# Cognee - 快速扫描

## 1. 项目定位

Cognee 是一个开源的 **AI 知识引擎**，定位为"让 AI Agent 拥有持久化、可学习的记忆"。它用 ECL（Extract, Cognify, Load）管线替代传统 RAG，结合向量搜索、图数据库和 LLM 驱动的实体抽取，将任意格式的数据（文档、URL、文本、代码）转化为结构化的知识图谱。核心 API 是四个动词——`remember`（存储）、`recall`（检索）、`forget`（删除）、`improve`（优化）。

它不仅是一个向量检索库，而是一个完整的知识基础设施：支持多租户隔离、多模态数据、本体论（Ontology） grounding、审计追踪（OTEL collector / tracing）以及 session-level 和 permanent graph 双层记忆架构。

## 2. 主要语言和技术栈

| 维度 | 详情 |
|------|------|
| 语言 | Python 3.10–3.13 |
| 包管理 | Poetry / uv / pip |
| LLM 框架 | LiteLLM（统一多提供商路由）+ Instructor（结构化输出） |
| 支持的 LLM | OpenAI（默认）、Anthropic、Google Gemini、Ollama、Mistral、AWS Bedrock、Azure |
| 图数据库 | Kuzu（默认）、Neo4j、Neptune、Postgres |
| 向量数据库 | LanceDB（默认）、ChromaDB、PGVector、Qdrant、Weaviate、Milvus |
| 关系型数据库 | SQLite（默认）、PostgreSQL |
| Web 框架 | FastAPI |
| 数据模型 | Pydantic BaseModel |
| 观测性 | OpenTelemetry (OTEL)、Sentry、Langfuse、structlog |
| 其他 | Alembic（迁移）、Docker/Helm 部署 |

## 3. 顶层目录结构

```
cognee/
├── cognee/                    # 核心 Python 包
│   ├── api/v1/                # FastAPI 路由 + SDK 入口
│   │   ├── add/               # 数据摄入
│   │   ├── cognify/           # 知识图谱构建
│   │   ├── search/            # 检索
│   │   ├── remember/          # V2 记忆存储 API
│   │   ├── recall/            # V2 记忆检索 API
│   │   ├── forget/            # 记忆删除
│   │   ├── improve/           # 记忆优化
│   │   └── session/           # 会话管理
│   ├── memory/                # 记忆条目定义 (QAEntry, TraceEntry, FeedbackEntry)
│   ├── tasks/                 # 管道任务（抽取、存储、摘要等）
│   │   ├── graph/             # 图实体抽取任务
│   │   ├── ingestion/         # 数据加载任务
│   │   ├── storage/           # 持久化任务
│   │   └── summarization/     # 摘要任务
│   ├── modules/               # 领域模块
│   │   ├── agent_memory/      # Agent 记忆装饰器与运行时上下文
│   │   ├── retrieval/         # 检索器实现（多种 SearchType）
│   │   ├── search/            # 搜索编排
│   │   ├── pipelines/         # 管道编排
│   │   ├── observability/     # 追踪与 Span
│   │   ├── ontology/          # 本体论支持
│   │   ├── users/             # 多租户与权限
│   │   └── session_lifecycle/ # 会话生命周期管理
│   ├── infrastructure/        # 基础设施适配器层
│   │   ├── databases/         # Graph/Vector/Relational 数据库接口
│   │   ├── llm/               # LLMGateway 统一 LLM 路由
│   │   ├── engine/            # DataPoint/Edge 核心数据模型
│   │   └── files/             # 文档加载器
│   └── shared/                # 共享工具与数据模型
├── cognee-frontend/           # React 前端 UI
├── cognee-mcp/                # MCP 协议集成
├── cognee-starter-kit/        # 入门脚手架
├── examples/                  # 示例代码
├── distributed/               # 分布式部署脚本 (Modal, Fly.io, Railway)
├── deployment/                # Helm chart
└── evals/                     # 评估框架
```

## 4. README 中对 memory 的定义

README 对 memory 的核心表述：

> "Cognee is an open-source knowledge engine that lets you ingest data in any format or structure and continuously learns to provide the right context for AI agents."

> "Use our knowledge engine to build personalized and dynamic memory for AI Agents."

Cognee 将 memory 定义为**知识图谱驱动的持久化上下文**——不是简单的向量 embedding 检索，而是"both searchable by meaning and connected by relationships as they change and evolve"。它提供了两层记忆架构：
- **Session memory**：快速的会话级缓存（`session_id` 命名空间），在后台同步到永久图
- **Permanent knowledge graph**：通过 `add` + `cognify` 构建的图数据库，包含实体、关系和摘要

## 5. 关键源码路径

| # | 文件路径（相对于 repo 根） | 说明 |
|---|------|------|
| 1 | `cognee/__init__.py` | SDK 入口，导出 `remember`, `recall`, `forget`, `improve`, `add`, `cognify`, `search` 等 |
| 2 | `cognee/memory/entries.py` | 记忆类型定义：`QAEntry`, `TraceEntry`, `FeedbackEntry`, `RecallScope`, `normalize_scope()` |
| 3 | `cognee/api/v1/remember/remember.py` | `remember()` 核心实现——调度 add/cognify 管道或 session manager |
| 4 | `cognee/api/v1/recall/recall.py` | `recall()` 核心实现——自动路由到 session 缓存或图检索 |
| 5 | `cognee/tasks/graph/extract_graph_from_data.py` | 从文档分块中通过 LLM 抽取实体和关系，构建 KnowledgeGraph |
| 6 | `cognee/infrastructure/databases/graph/graph_db_interface.py` | 图数据库抽象接口（Kuzu/Neo4j/Neptune 适配器基类） |
| 7 | `cognee/infrastructure/databases/vector/vector_db_interface.py` | 向量数据库抽象接口（LanceDB/ChromaDB/PGVector 适配器基类） |
| 8 | `cognee/infrastructure/llm/LLMGateway.py` | 统一 LLM 网关——多提供商路由 + 结构化输出 |
| 9 | `cognee/modules/retrieval/` | 多种检索器实现（graph_completion、triplet、rag、temporal 等 15+ 种 SearchType） |
| 10 | `cognee/modules/agent_memory/` | Agent 记忆装饰器，注入 memory context 到 LLM 调用 |

## 6. 是否值得深入阅读

**Yes — 强烈值得。**

Cognee 是一个功能完整的 AI 记忆平台，代码量较大但架构清晰。它解决的问题（多模态摄入、知识图谱构建、多租户隔离、双层记忆架构、审计追踪）与飞书协作 Memory Engine 的需求高度重叠。特别是它的图数据库抽象层、Ontology 支持、以及 OTEL 追踪都是值得深入学习的模式。

## 7. 对飞书协作 Memory Engine 的潜在价值

**评级：High**

具体原因：

| 飞书 Memory Engine 关注点 | Cognee 的相关性 | 详情 |
|------|------|------|
| **Multi-scope support** | 高 | Cognee 原生支持 `user → dataset → data` 三层层次 + `session_id` 会话级记忆 + 永久图记忆。`RecallScope` 支持 `graph`、`session`、`trace`、`graph_context`、`all` 多种范围。这与飞书的 doc/space/chat 多 scope 天然对应。 |
| **Auditability** | 高 | Cognee 内置 OpenTelemetry collector，`DataPoint` 基类有 `source_pipeline` 和 `source_task` 溯源字段。`TraceEntry` 用于记录 agent 操作轨迹。还有 `SessionLifecycle` 模块记录 LLM 调用用量。 |
| **LLM extraction patterns** | 高 | `extract_graph_from_data.py` 展示了完整的 LLM 驱动实体抽取管线：分块 → LLM 结构化输出（Instructor）→ 知识图构建 → 本体论对齐。`LLMGateway` 统一处理多提供商 + 结构化输出。`_stamp_provenance_deep()` 递归打溯源标签。 |
| **Adapter patterns** | 高 | `GraphDBInterface` 和 `VectorDBInterface` 是干净的 Protocol/ABC 抽象。Graph 层支持 Kuzu/Neo4j/Neptune/Postgres 五种后端，Vector 层支持 LanceDB/ChromaDB/PGVector/Qdrant/Weaviate/Milvus 六种后端。工厂模式在 `get_graph_engine()` / `get_vector_engine()` 中实现。 |
| **Provenance tracking** | 高 | `DataPoint` 基类内置 `source_pipeline`、`source_task`、`version` 等字段。`_stamp_provenance_deep()` 递归打溯源标签。 |

## 深度分析（10 维度）

### 1. 信息如何进入 memory

**API 入口**：`remember()` + `add()` + `cognify()`。

**流程**：
- `remember(data)` → 调度 add 管道（数据摄入）+ cognify 管道（知识图谱构建）
- 数据摄入：URL/文档/文本/代码 → 分块 → DataPoint
- Cognify：DataPoint → LLM 抽取实体/关系 → 知识图谱
- 支持 session 模式：先写入 session 缓存，后台同步到永久图

### 2. 是否经过 LLM 抽取

**是的**。`extract_graph_from_data.py` 通过 LLM 从文档分块中抽取实体和关系。使用 LiteLLM + Instructor 做结构化输出。`LLMGateway` 统一多提供商路由。

### 3. 数据结构

**记忆类型**（`cognee/memory/entries.py`）：`QAEntry`, `TraceEntry`, `FeedbackEntry`

**DataPoint**：基类含 `source_pipeline`, `source_task`, `version` 溯源字段

**知识图谱**：实体节点 + 关系边（图数据库存储）

### 4. 新增、更新、删除、遗忘

- **新增**：`remember()` 或 `add()` + `cognify()`
- **更新**：`improve()` — LLM 驱动的优化
- **删除**：`forget()` — 按 scope 删除
- **遗忘**：通过 `forget()` 显式触发，无自动 TTL

### 5. 重复、冲突、时间变化、过期

- 图数据库中实体通过名称匹配去重
- 无显式冲突解决机制
- 无 bi-temporal 支持
- 无自动过期

### 6. 检索

**15+ 种 SearchType**：graph_completion, triplet, rag, temporal 等

**RecallScope**：`graph`, `session`, `trace`, `graph_context`, `all`

### 7. 注入 agent context

`cognee/modules/agent_memory/` 提供装饰器，自动注入 memory context 到 LLM 调用。

### 8. 支持 scope

**多层 scope**：`user → dataset → data` + `session_id` 会话级 + 永久图

### 9. Audit/trace/provenance/rollback

- **OpenTelemetry**：内置 collector
- **DataPoint**：`source_pipeline`, `source_task` 溯源字段
- **TraceEntry**：记录 Agent 操作轨迹
- **FeedbackEntry**：对 QA 记录评分反馈

### 10. 适合迁移 vs 不适合迁移

**适合迁移**：
- `remember/recall/forget/improve` 四动词 API 设计
- 图数据库 + 向量数据库双抽象层
- `RecallScope` 多范围检索
- `DataPoint` 溯源字段设计
- Agent 记忆装饰器模式
- Instructor 结构化输出

**不适合迁移**：
- 代码量大、架构重，不适合比赛 demo 轻量需求
- 图数据库依赖
- 无 bi-temporal
- 无自动遗忘
- 配置复杂度高
