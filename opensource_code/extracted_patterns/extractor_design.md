# Extractor Design — 可借鉴模式

来源：基于 agent-memory-server, mem0, graphiti, langmem 的提取策略分析。

---

## 1. Extractor 抽象接口

**推荐设计**：

```python
from abc import ABC, abstractmethod
from typing import Any

class BaseExtractor(ABC):
    """Memory extraction abstraction."""
    
    @abstractmethod
    async def extract(self, events: list[dict]) -> list[MemoryCandidate]:
        """Extract memory candidates from raw events."""
        pass
    
    @abstractmethod
    def get_description(self) -> str:
        """Return description of extraction strategy for MCP tool."""
        pass


class LLMExtractor(BaseExtractor):
    """LLM-based extraction with schema validation."""
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        schema: type[MemoryCandidate],
        prompt_template: str,
    ):
        self.llm = llm_provider
        self.schema = schema
        self.prompt = prompt_template
    
    async def extract(self, events: list[dict]) -> list[MemoryCandidate]:
        # 1. Format prompt with events
        # 2. Call LLM with JSON schema
        # 3. Validate output against schema
        # 4. Return validated candidates
        pass


class RuleBasedExtractor(BaseExtractor):
    """Rule-based fallback extraction."""
    
    async def extract(self, events: list[dict]) -> list[MemoryCandidate]:
        # Pattern matching, keyword extraction, etc.
        pass
```

**借鉴来源**：
- `openclaw-memory/src/memory/extractor.py` — 当前项目已有 `BaseExtractor`, `RuleBasedExtractor`, `LLMExtractor`
- `agent-memory-server/memory_strategies.py` — `BaseMemoryStrategy` + 4 种具体策略
- `langmem/knowledge/extraction.py` — TrustCall 驱动的结构化提取

---

## 2. LLM Prompt 设计模式

### 2.1 上下文 Grounding（强烈推荐 agent-memory-server）

**核心 Prompt 设计**（`agent-memory-server/memory_strategies.py` 的 `DiscreteMemoryStrategy.EXTRACTION_PROMPT`）：

```python
EXTRACTION_PROMPT = """
You are a long-memory manager. Your job is to analyze text and extract
information that might be useful in future conversations with users.

CURRENT CONTEXT:
Current date and time: {current_datetime}

Extract two types of memories:
1. EPISODIC: Memories about specific episodes in time.
   Example: "User had a bad experience on a flight to Paris in 2024"

2. SEMANTIC: User preferences and general knowledge.
   Example: "User prefers window seats when flying"

CONTEXTUAL GROUNDING REQUIREMENTS:

1. PRONOUNS: Replace ALL pronouns (he/she/they/him/her/them) with the actual 
   person's name, EXCEPT for the application user, who must always be referred 
   to as "User".
   - "He loves coffee" → "User loves coffee"
   - "I told her about it" → "User told colleague about it"

2. TEMPORAL REFERENCES: Convert relative time to absolute dates.
   - "yesterday" → "March 15, 2025" (if current date is March 16, 2025)
   - "last year" → "2024"
   - "three months ago" → "December 2024"

3. SPATIAL REFERENCES: Resolve place references.
   - "there" → "San Francisco"
   - "that place" → "Chez Panisse restaurant"

4. DEFINITE REFERENCES: Resolve definite articles.
   - "the meeting" → "the quarterly planning meeting"
   - "the document" → "the budget proposal document"

For each memory, return a JSON object:
{{
    "type": "episodic" | "semantic",
    "text": "...",
    "topics": ["..."],
    "entities": ["..."],
    "event_date": "ISO8601" | null
}}

Return format:
{{
    "memories": [...]
}}

IMPORTANT RULES:
1. Only extract genuinely useful information.
2. Do not extract procedural knowledge.
3. ALWAYS ground ALL contextual references.
4. If you cannot determine what a pronoun refers to, omit that memory.

Message:
{{message}}

Extracted memories:
"""
```

**关键设计点**：
1. **代词解析**：强制替换为具体人名（"User" 或具体角色）
2. **时间解析**：相对时间 → 绝对日期
3. **空间解析**：模糊地点 → 具体位置
4. **定指解析**："the X" → 具体 X
5. **Few-shot 示例**：给出清晰的输入/输出示例

**借鉴来源**：
- `agent-memory-server/memory_strategies.py` — `DiscreteMemoryStrategy.EXTRACTION_PROMPT`（完整实现）
- `mem0/configs/prompts.py` — `ADDITIVE_EXTRACTION_PROMPT`（475 行，ADD-only 策略）

---

### 2.2 ADD-only 策略（推荐 mem0）

**核心思想**：LLM 只做 ADD 操作，不做 UPDATE/DELETE。冲突由下游去重逻辑处理。

```python
ADDITIVE_EXTRACTION_PROMPT = """
You are an additive memory extractor. Your task is to NEW facts from the conversation.

IMPORTANT:
- Only ADD new facts. Do NOT UPDATE or DELETE existing memories.
- If you see conflicting information, ADD both as separate memories.
- Downstream logic will handle deduplication.

Output format:
{
    "memory": [
        {
            "id": "0",
            "text": "User prefers dark mode",
            "attributed_to": "user",
            "linked_memory_ids": []
        }
    ]
}
"""
```

**优势**：
- 简化 LLM 交互（单一操作）
- 降低幻觉风险（不需要判断是否冲突）
- 保留历史完整性

**借鉴来源**：
- `mem0/configs/prompts.py` — `ADDITIVE_EXTRACTION_PROMPT`

---

### 2.3 结构化输出（推荐 langmem 的 TrustCall）

**TrustCall 模式**（`langmem/knowledge/extraction.py`）：

```python
from pydantic import BaseModel, Field
from trustcall import create_extractor

class MemoryCandidate(BaseModel):
    state_type: str = Field(description="Type of memory state")
    key: str = Field(description="Stable key for identity")
    current_value: str
    rationale: str
    owner: str | None
    status: str
    confidence: float
    source_refs: list[SourceRef]

extractor = create_extractor(
    llm,
    tools=[
        save_memory,  # LLM 调用工具写入
    ],
    response_model=MemoryCandidate,  # 强制结构化输出
)
```

**优势**：
- Pydantic schema 强制验证
- 比纯 JSON Prompt 更可靠
- 支持工具调用（function calling）

**借鉴来源**：
- `langmem/knowledge/extraction.py` — TrustCall 驱动的结构化提取

---

## 3. Schema 验证设计

**推荐设计**（当前项目已有 + langmem 增强）：

```python
from pydantic import BaseModel, field_validator

class MemoryCandidate(BaseModel):
    project_id: str
    state_type: str
    key: str
    current_value: str
    rationale: str
    owner: str | None
    status: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: list[SourceRef]
    detected_at: str
    
    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, v):
        if not v or len(v) == 0:
            raise ValueError("source_refs must be non-empty")
        return v
    
    @field_validator("state_type")
    @classmethod
    def validate_state_type(cls, v):
        allowed = {"goal", "task", "decision", "preference", "blocker", "risk"}
        if v not in allowed:
            raise ValueError(f"state_type must be one of {allowed}")
        return v
```

**LLM 输出验证流程**：

```python
async def extract_and_validate(events: list[dict]) -> list[MemoryCandidate]:
    # 1. Call LLM
    response = await llm(prompt, response_format={"type": "json_object"})
    
    # 2. Parse JSON
    try:
        data = json.loads(response.content)
    except json.JSONDecodeError:
        # Fallback to rule-based
        return rule_extractor.extract(events)
    
    # 3. Validate each candidate
    candidates = []
    for item in data.get("candidates", []):
        try:
            candidate = MemoryCandidate(**item)
            candidates.append(candidate)
        except ValidationError as e:
            logger.warning(f"Validation failed: {e}")
            continue  # Skip invalid, continue with rest
    
    # 4. Return validated
    return candidates
```

**借鉴来源**：
- `openclaw-memory/src/memory/schema.py` — 当前项目的 `MemoryItem`, `SourceRef`
- `langmem/knowledge/extraction.py` — TrustCall 的 Pydantic 验证
- `agent-memory-server/models.py` — Pydantic 模型 + 自定义验证器

---

## 4. 提取 Pipeline 设计

**推荐流程**（综合 mem0 V3 + agent-memory-server）：

```
Raw Events
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 0: Fetch Context              │
│ - Get last N messages for context   │
│ - Get recently extracted memories   │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 1: Retrieve for Dedup         │
│ - Vector search existing memories   │
│ - Pass to LLM as reference          │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 2: LLM Extraction             │
│ - Single call with ADD-only prompt  │
│ - Extract candidates + source refs  │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 3: Batch Embed                │
│ - Embed all candidate texts         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 4: Dedup (Hash + Semantic)    │
│ - MD5 hash dedup                    │
│ - Vector similarity dedup           │
│ - LLM merge for conflicts           │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 5: Entity Linking             │
│ - NER extract entities              │
│ - Match to entity store (>=0.95)    │
│ - Merge linked_memory_ids           │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Phase 6: Write to Store             │
│ - Batch write to vector store       │
│ - Save raw events to SQLite         │
└─────────────────────────────────────┘
```

**借鉴来源**：
- `mem0/memory/main.py` — V3 Phased Batch Pipeline（Phase 0-8）
- `agent-memory-server/extraction.py` — extraction pipeline + debounce

---

## 5. Debounce 提取设计（推荐 agent-memory-server）

**问题**：消息流太频繁时，避免重复触发提取。

**解决方案**：Trailing-edge debounce。

```python
EXTRACTION_DEBOUNCE_SECONDS = 60  # 1 分钟

async def schedule_trailing_extraction(session_id: str):
    """Schedule extraction after debounce period."""
    redis = get_redis()
    
    # Set pending key with TTL
    pending_key = f"extraction_pending:{session_id}"
    extraction_time = datetime.now() + timedelta(seconds=DEBOUNCE_SECONDS)
    await redis.setex(pending_key, DEBOUNCE_SECONDS * 2, extraction_time.isoformat())
    
    # Schedule task
    await docket.add(
        run_delayed_extraction,
        when=extraction_time,
        key=f"extraction:{session_id}:{extraction_time}",
    )(session_id=session_id)


async def run_delayed_extraction(session_id: str, scheduled_timestamp: str):
    """Run extraction if still valid (not superseded)."""
    redis = get_redis()
    pending_key = f"extraction_pending:{session_id}"
    
    # Check if superseded
    current_pending = await redis.get(pending_key)
    if current_pending != scheduled_timestamp:
        logger.info(f"Skipping extraction - superseded")
        return 0
    
    # Proceed with extraction
    working_memory = await get_working_memory(session_id)
    unextracted = [m for m in working_memory.messages if not m.extracted]
    
    if not unextracted:
        return 0
    
    # Extract
    memories = await extract_memories(unextracted)
    
    # Mark all as extracted
    for msg in working_memory.messages:
        msg.extracted = True
    await save_working_memory(working_memory)
    
    # Set post-extraction debounce
    await redis.setex(f"extraction_debounce:{session_id}", 300, "1")
    
    # Clear pending
    await redis.delete(pending_key)
    
    return len(memories)
```

**优势**：
- 避免频繁 LLM 调用
- 累积更多上下文再提取
- 新消息自动重置计时器

**借鉴来源**：
- `agent-memory-server/long_term_memory.py` — `schedule_trailing_extraction`, `run_delayed_extraction`
- `agent-memory-server/extraction.py` — debounce 逻辑

---

## 6. 多策略提取（推荐 agent-memory-server）

**4 种策略**：

| 策略 | 用途 | Prompt 重点 |
|------|------|-------------|
| Discrete | 提取独立事实 | 代词/时间/空间 grounding |
| Summary | 对话摘要 | 压缩到 N 词，保留关键决策 |
| Preferences | 用户偏好 | 聚焦 likes/dislikes/settings |
| Custom | 自定义 | 用户提供 Prompt，有安全校验 |

**策略工厂**：

```python
MEMORY_STRATEGIES = {
    "discrete": DiscreteMemoryStrategy,
    "summary": SummaryMemoryStrategy,
    "preferences": UserPreferencesMemoryStrategy,
    "custom": CustomMemoryStrategy,
}

def get_memory_strategy(name: str, **kwargs) -> BaseMemoryStrategy:
    if name not in MEMORY_STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}")
    return MEMORY_STRATEGIES[name](**kwargs)
```

**借鉴来源**：
- `agent-memory-server/memory_strategies.py` — 4 种策略完整实现
- `agent-memory-server/models.py` — `MemoryStrategyConfig` Pydantic 模型

---

## 7. 推荐飞书 Memory Engine 的 Extractor 设计

```python
class FeishuExtractor:
    """飞书协作场景的提取器。"""
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        scope: str = "chat",  # chat/doc/meeting/task
    ):
        self.llm = llm_provider
        self.scope = scope
        self.prompt = self._build_prompt_for_scope(scope)
    
    def _build_prompt_for_scope(self, scope: str) -> str:
        """根据作用域构建 Prompt。"""
        if scope == "chat":
            return CHAT_EXTRACTION_PROMPT
        elif scope == "doc":
            return DOC_EXTRACTION_PROMPT
        elif scope == "meeting":
            return MEETING_EXTRACTION_PROMPT
        elif scope == "task":
            return TASK_EXTRACTION_PROMPT
        else:
            raise ValueError(f"Unknown scope: {scope}")
    
    async def extract(
        self, 
        events: list[dict],
        strategy: str = "discrete",
    ) -> list[MemoryCandidate]:
        """Extract memories from Feishu events."""
        
        # 1. Get strategy
        extractor = get_memory_strategy(strategy)
        
        # 2. Format events for prompt
        formatted = self._format_events_for_scope(events)
        
        # 3. Call LLM
        response = await self.llm(
            self.prompt.format(message=formatted),
            response_format={"type": "json_object"},
        )
        
        # 4. Parse and validate
        data = json.loads(response.content)
        candidates = []
        for item in data.get("memories", []):
            try:
                candidate = MemoryCandidate(**item)
                candidates.append(candidate)
            except ValidationError as e:
                logger.warning(f"Validation failed: {e}")
                continue
        
        return candidates
    
    def _format_events_for_scope(self, events: list[dict]) -> str:
        """根据作用域格式化事件。"""
        if self.scope == "chat":
            return "\n".join([e.get("text", "") for e in events])
        elif self.scope == "doc":
            # Concatenate doc blocks
            return "\n---\n".join([e.get("content", "") for e in events])
        elif self.scope == "meeting":
            # Format meeting transcript
            return self._format_transcript(events)
        elif self.scope == "task":
            # Format task description + comments
            return self._format_task_context(events)
        else:
            raise ValueError(f"Unknown scope: {self.scope}")
```

**关键设计**：
1. **作用域感知 Prompt**：chat/doc/meeting/task 不同场景用不同 Prompt
2. **上下文 Grounding**：代词/时间/空间解析
3. **Schema 验证**：Pydantic 强制验证
4. **Fallback**：LLM 失败时回退到规则提取
5. **Debounce**：避免频繁触发

**借鉴来源**：
- 当前项目 `openclaw-memory/src/memory/extractor.py`
- `agent-memory-server/memory_strategies.py`
- `mem0/memory/main.py`
