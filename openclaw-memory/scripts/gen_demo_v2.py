"""Demo v2 message sender — reads demo_script_v2.md and sends messages to Feishu.

Usage:
  python scripts/gen_demo_v2.py --chat-id oc_xxx [--dry-run] [--delay-min 1] [--delay-max 5]

The script parses a pipe-delimited format:
  DAY|TIME|SENDER|MSG_TYPE|TEXT
"""

import argparse
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_script(script_path: str) -> list[dict]:
    """Parse demo_script_v2.md into a list of message dicts."""
    messages = []
    with open(script_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            day, time_str, sender, msg_type, text = parts
            messages.append({
                "day": int(day.strip()),
                "time": time_str.strip(),
                "sender": sender.strip(),
                "msg_type": msg_type.strip(),
                "text": text.strip(),
            })
    return messages


def format_message(msg: dict) -> str:
    """Format a message with sender prefix for Feishu display."""
    sender = msg["sender"]
    text = msg["text"]
    # Don't double-prefix
    if text.startswith(f"{sender}："):
        return text
    return f"{sender}：{text}"


def send_via_adapter(adapter, chat_id: str, content: str,
                      msg_type: str = "text", identity: str = "user") -> bool:
    """Send a message via LarkCliAdapter. Returns True on success.

    Uses identity="user" so the sender_type is "user" (not "app"),
    allowing the pipeline to ingest these demo messages as real
    collaboration signals.
    """
    result = adapter.send_message(chat_id, content, msg_type=msg_type,
                                  identity=identity)
    if result.returncode == 0 and result.data:
        inner = result.data.get("data", result.data) if isinstance(result.data, dict) else {}
        return bool(inner.get("message_id", ""))
    return False


def main():
    parser = argparse.ArgumentParser(description="Demo v2 message sender")
    parser.add_argument("--chat-id", required=True,
                        help="Feishu group chat_id (oc_xxx)")
    parser.add_argument("--script", default="data/demo_script_v2.md",
                        help="Path to the demo script markdown file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview messages without sending")
    parser.add_argument("--delay-min", type=float, default=1.0,
                        help="Min delay between messages (seconds)")
    parser.add_argument("--delay-max", type=float, default=4.0,
                        help="Max delay between messages (seconds)")
    parser.add_argument("--start-day", type=int, default=1,
                        help="Start from this day (1-10)")
    parser.add_argument("--identity", default="bot",
                        help="Sender identity: bot / user")
    args = parser.parse_args()

    script_path = ROOT / args.script if not Path(args.script).is_absolute() \
        else Path(args.script)
    if not script_path.exists():
        print(f"Script not found: {script_path}")
        sys.exit(1)

    messages = parse_script(str(script_path))
    messages = [m for m in messages if m["day"] >= args.start_day]

    if not messages:
        print("No messages found in script.")
        sys.exit(1)

    # Initialize adapter for sending
    from adapters.lark_cli_adapter import LarkCliAdapter
    adapter = LarkCliAdapter()

    print(f"Demo v2 Message Sender")
    print(f"  Chat: {args.chat_id}")
    print(f"  Messages: {len(messages)}")
    print(f"  Days: {messages[0]['day']} - {messages[-1]['day']}")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()

    sent = 0
    failed = 0
    current_day = messages[0]["day"]

    for i, msg in enumerate(messages):
        if msg["day"] != current_day:
            current_day = msg["day"]
            print(f"\n── Day {current_day} ──")

        formatted = format_message(msg)
        prefix = f"[{msg['day']} {msg['time']}]"
        preview = formatted[:80] + ("..." if len(formatted) > 80 else "")

        if args.dry_run:
            print(f"  {prefix} [{msg['msg_type']}] {preview}")
            sent += 1
        else:
            print(f"  {prefix} [{msg['msg_type']}] {preview}", end="", flush=True)
            success = send_via_adapter(
                adapter, args.chat_id, formatted, msg["msg_type"],
            )
            if success:
                print(" OK", flush=True)
                sent += 1
            else:
                print(" FAIL", flush=True)
                failed += 1

            # Random delay to simulate natural conversation pace
            if i < len(messages) - 1:
                delay = random.uniform(args.delay_min, args.delay_max)
                # Longer delay between days
                next_msg = messages[i + 1]
                if next_msg["day"] != msg["day"]:
                    delay = random.uniform(2.0, 5.0)
                time.sleep(delay)

    print(f"\nDone. Sent: {sent}, Failed: {failed}")
    if args.dry_run:
        print("(DRY-RUN mode — no messages were actually sent)")


if __name__ == "__main__":
    main()
