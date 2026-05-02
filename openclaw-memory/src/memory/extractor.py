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

# V1.10 新增: 复杂语义信号 —— 当规则提取器遇到这些信号时需 LLM 补充
_COMPLEX_SIGNALS = frozenset([
    "不再", "改为", "取消", "换成", "改成", "转给",
    "但是", "不过", "然而", "还是别",
    "我来", "他来", "她来",
    "放弃", "算了", "先后", "不做了", "回来了", "休假回来", "不阻塞", "扩容了",
    "考虑", "是否", "待定",
])


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
            # V1.12: 构建 event_map 用于 excerpt 原文验证 + sender/url 注入
            event_map = {str(e.get("message_id", "")): e for e in event_list if e.get("message_id")}
            candidates = [
                validate_candidate_dict(candidate, valid_message_ids, event_map)
                for candidate in payload["candidates"]
            ]
            items = [candidate_to_memory_item(candidate) for candidate in candidates]
            # V1.6: 后处理——过滤 ambiguous + 低置信度候选
            items = self._filter_ambiguous(items, candidates)
            # V1.10: 标准化 state_type（goal → project_goal 等别名映射）
            items = self._normalize_state_type(items)
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

    # V1.11: state_type 别名映射——将 LLM 可能输出的非标准类型标准化为枚举值
    _STATE_TYPE_ALIASES = {
        "goal": "project_goal",
        "objective": "project_goal",
        "target": "project_goal",
        "task": "next_step",
        "todo": "next_step",
        "action": "next_step",
        "next": "next_step",
        "status": "member_status",
        "availability": "member_status",
        "member": "member_status",
        "risk": "blocker",
        "dependency": "blocker",
        "block": "blocker",
        "issue": "blocker",
        "ddl": "deadline",
        "due": "deadline",
        "date": "deadline",
        "pause": "deferred",
        "postpone": "deferred",
        "delay": "deferred",
    }

    def _normalize_state_type(self, items: list[MemoryItem]) -> list[MemoryItem]:
        """标准化 MemoryItem 的 state_type，将别名映射为系统标准值。"""
        for item in items:
            canonical = self._STATE_TYPE_ALIASES.get(item.state_type)
            if canonical is not None:
                item.state_type = canonical
        return items

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

        prompt = f"""你是一个协作记忆提取助手，专门分析飞书群消息。你的任务是理解团队成员之间的自然对话，提取其中隐含的协作状态。

【任务目标】
从群聊消息中提取会影响后续执行的协作状态，包括：项目目标、负责人分配/变更、决策（含隐式决策）、截止时间、阻塞事项、下一步行动、成员状态。

【上下文信息】
{time_context}

{author_context}

参与讨论的人员：{', '.join(mentions.values()) if mentions else '未知'}

【提取要求】
1. 只提取当前仍会影响执行的状态。
2. 只添加新发现的协作状态，不判断是否与已有记忆冲突。下游系统会自动处理去重和版本管理。
3. 返回严格 JSON 格式：{{"candidates": [...]}}

【隐式语义识别规则】(关键！V1.11 新增)
许多协作信息是以隐式方式表达的，没有明确关键词。你必须识别以下模式：
- "XXX在弄/在做/在搞/在写 YYY" → state_type=owner, owner=XXX, current_value=YYY
- "考虑/是否/要不要/打算/准备 XXX" → state_type=decision, current_value=XXX, confidence=0.65-0.75（待定决策，置信度不要太高）
- "YYY还没好/还没做完/动不了/来不及/等YYY" → state_type=blocker, current_value=YYY
- "周五之前/赶在XX前/争取XX完成" → state_type=deadline, current_value=具体时间
- "记得/别忘了 XXX" → state_type=next_step, current_value=XXX
- "算了/还是/换成/不做了，XXX" → state_type=decision（决策变更）, current_value=XXX
- "那就/那先/先做 XXX" → state_type=next_step, current_value=XXX
- "方案不太行/换个思路" → state_type=decision（否定前方案）, current_value=相关描述
- "XXX没给/没出/没回复" → state_type=blocker, current_value=XXX延迟
- "这周大家集中把XXX搞完" → state_type=project_goal, current_value=XXX
- "我来/他来做/负责 XXX" → state_type=owner 或 next_step

关键判断方法：如果一个句子在协作上下文中暗示了"谁做什么、决定什么、等什么、截止时间"，就要提取。

【代词解析规则】(重要！)
- "他/她/他们" → 替换为具体人名
  - 优先从 @列表 和消息上下文中匹配
  - 如果无法确定具体指代对象 → confidence 必须 ≤ 0.3，且在 current_value 中标注 [ambiguous: 指代不明]
  - 禁止强行猜测！
- "我" → 替换为该条消息的发送者姓名（根据上面的"消息发送者映射"）
- "我们" → 替换为"项目组全体成员"或具体团队名

【时间解析规则】(重要！)
- 相对时间请基于每条消息自身的 created_at 字段计算。
- "明天/后天" → 基于该消息 created_at 推算具体日期
- "下周/下个月" → 基于该消息 created_at 推算
- "刚才/之前" → 根据 created_at 推算
- 禁止使用当前系统时间替代！

【输出格式】
每个 candidate 必须包含以下字段：
- project_id: 项目 ID（从消息中提取或使用 default）
- state_type: 状态类型（必须是以下之一：project_goal / owner / decision / deadline / blocker / next_step / deferred / member_status。禁止使用 goal、task、status、todo、risk 等非标准值）
- key: 记忆的唯一标识键（格式：小写英文+下划线，如 owner_api_module、decision_k8s_adopt）
- current_value: 当前值（具体信息，不要包含"负责人：""决策："等前缀标签）
- rationale: 为什么这条记忆重要（一句话）
- owner: 负责人姓名（owner/deadline/blocker 类型尽量填写，其他类型可选）
- status: 固定为 "active"
- confidence: 置信度（明确表达 0.75-0.9，隐式表达 0.55-0.7）
- source_refs: 来源引用列表，每个引用包含 chat_id, message_id, excerpt（原文摘要）, created_at
- detected_at: 检测时间（ISO 格式，用消息的 created_at）

【文档内容提取规则】(V1.12 新增)
当消息的 source_type 为 "doc" 或 sender_type 为 "doc_sync" 时，表示内容来自飞书文档：
- 文档中的章节标题、"项目目标""需求概述"等 → state_type=project_goal
- 文档中的负责人标注（如"负责人：XXX"）→ state_type=owner，与群聊消息同等对待
- 文档中的列表项、任务描述 → state_type=next_step
- 文档中的时间节点、"截止日期" → state_type=deadline
- 文档内容不是对话，不应用代词解析规则（"我""他"等指代的是文档作者，不是消息发送者）
- 文档内容默认置信度可略高（0.7-0.85），因为文档通常是经过整理的正式信息

【禁止提取规则】(严格遵循！)
以下情况**必须返回空的 candidates 列表**：
1. 纯链接/图片/文件/代码片段（无协作语义）
2. 纯社交闲聊："大家好""下班了""天气""下午茶"
3. 纯技术咨询问句且无动作指示："有人知道怎么配吗"
4. 纯 FYI 通知无指派/决策含义："下周二放假"
5. 纯确认回复："好的""收到""OK""明白了""嗯嗯"
6. 纯日常回应："稍等我看一下""我到了""我试试看"

判断原则：去掉所有协作信号后，剩余内容不足以形成协作状态 → 返回空列表。

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
            # V1.12：跳过空/anonymous/webhook，但保留 system（需保留以正确匹配已处理事件）和 doc_sync/task_sync
            if sender_type in ("", "anonymous", "webhook"):
                continue
            if sender_type == "user":
                name = sender.get("name") or sender_id
            elif sender_type in ("app", "bot"):
                name = f"bot({sender_id[:12]})"
            elif sender_type == "doc_sync":
                name = f"文档({sender_id[:20]})"
            elif sender_type == "task_sync":
                name = f"任务({sender_id[:20]})"
            elif sender_type == "system":
                continue  # 真实 system 消息（群成员变更等）仍然跳过
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
            items.extend(self._extract_deadline(event, text))
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
        """Extract current owner or responsible person statements.

        V1.11 修复：增加更多中文/英文格式支持。
        支持的格式：
        - "负责人：张三" / "owner：张三" / "owner 改成张三"
        - "由张三负责" / "由张三处理/做/开发/..."
        - "张三是这个模块的负责人" (V1.11)
        - "John is the owner of the API module" (V1.11)
        - "分工：张三负责前端，李四负责后端" → 提取多个 (V1.11)
        """
        items: list[MemoryItem] = []
        text_lower = text.lower()

        # Pattern 1: "负责人：张三" / "owner：张三" / "owner 改成张三"
        # 正向预查：名字后面必须跟关键词或句尾，防止贪心捕获整句
        match = re.search(
            r"(?:负责人|owner)\s*(?:[:：]|\s+(?:改成|改为|换成|转给))\s*"
            r"([A-Za-z0-9_\-一-鿿]{1,20})"
            r"(?=\s*(?:负责|owner|做|处理|开发|测试|实现|设计|修改|跟进|对接|完成|编写|优化|，|。|！|$))",
            text,
        )
        if not match:
            # 无后续关键词时，只取前 1-4 个字符作为人名（中文名通常 2-4 字）
            match = re.search(
                r"(?:负责人|owner)\s*(?:[:：]|\s+(?:改成|改为|换成|转给))\s*"
                r"([A-Za-z0-9_\-一-鿿]{1,4})",
                text,
            )
        if match:
            items.append(self._build_owner_item(event, text, match.group(1)))

        # Pattern 2: "由张三负责" / "由张三处理/做/开发/..."
        match = re.search(
            r"由\s*([A-Za-z0-9_\-一-鿿]{1,20})"
            r"\s*(?:负责|owner|做|处理|开发|测试|实现|设计|修改|跟进|对接|完成|编写|优化)",
            text,
        )
        if match:
            name = match.group(1)
            if name not in {item.owner for item in items}:
                items.append(self._build_owner_item(event, text, name))

        # Pattern 3: "XXX是这个模块的负责人" (V1.11)
        match = re.search(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*是\S*\s*(?:负责人|owner)",
            text,
        )
        if match:
            name = match.group(1)
            if name not in {item.owner for item in items}:
                items.append(self._build_owner_item(event, text, name))

        # Pattern 4: English "John is the owner of X" (V1.11)
        match = re.search(
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+the\s+owner\s+of",
            text,
        )
        if match:
            name = match.group(1)
            if name not in {item.owner for item in items}:
                items.append(self._build_owner_item(event, text, name))

        # Pattern 5: "分工：张三负责前端，李四负责后端" — 多负责人 (V1.11)
        for match in re.finditer(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*负责",
            text,
        ):
            name = match.group(1)
            if name.isascii() and name.islower():
                continue
            if name in {item.owner for item in items}:
                continue
            items.append(self._build_owner_item(event, text, name))

        # Pattern 6: "张三：负责后端引擎" — 文档列表常见格式 (V1.12)
        _non_name_words = frozenset({
            "目标", "决策", "阻塞", "下一步", "截止", "需求", "任务", "方案",
            "项目", "文档", "系统", "模块", "功能", "问题", "风险",
        })
        match = re.search(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*[:：]\s*(?:负责|owner|处理|开发|测试|实现|设计|修改|跟进|对接|完成|编写|优化)",
            text,
        )
        if match:
            name = match.group(1)
            if name not in _non_name_words and name not in {item.owner for item in items}:
                items.append(self._build_owner_item(event, text, name))

        # Guard: no items or no ownership keyword at all → skip
        if not items:
            return items
        if not any(kw in text_lower for kw in ("负责", "负责人", "owner", "由")):
            return []
        return items

    def _build_owner_item(
        self, event: dict, text: str, owner: str,
    ) -> MemoryItem:
        """Build a single owner-type MemoryItem."""
        return self._base_item(
            event, text, "owner", "current_owner", owner,
            "Message assigns or updates project responsibility.",
            0.7, owner=owner,
        )

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
        """V1.11: 提取成员状态信息，裁剪为关键信息。

        关键词：请假/不在/休假/出差/习惯用/擅长
        """
        if not any(kw in text for kw in self._MEMBER_STATUS_KEYWORDS):
            return []
        value = self._trim_member_status_value(text)
        return [
            self._base_item(
                event,
                text,
                "member_status",
                self._hash_key(text),
                value,
                "Message indicates member availability or work preference.",
                0.65,
            )
        ]

    def _trim_member_status_value(self, text: str) -> str:
        """V1.11: 从成员状态消息中裁剪出关键信息。

        "我这周请假，有什么事找李四" → "请假"
        "我习惯用 Figma 做设计" → "习惯用 Figma"
        "我明天不在，出差去上海" → "出差"
        "我擅长做后端架构设计" → "擅长后端架构"
        """
        # 匹配状态关键词及其前后上下文
        patterns = [
            (r"(请假)", "请假"),
            (r"(休假)", "休假"),
            (r"(出差)", "出差"),
            (r"(不在)", "不在"),
            (r"习惯用\s*(\S+)", None),  # 捕获后面的工具名
            (r"擅长做?\s*(.{1,15})", None),  # 捕获后面的技能
        ]
        for pattern, fallback in patterns:
            match = re.search(pattern, text)
            if match:
                if fallback:
                    return fallback
                return match.group(0)[:40]
        return text[:60]

    def _extract_deadline(self, event: dict, text: str) -> list[MemoryItem]:
        """V1.11: 提取截止时间/期限信息，裁剪为关键时间点。

        关键词：DDL/截止/deadline/延期/改到/调到
        """
        keywords = ("DDL", "截止", "deadline", "延期", "改到", "调到")
        if not any(kw in text for kw in keywords):
            return []
        value = self._trim_deadline_value(text)
        return [
            self._base_item(
                event,
                text,
                "deadline",
                self._hash_key(text),
                value,
                "Message sets or changes a deadline or time constraint.",
                0.68,
            )
        ]

    def _trim_deadline_value(self, text: str) -> str:
        """V1.11: 从截止日期消息中裁剪出关键时间点。

        "DDL 到下周五" → "下周五"
        "截止日期从周五改到下周三" → "下周三"
        "延期到明天交付" → "明天"
        "deadline 改到下周一" → "下周一"
        """
        # 找 "到" 之后的时间描述
        match = re.search(
            r"(?:到|至|在|为)\s*(.{2,20}?)(?:\s*[。！，,\.]|\s*交付|\s*完成|\s*之前|\s*为止|$)",
            text,
        )
        if match:
            return match.group(1).strip()[:40]
        # Fallback: 找时间词
        time_words = r"(?:下?周[一二三四五六日天]|明天|后天|今天|下?个?月[初底]|周[末末]|今[晚早]|明[晚早])"
        match = re.search(time_words, text)
        if match:
            return match.group(0)
        return text[:60]

    def _hash_key(self, text: str) -> str:
        """Return a compact stable key for text-scoped memory items."""
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        return f"item_{digest}"


class HybridExtractor(BaseExtractor):
    """Rule-first extractor that calls LLM only when rule results are insufficient.

    V1.10 新增：轻量 hybrid 判断逻辑。
    流程：
    1. 先跑 RuleBasedExtractor
    2. 检查规则结果是否需要 LLM 补充：
       a) 规则结果为空（什么都没提取到）
       b) 结果中所有 item 的 confidence 都 <= 0.65（低置信度）
       c) 消息文本包含复杂语义信号（不再、改为、取消、我来、但是、考虑、是否等）
       d) 结果缺少关键字段（owner=None 但消息提到了人名）
       e) 单条消息包含多个动作（需要上下文）
    3. 如果需要 LLM 补充，调用 LLM 提取器，LLM 输出经过 schema 校验
    4. 将规则结果与 LLM 结果合并去重

    安全保证：
    - LLM 输出必须经过现有 schema 校验
    - 校验失败时自动 fallback 到纯规则结果
    - 不引入任何真实写入或安全策略变更
    """

    # 人名模式：中文名 2-4 字 / 英文名 / 单字母名（如 C）
    _NAME_PATTERN = re.compile(
        # 中文姓名模式：以常见姓氏开头，后接非姓名词后缀
        r"(?:张|王|李|赵|刘|陈|杨|黄|吴|周|徐|孙|马|朱|胡|郭|何|高|林|罗|郑|梁|"
        r"谢|宋|唐|韩|曹|许|邓|萧|冯|程|蔡|彭|潘|袁|于|董|余|叶|蒋|魏|苏|吕|杜|"
        r"丁|沈|任|姚|卢|傅|钟|姜|崔|廖|谭|汪|范|金|石|贾|韦|夏|傅|方|白|邹|孟|"
        r"熊|秦|邱|江|尹|薛|闫|段|雷|侯|龙|史|陶|黎|贺|顾|毛|郝|龚|邵|万|钱|严|"
        r"覃|武|戴|莫|孔|向|汤)"
        r"(?!案|法|式|向|针|面|位|便|能|言|向|型|块|向)"
        r"[一-鿿]{1,3}|"
        # 单字母代号
        r"\b[A-Z]\b(?!\s*[a-z])"
    )
    # 复杂语义信号——当规则提取器遇到这些信号时需 LLM 补充
    _COMPLEX_SIGNALS = frozenset([
        "不再", "改为", "取消", "换成", "改成", "转给",
        "但是", "不过", "然而", "还是别",
        "我来", "他来", "她来",
        "放弃", "算了", "先后", "不做了", "回来了", "休假回来", "不阻塞", "扩容了",
        "考虑", "是否", "待定",
    ])
    # V1.11: 隐式语义信号——无明确协作关键词但暗示协作状态的表达
    _IMPLICIT_SIGNALS = frozenset([
        "在弄", "在做", "在搞", "在写", "在改", "在修",
        "还没好", "还没做完", "动不了", "来不及",
        "那就", "那先", "先做", "先弄",
        "方案不太行", "换个思路", "换个方案",
        "没给", "没出", "没回复", "一直没",
        "大家集中", "赶在", "争取", "尽量",
        "记得", "别忘了",
        "打算", "要不要",
        "搞定", "弄完", "搞完",
        "准备用", "准备把", "准备做",
    ])
    # 低置信度阈值
    _LOW_CONFIDENCE_THRESHOLD = 0.65

    def __init__(
        self,
        rule_extractor: BaseExtractor | None = None,
        llm_extractor: BaseExtractor | None = None,
    ) -> None:
        """Create a hybrid extractor.

        Args:
            rule_extractor: 规则提取器，默认 RuleBasedExtractor
            llm_extractor: LLM 提取器，默认 None（纯规则模式）
        """
        self.rule = rule_extractor or RuleBasedExtractor()
        self.llm = llm_extractor

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """Rule-first extraction with optional LLM supplement."""
        event_list = list(events)
        if not event_list:
            return []

        # Step 1: 规则提取
        rule_items = self.rule.extract(event_list)

        # Step 2: 判断是否需要 LLM 补充
        if self.llm is None:
            return rule_items

        if not self._needs_llm(rule_items, event_list):
            return rule_items

        # Step 3: LLM 提取（含 schema 校验 + fallback）
        llm_items = self._safe_llm_extract(event_list)

        # Step 4: 合并规则结果与 LLM 结果（去重）
        merged = self._merge_results(rule_items, llm_items)

        # V1.11: 二次兜底 — LLM 和规则都几乎没提取，但触发了 LLM
        if not merged and self._needs_llm(rule_items, event_list):
            # 生成一个低置信度的提示项，避免静默失败
            text_sample = str(
                event_list[0].get("text", "") if event_list else ""
            )[:80]
            from memory.schema import MemoryItem, source_ref_from_event
            note = MemoryItem(
                project_id=rule_items[0].project_id if rule_items else "default",
                state_type="note",
                key="unrecognized_signal",
                current_value=f"[未识别] 检测到协作信号但无法确定类型: {text_sample}",
                rationale="Hybrid 触发但规则和 LLM 均未提取。可能为新型隐式表达。",
                owner=None,
                status="active",
                confidence=0.25,
                source_refs=[source_ref_from_event(event_list[0], text_sample)]
                if event_list else [],
            )
            merged.append(note)

        return merged

    def _needs_llm(self, rule_items: list[MemoryItem], events: list[dict]) -> bool:
        """判断是否需要用 LLM 补充提取。

        Args:
            rule_items: 规则提取的结果列表
            events: 原始事件列表

        Returns:
            True 表示需要 LLM 补充
        """
        # (a) 规则结果为空
        if not rule_items:
            return True

        # (b) 所有 item 的 confidence 都低
        max_conf = max(item.confidence for item in rule_items)
        if max_conf <= self._LOW_CONFIDENCE_THRESHOLD:
            return True

        # (c) 消息文本包含复杂语义信号
        for event in events:
            text = str(event.get("text", "") or event.get("content", "") or "")
            if any(signal in text for signal in self._COMPLEX_SIGNALS):
                return True

        # (d) 结果缺少关键字段但消息提到了人名
        for event in events:
            text = str(event.get("text", "") or event.get("content", "") or "")
            mentions_name = bool(self._NAME_PATTERN.search(text))
            if not mentions_name:
                continue
            has_owner = any(item.owner is not None for item in rule_items)
            if not has_owner:
                return True

        # (e) 单条消息包含多个动作
        for event in events:
            text = str(event.get("text", "") or event.get("content", "") or "")
            clauses = re.split(r"[；;。！？\n]", text)
            if len(clauses) >= 3:
                return True

        # (f) V1.11: 消息含隐式语义信号（无明确关键词但暗示协作状态）
        for event in events:
            text = str(event.get("text", "") or event.get("content", "") or "")
            if any(signal in text for signal in self._IMPLICIT_SIGNALS):
                return True

        # (g) V1.11: 规则提取了某些类型但缺少关键互补类型
        rule_types = {item.state_type for item in rule_items}
        # 有目标但没有负责人 → LLM 补充
        if "project_goal" in rule_types and "owner" not in rule_types:
            for event in events:
                text = str(event.get("text", "") or event.get("content", "") or "")
                if self._NAME_PATTERN.search(text):
                    return True
        # 有阻塞但没有下一步 → LLM 补充行动计划
        if "blocker" in rule_types and "next_step" not in rule_types:
            for event in events:
                text = str(event.get("text", "") or event.get("content", "") or "")
                if any(kw in text for kw in ("需要", "下一步", "请", "可以", "试试")):
                    return True

        return False

    def _safe_llm_extract(self, events: list[dict]) -> list[MemoryItem]:
        """安全调用 LLM 提取，异常时返回空列表（不 fallback 到规则，避免循环）。

        LLM 输出经过完整 schema 校验（validate_candidate_dict），
        校验失败时返回空列表，由调用方决定如何处理。
        """
        try:
            return self.llm.extract(events)
        except Exception:
            return []

    def _merge_results(
        self, rule_items: list[MemoryItem], llm_items: list[MemoryItem]
    ) -> list[MemoryItem]:
        """合并规则结果与 LLM 结果。

        V1.10 增强：按 state_type 分组后做内容相似度感知的合并。
        相同 state_type + 内容相似度 > 0.7 视为同一记忆，用 LLM 版本替换。
        key 对齐确保 upsert_items 三层去重正确识别新旧版本关系。
        """
        rule_by_type: dict[str, list[MemoryItem]] = {}
        for item in rule_items:
            rule_by_type.setdefault(item.state_type, []).append(item)

        llm_by_type: dict[str, list[MemoryItem]] = {}
        for item in llm_items:
            llm_by_type.setdefault(item.state_type, []).append(item)

        result: list[MemoryItem] = []
        for state_type in list(rule_by_type.keys()) + [t for t in llm_by_type if t not in rule_by_type]:
            rules = rule_by_type.get(state_type, [])
            llms = llm_by_type.get(state_type, [])

            if not rules:
                result.extend(llms)
                continue
            if not llms:
                result.extend(rules)
                continue

            used_llms = set()
            for r_item in rules:
                best_match = None
                best_sim = 0.0
                for i, l_item in enumerate(llms):
                    if i in used_llms:
                        continue
                    sim = self._compute_bigram_similarity(
                        r_item.current_value, l_item.current_value
                    )
                    if sim > best_sim:
                        best_sim = sim
                        best_match = i

                if best_match is not None and best_sim > 0.7:
                    llm_item = llms[best_match]
                    llm_item.key = r_item.key
                    # V1.12: 合并规则证据到 LLM 项，避免证据丢失
                    existing_ids = {ref.message_id for ref in llm_item.source_refs}
                    for ref in r_item.source_refs:
                        if ref.message_id not in existing_ids:
                            llm_item.source_refs.append(ref)
                            existing_ids.add(ref.message_id)
                    result.append(llm_item)
                    used_llms.add(best_match)
                else:
                    result.append(r_item)

            for i, l_item in enumerate(llms):
                if i not in used_llms:
                    result.append(l_item)

        return result

    @staticmethod
    def _compute_bigram_similarity(text1: str, text2: str) -> float:
        """基于字符 bigram Jaccard 相似度，用于 merge 时内容匹配。"""
        def get_char_bigrams(text: str) -> set[str]:
            t = text.replace(" ", "").lower()
            return {t[i:i+2] for i in range(len(t) - 1)}
        b1 = get_char_bigrams(text1)
        b2 = get_char_bigrams(text2)
        if not b1 and not b2:
            return 1.0
        if not b1 or not b2:
            return 0.0
        return len(b1 & b2) / len(b1 | b2)
