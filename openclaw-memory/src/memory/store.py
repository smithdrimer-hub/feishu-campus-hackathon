"""Local JSON/JSONL storage for raw events and current memory state.

V1.5 改进：
- 三层去重：ID + Hash + Semantic 相似度
- 语义相似度计算：基于字符 n-gram 的 Jaccard 相似度
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
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
                   as_of: str | None = None) -> list[MemoryItem]:
        """Return active memory items, optionally filtered by project_id and/or as_of time.

        V1.6: 增加 as_of 参数，返回某一时间点有效的记忆。
        - as_of 为 None 时返回当前所有 active items（默认行为）
        - as_of 为 ISO 时间字符串时，返回 valid_from ≤ as_of < valid_to 的 item
        - 旧数据 valid_from="" 视为始终有效，在 as_of 查询中也被返回

        Args:
            project_id: 可选的项目 ID 过滤
            as_of: 可选的 ISO 时间字符串，返回该时刻有效的记忆

        Returns:
            符合条件的记忆项列表
        """
        state = self.load_state()
        items = [MemoryItem.from_dict(item) for item in state.get("items", [])]
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        if as_of is not None:
            items = self._filter_as_of(items, as_of)
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
                # 转为 UTC 并去掉 tzinfo，统一比较
                dt = dt.astimezone(datetime.fromisoformat("+00:00").tzinfo).replace(tzinfo=None)
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

    def list_history(self) -> list[MemoryItem]:
        """Return historical superseded memory items."""
        state = self.load_state()
        return [MemoryItem.from_dict(item) for item in state.get("history", [])]

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

            # 插入新记忆
            by_key[new_item.identity_key()] = new_item
            items.append(new_item)

        self.save_state(items, history, processed)
        return items

    def _compute_text_similarity(self, text1: str, text2: str) -> float:
        """Compute similarity between two texts using character n-gram Jaccard similarity.

        V1.5 新增：基于字符 n-gram 的 Jaccard 相似度计算。
        用于 Layer 3 语义去重，判断两段文本是否表达相同含义。

        算法说明：
        1. 将文本拆分为字符 bigram（连续 2 字符）集合
        2. 计算 Jaccard 相似度：|A ∩ B| / |A ∪ B|
        3. 返回 0-1 之间的相似度分数

        Args:
            text1: 第一段文本
            text2: 第二段文本

        Returns:
            相似度分数（0-1 之间，1 表示完全相同）
        """

        def get_char_bigrams(text: str) -> set[str]:
            """将文本拆分为字符 bigram 集合。

            Args:
                text: 输入文本

            Returns:
                bigram 集合
            """
            # 去除空格，统一为小写（对英文有效）
            text = text.replace(" ", "").lower()
            # 提取连续 2 字符
            return {text[i : i + 2] for i in range(len(text) - 1) if i + 2 <= len(text)}

        bigrams1 = get_char_bigrams(text1)
        bigrams2 = get_char_bigrams(text2)

        # 处理空集合情况
        if not bigrams1 and not bigrams2:
            return 1.0
        if not bigrams1 or not bigrams2:
            return 0.0

        # Jaccard 相似度：交集 / 并集
        intersection = len(bigrams1 & bigrams2)
        union = len(bigrams1 | bigrams2)
        return intersection / union if union > 0 else 0.0

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
