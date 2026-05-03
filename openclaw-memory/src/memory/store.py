"""Local JSON/JSONL storage for raw events and current memory state.

V1.5 改进：
- 三层去重：ID + Hash + Semantic 相似度
- 语义相似度计算：基于字符 n-gram 的 Jaccard 相似度
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from memory.schema import MemoryItem, raw_event_id, utc_now_iso


class MemoryStore:
    """Persist raw events and structured memory state in local files."""

    def __init__(self, data_dir: str | Path) -> None:
        """Create a store rooted at data_dir."""
        self.data_dir = Path(data_dir)
        self.raw_events_path = self.data_dir / "raw_events.jsonl"
        self.memory_state_path = self.data_dir / "memory_state.json"
        self.audit_path = self.data_dir / "audit.jsonl"

    # ── V1.12 审计日志 ──────────────────────────────────────────

    def audit_log(self, operator_id: str, operation: str,
                  project_id: str = "", state_type: str = "",
                  detail: str = "") -> None:
        """V1.12: 记录操作审计日志 (AUTH-9)。

        每条日志为 JSONL 一行，记录操作者、操作类型、时间等。
        """
        import json as _json
        entry = {
            "timestamp": self.data_dir and str(self.data_dir) or "",
            "operator_id": operator_id,
            "operation": operation,       # read / write / delete / search
            "project_id": project_id,
            "state_type": state_type,
            "detail": detail[:200],
        }
        # 用实际时间替代
        from datetime import datetime, timezone
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

    def read_audit_log(self, limit: int = 100) -> list[dict]:
        """V1.12: 读取最近的审计日志。"""
        if not self.audit_path.exists():
            return []
        entries = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    import json as _json
                    entries.append(_json.loads(line))
                except Exception:
                    pass
        return entries[-limit:]

    def ensure_files(self) -> None:
        """Create data files when they do not already exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.raw_events_path.exists():
            self.raw_events_path.write_text("", encoding="utf-8")
        if not self.memory_state_path.exists():
            self.save_state([], [], [])

    def append_raw_events(self, events: Iterable[dict[str, Any]]) -> int:
        """Append new raw events and return the number written."""
        self.ensure_files()
        existing_ids = {raw_event_id(event) for event in self.read_raw_events()}
        written = 0
        with self.raw_events_path.open("a", encoding="utf-8") as handle:
            for event in events:
                event_id = raw_event_id(event)
                if event_id in existing_ids:
                    continue
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                existing_ids.add(event_id)
                written += 1
        return written

    def read_raw_events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Read raw events, optionally filtering by project_id."""
        if not self.raw_events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.raw_events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if project_id is None or event.get("project_id") == project_id:
                events.append(event)
        return events

    def load_state(self) -> dict[str, Any]:
        """Load memory_state.json and return the decoded state object."""
        self.ensure_files()
        return json.loads(self.memory_state_path.read_text(encoding="utf-8"))

    def save_state(
        self,
        items: list[MemoryItem],
        history: list[MemoryItem],
        processed_event_ids: list[str],
    ) -> None:
        """Persist active items, historical items, and processed event ids."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [item.to_dict() for item in items],
            "history": [item.to_dict() for item in history],
            "processed_event_ids": sorted(set(processed_event_ids)),
            "updated_at": utc_now_iso(),
        }
        self.memory_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def search_keywords(
        self,
        query: str,
        project_id: str | None = None,
        as_of: str | None = None,
        top_k: int = 10,
    ) -> list[tuple[MemoryItem, float]]:
        """V1.9: 基于关键词的记忆搜索，返回匹配项列表（按相关度降序）。

        在当前 active items 中搜索 query 中包含的关键词，
        匹配字段：current_value（权重 2）、rationale（权重 1）、source_refs 摘要（权重 1）。
        中文按字符搜索，英文按空格分词搜索，大小写不敏感。

        这是语义搜索的前驱版本。后续可升级为向量搜索 + 混合融合。

        Args:
            query: 搜索关键词（如 "API 文档"、"张三 阻塞"）
            project_id: 可选的项目 ID 过滤
            as_of: 可选的时间点过滤
            top_k: 返回的最大结果数，默认 10

        Returns:
            (MemoryItem, score) 元组列表，按 score 降序排列。
            score 为正整数，越高表示匹配越充分。
        """
        items = self.list_items(project_id, as_of=as_of)
        if not query.strip():
            return [(item, 0.0) for item in items[:top_k]]

        # 分词：中文按字符 + 英文按空格
        # 将中英文混合文本拆分为独立的搜索词
        tokens = self._tokenize_query(query)

        scored: list[tuple[MemoryItem, float, str]] = []
        for item in items:
            score = 0.0
            matched_fields: list[str] = []

            for token in tokens:
                # current_value 匹配（权重 2）
                if token in item.current_value.lower():
                    score += 2.0
                    if "current_value" not in matched_fields:
                        matched_fields.append("current_value")

                # rationale 匹配（权重 1）
                if token in item.rationale.lower():
                    score += 1.0
                    if "rationale" not in matched_fields:
                        matched_fields.append("rationale")

                # source_refs 摘要匹配（权重 1）
                for ref in item.source_refs:
                    if token in ref.excerpt.lower():
                        score += 1.0
                        if "source_refs" not in matched_fields:
                            matched_fields.append("source_refs")

            if score > 0:
                scored.append((item, score, "; ".join(matched_fields)))

        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(item, s) for item, s, _ in scored[:top_k]]

    def search_advanced(
        self,
        project_id: str | None = None,
        state_type: str | None = None,
        keyword: str | None = None,
        owner: str | None = None,
        message_id: str | None = None,
        as_of: str | None = None,
        top_k: int = 20,
    ) -> list[tuple[MemoryItem, float]]:
        """V1.12: 多条件组合搜索。

        所有条件 AND 组合。不传的条件不过滤。
        关键词匹配 current_value/rationale/source_refs.excerpt。
        """
        items = self.list_items(project_id, as_of=as_of)

        # 按 message_id 过滤
        if message_id:
            items = [
                item for item in items
                if any(ref.message_id == message_id for ref in item.source_refs)
            ]

        # 按 state_type 过滤
        if state_type:
            items = [item for item in items if item.state_type == state_type]

        # 按 owner 过滤
        if owner:
            items = [item for item in items if item.owner and owner in item.owner]

        # 关键词评分
        if keyword and keyword.strip():
            tokens = self._tokenize_query(keyword)
            scored: list[tuple[MemoryItem, float]] = []
            for item in items:
                score = 0.0
                for token in tokens:
                    if token in item.current_value.lower():
                        score += 2.0
                    if token in item.rationale.lower():
                        score += 1.0
                    for ref in item.source_refs:
                        if token in ref.excerpt.lower():
                            score += 1.0
                            break
                if score > 0:
                    scored.append((item, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:top_k]

        # 无关键词时按更新时间倒序
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return [(item, 1.0) for item in items[:top_k]]

    def search_hybrid(
        self,
        query: str,
        project_id: str | None = None,
        vector_store: Any = None,
        top_k: int = 10,
        keyword_weight: float = 0.7,
        as_of: str | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        """Hybrid search: keyword scoring + vector semantic reranking via RRF.

        Combines keyword search (precise, deterministic) with vector search
        (semantic understanding). Uses Reciprocal Rank Fusion to merge results.

        Args:
            query: Search query string.
            project_id: Optional project filter.
            vector_store: VectorStore instance (None = keyword-only fallback).
            top_k: Max results to return.
            keyword_weight: Weight for keyword scores (0-1). Vector weight = 1 - keyword_weight.
            as_of: Optional temporal filter.

        Returns:
            (MemoryItem, fused_score) tuples, descending by score.
        """
        kw_results = self.search_keywords(query, project_id, as_of=as_of, top_k=top_k * 3)

        if vector_store is None or not getattr(vector_store, "available", False):
            return kw_results[:top_k]

        vec_results = vector_store.search(query, project_id=project_id, top_k=top_k * 3)

        if not vec_results:
            return kw_results[:top_k]

        items_by_id = {}
        for item, _score in kw_results:
            items_by_id[item.memory_id] = item

        all_items = self.list_items(project_id, as_of=as_of)
        for item in all_items:
            if item.memory_id not in items_by_id:
                items_by_id[item.memory_id] = item

        k_rrf = 60
        fused_scores: dict[str, float] = {}

        for rank, (item, _score) in enumerate(kw_results):
            rrf = 1.0 / (k_rrf + rank + 1)
            fused_scores[item.memory_id] = fused_scores.get(item.memory_id, 0) + keyword_weight * rrf

        vector_weight = 1.0 - keyword_weight
        for rank, (memory_id, _sim) in enumerate(vec_results):
            rrf = 1.0 / (k_rrf + rank + 1)
            fused_scores[memory_id] = fused_scores.get(memory_id, 0) + vector_weight * rrf

        ranked = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for memory_id, score in ranked[:top_k]:
            if memory_id in items_by_id:
                results.append((items_by_id[memory_id], score))

        return results

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        """V1.9: 将搜索查询拆分为独立的搜索词。

        中文按字符拆分（"API 文档" → ["api", "文", "档"]），
        英文按空格分词后转小写。
        去重。
        """
        import re
        tokens: list[str] = []
        # 按空格拆分
        for part in query.split():
            part = part.strip().lower()
            if not part:
                continue
            # 检测是否纯英文
            if re.match(r'^[a-zA-Z0-9_\-\.]+$', part):
                tokens.append(part)
            else:
                # 中英文混合：英文部分整体添加，中文部分逐字
                segments = re.findall(r'[a-zA-Z0-9_\-\.]+|[^a-zA-Z0-9_\-\.]', part)
                for seg in segments:
                    if re.match(r'^[a-zA-Z0-9_\-\.]+$', seg):
                        tokens.append(seg.lower())
                    else:
                        # 中文逐字
                        for ch in seg:
                            if ch.strip():
                                tokens.append(ch)
        # 去重但保持顺序
        seen = set()
        deduped = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped

    def list_items(self, project_id: str | None = None,
                   as_of: str | None = None,
                   user_id: str | None = None) -> list[MemoryItem]:
        """Return active memory items, optionally filtered.

        V1.12: 增加 user_id 参数，按用户身份过滤记忆（AUTH-4）。
        过滤逻辑：记忆的 source_refs 中 sender_id 匹配 user_id 的返回。
        user_id=None 时不过滤（兼容旧行为）。
        """
        state = self.load_state()
        items = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        if as_of is not None:
            items = self._filter_as_of(items, as_of)
        if user_id is not None:
            items = [
                item for item in items
                if any(ref.sender_id == user_id for ref in item.source_refs)
            ]
        return items

    @staticmethod
    def _parse_iso_as_utc(t: str) -> datetime | None:
        """解析 ISO 时间字符串，统一返回 UTC naive datetime。

        支持：带时区偏移的、Z 后缀的、无时区的。
        时区偏移会被转化为 UTC 时间。
        """
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                # V1.12 FIX-1: 使用 datetime.timezone.utc 替代 fromisoformat("+00:00")
                # 后者在 Python<3.11 可能失败，静默回退到字符串比较
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            return None

    def _compare_iso_time(self, t1: str, t2: str) -> int:
        """比较两个 ISO 时间字符串，返回 -1/0/1。

        先统一转为 UTC naive datetime 再比较，
        解析失败时 fallback 字符串比较。
        """
        dt1 = self._parse_iso_as_utc(t1)
        dt2 = self._parse_iso_as_utc(t2)
        if dt1 is not None and dt2 is not None:
            if dt1 < dt2:
                return -1
            if dt1 > dt2:
                return 1
            return 0
        # Fallback 字典序
        if t1 < t2:
            return -1
        if t1 > t2:
            return 1
        return 0

    def _filter_as_of(self, items: list[MemoryItem], as_of: str) -> list[MemoryItem]:
        """过滤出在 as_of 时刻有效的记忆。

        有效条件：valid_from ≤ as_of < valid_to（当 valid_to 存在时）
        旧数据 valid_from="" 视为始终有效（无条件通过）。
        使用 datetime 解析比较，支持跨时区。
        """
        filtered = []
        for item in items:
            # 旧数据兼容：valid_from="" 视为始终有效
            if item.valid_from and self._compare_iso_time(item.valid_from, as_of) > 0:
                continue
            if item.valid_to is not None and self._compare_iso_time(item.valid_to, as_of) <= 0:
                continue
            filtered.append(item)
        return filtered

    def list_history(self, project_id: str | None = None) -> list[MemoryItem]:
        """Return historical superseded memory items.

        V1.12: 支持按 project_id 过滤。
        """
        state = self.load_state()
        items = [MemoryItem.from_dict(item) for item in state.get("history", [])]
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        return items

    def find_items_by_message_id(self, message_id: str) -> list[MemoryItem]:
        """V1.12: 查找所有引用了指定消息的活跃+历史记忆项。

        用于证据链追溯：从一条消息反查它产生了哪些 MemoryItem。
        """
        state = self.load_state()
        active = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        history = [MemoryItem.from_dict(item) for item in state.get("history", [])]
        results = []
        for item in active + history:
            if any(ref.message_id == message_id for ref in item.source_refs):
                results.append(item)
        return results

    def build_inverted_index(self) -> InvertedIndex:
        """V1.12: 构建全文倒排索引。

        索引 raw_events + active items + history items 的所有文本字段。
        返回 InvertedIndex 实例，可复用于多次搜索。
        """
        idx = InvertedIndex()
        events = self.read_raw_events()
        idx.index_events(events)

        state = self.load_state()
        active = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        history_items = [MemoryItem.from_dict(item) for item in state.get("history", [])]
        idx.index_items(active + history_items)

        return idx

    def processed_event_ids(self) -> list[str]:
        """Return event ids that have already been processed into memory."""
        state = self.load_state()
        return list(state.get("processed_event_ids", []))

    def mark_processed(self, event_ids: Iterable[str]) -> None:
        """Persist additional processed event ids without changing memory items."""
        state = self.load_state()
        items = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        history = [MemoryItem.from_dict(item) for item in state.get("history", [])]
        processed = list(state.get("processed_event_ids", [])) + list(event_ids)
        self.save_state(items, history, processed)

    def upsert_items(self, new_items: Iterable[MemoryItem], processed_ids: Iterable[str] = ()) -> list[MemoryItem]:
        """Insert or supersede active memory items with 3-layer deduplication.

        V1.5 改进：三层去重架构
        1. Layer 1: Identity Key 去重（project_id:state_type:key 相同视为同一记忆）
        2. Layer 2: Content Hash 去重（内容完全相同则跳过）
        3. Layer 3: Semantic Similarity 去重（语义高度相似则合并 source_refs）

        Args:
            new_items: 新的记忆项列表
            processed_ids: 已处理的事件 ID 列表

        Returns:
            当前活跃的记忆项列表
        """
        state = self.load_state()
        items = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        history = [MemoryItem.from_dict(item) for item in state.get("history", [])]
        processed = list(state.get("processed_event_ids", [])) + list(processed_ids)

        # 按 identity_key 建立索引，用于快速查找
        by_key = {item.identity_key(): item for item in items}

        for new_item in new_items:
            old_item = by_key.get(new_item.identity_key())

            if old_item:
                # === Layer 1: Identity Key 去重 ===
                # identity_key 相同，可能是同一记忆的更新或重复

                # === Layer 2: Content Hash 去重 ===
                # 计算内容哈希，判断是否完全相同
                old_hash = hashlib.sha1(old_item.current_value.encode("utf-8")).hexdigest()
                new_hash = hashlib.sha1(new_item.current_value.encode("utf-8")).hexdigest()

                # V1.6：确保新 item 的 valid_from 有值，优先从 source_refs 取
                if not new_item.valid_from and new_item.source_refs:
                    new_item.valid_from = new_item.source_refs[0].created_at
                if not new_item.valid_from:
                    new_item.valid_from = utc_now_iso()

                if old_hash == new_hash:
                    # 内容完全相同，跳过插入，但合并 source_refs
                    # P0 修复：合并时按 message_id 去重，避免重复证据
                    existing_ids = {ref.message_id for ref in old_item.source_refs}
                    for ref in new_item.source_refs:
                        if ref.message_id not in existing_ids:
                            old_item.source_refs.append(ref)
                            existing_ids.add(ref.message_id)
                    old_item.confidence = max(old_item.confidence, new_item.confidence)
                    continue

                # === Layer 3: Semantic Similarity 去重 ===
                # 计算语义相似度，判断是否高度相似
                similarity = self._compute_text_similarity(old_item.current_value, new_item.current_value)

                if similarity > 0.9:
                    # P0 修复：否定词极性检查
                    # "张三负责" 和 "张三不负责" 字符bigram相似度高，但语义相反
                    if self._has_negation_polarity_change(old_item.current_value, new_item.current_value):
                        # V1.6: 标记旧项失效时间
                        if old_item.valid_to is None:
                            old_item.valid_to = utc_now_iso()
                        history.append(old_item)
                        new_item.version = old_item.version + 1
                        new_item.supersedes = [*old_item.supersedes, old_item.memory_id]
                        items = [item for item in items if item.memory_id != old_item.memory_id]
                        by_key[new_item.identity_key()] = new_item
                        items.append(new_item)
                        continue

                    # V1.6：关键字段保护检查
                    # owner 或 status 变化 → 不应 semantic merge，应 supersede
                    if old_item.owner != new_item.owner or old_item.status != new_item.status:
                        if old_item.valid_to is None:
                            old_item.valid_to = utc_now_iso()
                        history.append(old_item)
                        new_item.version = old_item.version + 1
                        new_item.supersedes = [*old_item.supersedes, old_item.memory_id]
                        items = [item for item in items if item.memory_id != old_item.memory_id]
                        by_key[new_item.identity_key()] = new_item
                        items.append(new_item)
                        continue

                    # 语义高度相似（>90%）且否定极性一致，关键字段未变化，视为同一记忆的不同表述
                    # 合并 source_refs，提升置信度
                    old_item.source_refs.extend(new_item.source_refs)
                    old_item.confidence = max(old_item.confidence, new_item.confidence)
                    continue

                # 内容不同且相似度不高，视为记忆的更新/推翻
                # 将旧记忆移入历史，新记忆标记为 supersede
                # V1.6: 标记旧项失效时间
                if old_item.valid_to is None:
                    old_item.valid_to = utc_now_iso()
                history.append(old_item)
                new_item.version = old_item.version + 1
                new_item.supersedes = [*old_item.supersedes, old_item.memory_id]
                items = [item for item in items if item.memory_id != old_item.memory_id]

            # V1.11 Layer 4: 跨 key 决策/截止日期覆盖
            # 不同 key 但同主题 → 覆盖旧条目
            if old_item is None and new_item.state_type in ("decision", "deadline"):
                for existing in list(items):
                    if existing.state_type != new_item.state_type:
                        continue
                    if existing.project_id != new_item.project_id:
                        continue
                    if self._is_same_topic(
                        existing.current_value, new_item.current_value,
                        new_item.state_type,
                    ):
                        if existing.valid_to is None:
                            existing.valid_to = utc_now_iso()
                        history.append(existing)
                        new_item.version = existing.version + 1
                        new_item.supersedes = [*existing.supersedes, existing.memory_id]
                        items = [item for item in items if item.memory_id != existing.memory_id]
                        old_key = existing.identity_key()
                        if by_key.get(old_key) is existing:
                            del by_key[old_key]

            # 插入新记忆
            by_key[new_item.identity_key()] = new_item
            items.append(new_item)

        self.save_state(items, history, processed)
        return items

    # 中文否定词集合，用于检测语义极性变化
    _NEGATION_WORDS = frozenset(["不", "没", "别", "勿", "未", "无", "否", "莫", "休", "甭"])
    # V1.6：常见中文误报豁免词。这些词含否定字但整体是肯定/中性表达。
    # 当两段文本的否定词命中全部来自豁免词时，不判为极性变化。
    _NEGATION_SAFE_WORDS = frozenset([
        "不管", "不错", "不得不", "没问题", "没关系", "没事",
        "不用担心", "不少", "不错过", "少不了", "说不定", "不仅", "不仅",
    ])

    def _has_negation_polarity_change(self, text1: str, text2: str) -> bool:
        """检测两段文本的否定极性是否发生变化。

        V1.6 修复：增加常见中文误报豁免词，防止"不管""不错"等误判。
        算法：
        1. 计算每段文本的"有效否定词数"（非豁免词中的否定字）
        2. 有效否定 > 0 的文本被判为否定极性

        Args:
            text1: 旧文本
            text2: 新文本

        Returns:
            True 如果否定极性发生变化（一个有有效否定另一个没有）
        """
        def _has_effective_negation(text: str) -> bool:
            # 将文本中所有豁免词替换为空，剩下的是"裸露"的非豁免文本
            cleaned = text
            for safe_word in self._NEGATION_SAFE_WORDS:
                cleaned = cleaned.replace(safe_word, "")
            # 检查 cleaned 中是否还有否定词
            return any(w in cleaned for w in self._NEGATION_WORDS)

        has_neg1 = _has_effective_negation(text1)
        has_neg2 = _has_effective_negation(text2)
        return has_neg1 != has_neg2

    @staticmethod
    def _compute_text_similarity(text1: str, text2: str) -> float:
        """Compute similarity between two texts using character n-gram Jaccard similarity.
        Made static for reuse in cross-key decision detection."""
        def get_char_bigrams(text: str) -> set[str]:
            text = text.replace(" ", "").lower()
            return {text[i : i + 2] for i in range(len(text) - 1) if i + 2 <= len(text)}
        bigrams1 = get_char_bigrams(text1)
        bigrams2 = get_char_bigrams(text2)
        if not bigrams1 and not bigrams2:
            return 1.0
        if not bigrams1 or not bigrams2:
            return 0.0
        intersection = len(bigrams1 & bigrams2)
        union = len(bigrams1 | bigrams2)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _is_same_topic(text1: str, text2: str, state_type: str = "decision") -> bool:
        """V1.11: 判断两个同类型条目是否属于同一主题（应触发跨 key 覆盖）。

        支持 decision 和 deadline。
        """
        import re

        # 覆盖信号词
        override_signals = ("改为", "不再", "换成", "改成", "算了", "改用",
                           "延期", "改到", "调到", "提前", "推后")
        has_override = any(s in text2 for s in override_signals)

        # 对于 deadline，额外检查数字日期重叠
        if state_type == "deadline":
            # 提取日期关键词（周一~周日/数字日期）
            date_words = set(re.findall(
                r"(下?周[一二三四五六日天]|明天|后天|今天|下?个?月[初底]|\d+月\d+[日号]|\d+[/-]\d+)",
                text1 + text2,
            ))
            if not date_words:
                pass
            else:
                dates1 = set(re.findall(
                    r"(下?周[一二三四五六日天]|明天|后天|今天|下?个?月[初底]|\d+月\d+[日号]|\d+[/-]\d+)",
                    text1,
                ))
                dates2 = set(re.findall(
                    r"(下?周[一二三四五六日天]|明天|后天|今天|下?个?月[初底]|\d+月\d+[日号]|\d+[/-]\d+)",
                    text2,
                ))
                # 两条 deadline 都提到具体日期
                # 只有共享相同日期词或有覆盖信号才判为同一主题
                if dates1 and dates2:
                    if dates1 & dates2:  # 共享同一日期词 → 同一 DL
                        return True
                    if has_override:     # 有覆盖信号 → 同一 DL 被修改
                        return True
                    # 不同日期且无覆盖信号 → 不同 DL，不合并
                    return False

        # 提取有意义词汇
        def extract_keywords(t: str) -> set:
            words = set()
            for m in re.finditer(r"[一-鿿]{2,4}", t):
                w = m.group()
                if w not in ("采用", "使用", "改为", "不再", "换成", "改成",
                             "决定", "决策", "确定", "作为", "替代", "前端",
                             "后端", "框架", "方案", "这个", "那个", "或者",
                             "延期", "改到", "调到", "截止", "DDL", "deadline",
                             "交付", "完成", "之前"):
                    words.add(w)
            for m in re.finditer(r"[A-Za-z]{3,}", t):
                words.add(m.group().lower())
            return words

        words1 = extract_keywords(text1)
        words2 = extract_keywords(text2)
        shared = words1 & words2

        bigram_sim = MemoryStore._compute_text_similarity(text1, text2)

        if has_override and shared:
            return True
        if len(shared) >= 2:
            return True
        if bigram_sim > 0.4:
            return True
        return False


# ── V1.12 全文倒排索引 ──────────────────────────────────────────

class InvertedIndex:
    """轻量级全文倒排索引，用于加速 token 级检索。

    不依赖任何外部库。索引 raw_events.jsonl + memory_state.json 中的文本字段。
    中文按字分词，英文按空格/标点分词。大小写不敏感。
    """

    def __init__(self) -> None:
        self._index: dict[str, set[str]] = {}  # token → {message_id, ...}

    def index_events(self, events: list[dict]) -> int:
        """索引 raw events 的文本字段。

        Returns:
            新增的 token 数。
        """
        added = 0
        for event in events:
            msg_id = str(event.get("message_id", ""))
            if not msg_id:
                continue
            text = str(event.get("text", event.get("content", "")))
            tokens = self._tokenize(text)
            for token in tokens:
                if token not in self._index:
                    self._index[token] = set()
                    added += 1
                self._index[token].add(msg_id)
        return added

    def index_items(self, items: list) -> int:
        """索引 MemoryItem 的文本字段。

        Args:
            items: MemoryItem 列表。
        Returns:
            新增的 token 数。
        """
        added = 0
        for item in items:
            texts = [item.current_value, item.rationale]
            for ref in item.source_refs:
                texts.append(ref.excerpt)
            for text in texts:
                tokens = self._tokenize(text)
                for token in tokens:
                    key = f"mem:{token}"
                    if key not in self._index:
                        self._index[key] = set()
                        added += 1
                    self._index[key].add(item.memory_id)
        return added

    def search(self, query: str, max_results: int = 50) -> list[str]:
        """搜索匹配 token 的 message_id 列表。

        Returns:
            按匹配 token 数降序排列的 message_id 列表。
        """
        tokens = self._tokenize(query)
        if not tokens:
            return []
        scores: dict[str, int] = {}
        for token in tokens:
            ids = self._index.get(token, set())
            for mid in ids:
                scores[mid] = scores.get(mid, 0) + 1
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [mid for mid, _ in ranked[:max_results]]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：中文逐字，英文按空格/标点分词。"""
        import re
        text = text.lower().strip()
        tokens: list[str] = []
        # 提取英文词
        for m in re.finditer(r"[a-z0-9_\-\.]{2,}", text):
            tokens.append(m.group())
        # 逐字提取中文
        for ch in text:
            if '一' <= ch <= '鿿':
                tokens.append(ch)
        return tokens
