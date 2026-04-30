# Memory API Design — 可借鉴模式

来源：基于 mem0, graphiti, agent-memory-server, cognee, OpenMemory 5 个项目的对比分析。

---

## 1. MemoryStore 接口抽象

**推荐设计**（综合 cognee 的 DataPoint + agent-memory-server 的 MemoryRecord）：

```python
class MemoryStore(Protocol):
    """Memory storage abstraction."""
    
    # CRUD
    def create(self, memory: MemoryItem) -> str:  # returns memory_id
    def get(self, memory_id: str) -> MemoryItem | None
    def update(self, memory_id: str, patches: dict) -> MemoryItem
    def delete(self, memory_id: str) -> bool
    
    # Query
    def list(self, filters: dict, limit: int, offset: int) -> list[MemoryItem]
    def search(self, query: str, filters: dict, top_k: int) -> list[ScoredMemory]
    
    # Lifecycle
    def forget(self, policy: ForgettingPolicy) -> int  # returns count of deleted
    def history(self, memory_id: str) -> list[MemoryEvent]
```

**借鉴来源**：
- `mem0/memory/base.py` — `MemoryBase` 抽象基类
- `agent-memory-server/models.py` — `MemoryRecord` 完整字段设计
- `cognee/memory/entries.py` — `DataPoint` 基类 + 多态条目

---

## 2. MemoryItem 数据模型

**推荐字段**（综合各家的最完整设计）：

```python
@dataclass
class MemoryItem:
    # === 核心身份 ===
    memory_id: str           # UUID
    identity_key: str        # 稳定键：project_id:state_type:key（用于去重/合并）
    
    # === 内容 ===
    state_type: str          # 记忆类型：goal/task/decision/preference/blocker 等
    key: str                 # 助记键：如 "api-rate-limit"
    current_value: str       # 当前值
    rationale: str           # 为什么这条记忆重要
    
    # === 作用域 ===
    project_id: str          # 项目/文档/群组 ID
    scope_type: str          # 作用域类型：document/chat/meeting/task
    owner: str | None        # 负责人
    user_id: str | None      # 相关用户
    
    # === 状态 ===
    status: str              # active/resolved/blocked/superseded
    confidence: float        # 置信度 0-1
    version: int             # 版本号
    supersedes: list[str]    # 被替代的 memory_id 列表
    
    # === 溯源（Provenance）===
    source_refs: list[SourceRef]  # 原始消息/文档锚点
    extracted_from: list[str]     # 消息 ID 列表
    created_at: str          # ISO timestamp
    updated_at: str          # ISO timestamp
    
    # === 扩展 ===
    metadata: dict           # 任意扩展字段
```

**借鉴来源**：
- `openclaw-memory/src/memory/schema.py` — 当前项目已有的 `MemoryItem` + `SourceRef`
- `graphiti_core/edges.py` — `EntityEdge` 的 bi-temporal 字段（`valid_at`, `invalid_at`）
- `agent-memory-server/models.py` — `MemoryRecord` 的 4 个时间戳 + `memory_hash`
- `cognee/memory/entries.py` — `DataPoint` 的 `source_pipeline`, `source_task`, `version`

---

## 3. Metadata 和 Scope 设计

**三级 Scope 体系**（推荐 mem0 + agent-memory-server 混合）：

```
Tenant (租户/组织)
  └── Project/Space (项目/空间/文档集)
      └── Session/Channel (会话/群组/单聊)
          └── User (个人)
```

**Filter 抽象**（借鉴 agent-memory-server 的过滤器层次）：

```python
@dataclass
class ScopeFilter:
    tenant_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None

@dataclass
class TypeFilter:
    state_types: list[str] | None = None  # 记忆类型过滤
    statuses: list[str] | None = None     # 状态过滤

@dataclass
class TimeFilter:
    created_after: str | None = None
    created_before: str | None = None
    updated_after: str | None = None
    event_date_range: tuple[str, str] | None = None  # (start, end)

@dataclass
class ContentFilter:
    topics: list[str] | None = None      # 标签匹配
    entities: list[str] | None = None    # 实体匹配
    keyword: str | None = None           # 全文搜索
```

**借鉴来源**：
- `mem0/configs/base.py` — `user_id`, `agent_id`, `run_id` 三级
- `agent-memory-server/models.py` — `SearchRequest` 的完整过滤器字段
- `agent-memory-server/filters.py` — `SessionId`, `Namespace`, `Topics`, `Entities`, `CreatedAt` 等过滤器类型

---

## 4. SourceRef / Provenance 设计

**推荐设计**（当前项目已有 + graphiti 增强）：

```python
@dataclass
class SourceRef:
    """Evidence anchor pointing back to the original Feishu message/document."""
    
    type: str              # "message" | "doc" | "meeting" | "task"
    chat_id: str           # 群组/单聊 ID
    message_id: str        # 消息 ID（消息类型必填）
    doc_id: str | None     # 文档 ID（文档类型必填）
    block_id: str | None   # 文档块 ID（可选）
    excerpt: str           # 原文片段（前 240 字符）
    created_at: str        # 原始消息/文档时间
    author_id: str | None  # 发言者/作者 ID
```

**借鉴来源**：
- `openclaw-memory/src/memory/schema.py` — 当前项目已有 `SourceRef`
- `graphiti_core/nodes.py` — `EpisodicNode` 的 `content` + `valid_at` + `source`
- `cognee/memory/entries.py` — `DataPoint` 的 `source_pipeline`, `source_task`, `source_user`

---

## 5. 时间处理设计

**Bi-temporal 模型**（强烈推荐 graphiti）：

```python
@dataclass
class MemoryItem:
    # ... 其他字段 ...
    
    # Bi-temporal 字段（graphiti 的核心创新）
    valid_at: str | None      # 事实何时变为 true
    invalid_at: str | None    # 事实何时停止为 true（被替代/推翻）
    expired_at: str | None    # 事实何时过期（TTL）
    
    # 标准时间戳
    created_at: str           # 入库时间
    updated_at: str           # 最后更新时间
```

**时间感知查询**：

```python
def query_at_time(memory_store, point_in_time: str) -> list[MemoryItem]:
    """查询某个时间点的有效记忆。"""
    return memory_store.list(
        filters={
            "valid_at_lte": point_in_time,
            "invalid_at_is_null": True,  # 或者 invalid_at_gt: point_in_time
        }
    )
```

**借鉴来源**：
- `graphiti_core/edges.py` — `EntityEdge` 的 `valid_at`, `invalid_at`, `expired_at`, `reference_time`
- `agent-memory-server/models.py` — 4 个时间戳设计

---

## 6. 配置管理设计

**推荐**（借鉴 mem0 的 Pydantic 配置）：

```python
from pydantic import BaseModel, Field

class MemoryConfig(BaseModel):
    """Memory engine configuration."""
    
    # Storage
    data_dir: str = "./data"
    vector_store: str = "qdrant"  # or "lancedb", "chromadb"
    
    # Scope
    default_tenant_id: str | None = None
    default_project_id: str | None = None
    
    # Extraction
    llm_provider: str = "anthropic"
    extraction_model: str = "claude-sonnet-4-6"
    add_only: bool = True  # ADD-only 模式，避免自动更新/删除
    
    # Forgetting
    enable_auto_forget: bool = False
    default_ttl_days: int | None = None
    
    # Search
    default_top_k: int = 10
    hybrid_alpha: float = 0.7  # 混合搜索权重
```

**借鉴来源**：
- `mem0/configs/base.py` — `MemoryConfig` Pydantic 模型
- `agent-memory-server/config.py` — settings 管理

---

## 7. 推荐飞书 Memory Engine 的 API 设计

**核心 API**（参考 cognee 的四动词 + mem0 的 scope）：

```python
class MemoryEngine:
    # === 写入 ===
    def remember(self, data: dict, scope: ScopeFilter) -> str
    def update(self, memory_id: str, patches: dict) -> MemoryItem
    def delete(self, memory_id: str) -> bool
    def forget(self, policy: ForgettingPolicy) -> int
    
    # === 检索 ===
    def recall(self, query: str | None, filters: dict, top_k: int) -> list[ScoredMemory]
    def get(self, memory_id: str) -> MemoryItem
    def list(self, filters: dict) -> list[MemoryItem]
    
    # === 审计 ===
    def history(self, memory_id: str) -> list[MemoryEvent]
    def query_at_time(self, point_in_time: str, filters: dict) -> list[MemoryItem]
    
    # === 工具（Handoff 用）===
    def generate_handoff_summary(self, project_id: str) -> str
    def generate_action_plan(self, project_id: str) -> list[ActionItem]
```

**借鉴来源**：
- `cognee/__init__.py` — `remember`, `recall`, `forget`, `improve`
- `mem0/memory/main.py` — `add`, `search`, `get`, `get_all`, `delete`, `history`
