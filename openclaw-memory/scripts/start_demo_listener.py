"""Demo listener startup — monitor @bot commands and card callbacks in demo1.

Usage:
  python scripts/start_demo_listener.py
  python scripts/start_demo_listener.py --interval 4

Listens for:
  - @bot commands (状态/面板/风险/审核/站会/交接/阻塞/进度)
  - Card button callbacks (confirm_task / dismiss_task)
"""

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
from memory.reply_handler import (
    BOT_COMMANDS, detect_bot_command, execute_bot_command,
    find_question, parse_confirmation,
    parse_card_action_callback, handle_card_callback,
)

logger = logging.getLogger("demo_listener")

# ── Config ──
CHAT_ID = "oc_1690fa5805369d63a18023e68eed0d65"
PROJECT_ID = "aurora-sprint"
DATA_DIR = ROOT / "data" / "demo"
POLL_INTERVAL = 4  # seconds


def main():
    parser = argparse.ArgumentParser(description="Demo1 Bot Listener")
    parser.add_argument("--chat-id", default=CHAT_ID)
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    adapter = LarkCliAdapter()
    store = MemoryStore(Path(args.data_dir))
    engine = MemoryEngine(store, RuleBasedExtractor())

    replied_ids: set[str] = set()
    last_baseline: set[str] = set()

    # Load existing messages as baseline
    result = adapter.list_chat_messages(args.chat_id, page_size=20)
    if result.returncode == 0:
        msgs = (result.data.get("data", {}).get("messages", [])
                or result.data.get("items", []) or [])
        last_baseline = {m.get("message_id", "") for m in msgs if m.get("message_id")}

    logger.info(f"Demo1 Listener started. Chat={args.chat_id} Project={args.project_id} Interval={args.interval}s")
    logger.info("Listening for: @bot commands + card callbacks")

    while True:
        try:
            result = adapter.list_chat_messages(args.chat_id, page_size=10)
            if result.returncode != 0:
                time.sleep(args.interval)
                continue

            msgs = (result.data.get("data", {}).get("messages", [])
                    or result.data.get("items", []) or [])

            for msg in msgs:
                mid = msg.get("message_id", "")
                if mid in replied_ids or mid in last_baseline:
                    continue

                sender = msg.get("sender", {}) or {}
                if sender.get("sender_type") == "app":
                    continue  # skip bot's own messages

                replied_ids.add(mid)
                body = msg.get("body", {}) or {}
                content = body.get("content", "") or msg.get("content", "")
                if isinstance(content, str) and content.strip().startswith("{"):
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        pass
                text = str(content) if not isinstance(content, dict) else json.dumps(content, ensure_ascii=False)

                # ── Handle card button callbacks ──
                callback = parse_card_action_callback(msg)
                if callback:
                    action_type = callback.get("action", "")
                    identity_key = callback.get("identity_key", "")
                    logger.info(f"Card callback: {action_type} key={identity_key[:40]}")
                    status = handle_card_callback(callback, adapter, store, args.project_id)
                    logger.info(f"Card handled: {status}")
                    continue

                # ── Handle review replies (text confirmation) ──
                reply_to = msg.get("reply_to", "") or msg.get("root_id", "")
                if reply_to:
                    question = find_question(reply_to)
                    if question:
                        is_confirm, indices = parse_confirmation(text)
                        if is_confirm:
                            logger.info(f"Review reply: indices={indices} for msg={reply_to}")
                            continue

                # ── Handle @bot commands ──
                action = detect_bot_command(text)
                if action:
                    logger.info(f"@bot command: {action}")
                    response = execute_bot_command(
                        action, args.chat_id, args.project_id, store=store, adapter=adapter,
                    )
                    if response:
                        adapter.send_message(args.chat_id, response, msg_type="markdown")
                        logger.info(f"@bot response sent ({len(response)} chars)")
                    continue

            time.sleep(args.interval)

        except KeyboardInterrupt:
            logger.info("Listener stopped.")
            break
        except Exception as e:
            logger.error(f"Error in poll loop: {e}", exc_info=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
