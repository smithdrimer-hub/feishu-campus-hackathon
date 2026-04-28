"""Demo script: sync Feishu chat messages into data/raw_events.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapters.lark_cli_adapter import LarkCliAdapter  # noqa: E402
from memory.store import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for chat sync."""
    parser = argparse.ArgumentParser(description="Sync Feishu chat messages into raw_events.jsonl.")
    parser.add_argument("--chat-id", required=True, help="Feishu chat_id, such as oc_xxx.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum messages to request, capped by lark-cli page size.")
    parser.add_argument("--project-id", default="openclaw-memory-demo", help="Project id stored on each raw event.")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Directory for raw_events.jsonl and memory_state.json.")
    return parser.parse_args()


def normalize_messages(payload: Any, chat_id: str, project_id: str) -> list[dict[str, Any]]:
    """Convert lark-cli message output into normalized raw event dicts."""
    messages = _extract_message_list(payload)
    return [_normalize_message(message, chat_id, project_id) for message in messages]


def _extract_message_list(payload: Any) -> list[dict[str, Any]]:
    """Return the most likely message list from a lark-cli JSON payload."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, dict):
        for key in ("messages", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _normalize_message(message: dict[str, Any], chat_id: str, project_id: str) -> dict[str, Any]:
    """Normalize one lark-cli message dict into the raw event schema."""
    content = _extract_text(message.get("content", ""))
    return {
        "project_id": project_id,
        "chat_id": chat_id,
        "message_id": str(message.get("message_id") or message.get("id") or ""),
        "text": content,
        "content": content,
        "created_at": str(message.get("create_time") or message.get("created_at") or ""),
        "sender": message.get("sender", {}),
        "raw": message,
    }


def _extract_text(content: Any) -> str:
    """Extract readable text from lark-cli message content."""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(parsed, dict):
            return str(parsed.get("text") or parsed.get("content") or content)
        return content
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or content)
    return str(content)


def main() -> None:
    """Run chat sync and report how many new raw events were written."""
    args = parse_args()
    adapter = LarkCliAdapter()
    result = adapter.list_chat_messages(args.chat_id, page_size=min(args.limit, 50))
    if result.returncode != 0:
        raise SystemExit(f"lark-cli failed: {result.stderr or result.stdout}")
    events = normalize_messages(result.data, args.chat_id, args.project_id)
    store = MemoryStore(args.data_dir)
    written = store.append_raw_events(events[: args.limit])
    print(f"Synced {written} new raw events to {store.raw_events_path}")


if __name__ == "__main__":
    main()
