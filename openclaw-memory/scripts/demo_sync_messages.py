"""Demo script: sync Feishu chat messages into data/raw_events.jsonl.

V1.10 修复：基于真实飞书 API 返回结构修正字段映射。
- 真实 API 中 content 是纯文本字符串（text 消息）或 JSON 字符串（post 消息）
- 消息列表和 mget 均不返回 at_list，@提及信息编码在 content 文本中
- sender 对 system 消息为全空字符串，需过滤
"""

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
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without writing to file.")
    parser.add_argument("--include-system", action="store_true", help="Include system messages (default: skip).")
    return parser.parse_args()


def normalize_messages(payload: Any, chat_id: str, project_id: str) -> list[dict[str, Any]]:
    """Convert lark-cli message output into normalized raw event dicts."""
    messages = _extract_message_list(payload)
    return [_normalize_message(message, chat_id, project_id) for message in messages]


def _extract_message_list(payload: Any) -> list[dict[str, Any]]:
    """Return the most likely message list from a lark-cli JSON payload.

    真实 API 返回结构: {"data": {"messages": [...], "has_more": ..., "page_token": ...}}
    """
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
    """Normalize one lark-cli message dict into the raw event schema.

    真实 API 字段:
    - message_id: "om_xxx" (必填)
    - content: 纯文本字符串 或 JSON 字符串 (post 类型)
    - create_time: "2026-04-24 17:49" (空格分隔, 非 ISO)
    - msg_type: "text" | "post" | "system" | "image" | ...
    - sender: {id, id_type, sender_type, tenant_key}
    - deleted: bool
    - updated: bool
    """
    content = _extract_text(message.get("content", ""))
    sender = message.get("sender", {}) or {}
    return {
        "project_id": project_id,
        "chat_id": chat_id,
        "message_id": str(message.get("message_id") or ""),
        "text": content,
        "content": content,
        "msg_type": str(message.get("msg_type", "text")),
        "created_at": str(message.get("create_time") or ""),
        "sender": {
            "id": str(sender.get("id", "")),
            "sender_type": str(sender.get("sender_type", "")),
            "name": str(sender.get("name", sender.get("id", ""))),
        },
    }


def _extract_text(content: Any) -> str:
    """Extract readable text from lark-cli message content.

    真实 API:
    - text 消息: content = "你好" (纯文本字符串)
    - post 消息: content = '{"text":"...","title":"..."}' (JSON 字符串)
    - system 消息: content = "沈哲熙 invited 飞书 CLI to the group."

    json.loads 对纯文本会抛 JSONDecodeError, fallback 返回原字符串。
    对 JSON 字符串则解析并提取 text 字段。
    """
    if not isinstance(content, str):
        return str(content)
    # 快速判断: 非 JSON 开头的纯文本直接返回
    stripped = content.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(parsed, dict):
        # post 消息: 优先取 text 字段
        text = parsed.get("text")
        if text:
            return str(text)
        # 也可能有 title + content 结构
        title = parsed.get("title", "")
        body = parsed.get("content", "")
        if isinstance(body, list):
            # post 格式: content = [{"tag":"text","text":"..."}, ...]
            body_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in body
            )
            return f"{title}\n{body_text}".strip()
        return str(parsed.get("content", stripped))
    return stripped


def main() -> None:
    """Run chat sync and report how many new raw events were written."""
    args = parse_args()
    adapter = LarkCliAdapter()
    result = adapter.list_chat_messages(args.chat_id, page_size=min(args.limit, 50))
    if result.returncode != 0:
        raise SystemExit(f"lark-cli failed: {result.stderr or result.stdout}")

    all_events = normalize_messages(result.data, args.chat_id, args.project_id)

    # 过滤 system 消息（sender_type 为空或 "system"）
    if not args.include_system:
        system_count = sum(
            1 for e in all_events
            if e["msg_type"] == "system" or e["sender"]["sender_type"] in ("", "system")
        )
        events = [
            e for e in all_events
            if e["msg_type"] != "system" and e["sender"]["sender_type"] not in ("", "system")
        ]
    else:
        system_count = 0
        events = all_events

    events = events[: args.limit]

    if args.dry_run:
        print(f"[DRY-RUN] 共 {len(all_events)} 条消息, "
              f"过滤 {system_count} 条系统消息, "
              f"将写入 {len(events)} 条")
        for e in events:
            print(f"  [{e['msg_type']}] {e['sender']['sender_type']}: {e['text'][:60]}")
        return

    store = MemoryStore(args.data_dir)
    written = store.append_raw_events(events)
    print(f"共 {len(all_events)} 条消息, "
          f"过滤 {system_count} 条系统消息, "
          f"写入 {written} 条到 {store.raw_events_path}")


if __name__ == "__main__":
    main()
