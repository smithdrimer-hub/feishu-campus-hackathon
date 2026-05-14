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
            # V1.12: 构建 event_map 用于 excerpt 原文验证 + sender/url 注入
            event_map = {str(e.get("message_id", "")): e for e in event_list if e.get("message_id")}
            candidates = [
                validate_candidate_dict(candidate, valid_message_ids, event_map)
                for candidate in payload["candidates"]
            ]
            items = [candidate_to_memory_item(candidate) for candidate in candidates]
            # V1.15: 从 LLM 原始输出中提取 decision_strength
            raw_candidates = payload.get("candidates", [])
            for i, item in enumerate(items):
                if item.state_type == "decision" and i < len(raw_candidates):
                    raw = raw_candidates[i]
                    if isinstance(raw, dict) and "decision_strength" in raw:
                        item.decision_strength = str(raw["decision_strength"])
                        if item.decision_strength not in ("confirmed",):
                            item.review_status = "needs_review"
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
                # V1.16: 保留而非丢弃——标记为待审核，保留模糊关联
                item.review_status = "needs_review"
                if not item.metadata:
                    item.metadata = {}
                item.metadata["ambiguous_reason"] = "指代不明或模糊表达"
                item.metadata["possible_match"] = True
                filtered.append(item)
                self._dropped_candidates.append({
                    "key": item.identity_key(),
                    "current_value": item.current_value,
                    "confidence": item.confidence,
                    "reason": "ambiguous+low_confidence (kept for review)",
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
- decision_strength: 仅用于 state_type="decision"。根据表述强度选择：discussion（讨论中）/ preference（偏好表达）/ tentative（暂定）/ confirmed（正式确认）。判断依据：是否有明确确认词（确定、最终方案、就这么定了）→ confirmed；是否有暂定词（先这样、暂时、初步）→ tentative；是否有倾向词（觉得、倾向于、建议）→ preference；是否有讨论词（考虑、要不要、打算）→ discussion。注意区分"我考虑一下"（个人思考，不提取）和"考虑用XXX"（协作讨论，提取为 discussion）
- detected_at: 检测时间（ISO 格式，用消息的 created_at）

【文档内容提取规则】(V1.12 新增)
当消息的 source_type 为 "doc" 或 sender_type 为 "doc_sync" 时，表示内容来自飞书文档：
- 文档中的章节标题、"项目目标""需求概述"等 → state_type=project_goal
- 文档中的负责人标注（如"负责人：XXX"）→ state_type=owner，与群聊消息同等对待
- 文档中的列表项、任务描述 → state_type=next_step
- 文档中的时间节点、"截止日期" → state_type=deadline
- 文档内容不是对话，不应用代词解析规则（"我""他"等指代的是文档作者，不是消息发送者）
- 文档内容默认置信度可略高（0.7-0.85），因为文档通常是经过整理的正式信息

【消息语用分类】(提取前必须判断！A1)

每条消息先归入以下三类之一，再决定是否从中提取任何协作信号：

1. **信号发布** — 说话人首次提出、更新、或实质性改变某协作状态。
   识别特征：
   · 说话人是一线执行者（而非 PM / 组长做汇总）
   · 内容是具体的、可操作的、有时间锚点的
   · 消息长度适中，通常聚焦 1-2 个话题
   · 例："后端API迁移卡住了，数据库兼容性有问题" → 可提取
   · 例："最终决定用 PostgreSQL，迁移文档我更新" → 可提取
   · 例："张三：请假半天，下午不在" → 可提取
   提取要求：current_value 只包含该话题的具体内容，不包含整条消息。

2. **信号引用** — 说话人在汇总、回顾、同步、或转述已有协作状态。
   识别特征：
   · 说话人是 PM / 组长 / 项目协调人
   · 消息含多个话题（进度 + 阻塞 + 下一步混在一起）
   · 含总结性表述（"进度同步""今日总结""本周回顾""冲刺收尾""同步一下"）
   · 含 @所有人 且内容为团队全局视图
   · 含感谢/庆祝/收尾用语
   · 例："今日进度：API 70%，阻塞是网关权限未批，下一步联调" → 不提取
   · 例："感谢大家10天努力，冲刺目标基本达成" → 不提取
   提取要求：返回空的 candidates 列表。这类消息中提到的协作状态
   是对已存在状态的引用，不是首次发布。不要在信号引用类消息中
   寻找可提取的片段 —— 整条跳过。

3. **无关消息** — 纯闲聊、确认回复、技术咨询、纯通知。→ 不提取。

【多话题消息处理】(A2)
当一条信号发布类消息包含多个独立协作话题时：
· 每个话题单独作为一个 candidate
· 每个 candidate 的 current_value 只包含该话题的具体内容，
  不包含消息中其他话题的文本
· 如果各话题之间没有独立的负责人/状态/时间信息，
  则合并为一个 candidate
· 信号引用类消息无论几个话题，整条不提取

【禁止提取规则】(严格遵循！)
以下情况**必须返回空的 candidates 列表**：
1. 纯链接/图片/文件/代码片段（无协作语义）
2. 纯社交闲聊："大家好""下班了""天气""下午茶"
3. 纯技术咨询问句且无动作指示："有人知道怎么配吗"
4. 纯 FYI 通知无指派/决策含义："下周二放假"
5. 纯确认回复："好的""收到""OK""明白了""嗯嗯"
6. 纯日常回应："稍等我看一下""我到了""我试试看"
7. PM / 组长做的进度同步、汇总、回顾 → 整体判定为"信号引用"，不提取。
   包括但不限于："进度同步""今日进度""本周总结""冲刺回顾""收尾"
   "汇报一下""同步一下""过一下进度""对齐一下"。
   即使其中提到了阻塞、决策、目标等关键词，也属于引用而非发布。(A3)
8. 冲刺结束时的感谢/庆祝/收尾消息 → 不提取。
   即使包含"目标""完成""达成"等词，也属于回顾而非新信号。(A3)
9. 成员状态必须有具体状态描述（"请假""出差""不在""习惯用""擅长"），
   仅出现人名、职位、角色而没有任何状态描述的，不提取。(A3)
10. 消息中出现的生活化时间词（"下班前""周末前""放假前"）
    如果同时包含明确的任务动词（"完成""提交""修复""上线"），
    应正常提取为 next_step 或 deadline。生活词本身不改变提取判断。(A3)

判断原则：去掉所有协作信号后，剩余内容不足以形成协作状态 → 返回空列表。

【语言要求】(V1.16)
- 消息是中文 → 提取结果必须是中文
- current_value 和 rationale 保持消息的原始语言
- 不要翻译中文术语为英文（如"微服务"不要写成"microservices"）
- 不要在中英文之间频繁切换
- 如果消息本身是中英混合（如"用 React 做前端"），保留混合格式

【执行步骤】(V1.16 — 请严格按此顺序处理)
步骤1 — 代词解析：将"我/你/他/她/我们/他们"替换为具体人名。
  - "我" → 该消息的发送者姓名
  - "他/她" → 从前序消息中查找最近明确提到的人名
  - 无法确定时：保留代词，在 current_value 中标注 [ambiguous: 指代不明]，confidence ≤ 0.3
步骤2 — 时间解析：将"明天/后天/下周/周五"替换为具体日期。
  - 基于消息自身的 created_at 计算
  - "下周" → 下周一；"下周五" → 对应日期
步骤3 — 指代解析：将"这个/那个/该模块/这个方案"替换为前序消息中的具体名称。
步骤4 — 协作信号提取：判断消息是否包含 8 种协作状态。
步骤5 — 决策强度判断：区分 discussion（讨论）/ preference（偏好）/ tentative（暂定）/ confirmed（确认）。
步骤6 — 输出 JSON。

【跨消息推理】(V1.16 — 关键！)
这些消息是多轮对话。注意以下模式：
- 如果消息 A 提出一个方案，消息 B 提出不同意见 → 这是同一个讨论的来回，提取为一条 decision（含 preference 或 tentative 变更），而非两条独立 decision
- 如果多条消息讨论同一主题（如都在说"技术选型"），将它们的信息合并为一条记忆
- 如果消息 A 说"XX 还没好"，消息 B 说"XX 已经做完了"→ 后者否定了前者的 blocker，只保留后者的结论

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
            # V1.12：跳过空/anonymous/webhook/system，保留 doc_sync/task_sync
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
    """Extract V1 collaboration state using simple Chinese/English keyword rules.

    V1.17: selector_mode=True（默认）→ 有把握才提取，没把握交给LLM。
    selector_mode=False → 传统模式（全部提取，用于 Golden Set 评测）。
    """

    def __init__(self, selector_mode: bool = False):
        self.selector_mode = selector_mode

    # V1.6: 成员状态关键词
    _MEMBER_STATUS_KEYWORDS = frozenset(["请假", "不在", "休假", "出差", "习惯用", "擅长"])
    # V1.15: 第一人称代词（不作为owner提取，改为解析发送者）
    _SELF_REFERENCE = frozenset({"我", "你", "他", "她", "我们", "你们", "他们", "她们", "大家", "自己"})

    # Tokens that can never be a valid person name even if regex captures them
    # as the leading group. They are usually domain words bleeding from a
    # truncated capture (e.g. "部署与主从配置" / "项目").
    _OWNER_NON_PERSON_TOKENS = frozenset({
        "部署", "配置", "项目", "任务", "模块", "功能", "需求", "方案", "系统",
        "服务", "接口", "数据库", "测试", "环境", "前端", "后端", "运维", "产品",
        "设计", "评审", "上线", "提测", "发布", "迭代", "版本", "今天", "明天",
        "现在", "之后", "之前", "分工", "团队", "成员", "团队分工", "组织",
        "负责人", "下一步", "决策", "目标", "状态", "文档", "审批", "纪要",
        "审核", "数据",
    })

    @staticmethod
    def _normalise_person_name(name: str) -> str:
        """Drop role prefixes like '后端-马超' → '马超' and trim punctuation."""
        value = (name or "").strip().strip(",.;:，。；：、")
        if "-" in value:
            tail = value.split("-")[-1].strip()
            if tail:
                value = tail
        return value

    @classmethod
    def _is_valid_person_name(cls, name: str) -> bool:
        """Reject obvious non-person tokens captured by greedy regex."""
        if not name:
            return False
        normalised = cls._normalise_person_name(name)
        if not normalised:
            return False
        if normalised in cls._SELF_REFERENCE:
            return False
        if normalised in cls._OWNER_NON_PERSON_TOKENS:
            return False
        # Pure ASCII lowercase (e.g. 'todo') is treated as a keyword, not a name.
        if normalised.isascii() and normalised.islower():
            return False
        # Reject runs that look like sentence fragments.
        if any(ch in normalised for ch in "。！，；：、 \t"):
            return False
        return True

    # ── V1.17: Rule→Selector 信号体系 ──────────────────────────

    # 强不确定语气词（检测到 → 整条消息交给LLM）
    _HIGH_UNCERTAINTY = frozenset({"吗", "呢", "吧", "？", "?", "嘛", "喽", "呗"})
    # 中等不确定（含试探/商量的信号）
    _MED_UNCERTAINTY = frozenset({"要不", "要不要", "是否", "是不是", "会不会", "能不能", "可不可以"})
    # 弱不确定（概率性表达）
    _WEAK_UNCERTAINTY = frozenset({"也许", "可能", "大概", "估计", "好像", "似乎", "应该吧", "不清楚"})

    # 否定词（豁免词已排除）
    _NEGATION_CHARS = frozenset({"不", "没", "别", "无", "非", "未"})
    _NEGATION_SAFE = frozenset({"不错", "不管", "没问题", "没关系", "不要紧",
                                 "不仅如此", "不少", "说不定",
                                 "不要忘了", "别忘了"})

    # 极简回复（仅白名单，不按长度。短消息如"周五交"不应被误杀）
    _TRIVIAL = frozenset({"好的", "收到", "行", "OK", "ok", "嗯", "对", "是的",
                           "好", "可以", "没问题", "知道了", "明白了", "了解了"})

    # 纯问题模式（技术咨询，无协作意图 → 直接跳过）
    _PURE_QUESTION = ("请问", "谁知道", "有没有人", "有人知道", "怎么配", "怎么调",
                      "如何配置", "如何使用", "能不能帮", "可以帮", "怎么弄",
                      "啥意思", "什么意思", "是什么", "在哪里")

    # V1.20: @bot 查询模式（关于项目状态的提问，非协作贡献 → 跳过提取）
    _BOT_QUERY_PATTERNS = (
        "@bot",
    )
    # 状态关键词 + 疑问词的组合（"有什么阻塞""谁在负责"等）
    _STATE_INQUIRY_PATTERNS = (
        ("阻塞", ("什么", "哪些", "多少", "有什么", "有哪些", "在哪", "怎么样", "如何", "还有")),
        ("负责", ("谁", "谁在", "谁是", "还有谁")),
        ("目标", ("什么", "是什么", "有哪些")),
        ("决策", ("什么", "有哪些", "做了什么")),
        ("下一步", ("是什么", "做什么", "怎么做")),
        ("进度", ("怎么样", "如何", "")),
        ("截止", ("是什么", "什么时候", "是哪天")),
        ("延期", ("哪些", "有什么")),
    )

    # 口语化弱信号（不精确，但有协作可能 → LLM）
    # V1.17: 精确信号（格式固定，规则可直接提取）
    _PRECISE_SIGNALS = frozenset({
        "负责人", "负责", "owner",
        "就这么定了", "最终方案", "敲定", "正式决定",
        "阻塞了", "卡住了", "卡住",
        "下一步", "待办", "TODO",
        "DDL", "截止日期", "截止", "deadline",
        "暂缓", "先不做", "搁置",
        "目标是", "目标", "goal",
        "请假", "出差", "休假", "不在公司", "习惯用", "擅长",
        "分工",  # 分工：XXX负责YYY — 格式固定
    })

    # V1.17: 模糊信号（触发词常见但协作语义弱 → delegate LLM）
    _FUZZY_SIGNALS = frozenset({
        "决策", "决定", "确定", "采用", "改为", "不再", "换成", "改成", "改用",
        "考虑", "觉得", "倾向于", "建议", "推荐",
        "阻塞", "风险", "依赖",
        "需要", "请",
        "延期", "改到", "调到",
        "暂停", "先不",
        "不在",
        "由",
        "在弄", "在做", "在搞", "在写", "还没好", "来不及",
        "我来", "他来", "她来", "那就", "那先", "先做",
        "没给", "没出", "没回复", "打算", "要不要",
        "搞定", "弄完", "搞完", "准备用", "准备做",
    })

    @staticmethod
    def _has_precise_signal(text: str) -> bool:
        return any(w in text for w in RuleBasedExtractor._PRECISE_SIGNALS)

    @staticmethod
    def _has_fuzzy_signal(text: str) -> bool:
        return any(w in text for w in RuleBasedExtractor._FUZZY_SIGNALS)

    @staticmethod
    def _has_uncertainty(text: str) -> bool:
        """检测强不确定语气 → 整条消息交给LLM。"""
        return any(w in text for w in RuleBasedExtractor._HIGH_UNCERTAINTY)

    @staticmethod
    def _has_negation(text: str) -> bool:
        """检测有效否定（排除豁免词）。"""
        cleaned = text
        for safe in RuleBasedExtractor._NEGATION_SAFE:
            cleaned = cleaned.replace(safe, "")
        return any(w in cleaned for w in RuleBasedExtractor._NEGATION_CHARS)

    @staticmethod
    def _is_trivial(text: str) -> bool:
        """极简回复 → 跳过（仅白名单，不按长度）。"""
        return text.strip() in RuleBasedExtractor._TRIVIAL

    @classmethod
    def _is_bot_query(cls, event: dict, text: str) -> bool:
        """检测是否为 @bot 查询或项目状态提问（不应提取为协作信号）。

        真实场景中用户会 @bot 问"有什么阻塞""谁在负责"等——
        这些是元问题，不是协作贡献，必须跳过提取。
        """
        # 1. 直接 @bot 提及
        if "@bot" in text:
            return True
        # 2. 事件中有 bot 的 at_list 标记
        at_list = event.get("at_list", []) or []
        for at in at_list:
            uid = (at.get("user_id", "") or at.get("open_id", "")
                   if isinstance(at, dict) else str(at))
            if uid and "bot" in uid.lower():
                return True
        # 3. 状态关键词 + 疑问词组合（"有什么阻塞""谁在负责"等）
        for state_kw, inquiry_words in cls._STATE_INQUIRY_PATTERNS:
            if state_kw not in text:
                continue
            for iw in inquiry_words:
                if iw and iw in text:
                    return True
                if not iw and ("?" in text or "？" in text):
                    return True
        return False

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """V1.17: Selector模式——有把握才提取，没把握加入 delegate_list。

        delegate_list 存储在 self._delegate_list 中，供 HybridExtractor 读取。
        selector_mode=False → 传统模式（全部提取，Golden Set 评测用）。
        """
        items: list[MemoryItem] = []
        self._delegate_list: list[dict] = []

        for event in events:
            text = self._event_text(event)
            if not text:
                continue

            if str(event.get("source_type", "")) == "approval":
                items.extend(self._extract_approval_status(event, text))
                continue
            if str(event.get("source_type", "")) == "task":
                items.extend(self._extract_task_source(event, text))
            if str(event.get("source_type", "")) == "calendar":
                items.extend(self._extract_calendar_source(event, text))

            # FEAT-2: 文档事件有结构化 hints 时直接提取，跳过规则重解析
            if (str(event.get("source_type", "")) == "doc"
                    and event.get("extraction_hints")):
                hint_items = self._extract_from_hints(event)
                if hint_items:
                    items.extend(hint_items)
                    continue

            # V1.20: @bot 查询 / 项目状态提问 → 跳过提取（不是协作贡献）
            if self._is_bot_query(event, text):
                continue

            # V1.19 selector mode: 规则先提取，仅对真正不确定的场景 delegate LLM
            if self.selector_mode:
                if self._is_trivial(text):
                    continue
                has_any = self._has_precise_signal(text) or self._has_fuzzy_signal(text)
                has_uncertain = self._has_uncertainty(text)
                has_neg = self._has_negation(text)

                # 纯问题 → 跳过
                if has_uncertain and text.startswith(self._PURE_QUESTION):
                    continue
                # 无任何信号 → 跳过
                if not has_any:
                    continue

            # 提取（selector 和非 selector 都走这里）
            before_count = len(items)
            items.extend(self._extract_goal(event, text))
            items.extend(self._extract_owner(event, text))
            items.extend(self._extract_decision(event, text))
            items.extend(self._extract_pause(event, text))
            items.extend(self._extract_blocker(event, text))
            items.extend(self._extract_next_step(event, text))
            items.extend(self._extract_member_status(event, text))
            items.extend(self._extract_deadline(event, text))
            if str(event.get("source_type", "")) == "meeting":
                items.extend(self._extract_meeting_action_items(event, text))

            new_count = len(items) - before_count
            new_event_items = items[before_count:]

            # V1.19: 5-signal delegate check — 仅 selector 模式
            if self.selector_mode:
                should_delegate = False

                # Signal 1: 否定 + 有信号 → 规则可能反向理解，需 LLM
                if has_neg and has_any:
                    should_delegate = True
                # Signal 2: 不确定语气 + 有信号 → 非确定陈述
                elif has_uncertain and has_any:
                    should_delegate = True
                # Signal 3: 同类型多个候选 → 规则矛盾，需 LLM 裁决
                elif new_count >= 1:
                    new_types = [i.state_type for i in new_event_items]
                    if len(new_types) != len(set(new_types)):
                        should_delegate = True
                # Signal 4: 规则空但消息非琐碎且有信号 → 隐式表达
                elif new_count == 0 and has_any:
                    should_delegate = True
                # Signal 5: 互补缺失 — 有 owner 无 goal，有 blocker 无 next_step
                elif new_count >= 1:
                    new_types = {i.state_type for i in new_event_items}
                    if "owner" in new_types and "project_goal" not in new_types:
                        all_types = {i.state_type for i in items}
                        if "project_goal" not in all_types:
                            should_delegate = True
                    if "blocker" in new_types and "next_step" not in new_types:
                        all_types = {i.state_type for i in items}
                        if "next_step" not in all_types:
                            should_delegate = True

                if should_delegate:
                    self._delegate_list.append(event)
        return items

    def _event_text(self, event: dict) -> str:
        """Return the normalized text content from a raw event."""
        value = event.get("text") or event.get("content") or event.get("excerpt") or ""
        return str(value).strip()

    def _extract_from_hints(self, event: dict) -> list[MemoryItem]:
        """FEAT-2: Extract MemoryItems from structured extraction_hints.

        When _chunk_doc_markdown() detects structured signals (owner name,
        deadline date) in document tables/list items, this method converts
        them directly to MemoryItems, skipping the text re-parse step.
        Falls back to empty list if hints are missing or ambiguous.

        Args:
            event: Doc event dict with optional extraction_hints.

        Returns:
            List of MemoryItems (0 or more) extracted from hints.
        """
        hints = event.get("extraction_hints", {}) or {}
        if not isinstance(hints, dict) or not hints:
            return []

        detected_type = str(hints.get("detected_type", ""))
        detected_owner = str(hints.get("detected_owner", ""))
        if not detected_type:
            return []

        items: list[MemoryItem] = []
        text = self._event_text(event)
        confidence = 0.80  # hints come from structured doc parsing

        if detected_type == "owner" and detected_owner:
            if self._is_valid_person_name(detected_owner):
                items.append(MemoryItem(
                    project_id=str(event.get("project_id", "default")),
                    state_type="owner",
                    key=f"owner_{detected_owner}",
                    current_value=f"{detected_owner}负责：{text[:200]}",
                    rationale=f"文档表格/列表项中显式标注的负责人",
                    owner=detected_owner,
                    status="active",
                    confidence=confidence,
                    source_refs=[source_ref_from_event(event, text[:120])],
                ))

        if detected_type == "deadline":
            ddl_val = str(hints.get("extraction_hint", ""))
            ddl_val = ddl_val.replace("deadline=", "") if ddl_val.startswith("deadline=") else ddl_val
            items.append(MemoryItem(
                project_id=str(event.get("project_id", "default")),
                state_type="deadline",
                key=f"deadline_{text[:30]}",
                current_value=text[:200],
                rationale=f"文档表格/列表项中显式标注的截止日期：{ddl_val[:60]}",
                status="active",
                confidence=confidence,
                source_refs=[source_ref_from_event(event, text[:120])],
            ))

        return items

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
        """Extract the current project goal when a message states one.

        1.2: 回顾/疑问/总结性表述 → needs_review（标记但不丢弃）。
        只对明确的目标设定陈述给高置信度。
        """
        if not any(word in text.lower() for word in ("目标", "goal", "要做的是")):
            return []
        # 1.2: 回顾/疑问 → 低置信度 + 待审核
        retro_kw = ("达成", "完成了", "做完了", "收尾", "回顾", "搞完了",
                    "差不多了", "基本达成", "已经完成", "已达成", "实现了")
        question_kw = ("什么", "吗", "呢", "？", "?", "怎么", "如何")
        is_retro = any(kw in text for kw in retro_kw)
        is_question = any(kw in text for kw in question_kw) and len(text) < 50
        confidence = 0.35 if (is_retro or is_question) else 0.65
        item = self._base_item(
            event, text, "project_goal", "current_goal", text,
            "Message appears to define or restate the current project goal.",
            confidence,
        )
        if is_retro or is_question:
            item.review_status = "needs_review"
        return [item]

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
        # 多匹配版本：一段文本里可能出现多个 "负责人：xxx — 模块" 行，
        # 例如文档分工章节。我们对所有匹配都走 owner 验证。
        seen_pattern1: set[str] = set()
        for m in re.finditer(
            r"(?:负责人|owner)\s*(?:[:：]|\s+(?:改成|改为|换成|转给))\s*"
            r"([A-Za-z0-9_\-一-鿿]{1,20})"
            r"(?=\s*(?:负责|owner|做|处理|开发|测试|实现|设计|修改|跟进|对接|完成|"
            r"编写|优化|，|。|！|$|—|–|-))",
            text,
        ):
            candidate = m.group(1)
            tail = text[m.end():m.end() + 40]
            domain_match = re.match(r"\s*[—\-–:：]\s*([^\n，。,、；;!]{1,30})", tail)
            domain = ""
            if domain_match:
                domain_raw = domain_match.group(1).strip()
                domain = self._slugify(domain_raw)
            if candidate in seen_pattern1:
                continue
            seen_pattern1.add(candidate)
            owner_item = self._build_owner_item(event, text, candidate, domain)
            if owner_item:
                items.append(owner_item)

        # Pattern 2: "由张三负责" / "由张三处理/做/开发/..."
        match = re.search(
            r"由\s*([A-Za-z0-9_\-一-鿿]{1,20})"
            r"\s*(?:负责|owner|做|处理|开发|测试|实现|设计|修改|跟进|对接|完成|编写|优化)"
            r"\s*([^，。,、；;!\n]{1,20})?",
            text,
        )
        if match:
            name = match.group(1)
            domain_raw = (match.group(2) or "").strip()
            domain = self._slugify(domain_raw) if domain_raw else ""
            if name not in {item.owner for item in items}:
                owner_item = self._build_owner_item(event, text, name, domain)
                if owner_item:
                    items.append(owner_item)

        # Pattern 3: "XXX是这个模块的负责人" (V1.11)
        match = re.search(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*是\S*\s*(?:负责人|owner)",
            text,
        )
        if match:
            name = match.group(1)
            if name not in {item.owner for item in items}:
                owner_item = self._build_owner_item(event, text, name)
                if owner_item:
                    items.append(owner_item)

        # Pattern 4: English "John is the owner of X" (V1.11)
        match = re.search(
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+the\s+owner\s+of\s+(.+)",
            text,
        )
        if match:
            name = match.group(1)
            domain_raw = (match.group(2) or "").strip()
            domain = self._slugify(domain_raw) if domain_raw else ""
            if name not in {item.owner for item in items}:
                owner_item = self._build_owner_item(event, text, name, domain)
                if owner_item:
                    items.append(owner_item)

        # Track names extracted by Pattern 2 to avoid Pattern 5 double-match
        _pat2_names = {item.owner for item in items}

        # Pattern 5: "分工：张三负责前端，李四负责后端" — 多负责人 (V1.11)
        for match in re.finditer(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*负责\s*([^，。,、；;!\n]{1,20})?",
            text,
        ):
            name = match.group(1)
            if name.isascii() and name.islower():
                continue
            # V1.16: 自指代不再跳过，由 _build_owner_item 解析为发送者
            if name in {item.owner for item in items}:
                continue
            if name in _pat2_names:  # V1.15: skip if already extracted by Pattern 2
                continue
            domain_raw = (match.group(2) or "").strip()
            domain = self._slugify(domain_raw) if domain_raw else ""
            owner_item = self._build_owner_item(event, text, name, domain)
            if owner_item:
                items.append(owner_item)

        # Pattern 6: "张三：负责后端引擎" — 文档列表常见格式 (V1.12)
        _non_name_words = frozenset({
            "目标", "决策", "阻塞", "下一步", "截止", "需求", "任务", "方案",
            "项目", "文档", "系统", "模块", "功能", "问题", "风险",
        })
        match = re.search(
            r"([A-Za-z0-9_\-一-鿿]{1,20})\s*[:：]\s*(?:负责|owner|处理|开发|测试|实现|设计|修改|跟进|对接|完成|编写|优化)"
            r"\s*([^，。,、；;!\n]{1,20})?",
            text,
        )
        if match:
            name = match.group(1)
            domain_raw = (match.group(2) or "").strip()
            domain = self._slugify(domain_raw) if domain_raw else ""
            if name not in _non_name_words and name not in {item.owner for item in items}:
                owner_item = self._build_owner_item(event, text, name, domain)
                if owner_item:
                    items.append(owner_item)

        # V1.15: dedup by (owner_name, key)
        seen: set[tuple[str, str]] = set()
        deduped: list[MemoryItem] = []
        for it in items:
            if it is None:
                continue
            sig = (it.current_value, it.key)
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(it)
        items = deduped

        # Guard: no items or no ownership keyword at all → skip
        if not items:
            return items
        if not any(kw in text_lower for kw in ("负责", "负责人", "owner", "由")):
            return []
        return items

    def _resolve_self_reference(self, name: str, event: dict) -> str:
        """V1.16: 将自指代词替换为消息发送者真实姓名。

        bot/app 身份的 sender 无效 → 返回空字符串（调用方跳过）。
        """
        if name in self._SELF_REFERENCE:
            sender = event.get("sender", {}) or {}
            sender_name = str(sender.get("name", ""))
            if (sender_name and sender_name not in self._SELF_REFERENCE
                    and not sender_name.startswith("bot(")
                    and not sender_name.startswith("cli_")):
                return sender_name
            return ""  # bot sender → invalid
        return name

    def _build_owner_item(
        self, event: dict, text: str, owner: str, domain: str = "",
    ) -> MemoryItem:
        """Build a single owner-type MemoryItem.

        V1.15: key 基于 domain 生成，支持多负责人共存。
        Phase A: drop obvious non-person captures and normalise role prefixes.
        """
        owner = self._resolve_self_reference(owner, event)
        if not owner:  # bot sender → skip
            return None
        if not self._is_valid_person_name(owner):
            return None
        owner = self._normalise_person_name(owner)
        # F2: 有职责描述 → 结构化展示；无描述 → 用原文上下文（避免"张三: 张三"）
        if domain:
            current_value = f"{owner}负责{domain}"
            key = f"owner_{domain}"
        else:
            # 取原文中的人名+上下文片段
            idx = text.find(owner)
            ctx = text[max(0, idx-5):idx+len(owner)+30].strip()
            if ctx.strip() == owner:
                return None  # 纯裸名，无上下文，跳过
            current_value = ctx[:120]
            key = self._hash_key(f"owner_{owner}_{text[:40]}")[:16]
        return self._base_item(
            event, text, "owner", key, current_value,
            "Message assigns or updates project responsibility.",
            0.7, owner=owner,
        )

    def _extract_decision(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract key decisions and decision reversals from a message.

        V1.15: 推断 decision_strength，区分讨论/偏好/暂定/确认。
        """
        lowered = text.lower()
        decision_words = (
            "决定", "决策", "确定", "采用", "改为", "不再", "instead", "decide",
            "拍板", "重申", "确认", "废弃", "最终方案",
        )
        if str(event.get("source_type", "")) in ("doc_comment", "meeting"):
            decision_words = (*decision_words, "考虑", "倾向")
        if not any(word in lowered for word in decision_words):
            return []

        # V1.15: 推断决策强度
        strength, confidence = self._infer_decision_strength(text)

        is_override = any(word in lowered for word in ("改为", "不再", "推翻", "instead"))
        key = "current_decision_override" if is_override else self._hash_key(text)

        item = self._base_item(
            event, text, "decision", key, text,
            "Message records a decision that can affect future execution.",
            confidence,
        )
        item.decision_strength = strength
        # 1.2: 试探词 → needs_review
        _tentative = ("可能", "或许", "要不", "考虑考虑", "再说", "先看看")
        if strength != "confirmed" or any(kw in text for kw in _tentative):
            item.review_status = "needs_review"
            item.confidence = min(item.confidence, 0.45)
        return [item]

    @staticmethod
    def _infer_decision_strength(text: str) -> tuple[str, float]:
        """Infer decision strength from signal words.

        Returns (strength, confidence) tuple.
        """
        _SIGNALS = {
            "discussion": (("考虑", "是否", "要不要", "打算", "商量", "讨论一下"), 0.55),
            "preference": (("倾向于", "觉得", "还是", "建议", "推荐"), 0.65),
            "tentative": (("那就先", "暂时", "先这样", "初步", "暂定", "先按"), 0.72),
            "confirmed": (("确定", "就这么定了", "最终方案", "决定", "敲定", "定下来"), 0.85),
        }
        for strength, (signals, conf) in _SIGNALS.items():
            if any(s in text for s in signals):
                return strength, conf
        return "tentative", 0.75

    def _extract_pause(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract paused or deferred work items from a message."""
        deferred_patterns = (
            "暂缓", "暂停", "延期", "先不", "搁置", "先别",
        )
        if not any(word in text for word in deferred_patterns) and not re.search(r"等.{1,10}回来再", text):
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

    # V1.15: 阻塞解除信号——包含这些词的消息描述的是阻塞被解决
    _BLOCKER_RESOLVE_SIGNALS = frozenset({
        "解除", "解决", "通过了", "好了", "完成了", "搞定", "OK了", "没事了", "没问题了",
    })

    def _extract_blocker(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract blockers, risks, or dependency constraints from a message.

        V1.15: Populates metadata with blocker_status and structured fields.
        V1.15: Skips messages that describe blocker resolution.
        """
        lowered = text.lower()
        if not any(word in lowered for word in (
            "阻塞", "卡住", "风险", "依赖", "blocker", "blocked", "risk",
            "跑不动", "等他们", "等它", "还没扩完",
        )):
            return []
        # 过滤站会开场白/回顾性提问 —— "卡住的事"是回顾不是新阻塞
        _standup_opener_signals = (
            "卡住的事", "卡住的事情", "说一下进度", "汇报一下",
            "同步一下", "过一下", "对齐一下",
        )
        if any(s in text for s in _standup_opener_signals):
            return []
        # "不依赖" = 绕过依赖，不是报告阻塞；"依赖注入" = 技术术语
        if "不依赖" in text or "依赖注入" in text:
            return []
        if any(s in text for s in self._BLOCKER_RESOLVE_SIGNALS):
            return []  # 阻塞已解除的消息不提取为新阻塞
        # F3: 过滤列表标题（纯标题无实质内容）
        _blocker_noise = ("阻塞清单", "风险列表", "问题列表", "待办清单",
                         "阻塞项", "风险项", "阻塞汇总", "风险汇总")
        if text.strip() in _blocker_noise:
            return []
        # 1.2: 短文本（<8字符）→ 低置信度 + 待审核，不丢弃
        is_short = len(text.strip()) < 8
        confidence = 0.40 if is_short else 0.70
        item = self._base_item(
            event, text, "blocker", self._hash_key(text), text,
            "Message identifies a blocker, risk, or dependency.", confidence,
        )
        if is_short:
            item.review_status = "needs_review"
        sender_name = ""
        sender = event.get("sender", {}) or {}
        if isinstance(sender, dict):
            sender_name = str(sender.get("name", sender.get("id", "")))
        # 报阻塞的人默认就是被阻塞的人（除非文本中明确指出了其他人）
        if not item.owner and sender_name:
            item.owner = sender_name
        # V1.18: 清理元数据文本中的控制字符
        clean_text = text[:200].replace("\x00", "").replace("\r", " ")
        item.metadata = {
            "blocker_status": "open",
            "blocking_reason": clean_text,
            "blocked_owner": item.owner or "",
            "dependency_owner": "",
            "acknowledged_by": "",
            "resolved_by": "",
            "resolved_at": "",
            "blocked_item": "",
        }
        return [item]

    def _extract_task_source(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract a Feishu task as a next_step memory."""
        if "【任务】" not in text:
            return []
        if any(status in text.lower() for status in ("completed", "done", "已完成")):
            return []

        first_line = text.splitlines()[0].replace("【任务】", "").strip()
        if not first_line:
            return []
        owner = None
        owner_match = re.search(r"负责人\s*[:：]\s*([A-Za-z0-9_\-一-鿿]{1,20})", text)
        if owner_match:
            candidate = owner_match.group(1).strip()
            if self._is_valid_person_name(candidate):
                owner = self._normalise_person_name(candidate)
        value = f"{owner} 需要 {first_line}" if owner else first_line
        item = self._base_item(
            event,
            text,
            "next_step",
            self._hash_key(f"task:{event.get('message_id', first_line)}"),
            value,
            "Feishu task should influence the next action plan.",
            0.76,
            owner=owner,
        )
        item.metadata = {"source_kind": "feishu_task", "task_title": first_line}
        return [item]

    def _extract_calendar_source(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract a Feishu calendar event as a scheduled next step."""
        if "【日程】" not in text:
            return []
        first_line = text.splitlines()[0].replace("【日程】", "").strip()
        if not first_line:
            return []
        owner = None
        owner_match = re.search(r"负责人\s*[:：]\s*([A-Za-z0-9_\-一-鿿]{1,20})", text)
        if owner_match:
            candidate = owner_match.group(1).strip()
            if self._is_valid_person_name(candidate):
                owner = self._normalise_person_name(candidate)
        value = f"日程：{first_line}"
        item = self._base_item(
            event,
            text,
            "next_step",
            self._hash_key(f"calendar:{event.get('message_id', first_line)}"),
            value,
            "Calendar event should influence the next action plan.",
            0.70,
            owner=owner,
        )
        item.metadata = {"source_kind": "calendar_event", "calendar_title": first_line}
        return [item]

    def _extract_approval_status(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract memory from Feishu approval events.

        Approval status is structured machine state, not normal chat text:
        pending keeps the project blocked by an external decision, rejected
        records a negative decision, and approved records both the decision and
        the blocker resolution evidence.
        """
        lowered = text.lower()
        status = ""
        if "pending" in lowered or "审批中" in text or "待审批" in text:
            status = "pending"
        elif "rejected" in lowered or "驳回" in text or "拒绝" in text:
            status = "rejected"
        elif "approved" in lowered or "通过" in text or "已批准" in text:
            status = "approved"
        else:
            return []

        title = text.splitlines()[0].replace("【审批】", "").strip()
        key = self._hash_key(f"approval:{title}")
        items: list[MemoryItem] = []

        if status == "pending":
            blocker = self._base_item(
                event, text, "blocker", f"approval_blocker_{key}",
                f"审批中：{title}",
                "Approval is pending and blocks project progress.",
                0.76,
            )
            blocker.metadata = {
                "blocker_status": "waiting_external",
                "blocking_reason": title,
                "blocked_owner": "",
                "dependency_owner": "审批人",
                "acknowledged_by": "",
                "resolved_by": "",
                "resolved_at": "",
                "blocked_item": title,
                "approval_status": "pending",
            }
            items.append(blocker)

        elif status == "rejected":
            decision = self._base_item(
                event, text, "decision", f"approval_decision_{key}",
                f"审批被驳回：{title}",
                "Approval was rejected; the related plan needs review or resubmission.",
                0.78,
            )
            decision.decision_strength = "confirmed"
            decision.review_status = "needs_review"
            decision.metadata = {"approval_status": "rejected"}
            items.append(decision)

            blocker = self._base_item(
                event, text, "blocker", f"approval_blocker_{key}",
                f"审批被驳回，仍阻塞：{title}",
                "Approval rejection keeps the work blocked until resubmitted.",
                0.74,
            )
            blocker.metadata = {
                "blocker_status": "open",
                "blocking_reason": title,
                "blocked_owner": "",
                "dependency_owner": "申请人",
                "acknowledged_by": "",
                "resolved_by": "",
                "resolved_at": "",
                "blocked_item": title,
                "approval_status": "rejected",
            }
            items.append(blocker)

        elif status == "approved":
            decision = self._base_item(
                event, text, "decision", f"approval_decision_{key}",
                f"审批已通过：{title}",
                "Approval was approved and can unblock downstream work.",
                0.82,
            )
            decision.decision_strength = "confirmed"
            decision.review_status = "auto_approved"
            decision.metadata = {"approval_status": "approved"}
            items.append(decision)

            blocker = self._base_item(
                event, text, "blocker", f"approval_blocker_{key}",
                f"审批通过，阻塞解除：{title}",
                "Approval completion resolves the external blocker.",
                0.78,
            )
            blocker.metadata = {
                "blocker_status": "resolved",
                "blocking_reason": title,
                "blocked_owner": "",
                "dependency_owner": "",
                "acknowledged_by": "",
                "resolved_by": "审批",
                "resolved_at": str(event.get("created_at", "")),
                "blocked_item": title,
                "approval_status": "approved",
            }
            items.append(blocker)

        return items

    def _extract_next_step(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract next-step or task-like statements from a message.

        V1.15: Extracts action subject as owner.
        2.1: 保留原有关键词，由 Hybrid._has_suspicious_rule_items() 过滤误判。
        """
        lowered = text.lower()
        if not any(word in lowered for word in (
            "下一步", "待办", "todo", "需要", "请", "next",
            "今天做", "明天做", "准备做", "开始做", "先做",
        )):
            return []

        # V1.15: 尝试提取动作主语作为owner
        owner = None
        name_match = re.search(
            r"([A-Za-z0-9_一-鿿]{1,20})"
            r"\s*(?:需要|去|来|要|可以|准备|开始|负责|配合)"
            r"\s*(?:做|完成|处理|沟通|设计|开发|写|改|修|部署|测试|跟|和|把)",
            text,
        )
        if name_match:
            candidate = name_match.group(1)
            if self._is_valid_person_name(candidate):
                owner = self._normalise_person_name(candidate)
        if not owner:
            sender = event.get("sender", {}) or {}
            if isinstance(sender, dict):
                raw = str(sender.get("name", sender.get("id", "")))
                # 拒绝包含非人名 token 的 sender（如 "任务负责人"）
                raw_ok = (
                    raw and self._is_valid_person_name(raw)
                    and not any(t in raw for t in self._OWNER_NON_PERSON_TOKENS)
                )
                if raw_ok:
                    owner = self._normalise_person_name(raw) or None

        return [
            self._base_item(
                event, text, "next_step", self._hash_key(text), text,
                "Message suggests work that should influence the next action plan.",
                0.62, owner=owner,
            )
        ]

    def _extract_meeting_action_items(self, event: dict, text: str) -> list[MemoryItem]:
        """Extract individual action items from Feishu Minutes text.

        sync_minutes emits compact text with several lines like
        "待办: 跟运维确认扩容完成 → 测试-张蕾". Each line should become
        an independent next_step memory with its own owner.
        """
        if "待办" not in text:
            return []

        results: list[MemoryItem] = []
        for line in text.splitlines():
            line = line.strip()
            match = re.match(r"待办\s*[:：]\s*(.+?)(?:\s*[→>]\s*(.+))?$", line)
            if not match:
                continue
            action = match.group(1).strip()
            raw_owner = (match.group(2) or "").strip()
            if not action:
                continue
            owner = None
            if raw_owner and self._is_valid_person_name(raw_owner):
                owner = self._normalise_person_name(raw_owner)

            value = f"{owner} 需要 {action}" if owner else action
            item = self._base_item(
                event,
                line,
                "next_step",
                self._hash_key(f"meeting_action:{owner or ''}:{action}"),
                value,
                "Meeting minutes action item should influence the next action plan.",
                0.74,
                owner=owner,
            )
            item.metadata = {
                "source_kind": "meeting_action_item",
                "action_item": action,
            }
            results.append(item)

        return results

    def _extract_member_status(self, event: dict, text: str) -> list[MemoryItem]:
        """V1.12 REAL-1: 提取成员状态信息，支持邻近匹配。

        关键词：请假/不在/休假/出差/习惯用/擅长
        邻近匹配: "请假" 也匹配 "请个假"、"请了假"（允许 1 字间隔）。
        """
        if not any(self._keyword_fuzzy_match(text, kw)
                   for kw in self._MEMBER_STATUS_KEYWORDS):
            return []
        value = self._trim_member_status_value(text)
        # 2.1: 过滤裸名/无实质内容（如"王五""没事""好的"）
        if len(value.strip()) < 2 or value.strip() in ("没事", "好的", "收到", "OK", "行"):
            return []
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
        has_explicit_keyword = any(kw in text for kw in keywords)
        has_implicit_delivery_time = (
            re.search(r"(下?周[一二三四五六日天]|明天|后天|今天|\d+月\d+[日号]|\d{4}-\d{2}-\d{2})", text)
            and any(word in text for word in ("提测", "上线", "发布", "交付", "完成"))
            and str(event.get("source_type", "")) in ("doc", "meeting", "task", "calendar")
        )
        if not has_explicit_keyword and not has_implicit_delivery_time:
            return []
        value = self._trim_deadline_value(text)
        sender = event.get("sender", {}) or {}
        owner = None
        if isinstance(sender, dict):
            raw = str(sender.get("name", sender.get("id", "")))
            raw_ok = (
                raw and self._is_valid_person_name(raw)
                and not any(t in raw for t in self._OWNER_NON_PERSON_TOKENS)
            )
            if raw_ok:
                owner = self._normalise_person_name(raw) or None
        return [
            self._base_item(
                event,
                text,
                "deadline",
                self._hash_key(text),
                value,
                "Message sets or changes a deadline or time constraint.",
                0.68,
                owner=owner,
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

    @staticmethod
    def _keyword_fuzzy_match(text: str, keyword: str) -> bool:
        """V1.12 REAL-1: 允许关键词字符间有 0-1 字间隔的模糊匹配。

        "请假" 匹配 "请个假"、"请了假"、"请一天假"。
        """
        if keyword in text:
            return True
        if len(keyword) < 2:
            return False
        # 关键词每对相邻字符间允许 .{0,2}（0-2 个任意字符）
        pattern = ".{0,2}".join(keyword)
        return bool(re.search(pattern, text))

    @staticmethod
    def _slugify(domain: str) -> str:
        """Convert a domain description to a clean key-safe slug."""
        import re as _re
        slug = domain.strip()
        slug = _re.sub(r"[^\w一-鿿]", "_", slug)
        slug = _re.sub(r"_+", "_", slug).strip("_")
        return slug[:30] if slug else ""

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
        # V1.17: 有 LLM 时才启用 Selector 模式；无 LLM 时退化为全量提取
        if self.llm is not None:
            self.rule.selector_mode = True
        # Phase D: cumulative LLM call counter exposed for benchmarks.
        self.llm_call_count = 0
        self.llm_total_seconds = 0.0

    def extract(self, events: Iterable[dict]) -> list[MemoryItem]:
        """V1.17: Selector模式——规则提取有把握的，没把握的交给LLM。"""
        event_list = list(events)
        if not event_list:
            return []

        # Step 1: 规则提取（delegate_list 存在 self.rule._delegate_list 中）
        rule_items = self.rule.extract(event_list)
        delegate_list = getattr(self.rule, "_delegate_list", [])

        # Step 2: delegate_list + 可疑事件 → LLM 校验
        llm_items = []
        suspicious_ids: set[str] = set()
        if self.llm is not None:
            suspicious_ids = self._get_suspicious_message_ids(rule_items)
            # 构建 LLM 批次：delegate事件 + 可疑事件（去重）
            llm_batch_ids = {ev.get("message_id", "") for ev in (delegate_list or [])}
            llm_batch_ids |= suspicious_ids
            if llm_batch_ids:
                llm_batch = [ev for ev in event_list
                            if ev.get("message_id", "") in llm_batch_ids]
                if llm_batch:
                    llm_items = self._safe_llm_extract(llm_batch)
            # 2.2: 用 LLM 结果替换可疑事件对应的 RuleOnly 条目
            if suspicious_ids:
                rule_items = [i for i in rule_items
                             if not (i.source_refs
                                     and i.source_refs[0].message_id in suspicious_ids)]
            # LLM 空返回时回退：用规则重新提取 delegate 项
            if not llm_items and delegate_list:
                saved_mode = getattr(self.rule, "selector_mode", False)
                self.rule.selector_mode = False
                for ev in delegate_list:
                    rule_items.extend(self.rule.extract([ev]))
                self.rule.selector_mode = saved_mode

        # V1.17: 保留旧逻辑——规则完全没提取时也调LLM（空消息/无信号场景）
        if not rule_items and not llm_items and self.llm is not None:
            llm_items = self._safe_llm_extract(event_list)

        if not rule_items and not llm_items:
            return []

        if not llm_items:
            return rule_items

        # Step 3: 合并规则结果与 LLM 结果（去重）
        merged = self._merge_results(rule_items, llm_items)

        # V1.11: 二次兜底 — LLM 和规则都几乎没提取，但触发了 LLM
        if not merged and self._needs_llm(rule_items, event_list):
            text_sample = str(
                event_list[0].get("text", "") if event_list else ""
            )[:80]
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Hybrid 触发但规则和 LLM 均未提取协作信号: %s", text_sample
            )

        return merged

    def _get_suspicious_message_ids(self, rule_items: list[MemoryItem]) -> set[str]:
        """2.2: 返回可疑条目的 source message_id 集合。

        可疑信号：
        - blocker 文本 > 120 字（可能是 PM 汇报）
        - member_status 是裸名（≤4 字）
        - next_step 含生活/请假关键词
        - decision 含总结/提醒类用语
        """
        ids: set[str] = set()
        if not rule_items:
            return ids
        for item in rule_items:
            val = item.current_value or ""
            suspicious = False
            if item.state_type == "blocker" and len(val) > 120:
                suspicious = True
            elif item.state_type == "member_status" and len(val.strip()) < 5:
                suspicious = True
            elif item.state_type == "next_step":
                _casual = ("吃饭", "下午茶", "请假", "休息", "周末", "老板请客",
                          "奶茶", "火锅", "下班", "打游戏")
                if any(kw in val for kw in _casual):
                    suspicious = True
            elif item.state_type == "decision":
                _summary = ("总结", "同步一下", "汇报", "过一下", "对齐", "站会")
                if any(kw in val for kw in _summary):
                    suspicious = True
            if suspicious and item.source_refs:
                ids.add(item.source_refs[0].message_id)
        return ids

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

        # (h) V1.15: 规则 item 内部存在"信任冲突"信号 → LLM 二次判断
        for item in rule_items:
            text = item.current_value if item.current_value else ""
            if item.state_type == "owner":
                if item.owner in ("我", "你", "他", "她", "我们", "他们", "大家"):
                    return True
                if len(item.owner) == 1 and item.owner not in ("张", "王", "李",
                        "赵", "刘", "陈", "杨", "黄", "吴", "周"):
                    return True  # 单字且非姓氏 → 可能是误提取
            if item.state_type == "blocker":
                _conflict = ("解除", "解决", "通过了", "好了", "完成了",
                            "搞定", "OK了", "不阻塞", "但是", "但", "虽然", "不过")
                if any(w in text for w in _conflict):
                    return True
            if item.state_type == "decision":
                _vague = ("不确定", "可能", "也许", "看看", "试试", "再说")
                if any(w in text for w in _vague):
                    return True
            if item.confidence > 0.7:
                _neg_question = ("不确定", "不认为", "没觉得", "是真的吗",
                                 "对吗", "吗？", "？")
                if any(w in text for w in _neg_question):
                    return True

        return False

    def _safe_llm_extract(self, events: list[dict]) -> list[MemoryItem]:
        """安全调用 LLM 提取，异常时返回空列表（不 fallback 到规则，避免循环）。

        LLM 输出经过完整 schema 校验（validate_candidate_dict），
        校验失败时返回空列表，由调用方决定如何处理。
        """
        import time as _time
        self.llm_call_count += 1
        started = _time.time()
        try:
            return self.llm.extract(events)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "LLM extraction failed, falling back to rules: %s", e)
            return []
        finally:
            self.llm_total_seconds += _time.time() - started

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
