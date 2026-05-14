"""Feishu WebSocket event listener via lark-cli event +subscribe.

V1.16: Wraps the official `lark-cli event +subscribe` command as a subprocess,
reading NDJSON events from stdout. Provides heartbeat detection, exponential
backoff reconnection, and event routing.

Architecture:
    lark-cli event +subscribe (subprocess, stdout NDJSON)
            │
    EventStreamListener (read stdout, heartbeat, reconnect)
            │
    EventRouter (filter by chat_id, dispatch to handlers)
"""

from __future__ import annotations

import atexit
import json
import logging
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventStreamListener:
    """Wrap lark-cli event +subscribe as a managed subprocess.

    Usage:
        listener = EventStreamListener(chat_id="oc_xxx")
        listener.on_event = lambda event: print(event)
        listener.start()  # blocks until stop() is called
    """

    def __init__(
        self,
        chat_id: str = "",
        event_types: str = "im.message.receive_v1",
        heartbeat_timeout: float = 90,
        reconnect_max_delay: float = 60,
    ) -> None:
        self.chat_id = chat_id
        self.event_types = event_types
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_max_delay = reconnect_max_delay
        self._process: subprocess.Popen | None = None
        self._running = False
        self._last_event_time = 0.0
        self._retry_count = 0
        self.on_event: Callable[[dict], None] | None = None
        # Track whether we're in the middle of a reconnect
        self._reconnecting = False
        atexit.register(self.stop)

    def start(self) -> None:
        """Start the event loop. Blocks until stop() is called."""
        self._running = True
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except ValueError:
            pass  # running in a thread — signals not available

        while self._running:
            try:
                self._start_subprocess()
                self._read_events()
            except Exception as e:
                logger.warning("Event listener error: %s", e)
            if self._running:
                self._reconnect()

    def stop(self) -> None:
        """Gracefully stop the listener."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()

    # ── internal ────────────────────────────────────────────────

    def _start_subprocess(self) -> None:
        """Launch lark-cli event +subscribe."""
        args = [
            "lark-cli.cmd", "event", "+subscribe",
            "--as", "bot",
            "--compact",
            "--event-types", self.event_types,
        ]
        logger.info("Starting: %s", " ".join(args))
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        self._last_event_time = time.time()

    def _read_events(self) -> None:
        """Read NDJSON from subprocess stdout, one line per event.

        V1.19: 心跳监护——后台线程定期检查 _check_heartbeat()，
        超时时终止子进程，触发外层重连。
        """
        assert self._process and self._process.stdout

        # 启动心跳监护线程
        heartbeat_stop = threading.Event()

        def _heartbeat_watchdog() -> None:
            check_interval = max(self.heartbeat_timeout / 3, 10.0)
            while not heartbeat_stop.is_set():
                time.sleep(check_interval)
                if heartbeat_stop.is_set():
                    return
                if not self._check_heartbeat():
                    logger.warning(
                        "Heartbeat lost (%.0fs since last event), restarting subprocess",
                        time.time() - self._last_event_time,
                    )
                    if self._process and self._process.poll() is None:
                        self._process.terminate()
                    return

        watchdog = threading.Thread(target=_heartbeat_watchdog, daemon=True)
        watchdog.start()

        try:
            for line in self._process.stdout:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._last_event_time = time.time()
                self._retry_count = 0  # reset on successful event

                # Filter by chat_id（reaction 事件无 chat_id，不筛选）
                ev_chat = event.get("chat_id", "")
                ev_type = event.get("type", event.get("event_type", ""))
                if self.chat_id and ev_chat and ev_chat != self.chat_id:
                    continue

                if self.on_event:
                    try:
                        self.on_event(event)
                    except Exception as e:
                        logger.warning("Event handler error: %s", e)
        finally:
            heartbeat_stop.set()

    def _reconnect(self) -> None:
        """Exponential backoff reconnect."""
        delay = min(2 ** self._retry_count, self.reconnect_max_delay)
        self._retry_count += 1
        logger.info("Reconnecting in %.0fs (attempt %d)...", delay, self._retry_count)
        time.sleep(delay)

    def _check_heartbeat(self) -> bool:
        """Return True if heartbeat is still alive."""
        elapsed = time.time() - self._last_event_time
        return elapsed < self.heartbeat_timeout

    def _handle_signal(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM."""
        logger.info("Received signal %d, stopping...", signum)
        self.stop()


class EventRouter:
    """Route incoming events to registered handlers by event type.

    Usage:
        router = EventRouter(chat_id="oc_xxx", store=store, adapter=adapter)
        listener = EventStreamListener(chat_id="oc_xxx")
        listener.on_event = router.handle
    """

    def __init__(self, chat_id: str = "", store=None, adapter=None) -> None:
        self.chat_id = chat_id
        self.store = store
        self.adapter = adapter
        self.handlers: dict[str, Callable] = {}

    def register(self, event_type: str, handler: Callable) -> None:
        self.handlers[event_type] = handler

    def handle(self, event: dict) -> None:
        """Dispatch an event to the appropriate handler."""
        etype = event.get("event_type", event.get("type", ""))
        handler = self.handlers.get(etype)
        if handler:
            try:
                handler(event)
            except Exception as e:
                logger.warning("Handler error for %s: %s", etype, e)
        else:
            logger.debug("No handler for event type: %s", etype)

    @staticmethod
    def extract_text(event: dict) -> str:
        """Extract message text from a compact-format event."""
        return str(
            event.get("text", "")
            or event.get("content", "")
            or event.get("body", {}).get("content", "")
        )


# ── FEAT-5: New member onboarding handler ────────────────────────

def handle_member_added(
    event: dict,
    store: Any = None,
    adapter: Any = None,
    chat_id: str = "",
    project_id: str = "",
) -> str | None:
    """Handle im.chat.member.user.added_v1 event.

    When a new member joins a managed group chat, generate a welcome
    context card using the existing project-state machinery and send
    it to the group.  Returns the message_id if successfully sent,
    or None if the event is not a member-added event or sending failed.

    Args:
        event: Raw Feishu event dict (compact format).
        store: MemoryStore instance for listing items.
        adapter: LarkCliAdapter for sending messages.
        chat_id: Target chat for the welcome message.
        project_id: Project identifier for context generation.

    Returns:
        Sent message_id string, or None.
    """
    # Extract member info from event
    members = event.get("members", []) or []
    event_type = event.get("event_type", "") or event.get("type", "")
    if "member.user.added" not in str(event_type):
        return None

    for member in members:
        user_id = (member.get("user_id", "")
                   or member.get("open_id", "")
                   or member.get("member_id", ""))
        name = (member.get("name", "")
                or member.get("display_name", "")
                or member.get("member_name", ""))
        if not user_id:
            continue

        try:
            from memory.project_state import (
                build_personal_work_context,
                render_personal_context_text,
            )
            resolved_project = project_id or "default"
            items = store.list_items(resolved_project) if store else []
            ctx = build_personal_work_context(name, resolved_project, items)
            welcome_text = render_personal_context_text(ctx)

            welcome_msg = (
                f"欢迎新成员 **{name}** 加入群聊！\n\n"
                f"**当前项目状态已为你加载：**\n\n"
                f"{welcome_text}\n\n"
                f"---\n回复 **@bot 状态** 查看更多"
            )

            if adapter:
                result = adapter.send_message(
                    chat_id, welcome_msg, msg_type="markdown",
                )
                if result.returncode == 0 and result.data:
                    inner = (result.data.get("data", result.data)
                             if isinstance(result.data, dict) else {})
                    return str(inner.get("message_id", ""))
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "handle_member_added failed for %s", name, exc_info=True)

    return None
