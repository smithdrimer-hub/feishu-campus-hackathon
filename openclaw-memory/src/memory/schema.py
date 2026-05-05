"""Dataclasses for structured Memory state and source evidence."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string without timezone suffix.

    使用 UTC naive 格式（如 '2026-04-28T03:52:56.466417'），避免跨时区比较问题。
    所有内部时间字段（valid_from/valid_to/recorded_at）统一使用此格式。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


@dataclass
class SourceRef:
    """Evidence anchor pointing back to the original Feishu message.

    V1.12: 新增 sender_name/sender_id/source_url 完善证据链。
    """

    type: str
    chat_id: str
    message_id: str
    excerpt: str
    created_at: str
    sender_name: str = ""
    sender_id: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize this source reference into a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        """Create a SourceRef from a JSON-compatible dict.
        V1.12: 向后兼容旧数据（缺失字段默认为空字符串）。
        """
        return cls(
            type=str(data.get("type", "message")),
            chat_id=str(data.get("chat_id", "")),
            message_id=str(data.get("message_id", "")),
            excerpt=str(data.get("excerpt", "")),
            created_at=str(data.get("created_at", "")),
            sender_name=str(data.get("sender_name", "")),
            sender_id=str(data.get("sender_id", "")),
            source_url=str(data.get("source_url", "")),
        )


@dataclass
class MemoryItem:
    """Current collaboration state item with evidence and version metadata."""

    project_id: str
    state_type: str
    key: str
    current_value: str
    rationale: str
    owner: str | None
    status: str
    confidence: float
    source_refs: list[SourceRef]
    version: int = 1
    supersedes: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)
    memory_id: str = field(default_factory=lambda: f"mem_{uuid4().hex}")
    # V1.6：bi-temporal 字段
    # valid_from: 业务上从什么时候开始成立，优先来自 source event created_at
    # valid_to: 业务上什么时候失效，active item 为 None
    # recorded_at: 系统何时抽取/写入
    valid_from: str = ""      # 空字符串兼容旧数据
    valid_to: str | None = None
    recorded_at: str = field(default_factory=utc_now_iso)
    # V1.15 P0: 可信度字段
    decision_strength: str = ""  # discussion | preference | tentative | confirmed
    review_status: str = ""      # auto_approved | needs_review | approved | rejected
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展字段（blocker_status 等）

    def identity_key(self) -> str:
        """Return the stable key used to decide whether this item supersedes another."""
        return f"{self.project_id}:{self.state_type}:{self.key}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize this memory item into a JSON-compatible dict."""
        data = asdict(self)
        data["source_refs"] = [ref.to_dict() for ref in self.source_refs]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
        """Create a MemoryItem from a JSON-compatible dict.
        V1.6: 旧数据缺少 valid_from/valid_to/recorded_at 时兼容加载。
        """
        refs = [SourceRef.from_dict(ref) for ref in data.get("source_refs", [])]
        return cls(
            project_id=str(data.get("project_id", "")),
            state_type=str(data.get("state_type", "")),
            key=str(data.get("key", "")),
            current_value=str(data.get("current_value", "")),
            rationale=str(data.get("rationale", "")),
            owner=data.get("owner"),
            status=str(data.get("status", "active")),
            confidence=float(data.get("confidence", 0.5)),
            source_refs=refs,
            version=int(data.get("version", 1)),
            supersedes=list(data.get("supersedes", [])),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            memory_id=str(data.get("memory_id", f"mem_{uuid4().hex}")),
            # V1.6: 旧数据兼容
            valid_from=str(data.get("valid_from", "")),
            valid_to=data.get("valid_to"),
            recorded_at=str(data.get("recorded_at", utc_now_iso())),
            decision_strength=str(data.get("decision_strength", "")),
            review_status=str(data.get("review_status", "")),
            metadata=dict(data.get("metadata", {}) or {}),
        )


def source_ref_from_event(event: dict[str, Any], excerpt: str | None = None) -> SourceRef:
    """Build a SourceRef from a normalized raw event dict.

    V1.12: 保留 sender 信息和 source_url，完善证据链。
    """
    text = excerpt if excerpt is not None else str(event.get("text", event.get("content", "")))
    chat_id = str(event.get("chat_id", ""))
    message_id = str(event.get("message_id", ""))
    sender = event.get("sender", {}) or {}

    # 构建来源链接（V1.12: 区分消息和文档 URL）
    source_url = ""
    source_type = str(event.get("source_type", "message"))
    if source_type == "doc":
        # 文档链接：从 message_id 中提取 doc_id（格式: doc_{doc_id}_{hash}）
        doc_id_raw = str(event.get("message_id", ""))
        if doc_id_raw.startswith("doc_"):
            parts = doc_id_raw.split("_", 1)
            if len(parts) > 1:
                doc_token = parts[1].rsplit("_", 1)[0]
                source_url = f"https://www.feishu.cn/docx/{doc_token}"
    elif chat_id and message_id:
        source_url = f"https://app.feishu.cn/client/messages/{chat_id}/{message_id}"

    return SourceRef(
        type=str(event.get("source_type", "message")),
        chat_id=chat_id,
        message_id=message_id,
        excerpt=text[:240],
        created_at=str(event.get("created_at", "")),
        sender_name=str(sender.get("name", sender.get("id", ""))),
        sender_id=str(sender.get("id", "")),
        source_url=source_url,
    )


def source_ref_from_doc(doc_data: dict[str, Any], excerpt: str | None = None) -> SourceRef:
    """Build a SourceRef from a doc fetch result.

    V1.8: 文档数据源，SourceRef.type 为 "doc"。
    """
    text = excerpt if excerpt is not None else str(doc_data.get("title", ""))
    return SourceRef(
        type="doc",
        chat_id="",
        message_id=str(doc_data.get("doc_id", "")),
        excerpt=text[:240],
        created_at=str(doc_data.get("created_at", utc_now_iso())),
    )


def source_ref_from_task(task_data: dict[str, Any], excerpt: str | None = None) -> SourceRef:
    """Build a SourceRef from a task fetch result.

    V1.8: 任务数据源，SourceRef.type 为 "task"。
    """
    text = excerpt if excerpt is not None else str(task_data.get("summary", ""))
    return SourceRef(
        type="task",
        chat_id="",
        message_id=str(task_data.get("guid", "")),
        excerpt=text[:240],
        created_at=str(task_data.get("created_at", utc_now_iso())),
    )


def raw_event_id(event: dict[str, Any]) -> str:
    """Return the stable event id used for de-duplication."""
    message_id = str(event.get("message_id", ""))
    if message_id:
        return message_id
    digest = hashlib.sha1(str(sorted(event.items())).encode("utf-8")).hexdigest()[:16]
    return f"event_{digest}"
