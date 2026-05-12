"""V1.19 P1: SQLite 存储后端 — 实现 StorageBackend 协议。

统一表设计：memory_records 用 record_type 区分 active/history。
WAL 模式 + 事务安全 + 索引优化。
"""

from __future__ import annotations

import json as _json
import sqlite3
import threading
from pathlib import Path
import re
from typing import Any, Iterable

from memory.storage_protocol import StorageBackend

# ── 建表 SQL ──────────────────────────────────────────────────────

_CREATE_MEMORY_RECORDS = """
CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    state_type TEXT NOT NULL,
    key TEXT NOT NULL,
    current_value TEXT NOT NULL,
    rationale TEXT DEFAULT '',
    owner TEXT,
    status TEXT DEFAULT 'active',
    confidence REAL DEFAULT 0.5,
    version INTEGER DEFAULT 1,
    supersedes TEXT DEFAULT '[]',
    updated_at TEXT DEFAULT '',
    valid_from TEXT DEFAULT '',
    valid_to TEXT,
    recorded_at TEXT DEFAULT '',
    decision_strength TEXT DEFAULT '',
    review_status TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    status_reason TEXT DEFAULT '',
    status_changed_at TEXT DEFAULT '',
    status_changed_by TEXT DEFAULT '',
    source_refs TEXT DEFAULT '[]',
    media_refs TEXT DEFAULT '[]',
    record_type TEXT DEFAULT 'active'
)
"""

_CREATE_RAW_EVENTS = """
CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE NOT NULL,
    project_id TEXT DEFAULT '',
    chat_id TEXT DEFAULT '',
    text TEXT DEFAULT '',
    content TEXT DEFAULT '',
    msg_type TEXT DEFAULT 'text',
    created_at TEXT DEFAULT '',
    sender_id TEXT DEFAULT '',
    sender_name TEXT DEFAULT '',
    sender_type TEXT DEFAULT '',
    media_refs TEXT DEFAULT '[]',
    has_unsupported_media INTEGER DEFAULT 0,
    raw_json TEXT DEFAULT '{}'
)
"""

_CREATE_PROCESSED_EVENTS = """
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    processed_at TEXT DEFAULT ''
)
"""

# ── 索引 ───────────────────────────────────────────────────────────

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mem_project_type ON memory_records(project_id, record_type)",
    "CREATE INDEX IF NOT EXISTS idx_mem_state_type ON memory_records(state_type)",
    "CREATE INDEX IF NOT EXISTS idx_mem_status ON memory_records(status)",
    "CREATE INDEX IF NOT EXISTS idx_mem_identity ON memory_records(project_id, state_type, key)",
    "CREATE INDEX IF NOT EXISTS idx_raw_msg_id ON raw_events(message_id)",
]

# ── SQLiteStorageBackend ──────────────────────────────────────────


class SQLiteStorageBackend(StorageBackend):
    """基于 SQLite 的 MemoryStore 持久化后端。

    所有写操作走事务。统一 memory_records 表。
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "memory.db"
        self._local = threading.local()

    # ── 连接管理 ──────────────────────────────────────────────────

    @property
    def _conn(self) -> sqlite3.Connection:
        """线程本地连接（WAL 模式下支持多线程读）。"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    # ── 连接管理 ──────────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接（测试清理时使用）。"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ── StorageBackend 实现 ───────────────────────────────────────

    def ensure_files(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = self._conn
        conn.execute(_CREATE_MEMORY_RECORDS)
        conn.execute(_CREATE_RAW_EVENTS)
        conn.execute(_CREATE_PROCESSED_EVENTS)
        for idx_sql in _INDEXES:
            conn.execute(idx_sql)
        conn.commit()

    def load_state(self) -> dict[str, Any]:
        self.ensure_files()
        conn = self._conn
        active_rows = conn.execute(
            "SELECT * FROM memory_records WHERE record_type='active'"
        ).fetchall()
        history_rows = conn.execute(
            "SELECT * FROM memory_records WHERE record_type='history'"
        ).fetchall()
        processed_rows = conn.execute(
            "SELECT event_id FROM processed_events"
        ).fetchall()

        items = [self._row_to_item_dict(dict(r)) for r in active_rows]
        history = [self._row_to_item_dict(dict(r)) for r in history_rows]
        processed = [r["event_id"] for r in processed_rows]
        return {"items": items, "history": history, "processed_event_ids": processed}

    def save_state(self, items: list, history: list, processed_ids: list[str]) -> None:
        conn = self._conn
        try:
            # 清空并重写 active/history
            conn.execute("DELETE FROM memory_records WHERE record_type='active'")
            conn.execute("DELETE FROM memory_records WHERE record_type='history'")
            for item in items:
                row = self._item_to_row(item)
                row["record_type"] = "active"
                self._insert_memory_record(conn, row)
            for item in history:
                row = self._item_to_row(item)
                row["record_type"] = "history"
                self._insert_memory_record(conn, row)
            # 追加处理的 event IDs
            for eid in processed_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO processed_events(event_id, processed_at) VALUES(?, ?)",
                    (str(eid), _utc_now()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def append_raw_events(self, events: Iterable[dict[str, Any]]) -> int:
        self.ensure_files()
        conn = self._conn
        written = 0
        try:
            for event in events:
                mid = event.get("message_id", "")
                if not mid:
                    continue
                sender = event.get("sender", {}) or {}
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO raw_events
                       (message_id, project_id, chat_id, text, content, msg_type,
                        created_at, sender_id, sender_name, sender_type,
                        media_refs, has_unsupported_media, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(mid),
                        str(event.get("project_id", "")),
                        str(event.get("chat_id", "")),
                        str(event.get("text", "")),
                        str(event.get("content", "")),
                        str(event.get("msg_type", "text")),
                        str(event.get("created_at", "")),
                        str(sender.get("id", "")),
                        str(sender.get("name", "")),
                        str(sender.get("sender_type", "")),
                        _json.dumps(event.get("media_refs", []), ensure_ascii=False),
                        int(bool(event.get("has_unsupported_media"))),
                        _json.dumps(event, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount > 0:
                    written += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return written

    def read_raw_events(self) -> list[dict[str, Any]]:
        self.ensure_files()
        conn = self._conn
        rows = conn.execute("SELECT raw_json FROM raw_events ORDER BY id").fetchall()
        results = []
        for r in rows:
            try:
                results.append(_json.loads(r["raw_json"]))
            except (_json.JSONDecodeError, TypeError):
                pass
        return results

    def processed_event_ids(self) -> list[str]:
        self.ensure_files()
        conn = self._conn
        return [r["event_id"] for r in conn.execute("SELECT event_id FROM processed_events").fetchall()]

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        self.ensure_files()
        conn = self._conn
        try:
            for eid in event_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO processed_events(event_id, processed_at) VALUES(?, ?)",
                    (str(eid), _utc_now()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ── V1.19 P2: 查询下推 ──────────────────────────────────────

    def list_items(self, project_id: str | None = None,
                   statuses: set[str] | None = None,
                   as_of: str | None = None,
                   user_id: str | None = None,
                   limit: int = 0, offset: int = 0) -> list[dict] | None:
        """SQL WHERE 下推——只返回匹配行，不在内存中全量过滤。"""
        self.ensure_files()
        conn = self._conn
        where = ["record_type='active'"]
        params: list[Any] = []

        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if as_of:
            where.append("(valid_from = '' OR valid_from <= ?)")
            params.append(as_of)
            where.append("(valid_to IS NULL OR valid_to > ?)")
            params.append(as_of)

        sql = f"SELECT * FROM memory_records WHERE {' AND '.join(where)} ORDER BY recorded_at DESC"
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        if offset > 0:
            sql += f" OFFSET {int(offset)}"

        rows = conn.execute(sql, params).fetchall()
        results = [self._row_to_item_dict(dict(r)) for r in rows]

        # user_id 过滤仍在内存中（source_refs 是 JSON 列）
        if user_id:
            results = [
                r for r in results
                if any(ref.get("sender_id") == user_id
                       for ref in (r.get("source_refs") or []))
            ]
        return results

    def search_keywords(self, query: str, project_id: str | None = None,
                        top_k: int = 10) -> list[dict] | None:
        """SQL LIKE 搜索——利用索引，不走全量内存遍历。"""
        if not query.strip():
            return []
        self.ensure_files()
        conn = self._conn
        # 中文按字符拆词，英文按空格
        terms = _tokenize_query(query)
        if not terms:
            return []

        where = ["record_type='active'"]
        params: list[Any] = []
        for term in terms[:5]:  # 最多 5 个词，防 SQL 膨胀
            where.append("(current_value LIKE ? OR rationale LIKE ?)")
            params.extend([f"%{term}%", f"%{term}%"])
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)

        sql = f"SELECT * FROM memory_records WHERE {' AND '.join(where)} ORDER BY confidence DESC LIMIT {int(top_k)}"
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_item_dict(dict(r)) for r in rows]

    # ── 内部 ──────────────────────────────────────────────────────

    @staticmethod
    def _item_to_row(item) -> dict[str, Any]:
        """将 MemoryItem（或 dict）转为统一的列字典。"""
        if isinstance(item, dict):
            d = item
        elif hasattr(item, "to_dict"):
            d = item.to_dict()
        else:
            d = item

        refs = d.get("source_refs", [])
        if not isinstance(refs, list):
            refs = []
        refs_json = _json.dumps(
            [r.to_dict() if hasattr(r, "to_dict") else r for r in refs],
            ensure_ascii=False,
        )

        return {
            "memory_id": str(d.get("memory_id", "")),
            "project_id": str(d.get("project_id", "")),
            "state_type": str(d.get("state_type", "")),
            "key": str(d.get("key", "")),
            "current_value": str(d.get("current_value", "")),
            "rationale": str(d.get("rationale", "")),
            "owner": d.get("owner"),
            "status": str(d.get("status", "active")),
            "confidence": float(d.get("confidence", 0.5)),
            "version": int(d.get("version", 1)),
            "supersedes": _json.dumps(list(d.get("supersedes", []))),
            "updated_at": str(d.get("updated_at", "")),
            "valid_from": str(d.get("valid_from", "")),
            "valid_to": d.get("valid_to"),
            "recorded_at": str(d.get("recorded_at", "")),
            "decision_strength": str(d.get("decision_strength", "")),
            "review_status": str(d.get("review_status", "")),
            "metadata": _json.dumps(dict(d.get("metadata", {}) or {}), ensure_ascii=False),
            "status_reason": str(d.get("status_reason", "")),
            "status_changed_at": str(d.get("status_changed_at", "")),
            "status_changed_by": str(d.get("status_changed_by", "")),
            "source_refs": refs_json,
            "media_refs": _json.dumps(list(d.get("media_refs", []))),
        }

    @staticmethod
    def _row_to_item_dict(row: dict[str, Any]) -> dict[str, Any]:
        """将数据库行还原为 MemoryItem.from_dict 可以消费的 dict。"""
        refs_raw = row.get("source_refs", "[]")
        try:
            refs = _json.loads(refs_raw) if isinstance(refs_raw, str) else []
        except (_json.JSONDecodeError, TypeError):
            refs = []

        supersedes_raw = row.get("supersedes", "[]")
        try:
            supersedes = _json.loads(supersedes_raw) if isinstance(supersedes_raw, str) else []
        except (_json.JSONDecodeError, TypeError):
            supersedes = []

        meta_raw = row.get("metadata", "{}")
        try:
            metadata = _json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except (_json.JSONDecodeError, TypeError):
            metadata = {}

        media_raw = row.get("media_refs", "[]")
        try:
            media_refs = _json.loads(media_raw) if isinstance(media_raw, str) else []
        except (_json.JSONDecodeError, TypeError):
            media_refs = []

        return {
            "memory_id": row.get("memory_id", ""),
            "project_id": row.get("project_id", ""),
            "state_type": row.get("state_type", ""),
            "key": row.get("key", ""),
            "current_value": row.get("current_value", ""),
            "rationale": row.get("rationale", ""),
            "owner": row.get("owner"),
            "status": row.get("status", "active"),
            "confidence": float(row.get("confidence", 0.5)),
            "version": int(row.get("version", 1)),
            "supersedes": supersedes,
            "updated_at": row.get("updated_at", ""),
            "valid_from": row.get("valid_from", ""),
            "valid_to": row.get("valid_to"),
            "recorded_at": row.get("recorded_at", ""),
            "decision_strength": row.get("decision_strength", ""),
            "review_status": row.get("review_status", ""),
            "metadata": metadata,
            "status_reason": row.get("status_reason", ""),
            "status_changed_at": row.get("status_changed_at", ""),
            "status_changed_by": row.get("status_changed_by", ""),
            "source_refs": refs,
            "media_refs": media_refs,
        }

    @staticmethod
    def _insert_memory_record(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO memory_records
               (memory_id, project_id, state_type, key, current_value, rationale,
                owner, status, confidence, version, supersedes, updated_at,
                valid_from, valid_to, recorded_at, decision_strength, review_status,
                metadata, status_reason, status_changed_at, status_changed_by,
                source_refs, media_refs, record_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["memory_id"], row["project_id"], row["state_type"], row["key"],
                row["current_value"], row["rationale"], row["owner"], row["status"],
                row["confidence"], row["version"], row["supersedes"], row["updated_at"],
                row["valid_from"], row["valid_to"], row["recorded_at"],
                row["decision_strength"], row["review_status"],
                row["metadata"], row["status_reason"], row["status_changed_at"],
                row["status_changed_by"], row["source_refs"], row["media_refs"],
                row.get("record_type", "active"),
            ),
        )


def _tokenize_query(query: str) -> list[str]:
    """拆分查询为搜索词元：中文按单字+双字组合，英文按空格。"""
    tokens = []
    # 英文/数字词
    for word in re.findall(r"[a-zA-Z0-9_]+", query):
        if len(word) >= 2:
            tokens.append(word)
    # 中文：取连续中文段
    for segment in re.findall(r"[一-鿿]+", query):
        if len(segment) <= 4:
            tokens.append(segment)
        else:
            # 长中文段：取双字组合
            for i in range(len(segment) - 1):
                tokens.append(segment[i:i + 2])
    return tokens[:5]


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
