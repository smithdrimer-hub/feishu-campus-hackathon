"""Polling-based AI agent listener — no WebSocket subscription required.

Why polling instead of WebSocket?
  The Feishu bot needs explicit subscription config in admin console to
  receive `im.message.receive_v1` events. Polling uses the read API which
  the bot already has access to (we use it in demo_e2e_pipeline).

Run:
  python scripts/agent_listener_poll.py
  python scripts/agent_listener_poll.py --interval 5 --as user
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from adapters.lark_cli_adapter import LarkCliAdapter
from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.store import MemoryStore

import demo_agent_loop  # reusable run_agent_loop()
from agent_listener import (  # reuse trigger patterns + helpers
    is_trigger,
    bootstrap_memory,
    DEFAULT_CHAT_ID,
    DEFAULT_PROJECT,
    DEFAULT_DATA_DIR,
    DEFAULT_BOOTSTRAP_CACHE,
    TRIGGER_COOLDOWN_SEC,
)

logger = logging.getLogger("agent_listener_poll")


def _extract_msg_text(msg: dict) -> str:
    """Extract plain text from a Feishu message dict (text / post types)."""
    body = msg.get("body", {}) or {}
    content = body.get("content") or msg.get("content", "")
    if not content:
        return ""

    # `content` from list_chat_messages is a JSON string for many msg_types
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                if "text" in obj:
                    return str(obj["text"])
                # Post message: paragraphs of segments
                if "content" in obj and isinstance(obj["content"], list):
                    out = []
                    for para in obj["content"]:
                        for seg in para or []:
                            if isinstance(seg, dict) and seg.get("text"):
                                out.append(seg["text"])
                    return " ".join(out)
        except json.JSONDecodeError:
            pass
    return str(content)


def _list_recent_messages(adapter: LarkCliAdapter, chat_id: str, page_size: int, identity: str) -> list[dict]:
    """Return latest messages, newest first."""
    res = adapter.list_chat_messages(
        chat_id=chat_id, page_size=page_size, identity=identity, sort="desc",
    )
    if res.returncode != 0 or not res.data:
        logger.warning("list_chat_messages failed (rc=%s): %s",
                       res.returncode, (res.data or {}).get("error", "?"))
        return []
    payload = (res.data or {}).get("data", {})
    return list(payload.get("messages", []) or [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Polling-based AI agent listener.")
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--bootstrap-cache", default=DEFAULT_BOOTSTRAP_CACHE)
    parser.add_argument("--agent-id", default="risk-analyzer")
    parser.add_argument("--interval", type=float, default=4.0,
                        help="seconds between polls (default 4)")
    parser.add_argument("--as", dest="identity", default="user",
                        choices=["user", "bot"],
                        help="lark-cli identity for reading messages")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--cooldown", type=int, default=TRIGGER_COOLDOWN_SEC)
    parser.add_argument("--inspect", action="store_true",
                        help="Print every new message, do not trigger agent")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    )

    print("=" * 72, flush=True)
    print(f"AI Agent Listener (POLLING)  ·  agent_id={args.agent_id}", flush=True)
    print(f"Chat:    {args.chat_id}", flush=True)
    print(f"Project: {args.project_id}", flush=True)
    print(f"Mode:    {'INSPECT ONLY' if args.inspect else 'LIVE'}    "
          f"Identity: --as {args.identity}    Interval: {args.interval}s", flush=True)
    print("=" * 72, flush=True)

    # Bootstrap memory (always, even in inspect mode — still useful)
    data_dir = ROOT / args.data_dir
    cache = Path(args.bootstrap_cache) if args.bootstrap_cache else None
    store = bootstrap_memory(data_dir, args.project_id, cache)

    adapter = LarkCliAdapter()

    # Establish baseline so we don't react to historical messages.
    # Use the SAME page_size as polling so we don't miss anything on the
    # first regular poll.
    print("\n[init] Reading current latest message as baseline ...", flush=True)
    initial = _list_recent_messages(adapter, args.chat_id,
                                    page_size=args.page_size,
                                    identity=args.identity)
    if not initial:
        print("⚠️  Could not read messages. Check `lark-cli auth login` and chat membership.",
              flush=True)
    seen_ids: set[str] = {m.get("message_id", "") for m in initial}
    last_seen_at: int = 0
    for m in initial:
        try:
            t = int(m.get("create_time", "0"))
            last_seen_at = max(last_seen_at, t)
        except Exception:
            pass
    print(f"[init] baseline: {len(seen_ids)} known msg ids, "
          f"last_create_time={last_seen_at}", flush=True)

    print("\n💡 Polling started. 在飞书群里发消息触发，例如：", flush=True)
    print("   - @bot 周五能上线吗？", flush=True)
    print("   - 现在项目风险大不大？", flush=True)
    print("   - 项目状态如何", flush=True)
    print("\nCtrl+C 退出\n", flush=True)

    last_fired_at: dict[str, float] = {}
    poll_n = 0

    try:
        while True:
            poll_n += 1
            msgs = _list_recent_messages(adapter, args.chat_id,
                                         page_size=args.page_size,
                                         identity=args.identity)
            new_msgs = []
            for m in msgs:
                mid = m.get("message_id", "")
                if not mid or mid in seen_ids:
                    continue
                # Only react to messages NEWER than baseline (some APIs return slightly older items)
                try:
                    if int(m.get("create_time", "0")) <= last_seen_at:
                        seen_ids.add(mid)
                        continue
                except Exception:
                    pass
                seen_ids.add(mid)
                new_msgs.append(m)

            # Process oldest first
            for m in reversed(new_msgs):
                text = _extract_msg_text(m)
                sender = m.get("sender", {}) or {}
                sender_name = sender.get("name") or sender.get("id", "?")
                sender_type = sender.get("sender_type", "user")
                mid = m.get("message_id", "")
                ts = m.get("create_time", "0")
                try:
                    last_seen_at = max(last_seen_at, int(ts))
                except Exception:
                    pass

                # Bot detection: Feishu's list_chat_messages reports the bot's
                # sender as the App ID like "cli_a9704d14e9fa1bc2" while real
                # users have human-readable names. Treat any sender_name
                # starting with "cli_" as a bot message.
                is_bot = (
                    sender_type == "bot"
                    or sender_name.startswith("cli_")
                    or m.get("msg_type") in ("interactive",)
                )
                tag = "BOT" if is_bot else "USR"
                short_text = text.replace("\n", " ")[:80]
                print(f"  [poll#{poll_n}] new {tag} msg from {sender_name}: {short_text}",
                      flush=True)

                if args.inspect:
                    continue

                if is_bot:
                    continue  # don't react to our own bot messages

                matched, pattern = is_trigger(text)
                if not matched:
                    continue

                now = time.time()
                last = last_fired_at.get(args.chat_id, 0.0)
                if now - last < args.cooldown:
                    print(f"    ⏳ cooldown {args.cooldown - (now - last):.0f}s left, skip",
                          flush=True)
                    continue
                last_fired_at[args.chat_id] = now

                print(f"  🔔 TRIGGER MATCHED [{pattern}] — calling agent ...", flush=True)
                try:
                    response = demo_agent_loop.run_agent_loop(
                        chat_id=args.chat_id,
                        project_id=args.project_id,
                        trigger_text=text,
                        trigger_sender=sender_name,
                        source_message_id=mid,
                        store=store,
                        agent_id=args.agent_id,
                        send_card_to_feishu=True,
                        write_back=True,
                        log=logger,
                    )
                    print(f"  ✅ AI replied · risk={response.get('risk_level')} · "
                          f"actions={len(response.get('actions') or [])}", flush=True)
                except Exception as e:
                    logger.exception("agent_loop failed: %s", e)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
