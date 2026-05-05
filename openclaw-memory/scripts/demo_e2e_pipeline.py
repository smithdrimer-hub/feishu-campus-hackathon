"""End-to-end pipeline: sync Feishu messages → extract memory → send state panel → pin.

V1.11 新增：打通真实飞书端到端流程。

用法:
  # 只同步+提取，不发送消息（安全模式）
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --project-id demo --dry-run

  # 同步+提取+发送+置顶（完整流程）
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --project-id demo

  # 加上文档/任务数据源
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --doc-id doc_xxx
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --task-query "V1"

  # 发送个人上下文（形态 B）
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --personal 张三

  # 使用 Hybrid 提取器
  python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --hybrid
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw Memory Engine — 飞书端到端流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--chat-id", required=True, help="飞书群聊 chat_id (oc_xxx)")
    parser.add_argument("--project-id", default="e2e-default", help="项目 ID")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "e2e"), help="数据目录")
    parser.add_argument("--limit", type=int, default=50, help="最大同步消息数")
    parser.add_argument("--dry-run", action="store_true", help="只同步+提取，不发消息不置顶")
    parser.add_argument("--no-pin", action="store_true", help="发送消息但不置顶")
    parser.add_argument("--hybrid", action="store_true", help="使用 HybridExtractor（需配置 LLM）")
    parser.add_argument("--personal", default=None, help="发送个人上下文（形态 B），传入用户名")
    parser.add_argument("--doc-id", default=None, help="飞书文档 ID (doc_xxx)，同步文档数据源")
    parser.add_argument("--task-query", default=None, help="任务搜索关键词，同步任务数据源")
    parser.add_argument("--sync-calendar", action="store_true", help="同步本周日历日程")
    parser.add_argument("--sync-minutes", action="store_true", help="同步最近会议纪要")
    parser.add_argument("--sync-approvals", action="store_true", help="同步进行中的审批")
    parser.add_argument("--identity", default="bot", help="消息发送身份: bot (默认) / user")
    parser.add_argument("--execute-actions", action="store_true",
                        help="提取后执行行动计划（创建任务/文档，发送提醒）")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="自动确认操作，跳过 requires_confirmation 检查")
    parser.add_argument("--standup", action="store_true",
                        help="输出站会摘要格式（昨日/今日/阻塞）替代状态面板")
    parser.add_argument("--confirm-checklist", action="store_true",
                        help="会议纪要后发送确认清单到群")
    parser.add_argument("--trigger", action="store_true",
                        help="启用触发引擎（基于 diff 自动检测并生成动作提案）")
    parser.add_argument("--mode", default="preview",
                        choices=["preview", "confirm", "auto"],
                        help="触发引擎模式: preview(仅显示) / confirm(逐条确认) / auto(自动执行)")
    return parser.parse_args()


def _get_extractor(use_hybrid: bool = False):
    """Create the appropriate extractor instance."""
    from memory.extractor import RuleBasedExtractor, HybridExtractor, LLMExtractor

    if not use_hybrid:
        return RuleBasedExtractor()

    from memory.llm_provider import OpenAIProvider
    provider = _get_llm_provider()
    if provider is None:
        print("[WARN] LLM 未配置，Hybrid 降级为纯规则模式")
        return HybridExtractor(rule_extractor=RuleBasedExtractor(), llm_extractor=None)

    return HybridExtractor(
        rule_extractor=RuleBasedExtractor(),
        llm_extractor=LLMExtractor(provider, fallback=RuleBasedExtractor()),
    )


def _get_llm_provider():
    """Try to create LLM provider from config."""
    import os
    config_path = ROOT / "config.local.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            llm_cfg = cfg.get("llm", {})
            if llm_cfg.get("provider") == "openai" and llm_cfg.get("api_key"):
                from memory.llm_provider import OpenAIProvider
                return OpenAIProvider(
                    api_key=llm_cfg["api_key"],
                    base_url=llm_cfg.get("base_url"),
                    model=llm_cfg.get("model", "gpt-4o-mini"),
                )
        except Exception:
            pass
    return None


def _markdown_safe(text: str, max_len: int = 4000) -> str:
    """Truncate text to a safe length for Feishu markdown messages."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n... (内容过长已截断)"


def main() -> None:
    args = parse_args()

    from adapters.lark_cli_adapter import LarkCliAdapter
    from memory.engine import MemoryEngine
    from memory.store import MemoryStore

    # ── 初始化 ────────────────────────────────────────────────
    adapter = LarkCliAdapter()
    store = MemoryStore(Path(args.data_dir))
    extractor = _get_extractor(args.hybrid)
    engine = MemoryEngine(store, extractor=extractor, adapter=adapter)

    # V1.12 AUTH-1/2: 身份感知 + 群聊自动绑定
    identity = engine.get_identity()
    if not identity.get("open_id"):
        dr_result = adapter.doctor()
        if dr_result.returncode == 0:
            checks = (dr_result.data or {}).get("checks", [])
            for c in checks:
                if c.get("name") == "token_exists":
                    msg = c.get("message", "")
                    if "(" in msg:
                        name_part = msg.split("(")[-1].rstrip(")")
                        identity["name"] = name_part.split(" ")[0] if " " in name_part else name_part
            identity["open_id"] = "current_user"
            engine.set_identity(identity["open_id"], identity.get("name", ""))
    # 自动绑定 chat_id → project_id
    existing = engine.get_project_for_chat(args.chat_id)
    if existing and args.project_id == "e2e-default":
        args.project_id = existing
    else:
        engine.bind_chat_to_project(args.chat_id, args.project_id)

    resolved_name = identity.get("name", "未登录")
    print(f"{'='*60}")
    print(f"OpenClaw Memory Engine — 飞书端到端流水线")
    print(f"{'='*60}")
    print(f"用户: {resolved_name}")
    print(f"群聊: {args.chat_id}")
    print(f"项目: {args.project_id}")
    print(f"提取器: {'Hybrid' if args.hybrid else 'RuleBased'}")
    print(f"{'='*60}\n")

    # ── Step 1: 同步群消息（V1.11: 分页 + 增量去重）────────────────
    print("[1/5] 同步群消息...")
    # 获取当前已处理的消息 ID，避免重复提取
    processed_ids = set(store.processed_event_ids())
    all_messages = []
    page_token = None
    pages = 0
    max_pages = max(args.limit // 50, 1)

    while pages < max_pages:
        result = adapter.list_chat_messages(
            args.chat_id, page_size=50, page_token=page_token,
        )
        if result.returncode != 0:
            break
        page_msgs = _extract_message_list(result.data)
        if not page_msgs:
            break
        all_messages.extend(page_msgs)
        pages += 1
        # 检查是否有更多页
        has_more = (result.data or {}).get("data", {}).get("has_more", False)
        page_token = (result.data or {}).get("data", {}).get("page_token", "")
        if not has_more or not page_token:
            break

    # 过滤：协作消息 + 跳过已处理
    events = []
    new_count = 0
    for msg in all_messages:
        if not _is_collaboration_message(msg):
            continue
        ev = _normalize_event(msg, args.chat_id, args.project_id)
        if ev["message_id"] in processed_ids:
            continue
        events.append(ev)
        new_count += 1

    written = store.append_raw_events(events)
    print(f"    共 {len(all_messages)} 条消息 ({pages} 页), "
          f"新写入 {written} 条\n")

    # ── Step 2: 提取记忆 ───────────────────────────────────────
    print("[2/5] 提取结构化记忆...")
    items = engine.process_new_events(args.project_id, debounce=False)
    print(f"    提取到 {len(items)} 条活跃记忆\n")

    # ── Step 3: 可选 — 文档/任务数据源 ──────────────────────────
    if args.doc_id:
        print(f"[3/5] 同步文档: {args.doc_id}")
        try:
            doc_items = engine.sync_doc(args.doc_id, project_id=args.project_id)
            print(f"    文档提取到 {len(doc_items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    文档同步失败: {e}\n")
    if args.task_query:
        print(f"[3/5] 搜索任务: '{args.task_query}'")
        try:
            task_items = engine.sync_tasks(args.task_query, project_id=args.project_id)
            print(f"    任务提取到 {len(task_items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    任务同步失败: {e}\n")

    if args.sync_calendar:
        from datetime import date, timedelta
        today = date.today().isoformat()
        week_end = (date.today() + timedelta(days=7)).isoformat()
        print(f"[3/5] 同步日历: {today} ~ {week_end}")
        try:
            cal_items = engine.sync_calendar(today, week_end, project_id=args.project_id)
            print(f"    日历提取到 {len(cal_items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    日历同步失败: {e}\n")
    if args.sync_minutes:
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=30)).isoformat()
        end = date.today().isoformat()
        print(f"[3/5] 搜索会议纪要: {start} ~ {end}")
        try:
            min_items = engine.sync_minutes(start, end, project_id=args.project_id)
            print(f"    纪要提取到 {len(min_items)} 条记忆\n")
            if args.confirm_checklist and min_items:
                from memory.project_state import render_confirmation_checklist
                checklist = render_confirmation_checklist(min_items)
                print(f"    确认清单:\n{checklist}")
                if not args.dry_run:
                    adapter.send_message(args.chat_id, _markdown_safe(checklist),
                                         msg_type="markdown")
                    print("    确认清单已发送到群")
        except RuntimeError as e:
            print(f"    纪要同步失败: {e}\n")
    if args.sync_approvals:
        print(f"[3/5] 同步进行中的审批")
        try:
            app_items = engine.sync_approvals("pending", project_id=args.project_id)
            print(f"    审批提取到 {len(app_items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    审批同步失败: {e}\n")

    if not any([args.doc_id, args.task_query, args.sync_calendar,
                args.sync_minutes, args.sync_approvals]):
        print("[3/5] (无额外数据源)\n")

    # 重新获取最新 items（可能包含文档/任务新增的）
    items = store.list_items(args.project_id)

    # ── Step 3.5: 可选 — 触发引擎 / 手动执行 ─────────────────────
    action_results: list[tuple] = []
    if args.trigger:
        print("[3.5/5] 触发引擎扫描...")
        from memory.action_trigger import ActionTrigger
        from memory.action_executor import ActionExecutor

        diff = getattr(engine, "last_diff", None) or {
            "created": [], "updated": [], "unchanged": [],
        }
        trigger = ActionTrigger(
            engine=engine,
            log_path=str(Path(args.data_dir) / "action_log.jsonl"),
        )
        proposals = trigger.scan(diff, args.project_id, args.chat_id, mode=args.mode)
        print(f"    生成 {len(proposals)} 个动作提案：")
        for p in proposals:
            tag = {"low": "[OK]", "medium": "[!!]", "high": "[!!HIGH]"} \
                .get(p.risk_level, "->")
            safe_title = p.title.encode("ascii", errors="replace").decode("ascii")
            print(f"      {tag} [{p.risk_level}] {p.action_type}: {safe_title[:70]}")

        if args.mode == "preview":
            print("    (preview 模式，不执行)\n")
            action_results = [
                (p.action_type, False, "preview mode — not executed", {})
                for p in proposals
            ]
        else:
            auto_confirm = args.mode == "auto"
            executor = ActionExecutor(adapter, auto_confirm=auto_confirm)
            owner_map: dict[str, str] = {}
            for p in proposals:
                if p.target_owner_open_id and p.target_owner:
                    owner_map[p.target_owner] = p.target_owner_open_id
            exec_context = {
                "chat_id": args.chat_id,
                "project_id": args.project_id,
                "owner_map": owner_map,
            }
            # Convert ActionProposal → PlannedAction for executor
            from memory.action_planner import PlannedAction
            planned = []
            for p in proposals:
                planned.append(PlannedAction(
                    action_type="send_message" if p.action_type == "send_alert" else p.action_type,
                    title=p.metadata.get("alert_detail", p.title),
                    reason=p.reason,
                    command_hint="",
                    requires_confirmation=p.requires_confirmation,
                    metadata=p.metadata,
                ))
            results = executor.execute_plan(planned, exec_context)
            trigger.write_results(results, args.project_id)

            executed = sum(1 for r in results if r.success)
            blocked = sum(1 for r in results
                          if not r.success and "confirmation" in r.error)
            failed = sum(1 for r in results
                         if not r.success and "confirmation" not in r.error)
            print(f"    结果：{executed} 已执行，{blocked} 需确认，{failed} 失败\n")
            action_results = [
                (r.action.action_type, r.success, r.error, r.output_data)
                for r in results
            ]

    elif args.execute_actions:
        print("[3.5/5] 生成并执行行动计划...")
        from memory.action_planner import generate_action_plan
        from memory.action_executor import ActionExecutor

        plan = generate_action_plan(args.project_id, items)
        print(f"    生成了 {len(plan)} 个计划操作")

        # 构建 owner_map：解析 owner 姓名为 open_id，用于 @提醒
        owner_map: dict[str, str] = {}
        for item in items:
            if item.owner and item.state_type in ("owner", "next_step", "blocker"):
                if item.owner not in owner_map:
                    open_id = engine.resolve_owner_open_id(item.owner)
                    if open_id:
                        owner_map[item.owner] = open_id

        executor = ActionExecutor(adapter, auto_confirm=args.auto_confirm)
        exec_context = {
            "chat_id": args.chat_id,
            "project_id": args.project_id,
            "owner_map": owner_map,
        }
        results = executor.execute_plan(plan, exec_context)

        executed = sum(1 for r in results if r.success)
        blocked = sum(1 for r in results
                      if not r.success and "confirmation" in r.error)
        failed = sum(1 for r in results
                     if not r.success and "confirmation" not in r.error)
        print(f"    结果：{executed} 已执行，{blocked} 需确认，{failed} 失败")
        for r in results:
            if r.success:
                detail = ""
                if r.output_data:
                    detail = f" → {r.output_data}"
                print(f"      OK {r.action.action_type}: {r.action.title[:60]}{detail}")
            elif not r.success:
                print(f"      -- {r.action.action_type}: {r.error[:80]}")
        action_results = [
            (r.action.action_type, r.success, r.error, r.output_data)
            for r in results
        ]
        print()

    # ── Step 4: 生成状态面板 ────────────────────────────────────
    print("[4/5] 生成状态面板...")
    from memory.project_state import (
        build_group_project_state,
        build_personal_work_context,
        render_group_state_panel_text,
        render_personal_context_text,
    )

    if args.standup:
        from memory.project_state import render_standup_summary
        panel_text = render_standup_summary(items, args.project_id)
        panel_type = "站会摘要"
    elif args.personal:
        ctx = build_personal_work_context(args.personal, args.project_id, items)
        panel_text = render_personal_context_text(ctx)
        panel_type = f"个人上下文 ({args.personal})"
    else:
        state = build_group_project_state(args.project_id, items)
        panel_text = render_group_state_panel_text(state)
        panel_type = "项目状态面板"

    if not items:
        panel_text = f"当前项目 ({args.project_id}) 暂无提取到的协作记忆。请先在群内讨论后重试。\n"

    # 附加执行结果摘要
    if action_results:
        executed = sum(1 for _, ok, _, _ in action_results if ok)
        blocked = sum(1 for _, _, err, _ in action_results
                      if not err == "" and "confirmation" in err)
        failed = sum(1 for _, ok, err, _ in action_results
                     if not ok and "confirmation" not in err)
        panel_text += (
            f"\n---\n"
            f"*行动计划执行完毕：{executed} 成功"
        )
        if blocked:
            panel_text += f"，{blocked} 需确认"
        if failed:
            panel_text += f"，{failed} 失败"
        panel_text += "。*\n"

    print(f"    {panel_type}已生成 ({len(panel_text)} 字符)\n")

    # ── Step 5: 发送并置顶 ──────────────────────────────────────
    if args.dry_run:
        print("[5/5] DRY-RUN — 不发送消息\n")
        print("── 将发送以下内容 ──")
        _safe_print(panel_text)
        print("── 结束 ──")
        return

    print("[5/5] 发送消息到群聊...")
    send_result = adapter.send_message(
        args.chat_id,
        _markdown_safe(panel_text),
        msg_type="markdown",
        identity=args.identity,
    )
    if send_result.returncode != 0:
        print(f"    发送失败: {send_result.stderr or send_result.stdout}")
    else:
        msg_id = _extract_msg_id(send_result)
        print(f"    已发送 (message_id: {msg_id})")

        if not args.no_pin and msg_id:
            pin_result = adapter.pin_message(msg_id)
            if pin_result.returncode == 0:
                print(f"    已置顶")
            else:
                print(f"    置顶失败: {pin_result.stderr or pin_result.stdout}")

    print(f"\n{'='*60}")
    print(f"完成！数据目录: {Path(args.data_dir).resolve()}")
    print(f"{'='*60}")


# ── Message helpers ────────────────────────────────────────────────

def _extract_message_list(payload: Any) -> list[dict[str, Any]]:
    """Return the message list from a lark-cli JSON payload."""
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


def _is_collaboration_message(msg: dict) -> bool:
    """Filter out system messages and bot verification noise."""
    msg_type = msg.get("msg_type", "")
    if msg_type == "system":
        return False
    sender = msg.get("sender", {}) or {}
    sender_type = sender.get("sender_type", "")
    if sender_type in ("", "system"):
        return False
    return True


def _normalize_event(msg: dict, chat_id: str, project_id: str) -> dict[str, Any]:
    """Normalize one lark-cli message dict into a raw event dict."""
    content = _extract_text(msg.get("content", ""))
    sender = msg.get("sender", {}) or {}
    return {
        "project_id": project_id,
        "chat_id": chat_id,
        "message_id": str(msg.get("message_id") or ""),
        "text": content,
        "content": content,
        "msg_type": str(msg.get("msg_type", "text")),
        "created_at": str(msg.get("create_time") or ""),
        "sender": {
            "id": str(sender.get("id", "")),
            "sender_type": str(sender.get("sender_type", "")),
            "name": str(sender.get("name", sender.get("id", ""))),
        },
    }


def _extract_text(content: Any) -> str:
    """Extract readable text from lark-cli message content."""
    if not isinstance(content, str):
        return str(content)
    stripped = content.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(parsed, dict):
        text = parsed.get("text")
        if text:
            return str(text)
        title = parsed.get("title", "")
        body = parsed.get("content", "")
        if isinstance(body, list):
            body_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in body
            )
            return f"{title}\n{body_text}".strip()
        return str(parsed.get("content", stripped))
    return stripped


def _extract_msg_id(result) -> str:
    """Extract message_id from a send/reply CliResult."""
    if result.data:
        inner = result.data.get("data", result.data)
        if isinstance(inner, dict):
            return str(inner.get("message_id", ""))
    return ""


def _safe_print(text: str) -> None:
    """Print text safely on Windows terminals that may not support full Unicode."""
    try:
        print(text)
    except UnicodeEncodeError:
        # Replace non-ASCII characters with ? for terminal display
        print(text.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
