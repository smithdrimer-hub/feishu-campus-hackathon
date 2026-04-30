# graphiti (getzep/graphiti) - 快速扫描

## 1. 项目定位
**时序上下文图（Temporal Context Graph）构建框架**，用于 AI Agents。核心创新：图中每个事实都有"有效性时间窗口"，支持查询"现在什么是对的"或"在某个时间点什么是对的"。由 Zep 团队开源，是 Zep 商业产品的核心引擎。

## 2. 主要语言和技术栈
- **主要语言**: Python 3.10+
- **核心依赖**: Pydantic, Neo4j/FalkorDB/Kuzu/Amazon Neptune, OpenAI (默认 LLM)，numpy, tenacity
- **构建工具**: uv (hatchling)
- **附加组件**: MCP Server, FastAPI REST Server, OpenTelemetry tracing

## 3. 顶层目录结构
| 目录 | 用途 |
|------|------|
| `graphiti_core/` | 核心库 |
| `graphiti_core/graphiti.py` | 主入口类 (~70KB) |
| `graphiti_core/nodes.py` | 节点模型 |
| `graphiti_core/edges.py` | 边模型 |
| `graphiti_core/driver/` | 图数据库驱动 |
| `graphiti_core/llm_client/` | LLM 客户端 |
| `graphiti_core/search/` | 混合搜索 |
| `graphiti_core/prompts/` | LLM Prompt |
| `graphiti_core/models/` | Pydantic 模型 + Cypher 查询模板 |
| `server/` | FastAPI REST 服务 |
| `mcp_server/` | MCP 协议服务 |

## 4. README 中对 memory 的定义
用 **Context Graph（上下文图）** 概念：
- 记忆 = **Entities（实体节点）** + **Facts/Relationships（带时间窗的关系边）** + **Episodes（原始数据片段）**
- 每条事实有 validity window：何时变为 true，何时被替代（但不被删除）
- Episode 是原始数据的"地面真实"，所有推导事实可追溯到 Episode
- "Unlike static knowledge graphs, Graphiti's context graphs track how facts change over time, maintain provenance to source data"

## 5. 关键源码路径
| 文件路径 | 说明 |
|----------|------|
| `graphiti_core/__init__.py` | 入口，导出 `Graphiti` 类 |
| `graphiti_core/graphiti.py` | 主类 (~70KB)，所有操作编排 |
| `graphiti_core/nodes.py` | 节点定义 (~38KB): EntityNode, EpisodicNode, CommunityNode, SagaNode |
| `graphiti_core/edges.py` | 边定义 (~34KB): Edge, EntityEdge, EpisodicEdge 等 |
| `graphiti_core/driver/driver.py` | 图驱动抽象基类 |
| `graphiti_core/driver/neo4j_driver.py` | Neo4j 驱动实现 |
| `graphiti_core/search/search.py` | 混合搜索主逻辑 |
| `graphiti_core/prompts/lib.py` | Prompt 库：实体提取、去重、摘要 |

## 6. 是否值得深入阅读
**Yes** — 时序知识图的完整实现，4 种图数据库后端，最强的 provenance 方案。

## 7. 对飞书协作 Memory Engine 的潜在价值
**High**

| 评估维度 | 价值 | 原因 |
|----------|------|------|
| Multi-scope support | Medium | `group_id` 实现图分区，粒度不如 Mem0 丰富 |
| Auditability | **Very High** | bi-temporal tracking，可查任意时间点事实状态，事实被替代而非删除 |
| LLM extraction | Medium | 从非结构化文本提取 entities/relationships，与飞书场景相关 |
| Adapter patterns | Medium | Driver 抽象层设计良好，仅面向图数据库 |
| Provenance tracking | **Very High** | Episode 机制是最强 provenance 方案 |

## 深度分析（10 维度）

### 1. 信息如何进入 memory

**API 入口**：`Graphiti.add_episode()` (`graphiti_core/graphiti.py`)。还有 `add_episode_bulk()` 和 `add_triplet()`。

**Ingest 流程**（~150 行核心逻辑）：
1. 验证参数，设置 group_id
2. 检索前 N 条 episode 作为上下文
3. 创建 EpisodicNode（原始数据）
4. LLM 提取实体：`extract_nodes()`
5. 实体去重：`resolve_extracted_nodes()` — 与已有图比对合并
6. LLM 提取关系：`extract_edges()`
7. 关系去重+无效化：`resolve_extracted_edges()`
8. 提取节点属性：`extract_attributes_from_nodes()`
9. 批量保存：`add_nodes_and_edges_bulk()`
10. 可选：Saga + Community

**EpisodeType**：`message`, `json`, `text`, `fact_triple`

### 2. 是否经过 LLM 抽取

**是的，大量 LLM 调用**。使用 `prompt_library` (`graphiti_core/prompts/lib.py`) 中的多个 prompt：

| Prompt | 用途 |
|--------|------|
| `extract_nodes_and_edges` | 单次提取实体+关系 |
| `dedupe_nodes.nodes` | 实体去重 |
| `dedupe_edges` | 关系去重+无效化 |
| `summarize_nodes` | 生成实体 summary |
| `summarize_sagas` | Saga 摘要 |

**extract_message prompt**：输出 Pydantic 模型 `CombinedExtraction`，结构化 schema 验证。

### 3. 数据结构

**四种节点**（`nodes.py`）：
- `EpisodicNode`：`content`, `valid_at`, `source`, `entity_edges` — 原始数据
- `EntityNode`：`name`, `name_embedding`, `summary`, `attributes` — 实体
- `CommunityNode`：社区聚类
- `SagaNode`：叙事线

**五种边**（`edges.py`）：
- `EntityEdge`：`name`(关系类型), `fact`(自然语言), `fact_embedding`, `episodes`, `valid_at`, `invalid_at`, `expired_at`, `reference_time` — **核心事实边**
- `EpisodicEdge`：Episodic → Entity
- `CommunityEdge`：Community → Entity
- `HasEpisodeEdge`：Saga → Episodic
- `NextEpisodeEdge`：Episodic → Episodic

**Bi-temporal 字段**（EntityEdge）是核心设计：`valid_at` + `invalid_at` + `expired_at` + `reference_time` + `episodes: list[str]`

### 4. 新增、更新、删除、遗忘

- **新增**：`add_episode()` 创建 EpisodicNode + EntityNode + EntityEdge
- **更新**：实体去重时合并；关系**无效化**而非更新（旧边 `invalid_at` 被设置，新边创建）
- **删除**：`remove_episode()` 删除 episode 及独家衍生的边/节点
- **遗忘**：事实被"无效化"而非删除，`invalid_at` 标记

### 5. 重复、冲突、时间变化、过期

- **实体去重**：LLM 判断（`dedupe_nodes` prompt）
- **关系去重**：LLM 判断（`dedupe_edges` prompt），返回 `(resolved_edges, invalidated_edges, new_edges)`
- **冲突**：旧事实 `invalid_at` 被设为当前 episode 的 `valid_at`，旧事实保留在图中
- **时间**：Bi-temporal 模型，搜索可过滤 `valid_at <= t AND (invalid_at IS NULL OR invalid_at > t)`

### 6. 检索

**四通道并行搜索**（`search/search.py`）：
| 通道 | 方法 | Reranker |
|------|------|----------|
| Edge | BM25 + cosine + BFS | RRF/cross_encoder/MMR/node_distance |
| Node | BM25 + cosine + BFS | 同上 |
| Episode | BM25 only | RRF/cross_encoder |
| Community | BM25 + cosine | RRF/MMR |

**预设配置**：`EDGE_HYBRID_SEARCH_RRF`, `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` 等

### 7. 注入 agent context

无内置注入。`SearchResults` 返回完整 nodes/edges/episodes/communities 对象，使用者自行格式化。

### 8. 支持 scope

**`group_id` 一级分区**，所有节点和边都有 `group_id`，每个 group_id 对应独立 Neo4j database。

粒度不如 mem0：只有一级，需应用层管理 group_id 语义。Saga 提供额外叙事线维度。

### 9. Audit/trace/provenance/rollback

**最强的维度**：
- **Provenance**：`EntityEdge.episodes` 字段 → EpisodicNode 完整 `content`，可追溯每条事实的来源
- **History**：Bi-temporal 本身是完整历史记录，可查任意时间点
- **OpenTelemetry**：`Tracer` + `_trace_phase` 记录全流程
- **Explainability**：`edge.fact` 自然语言描述 + `edge.name` 关系类型

### 10. 适合迁移 vs 不适合迁移

**适合迁移**：
- Bi-temporal 模型（`valid_at` + `invalid_at`）
- Episode → Edge provenance 链路
- 事实无效化而非删除
- 关系图模型（Entity → RELATES_TO → Entity）
- SearchConfig 架构（可插拔 reranker）
- Saga 叙事线 + Community 聚类
- Driver 抽象层 + Prompt 版本管理

**不适合迁移**：
- 图数据库强制依赖（部署复杂）
- 缺少多级 scope（只有 group_id）
- LLM 调用次数多（3-5 次 per episode）
- 无本地嵌入式模式
- 搜索 API 复杂度高
- 缺少高级元数据过滤
