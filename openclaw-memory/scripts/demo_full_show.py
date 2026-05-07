"""OpenClaw Memory Engine — One-Click Show Demo.

Three-act demo runnable with a single command. Default mode is console-only
(safe for evaluators); pass --feishu to also send cards to a Feishu group.

Usage:
  python scripts/demo_full_show.py                  # console only, safe
  python scripts/demo_full_show.py --feishu         # also send cards to Feishu
  python scripts/demo_full_show.py --quick          # skip Act 1 extraction recompute

Acts:
  1. RuleBased vs Hybrid+DeepSeek extraction on 15 colloquial messages
  2. AI Agent loop: trigger question → reads Memory + Pattern → DeepSeek
     reasons → reply card → writeback (audit trail)
  3. Memory audit: pretty-print the persistent store with AI/human authorship
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.pattern_memory import generate_all_patterns
from memory.store import MemoryStore

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e71397d04b974b02a84b3f02b4b0302e")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"
DEFAULT_PROJECT = "natural-daily"
SCENARIO_FILE = ROOT / "examples" / "natural_chat_scenarios.jsonl"
DEFAULT_SCENARIO_ID = "natural_daily_standup"


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------
def banner(title: str, subtitle: str = "") -> None:
    print()
    print("═" * 78)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("═" * 78)


def section(title: str) -> None:
    print(f"\n──── {title} " + "─" * (70 - len(title)))


def pad(label: str, n: int = 16) -> str:
    return label.ljust(n)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_scenario(scenario_id: str = DEFAULT_SCENARIO_ID) -> dict:
    for line in SCENARIO_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("scenario_id") == scenario_id:
            return obj
    raise SystemExit(f"scenario {scenario_id} not found")


def make_provider() -> OpenAIProvider:
    return OpenAIProvider(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=0.1,
        max_tokens=4000,
    )


# ---------------------------------------------------------------------------
# Act 1 — extraction comparison
# ---------------------------------------------------------------------------
def run_act1(events: list[dict], project_id: str) -> dict[str, Any]:
    banner(
        "ACT 1 / 3  —  RuleBased vs Hybrid+DeepSeek Extraction",
        f"15 条纯口语化飞书消息，对比规则单独 vs 规则+LLM 提取覆盖率",
    )

    section("Rule-only extraction")
    t0 = time.time()
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        eng = MemoryEngine(store, RuleBasedExtractor())
        eng.ingest_events(events, debounce=False)
        rule_items = list(store.list_items(project_id))
    rule_t = time.time() - t0
    rule_types = sorted({i.state_type for i in rule_items})
    print(f"  → {len(rule_items)} memories  ·  {rule_t*1000:.0f} ms")
    print(f"  → types: {rule_types}")
    for it in rule_items:
        sender = it.source_refs[0].sender_name if it.source_refs else "?"
        print(f"      · {pad(it.state_type, 14)} {it.current_value[:40]:42s} (by {sender})")

    section("Hybrid extraction (rules first → LLM for fuzzy)")
    t0 = time.time()
    provider = make_provider()
    hybrid = HybridExtractor(
        rule_extractor=RuleBasedExtractor(),
        llm_extractor=LLMExtractor(provider, fallback=RuleBasedExtractor()),
    )
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        eng = MemoryEngine(store, hybrid)
        eng.ingest_events(events, debounce=False)
        hyb_items = list(store.list_items(project_id))
    hyb_t = time.time() - t0
    hyb_types = sorted({i.state_type for i in hyb_items})
    print(f"  → {len(hyb_items)} memories  ·  {hyb_t:.1f} s (incl. DeepSeek)")
    print(f"  → types: {hyb_types}")
    for it in hyb_items:
        sender = it.source_refs[0].sender_name if it.source_refs else "?"
        owner = f" [owner={it.owner}]" if it.owner else ""
        print(f"      · {pad(it.state_type, 14)} {it.current_value[:38]:40s}{owner} (by {sender})")

    only_hybrid = sorted(set(hyb_types) - set(rule_types))
    section("Δ 差异（Hybrid 多识别出来的 state_type）")
    if only_hybrid:
        print(f"  💡 {only_hybrid}")
        print(f"  规则只识别带关键词的（请假/暂缓），Hybrid 通过 LLM 抽出隐式语义")
    else:
        print("  类型集相同；差异在条目数量与字段精度。")

    return {
        "rule_items": rule_items,
        "hybrid_items": hyb_items,
        "rule_time_ms": int(rule_t * 1000),
        "hybrid_time_s": hyb_t,
    }


# ---------------------------------------------------------------------------
# Act 2 — AI Agent loop
# ---------------------------------------------------------------------------
def run_act2(
    events: list[dict],
    project_id: str,
    chat_id: str,
    *,
    feishu: bool,
) -> dict[str, Any]:
    banner(
        "ACT 2 / 3  —  AI Agent Loop (Memory + Pattern → DeepSeek → 飞书卡片)",
        '触发问题: "@bot 周五能上线吗？我有点担心"',
    )

    # Use a clean disposable store to make the demo deterministic
    data_dir = ROOT / "data" / "demo_full_show"
    data_dir.mkdir(parents=True, exist_ok=True)
    for f in data_dir.glob("*.jsonl"):
        f.unlink()
    for f in data_dir.glob("*.json"):
        f.unlink()

    section("Step 2.1  Bootstrap Memory (Hybrid extraction on 15 events)")
    provider = make_provider()
    hybrid = HybridExtractor(
        rule_extractor=RuleBasedExtractor(),
        llm_extractor=LLMExtractor(provider, fallback=RuleBasedExtractor()),
    )
    store = MemoryStore(data_dir)
    eng = MemoryEngine(store, hybrid)
    eng.ingest_events(events, debounce=False)
    items = list(store.list_items(project_id))
    print(f"  → {len(items)} memories persisted to {data_dir}")

    section("Step 2.2  Generate Work Patterns (V1.18 Pattern Memory)")
    patterns = generate_all_patterns(items, project_id)
    if patterns:
        for p in patterns:
            print(f"  · [{p.pattern_type:22s}] {p.summary[:70]}")
    else:
        print("  (no patterns produced — dataset too small)")

    section("Step 2.3  Call AI Agent — read Memory + Patterns → DeepSeek")
    import demo_agent_loop  # noqa
    trigger = "@bot 周五能上线吗？我有点担心"
    sender_name = "产品-小李"
    prompt = demo_agent_loop.build_agent_prompt(
        items, project_id, trigger, sender_name, "risk-analyzer",
    )
    print(f"  prompt: {len(prompt)} chars  ·  calling DeepSeek...")
    t0 = time.time()
    response = demo_agent_loop.call_agent(prompt)
    elapsed = time.time() - t0
    print(f"  → ok in {elapsed:.1f} s  ·  risk={response.get('risk_level')}  ·  "
          f"actions={len(response.get('actions') or [])}")
    print(f"  AI summary: {response.get('summary_one_line', '')}")
    for a in (response.get("actions") or [])[:5]:
        prio = (a.get("priority") or "p2").upper()
        print(f"      {prio}  @{a.get('target_owner', '?'):<6} → {a.get('action', '')[:60]}")

    actually_used = sorted({p.pattern_type for p in patterns} & set(response.get("patterns_used") or []))
    if actually_used:
        print(f"  patterns AI cited (verified, no hallucination): {actually_used}")

    if feishu:
        section("Step 2.4  Send to Feishu  (will appear in group)")
        # Send trigger as bot
        msg_id, _ = demo_agent_loop.send_trigger_text(chat_id, sender_name, trigger)
        time.sleep(1.5)
        # Send card
        card = demo_agent_loop.build_response_card(
            chat_id, trigger, sender_name, response, items, "risk-analyzer",
        )
        demo_agent_loop.send_card(chat_id, card, "agent-response")
        # Writeback
        new_item = demo_agent_loop.writeback_ai_action(
            store, project_id, response, msg_id, chat_id, "risk-analyzer",
        )
        print(f"  → memory_id={new_item.memory_id} (actor_type=ai_agent)")
    else:
        section("Step 2.4  (Feishu send skipped — pass --feishu to enable)")

    return {"items": items, "patterns": patterns, "response": response}


# ---------------------------------------------------------------------------
# Act 3 — Memory audit
# ---------------------------------------------------------------------------
def run_act3(act2: dict[str, Any]) -> None:
    banner(
        "ACT 3 / 3  —  Memory Audit Trail",
        "看持久化的状态：规则项 + LLM 项 + Pattern 衍生 + AI 行动（actor_type）",
    )

    items = act2["items"]
    patterns = act2["patterns"]
    response = act2["response"]

    section(f"Items in store ({len(items)} memories)")
    by_type: dict[str, list] = {}
    for it in items:
        by_type.setdefault(it.state_type, []).append(it)
    for t, bucket in sorted(by_type.items()):
        print(f"  {pad(t, 14)} × {len(bucket)}")

    section("Authorship breakdown")
    # In this simple show, all `items` are pre-AI; AI writeback (if --feishu) was added but not in `items`.
    rule_count = sum(1 for it in items if it.confidence >= 0.85)
    llm_count = sum(1 for it in items if 0.65 <= it.confidence < 0.85)
    print(f"  · 高置信度 (规则严格命中, conf ≥ 0.85): {rule_count}")
    print(f"  · LLM 抽取/扩展项 (0.65 ≤ conf < 0.85): {llm_count}")
    print(f"  · AI Agent 写回项 (actor_type=ai_agent):  ← 见 --feishu 模式下产生")

    section("Patterns derived (second-order, no message re-scan)")
    for p in patterns:
        print(f"  [{p.pattern_type}]  scope={p.scope}  conf={p.confidence:.2f}")
        print(f"      {p.summary[:120]}")

    section("AI's structured response (just generated)")
    print(json.dumps({
        "summary_one_line": response.get("summary_one_line"),
        "risk_level": response.get("risk_level"),
        "key_findings_count": len(response.get("key_findings") or []),
        "actions_count": len(response.get("actions") or []),
        "patterns_used": response.get("patterns_used"),
        "memory_writeback_state_type": (response.get("memory_writeback") or {}).get("state_type"),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Memory Engine one-click show.")
    parser.add_argument("--feishu", action="store_true",
                        help="Also push trigger + AI card to Feishu (writes to chat group)")
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO_ID)
    parser.add_argument("--quick", action="store_true",
                        help="Skip Act 1 extraction recompute (Act 2 still runs Hybrid)")
    args = parser.parse_args()

    banner(
        "OpenClaw Memory Engine  —  Hybrid Human/AI Team OS  Demo",
        f"Mode: {'LIVE Feishu' if args.feishu else 'Console-only'}   "
        f"Project: {args.project_id}   Scenario: {args.scenario}",
    )

    scenario = load_scenario(args.scenario)
    events = scenario["events"]
    print(f"\nScenario:    {scenario['title']}")
    print(f"Events:      {len(events)} 条 ({scenario.get('description', '')[:60]})")

    if not args.quick:
        run_act1(events, args.project_id)
    else:
        print("\n[Act 1 skipped via --quick]")

    act2_data = run_act2(
        events, args.project_id, args.chat_id, feishu=args.feishu,
    )

    run_act3(act2_data)

    banner("Demo Complete  ✨", "Thanks for watching.")


if __name__ == "__main__":
    main()
