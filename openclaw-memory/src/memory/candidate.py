"""Validated LLM candidate schema for trusted Memory extraction.

V1.12 FIX-11: excerpt 验证从子串匹配升级为 difflib 模糊匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from memory.schema import MemoryItem, SourceRef


class CandidateValidationError(ValueError):
    """Raised when an LLM candidate does not satisfy the trusted schema."""


@dataclass
class MemoryCandidate:
    """Structured candidate emitted by an LLM before conversion to MemoryItem."""

    project_id: str
    state_type: str
    key: str
    current_value: str
    rationale: str
    owner: str | None
    status: str
    confidence: float
    source_refs: list[SourceRef]
    detected_at: str


def validate_candidate_dict(
    data: dict[str, Any],
    valid_message_ids: Iterable[str],
    event_map: dict[str, dict] | None = None,
) -> MemoryCandidate:
    """Validate one candidate dict and return a MemoryCandidate.

    V1.12: 支持 event_map 用于 excerpt 原文验证。
    """
    required = {
        "project_id",
        "state_type",
        "key",
        "current_value",
        "rationale",
        "owner",
        "status",
        "confidence",
        "source_refs",
        "detected_at",
    }
    missing = sorted(required - set(data))
    if missing:
        raise CandidateValidationError(f"candidate missing fields: {', '.join(missing)}")

    confidence = _validate_confidence(data["confidence"])
    source_refs = _validate_source_refs(
        data["source_refs"], set(valid_message_ids), event_map,
    )

    return MemoryCandidate(
        project_id=_required_str(data, "project_id"),
        state_type=_required_str(data, "state_type"),
        key=_required_str(data, "key"),
        current_value=_required_str(data, "current_value"),
        rationale=_required_str(data, "rationale"),
        owner=_optional_str(data["owner"]),
        status=_required_str(data, "status"),
        confidence=confidence,
        source_refs=source_refs,
        detected_at=_required_str(data, "detected_at"),
    )


def candidate_to_memory_item(candidate: MemoryCandidate) -> MemoryItem:
    """Convert a validated MemoryCandidate into a MemoryItem.

    ADD-only 策略（借鉴 mem0）：
    LLM 只做 ADD 提取，不做 UPDATE/DELETE 判断。
    如果 LLM 输出了 status="superseded"，系统强制改为 "active"，
    由下游 upsert_items() 的三层去重逻辑处理版本冲突和 supersede。
    """
    # ADD-only: LLM 输出的 superseded 由系统去重层处理，不直接使用
    status = candidate.status
    if status == "superseded":
        status = "active"

    return MemoryItem(
        project_id=candidate.project_id,
        state_type=candidate.state_type,
        key=candidate.key,
        current_value=candidate.current_value,
        rationale=candidate.rationale,
        owner=candidate.owner,
        status=status,
        confidence=candidate.confidence,
        source_refs=candidate.source_refs,
        updated_at=candidate.detected_at,
    )


def _required_str(data: dict[str, Any], field: str) -> str:
    """Return a non-empty string field or raise a validation error."""
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CandidateValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    """Return an optional string value or raise a validation error."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise CandidateValidationError("owner must be a string or null")
    return value.strip() or None


def _validate_confidence(value: Any) -> float:
    """Return a confidence float in [0, 1] or raise a validation error."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CandidateValidationError("confidence must be a number")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise CandidateValidationError("confidence must be between 0 and 1")
    return confidence


def _validate_source_refs(
    value: Any,
    valid_message_ids: set[str],
    event_map: dict[str, dict] | None = None,
) -> list[SourceRef]:
    """Validate source_refs and return SourceRef objects.

    V1.12: 新增 excerpt 原文验证——如果 LLM 返回的 excerpt 不是原文子串，
    用原文前 240 字符替代，防止 LLM 虚构证据。
    """
    if not isinstance(value, list) or not value:
        raise CandidateValidationError("source_refs must be a non-empty list")
    refs = []
    for ref in value:
        if not isinstance(ref, dict):
            raise CandidateValidationError("each source_ref must be an object")
        message_id = _required_str(ref, "message_id")
        if message_id not in valid_message_ids:
            raise CandidateValidationError(f"source_ref message_id not found in raw events: {message_id}")

        excerpt = _required_str(ref, "excerpt")
        chat_id = _required_str(ref, "chat_id")
        created_at = _required_str(ref, "created_at")

        # V1.12 FIX-11: 验证 excerpt 是否来自原文（模糊匹配）
        source_type = str(ref.get("type", "message"))
        sender_name = ""
        sender_id = ""
        source_url = ""
        if event_map and message_id in event_map:
            ev = event_map[message_id]
            event_text = str(ev.get("text", ev.get("content", "")))
            if event_text and not _excerpt_matches(excerpt, event_text):
                # LLM 生成的 excerpt 与原文差异过大 → 用原文替代
                excerpt = event_text[:240]
            sender = ev.get("sender", {}) or {}
            sender_name = str(sender.get("name", sender.get("id", "")))
            sender_id = str(sender.get("id", ""))
            if chat_id and message_id:
                source_url = f"https://app.feishu.cn/client/messages/{chat_id}/{message_id}"

        refs.append(
            SourceRef(
                type=source_type,
                chat_id=chat_id,
                message_id=message_id,
                excerpt=excerpt,
                created_at=created_at,
                sender_name=sender_name,
                sender_id=sender_id,
                source_url=source_url,
            )
        )
    return refs


def _excerpt_matches(excerpt: str, source_text: str, threshold: float = 0.25) -> bool:
    """V1.12 FIX-11 real: 基于共享 token 的 excerpt 验证。

    提取中文词（2-4字）和英文词，计算共享 token 的 Jaccard 相似度。
    比字符级 SequenceMatcher 更鲁棒——"负责人改为张三" 和 "换成张三负责"
    共享 token {负责人, 张三, 负责/换成}，相似度 0.3+。

    Args:
        excerpt: LLM 返回的节选文本。
        source_text: 原始消息文本。
        threshold: token 级 Jaccard 相似度阈值（0-1），默认 0.25。

    Returns:
        True 如果 excerpt 与原文的共享 token 足够多。
    """
    if not excerpt or not source_text:
        return False
    if excerpt in source_text:
        return True

    tokens_e = _extract_tokens(excerpt)
    tokens_s = _extract_tokens(source_text)
    if not tokens_e or not tokens_s:
        return False

    shared = tokens_e & tokens_s
    # 共享 token 绝对数 ≥ 2 → 足以判定为原文派生
    if len(shared) >= 2:
        return True
    # 单个共享 token 且 excerpt 很短 → 也接受（短文本 token 少）
    if len(shared) >= 1 and len(tokens_e) <= 3:
        return True
    # Jaccard 兜底
    sim = len(shared) / len(tokens_e | tokens_s)
    return sim >= threshold


def _extract_tokens(text: str) -> set[str]:
    """V1.12: 从文本中提取有意义的 token。

    使用 2-3 字中文 bigram/trigram + 英文词。
    排除纯停用词 token。
    """
    import re
    _stop = frozenset({
        "这个", "那个", "或者", "以及", "但是", "不过", "然而",
        "因为", "所以", "如果", "虽然", "可以", "应该",
        "一个", "一些", "一下", "一种", "什么", "怎么", "为什么",
        "进行", "通过", "大家", "我们", "他们",
    })
    tokens: set[str] = set()
    # 中文 2-gram（固定 2 字，保证短 excerpt 和长原文的 token 能匹配）
    for m in re.finditer(r"[一-鿿]{2}", text):
        w = m.group()
        if w not in _stop:
            tokens.add(w)
    # 英文词 3+ 字母（V1.12 REAL-3: 同义词归一化）
    _synonyms = {
        "switch": "change", "migrate": "change", "move": "change",
        "adopt": "use", "utilize": "use", "employ": "use",
        "choose": "select", "pick": "select", "decide": "select",
        "postpone": "delay", "defer": "delay", "reschedule": "delay",
        "complete": "finish", "finalize": "finish", "wrap": "finish",
    }
    for m in re.finditer(r"[A-Za-z]{3,}", text):
        word = m.group().lower()
        tokens.add(_synonyms.get(word, word))
    return tokens
