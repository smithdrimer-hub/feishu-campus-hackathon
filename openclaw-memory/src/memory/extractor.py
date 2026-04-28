"""Extractor implementations for collaboration state from raw messages.

V1.5 改进：
- Prompt Grounding：代词/时间/空间解析规则
- 飞书场景优化：@提及解析、飞书实体识别
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable

from memory.candidate import CandidateValidationError, candidate_to_memory_item, validate_candidate_dict
from memory.llm_provider import LLMProvider
from memory.schema import MemoryItem, source_ref_from_event


class BaseExtractor:
    """Interface for future rule-based or LLM-based memory extractors."""

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """Extract MemoryItems from raw events and return the new items."""
        raise NotImplementedError


class LLMExtractor(BaseExtractor):
    """Extract MemoryItems from strict JSON LLM output with rule fallback."""

    def __init__(self, provider: LLMProvider, fallback: BaseExtractor | None = None) -> None:
        """Create an LLM extractor with a provider and fallback extractor."""
        self.provider = provider
        self.fallback = fallback or RuleBasedExtractor()
        # V1.6: 记录被丢弃的 ambiguous 候选，供调试和分析
        self._dropped_candidates: list[dict] = []

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """Extract memory via LLM JSON; fall back to rules on parse or validation failure."""
        event_list = list(events)
        if not event_list:
            return []
        self._dropped_candidates = []
        author_map = self._build_author_map(event_list)
        time_ref = self._build_time_reference(event_list)
        prompt = self._build_prompt(event_list, author_map, time_ref)
        response = self.provider.generate(prompt)
        try:
            payload = self._parse_response(response)
            valid_message_ids = {str(event.get("message_id", "")) for event in event_list if event.get("message_id")}
            candidates = [
                validate_candidate_dict(candidate, valid_message_ids)
                for candidate in payload["candidates"]
            ]
            items = [candidate_to_memory_item(candidate) for candidate in candidates]
            # V1.6: 后处理——过滤 ambiguous + 低置信度候选
            items = self._filter_ambiguous(items, candidates)
            return items
        except (json.JSONDecodeError, KeyError, TypeError, CandidateValidationError):
            return self.fallback.extract(event_list)

    def _filter_ambiguous(self, items: list[MemoryItem], candidates: list) -> list[MemoryItem]:
        """过滤 ambiguous + 低置信度的候选。

        V1.6 新增：不依赖 LLM 自觉遵守 prompt 规则，
        在代码层做硬性过滤。如果 candidate 的 current_value 含 [ambiguous]
        且 confidence ≤ 0.3，则不写入正式记忆。
        被丢弃的候选记录在 _dropped_candidates 中，供调试用。
        """
        filtered = []
        for item, candidate in zip(items, candidates):
            is_ambiguous = "[ambiguous" in item.current_value
            if is_ambiguous and item.confidence <= 0.3:
                self._dropped_candidates.append({
                    "key": item.identity_key(),
                    "current_value": item.current_value,
                    "confidence": item.confidence,
                    "reason": "ambiguous+low_confidence",
                })
            else:
                filtered.append(item)
        return filtered

    def _build_prompt(self, events: list[dict], author_map: dict[str, str] | None = None,
                      time_ref: dict[str, str] | None = None) -> str:
        """Build prompt with contextual grounding for Feishu collaboration scenarios.

        V1.5 改进：
        - 代词解析：他/她/他们 → 具体人名
        - 时间解析：明天/下周 → 具体日期（基于消息 created_at）
        - 空间解析：这里/那里 → 具体位置
        - 飞书@提及解析：从 at_list 提取参与人员

        V1.5-P0 修复：
        - "我"绑定到消息发送者姓名（通过 author_map）
        - 时间基于消息的 created_at 范围，而非 datetime.now()
        - 指代不明时降 confidence 规则

        Args:
            events: 飞书消息事件列表，每个事件包含 text, sender, created_at 等字段
            author_map: {author_id: display_name} 映射，用于"我"的解析
            time_ref: 消息时间参考范围，包含 min_time 和 max_time

        Returns:
            完整的 Prompt 字符串，包含上下文信息、解析规则和提取要求
        """
        mentions = self._extract_mentions(events)

        # === 构建时间参考上下文 ===
        # 使用消息的 created_at 范围，而非 datetime.now()
        # 这样历史消息回放时相对时间也能正确解析
        if time_ref:
            time_context = (
                f"消息时间范围：{time_ref.get('min_time', '未知')} ~ {time_ref.get('max_time', '未知')}\n"
                f"注意：所有相对时间如'明天''下周'请基于消息自身的 created_at 字段计算"
            )
        else:
            time_context = "消息时间范围：未知"

        # === 构建作者映射上下文 ===
        if author_map:
            author_context = (
                "消息发送者映射（用于解析'我'）：\n" +
                "\n".join(f"  - {name} (id: {aid})" for aid, name in sorted(author_map.items()))
            )
        else:
            author_context = "消息发送者映射：无"

        prompt = f"""你是一个协作记忆提取助手，专门分析飞书群消息。

【任务目标】
提取当前仍会影响后续执行的协作状态，包括：目标、负责人、决策、阻塞事项、下一步行动。

【上下文信息】
{time_context}

{author_context}

参与讨论的人员：{', '.join(mentions.values()) if mentions else '未知'}

【提取要求】
1. 只提取当前仍会影响执行的状态（目标、负责人、决策、阻塞、下一步）
2. 被后续消息推翻的旧决策要标记为 superseded
3. 返回严格 JSON 格式：{{"candidates": [...]}}

【代词解析规则】(重要！)
- "他/她/他们" → 替换为具体人名
  - 优先从 @列表 和消息上下文中匹配
  - 如果无法确定具体指代对象 → confidence 必须 ≤ 0.3，且在 current_value 中标注 [ambiguous: 指代不明]
  - 禁止强行猜测！
- "我" → 替换为该条消息的发送者姓名（根据上面的"消息发送者映射"）
- "我们" → 替换为"项目组全体成员"或具体团队名
- 示例："他说要改方案"（若无法确定"他"是谁）→ confidence ≤ 0.3 并标注

【时间解析规则】(重要！)
- 相对时间请基于每条消息自身的 created_at 字段计算！
  - "明天/后天" → 基于该消息 created_at 推算具体日期
  - "下周/下个月" → 基于该消息 created_at 推算
  - "刚才/之前" → 根据 created_at 推算
  - 禁止使用当前系统时间替代！
- 示例："明天完成"（若消息 created_at=2026-04-25）→ "2026-04-26 前完成"

【空间解析规则】(重要！)
- "这里/这个地方" → 具体地点或上下文指代
- "那个文档" → 具体文档名称或链接
- 示例："把文档改一下" → "把需求文档 (doc_xxx) 改一下"

【输出格式】
每个 candidate 必须包含以下字段：
- project_id: 项目 ID（从消息中提取或使用 default）
- state_type: 状态类型（goal/owner/decision/blocker/next_step/deferred）
- key: 记忆的唯一标识键
- current_value: 当前值（具体信息）
- rationale: 为什么这条记忆重要
- owner: 负责人（可选）
- status: 状态（active/superseded/deferred）
- confidence: 置信度（0-1 之间的数字）
- source_refs: 来源引用列表，每个引用包含 chat_id, message_id, excerpt, created_at
- detected_at: 检测时间（ISO 格式）

【消息列表】
{json.dumps(events, ensure_ascii=False, indent=2)}

【提取结果】
"""
        return prompt

    def _extract_mentions(self, events: list[dict]) -> dict[str, str]:
        """从飞书消息事件列表中提取@提及映射。

        V1.5 新增：飞书场景优化，解析消息中的@user 提及，
        用于 Prompt 中的代词解析上下文。

        Args:
            events: 飞书消息事件列表

        Returns:
            {user_id: display_name} 映射字典，用于代词替换参考
        """
        mentions = {}
        for event in events:
            # 飞书消息可能包含 at_list 字段（@的用户列表）
            at_list = event.get("at_list", [])
            if isinstance(at_list, list):
                for at_user in at_list:
                    user_id = at_user.get("user_id") or at_user.get("id")
                    user_name = at_user.get("user_name") or at_user.get("name")
                    if user_id and user_name:
                        mentions[user_id] = user_name

            # 也从文本中@提及提取（备用方案）
            text = event.get("text", "")
            if isinstance(text, str):
                # 匹配 @用户名 模式
                for match in re.finditer(r"@([A-Za-z0-9_\u4e00-\u9fff]+)", text):
                    name = match.group(1)
                    mentions[f"mention_{name}"] = name

        return mentions

    def _build_author_map(self, events: list[dict]) -> dict[str, str]:
        """从事件列表构建 author_id → display_name 映射。

        用于 prompt 中"我"的解析绑定。从飞书 sender 结构中提取：
        - user 消息：sender.name 为真实姓名
        - app 消息：使用 sender.id 并标注为 bot
        - system/anonymous/webhook 消息：跳过（不参与"我"的解析）

        V1.6 修复：空 sender.id、system/webhook/anonymous sender 跳过，
        system 消息不参与"我"的作者解析。

        Returns:
            {author_id: display_name} 映射字典
        """
        author_map: dict[str, str] = {}
        for event in events:
            sender = event.get("sender")
            if not isinstance(sender, dict):
                continue
            sender_id = sender.get("id")
            # V1.6：跳过空 id 或纯空白 id 的 sender（system 消息等）
            if not sender_id or not sender_id.strip():
                continue
            if sender_id in author_map:
                continue
            sender_type = sender.get("sender_type", "unknown")
            # V1.6：跳过 system/anonymous/webhook 等非人非 bot 的 sender
            if sender_type in ("", "system", "anonymous", "webhook"):
                continue
            if sender_type == "user":
                # user 消息使用真实姓名
                name = sender.get("name") or sender_id
            elif sender_type == "app":
                # app/bot 消息标记为 bot
                name = f"bot({sender_id[:12]})"
            else:
                name = sender_id
            author_map[sender_id] = name
        return author_map

    def _build_time_reference(self, events: list[dict]) -> dict[str, str]:
        """从事件列表构建时间参考范围。

        用于 prompt 中相对时间（明天/下周）的正确解析。
        使用消息自身的 created_at 字段，确保历史回放时时间解析正确。

        V1.6 修复：用 datetime 排序列而非字符串排序，支持时区偏移。

        Returns:
            {"min_time": earliest_time, "max_time": latest_time}
        """
        parsed_times: list[tuple[str, str]] = []
        for event in events:
            raw = event.get("created_at")
            if not raw:
                continue
            raw_str = str(raw)
            # 尝试解析为 datetime 用于排序，支持 Z 后缀和时区偏移
            sort_key = raw_str
            try:
                dt = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
                sort_key = dt.isoformat()
            except (ValueError, TypeError):
                pass  # 解析失败 fallback 字符串排序
            parsed_times.append((sort_key, raw_str))
        if not parsed_times:
            return {"min_time": "未知", "max_time": "未知"}
        parsed_times.sort(key=lambda x: x[0])
        return {"min_time": parsed_times[0][1], "max_time": parsed_times[-1][1]}

    def _parse_response(self, response: str) -> dict[str, Any]:
        """Parse a provider response and require the top-level candidates list."""
        payload = json.loads(response.strip())
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            raise CandidateValidationError("LLM response must be an object with candidates list")
        return payload


class RuleBasedExtractor(BaseExtractor):
    """Extract V1 collaboration state using simple Chinese/English keyword rules."""

    # V1.6: 成员状态关键词
    _MEMBER_STATUS_KEYWORDS = frozenset(["请假", "不在", "休假", "出差", "习惯用", "擅长"])

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """Extract candidate memory items from normalized raw message events."""
        items: list[MemoryItem] = []
        for event in events:
            text = self._event_text(event)
            if not text:
                continue
            items.extend(self._extract_goal(event, text))
            items.extend(self._extract_owner(event, text))
            items.extend(self._extract_decision(event, text))
            items.extend(self._extract_pause(event, text))
            items.extend(self._extract_blocker(event, text))
            items.extend(self._extract_next_step(event, text))
            items.extend(self._extract_member_status(event, text))
        return items

    def _event_text(self, event: dict) -> str:
        """Return the normalized text content from a raw event."""
        value = event.get("text") or event.get("content") or event.get("excerpt") or ""
        return str(value).strip()

    def _base_item(
        self,
        event: dict,
        text: str,
        state_type: str,
        key: str,
        current_value: str,
        rationale: str,
        confidence: float,
        owner: str | None = None,
    ) -> MemoryItem:
        """Build a MemoryItem with a single message source reference."""
        return MemoryItem(
            project_id=str(event.get("project_id", "default")),
            state_type=state_type,
            key=key,
            current_value=current_value[:500],
            rationale=rationale[:500],
            owner=owner,
            status="active",
            confidence=confidence,
            source_refs=[source_ref_from_event(event, text)],
        )

    def _extract_goal(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract the current project goal when a message states one."""
        if not any(word in text.lower() for word in ("目标", "goal", "要做的是")):
            return []
        return [
            self._base_item(
                event,
                text,
                "project_goal",
                "current_goal",
                text,
                "Message appears to define or restate the current project goal.",
                0.65,
            )
        ]

    def _extract_owner(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract current owner or responsible person statements."""
        match = re.search(r"(?:负责人|owner|由)\s*[:：]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,20})\s*(?:负责|owner)?", text)
        if not match or "负责" not in text and "负责人" not in text.lower() and "owner" not in text.lower():
            return []
        owner = match.group(1)
        return [
            self._base_item(
                event,
                text,
                "owner",
                "current_owner",
                owner,
                "Message assigns or updates project responsibility.",
                0.7,
                owner=owner,
            )
        ]

    def _extract_decision(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract key decisions and decision reversals from a message."""
        lowered = text.lower()
        decision_words = ("决定", "决策", "确定", "采用", "改为", "不再", "instead", "decide")
        if not any(word in lowered for word in decision_words):
            return []
        key = "current_decision_override" if any(word in lowered for word in ("改为", "不再", "推翻", "instead")) else self._hash_key(text)
        return [
            self._base_item(
                event,
                text,
                "decision",
                key,
                text,
                "Message records a decision that can affect future execution.",
                0.75,
            )
        ]

    def _extract_pause(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract paused or deferred work items from a message."""
        if not any(word in text for word in ("暂缓", "暂停", "延期", "先不", "搁置")):
            return []
        return [
            self._base_item(
                event,
                text,
                "deferred",
                self._hash_key(text),
                text,
                "Message marks work as paused, deferred, or intentionally not pursued now.",
                0.72,
            )
        ]

    def _extract_blocker(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract blockers, risks, or dependency constraints from a message."""
        lowered = text.lower()
        if not any(word in lowered for word in ("阻塞", "卡住", "风险", "依赖", "blocker", "blocked", "risk")):
            return []
        return [
            self._base_item(
                event,
                text,
                "blocker",
                self._hash_key(text),
                text,
                "Message identifies a blocker, risk, or dependency.",
                0.7,
            )
        ]

    def _extract_next_step(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract next-step or task-like statements from a message."""
        lowered = text.lower()
        if not any(word in lowered for word in ("下一步", "待办", "todo", "需要", "请", "next")):
            return []
        return [
            self._base_item(
                event,
                text,
                "next_step",
                self._hash_key(text),
                text,
                "Message suggests work that should influence the next action plan.",
                0.62,
            )
        ]

    def _extract_member_status(self, event: dict, text: str) -> list[MemoryItem]:
        """V1.6: 提取成员状态信息（请假、出差、工作偏好等）。

        关键词：请假/不在/休假/出差/习惯用/擅长
        """
        if not any(kw in text for kw in self._MEMBER_STATUS_KEYWORDS):
            return []
        return [
            self._base_item(
                event,
                text,
                "member_status",
                self._hash_key(text),
                text,
                "Message indicates member availability or work preference.",
                0.65,
            )
        ]

    def _hash_key(self, text: str) -> str:
        """Return a compact stable key for text-scoped memory items."""
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        return f"item_{digest}"
