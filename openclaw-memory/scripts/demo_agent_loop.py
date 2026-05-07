"""AI Agent Loop Demo — let an AI read Memory, reason, and act in Feishu.

Pipeline:
  1. (optional) Send a trigger message to Feishu (e.g. "@bot 周五能上线吗?")
  2. Load the current project memory (from cached extraction or a MemoryStore dir)
  3. Build an LLM prompt that includes:
       - the trigger
       - the structured memory state (goals, owners, blockers, deadline, ...)
  4. Call DeepSeek to produce a structured analysis JSON
  5. Render the response as a Feishu interactive card and post it back
  6. Persist the AI action as a MemoryItem with metadata.actor_type=ai_agent

Demo flow (default scenario):
    user> @bot 周五能上线吗？我有点担心
    [5s later]
    ai>   📊 风险分析（HIGH）
          ├─ 当前进度：登录模块进行中
          ├─ 关键阻塞：设计稿未出（卡王五）
          ├─ deadline：周五前联调完成
          └─ 建议：
             [P0] @王五 推动设计稿（解阻塞）
             [P1] @李四 ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.schema import MemoryItem, SourceRef, utc_now_iso
from memory.store import MemoryStore

LARK_CLI = "/Users/flewolf/.local/bin/lark-cli"
DEFAULT_CHAT_ID = "oc_e1c6a2c2a42b67606b91ad69bab226f4"
DEFAULT_PROJECT = "natural-daily"
DEFAULT_AGENT_ID = "risk-analyzer"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-e71397d04b974b02a84b3f02b4b0302e")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DEFAULT_TRIGGER = "@bot 周五能上线吗？我有点担心"
DEFAULT_TRIGGER_SENDER = "产品-小李"


# ---------------------------------------------------------------------------
# lark-cli wrapper
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
# Memory loading: re-extract Hybrid memories from cached events
# ---------------------------------------------------------------------------
def load_memory_items(events_cache: Path, project_id: str) -> tuple[list[MemoryItem], MemoryStore, Path]:
    """Build memory items from cached events (Hybrid mode) into a persistent store."""
    events = json.loads(events_cache.read_text())
    for ev in events:
        ev["project_id"] = project_id

    data_dir = ROOT / "data" / "agent_demo"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Fresh store for the demo
    for f in data_dir.glob("*.jsonl"):
        f.unlink()
    for f in data_dir.glob("*.json"):
        f.unlink()

    store = MemoryStore(data_dir)
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
    engine = MemoryEngine(store, hybrid)
    engine.ingest_events(events, debounce=False)
    items = list(store.list_items(project_id))
    return items, store, data_dir


# ---------------------------------------------------------------------------
# Build the agent prompt (memory context → LLM)
# ---------------------------------------------------------------------------
def _format_items_for_prompt(items: list[MemoryItem]) -> str:
    """Group items by state_type and emit a compact, citable bullet list."""
    by_type: dict[str, list[MemoryItem]] = {}
    for it in items:
        by_type.setdefault(it.state_type, []).append(it)

    type_order = ["project_goal", "owner", "decision", "blocker", "deadline",
                  "deferred", "member_status", "next_step"]
    lines = []
    for t in type_order:
        bucket = by_type.get(t, [])
        if not bucket:
            continue
        lines.append(f"\n## {t} ({len(bucket)} items)")
        for it in bucket:
            sender = it.source_refs[0].sender_name if it.source_refs else "?"
            mid = it.source_refs[0].message_id if it.source_refs else ""
            owner_part = f" [owner={it.owner}]" if it.owner else ""
            lines.append(
                f"- {it.current_value}{owner_part}  "
                f"(msg_id={mid}, by={sender}, conf={it.confidence:.2f})"
            )
    return "\n".join(lines)


AGENT_PROMPT_TEMPLATE = """你是一个企业团队的 AI 协作助手 (agent_id={agent_id})。
你的任务：根据当前项目的结构化记忆 (memory) + Agent Context Pack + 系统识别的协作模式 (patterns) + 触发问题，给出**基于证据**的风险评估和行动建议。

# 当前项目结构化记忆 (按类型分组人类可读)
项目: {project_id}
共 {item_count} 条记忆：
{memory_dump}

# Agent Context Pack (机器可读，含 supersedes 与 raw_snippets)
{agent_context_pack}

# 系统识别的协作模式 (Pattern Memory)
{pattern_dump}

# 触发问题
来自 {trigger_sender}：{trigger_text}

# 你的任务
1. 综合上述 memory 和 patterns 判断当前局势。**只能基于上面的事实，不能虚构**。
2. 在 risk_reason 里**明确引用**用到的 pattern_type（如 blocker_hotspot / handoff_risk / deadline_risk_score 等），让推理可追溯。
3. 评估问题对应的风险等级。
4. 给出 1-3 条具体行动建议，每条必须 @ 对应负责人，标 priority。
5. 把分析结论以 JSON 格式返回，schema 如下：

```json
{{
  "agent_id": "{agent_id}",
  "summary_one_line": "一句话总览(<=40字)",
  "risk_level": "high|medium|low",
  "risk_reason": "为什么这个等级，2-3行",
  "key_findings": [
    {{"label": "进度|阻塞|deadline|成员状态", "detail": "...", "evidence_msg_id": "..."}}
  ],
  "actions": [
    {{"target_owner": "人名", "action": "具体行动", "priority": "p0|p1|p2", "rationale": "为什么"}}
  ],
  "memory_writeback": {{
    "state_type": "decision|next_step|blocker",
    "current_value": "本次 AI 分析的可记录结论(<=60字)",
    "rationale": "AI 综合分析当前 memory 后认为..."
  }},
  "patterns_used": ["blocker_hotspot", "deadline_risk_score"]
}}
```

【重要】
- key_findings 的 evidence_msg_id 必须取自上面 memory 的 msg_id 字段
- actions.target_owner 必须是上面 memory 中真实出现过的人名
- patterns_used 必须**严格**只包含上面"协作模式 (Pattern Memory)"一节中实际出现过的 pattern_type，**禁止虚构**未出现的 pattern 名（如未列出 blocker_hotspot 就不能写）；没真用就空数组
- 不要包裹 markdown，直接返回 JSON
"""


def _format_patterns_for_prompt(items: list[MemoryItem], project_id: str) -> tuple[str, list[dict]]:
    """V1.18 integration: derive Work Patterns and serialize for the prompt.

    Returns (formatted_text, raw_patterns) so the card builder can also see them.
    """
    try:
        from memory.pattern_memory import generate_all_patterns
        patterns = generate_all_patterns(items, project_id)
    except Exception as e:
        return f"(pattern generation skipped: {e})", []

    if not patterns:
        return "(暂无识别到的协作模式)", []

    lines = []
    raw = []
    for p in patterns:
        # PatternMemoryItem dataclass; access attrs
        ptype = getattr(p, "pattern_type", "?")
        scope = getattr(p, "scope", "")
        summary = getattr(p, "summary", "")
        conf = getattr(p, "confidence", 0.0)
        win = getattr(p, "time_window", "")
        lines.append(f"- **[{ptype}]** scope={scope}, window={win}, conf={conf:.2f}")
        # Render summary indented
        for sl in str(summary).split("\n"):
            lines.append(f"    {sl}")
        raw.append({
            "pattern_type": ptype, "scope": scope, "summary": summary,
            "confidence": conf, "time_window": win,
        })
    return "\n".join(lines), raw


def build_agent_prompt(
    items: list[MemoryItem],
    project_id: str,
    trigger_text: str,
    trigger_sender: str,
    agent_id: str,
) -> str:
    pattern_dump, _ = _format_patterns_for_prompt(items, project_id)
    # V1.18: 注入结构化 Agent Context Pack（机器可读）
    try:
        from memory.project_state import build_agent_context_pack
        ctx_pack = build_agent_context_pack(project_id, items, max_items_per_section=5)
        # 裁剪 raw_snippets 防止超长
        for d in ctx_pack.get("decisions", []):
            d["raw_snippets"] = d.get("raw_snippets", [])[:2]
        ctx_pack_str = json.dumps(ctx_pack, ensure_ascii=False, indent=2)
        if len(ctx_pack_str) > 3000:
            ctx_pack_str = ctx_pack_str[:3000] + "\n  ... (truncated for prompt budget)"
    except Exception as e:
        ctx_pack_str = f'{{"_error": "{e}"}}'
    return AGENT_PROMPT_TEMPLATE.format(
        agent_id=agent_id,
        project_id=project_id,
        item_count=len(items),
        memory_dump=_format_items_for_prompt(items),
        agent_context_pack=ctx_pack_str,
        pattern_dump=pattern_dump,
        trigger_sender=trigger_sender,
        trigger_text=trigger_text,
    )


# ---------------------------------------------------------------------------
# Call agent
# ---------------------------------------------------------------------------
def call_agent(prompt: str) -> dict:
    provider = OpenAIProvider(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=0.2,
        max_tokens=2000,
    )
    raw = provider.generate(prompt)
    if not raw:
        raise RuntimeError("Agent returned empty response")
    # tolerate leading/trailing markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[-1].strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")].strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------
RISK_TEMPLATE = {"high": "red", "medium": "orange", "low": "green"}
RISK_EMOJI = {"high": "🚨", "medium": "⚠️", "low": "✅"}
PRIO_EMOJI = {"p0": "🔥", "p1": "⚡", "p2": "📌"}


def _msg_link(chat_id: str, msg_id: str) -> str:
    if not chat_id or not msg_id:
        return ""
    return f"https://app.feishu.cn/client/messages/{chat_id}/{msg_id}"


def build_response_card(
    chat_id: str,
    trigger_text: str,
    trigger_sender: str,
    response: dict,
    items: list[MemoryItem],
    agent_id: str,
) -> dict:
    risk = response.get("risk_level", "medium").lower()
    template = RISK_TEMPLATE.get(risk, "blue")
    emoji = RISK_EMOJI.get(risk, "🤖")
    item_by_msg: dict[str, MemoryItem] = {}
    for it in items:
        if it.source_refs and it.source_refs[0].message_id:
            item_by_msg[it.source_refs[0].message_id] = it

    def _lookup_evidence(ev_id: str) -> MemoryItem | None:
        """Match by full id, then by prefix (LLM may truncate long ids)."""
        if not ev_id:
            return None
        if ev_id in item_by_msg:
            return item_by_msg[ev_id]
        for full_id, it in item_by_msg.items():
            if full_id.startswith(ev_id) or ev_id.startswith(full_id):
                return it
        return None

    elements: list[dict] = []

    # Trigger context
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**触发**：{trigger_sender} 提问\n💬 _{trigger_text}_"}
    })
    elements.append({"tag": "hr"})

    # One-line summary
    summary = response.get("summary_one_line", "")
    if summary:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**🔎 总览**：{summary}"}
        })

    # Risk box
    risk_reason = response.get("risk_reason", "")
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**{emoji} 风险等级**：`{risk.upper()}`\n{risk_reason}"}
    })
    elements.append({"tag": "hr"})

    # Key findings
    findings = response.get("key_findings", [])
    if findings:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**📊 关键判断点**"}})
        for f in findings[:5]:
            label = f.get("label", "?")
            detail = f.get("detail", "")
            ev_id = f.get("evidence_msg_id", "")
            ev_item = _lookup_evidence(ev_id)
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"- **[{label}]** {detail}"}
            })
            if ev_item and ev_item.source_refs:
                ref = ev_item.source_refs[0]
                excerpt = (ref.excerpt or "").strip()[:60]
                link = _msg_link(chat_id, ref.message_id)
                if link:
                    elements.append({
                        "tag": "note",
                        "elements": [{"tag": "lark_md",
                            "content": f"📎 证据：[{ref.sender_name}：{excerpt}]({link})"}]
                    })
                else:
                    elements.append({
                        "tag": "note",
                        "elements": [{"tag": "lark_md",
                            "content": f"📎 证据：{ref.sender_name}：{excerpt}"}]
                    })
        elements.append({"tag": "hr"})

    # Actions
    actions = response.get("actions", [])
    if actions:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**🎯 建议行动**"}})
        for a in actions[:5]:
            owner = a.get("target_owner", "?")
            prio = (a.get("priority") or "p2").lower()
            pe = PRIO_EMOJI.get(prio, "📌")
            text = a.get("action", "")
            why = a.get("rationale", "")
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md",
                         "content": f"{pe} **[{prio.upper()}] @{owner}** — {text}"}
            })
            if why:
                elements.append({
                    "tag": "note",
                    "elements": [{"tag": "lark_md", "content": f"💡 {why}"}]
                })
        elements.append({"tag": "hr"})

    # Patterns used — guard against LLM hallucination by cross-checking with
    # what was actually generated from MemoryStore.
    raw_used = response.get("patterns_used") or []
    try:
        from memory.pattern_memory import generate_all_patterns
        actual_pattern_types = {
            p.pattern_type for p in
            generate_all_patterns(items, items[0].project_id if items else "")
        }
    except Exception:
        actual_pattern_types = set()
    patterns_used = [p for p in raw_used if p in actual_pattern_types]
    if patterns_used:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": "**⚙️ 引用的协作模式 (Pattern Memory)**\n"
                                + "  ".join(f"`{p}`" for p in patterns_used)}
        })

    # Footer
    elements.append({
        "tag": "note",
        "elements": [{"tag": "lark_md",
            "content": (
                f"🤖 由 AI Agent `{agent_id}` 基于 {len(items)} 条 Memory + Work Pattern Memory 综合生成"
                f" · 已记录至 Memory（actor_type=ai_agent）"
            )}],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} AI Agent · 风险分析"},
            "subtitle": {"tag": "plain_text",
                         "content": "Memory-grounded analysis · 不来自虚构"},
            "template": template,
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Memory writeback
# ---------------------------------------------------------------------------
def writeback_ai_action(
    store: MemoryStore,
    project_id: str,
    response: dict,
    trigger_message_id: str,
    chat_id: str,
    agent_id: str,
) -> MemoryItem:
    """Persist the AI's writeback as a MemoryItem with actor_type=ai_agent."""
    wb = response.get("memory_writeback") or {}
    state_type = wb.get("state_type", "next_step")
    value = wb.get("current_value") or response.get("summary_one_line", "AI 分析结论")
    rationale = wb.get("rationale", "AI agent 综合 memory 综合给出")
    risk = response.get("risk_level", "medium")

    src = SourceRef(
        type="ai_action",
        chat_id=chat_id,
        message_id=trigger_message_id,
        excerpt=value[:200],
        created_at=utc_now_iso(),
        sender_name=f"AI:{agent_id}",
        sender_id=f"agent:{agent_id}",
        source_url=_msg_link(chat_id, trigger_message_id),
    )
    item = MemoryItem(
        project_id=project_id,
        state_type=state_type,
        key=f"ai_action_{int(time.time())}",
        current_value=value,
        rationale=rationale,
        owner=None,
        status="active",
        confidence=0.75,
        source_refs=[src],
        decision_strength="tentative",
        review_status="needs_review",
        metadata={
            "actor_type": "ai_agent",
            "agent_id": agent_id,
            "risk_level": risk,
            "actions_proposed": len(response.get("actions") or []),
            "triggered_by_message_id": trigger_message_id,
        },
    )
    store.upsert_items([item], processed_ids=[])
    return item


# ---------------------------------------------------------------------------
# Send card
# ---------------------------------------------------------------------------
def send_card(chat_id: str, card: dict, label: str) -> str:
    card_json = json.dumps(card, ensure_ascii=False)
    print(f"  Sending card [{label}] ({len(card_json)} bytes) ...")
    result = lark_run([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id,
        "--msg-type", "interactive",
        "--content", card_json,
    ])
    if not result.get("ok"):
        raise RuntimeError(f"send card failed: {result}")
    msg_id = result["data"]["message_id"]
    print(f"  → message_id={msg_id}")
    return msg_id


def send_trigger_text(chat_id: str, sender_label: str, trigger_text: str) -> tuple[str, str]:
    """Post the trigger message as bot, return (message_id, created_at)."""
    full = f"[{sender_label}] {trigger_text}"
    print(f"  Sending trigger text: {full}")
    result = lark_run([
        "im", "+messages-send", "--as", "bot",
        "--chat-id", chat_id, "--text", full,
    ])
    if not result.get("ok"):
        raise RuntimeError(f"trigger send failed: {result}")
    return result["data"]["message_id"], result["data"]["create_time"]


# ---------------------------------------------------------------------------
# Reusable library entrypoint (for listeners / external callers)
# ---------------------------------------------------------------------------
def run_agent_loop(
    chat_id: str,
    project_id: str,
    trigger_text: str,
    trigger_sender: str,
    source_message_id: str,
    *,
    store: MemoryStore,
    agent_id: str = DEFAULT_AGENT_ID,
    send_card_to_feishu: bool = True,
    write_back: bool = True,
    log: Any = None,
) -> dict:
    """One-shot agent loop: read memory → call LLM → reply card → writeback.

    Designed to be called by both the demo CLI and the WebSocket listener.

    Args:
        chat_id: Feishu chat where the response card is sent.
        project_id: which project's memory to read.
        trigger_text: the user's question text.
        trigger_sender: display name of the user who asked.
        source_message_id: the message_id of the trigger (for writeback audit).
        store: a live MemoryStore — caller is responsible for populating it
               (typically via Hybrid extraction or persistent sync).
        agent_id: short identifier for this agent (appears in metadata).
        send_card_to_feishu: if False, skip the card send (useful for tests).
        write_back: if False, skip persisting AI action to memory.
        log: optional logger / callable for status output.

    Returns:
        The structured response dict from the LLM (also written back).
    """
    def _log(msg: str) -> None:
        if log is None:
            print(msg)
        elif callable(log):
            log(msg)
        else:
            log.info(msg)

    items = list(store.list_items(project_id))
    if not items:
        raise RuntimeError(f"No memory items for project {project_id}; sync first.")
    _log(f"  agent_loop: {len(items)} memory items loaded")

    prompt = build_agent_prompt(items, project_id, trigger_text, trigger_sender, agent_id)
    _log(f"  agent_loop: prompt={len(prompt)} chars, calling DeepSeek ...")
    t0 = time.time()
    response = call_agent(prompt)
    _log(f"  agent_loop: ok in {time.time()-t0:.1f}s · "
         f"risk={response.get('risk_level')} · "
         f"actions={len(response.get('actions') or [])}")

    if send_card_to_feishu:
        card = build_response_card(
            chat_id, trigger_text, trigger_sender,
            response, items, agent_id,
        )
        send_card(chat_id, card, "agent-response")

    if write_back:
        new_item = writeback_ai_action(
            store, project_id, response, source_message_id, chat_id, agent_id,
        )
        _log(f"  agent_loop: wrote memory_id={new_item.memory_id} "
             f"(actor_type={new_item.metadata.get('actor_type')})")

    return response


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="AI agent reads Memory, analyzes, replies in Feishu.")
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    parser.add_argument("--events-cache", default="/tmp/full_loop_events.json",
                        help="JSON file with cached events that seed the project memory")
    parser.add_argument("--trigger", default=DEFAULT_TRIGGER,
                        help="The user message that triggers the agent")
    parser.add_argument("--trigger-sender", default=DEFAULT_TRIGGER_SENDER)
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--no-trigger-send", action="store_true",
                        help="Skip sending the trigger message (use the cached one)")
    parser.add_argument("--no-card", action="store_true",
                        help="Skip sending the response card")
    parser.add_argument("--no-writeback", action="store_true",
                        help="Skip persisting the AI action back to Memory")
    args = parser.parse_args()

    print("=" * 72)
    print(f"AI Agent Loop · agent_id={args.agent_id}")
    print(f"Project: {args.project_id}    Chat: {args.chat_id}")
    print("=" * 72)

    # 1. Load Memory
    print("\n[1/6] Loading Memory from cached events ...")
    cache = Path(args.events_cache)
    if not cache.exists():
        raise SystemExit(f"events cache not found: {cache}")
    items, store, data_dir = load_memory_items(cache, args.project_id)
    print(f"  → {len(items)} memories ({sorted({i.state_type for i in items})})")
    print(f"  → store: {data_dir}")

    # 2. Send trigger (or use given one)
    trigger_msg_id = ""
    if args.no_trigger_send:
        trigger_msg_id = "no_trigger_sent"
        print(f"\n[2/6] Trigger (skipped): {args.trigger}")
    else:
        print(f"\n[2/6] Sending trigger to Feishu ...")
        trigger_msg_id, _ = send_trigger_text(args.chat_id, args.trigger_sender, args.trigger)
        time.sleep(2)  # demo pacing

    # 3. Build prompt
    print("\n[3/6] Building agent prompt ...")
    prompt = build_agent_prompt(
        items, args.project_id, args.trigger, args.trigger_sender, args.agent_id
    )
    print(f"  → prompt={len(prompt)} chars")

    # 4. Call agent
    print("\n[4/6] Calling DeepSeek agent ...")
    t0 = time.time()
    response = call_agent(prompt)
    print(f"  → ok in {time.time()-t0:.1f}s · "
          f"risk={response.get('risk_level')} · "
          f"actions={len(response.get('actions') or [])}")

    # Pretty-print AI's intent (so the run is auditable in stdout)
    print("\n  AI summary :", response.get("summary_one_line", ""))
    for a in response.get("actions", [])[:5]:
        print(f"     {a.get('priority','?').upper():<3} @{a.get('target_owner','?'):<6} "
              f"→ {a.get('action','')[:60]}")

    # 5. Send card
    if args.no_card:
        print("\n[5/6] Card sending skipped.")
        Path("/tmp/agent_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2))
        print(f"  Response saved to /tmp/agent_response.json")
    else:
        print("\n[5/6] Sending response card ...")
        card = build_response_card(
            args.chat_id, args.trigger, args.trigger_sender,
            response, items, args.agent_id,
        )
        send_card(args.chat_id, card, "agent-response")

    # 6. Writeback to Memory
    if args.no_writeback:
        print("\n[6/6] Memory writeback skipped.")
    else:
        print("\n[6/6] Writing AI action back to Memory ...")
        new_item = writeback_ai_action(
            store, args.project_id, response, trigger_msg_id,
            args.chat_id, args.agent_id,
        )
        print(f"  → new memory_id={new_item.memory_id}")
        print(f"     state_type={new_item.state_type}  "
              f"actor_type={new_item.metadata.get('actor_type')}  "
              f"risk={new_item.metadata.get('risk_level')}")

    print("\nDone. ✨")


if __name__ == "__main__":
    main()
