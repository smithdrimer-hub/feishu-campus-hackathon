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
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _resolve_lark_cli_binary() -> str:
    """Pick the right lark-cli binary for this OS.

    Priority:
      1. $LARK_CLI_BIN env var (explicit override)
      2. lark-cli  (POSIX path, expected on Linux/macOS)
      3. lark-cli.cmd (legacy Windows fallback)
    """
    explicit = os.environ.get("LARK_CLI_BIN")
    if explicit:
        return explicit
    if os.name == "nt":
        return "lark-cli.cmd"
    return "lark-cli"


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
            _resolve_lark_cli_binary(), "event", "+subscribe",
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
        """Read NDJSON from subprocess stdout, one line per event."""
        assert self._process and self._process.stdout
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

            # Filter by chat_id
            ev_chat = event.get("chat_id", "")
            if self.chat_id and ev_chat and ev_chat != self.chat_id:
                continue

            if self.on_event:
                try:
                    self.on_event(event)
                except Exception as e:
                    logger.warning("Event handler error: %s", e)

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
