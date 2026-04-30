# 开源 Memory 项目综合对比与飞书 Memory Engine 推荐方案

---

## 1. 当前主项目已有架构总结

**项目名称**：OpenClaw Memory Engine V1/V1.1

**核心文件**：

| 文件 | 用途 | 状态 |
|------|------|------|
| `openclaw-memory/src/memory/schema.py` | MemoryItem, SourceRef 数据模型 | ✅ 已有 |
| `openclaw-memory/src/memory/store.py` | JSON 本地存储，upsert 去重 | ✅ 已有 |
| `openclaw-memory/src/memory/engine.py` | 编排 ingest → extract → upsert | ✅ 已有 |
| `openclaw-memory/src/memory/extractor.py` | 规则抽取 + LLM 抽取 + schema 校验 | ✅ 已有 |
| `openclaw-memory/src/memory/llm_provider.py` | FakeLLMProvider | ✅ 已有 |
| `openclaw-memory/src/memory/handoff.py` | 交接摘要生成 | ✅ 已有 |
| `openclaw-memory/src/memory/action_planner.py` | 行动计划生成 | ✅ 已有 |
| `openclaw-memory/src/memory/candidate.py` | MemoryCandidate 数据类 | ✅ 已有 |
| `openclaw-memory/src/adapters/lark_cli_adapter.py` | 飞书 CLI 适配器 | ✅ 已有 |
| `openclaw-memory/src/adapters/command_registry.py` | 命令注册/分类 | ✅ 已有 |
| `openclaw-memory/src/safety/policy.py` | 安全策略（读写命令分类） | ✅ 已有 |

**核心能力**：
- ✅ 从飞书群消息读取项目讨论
- ✅ 保存原始消息证据（SourceRef）
- ✅ LLM + 规则双模式提取结构化记忆
- ✅ Schema 校验（LLM 输出必须验证）
- ✅ 记忆更新/冲突解决（supersedes 机制）
- ✅ 生成交接摘要与行动计划
- ✅ 安全策略（只读命令自动允许，写入命令需确认）

**V1.1 限制**：
- ⚠️ FakeLLMProvider（无真实 LLM 集成）
- ⚠️ 无 bi-temporal 支持（无法查询"某个时间点的事实"）
- ⚠️ 弱 provenance（无法追溯原始消息 → 记忆的完整链路）
- ⚠️ 无自动遗忘策略（TTL/策略清理）
- ⚠️ 检索能力弱（无混合搜索、无 rerank）

---

## 2. 7 个开源项目对比表

| 维度 | mem0 | graphiti | agent-memory-server | OpenMemory | cognee | langmem | memory-template |
|------|------|----------|---------------------|------------|--------|---------|-----------------|
| **定位** | 通用记忆层 SDK | 时序知识图谱 | 记忆层服务端 | 认知记忆引擎 | 知识引擎 | LangGraph 记忆插件 | LangGraph 模板 |
| **语言** | Python + TS | Python | Python | TS + Python | Python | Python | Python |
| **存储** | 18+ 向量库 | 4 种图数据库 | Redis | SQLite/Postgres | 图 + 向量 + 关系 | LangGraph BaseStore | LangGraph Store |
| **记忆模型** | 扁平事实 + 实体索引 | 时序图（节点 + 边） | 文档 + 标签 | 五扇区图 | 知识图谱 | Store Items | Store Items |
| **LLM 提取** | ADD-only Prompt | 实体 + 关系提取 | 4 种策略 Prompt | 扇区分类（regex） | 图提取（Instructor） | TrustCall 结构化 | Patch/Insert |
| **去重** | MD5 + 语义 + 实体 | LLM 判断 | ID + hash + 语义 + LLM | Identity fields | Identity fields | N/A | Debounce |
| **遗忘** | 无 | 无效化（不删除） | TTL + inactivity + budget | 衰减引擎 | 手动 forget | 无 | 无 |
| **检索** | 语义+BM25+ 实体三路 | 四通道并行 | 语义/关键词/混合 | 复合评分 | 15+ SearchType | 基础搜索 | 基础搜索 |
| **Scope** | user/agent/run 三级 | group_id 一级 | namespace/user_id/session_id | user_id | Tenant/User/Dataset/Session | Namespace | Namespace |
| **Provenance** | 弱（actor_id） | 最强（Episode→Edge） | 基础（extracted_from） | Waypoint 图 | 强（source_pipeline） | 弱（时间戳） | 弱 |
| **Audit** | History 表 | Bi-temporal | 4 时间戳 | 衰减日志 | OTEL + DataPoint | 无 | 无 |
| **部署** | 低（SQLite+ 向量库） | 高（图数据库） | 中（Redis） | 低（SQLite） | 高（多数据库） | 低（LangGraph） | 低（LangGraph） |
| **代码量** | ~200KB | ~300KB | ~100KB | ~50KB | ~500KB | ~150KB | ~300 行 |

---

## 3. 最适合借鉴的 Top 3 项目及理由

### 🥇 第一名：agent-memory-server（Redis Agent Memory Server）

**理由**：
1. **提取策略 Prompt 最成熟** — 代词/时间/空间 grounding 设计可直接用于飞书聊天场景
2. **三层去重架构** — ID + hash + semantic + LLM merge 是最完善的去重方案
3. **策略遗忘** — TTL + inactivity + budget + pinning，适合飞书海量数据
4. **混合搜索** — 语义 + 关键词 + RRF 融合 + recency re-ranking
5. **过滤器设计** — 完整的类型化过滤器体系
6. **Debounce 提取** — trailing-edge debounce 避免消息流重复提取

**可复用代码/设计**：
- `memory_strategies.py` — 4 种策略 Prompt（尤其是 DiscreteMemoryStrategy）
- `long_term_memory.py` — 去重 + 遗忘逻辑
- `filters.py` — 过滤器类型层次
- `models.py` — MemoryRecord 完整字段设计

### 🥈 第二名：graphiti（时序知识图谱）

**理由**：
1. **Bi-temporal 模型** — `valid_at` + `invalid_at` 是飞书场景的杀手级特性
2. **Provenance 最强** — Episode → Edge 完整溯源链
3. **事实无效化而非删除** — 保留历史，适合审计
4. **关系图模型** — 天然对应飞书的"人员 - 文档 - 群组"关系

**可复用设计**：
- `edges.py` — EntityEdge 的 bi-temporal 字段
- `nodes.py` — EpisodicNode 的 `content` + `source` 设计
- `prompts/lib.py` — 实体/关系提取 Prompt

### 🥉 第三名：mem0（通用记忆层 SDK）

**理由**：
1. **三级 scope** — user_id/agent_id/run_id 直接对应飞书的文档/群组/个人
2. **ADD-only 策略** — 简化 LLM 交互，降低幻觉风险
3. **Factory 模式** — 18+ 向量库适配器，优雅的插件架构
4. **高级过滤** — AND/OR/NOT + 比较运算符

**可复用设计**：
- `configs/prompts.py` — ADDITIVE_EXTRACTION_PROMPT
- `utils/factory.py` — VectorStoreFactory 等工厂模式
- `memory/main.py` — V3 Phased Batch Pipeline

---

## 4. 具体借鉴方式（对应文件/模块）

| 借鉴内容 | 来源项目 | 飞书目标文件 | 用途 |
|----------|----------|--------------|------|
| **Prompt Grounding** | agent-memory-server | `src/memory/extractor.py` | 代词/时间/空间解析 |
| **三层去重** | agent-memory-server | `src/memory/store.py` | ID + hash + semantic |
| **策略遗忘** | agent-memory-server | `src/memory/store.py` | TTL + inactivity + budget |
| **Bi-temporal 字段** | graphiti | `src/memory/schema.py` | valid_at + invalid_at |
| **Provenance 链路** | graphiti | `src/memory/schema.py` | EpisodicNode → Edge 溯源 |
| **ADD-only 策略** | mem0 | `src/memory/extractor.py` | 简化 LLM 提取 |
| **三级 Scope** | mem0 | `src/memory/schema.py` | project_id/session_id/user_id |
| **Factory 模式** | mem0 | `src/adapters/` | 向量库适配器 |
| **混合搜索 RRF** | agent-memory-server | `src/memory/retrieval.py`（新增） | 语义 + 关键词融合 |
| **Recency Re-rank** | agent-memory-server | `src/memory/retrieval.py`（新增） | 时间衰减重排序 |
| **过滤器类型** | agent-memory-server | `src/memory/filters.py`（新增） | 类型化过滤器 |
| **TrustCall 结构化** | langmem | `src/memory/extractor.py` | Pydantic schema 验证 |
| **Debounce 提取** | agent-memory-server | `src/memory/engine.py` | trailing-edge debounce |

---

## 5. 推荐的 V1.5 最小可行架构

**架构目标**：在比赛 Demo 可用的前提下，吸收开源项目的精华设计。

```
┌─────────────────────────────────────────────────────────────┐
│                    Feishu Memory Engine V1.5                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐ │
│  │   Lark CLI   │────▶│   Adapter    │────▶│   Safety     │ │
│  │   (Feishu)   │     │  (Platform)  │     │   Policy     │ │
│  └──────────────┘     └──────────────┘     └──────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                   Memory Engine                          │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Ingest     │────▶│   Extract    │                 │ │
│  │  │   (events)   │     │  (LLM+Rule)  │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  │                          │                               │ │
│  │                          ▼                               │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Dedup      │◀────│   Debounce   │                 │ │
│  │  │  (3-layer)   │     │  (trailing)  │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  │                          │                               │ │
│  │                          ▼                               │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Upsert     │────▶│   Forgetting │                 │ │
│  │  │  (supersede) │     │   (policy)   │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                    Memory Store                          │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │  Raw Events  │     │ Memory State │                 │ │
│  │  │   (JSONL)    │     │   (JSON)     │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  │                          │                               │ │
│  │                          ▼                               │ │
│  │  ┌──────────────────────────────────────────────────┐   │ │
│  │  │  Bi-temporal Fields: valid_at, invalid_at        │   │ │
│  │  │  Provenance: SourceRef chain to original message │   │ │
│  │  │  Scope: project_id, session_id, user_id          │   │ │
│  │  └──────────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                   Retrieval Layer                        │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Search     │────▶│   Rerank     │                 │ │
│  │  │ (hybrid RRF) │     │  (recency)   │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  │                          │                               │ │
│  │                          ▼                               │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Filters    │     │   Scopes     │                 │ │
│  │  │ (typed)      │     │ (multi-level)│                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                   Handoff / Action                       │ │
│  │  ┌──────────────┐     ┌──────────────┐                 │ │
│  │  │   Handoff    │────▶│   Action     │                 │ │
│  │  │   Summary    │     │   Plan       │                 │ │
│  │  └──────────────┘     └──────────────┘                 │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**关键升级**（相对于 V1.1）：
1. **Bi-temporal 字段** — 支持时序查询
2. **三层去重** — ID + hash + semantic
3. **策略遗忘** — TTL + inactivity + budget
4. **混合搜索** — 语义 + 关键词 RRF 融合
5. **Recency Re-rank** — 时间衰减重排序
6. **Debounce 提取** — trailing-edge debounce

---

## 6. 需新增或修改的文件列表

### 新增文件

| 文件 | 目的 | 借鉴来源 |
|------|------|----------|
| `openclaw-memory/src/memory/retrieval.py` | 检索层（混合搜索 + RRF + rerank） | agent-memory-server |
| `openclaw-memory/src/memory/filters.py` | 类型化过滤器 | agent-memory-server |
| `openclaw-memory/src/memory/forgetting.py` | 遗忘策略（TTL + inactivity + budget） | agent-memory-server |
| `openclaw-memory/src/memory/debounce.py` | Debounce 提取逻辑 | agent-memory-server |
| `openclaw-memory/examples/01_quickstart.py` | 5 分钟快速开始 | graphiti |
| `openclaw-memory/examples/scenarios/` | 场景测试数据 | cognee |

### 修改文件

| 文件 | 修改内容 | 借鉴来源 |
|------|----------|----------|
| `openclaw-memory/src/memory/schema.py` | 添加 bi-temporal 字段（valid_at, invalid_at） | graphiti |
| `openclaw-memory/src/memory/store.py` | 三层去重 + 遗忘策略 + bi-temporal 查询 | agent-memory-server + graphiti |
| `openclaw-memory/src/memory/extractor.py` | Prompt grounding（代词/时间/空间解析） | agent-memory-server |
| `openclaw-memory/src/memory/engine.py` | Debounce 提取编排 | agent-memory-server |
| `openclaw-memory/src/adapters/lark_cli_adapter.py` | 增加 retrieval 接口 | - |
| `openclaw-memory/tests/test_store.py` | 扩展测试覆盖 | agent-memory-server |
| `openclaw-memory/tests/test_retrieval.py` | 新增检索测试 | - |
| `openclaw-memory/tests/test_forgetting.py` | 新增遗忘测试 | - |

---

## 7. 每个文件的改动目的

### `schema.py`
- **添加**：`valid_at`, `invalid_at` 字段（bi-temporal）
- **添加**：`event_date` 字段（episodic 记忆的事件时间）
- **目的**：支持时序查询，追溯"某个时间点的事实"

### `store.py`
- **添加**：三层去重逻辑（ID + hash + semantic）
- **添加**：遗忘策略（TTL + inactivity + budget + pinning）
- **添加**：bi-temporal 查询方法（`query_at_time()`）
- **目的**：去重更完善，自动清理过期数据，时序审计

### `extractor.py`
- **添加**：Prompt grounding（代词/时间/空间解析）
- **修改**：LLM 提取 Prompt 采用 agent-memory-server 的 DiscreteMemoryStrategy
- **目的**：提高提取质量，解决指代不明问题

### `engine.py`
- **添加**：Debounce 提取编排（trailing-edge）
- **目的**：避免消息流太频繁重复提取

### `retrieval.py`（新增）
- **实现**：混合搜索（RRF 融合）
- **实现**：Recency re-rank（时间衰减）
- **实现**：类型化过滤器
- **目的**：提高检索质量，支持多维度过滤

### `filters.py`（新增）
- **实现**：SessionId, Namespace, Topics, Entities, CreatedAt, EventDate 等过滤器
- **目的**：类型安全的过滤查询

### `forgetting.py`（新增）
- **实现**：TTL + inactivity + budget + pinning 策略
- **目的**：自动清理过期记忆，控制存储增长

### `debounce.py`（新增）
- **实现**：Trailing-edge debounce 逻辑
- **目的**：避免频繁触发 LLM 提取

---

## 8. 最小 Demo 路线

**V1.5 Demo 脚本**：

```bash
# 1. 快速开始（Fake LLM）
python examples/01_quickstart.py

# 2. 从飞书同步真实消息
python examples/02_sync_from_feishu.py --chat-id chat_001 --limit 50

# 3. 生成交接摘要
python examples/03_handoff_demo.py --project-id demo-001

# 4. 生成行动计划
python examples/04_action_plan_demo.py --project-id demo-001

# 5. 演示检索（混合搜索 + 过滤）
python examples/05_retrieval_demo.py --project-id demo-001 --query "API 文档"

# 6. 演示 bi-temporal 查询
python examples/06_temporal_demo.py --project-id demo-001 --at-time "2025-04-20T12:00:00Z"
```

**Demo 亮点**：
- ✅ 从飞书真实消息导入
- ✅ LLM 提取结构化记忆（Prompt grounding）
- ✅ 三层去重展示
- ✅ 交接摘要生成
- ✅ 行动计划生成
- ✅ 混合搜索 + 过滤
- ✅ Bi-temporal 查询（"当时决定的是什么？"）

---

## 9. 测试清单

**Unit Tests**：
- [ ] `test_schema.py` — MemoryItem, SourceRef, bi-temporal 字段
- [ ] `test_store.py` — CRUD, upsert, dedup, forgetting, bi-temporal 查询
- [ ] `test_extractor.py` — Prompt grounding 验证
- [ ] `test_retrieval.py` — 混合搜索，RRF，rerank，过滤器
- [ ] `test_forgetting.py` — TTL, inactivity, budget 策略
- [ ] `test_debounce.py` — trailing-edge debounce 逻辑

**Integration Tests**：
- [ ] `test_lark_adapter.py` — 飞书 CLI 适配器
- [ ] `test_handoff.py` — 交接摘要生成
- [ ] `test_action_plan.py` — 行动计划生成

**Scenario Tests**：
- [ ] `test_conflict_resolution.py` — 冲突决策处理
- [ ] `test_temporal_handling.py` — bi-temporal 查询
- [ ] `test_multi_scope.py` — 多作用域检索

---

## 10. 风险点

### 许可证风险
- **mem0** — MIT 许可证 ✅ 可商用
- **graphiti** — Apache 2.0 ✅ 可商用
- **agent-memory-server** — MIT ✅ 可商用
- **cognee** — MIT ✅ 可商用
- **OpenMemory** — MIT ✅ 可商用
- **langmem** — MIT ✅ 可商用
- **memory-template** — MIT ✅ 可商用

**结论**：所有借鉴项目均为宽松许可证，可安全复用。

### 依赖膨胀风险
- **agent-memory-server** 依赖 Redis + RedisVL — 飞书可改用 SQLite + 轻量向量库
- **graphiti** 依赖图数据库 — 飞书可先用 JSON 存储模拟，后续再引入图数据库

**缓解**：V1.5 优先保持轻量级，核心逻辑复用，存储层保持当前 JSON 方案。

### 写入动作安全风险
- 飞书适配器发送消息、创建任务等写入操作需要用户确认
- 当前项目的安全策略已设计良好，保持即可

**建议**：增加 Dry-run 机制，写入前先模拟显示结果。

### LLM 幻觉风险
- LLM 提取的记忆可能不准确
- 当前项目已有 schema 校验，LLM 输出必须验证

**建议**：
1. 采用 ADD-only 策略（mem0）— 只做添加，不做更新/删除
2. 置信度阈值 — 低于阈度的记忆标记为"待确认"
3. 人工确认流程 — 重要记忆（任务分配、决策）需要用户确认

### 数据隐私风险
- 飞书消息包含敏感信息
- 记忆存储需要权限控制

**建议**：
1. 本地存储加密（可选）
2. Scope 隔离 — 不同项目/群组的记忆严格隔离
3. 审计日志 — 记录所有读写操作

---

## 11. 总结

**推荐借鉴优先级**：

| 优先级 | 借鉴内容 | 来源 | 实现难度 | 价值 |
|--------|----------|------|----------|------|
| P0 | Prompt Grounding | agent-memory-server | 低 | 高 |
| P0 | 三层去重 | agent-memory-server | 中 | 高 |
| P1 | Bi-temporal 字段 | graphiti | 中 | 高 |
| P1 | 混合搜索 RRF | agent-memory-server | 中 | 高 |
| P2 | 策略遗忘 | agent-memory-server | 中 | 中 |
| P2 | Debounce 提取 | agent-memory-server | 低 | 中 |
| P3 | Recency Re-rank | agent-memory-server | 低 | 低 |

**V1.5 目标**：在比赛 Demo 可用的前提下，吸收 agent-memory-server 的 Prompt grounding、三层去重、混合搜索，以及 graphiti 的 bi-temporal 字段。保持当前项目的轻量级架构，不引入重型依赖（Redis、图数据库）。
