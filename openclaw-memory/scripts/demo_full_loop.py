"""End-to-end colloquial demo: RuleBased vs Hybrid (DeepSeek) head-to-head.

Pipeline:
  1. Read natural-language events from examples/natural_chat_scenarios.jsonl
  2. Send each message to Feishu group as bot (capture real message_id)
  3. Run RuleBasedExtractor on events → Card A
  4. Run HybridExtractor (rules + DeepSeek-V4-Pro) on events → Card B
  5. Send both cards back to the group so users see the comparison

Usage:
  python scripts/demo_full_loop.py --chat-id oc_xxx
  python scripts/demo_full_loop.py --skip-send --cache /tmp/full_loop_events.json
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.store import MemoryStore

LARK_CLI = "/Users/flewolf/.local/bin/lark-cli"
SCENARIO_FILE = ROOT / "examples" / "natural_chat_scenarios.jsonl"
DEFAULT_SCENARIO_ID = "natural_daily_standup"
DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"

# DeepSeek config (overridable via env)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e71397d04b974b02a84b3f02b4b0302e")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


# ---------------------------------------------------------------------------
# lark-cli wrapper with retry
# ---------------------------------------------------------------------------
def lark_run(args: list[str], retries: int = 3) -> dict:
    env = os.environ.copy()
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    env.setdefault("ALL_PROXY", "http://127.0.0.1:7890")
    last_err: str | None = None
    for attempt in range(retries):
        proc = subprocess.run(
            [LARK_CLI] + args, capture_output=True, text=True, env=env, timeout=45
        )
        out = proc.stdout.strip()
        lines = [l for l in out.split("\n") if l and not l.startswith("[lark-cli]")]
        if lines:
            try:
                return json.loads("\n".join(lines))
            except json.JSONDecodeError:
                last_err = f"Bad JSON: {out[:200]}"
        else:
            last_err = f"empty output. stderr: {proc.stderr.strip()[:200]}"
        if attempt < retries - 1:
            wait = 2 * (attempt + 1)
            print(f"  [retry {attempt+1}/{retries}] {(last_err or '')[:90]} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"lark-cli failed after {retries} retries: {last_err}")


# ---------------------------------------------------------------------------
# Load colloquial scenario
# ---------------------------------------------------------------------------
def load_scenario(path: Path, scenario_id: str) -> dict:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("scenario_id") == scenario_id:
            return obj
    raise SystemExit(f"scenario {scenario_id} not found in {path}")


# ---------------------------------------------------------------------------
# Send messages and rewrite event ids
# ---------------------------------------------------------------------------
def send_demo_messages(events: list[dict], chat_id: str) -> list[dict]:
    print(f"=== Sending {len(events)} colloquial messages to {chat_id} ===\n")
    new_events: list[dict] = []
    for i, ev in enumerate(events):
        sender = ev.get("sender", {}).get("name", "?")
        text = ev["text"]
        full_text = f"[{sender}] {text}"
        result = lark_run([
            "im", "+messages-send", "--as", "bot",
            "--chat-id", chat_id, "--text", full_text,
        ])
        if not result.get("ok"):
            print(f"  [FAIL] {full_text}\n    {result}")
            continue
        msg_id = result["data"]["message_id"]
        created_at = result["data"]["create_time"].replace(" ", "T")
        new_ev = dict(ev)
        new_ev["chat_id"] = chat_id
        new_ev["message_id"] = msg_id
        new_ev["created_at"] = created_at
        new_events.append(new_ev)
        print(f"  [{i+1:02d}] {sender}: {text[:32]}  (msg={msg_id[:18]}…)")
        time.sleep(0.5)
    print(f"\n=== Sent {len(new_events)}/{len(events)} messages ===\n")
    return new_events


# ---------------------------------------------------------------------------
# Card builder (works for both rule and hybrid results)
# ---------------------------------------------------------------------------
def _evidence_note(item) -> dict | None:
    if not item.source_refs:
        return None
    ref = item.source_refs[0]
    sender = ref.sender_name or "?"
    excerpt = (ref.excerpt or "").strip().replace("\n", " ")
    if len(excerpt) > 70:
        excerpt = excerpt[:70] + "…"
    return {
        "tag": "note",
        "elements": [
            {"tag": "lark_md", "content": f"📎 {sender}：{excerpt}"}
        ],
    }


def _strip_prefix(value: str, prefixes: list[str]) -> str:
    v = value.strip()
    for p in prefixes:
        if v.startswith(p):
            v = v[len(p):].strip()
    return v


def _is_raw_quote(item) -> bool:
    """True when current_value is essentially the original message excerpt
    (rule extractor often copies the whole sentence verbatim)."""
    if not item.source_refs:
        return False
    excerpt = (item.source_refs[0].excerpt or "").strip().rstrip("。.?！!，,")
    value = item.current_value.strip().rstrip("。.?！!，,")
    if not value or not excerpt:
        return False
    return value == excerpt


def _clean_items_for_display(items: list) -> list:
    """Drop noisy rule-style raw quotes for the Hybrid card.

    Strategy:
      1. Drop any item whose `current_value` is a verbatim copy of the
         source message excerpt — those are rule extractor leftovers
         that add no semantic value over what the LLM already abstracted.
      2. Then dedupe within (message_id, state_type) keeping the highest
         confidence item — handles the rule "请假" vs. LLM "赵六明天请假"
         duplicate where the bigram similarity check failed to merge.
    """
    no_raw = [it for it in items if not _is_raw_quote(it)]

    by_key: dict[tuple, object] = {}
    for it in no_raw:
        msg_id = it.source_refs[0].message_id if it.source_refs else f"_no_src_{id(it)}"
        k = (msg_id, it.state_type)
        cur = by_key.get(k)
        if cur is None or it.confidence > cur.confidence:
            by_key[k] = it
    return list(by_key.values())


def build_card(
    items: list, title: str, subtitle: str, template: str, footer: str,
    clean: bool = True,
) -> dict:
    if clean:
        items = _clean_items_for_display(items)
    by_type: dict[str, list] = {}
    for it in items:
        by_type.setdefault(it.state_type, []).append(it)

    elements: list[dict] = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**生成时间**：{time.strftime('%Y-%m-%d %H:%M')}　|　**条数**：{len(items)} 条记忆"}
    })
    elements.append({"tag": "hr"})

    def _section(emoji_title: str, type_key: str, strip_prefixes: list[str], dedup: bool = False):
        items_list = by_type.get(type_key) or []
        if not items_list:
            return
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": emoji_title}})
        seen: set[str] = set()
        shown = 0
        for it in items_list:
            v = _strip_prefix(it.current_value, strip_prefixes)
            if dedup:
                if v in seen or len(v) < 2:
                    continue
                seen.add(v)
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"- {v}"}
            })
            note = _evidence_note(it)
            if note:
                elements.append(note)
            shown += 1
            if shown >= 6:
                break
        elements.append({"tag": "hr"})

    _section("🎯 **项目目标**", "project_goal", ["目标：", "目标:"])
    _section("👥 **负责人**", "owner", ["负责人：", "负责人:"], dedup=True)
    _section("✅ **决策**", "decision", ["决策：", "决策:"])
    _section("🚨 **阻塞**", "blocker", ["阻塞：", "阻塞:"])

    if by_type.get("deadline"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "⏰ **截止时间**"}})
        for it in by_type["deadline"][:3]:
            elements.append({"tag": "div",
                             "text": {"tag": "lark_md", "content": f"- {it.current_value}"}})
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    _section("⏸️ **暂缓事项**", "deferred", ["暂缓：", "暂缓:"])

    if by_type.get("member_status"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "👤 **成员状态**"}})
        for it in by_type["member_status"][:3]:
            sender = it.source_refs[0].sender_name if it.source_refs else "?"
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"- {sender}：{it.current_value}"}
            })
            note = _evidence_note(it)
            if note:
                elements.append(note)
        elements.append({"tag": "hr"})

    _section("▶️ **下一步**", "next_step", ["下一步：", "下一步:"], dedup=True)

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": footer}],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": template,
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Comparison summary card
# ---------------------------------------------------------------------------
def build_compare_card(rule_items: list, hybrid_items: list, scenario_title: str) -> dict:
    rule_types = sorted({it.state_type for it in rule_items})
    hybrid_types = sorted({it.state_type for it in hybrid_items})
    only_hybrid = sorted(set(hybrid_types) - set(rule_types))

    elements = [
        {"tag": "div", "text": {"tag": "lark_md",
            "content": f"**口语化测试场景**：{scenario_title}\n"
                       f"原始消息：15 条纯口语 · 无任何「目标/决策/负责人」结构化前缀"}},
        {"tag": "hr"},
        {"tag": "column_set", "flex_mode": "none", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**📐 RuleBased**\n提取记忆：**{len(rule_items)}** 条\n类型："
                              + ("、".join(rule_types) if rule_types else "（空）")}}
            ]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                    "content": f"**🤖 Hybrid + DeepSeek**\n提取记忆：**{len(hybrid_items)}** 条\n类型："
                              + ("、".join(hybrid_types) if hybrid_types else "（空）")}}
            ]},
        ]},
        {"tag": "hr"},
    ]

    if only_hybrid:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "**🔍 只有 Hybrid 才识别出来的隐式语义**：" + "、".join(only_hybrid)}})
    else:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "RuleBased 和 Hybrid 提取覆盖类型一致；差异在数量与字段精度。"}})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
            "content": "下面紧跟两张详情卡片：先看规则版「漏了什么」，再看 Hybrid 版「补全了什么」"}],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "OpenClaw · 口语化全链路对比演示"},
            "subtitle": {"tag": "plain_text", "content": "同一批消息，规则 vs 规则+大模型"},
            "template": "indigo",
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Send card via lark-cli
# ---------------------------------------------------------------------------
def send_card(chat_id: str, card: dict, label: str) -> str:
    card_json = json.dumps(card, ensure_ascii=False)
    print(f"=== Sending card [{label}] ({len(card_json)} bytes) ===")
    result = lark_run([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card_json,
    ])
    if not result.get("ok"):
        raise RuntimeError(f"send card failed: {result}")
    msg_id = result["data"]["message_id"]
    print(f"  → message_id={msg_id}\n")
    return msg_id


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def extract_rule(events: list[dict], project_id: str) -> list:
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        engine = MemoryEngine(store, RuleBasedExtractor())
        engine.ingest_events(events, debounce=False)
        return list(store.list_items(project_id))


def extract_hybrid(events: list[dict], project_id: str) -> list:
    provider = OpenAIProvider(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=0.1,
        max_tokens=4000,
    )
    rule = RuleBasedExtractor()
    llm = LLMExtractor(provider, fallback=rule)
    hybrid = HybridExtractor(rule_extractor=rule, llm_extractor=llm)

    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        engine = MemoryEngine(store, hybrid)
        engine.ingest_events(events, debounce=False)
        return list(store.list_items(project_id))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO_ID)
    parser.add_argument("--scenario-file", default=str(SCENARIO_FILE))
    parser.add_argument("--skip-send", action="store_true",
                        help="skip sending demo messages, reuse cache")
    parser.add_argument("--cache", default="/tmp/full_loop_events.json")
    parser.add_argument("--no-cards", action="store_true",
                        help="skip sending cards (only print extraction summary)")
    args = parser.parse_args()

    scenario = load_scenario(Path(args.scenario_file), args.scenario)
    project_id = scenario["project_id"]
    title = scenario.get("title", args.scenario)
    raw_events = scenario["events"]
    print(f"Scenario: {title}  ({len(raw_events)} events, project_id={project_id})\n")

    cache = Path(args.cache)
    if args.skip_send and cache.exists():
        print(f"Using cached events from {cache}\n")
        events = json.loads(cache.read_text())
    else:
        events = send_demo_messages(raw_events, args.chat_id)
        cache.write_text(json.dumps(events, ensure_ascii=False, indent=2))
        print(f"Cached events to {cache}\n")

    if not events:
        print("No events sent successfully. Abort.")
        sys.exit(1)

    # Make sure project_id is consistent for our scratch stores
    for ev in events:
        ev["project_id"] = project_id

    print("=== [1/2] RuleBased extraction ===")
    rule_items = extract_rule(events, project_id)
    rule_types = {it.state_type for it in rule_items}
    print(f"  → {len(rule_items)} memories, types={sorted(rule_types)}\n")

    print("=== [2/2] Hybrid (rule + DeepSeek-V4-Pro) extraction ===")
    hybrid_items = extract_hybrid(events, project_id)
    hybrid_types = {it.state_type for it in hybrid_items}
    print(f"  → {len(hybrid_items)} memories, types={sorted(hybrid_types)}\n")

    # Diff
    extra = hybrid_types - rule_types
    if extra:
        print(f"💡 Hybrid 多识别出的类型：{sorted(extra)}")
    else:
        print("💡 类型集相同；差异在条目数量与字段补全。")
    print()

    if args.no_cards:
        return

    # Compare card → Rule card → Hybrid card
    compare = build_compare_card(rule_items, hybrid_items, title)
    rule_card = build_card(
        rule_items,
        title="📐 RuleBased 提取结果",
        subtitle="只用关键词/正则，不调用大模型（baseline）",
        template="grey",
        footer=f"基于 {len(rule_items)} 条规则提取的记忆 · 显示原始抽取，未做去重清洗",
        clean=False,  # show rule output warts and all
    )
    hybrid_card = build_card(
        hybrid_items,
        title="🤖 Hybrid 提取结果（规则 + DeepSeek）",
        subtitle="规则先跑，模糊语义自动 fallback 到 LLM 抽取并清洗",
        template="blue",
        footer="LLM 已对原文做语义抽象与归一；同一消息的冗余规则项自动去重",
        clean=True,
    )

    send_card(args.chat_id, compare, "compare")
    time.sleep(1.0)
    send_card(args.chat_id, rule_card, "rule")
    time.sleep(1.0)
    send_card(args.chat_id, hybrid_card, "hybrid")
    print("All three cards sent. 🎉  请打开飞书群对比效果。")


if __name__ == "__main__":
    main()
