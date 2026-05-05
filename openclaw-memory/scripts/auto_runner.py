"""Automated daily sync runner with optional WebSocket event listener.

V1.16: Supports --once (single run) and continuous mode (daily 9AM sync
+ WebSocket event listener for confirmation replies and @bot commands).

Usage:
  python scripts/auto_runner.py --once                # single run, exit
  python scripts/auto_runner.py                        # continuous mode
  python scripts/auto_runner.py --config my.yaml       # custom config
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("auto_runner")


def load_config(path: str) -> dict:
    """Load YAML config, falling back to example if not found."""
    p = Path(path)
    if not p.exists():
        example = p.with_name("config.example.yaml")
        if example.exists():
            logger.warning("%s not found, using %s", path, example)
            p = example
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_pipeline(project: dict, dry_run: bool = True) -> None:
    """Run the full sync+extract pipeline for one project."""
    from memory.store import MemoryStore
    from memory.engine import MemoryEngine
    from memory.extractor import RuleBasedExtractor
    from adapters.lark_cli_adapter import LarkCliAdapter

    chat_id = project.get("chat_id", "")
    project_id = project.get("project_id", "default")
    data_dir = project.get("data_dir", "data/")

    adapter = LarkCliAdapter()
    store = MemoryStore(Path(data_dir))
    engine = MemoryEngine(store, RuleBasedExtractor(), adapter=adapter)

    # Sync messages
    logger.info("[%s] Syncing messages...", project_id)
    result = adapter.list_chat_messages(chat_id, page_size=50)
    if result.returncode != 0:
        logger.error("[%s] Sync failed", project_id)
        return

    payload = result.data or {}
    msgs = payload.get("data", {}).get("messages", []) or []
    events = []
    for m in msgs:
        if m.get("msg_type") == "system":
            continue
        text = str(m.get("content", m.get("text", "")))[:2000]
        if not text:
            continue
        events.append({
            "project_id": project_id, "chat_id": chat_id,
            "message_id": m.get("message_id", ""), "text": text,
            "created_at": m.get("create_time", ""),
            "sender": {
                "id": (m.get("sender", {}) or {}).get("id", ""),
                "name": (m.get("sender", {}) or {}).get("name", ""),
                "sender_type": (m.get("sender", {}) or {}).get("sender_type", "user"),
            },
        })

    store.append_raw_events(events)
    items = engine.process_new_events(project_id, debounce=False)
    logger.info("[%s] Extracted %d active items", project_id, len(items))

    # Generate state panel (preview mode — no auto send)
    from memory.project_state import build_group_project_state, \
        render_group_state_panel_text
    state = build_group_project_state(project_id, items)
    panel = render_group_state_panel_text(state)
    logger.info("[%s] State panel: %d chars", project_id, len(panel))

    # Generate morning report
    report = generate_morning_report(project_id, items, store)

    if not dry_run:
        adapter.send_message(chat_id, panel, msg_type="markdown")
        adapter.send_message(chat_id, report, msg_type="markdown")
        logger.info("[%s] Panel + morning report sent", project_id)
    else:
        logger.info("[%s] Dry-run: not sent", project_id)


def generate_morning_report(project_id: str, items: list, store) -> str:
    """Generate a concise morning briefing."""
    from datetime import datetime, timedelta, timezone
    from memory.date_parser import deadline_is_imminent

    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(hours=24)).isoformat()
    today_str = now.strftime("%Y-%m-%d")

    # Counts
    tasks = [i for i in items if i.state_type == "next_step" and i.status == "active"]
    blockers = [i for i in items if i.state_type == "blocker" and i.status == "active"]
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)
    deadlines = [i for i in items if i.state_type == "deadline" and i.status == "active"]
    imminent = [d for d in deadlines if deadline_is_imminent(d.current_value, within_days=3)]
    needs_review = [i for i in items if getattr(i, "review_status", "") == "needs_review"]

    # Yesterday changes
    new_tasks = [i for i in items if i.updated_at and i.updated_at > yesterday and i.state_type == "next_step"]
    new_blockers = [i for i in items if i.updated_at and i.updated_at > yesterday and i.state_type == "blocker"]
    resolved = [i for i in items if i.state_type == "blocker"
                and (getattr(i, "metadata", {}) or {}).get("blocker_status") == "resolved"]

    lines = [
        f"## 每日早报 — {project_id}",
        f"_{today_str}_",
        "",
        "### 当前状态",
        f"- 活跃任务: {len(tasks)} 个",
        f"- 未解决阻塞: {len(unresolved)} 个",
    ]
    if imminent:
        lines.append(f"- 临近截止: {len(imminent)} 个")
        for d in imminent[:3]:
            lines.append(f"  - {d.current_value[:60]}")
    lines.append(f"- 待审核记忆: {len(needs_review)} 条")
    lines.append("")

    if new_tasks or new_blockers or resolved:
        lines.append("### 昨日变化")
        if new_tasks:
            lines.append(f"- 新增任务: {len(new_tasks)} 个")
        if new_blockers:
            lines.append(f"- 新增阻塞: {len(new_blockers)} 个")
        if resolved:
            lines.append(f"- 已解决阻塞: {len(resolved)} 个")
        lines.append("")

    if needs_review:
        lines.append(f"> 有 {len(needs_review)} 条记忆待审核，使用 `审核台` 查看详情。")

    return "\n".join(lines)


def daily_sync_loop(config: dict) -> None:
    """Thread: run pipeline at configured time each day."""
    sync_cfg = config.get("auto_sync", {})
    if not sync_cfg.get("enabled", True):
        logger.info("Auto-sync disabled")
        return

    sync_time = sync_cfg.get("time", "09:00")
    dry_run = config.get("demo", {}).get("dry_run", True)
    projects = config.get("projects", [])

    logger.info("Daily sync scheduled at %s", sync_time)
    last_run_date = ""

    while True:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        if current_time >= sync_time and today != last_run_date:
            logger.info("=== Daily sync triggered ===")
            for proj in projects:
                try:
                    run_pipeline(proj, dry_run=dry_run)
                except Exception as e:
                    logger.error("Pipeline error for %s: %s",
                                 proj.get("project_id"), e)
            last_run_date = today

        time.sleep(60)


def event_listen_loop(config: dict) -> None:
    """Thread: WebSocket event listener for replies and @bot commands."""
    ev_cfg = config.get("event_listener", {})
    if not ev_cfg.get("enabled", False):
        logger.info("Event listener disabled")
        return

    from adapters.event_listener import EventStreamListener, EventRouter
    from memory.reply_handler import (
        detect_bot_command, execute_bot_command,
        find_question, parse_confirmation,
    )

    projects = config.get("projects", [])
    if not projects:
        logger.warning("No projects configured for event listener")
        return

    proj = projects[0]
    chat_id = proj.get("chat_id", "")
    project_id = proj.get("project_id", "default")
    data_dir = proj.get("data_dir", "data/")

    from memory.store import MemoryStore
    from adapters.lark_cli_adapter import LarkCliAdapter
    store = MemoryStore(Path(data_dir))
    adapter = LarkCliAdapter()

    router = EventRouter(chat_id=chat_id, store=store, adapter=adapter)

    def handle_message(event: dict) -> None:
        text = EventRouter.extract_text(event)
        if not text:
            return

        # Check for confirmation reply
        reply_to = event.get("reply_to", event.get("parent_id", ""))
        if reply_to:
            question = find_question(reply_to)
            if question:
                is_conf, indices = parse_confirmation(text)
                if is_conf:
                    logger.info("Confirmation reply detected: %s → %s",
                                text[:50], indices)
                    # TODO: approve/reject based on indices
                    adapter.send_message(chat_id,
                                         f"收到确认: {len(indices)} 项已标记")
                    return

        # Check for @bot command
        cmd = detect_bot_command(text)
        if cmd:
            logger.info("Bot command: %s → %s", text[:50], cmd)
            response = execute_bot_command(cmd, chat_id, project_id,
                                           store, adapter)
            adapter.send_message(chat_id, response, msg_type="markdown")

    router.register("im.message.receive_v1", handle_message)

    listener = EventStreamListener(
        chat_id=chat_id,
        event_types=ev_cfg.get("event_types", "im.message.receive_v1"),
        heartbeat_timeout=ev_cfg.get("heartbeat_timeout", 90),
        reconnect_max_delay=ev_cfg.get("reconnect_max_delay", 60),
    )
    listener.on_event = router.handle
    logger.info("Event listener starting for chat %s...", chat_id)
    listener.start()


# ── CLI ────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Memory Engine Auto Runner")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"),
                        help="Path to config YAML file")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit (no scheduler)")
    parser.add_argument("--ws-check", action="store_true",
                        help="Run WebSocket health check for N minutes")
    parser.add_argument("--minutes", type=int, default=5,
                        help="Duration for --ws-check (default: 5)")
    return parser.parse_args()


def ws_health_check(config: dict, minutes: int) -> None:
    """Run WebSocket listener for N minutes and report health stats."""
    from adapters.event_listener import EventStreamListener
    import time as _time

    ev_cfg = config.get("event_listener", {})
    projects = config.get("projects", [])
    if not projects:
        logger.error("No projects configured")
        return
    chat_id = projects[0].get("chat_id", "")

    stats = {
        "start_time": _time.time(),
        "events": 0,
        "errors": 0,
        "heartbeats": 0,
        "reconnects": 0,
        "by_type": {},
        "last_heartbeat": _time.time(),
    }

    def on_event(event: dict) -> None:
        stats["events"] += 1
        stats["last_heartbeat"] = _time.time()
        etype = event.get("event_type", event.get("type", "unknown"))
        stats["by_type"][etype] = stats["by_type"].get(etype, 0) + 1

    listener = EventStreamListener(
        chat_id=chat_id,
        event_types=ev_cfg.get("event_types", "im.message.receive_v1"),
        heartbeat_timeout=ev_cfg.get("heartbeat_timeout", 90),
        reconnect_max_delay=ev_cfg.get("reconnect_max_delay", 60),
    )
    listener.on_event = on_event

    # Run in background thread, wait for duration
    import threading
    t = threading.Thread(target=listener.start, daemon=True)
    t.start()

    deadline = _time.time() + minutes * 60
    while _time.time() < deadline:
        _time.sleep(5)
        # Check heartbeat
        elapsed_since_last = _time.time() - stats["last_heartbeat"]
        if elapsed_since_last > ev_cfg.get("heartbeat_timeout", 90):
            stats["errors"] += 1

    listener.stop()
    elapsed = _time.time() - stats["start_time"]

    # Report
    print(f"\nWebSocket Health Check ({minutes} min)")
    print(f"  Online: {int(elapsed // 60)}m{int(elapsed % 60)}s")
    print(f"  Events received: {stats['events']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Events by type:")
    for etype, count in sorted(stats["by_type"].items()):
        print(f"    {etype}: {count}")
    if stats["events"] == 0:
        print(f"  (No events — group may have no new messages)")


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.ws_check:
        ws_health_check(config, args.minutes)
        return

    if args.once:
        projects = config.get("projects", [])
        dry_run = config.get("demo", {}).get("dry_run", True)
        for proj in projects:
            run_pipeline(proj, dry_run=dry_run)
        logger.info("--once completed")
        return

    # Continuous mode: daily sync + event listener
    logger.info("Starting auto runner (continuous mode)...")
    t1 = threading.Thread(target=daily_sync_loop, args=(config,), daemon=True)
    t2 = threading.Thread(target=event_listen_loop, args=(config,), daemon=True)
    t1.start()
    t2.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
