"""V1.12: 多用户数据隔离 — 按 open_id 分片存储。

每个用户拥有独立的数据目录: data/users/{open_id}/raw_events.jsonl + memory_state.json
MultiUserStore 自动路由到当前活跃用户的数据空间。

用法:
    session = UserSession()
    session.refresh()
    store = MultiUserStore(session, base_dir="data")
    items = store.list_items("my-project")  # 自动路由到当前用户目录
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from memory.schema import MemoryItem
from memory.store import MemoryStore


class MultiUserStore:
    """按 open_id 分片的记忆存储，自动路由到活跃用户的数据目录。"""

    def __init__(self, session, base_dir: str | Path = "data") -> None:
        """创建多用户存储。

        Args:
            session: UserSession 实例，提供当前活跃用户信息。
            base_dir: 基础数据目录。
        """
        self._session = session
        self._base_dir = Path(base_dir)
        self._stores: dict[str, MemoryStore] = {}

    # ── 路由 ────────────────────────────────────────────────────

    @property
    def current_user_id(self) -> str:
        uid = self._session.current_open_id
        return uid if uid else "anonymous"

    @property
    def current_store(self) -> MemoryStore:
        uid = self.current_user_id
        if uid not in self._stores:
            user_dir = self._base_dir / "users" / uid
            self._stores[uid] = MemoryStore(user_dir)
        return self._stores[uid]

    def get_store_for(self, open_id: str) -> MemoryStore:
        """获取指定用户的数据存储（仅用于管理员操作）。"""
        if open_id not in self._stores:
            self._stores[open_id] = MemoryStore(
                self._base_dir / "users" / open_id,
            )
        return self._stores[open_id]

    # ── 委托 ────────────────────────────────────────────────────

    def list_items(self, project_id: str | None = None,
                   as_of: str | None = None) -> list[MemoryItem]:
        return self.current_store.list_items(project_id, as_of=as_of)

    def list_history(self, project_id: str | None = None) -> list[MemoryItem]:
        return self.current_store.list_history(project_id)

    def search_keywords(self, query: str, project_id: str | None = None,
                        as_of: str | None = None,
                        top_k: int = 10) -> list:
        return self.current_store.search_keywords(
            query, project_id=project_id, as_of=as_of, top_k=top_k,
        )

    def search_advanced(self, **kwargs) -> list:
        return self.current_store.search_advanced(**kwargs)

    def find_items_by_message_id(self, message_id: str) -> list[MemoryItem]:
        return self.current_store.find_items_by_message_id(message_id)

    def upsert_items(self, new_items, processed_ids=()):
        return self.current_store.upsert_items(new_items, processed_ids)

    def append_raw_events(self, events) -> int:
        return self.current_store.append_raw_events(events)

    def read_raw_events(self, project_id: str | None = None) -> list[dict]:
        return self.current_store.read_raw_events(project_id)

    def processed_event_ids(self) -> list[str]:
        return self.current_store.processed_event_ids()

    def mark_processed(self, event_ids) -> None:
        self.current_store.mark_processed(event_ids)

    def build_inverted_index(self):
        return self.current_store.build_inverted_index()

    # ── 管理 ────────────────────────────────────────────────────

    def list_all_user_ids(self) -> list[str]:
        """列出所有已有数据的用户 ID。"""
        users_dir = self._base_dir / "users"
        if not users_dir.exists():
            return []
        return [
            d.name for d in users_dir.iterdir()
            if d.is_dir() and (d / "memory_state.json").exists()
        ]
