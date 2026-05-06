"""OpenClaw Memory Engine — One-day-in-the-life cinematic demo.

故事板：5 人团队 + 1 AI 同事，从早 8:30 到深夜 23:00 的一天。
每个场景一个独立函数，可单独跑也可串成完整电影。

Usage:
  python scripts/demo_movie.py --scene morning            # 单场景
  python scripts/demo_movie.py --scene orchestrator
  python scripts/demo_movie.py --scene hotspot
  python scripts/demo_movie.py --scene handoff_risk
  python scripts/demo_movie.py --scene standup
  python scripts/demo_movie.py --scene handoff_summary
  python scripts/demo_movie.py --scene review_desk
  python scripts/demo_movie.py --all                      # 完整电影 (~2 min)
  python scripts/demo_movie.py --all --feishu             # 同时发飞书
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.pattern_memory import generate_all_patterns
from memory.schema import MemoryItem
from memory.store import MemoryStore

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e71397d04b974b02a84b3f02b4b0302e")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"
DEFAULT_PROJECT = "movie-demo"
SCENARIO_FILE = ROOT / "examples" / "movie_demo_scenario.jsonl"
LARK_CLI = "/Users/flewolf/.local/bin/lark-cli"


# ---------------------------------------------------------------------------
# Feishu helpers
# ---------------------------------------------------------------------------
def lark_run(cli_args: list[str], retries: int = 3) -> dict:
    import subprocess
    env = os.environ.copy()
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7890")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:7890")
    env.setdefault("ALL_PROXY", "http://127.0.0.1:7890")
    last_err: str | None = None
    for attempt in range(retries):
        proc = subprocess.run(
            [LARK_CLI] + cli_args, capture_output=True, text=True, env=env, timeout=45,
        )
        out = proc.stdout.strip()
        lines = [l for l in out.split("\n") if l and not l.startswith("[lark-cli]")]
        if lines:
            try:
                return json.loads("\n".join(lines))
            except json.JSONDecodeError:
                last_err = f"Bad JSON: {out[:200]}"
        else:
            last_err = f"empty stdout. stderr: {proc.stderr.strip()[:200]}"
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"lark-cli failed after {retries} retries: {last_err}")


def send_card(card: dict, chat_id: str, label: str = "") -> str:
    """Push an interactive card to a Feishu chat. Return message_id."""
    content = json.dumps(card, ensure_ascii=False)
    result = lark_run([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", content,
    ])
    if not result.get("ok"):
        raise RuntimeError(f"send card[{label}] failed: {result}")
    return result["data"]["message_id"]


def evidence_note(item: MemoryItem) -> dict | None:
    """Render a single MemoryItem's first source ref as a Feishu note element."""
    if not item.source_refs:
        return None
    ref = item.source_refs[0]
    sender = ref.sender_name or "?"
    excerpt = (ref.excerpt or "").strip().replace("\n", " ")
    if len(excerpt) > 70:
        excerpt = excerpt[:70] + "…"
    return {
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": f"📎 {sender}：{excerpt}"}],
    }


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------
def slate(scene_no: str, title: str, time_marker: str, pov: str) -> None:
    """Prints a film-style scene slate before each scene."""
    print()
    print("┌" + "─" * 76 + "┐")
    print(f"│  🎬 SCENE {scene_no}  ·  {time_marker}".ljust(78) + "│")
    print(f"│  📍 {title}".ljust(78) + "│")
    print(f"│  👤 POV: {pov}".ljust(78) + "│")
    print("└" + "─" * 76 + "┘")


def beat(text: str) -> None:
    print(f"   → {text}")


# ---------------------------------------------------------------------------
# Memory bootstrap (run once, share across scenes)
# ---------------------------------------------------------------------------
def bootstrap_memory(project_id: str, fresh: bool = True) -> tuple[MemoryStore, list[MemoryItem]]:
    """Extract events from movie scenario, persist to store, seed metadata.

    Returns (store, items). Items have demo-friendly metadata seeded for
    pattern fire-up (dependency_owner, deadline.owner).
    """
    scenario = json.loads(SCENARIO_FILE.read_text(encoding="utf-8").strip())
    events = scenario["events"]

    data_dir = ROOT / "data" / "demo_movie"
    data_dir.mkdir(parents=True, exist_ok=True)
    if fresh:
        for f in data_dir.glob("*.jsonl"):
            f.unlink()
        for f in data_dir.glob("*.json"):
            f.unlink()

    store = MemoryStore(data_dir)
    provider = OpenAIProvider(
        api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL, temperature=0.1, max_tokens=4000,
    )
    rule = RuleBasedExtractor()
    hybrid = HybridExtractor(
        rule_extractor=rule,
        llm_extractor=LLMExtractor(provider, fallback=rule),
    )
    eng = MemoryEngine(store, hybrid)
    eng.ingest_events(events, debounce=False)
    items = list(store.list_items(project_id))

    items = _seed_demo_metadata(items, store)
    return store, items


def _seed_demo_metadata(items: list[MemoryItem], store: MemoryStore) -> list[MemoryItem]:
    """Inject demo-only metadata so dependency_blocker + deadline_risk_score fire.

    In-place mutation; we keep a list reference so all downstream scenes
    share the same enriched items.
    """
    for it in items:
        if it.state_type == "blocker":
            text = it.current_value
            md = dict(it.metadata or {})
            if "设计稿" in text or "UI" in text or "原型" in text:
                md["dependency_owner"] = "设计-小林"
            elif "运维" in text or "环境" in text or "审批" in text:
                md["dependency_owner"] = "运维"
            elif "证书" in text:
                md["dependency_owner"] = "财务"
            md.setdefault("blocker_status", "open")
            it.metadata = md
        if it.state_type == "deadline" and not it.owner:
            it.owner = "前端-吴凡"
    return items


# ---------------------------------------------------------------------------
# Scene placeholder (to be filled in subsequent steps)
# ---------------------------------------------------------------------------
def scene_morning(items, store, args) -> None:
    """SCENE 1 — 个人晨报：用户视角，"我不在的这两天发生了什么"。"""
    from memory.project_state import build_morning_briefing

    slate("1", "吴凡的早安卡 (Morning Briefing)",
          "08:32 · 通勤路上", "前端-吴凡 · 出差 2 天回来")

    # 模拟"上次看群是 2 天前"
    last_seen = "2026-05-04T18:00:00"
    briefing = build_morning_briefing("吴凡", args.project_id, items, last_seen_at=last_seen)
    beat(f"recent_changes={len(briefing.get('recent_changes', []))} 条 · "
         f"waiting_on_me={len(briefing.get('waiting_on_me', []))} 项 · "
         f"deadlines={len(briefing.get('deadlines', []))} · "
         f"team_status={len(briefing.get('team_status', []))} · "
         f"actions={len(briefing.get('suggested_actions', []))}")

    elements: list[dict] = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": "**🚇 吴凡 · 通勤路上**　收到这条简报，0 秒进入工作状态"}
    })
    elements.append({"tag": "hr"})

    # 1. 你不在期间发生了什么
    changes = briefing.get("recent_changes", [])
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": f"**📥 你不在的 2 天里 ({len(changes)} 条变化)**"}})
    if changes:
        for c in changes[:5]:
            sender = c.get("sender") or "?"
            elements.append({"tag": "div",
                             "text": {"tag": "lark_md",
                                      "content": f"- [{c['type']}] {c['value']}"}})
            elements.append({"tag": "note",
                             "elements": [{"tag": "lark_md",
                                           "content": f"📎 {sender}"}]})
    else:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "_暂无新变化_"}})
    elements.append({"tag": "hr"})

    # 2. 等你处理的事
    waiting = briefing.get("waiting_on_me", [])
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": f"**🔥 等你处理的事 ({len(waiting)})**"}})
    for w in waiting[:5]:
        emoji = "🚨" if w["urgency"] == "high" else "📌"
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": f"{emoji} [{w['type']}] {w['value']}"}})
    if not waiting:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "_无_"}})
    elements.append({"tag": "hr"})

    # 3. Deadlines
    dls = briefing.get("deadlines", [])
    if dls:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": f"**⏰ 临近截止日**\n- {dls[0]['value']}"}})
        elements.append({"tag": "hr"})

    # 4. 队友状态
    team = briefing.get("team_status", [])
    if team:
        team_lines = ["**👥 队友状态**"]
        for t in team[:5]:
            team_lines.append(f"- {t['who']}：{t['status']}")
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "\n".join(team_lines)}})
        elements.append({"tag": "hr"})

    # 5. 系统建议
    actions = briefing.get("suggested_actions", [])
    if actions:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": f"**▶️ 系统为你排好的优先级**"}})
        for a in actions[:4]:
            elements.append({"tag": "div",
                             "text": {"tag": "lark_md",
                                      "content": f"`P{a['priority']}` {a['action']}"}})
            elements.append({"tag": "note",
                             "elements": [{"tag": "lark_md",
                                           "content": f"💡 {a['reason']}"}]})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": "🌅 由 Memory Engine 自动生成 · 基于 14 条结构化记忆 · "
                                 "不需要翻聊天记录，0 秒进入工作状态"}]
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "🌅 给 吴凡 的早安简报"},
            "subtitle": {"tag": "plain_text",
                         "content": "出差 2 天回来 · 自动重建工作上下文"},
            "template": "yellow",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "morning")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节  (--feishu 真发)")


def scene_orchestrator(items, store, args) -> None:
    """SCENE 2 — 全组依赖链编排：找堵塞口，多米诺式解阻塞。"""
    from memory.orchestrator import orchestrate

    slate("2", "全组任务编排卡 (Orchestrator)",
          "09:00 · 项目大群", "全员看见 · 无早会")

    plan = orchestrate(args.project_id, items)
    beat(f"actions={len(plan.actions)} · "
         f"chains={len(plan.dependency_chains)} · "
         f"team={len(plan.team_status_summary)}")

    elements: list[dict] = []
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": f"**🎯 编排策略**：{plan.generated_reason}"}})
    elements.append({"tag": "hr"})

    # 团队状态一览
    if plan.team_status_summary:
        lines = ["**👥 团队当前状态**"]
        for name, status in sorted(plan.team_status_summary.items()):
            icon = "🔴" if "阻塞" in status else ("⚪" if "不可用" in status else "🟢")
            lines.append(f"{icon} `{name}`：{status}")
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "\n".join(lines)}})
        elements.append({"tag": "hr"})

    # 依赖链可视化
    if plan.dependency_chains:
        chain_lines = ["**🔗 阻塞依赖链**（解一处，多处亮）"]
        for c in plan.dependency_chains[:5]:
            resolver = f" → 找 **{c['resolver']}**" if c.get("resolver") else ""
            chain_lines.append(
                f"⛓️ `{c['blocker']}`  卡住 **{c['blocks']}**  "
                f"(影响 {c['downstream']} 下游){resolver}"
            )
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "\n".join(chain_lines)}})
        elements.append({"tag": "hr"})

    # 行动清单
    if plan.actions:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": "**▶️ 今日行动 (按解锁下游数排序)**"}})
        for a in plan.actions[:5]:
            tag = "🔥" if a.priority == 1 else ("⚡" if a.priority == 2 else "📌")
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md",
                         "content": f"{tag} **P{a.priority} @{a.assignee}** — {a.action}"}
            })
            elements.append({
                "tag": "note",
                "elements": [{"tag": "lark_md",
                              "content": f"💡 {a.reason}"}]
            })

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": "🎯 由 Orchestrator 自动生成 · 无早会同步 · "
                                 "解一个堵塞口，下游多米诺式解锁"}]
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "🎯 今日全组任务编排"},
            "subtitle": {"tag": "plain_text",
                         "content": "拉开堵塞口 · 多米诺式解阻塞"},
            "template": "blue",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "orchestrator")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节  (--feishu 真发)")


def scene_hotspot(items, store, args) -> None:
    """SCENE 5 — 阻塞热点预警：组织级洞察，不是单点提醒。"""
    from memory.pattern_memory import generate_blocker_hotspot

    slate("5", "阻塞热点预警 (Blocker Hotspot)",
          "12:50 · 项目大群", "全员 · 系统主动播报")

    patterns = generate_blocker_hotspot(items, args.project_id)
    if not patterns:
        beat("无阻塞热点（active blocker 不足 2 条）")
        return
    p = patterns[0]
    beat(f"hotspot pattern conf={p.confidence:.2f}")

    elements: list[dict] = []
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": "**📍 系统检测到组织级阻塞模式**\n"
                                         "_这不是单条阻塞预警，是看趋势_"}})
    elements.append({"tag": "hr"})

    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": p.summary}})
    elements.append({"tag": "hr"})

    if p.evidence_refs:
        ev_lines = ["**📎 关联证据**"]
        for ref in p.evidence_refs[:5]:
            sender = ref.get("sender") or "?"
            value = ref.get("value", "")[:60]
            ev_lines.append(f"- [{sender}] {value}")
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": "\n".join(ev_lines)}})
        elements.append({"tag": "hr"})

    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": "**💡 洞察**：当同一类阻塞反复出现，说明问题不在单个任务，"
                                         "而在交付节奏 / 协作流程上。建议跟进根因。"}})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": f"📍 由 Pattern Memory 生成 · "
                                 f"confidence={p.confidence:.2f} · "
                                 f"pattern_type={p.pattern_type} · "
                                 f"窗口={p.time_window}"}],
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "📍 阻塞热点预警"},
            "subtitle": {"tag": "plain_text",
                         "content": "组织级洞察 · 看趋势不看单点"},
            "template": "orange",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "hotspot")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节")


def scene_standup(items, store, args) -> None:
    """SCENE 7 — 站会自动摘要：每天定时进群，0 早会成本。"""
    slate("7", "今日站会摘要 (Standup Summary)",
          "18:00 · 项目大群", "全员 · 自动播报")

    decisions = [i for i in items if i.state_type == "decision"]
    next_steps = [i for i in items if i.state_type == "next_step" and i.status == "active"]
    blockers = [i for i in items if i.state_type == "blocker" and i.status == "active"]
    deferred = [i for i in items if i.state_type == "deferred"]
    member_st = [i for i in items if i.state_type == "member_status"]
    deadlines = [i for i in items if i.state_type == "deadline"]
    beat(f"decisions={len(decisions)} · next_steps={len(next_steps)} · "
         f"blockers={len(blockers)} · deferred={len(deferred)}")

    elements: list[dict] = []
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": "**📊 自动从今日 26 条群消息聚合 · 0 早会成本**"}})
    elements.append({"tag": "hr"})

    # 昨日进展（最近 decision + resolved blocker）
    yesterday_lines = ["**📅 Yesterday — 昨天发生了什么**"]
    for d in decisions[:3]:
        sender = d.source_refs[0].sender_name if d.source_refs else "?"
        yesterday_lines.append(f"✅ 决策：{d.current_value[:60]}  _by {sender}_")
    for df in deferred[:2]:
        yesterday_lines.append(f"⏸️ 暂缓：{df.current_value[:60]}")
    if len(yesterday_lines) == 1:
        yesterday_lines.append("_无新动作_")
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                             "content": "\n".join(yesterday_lines)}})
    elements.append({"tag": "hr"})

    # 今日计划
    today_lines = ["**🎯 Today — 今天在做的事**"]
    for ns in next_steps[:5]:
        owner = ns.owner or "?"
        today_lines.append(f"▶️ {ns.current_value[:55]}  _@{owner}_")
    if len(today_lines) == 1:
        today_lines.append("_无明确任务_")
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                             "content": "\n".join(today_lines)}})
    elements.append({"tag": "hr"})

    # 阻塞 & 风险
    risk_lines = ["**🚨 Blockers — 现在还卡着什么**"]
    for b in blockers[:5]:
        owner = b.owner or "?"
        meta = getattr(b, "metadata", None) or {}
        dep = meta.get("dependency_owner")
        dep_hint = f" → 依赖 {dep}" if dep else ""
        risk_lines.append(f"🔴 [{owner}] {b.current_value[:50]}{dep_hint}")
    for d in deadlines[:1]:
        risk_lines.append(f"⏰ Deadline: {d.current_value[:60]}")
    if len(risk_lines) == 1:
        risk_lines.append("_无阻塞_")
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                             "content": "\n".join(risk_lines)}})
    elements.append({"tag": "hr"})

    # 成员状态
    if member_st:
        ms_lines = ["**👥 团队成员状态**"]
        for m in member_st[:5]:
            owner = m.owner or (m.source_refs[0].sender_name if m.source_refs else "?")
            ms_lines.append(f"- {owner}: {m.current_value[:40]}")
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                 "content": "\n".join(ms_lines)}})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": f"📊 由 {len(items)} 条结构化记忆 yesterday/today/blockers 三段式聚合 · "
                                 "无需手动写日报"}],
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "📊 今日站会摘要"},
            "subtitle": {"tag": "plain_text",
                         "content": "自动生成 · 替代每日早会"},
            "template": "indigo",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "standup")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节")


def scene_handoff(items, store, args) -> None:
    """SCENE 9 — 8 维度交接摘要卡：突发离场，3 秒生成接手包。"""
    slate("9", "项目交接摘要 (Handoff Summary)",
          "19:50 · 私聊给王五", "吴凡突发离场 → 王五接手")

    by_type: dict[str, list[MemoryItem]] = {}
    for it in items:
        by_type.setdefault(it.state_type, []).append(it)

    section_specs = [
        ("project_goal", "🎯 项目目标", "blue"),
        ("owner", "👥 负责人", "grey"),
        ("decision", "✅ 关键决策（必须知道）", "green"),
        ("deadline", "⏰ 截止时间", "red"),
        ("blocker", "🚨 当前阻塞", "orange"),
        ("deferred", "⏸️ 暂缓事项", "purple"),
        ("member_status", "👤 成员状态", "indigo"),
        ("next_step", "▶️ 当前任务", "blue"),
    ]

    def _emit(it: MemoryItem) -> list[dict]:
        ds = getattr(it, "decision_strength", "")
        rs = getattr(it, "review_status", "")
        strength = f" `{ds}`" if ds else ""
        review = " ⚠️" if rs == "needs_review" else ""
        owner_part = f"  _@{it.owner}_" if it.owner else ""
        block = [{"tag": "div",
                  "text": {"tag": "lark_md",
                           "content": f"- {it.current_value[:80]}{strength}{review}{owner_part}"}}]
        ev = evidence_note(it)
        if ev:
            block.append(ev)
        return block

    elements: list[dict] = []
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": "**🚨 紧急生成 · 0 秒上岗包**\n"
                                         f"基于 {len(items)} 条结构化记忆 + Pattern Memory 自动归纳"}})
    elements.append({"tag": "hr"})

    for state_type, title, _ in section_specs:
        bucket = by_type.get(state_type, [])
        if not bucket:
            continue
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": f"**{title}**"}})
        for it in bucket[:3]:
            elements.extend(_emit(it))
        if len(bucket) > 3:
            elements.append({"tag": "note",
                             "elements": [{"tag": "lark_md",
                                           "content": f"_... 还有 {len(bucket)-3} 条 ..._"}]})
        elements.append({"tag": "hr"})

    # Pattern Memory 收口
    from memory.pattern_memory import generate_all_patterns
    patterns = generate_all_patterns(items, args.project_id)
    if patterns:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": "**🔄 协作模式与交接风险（系统自动归纳）**"}})
        for p in patterns[:3]:
            elements.append({"tag": "div",
                             "text": {"tag": "lark_md",
                                      "content": f"- `[{p.pattern_type}]` {p.summary[:90]}"}})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": "📋 由 Memory Engine 实时生成 · 每条带原始消息证据 · "
                                 "无需交接会议 · 看完即接手"}],
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "📋 项目交接摘要 · 给王五"},
            "subtitle": {"tag": "plain_text",
                         "content": "8 维度 + Pattern · 接手 0 秒上岗"},
            "template": "carmine",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "handoff")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节")


def _seed_review_items(items: list[MemoryItem]) -> list[MemoryItem]:
    """Inject demo review-queue states so the desk card has content to show."""
    decisions = [i for i in items if i.state_type == "decision"]
    if decisions:
        # 第一条决策 → tentative + needs_review (模拟 AI 候选)
        d0 = decisions[0]
        d0.decision_strength = "tentative"
        d0.review_status = "needs_review"
        md = dict(d0.metadata or {})
        md["actor_type"] = "ai_agent"
        md["agent_id"] = "risk-analyzer"
        d0.metadata = md
    if len(decisions) >= 2:
        # 第二条 → 标记冲突 (与历史决策冲突)
        d1 = decisions[1]
        d1.review_status = "needs_review"
        md = dict(d1.metadata or {})
        md["conflict_status"] = "conflicting"
        md["conflict_with"] = "5/3 旧决策 (用 React 18)"
        d1.metadata = md
    # 给某条 next_step 也加个 needs_review (低置信度)
    next_steps = [i for i in items if i.state_type == "next_step"]
    if next_steps:
        n0 = next_steps[0]
        if not n0.review_status:
            n0.review_status = "needs_review"
            n0.confidence = 0.55
    return items


def scene_review(items, store, args) -> None:
    """SCENE 8 — 决策审核台：AI 候选 + 人裁定治理护城河。"""
    slate("8", "决策审核台 (Review Desk)",
          "18:30 · 私聊给 CTO", "管理者夜间复核 · AI 候选不自动生效")

    items = _seed_review_items(items)
    pending = [i for i in items if getattr(i, "review_status", "") == "needs_review"]
    beat(f"待审核 {len(pending)} 项")

    if not pending:
        beat("无待审核项")
        return

    elements: list[dict] = []
    elements.append({"tag": "div",
                     "text": {"tag": "lark_md",
                              "content": "**⚖️ 今日产出 14 条新记忆，3 条需要你确认**\n"
                                         "_AI 做候选识别，人做最终裁定_"}})
    elements.append({"tag": "hr"})

    for idx, it in enumerate(pending[:5], start=1):
        ds = getattr(it, "decision_strength", "") or "—"
        meta = getattr(it, "metadata", None) or {}
        actor = meta.get("actor_type", "human")
        conflict = meta.get("conflict_status") == "conflicting"
        conflict_with = meta.get("conflict_with", "")
        actor_badge = "🤖 AI" if actor == "ai_agent" else "👤 人"

        head = f"**{idx}. [{it.state_type}] {actor_badge}** · `decision_strength={ds}`"
        if conflict:
            head += "  🚨 **冲突**"
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": head}})
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": f"> {it.current_value[:120]}"}})
        if conflict and conflict_with:
            elements.append({"tag": "note",
                             "elements": [{"tag": "lark_md",
                                           "content": f"⚠️ 与 {conflict_with} 矛盾，需人工裁定"}]})
        ev = evidence_note(it)
        if ev:
            elements.append(ev)
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md",
                                  "content": f"_confidence={it.confidence:.2f} · "
                                             f"memory_id={it.memory_id[:14]}…_"}})
        # 模拟操作按钮（视觉，不挂回调）
        elements.append({"tag": "action",
                         "actions": [
                             {"tag": "button",
                              "text": {"tag": "plain_text", "content": "✅ approve"},
                              "type": "primary"},
                             {"tag": "button",
                              "text": {"tag": "plain_text", "content": "❌ reject"},
                              "type": "danger"},
                             {"tag": "button",
                              "text": {"tag": "plain_text", "content": "✏️ modify"},
                              "type": "default"},
                             {"tag": "button",
                              "text": {"tag": "plain_text", "content": "🔀 merge"},
                              "type": "default"},
                         ]})
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
                      "content": "⚖️ 管家身份已验证（群成员） · "
                                 "approve 后写入正式记忆 · "
                                 "reject 后归档不删除（可追溯）"}],
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "⚖️ 决策审核台"},
            "subtitle": {"tag": "plain_text",
                         "content": "AI 候选 · 人裁定 · 治理护城河"},
            "template": "purple",
        },
        "elements": elements,
    }

    if args.feishu:
        msg_id = send_card(card, args.chat_id, "review")
        beat(f"飞书卡片已发：{msg_id}")
    else:
        beat(f"卡片 JSON: {len(json.dumps(card, ensure_ascii=False))} 字节")


SCENE_REGISTRY = {
    "morning": scene_morning,
    "orchestrator": scene_orchestrator,
    "hotspot": scene_hotspot,
    "standup": scene_standup,
    "handoff": scene_handoff,
    "review": scene_review,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=list(SCENE_REGISTRY.keys()))
    parser.add_argument("--all", action="store_true", help="Run all scenes in order")
    parser.add_argument("--feishu", action="store_true", help="Also send to Feishu")
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    args = parser.parse_args()

    print("\n" + "═" * 78)
    print("  OpenClaw Memory Engine  ·  A Day in a Hybrid Human/AI Team")
    print("═" * 78)

    print("\n[setup] Bootstrapping memory ...")
    t0 = time.time()
    store, items = bootstrap_memory(args.project_id)
    print(f"[setup] {len(items)} memories  ·  {time.time()-t0:.1f}s")

    if args.all:
        scenes_to_run = list(SCENE_REGISTRY.values())
    elif args.scene:
        scenes_to_run = [SCENE_REGISTRY[args.scene]]
    else:
        scenes_to_run = list(SCENE_REGISTRY.values())

    for fn in scenes_to_run:
        fn(items, store, args)
        time.sleep(0.5)

    print("\n" + "═" * 78)
    print("  END.")
    print("═" * 78)


if __name__ == "__main__":
    main()
