# mem0 (mem0ai/mem0) - 快速扫描

## 1. 项目定位
Mem0 是一个面向 AI Agents 的**长期记忆层**（Long-Term Memory Layer），定位为"个人化 AI 交互的基础设施"。解决核心问题：让 AI 助手/客服/Agent 跨会话记住用户偏好、历史行为和上下文。由 Y Combinator S24 孵化，兼具开源 SDK 和商业托管服务（mem0.ai）。

## 2. 主要语言和技术栈
- **主要语言**: Python 3.10+
- **TypeScript SDK**: 有（`mem0-ts/` 目录）
- **核心依赖**: Pydantic, OpenAI SDK, Qdrant (默认向量), SQLAlchemy + SQLite, Spacy (可选)
- **向量存储后端**（18+ 适配器）: Qdrant, Chroma, Weaviate, Pinecone, Milvus, Elasticsearch, OpenSearch, PGVector, Redis, MongoDB, Cassandra, Faiss 等
- **构建工具**: Poetry
- **服务端**: FastAPI + Docker Compose

## 3. 顶层目录结构
| 目录 | 用途 |
|------|------|
| `mem0/` | Python SDK 核心库 |
| `mem0/memory/` | 记忆引擎主逻辑 |
| `mem0/vector_stores/` | 18+ 种向量存储适配器 |
| `mem0/configs/` | 配置、Prompt 模板、枚举 |
| `mem0/embeddings/`, `mem0/llms/`, `mem0/reranker/` | LLM/Embedding/Reranker 工厂与适配器 |
| `mem0-ts/` | TypeScript/Node.js SDK |
| `server/` | 自托管 FastAPI 服务端 |
| `cli/` | CLI 工具 |
| `cookbooks/` | 示例 Notebook |

## 4. README 中对 memory 的定义
三层结构：
- **Multi-Level Memory**: 用户级（User）、会话级（Session）、Agent 级状态
- 记忆是"从对话中自动提取的事实和偏好"
- 2026 年新版算法采用 "Single-pass ADD-only extraction"（单次调用 LLM，只做添加不做更新/删除，记忆累加）
- 支持 Entity linking 和 Multi-signal retrieval（语义 + BM25 + 实体并行评分融合）

## 5. 关键源码路径
| 文件路径 | 说明 |
|----------|------|
| `mem0/__init__.py` | 入口: `Memory`, `AsyncMemory`, `MemoryClient` |
| `mem0/memory/main.py` | 核心记忆引擎 (~140KB)，add/search/get/delete/history/flatten |
| `mem0/memory/base.py` | `MemoryBase` 抽象基类 |
| `mem0/memory/storage.py` | SQLite 本地存储层 |
| `mem0/vector_stores/base.py` | 向量存储抽象接口 |
| `mem0/configs/prompts.py` | LLM 提取 Prompt（FACT_RETRIEVAL_PROMPT） |
| `mem0/configs/base.py` | MemoryConfig 和 MemoryItem Pydantic 模型 |
| `mem0/utils/entity_extraction.py` | 实体提取逻辑 |
| `mem0/utils/factory.py` | VectorStoreFactory, EmbedderFactory, LlmFactory |
| `server/main.py` | FastAPI 服务端入口 |

## 6. 是否值得深入阅读
**Yes** — 功能最完整的开源 AI 记忆库。18+ 向量适配器、成熟 LLM Prompt、多层级记忆、完整 CLI + TS SDK + 服务端。

## 7. 对飞书协作 Memory Engine 的潜在价值
**High**

| 评估维度 | 价值 | 原因 |
|----------|------|------|
| Multi-scope support | High | user_id/session_id/agent_id 三级 scope，对应飞书"文档/群组/个人" |
| Auditability | Medium | 有 `history(memory_id)` 但 v3 ADD-only 策略不显式记录删除/更新 |
| LLM extraction | High | FACT_RETRIEVAL_PROMPT 设计精良，ADD-only 策略简单可靠 |
| Adapter patterns | High | `vector_stores/base.py` + Factory 模式是极佳参考 |
| Provenance tracking | Low | 缺少细粒度 provenance（哪句话产生哪条记忆） |

## 深度分析（10 维度）

### 1. 信息如何进入 memory

**API 入口**：`Memory.add()` 方法 (`mem0/memory/main.py`)。

**Ingest 流程**（V3 Phased Batch Pipeline）：
- Phase 0：SQLite 获取最近 10 条消息作为上下文
- Phase 1：向量检索 top_k=10 已有记忆用于去重参考
- Phase 2：单次 LLM 调用，ADD-only 提取
- Phase 3：批量向量化
- Phase 4/5：CPU 处理 + MD5 hash 去重
- Phase 6：批量写入向量存储
- Phase 7：批量实体链接（NER + 向量相似度 >=0.95 合并）
- Phase 8：保存原始消息到 SQLite

**Batch vs Streaming**：支持单条和批量，有 `AsyncMemory` 异步版本。`infer=False` 时跳过 LLM。

### 2. 是否经过 LLM 抽取

**是的**。核心 prompt `ADDITIVE_EXTRACTION_PROMPT` (`mem0/configs/prompts.py`) 约 475 行的超长 system prompt。

特点：
- ADD-only 策略：只做 ADD，不做 UPDATE/DELETE
- 输入包含：Summary、Recently Extracted Memories、Existing Memories、New Messages、Observation Date、Current Date
- 输出：`{"memory": [{"id": "0", "text": "...", "attributed_to": "user|assistant", "linked_memory_ids": [...]}]}`
- 12 个 few-shot 示例
- `response_format={"type": "json_object"}` + `json.loads()` 容错解析

### 3. 数据结构

**MemoryItem** (`mem0/configs/base.py`)：`id`, `memory`, `hash`, `metadata`, `score`, `created_at`, `updated_at`

**Entity Store**：独立向量集合，`entity_type` + `linked_memory_ids`

**History 表**（SQLite）：`old_memory`, `new_memory`, `event`(ADD/UPDATE/DELETE), `actor_id`, `role`, `is_deleted`

### 4. 新增、更新、删除、遗忘

- **新增**：`add()` V3 ADD-only
- **更新**：`update(memory_id)` 替换向量 + history 记录
- **删除**：`delete(memory_id)` 从向量库移除 + history + 清理 entity store
- **遗忘**：**无自动 TTL 或遗忘策略**

### 5. 重复、冲突、时间变化、过期

- **重复**：MD5 hash 去重 + 实体向量相似度去重
- **冲突**：**无显式解决**，新旧共存
- **时间**：有 `created_at`/`updated_at`，无时间范围检索
- **过期**：**无 TTL**

### 6. 检索

**三路融合**：semantic + BM25 + entity boost。支持 rerank（Cohere/sentence_transformer/LLM）。

**高级过滤**：`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `contains`, `AND`, `OR`, `NOT`

**scope 过滤**：必须提供 `user_id`/`agent_id`/`run_id` 至少一个

### 7. 注入 agent context

无内置注入方法。`search()` 返回字典列表，用户自行格式化。`chat()` 方法为 `NotImplementedError`。

### 8. 支持 scope

**三级 scope**：`user_id`（用户）+ `agent_id`（Agent）+ `run_id`（会话）。额外维度：`actor_id`, `role`, `attributed_to`, 自定义 `metadata`。

### 9. Audit/trace/provenance/rollback

- **History**：`history(memory_id)` 返回完整变更事件，SQLite 存储
- **Provenance**：记录 `actor_id` 和 `role`，**但无法追溯哪条原始消息产生了哪条记忆**
- **Rollback**：无自动 rollback，可通过 history 手动恢复
- **Explainability**：仅有 `score` 字段，无检索原因解释

### 10. 适合迁移 vs 不适合迁移

**适合迁移**：
- Factory 模式（VectorStoreFactory 等 4 个工厂）
- 三级 scope 设计
- V3 ADD-only 提取策略
- ADDITIVE_EXTRACTION_PROMPT（475 行精心设计的 prompt）
- 混合检索（三路融合）
- 高级过滤（AND/OR/NOT + 比较运算符）
- Batch pipeline 架构（Phase 0-8）
- MemoryConfig Pydantic 模型

**不适合迁移**：
- 扁平记忆模型（无实体关系图）
- 无 TTL/自动遗忘
- 弱 provenance
- 无 bi-temporal 支持
- SQLite 作为 history 存储
