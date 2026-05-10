"""Unified Feishu event listener with full downstream processing.

V1.18: Merges WebSocket + polling paths. Prioritizes real-time WebSocket
(lark-cli event +subscribe), falls back to polling if WS is unavailable.
On every new message, runs extract → trigger → execute → send pipeline.

Architecture:
  WebSocket (primary) or Polling (fallback)
        │
        ▼
  extract (pipeline on new message)
        │
        ▼
  trigger (ActionTrigger.scan on diff)
        │
        ▼
  execute (ActionExecutor auto mode)
        │
        ▼
  send response / reply to group

Usage:
  python scripts/unified_listener.py                 # auto-detect mode
  python scripts/unified_listener.py --mode ws       # force WebSocket
  python scripts/unified_listener.py --mode poll     # force polling
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("unified_listener")

# ── Config defaults ────────────────────────────────────────────
DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"
DEFAULT_PROJECT = "memory-sandbox"
DEFAULT_DATA_DIR = "data/unified"
POLL_INTERVAL = 5  # seconds

# V1.18: 已处理消息去重（防 WS+poll 双重处理）
_PROCESSED_MSG_IDS: set[str] = set()
_MAX_PROCESSED = 500

# V1.18: 滑动窗口上下文（多轮对话理解）
_MSG_BUFFER: list[dict] = []
_BUFFER_SIZE = 5
_CHAT_BUFFERS: dict[str, list[dict]] = {}  # 按 chat_id 分片

# V1.18: 处理摘要推送冷却
_LAST_PUSH: dict[str, float] = {}
_PUSH_COOLDOWN = 60  # 秒
_PENDING_SUMMARY: dict[str, list[str]] = {}


def _add_to_buffer(chat_id: str, event: dict) -> None:
    """维护滑动窗口：最近 N 条消息作为 LLM 上下文。"""
    buf = _CHAT_BUFFERS.setdefault(chat_id, [])
    buf.append(event)
    if len(buf) > _BUFFER_SIZE:
        buf.pop(0)


def _get_context_events(chat_id: str, current: dict) -> list[dict]:
    """获取当前消息 + 历史缓冲作为提取上下文。"""
    buf = _CHAT_BUFFERS.get(chat_id, [])
    return list(buf) + [current]


def _maybe_push_summary(chat_id: str, adapter, parts: list[str]) -> None:
    """推送处理摘要到群，60秒冷却，合并多条摘要。"""
    now = time.time()
    if chat_id in _LAST_PUSH and now - _LAST_PUSH[chat_id] < _PUSH_COOLDOWN:
        _PENDING_SUMMARY.setdefault(chat_id, []).extend(parts)
        return
    _LAST_PUSH[chat_id] = now
    pending = _PENDING_SUMMARY.pop(chat_id, [])
    all_parts = pending + parts
    if all_parts:
        msg = "Memory Engine: " + " | ".join(all_parts[-3:])  # 最多 3 条
        adapter.send_message(chat_id, msg)
        logger.info("Push summary: %s", msg[:100])


def _is_duplicate(msg_id: str) -> bool:
    """检查消息是否已处理过。"""
    if not msg_id:
        return False
    if msg_id in _PROCESSED_MSG_IDS:
        return True
    _PROCESSED_MSG_IDS.add(msg_id)
    if len(_PROCESSED_MSG_IDS) > _MAX_PROCESSED:
        _PROCESSED_MSG_IDS.clear()  # 简单策略：超过上限清空
    return False


def _check_confirmation_reply(text: str, chat_id: str, adapter,
                              project_id: str = "", data_dir: str = "",
                              root_msg_id: str = "") -> bool:
    """V1.18 R4闭环: 仅检测对 bot 确认消息的回复 → 标记 approved/rejected → 回复群。

    V1.18.1: 只处理 root_id 匹配 question_map.jsonl 的回复消息，忽略普通聊天。
    当用户回复"都不是"时，将候选 identity_key 写入 ignore_list.jsonl（24h 冷却）。
    """
    if not root_msg_id:
        return False  # 不是回复消息，跳过

    from memory.reply_handler import parse_confirmation, find_question
    from memory.store import MemoryStore
    from memory.action_trigger import write_ignore_entry

    question = find_question(root_msg_id)
    if not question:
        return False  # 回复的不是我们的确认问题

    is_conf, indices = parse_confirmation(text)
    if not is_conf:
        return False

    if data_dir:
        store = MemoryStore(Path(data_dir))
        pending = [i for i in store.list_items(project_id)
                   if getattr(i, "review_status", "") == "needs_review"]
        if indices:
            for idx in indices:
                if 1 <= idx <= len(pending):
                    store.update_item_review(pending[idx - 1].memory_id, "approved")
            msg = f"已确认 {len(indices)} 项，可通过审核台查看。"
        else:
            # "都不是" → 写入 24h 忽略列表
            ignored_keys = question.get("candidate_identity_keys", [])
            for key in ignored_keys:
                write_ignore_entry(key)
            for item in pending[:5]:
                store.update_item_review(item.memory_id, "rejected")
            msg = "已标记为不需要创建任务（24h 内不再询问相同候选）。"
        adapter.send_message(chat_id, msg)
        logger.info("R4 closed: %s → %s", text[:50], msg)
    return True


# ── Core: process a single new message ─────────────────────────

def process_message(text: str, chat_id: str, project_id: str,
                    data_dir: str, adapter, dry_run: bool = False,
                    msg_id: str = "", use_hybrid: bool = False,
                    root_msg_id: str = ""):
    """Full pipeline on one new message: extract → trigger → execute.

    V1.18: 支持 R4 确认回复闭环 + 消息去重 + Hybrid 模式。
    V1.18.1: root_msg_id 用于检测回复消息是否针对 bot 确认提问。
    """
    # 去重
    if _is_duplicate(msg_id):
        logger.debug("Skipping duplicate: %s", msg_id[:20])
        return [], []

    # R4 确认回复检测（仅处理对 bot 确认消息的回复）
    if _check_confirmation_reply(text, chat_id, adapter, project_id, data_dir, root_msg_id):
        return [], []

    from memory.store import MemoryStore
    from memory.engine import MemoryEngine
    from memory.extractor import RuleBasedExtractor, HybridExtractor, LLMExtractor
    from memory.action_trigger import ActionTrigger
    from memory.action_executor import ActionExecutor
    from memory.action_planner import PlannedAction

    store = MemoryStore(Path(data_dir))

    # V1.18: Hybrid 模式 (selector: 精确→规则 / 模糊→LLM)
    if use_hybrid:
        try:
            from memory.llm_provider import OpenAIProvider
            import yaml
            cfg_path = ROOT / "config.local.yaml"
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                llm_cfg = cfg.get("llm", {})
                provider = OpenAIProvider(
                    api_key=llm_cfg.get("api_key", ""),
                    base_url=llm_cfg.get("base_url", ""),
                    model=llm_cfg.get("model", ""),
                )
                llm_ext = LLMExtractor(provider, fallback=RuleBasedExtractor())
                extractor = HybridExtractor(
                    rule_extractor=RuleBasedExtractor(),
                    llm_extractor=llm_ext,
                )
            else:
                extractor = RuleBasedExtractor()
        except Exception:
            extractor = RuleBasedExtractor()
    else:
        extractor = RuleBasedExtractor()

    engine = MemoryEngine(store, extractor, adapter=adapter)

    # V1.18: 滑动窗口上下文——把缓冲消息附加到当前消息
    now_iso = datetime.now(timezone.utc).isoformat()
    current_event = {
        "project_id": project_id, "chat_id": chat_id,
        "message_id": msg_id or f"unified_{int(time.time())}",
        "text": text, "content": text, "created_at": now_iso,
        "sender": {"id": "unified_listener", "sender_type": "user",
                   "name": "系统监听"},
    }
    _add_to_buffer(chat_id, current_event)
    context_events = _get_context_events(chat_id, current_event) if use_hybrid else [current_event]

    if not dry_run:
        store.append_raw_events(context_events)
        items = engine.process_new_events(project_id, debounce=False)
    else:
        items = engine.extractor.extract(context_events)

    logger.info("Extracted %d items (context=%d msgs): %s",
                len(items), len(context_events), text[:60])

    # Trigger + Execute
    diff = getattr(engine, "last_diff", {
        "created": items, "updated": [], "unchanged": [], "conflicts": [],
    })
    trigger = ActionTrigger(engine=engine,
                            log_path=str(Path(data_dir) / "action_log.jsonl"))
    proposals = trigger.scan(diff, project_id, chat_id)

    if proposals and not dry_run:
        executor = ActionExecutor(adapter, auto_confirm=True)
        planned = [
            PlannedAction(
                action_type="send_message" if p.action_type == "send_alert"
                else p.action_type,
                title=p.metadata.get("alert_detail", p.title),
                reason=p.reason, command_hint="",
                requires_confirmation=p.requires_confirmation,
                metadata=p.metadata,
            ) for p in proposals
        ]
        results = executor.execute_plan(planned, {
            "chat_id": chat_id, "project_id": project_id,
        })
        trigger.write_results(results, project_id)
        ok = sum(1 for r in results if r.success)
        logger.info("Trigger: %d proposals, %d executed", len(proposals), ok)

        # V1.18: 处理摘要推送
        summary_parts = []
        new_types = set(i.state_type for i in items)
        if new_types:
            summary_parts.append(f"提取到 {len(items)} 条记忆 ({', '.join(sorted(new_types)[:3])})")
        if ok > 0:
            summary_parts.append(f"执行了 {ok} 个动作")
        if summary_parts and not dry_run:
            _maybe_push_summary(chat_id, adapter, summary_parts)

    return items, proposals


# ── WebSocket mode ─────────────────────────────────────────────

def _handle_reaction(event: dict, data_dir: str, project_id: str,
                     chat_id: str, adapter) -> bool:
    """V1.18: 检测表情反应 → approve/reject。返回 True 表示已处理。"""
    etype = event.get("type", event.get("event_type", ""))
    if "reaction" not in etype:
        return False

    emoji = event.get("emoji_type", event.get("reaction_type", "")).lower()
    msg_id = event.get("message_id", "")

    # Only process reactions on bot messages (confirmation questions)
    if not msg_id:
        return False

    from memory.store import MemoryStore
    from memory.reply_handler import find_question
    store = MemoryStore(Path(data_dir))
    question = find_question(msg_id)
    if not question:
        return False

    # V1.18: 表情撤回 → 忽略（飞书发送 action="removed"）
    action = event.get("action", "added")
    if action == "removed":
        logger.info("Reaction removed: %s on %s", emoji, msg_id[:20])
        return True

    _YES_EMOJI = {"ok", "thumbsup", "like", "heart", "+1", "clap", "heavy_check_mark"}
    _NO_EMOJI = {"thumbsdown", "x", "dislike", "-1", "no_good", "facepalm",
                  "cross", "fail", "no", "negative_squared_cross_mark"}

    if emoji not in _YES_EMOJI and emoji not in _NO_EMOJI:
        return False

    # V1.18: 5 秒消抖——用户可能在改主意，等反应稳定后再执行
    _REACTION_TIMER: dict[str, tuple] = {}
    import threading
    now = time.time()
    last = _REACTION_TIMER.get(msg_id)
    if last:
        last_time, last_timer = last
        if now - last_time < 5:
            last_timer.cancel()
    delay = 5.0
    timer = threading.Timer(delay, _execute_reaction,
                            args=[msg_id, emoji, _YES_EMOJI, _NO_EMOJI,
                                  store, project_id, adapter, chat_id])
    timer.start()
    _REACTION_TIMER[msg_id] = (now, timer)
    logger.info("Reaction queued (%ss debounce): %s on %s", delay, emoji, msg_id[:20])
    return True


def _execute_reaction(msg_id, emoji, yes_set, no_set, store, project_id, adapter, chat_id):
    """V1.18: 消抖后执行反应确认/驳回。"""
    pending = [i for i in store.list_items(project_id)
               if getattr(i, "review_status", "") == "needs_review"]
    if emoji in yes_set:
        if not pending:
            adapter.send_message(chat_id, "当前没有待确认项，无需确认")
            return True
        for item in pending[:5]:
            store.update_item_review(item.memory_id, "approved")
        adapter.send_message(chat_id, f"已确认 {len(pending[:5])}/{len(pending)} 项待确认")
        return True

    if emoji in no_set:
        if not pending:
            adapter.send_message(chat_id, "当前没有待驳回项")
            return True
        for item in pending[:5]:
            store.update_item_review(item.memory_id, "rejected")
        adapter.send_message(chat_id, f"已驳回 {len(pending[:5])}/{len(pending)} 项待确认")
        return True

    return False


def ws_listen(chat_id: str, project_id: str, data_dir: str,
              adapter, dry_run: bool = False, use_hybrid: bool = False):
    """Real-time WebSocket via lark-cli event +subscribe."""
    from adapters.event_listener import EventStreamListener

    listener = EventStreamListener(
        chat_id=chat_id,
        event_types="im.message.receive_v1,im.message.reaction.created_v1",
        heartbeat_timeout=90,
        reconnect_max_delay=60,
    )

    def on_event(event: dict):
        # V1.18: reaction events first
        if _handle_reaction(event, data_dir, project_id, chat_id, adapter):
            return

        text = event.get("content", event.get("text", ""))
        msg_id = event.get("message_id", "")
        root_msg_id = event.get("root_id", event.get("parent_id", ""))
        if not text:
            return
        if _is_duplicate(msg_id):
            return
        logger.info("WS event: %s", text[:60])
        process_message(text, chat_id, project_id, data_dir, adapter,
                        dry_run, msg_id=msg_id, use_hybrid=use_hybrid,
                        root_msg_id=root_msg_id)

    listener.on_event = on_event
    logger.info("WebSocket listener starting for chat %s...", chat_id)
    listener.start()


# ── Polling mode ───────────────────────────────────────────────

def poll_listen(chat_id: str, project_id: str, data_dir: str,
                adapter, dry_run: bool = False, interval: int = POLL_INTERVAL,
                use_hybrid: bool = False):
    """Polling fallback via lark-cli im +chat-messages-list."""
    logger.info("Polling listener starting (interval=%ds)...", interval)
    while True:
        try:
            result = adapter.list_chat_messages(chat_id, page_size=5)
            if result.returncode != 0:
                time.sleep(interval)
                continue

            payload = result.data or {}
            msgs = payload.get("data", {}).get("messages", []) or []
            for msg in msgs:
                msg_id = msg.get("message_id", "")
                if _is_duplicate(msg_id):
                    continue

                text = str(msg.get("content", msg.get("text", "")))
                if not text or msg.get("msg_type") == "system":
                    continue

                root_msg_id = msg.get("root_id", msg.get("parent_id", ""))
                logger.info("Poll event: %s", text[:60])
                process_message(text, chat_id, project_id, data_dir,
                                adapter, dry_run, msg_id=msg_id,
                                use_hybrid=use_hybrid,
                                root_msg_id=root_msg_id)

        except Exception as e:
            logger.warning("Poll error: %s", e)

        time.sleep(interval)


# ── CLI ────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Unified Feishu Event Listener")
    p.add_argument("--mode", default="auto",
                   choices=["auto", "ws", "poll"],
                   help="auto: try WS first, fall back to poll")
    p.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    p.add_argument("--project-id", default=DEFAULT_PROJECT)
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--interval", type=int, default=POLL_INTERVAL,
                   help="Polling interval in seconds")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract but don't write or send")
    p.add_argument("--hybrid", action="store_true",
                   help="Use Hybrid extractor (selector: precise->rule, fuzzy->LLM)")
    return p.parse_args()


def main():
    args = parse_args()
    from adapters.lark_cli_adapter import LarkCliAdapter
    adapter = LarkCliAdapter()

    if args.mode == "ws":
        ws_listen(args.chat_id, args.project_id, args.data_dir,
                  adapter, args.dry_run, args.hybrid)
    elif args.mode == "poll":
        poll_listen(args.chat_id, args.project_id, args.data_dir,
                    adapter, args.dry_run, args.interval, args.hybrid)
    else:  # auto
        try:
            ws_listen(args.chat_id, args.project_id, args.data_dir,
                      adapter, args.dry_run, args.hybrid)
        except Exception as e:
            logger.warning("WS failed (%s), falling back to polling...", e)
            poll_listen(args.chat_id, args.project_id, args.data_dir,
                        adapter, args.dry_run, args.interval, args.hybrid)


if __name__ == "__main__":
    main()
