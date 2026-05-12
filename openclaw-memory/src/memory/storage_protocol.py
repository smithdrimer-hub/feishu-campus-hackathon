"""V1.19: 存储后端协议 — MemoryStore 的持久化层可替换。

当前默认实现：JsonStorageBackend（JSON/JSONL 文件）。
扩展方式：实现 StorageBackend 接口，传给 MemoryStore(backend=...)。

不做 SQLite 实现——仅预留接口证明架构可扩展。
"""

from __future__ import annotations

import json as _json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable


class StorageBackend(ABC):
    """MemoryStore 持久化层的抽象接口。

    所有方法接收/返回 Python 原生类型（dict/list/MemoryItem dict），
    不依赖具体的文件格式。
    """

    @abstractmethod
    def ensure_files(self) -> None:
        """初始化存储——创建必要的文件/目录/表。"""
        ...

    @abstractmethod
    def load_state(self) -> dict[str, Any]:
        """加载完整状态，返回 {"items": [...], "history": [...], "processed_event_ids": [...]}。"""
        ...

    @abstractmethod
    def save_state(self, items: list, history: list, processed_ids: list[str]) -> None:
        """保存完整状态。"""
        ...

    @abstractmethod
    def append_raw_events(self, events: Iterable[dict[str, Any]]) -> int:
        """追加原始事件，返回写入数量。"""
        ...

    @abstractmethod
    def read_raw_events(self) -> list[dict[str, Any]]:
        """读取所有原始事件。"""
        ...

    @abstractmethod
    def processed_event_ids(self) -> list[str]:
        """返回已处理的事件 ID 列表。"""
        ...

    @abstractmethod
    def mark_processed(self, event_ids: Iterable[str]) -> None:
        """标记事件 ID 为已处理。"""
        ...

    # ── V1.19 P2: 查询下推（可选实现）────────────────────────────

    def list_items(self, project_id: str | None = None,
                   statuses: set[str] | None = None,
                   as_of: str | None = None,
                   user_id: str | None = None,
                   limit: int = 0, offset: int = 0) -> list[dict] | None:
        """后端级过滤查询。返回 None 表示委托 MemoryStore 在内存中过滤。"""
        return None

    def search_keywords(self, query: str, project_id: str | None = None,
                        top_k: int = 10) -> list[dict] | None:
        """后端级关键词搜索。返回 None 表示委托 MemoryStore 在内存中搜索。"""
        return None


class JsonStorageBackend(StorageBackend):
    """基于 JSON/JSONL 文件的默认存储后端。

    文件结构:
      - {data_dir}/memory_state.json  — 完整状态（items + history + processed）
      - {data_dir}/raw_events.jsonl   — 原始事件（追加）
      - {data_dir}/audit.jsonl        — 审计日志（追加）
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.memory_state_path = self.data_dir / "memory_state.json"
        self.raw_events_path = self.data_dir / "raw_events.jsonl"
        self.audit_path = self.data_dir / "audit.jsonl"

    # ── StorageBackend 实现 ─────────────────────────────────────

    def ensure_files(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.raw_events_path.exists():
            self.raw_events_path.write_text("", encoding="utf-8")
        if not self.memory_state_path.exists():
            self.save_state([], [], [])

    def load_state(self) -> dict[str, Any]:
        if not self.memory_state_path.exists():
            return {"items": [], "history": [], "processed_event_ids": []}
        try:
            return _json.loads(
                self.memory_state_path.read_text(encoding="utf-8")
            )
        except (_json.JSONDecodeError, OSError):
            return {"items": [], "history": [], "processed_event_ids": []}

    def save_state(self, items: list, history: list, processed_ids: list[str]) -> None:
        payload = {
            "items": [item.to_dict() if hasattr(item, "to_dict") else item for item in items],
            "history": [item.to_dict() if hasattr(item, "to_dict") else item for item in history],
            "processed_event_ids": processed_ids,
        }
        tmp = self.memory_state_path.with_suffix(".json.tmp")
        text = _json.dumps(payload, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.memory_state_path)

    def append_raw_events(self, events: Iterable[dict[str, Any]]) -> int:
        self.ensure_files()
        existing_ids = set()
        try:
            for line in self.raw_events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        ev = _json.loads(line)
                        mid = ev.get("message_id", "")
                        if mid:
                            existing_ids.add(mid)
                    except _json.JSONDecodeError:
                        pass
        except OSError:
            pass

        written = 0
        with self.raw_events_path.open("a", encoding="utf-8") as handle:
            for event in events:
                eid = event.get("message_id", "")
                if eid and eid in existing_ids:
                    continue
                if eid:
                    existing_ids.add(eid)
                handle.write(_json.dumps(event, ensure_ascii=False) + "\n")
                written += 1
        return written

    def read_raw_events(self) -> list[dict[str, Any]]:
        if not self.raw_events_path.exists():
            return []
        results = []
        for line in self.raw_events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(_json.loads(line))
            except _json.JSONDecodeError:
                pass
        return results

    def processed_event_ids(self) -> list[str]:
        state = self.load_state()
        return list(state.get("processed_event_ids", []))

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        state = self.load_state()
        existing = set(state.get("processed_event_ids", []))
        existing.update(event_ids)
        # 保持 items/history 不变，只更新 processed
        self.save_state(
            state.get("items", []),
            state.get("history", []),
            list(existing),
        )
