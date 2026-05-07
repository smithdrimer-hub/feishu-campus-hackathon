# agent-memory-server - 快速扫描

## 1. 项目定位

Redis Agent Memory Server (redis/agent-memory-server) 是一个为 AI Agent 提供的**记忆层服务**，采用服务端架构（REST API + MCP Server），通过 Redis 作为后端存储。核心定位：让 AI Agent 拥有会话级（Working Memory）和持久化（Long-term Memory）的双层记忆能力，自动从对话中提取/去重/遗忘记忆。

## 2. 主要语言和技术栈

- **语言**: Python
- **框架**: FastAPI（REST API）、FastMCP（MCP 协议）
- **核心依赖**:
  - **Redis + RedisVL** — 向量搜索（HNSW/FLAT）、全文搜索（RediSearch）、JSON 存储
  - **LiteLLM** — 统一 LLM/Embedding provider 抽象（支持 100+ 模型）
  - **BERTopic** — 主题提取（可选）
  - **transformers** — NER（可选）
- **客户端 SDK**: Python, JavaScript, Java
- **协议**: REST API + MCP

## 3. 顶层目录结构

| 目录/文件 | 用途 |
|---|---|
| `agent_memory_server/` | 核心服务端源码 |
| `agent-memory-client/` | Python/JS/Java 客户端 SDK |
| `agent_memory_server/api.py` | FastAPI REST API 路由 |
| `agent_memory_server/mcp.py` | MCP Server 实现 |
| `agent_memory_server/working_memory.py` | 工作记忆（会话级）管理 |
| `agent_memory_server/long_term_memory.py` | 长期记忆管理 |
| `agent_memory_server/memory_strategies.py` | 记忆提取策略 |
| `agent_memory_server/extraction.py` | 记忆提取管道 |
| `agent_memory_server/memory_vector_db.py` | 向量数据库抽象层 |
| `agent_memory_server/models.py` | Pydantic 数据模型 |
| `agent_memory_server/llm/` | LLM 客户端与 Embedding 封装 |
| `tests/` | 测试套件 |

## 4. README 中对 Memory 的定义

**双层架构**：Working Memory (Session-scoped) → Long-term Memory (Persistent)

- **Working Memory**: 会话级临时记忆，包含消息、结构化记忆、摘要、元数据
- **Long-term Memory**: 持久化记忆，支持语义/关键词/混合搜索、去重、遗忘

记忆类型：Semantic（用户偏好和通用知识）、Episodic（带时间维度的事件）、Message（原始对话消息）

## 5. 关键源码路径

| # | 文件路径 | 重要性说明 |
|---|----------|---|
| 1 | `agent_memory_server/models.py` | 核心数据模型：MemoryRecord, WorkingMemory, MemoryTypeEnum |
| 2 | `agent_memory_server/memory_strategies.py` | 4 种提取策略含 Prompt |
| 3 | `agent_memory_server/long_term_memory.py` | 长期记忆 CRUD、搜索、去重、遗忘 |
| 4 | `agent_memory_server/working_memory.py` | 工作记忆 get/set/delete、TTL |
| 5 | `agent_memory_server/extraction.py` | 提取管道：debounce、thread-aware、LLM 合并 |
| 6 | `agent_memory_server/memory_vector_db.py` | 向量数据库抽象接口 |
| 7 | `agent_memory_server/memory_vector_db_factory.py` | 可插拔工厂模式 |
| 8 | `agent_memory_server/filters.py` | 搜索过滤器封装 |

## 6. 是否值得深入阅读

**Yes — 强烈推荐。**

理由：提取策略 Prompt 设计精良，hash/semantic/LLM merge 三层去重，遗忘策略实用，工厂模式优雅，生产就绪。

## 7. 对飞书协作 Memory Engine 的潜在价值

**High**

| 维度 | 价值分析 |
|---|---|
| Multi-scope support | `namespace` + `user_id` + `session_id` 三级作用域，可映射飞书 doc_space/user/chat |
| Auditability | 四个时间戳 + `memory_hash`，但无完整审计日志 |
| LLM extraction patterns | **核心价值**：4 种策略 + 上下文 grounding Prompt 非常成熟 |
| Adapter patterns | `memory_vector_db_factory.py` 工厂模式 + LiteLLM 多 provider |
| Provenance tracking | 基础级：`session_id`、`user_id` 可追溯来源会话 |
| 额外亮点 | 对话摘要、摘要视图、遗忘策略、MCP 协议集成 |

## 深度分析（10 维度）

### 1. 信息如何进入 memory

**API 入口**：`MemoryMessage` → `WorkingMemory` → `extract_memories_from_session_thread()` → `MemoryRecord` → Long-term。

**流程**：
1. 消息写入 WorkingMemory（会话级缓存）
2. Trailing-edge debounce 触发提取（避免消息流太频繁重复提取）
3. LLM 提取记忆（4 种策略可选）
4. 三层去重（ID + hash + semantic）
5. 写入长期记忆（Redis）

### 2. 是否经过 LLM 抽取

**是的，4 种策略**（`memory_strategies.py`）：
- **DiscreteMemoryStrategy**：提取 episodic/semantic 事实，有详细的代词/时间/空间 grounding Prompt
- **SummaryMemoryStrategy**：对话摘要
- **UserPreferencesMemoryStrategy**：用户偏好提取
- **CustomMemoryStrategy**：自定义 Prompt，有安全校验

输出格式：`{"memories": [{"type": "episodic"|"semantic", "text": "...", "topics": [...], "entities": [...], "event_date": "..." | null}]}`

### 3. 数据结构

**MemoryRecord**（`models.py`）：
```python
id, text, session_id, user_id, namespace,
last_accessed, created_at, updated_at, pinned,
access_count, topics, entities, memory_hash,
memory_type, extracted_from, event_date,
extraction_strategy, extraction_strategy_config
```

**MemoryMessage**：`role, content, id, created_at, persisted_at, discrete_memory_extracted`

**WorkingMemory**：`messages[], memories[], context, user_id, session_id, namespace, long_term_memory_strategy`

### 4. 新增、更新、删除、遗忘

- **新增**：`create_long_term_memory()` API
- **更新**：`edit_long_term_memory()` + recency re-ranking
- **删除**：`delete_long_term_memory(ids)`
- **遗忘**：`forget()` 策略（TTL + inactivity + budget + pinning）

### 5. 重复、冲突、时间变化、过期

- **去重**：三层 - ID 去重 + MD5 hash + 语义向量相似度（LLM merge）
- **冲突**：语义去重时 LLM 判断是否合并
- **时间**：4 个时间戳（created_at, updated_at, last_accessed, persisted_at）
- **过期**：Policy-based forgetting（max_age_days, max_inactive_days, budget, pinned 保护）

### 6. 检索

**SearchRequest**（`models.py`）：
- **模式**：Semantic / Keyword / Hybrid（可配 `hybrid_alpha`）
- **过滤**：session_id, user_id, namespace, topics, entities, created_at, last_accessed, memory_type, event_date
- **Recency re-ranking**：可配权重（semantic/recency/freshness/novelty）

### 7. 注入 agent context

**MemoryPrompt**：拼接 system prompt + working memory context + long-term memories

### 8. 支持 scope

**三级**：`namespace` + `user_id` + `session_id`

### 9. Audit/trace/provenance/rollback

- **Provenance**：`extracted_from`（消息 ID 列表）, `session_id`, `user_id`
- **时间戳**：4 个时间戳追踪
- **无完整变更历史**：没有 history API

### 10. 适合迁移 vs 不适合迁移

**适合迁移**：
- Prompt grounding 设计（代词/时间/空间解析）
- 三层去重架构（ID + hash + semantic + LLM merge）
- Policy-based forgetting（TTL + inactivity + budget + pinning）
- 混合搜索 + 丰富过滤器
- Debounce 提取（避免消息流重复提取）
- Filter 系统设计（TagFilter/NumFilter/DateTimeFilter）

**不适合迁移**：
- Redis 强依赖（RedisVL, RediSearch）
- 服务端架构太重（FastAPI + MCP + Docket）
- 无 bi-temporal 支持（不如 graphiti）
- 弱 provenance（不如 cognee/graphiti）
