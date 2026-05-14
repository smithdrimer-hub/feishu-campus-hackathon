"""@bot 交互处理器 — 监听群聊中的 @bot 消息，查询 Memory Engine 后回复简洁卡片。

用法:
  python scripts/bot_handler.py --chat-id oc_xxx --data-dir data/auto
  python scripts/bot_handler.py --interval 5

触发示例:
  @bot 风险大不大 → 查询 blocker + deadline，生成风险卡片
  @bot 待审核      → 列出 needs_review 记忆
  @bot 阻塞        → 活跃阻塞清单
  @bot 进度        → 项目状态摘要
  @bot 交接        → 完整交接摘要
  @bot 谁负责      → 负责人列表
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapters.lark_cli_adapter import LarkCliAdapter
from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor
from memory.store import MemoryStore
from memory.project_state import build_group_project_state

logger = logging.getLogger("bot_handler")

# ── 意图路由 ──────────────────────────────────────────────────────

def _route_intent(text: str) -> str | None:
    """根据消息文本识别用户意图，返回路由 key。"""
    t = text.lower().replace("@bot", "").strip()
    if any(w in t for w in ["风险", "危险", "出问题", "上线", "ddl", "deadline"]):
        return "risk"
    if any(w in t for w in ["待审核", "审核台", "needs_review", "确认"]):
        return "review"
    if any(w in t for w in ["阻塞", "卡住", "blocker"]):
        return "blocker"
    if any(w in t for w in ["进度", "状态", "状况", "情况", "项目"]):
        return "status"
    if any(w in t for w in ["交接", "接手", "handoff"]):
        return "handoff"
    if any(w in t for w in ["谁负责", "负责人", "owner", "分工"]):
        return "owner"
    if any(w in t for w in ["什么", "有哪些", "看看", "帮我看"]):
        return "status"  # 泛查询 → 状态面板
    return None


# ── 卡片生成 ──────────────────────────────────────────────────────

def _card_header(title: str, template: str = "blue") -> dict:
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def _card_div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _card_note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def _card_hr() -> dict:
    return {"tag": "hr"}


def _evidence_line(ref) -> str:
    """生成证据行：sender + 时间。"""
    if not ref:
        return ""
    name = ref.sender_name or ""
    time_str = (ref.created_at or "")[:10] or ""
    if name and time_str:
        return f"({name} {time_str})"
    return f"({name or time_str})"


def _build_risk_card(blockers, deadlines, items) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _card_note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def _card_hr() -> dict:
    return {"tag": "hr"}


def _build_risk_card(blockers, deadlines, items) -> dict:
    active_blockers = [b for b in blockers if b.status == "active"]
    severity = "HIGH" if len(active_blockers) >= 2 else "MEDIUM"
    elements = [_card_div(f"风险等级: **{severity}** | 活跃阻塞: {len(active_blockers)} 个")]
    for b in active_blockers[:3]:
        ref = b.source_refs[0] if b.source_refs else None
        evidence = _evidence_line(ref)
        elements.append(_card_div(f"- {b.current_value[:80]}  {evidence}"))
    if len(active_blockers) > 3:
        elements.append(_card_div(f"...及其他 {len(active_blockers)-3} 个阻塞"))
    elements.append(_card_hr())
    elements.append(_card_div(f"共 {len(items)} 条活跃记忆"))
    elements.append(_card_note("Memory Engine"))
    return {"config": {"wide_screen_mode": True},
            "header": _card_header("项目风险分析", "red"),
            "elements": elements}


def _build_review_card(needs_review_items) -> dict:
    elements = []
    if not needs_review_items:
        elements.append(_card_div("没有待审核的记忆，项目状态健康。"))
    else:
        for item in needs_review_items[:3]:
            conf = item.confidence
            elements.append(_card_div(f"[{item.state_type}] {item.current_value[:80]} (置信度: {conf:.2f})"))
            if item.status_reason:
                elements.append(_card_note(f"原因: {item.status_reason[:80]}"))
    return {"config": {"wide_screen_mode": True},
            "header": _card_header(f"待审核记忆 ({len(needs_review_items)} 条)", "purple"),
            "elements": elements}


def _build_blocker_card(blockers) -> dict:
    active = [b for b in blockers if b.status == "active"]
    elements = []
    for b in active[:3]:
        ref = b.source_refs[0] if b.source_refs else None
        evidence = _evidence_line(ref)
        elements.append(_card_div(f"- {b.current_value[:80]}  {evidence}"))
    if len(active) > 3:
        elements.append(_card_div(f"...及其他 {len(active)-3} 个阻塞"))
    if not active:
        elements.append(_card_div("当前没有活跃阻塞。"))
    elements.append(_card_note("Memory Engine"))
    return {"config": {"wide_screen_mode": True},
            "header": _card_header(f"活跃阻塞 ({len(active)} 个)", "red"),
            "elements": elements}


def _build_status_card(items) -> dict:
    from collections import Counter
    types = Counter(i.state_type for i in items)
    labels = {"project_goal": "目标", "owner": "负责人", "decision": "决策",
              "blocker": "阻塞", "deadline": "截止", "next_step": "下一步",
              "deferred": "暂缓", "member_status": "成员"}
    lines = []
    for t, label in labels.items():
        c = types.get(t, 0)
        if c > 0:
            lines.append(f"{label}: {c}")
    elements = [_card_div("  |  ".join(lines))]
    elements.append(_card_note(f"共 {len(items)} 条活跃记忆"))
    return {"config": {"wide_screen_mode": True},
            "header": _card_header("项目状态摘要", "blue"),
            "elements": elements}


def _build_owner_card(owners) -> dict:
    elements = []
    for o in owners[:3]:
        ref = o.source_refs[0] if o.source_refs else None
        name = ref.sender_name if ref else ""
        line = f"- {o.current_value[:60]}"
        if name:
            line += f" (来源: {name})"
        elements.append(_card_div(line))
    if not owners:
        elements.append(_card_div("未找到负责人信息。"))
    return {"config": {"wide_screen_mode": True},
            "header": _card_header("当前负责人", "blue"),
            "elements": elements}


# ── LLM 回答具体问题 ─────────────────────────────────────────────

def _format_memory_for_llm(items, history_items) -> str:
    """将记忆状态压缩为 LLM 可读的上下文。"""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in items:
        grouped[item.state_type].append(item)

    parts = ["## 当前项目状态 (memory_state)", ""]
    labels = {
        "project_goal": "目标", "owner": "负责人", "decision": "决策",
        "blocker": "阻塞", "deadline": "截止日期", "next_step": "下一步",
        "deferred": "暂缓", "member_status": "成员状态",
    }
    for st, label in labels.items():
        state_items = grouped.get(st, [])
        if not state_items:
            continue
        parts.append(f"### {label}")
        for item in state_items[:3]:
            parts.append(f"- {item.current_value}")
            if item.source_refs:
                ref = item.source_refs[0]
                parts.append(f"  (来源: {ref.sender_name}, {ref.created_at[:10]})")
        parts.append("")

    if history_items:
        recent = [h for h in history_items if h.status in ("corrected", "superseded", "expired")]
        if recent:
            parts.append("## 近期变更/纠正")
            for h in recent[:5]:
                parts.append(f"- [{h.status}] {h.current_value[:60]}")
    return "\n".join(parts)


def _ask_llm(question: str, items, history_items, project_id: str) -> str:
    """用 LLM 结合 memory_state 回答具体问题。"""
    try:
        from config import get_config
        cfg = get_config()
        if not cfg.llm.api_key:
            return {"config": {"wide_screen_mode": True},
                    "header": _card_header("LLM 未配置", "red"),
                    "elements": [_card_div("无法回答具体问题。请使用 @bot 风险 / @bot 阻塞 等快捷指令。")]}

        from memory.llm_provider import OpenAIProvider
        provider = OpenAIProvider(
            api_key=cfg.llm.api_key,
            base_url=cfg.llm.base_url,
            model=cfg.llm.model,
            temperature=0.1, max_tokens=1500,
        )

        context = _format_memory_for_llm(items, history_items)
        clean_question = question.replace("@bot", "").strip()

        prompt = f"""你是飞书群的 AI 助手。请根据以下项目记忆状态，回答用户的提问。

{context}

用户提问: {clean_question}

要求:
- 用简洁的中文回答，3-5 段
- 引用具体的记忆条目和来源（谁说的、哪天说的）
- 如果有证据不足的地方，诚实说明
- 不要编造不在记忆中的信息
- 结尾附一条建议"""

        raw = provider.generate(prompt)
        if not raw:
            return {"config": {"wide_screen_mode": True},
                    "header": _card_header("LLM 返回空", "red"),
                    "elements": [_card_div("请稍后重试。")]}
        return {"config": {"wide_screen_mode": True},
                "header": _card_header("AI 助手回复", "blue"),
                "elements": [_card_div(raw.strip()),
                             _card_note(f"基于 Memory Engine ({len(items)} 条活跃记忆)")]}
    except Exception as e:
        logger.warning(f"LLM question failed: {e}")
        return {"config": {"wide_screen_mode": True},
                "header": _card_header("生成失败", "red"),
                "elements": [_card_div(str(e)[:200])]}


# ── 主循环 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="@bot Memory Engine handler")
    parser.add_argument("--chat-id", default="oc_e1c6a2c2a42b67606b91ad69bab226f4")
    parser.add_argument("--project-id", default="memory-sandbox")
    parser.add_argument("--data-dir", default="data/auto")
    parser.add_argument("--interval", type=int, default=4, help="轮询间隔(秒)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    adapter = LarkCliAdapter()
    data_dir = ROOT / args.data_dir
    store = MemoryStore(data_dir)
    engine = MemoryEngine(store, RuleBasedExtractor())

    # 已回复的消息不重复处理
    replied_ids: set[str] = set()
    last_baseline: set[str] = set()

    # 加载现有消息作为基线
    result = adapter.list_chat_messages(args.chat_id, page_size=20)
    if result.returncode == 0:
        msgs = result.data.get("data", {}).get("messages", []) or \
               result.data.get("items", []) or []
        last_baseline = {m.get("message_id", "") for m in msgs if m.get("message_id")}

    logger.info(f"Bot handler started. Interval={args.interval}s")

    while True:
        try:
            result = adapter.list_chat_messages(args.chat_id, page_size=10)
            if result.returncode != 0:
                time.sleep(args.interval)
                continue

            msgs = result.data.get("data", {}).get("messages", []) or \
                   result.data.get("items", []) or []

            for msg in msgs:
                mid = msg.get("message_id", "")
                if mid in replied_ids or mid in last_baseline:
                    continue
                # 跳过 bot 自己的消息（防止循环回复）
                sender = msg.get("sender", {}) or {}
                if sender.get("sender_type") == "app":
                    continue
                replied_ids.add(mid)

                body = msg.get("body", {}) or {}
                content = body.get("content", "") or msg.get("content", "")
                if isinstance(content, str) and content.strip().startswith("{"):
                    try:
                        obj = json.loads(content)
                        content = obj.get("text", content)
                    except json.JSONDecodeError:
                        pass

                text = str(content).strip()
                if not text or "@bot" not in text.lower():
                    continue

                intent = _route_intent(text)
                if not intent:
                    # 不匹配简单路由但包含 @bot → LLM 回答具体问题
                    if "@bot" in text.lower():
                        intent = "question"

                if not intent:
                    continue

                logger.info(f"@bot intent={intent} text={text[:80]}")

                # 查询记忆（不同步——避免把 @bot 消息作为协作记忆提取）
                # engine.process_new_events 只应在 sync 命令时调用

                items = store.list_items(args.project_id)
                history = store.list_history(args.project_id)

                # 根据意图生成卡片
                card = ""
                if intent == "question":
                    card = _ask_llm(text, items, history, args.project_id)
                elif intent == "risk":
                    blockers = [i for i in items if i.state_type == "blocker"]
                    deadlines = [i for i in items if i.state_type == "deadline"]
                    card = _build_risk_card(blockers, deadlines, items)
                elif intent == "review":
                    needs = [i for i in items if i.review_status == "needs_review"]
                    card = _build_review_card(needs)
                elif intent == "blocker":
                    blockers = [i for i in items if i.state_type == "blocker"]
                    card = _build_blocker_card(blockers)
                elif intent == "status":
                    card = _build_status_card(items)
                elif intent == "handoff":
                    from memory.handoff import generate_handoff
                    card = generate_handoff(args.project_id, items, history)
                    card = card[:3000] + "\n\n...(移交摘要完整版已截断)" if len(card) > 3000 else card
                elif intent == "owner":
                    owners = [i for i in items if i.state_type == "owner"]
                    card = _build_owner_card(owners)

                if card:
                    import json as _json
                    result = adapter.send_message(args.chat_id, _json.dumps(card, ensure_ascii=False),
                                                  msg_type="interactive")
                    if result.returncode == 0:
                        logger.info(f"Card sent for intent={intent}")
                    else:
                        logger.warning(f"Card FAILED rc={result.returncode} stderr={result.stderr[:200]}")

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            logger.warning(f"Loop error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
