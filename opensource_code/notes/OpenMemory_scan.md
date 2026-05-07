# OpenMemory - 快速扫描

## 1. 项目定位

**认知记忆引擎（Cognitive Memory Engine）**，模拟人类大脑的多扇区记忆结构。"Not RAG. Not a vector DB."

关键差异化：
- **多扇区记忆**：Episodic, Semantic, Procedural, Emotional, Reflective 五扇区
- **时序知识图谱**：`valid_from` / `valid_to` 时间窗口
- **衰减引擎**：基于 salience + recency + coactivation 的自适应遗忘
- **Waypoint 图**：记忆之间的关联链接，可解释检索路径
- **本地优先**：默认 SQLite，也可 Postgres
- **双 SDK**：Python + Node.js

> 注意：README 注明 "This project is currently being fully rewritten."

## 2. 主要语言和技术栈

- **SDK 语言**: Python + TypeScript/Node.js
- **存储**: SQLite（默认）、PostgreSQL、Valkey
- **嵌入模型**: OpenAI, Gemini, Ollama, AWS Bedrock, E5/BGE
- **后端框架**: Express.js（Node.js server）
- **协议**: REST API + MCP
- **连接源**: GitHub, Notion, Google Drive, OneDrive, Web Crawler 等

## 3. 顶层目录结构

| 目录/文件 | 用途 |
|---|---|
| `packages/openmemory-js/src/core/` | 核心：db.ts, cfg.ts, types.ts, models.ts |
| `packages/openmemory-js/src/memory/` | 记忆引擎：hsg.ts, decay.ts, reflect.ts, embed.ts |
| `packages/openmemory-js/src/server/` | HTTP 服务端 |
| `packages/openmemory-js/src/sources/` | 数据源连接器 |
| `packages/openmemory-js/src/temporal_graph/` | 时序知识图谱 |
| `packages/openmemory-js/src/vector/` | 向量存储实现 |
| `packages/openmemory-js/src/ops/` | 操作层 |
| `packages/openmemory-py/` | Python SDK |
| `dashboard/` | Web Dashboard UI |

## 4. README 中对 Memory 的定义

**多扇区结构**模拟人类大脑：
- **Episodic**：事件和经历，带时间上下文
- **Semantic**：事实和知识
- **Procedural**：技能和操作步骤
- **Emotional**：感受与情绪
- **Reflective**：从其他记忆中提取的洞察

检索："Composite scoring: Salience + recency + coactivation, not just cosine distance"

Waypoint 图提供可解释追溯，时间作为核心维度。

## 5. 关键源码路径

| # | 文件路径 | 重要性说明 |
|---|----------|---|
| 1 | `packages/openmemory-js/src/memory/hsg.ts` | 核心：分层扇区图，扇区分类器、记忆添加查询 |
| 2 | `packages/openmemory-js/src/memory/decay.ts` | 衰减引擎：自适应遗忘，hot/warm/cold 三级存储 |
| 3 | `packages/openmemory-js/src/memory/reflect.ts` | 反思/合并引擎：聚类相似记忆 |
| 4 | `packages/openmemory-js/src/core/types.ts` | 所有核心类型定义 |
| 5 | `packages/openmemory-js/src/core/db.ts` | 数据库层：SQLite/Postgres 适配 |
| 6 | `packages/openmemory-js/src/core/memory.ts` | Memory 类核心 API |
| 7 | `packages/openmemory-js/src/temporal_graph/store.ts` | 时序知识图谱存储 |
| 8 | `packages/openmemory-js/src/sources/` | 7 个外部源连接器 |

## 6. 是否值得深入阅读

**Yes — 强烈推荐。**

理由：认知科学设计（五扇区 + 衰减 + 反思），时序知识图谱，可解释检索（Waypoint 图），多 SDK，连接源丰富。但正在大规模重写中。

## 7. 对飞书协作 Memory Engine 的潜在价值

**High**

| 维度 | 价值分析 |
|---|---|
| Multi-scope support | `user_id` 用户隔离，时序图谱可按实体查询，需扩展 workspace/doc scope |
| Auditability | 有时间戳 + version，Waypoint 图提供可解释路径 |
| LLM extraction patterns | 扇区分类器用 regex + 模型名，反思模块用 Jaccard 聚类 |
| Adapter patterns | db.ts 支持 SQLite/Postgres，7 个 Source 连接器展示统一摄入接口 |
| Provenance tracking | `meta` 字段 + Waypoint 图 + 时序图谱 `valid_from`/`valid_to` |
| 额外亮点 | 衰减引擎、时序知识图谱、反思/合并、多扇区分类 |
