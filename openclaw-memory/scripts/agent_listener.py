"""WebSocket listener that triggers the AI risk-analyzer agent in real time.

Wires together:
  - lark-cli `event +subscribe`  (via EventStreamListener)
  - keyword-based trigger detection
  - demo_agent_loop.run_agent_loop  (LLM → card → memory writeback)

Run:
  python scripts/agent_listener.py
  python scripts/agent_listener.py --chat-id oc_xxx --project-id natural-daily
  python scripts/agent_listener.py --inspect      # dump raw events, do not trigger

Trigger keywords (any of):
  - "@bot" / "@AI"
  - 上线 + (吗|?|？)
  - 风险 + (?|？|大不大|怎么样|如何)
  - "项目状况" / "项目状态"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from adapters.event_listener import EventStreamListener, EventRouter
from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.store import MemoryStore

import demo_agent_loop  # noqa: E402

DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"
DEFAULT_PROJECT = "natural-daily"
DEFAULT_DATA_DIR = "data/agent_demo"
DEFAULT_BOOTSTRAP_CACHE = "/tmp/full_loop_events.json"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e71397d04b974b02a84b3f02b4b0302e")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# 30s per chat cooldown — protect against accidental spam / fast typers
TRIGGER_COOLDOWN_SEC = 30

logger = logging.getLogger("agent_listener")


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------
TRIGGER_PATTERNS = [
    re.compile(r"@\s*bot", re.I),
    re.compile(r"@\s*AI", re.I),
    re.compile(r"上线.*[吗\?？]"),
    re.compile(r"风险.*([\?？]|大不大|怎么样|如何|高不高)"),
    re.compile(r"项目(状况|状态|进度|情况)"),
    re.compile(r"现在.*[吗\?？].*(项目|情况|进度|状态)"),
    re.compile(r"分析一下"),
    re.compile(r"AI\s*帮我(分析|看看|评估)"),
]


def is_trigger(text: str) -> tuple[bool, str]:
    """Return (matched, matched_pattern) for the input text."""
    if not text or len(text) < 4:
        return False, ""
    for pat in TRIGGER_PATTERNS:
        if pat.search(text):
            return True, pat.pattern
    return False, ""


# ---------------------------------------------------------------------------
# Sender / message extraction (resilient to schema variations)
# ---------------------------------------------------------------------------
def extract_sender(event: dict) -> tuple[str, str, str]:
    """Return (sender_id, sender_name, sender_type) — best-effort."""
    sender = event.get("sender", {}) or {}
    sender_id = (
        sender.get("id")
        or sender.get("sender_id", {}).get("user_id", "")
        or sender.get("user_id", "")
        or event.get("sender_id", "")
        or event.get("user_id", "")
        or ""
    )
    sender_name = (
        sender.get("name")
        or event.get("sender_name", "")
        or sender.get("display_name", "")
        or ""
    )
    sender_type = (
        sender.get("sender_type")
        or sender.get("type")
        or event.get("sender_type", "")
        or "user"
    )
    return str(sender_id), str(sender_name), str(sender_type)


def extract_message_id(event: dict) -> str:
    return str(
        event.get("message_id", "")
        or event.get("msg_id", "")
        or (event.get("message", {}) or {}).get("message_id", "")
    )


def extract_text(event: dict) -> str:
    raw = (
        event.get("text", "")
        or event.get("content", "")
        or (event.get("body", {}) or {}).get("content", "")
        or (event.get("message", {}) or {}).get("content", "")
    )
    if not raw:
        return ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "text" in obj:
                return str(obj["text"])
        except json.JSONDecodeError:
            pass
    return str(raw)


# ---------------------------------------------------------------------------
# Bootstrap memory
# ---------------------------------------------------------------------------
def bootstrap_memory(
    data_dir: Path,
    project_id: str,
    bootstrap_cache: Path | None,
) -> MemoryStore:
    """Open or create a MemoryStore. If empty and a bootstrap cache exists,
    populate it via Hybrid extraction so the agent has context to work with."""
    data_dir.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(data_dir)
    items = list(store.list_items(project_id))
    if items:
        logger.info("Memory store has %d items already; skipping bootstrap.", len(items))
        return store

    if bootstrap_cache and bootstrap_cache.exists():
        logger.info("Memory store empty; bootstrapping from %s ...", bootstrap_cache)
        events = json.loads(bootstrap_cache.read_text())
        for ev in events:
            ev["project_id"] = project_id
        provider = OpenAIProvider(
            api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL, temperature=0.1, max_tokens=4000,
        )
        rule = RuleBasedExtractor()
        hybrid = HybridExtractor(
            rule_extractor=rule,
            llm_extractor=LLMExtractor(provider, fallback=rule),
        )
        engine = MemoryEngine(store, hybrid)
        engine.ingest_events(events, debounce=False)
        items = list(store.list_items(project_id))
        logger.info("Bootstrap done: %d items", len(items))
    else:
        logger.warning(
            "No memory and no bootstrap cache — agent will refuse to run "
            "until messages are synced."
        )
    return store


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time AI agent listener.")
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--bootstrap-cache", default=DEFAULT_BOOTSTRAP_CACHE)
    parser.add_argument("--agent-id", default="risk-analyzer")
    parser.add_argument("--inspect", action="store_true",
                        help="Print raw events for schema inspection, do not trigger")
    parser.add_argument("--no-trigger-self-check", action="store_true",
                        help="DANGER: allow triggering on bot's own messages")
    parser.add_argument("--cooldown", type=int, default=TRIGGER_COOLDOWN_SEC)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    )

    print("=" * 72)
    print(f"AI Agent Listener  ·  agent_id={args.agent_id}")
    print(f"Chat:    {args.chat_id}")
    print(f"Project: {args.project_id}")
    print(f"Mode:    {'INSPECT ONLY' if args.inspect else 'LIVE'}")
    print(f"Cooldown: {args.cooldown}s per chat")
    print("=" * 72)

    # Bootstrap memory (only when not in inspect mode)
    store = None
    if not args.inspect:
        data_dir = ROOT / args.data_dir
        cache = Path(args.bootstrap_cache) if args.bootstrap_cache else None
        store = bootstrap_memory(data_dir, args.project_id, cache)

    # Cooldown tracker
    last_fired_at: dict[str, float] = {}

    def handle_message(event: dict) -> None:
        text = extract_text(event)
        msg_id = extract_message_id(event)
        sender_id, sender_name, sender_type = extract_sender(event)
        chat_id = event.get("chat_id", args.chat_id) or args.chat_id

        if args.inspect:
            print("\n" + "─" * 60)
            print(f"  text:        {text[:120]}")
            print(f"  msg_id:      {msg_id}")
            print(f"  sender:      name={sender_name!r} id={sender_id!r} type={sender_type!r}")
            print(f"  chat_id:     {chat_id}")
            print(f"  raw keys:    {list(event.keys())}")
            return

        # Skip our own bot messages → avoid response→trigger loops
        if not args.no_trigger_self_check and sender_type == "bot":
            logger.debug("Skipping bot message: %s", text[:50])
            return

        matched, pattern = is_trigger(text)
        if not matched:
            return

        # Cooldown
        now = time.time()
        last = last_fired_at.get(chat_id, 0.0)
        if now - last < args.cooldown:
            logger.info("Cooldown active (%.1fs left), skipping: %s",
                        args.cooldown - (now - last), text[:50])
            return
        last_fired_at[chat_id] = now

        logger.info("🔔 TRIGGER MATCHED [%s]  by=%s  text=%r",
                    pattern, sender_name or sender_id or "?", text[:80])

        try:
            response = demo_agent_loop.run_agent_loop(
                chat_id=chat_id,
                project_id=args.project_id,
                trigger_text=text,
                trigger_sender=sender_name or sender_id or "anonymous",
                source_message_id=msg_id,
                store=store,
                agent_id=args.agent_id,
                send_card_to_feishu=True,
                write_back=True,
                log=logger,
            )
            logger.info("✅ AI response sent · risk=%s · actions=%d",
                        response.get("risk_level"),
                        len(response.get("actions") or []))
        except Exception as e:
            logger.exception("agent_loop failed: %s", e)

    router = EventRouter(chat_id=args.chat_id, store=store, adapter=None)
    router.register("im.message.receive_v1", handle_message)

    listener = EventStreamListener(
        chat_id=args.chat_id,
        event_types="im.message.receive_v1",
        heartbeat_timeout=120,
        reconnect_max_delay=60,
    )
    listener.on_event = router.handle

    print("\n💡 Listening… 在飞书群里发消息触发，例如：")
    print("   - @bot 周五能上线吗?")
    print("   - 现在项目风险大不大？")
    print("   - 帮我分析一下当前情况")
    print("\nCtrl+C 退出\n")

    try:
        listener.start()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
