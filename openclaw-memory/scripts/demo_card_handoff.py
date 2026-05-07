"""End-to-end card demo:
  1. Send demo messages to Feishu group, capture real message_ids
  2. Build events with real IDs
  3. Extract memory via RuleBasedExtractor
  4. Render result as Feishu interactive card JSON
  5. Send card to group

Usage:
  python scripts/demo_card_handoff.py --chat-id oc_xxx [--skip-send-messages]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor
from memory.store import MemoryStore

LARK_CLI = "/Users/flewolf/.local/bin/lark-cli"


DEMO_MESSAGES = [
    ("何璐(产品)", "目标：完成商城App v2.0 周五前提测"),
    ("何璐(产品)", "负责人：吴凡负责商品列表和购物车"),
    ("何璐(产品)", "负责人：小杨负责支付和订单"),
    ("何璐(产品)", "决策：确定支付走微信支付，不接支付宝"),
    ("何璐(产品)", "DDL 周五前必须提测"),
    ("小杨(前端)", "阻塞：微信支付的商户证书还没申请下来"),
    ("吴凡(前端)", "小杨今天请假，支付那块明天他来"),
    ("何璐(产品)", "暂缓：退款功能这版先不做，下个迭代再加"),
    ("何璐(产品)", "下一步：吴凡明天开始切图"),
]


def lark_run(args: list[str], retries: int = 3) -> dict:
    env = os.environ.copy()
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    env.setdefault("ALL_PROXY", "http://127.0.0.1:7890")
    last_err = None
    for attempt in range(retries):
        proc = subprocess.run([LARK_CLI] + args, capture_output=True, text=True, env=env, timeout=30)
        out = proc.stdout.strip()
        lines = [l for l in out.split("\n") if l and not l.startswith("[lark-cli]")]
        if lines:
            try:
                return json.loads("\n".join(lines))
            except json.JSONDecodeError:
                last_err = f"Bad JSON: {out}"
        else:
            last_err = f"empty output. stderr: {proc.stderr.strip()[:200]}"
        if attempt < retries - 1:
            wait = 2 * (attempt + 1)
            print(f"  [retry {attempt+1}/{retries}] {last_err[:80]} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"lark-cli failed after {retries} retries: {last_err}")


def send_demo_messages(chat_id: str) -> list[dict]:
    """Send each demo message via bot, capture real message_id + timestamp."""
    print(f"=== Sending {len(DEMO_MESSAGES)} demo messages to {chat_id} ===\n")
    events = []
    base_time = int(time.time()) - 86400 * 2

    for i, (sender, text) in enumerate(DEMO_MESSAGES):
        full_text = f"[{sender}] {text}"
        result = lark_run([
            "im", "+messages-send", "--as", "bot",
            "--chat-id", chat_id, "--text", full_text
        ])
        if not result.get("ok"):
            print(f"  [FAIL] {full_text}")
            print(f"    {result}")
            continue
        msg_id = result["data"]["message_id"]
        created_at = result["data"]["create_time"]
        events.append({
            "project_id": "demo-card",
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "created_at": created_at.replace(" ", "T"),
            "sender": {"id": f"ou_demo_{i}", "name": sender, "sender_type": "user"},
        })
        print(f"  [OK] {sender}: {text[:40]}  (msg_id={msg_id[:20]}...)")
        time.sleep(0.6)

    print(f"\n=== Sent {len(events)} messages successfully ===\n")
    return events


def build_card(items: list, project_id: str, chat_id: str) -> dict:
    """Build a Feishu interactive card JSON from extracted memories."""
    by_type = {}
    for item in items:
        by_type.setdefault(item.state_type, []).append(item)

    elements = []

    # Header subtitle
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**项目**：{project_id}　|　**生成时间**：{time.strftime('%Y-%m-%d %H:%M')}"}
    })
    elements.append({"tag": "hr"})

    def _evidence_note(item):
        """Append evidence as a separate note element (grey small font in Feishu)."""
        if not item.source_refs:
            return None
        ref = item.source_refs[0]
        sender = ref.sender_name or "?"
        excerpt = (ref.excerpt or "").strip()
        if len(excerpt) > 80:
            excerpt = excerpt[:80] + "…"
        return {
            "tag": "note",
            "elements": [
                {"tag": "lark_md", "content": f"📎 {sender}：{excerpt}"}
            ]
        }

    def _add_section(title, items_list, value_strip=""):
        if not items_list:
            return
        for i, it in enumerate(items_list):
            v = it.current_value
            if value_strip:
                v = v.replace(value_strip, "").strip()
            prefix = f"{title}\n" if i == 0 else ""
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"{prefix}- {v}"}
            })
            note = _evidence_note(it)
            if note:
                elements.append(note)

    # Goals
    if by_type.get("project_goal"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "🎯 **项目目标**"}})
        for it in by_type["project_goal"][:1]:
            v = it.current_value.replace("目标：", "").strip()
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{v}**"}})
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    # Owners (deduped)
    if by_type.get("owner"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "👥 **负责人**"}})
        seen = set()
        for it in by_type["owner"]:
            v = it.current_value.replace("负责人：", "").strip()
            if v in seen or len(v) < 3:
                continue
            seen.add(v)
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"- {v}"}})
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    # Decisions
    if by_type.get("decision"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "✅ **已确认决策**"}})
        for it in by_type["decision"][:5]:
            v = it.current_value.replace("决策：", "").strip()
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"- {v}"}})
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    # Blockers
    if by_type.get("blocker"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "🚨 **当前阻塞**"}})
        for it in by_type["blocker"][:5]:
            v = it.current_value.replace("阻塞：", "").strip()
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"- {v}"}})
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    # Deadline
    if by_type.get("deadline"):
        ddl = by_type["deadline"][0].current_value
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"⏰ **DDL**：{ddl}"}})
        elements.append({"tag": "hr"})

    # Deferred
    if by_type.get("deferred"):
        lines = ["⏸️ **暂缓事项**"]
        for it in by_type["deferred"][:3]:
            v = it.current_value.replace("暂缓：", "").strip()
            lines.append(f"- {v}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        elements.append({"tag": "hr"})

    # Member status
    if by_type.get("member_status"):
        lines = ["👤 **成员状态**"]
        for it in by_type["member_status"][:3]:
            sender = it.source_refs[0].sender_name if it.source_refs else "?"
            lines.append(f"- {sender}：{it.current_value}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        elements.append({"tag": "hr"})

    # Next step
    if by_type.get("next_step"):
        seen = set()
        lines = ["▶️ **建议下一步**"]
        for it in by_type["next_step"]:
            v = it.current_value.replace("下一步：", "").strip()
            if v in seen or "阻塞" in v:
                continue
            seen.add(v)
            lines.append(f"- {v}")
            if len(seen) >= 3:
                break
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    # Footer
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "lark_md",
             "content": f"由 OpenClaw Memory Engine 从 {len(items)} 条结构化记忆生成 · 每条都可追溯到原始消息"}
        ]
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "项目交接摘要 · 商城App v2.0"},
            "subtitle": {"tag": "plain_text", "content": "无需翻聊天记录，0 秒了解项目现状"},
            "template": "blue",
        },
        "elements": elements,
    }
    return card


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", default="oc_e1c6a2c2a42b67606b91ad69bab226f4")
    parser.add_argument("--skip-send-messages", action="store_true",
                        help="Skip sending demo messages, use cached events")
    parser.add_argument("--cache-file", default="/tmp/demo_card_events.json")
    args = parser.parse_args()

    cache = Path(args.cache_file)

    if args.skip_send_messages and cache.exists():
        print(f"Using cached events from {cache}")
        events = json.loads(cache.read_text())
    else:
        events = send_demo_messages(args.chat_id)
        cache.write_text(json.dumps(events, ensure_ascii=False, indent=2))

    print("=== Extracting memory ===")
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        engine = MemoryEngine(store, RuleBasedExtractor())
        engine.ingest_events(events, debounce=False)
        items = store.list_items("demo-card")
    print(f"Extracted {len(items)} memories\n")

    print("=== Building card JSON ===")
    card = build_card(items, "商城App v2.0", args.chat_id)
    card_json = json.dumps(card, ensure_ascii=False)
    Path("/tmp/demo_card.json").write_text(card_json)
    print(f"Card saved to /tmp/demo_card.json ({len(card_json)} bytes)\n")

    print("=== Sending card to Feishu ===")
    result = lark_run([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", args.chat_id,
        "--msg-type", "interactive",
        "--content", card_json,
    ])
    if result.get("ok"):
        print(f"Card sent! message_id={result['data']['message_id']}")
        print(f"Open Feishu group to see the card.")
    else:
        print(f"Failed: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
