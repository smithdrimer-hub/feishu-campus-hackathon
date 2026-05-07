# memory-template (LangGraph Memory Service) - 快速扫描

## 1. 项目定位

LangGraph Memory Service 是一个由 LangChain 官方团队提供的 **长期记忆服务模板**，定位为"让任意 LangGraph Agent 都能连接的用户级记忆管理服务"。它不是一个完整框架，而是一个可部署的模板（template），展示了如何用 LangGraph 构建 debounced、schema-driven 的记忆提取与更新管线。

它的核心价值在于回答了 memory 服务的三个关键设计问题：
1. 何时形成记忆？→ 使用 **debouncing**（防抖），在用户停止交互后延迟处理
2. 记忆包含什么？→ 通过 **memory schemas**（JSON Schema）定义，LLM 按 schema 提取
3. 记忆如何更新？→ 两种模式：**patch**（增量修补单文档）和 **insert**（追加新条目）

## 2. 主要语言和技术栈

| 维度 | 详情 |
|------|------|
| 语言 | Python 3.11+ |
| 包管理 | setuptools + pyproject.toml |
| 核心框架 | LangGraph 0.3+（状态图 + 调度） |
| 记忆管理 | `langmem` >= 0.0.25（trustcall 库封装，处理 patch/insert 模式） |
| LLM 路由 | `langchain.chat_models.init_chat_model()` |
| 支持的 LLM | Anthropic Claude（默认）、OpenAI GPT |
| 存储 | LangGraph 内置 store（`get_store()`），支持 TTL 和向量索引 |
| 部署 | LangGraph Cloud（`langgraph.json` 配置），CLI `langgraph-cli` |
| 嵌入式存储 | SQLite（checkpoint），向量索引（OpenAI text-embedding-3-small, 1536 dim） |

依赖极简，核心依赖只有：`langgraph`, `langgraph-checkpoint`, `langchain`, `langchain-openai`, `langchain-anthropic`, `langgraph-sdk`, `langmem`, `python-dotenv`。

## 3. 顶层目录结构

```
memory-template/
├── README.md                   # 详细的设计文档
├── pyproject.toml              # 项目配置与依赖
├── langgraph.json              # LangGraph 部署配置（graphs, store, env）
├── src/
│   ├── memory_graph/           # 记忆图——核心记忆提取与更新逻辑
│   │   ├── __init__.py
│   │   ├── graph.py            # 主图：遍历 memory_types，并行处理
│   │   ├── configuration.py    # MemoryConfig + Configuration dataclass + DEFAULT_MEMORY_CONFIGS
│   │   └── utils.py
│   └── chatbot/                # 示例聊天机器人——展示如何调用记忆服务
│       ├── __init__.py
│       ├── graph.py            # ChatState + bot 节点 + schedule_memories（debounce）
│       ├── configuration.py    # ChatConfigurable（user_id, delay_seconds, model）
│       ├── prompts.py          # 系统提示模板
│       └── utils.py
├── tests/
│   └── integration_tests/      # 集成测试
│       └── test_graph.py       # 使用 @langsmith.unit 装饰器的评估用例
└── static/                     # 架构图与流程图
```

项目非常精简——只有约 10 个 Python 源文件，核心逻辑集中在 `memory_graph/graph.py`（~90 行）和 `chatbot/graph.py`（~90 行）。

## 4. README 中对 memory 的定义

README 对 memory 的核心定义：

> "Memory lets your AI applications learn from each user interaction. It lets them become effective as they adapt to users' personal tastes and even learn from prior mistakes."

> "This template shows you how to build and deploy a long-term memory service that you can connect to from any LangGraph agent so they can manage user-scoped memories."

Memory 被定义为 **用户范围的结构化文档集合**，通过 JSON Schema 定义"shape"，由 LLM 从对话中提取并持续更新。两种 schema 模式：

- **Patch**（如 `User` 档案）：维护单个 JSON 文档，增量修补，始终只有一份"当前状态"
- **Insert**（如 `Note` 笔记）：追加新条目，可选更新已有条目，数量无上限

Memory 存储在 LangGraph store 中，按 `(user_id, memory_type_name)` 命名空间隔离。

## 5. 关键源码路径

| # | 文件路径（相对于 repo 根） | 说明 |
|---|------|------|
| 1 | `src/memory_graph/graph.py` | 记忆图主入口：`@entrypoint` graph 遍历 memory_types 并行调用 `process_memory_type` |
| 2 | `src/memory_graph/configuration.py` | `MemoryConfig` dataclass（name, description, parameters, update_mode）+ `DEFAULT_MEMORY_CONFIGS`（User profile + Note）|
| 3 | `src/chatbot/graph.py` | 示例聊天图：`bot` 节点从 store 搜索记忆注入 prompt，`schedule_memories` 节点使用 `after_seconds` debounce |
| 4 | `src/chatbot/configuration.py` | `ChatConfigurable`（user_id, delay_seconds, memory_types, model） |
| 5 | `langgraph.json` | 部署配置：graph 入口定义、store TTL/索引配置、嵌入式向量设置 |
| 6 | `src/memory_graph/utils.py` | 记忆格式化辅助函数 |
| 7 | `src/chatbot/utils.py` | `format_memories()` 将 store items 格式化为 prompt 文本 |
| 8 | `src/chatbot/prompts.py` | 系统提示模板，包含 `{user_info}` 占位符 |
| 9 | `tests/integration_tests/test_graph.py` | 评估测试用例，使用 LangSmith `@unit` 装饰器 |

## 6. 是否值得深入阅读

**Yes — 值得，但原因不同。**

这个项目的代码量很小（~300 行），但它的设计思想和架构模式非常有参考价值。它展示了一个完整的记忆服务从 debounce 调度 → schema-driven 提取 → patch/insert 更新 → 命名空间存储的完整流程。与 Cognee 的"大而全"不同，memory-template 是"小而精"，专注于 memory 管理的核心抽象。

## 7. 对飞书协作 Memory Engine 的潜在价值

**评级：Medium**

具体原因：

| 飞书 Memory Engine 关注点 | memory-template 的相关性 | 详情 |
|------|------|------|
| **Multi-scope support** | 中 | 仅支持 `user_id` 单层 scope。但它的 `(user_id, memory_type_name)` 双命名空间设计可以扩展为 `(tenant_id, scope_type, scope_id, memory_type)` 多级命名空间。思路清晰但不完整。 |
| **Auditability** | 低 | 项目本身没有内置审计追踪。LangGraph checkpoint 提供了状态快照，但这不是专门的 audit log 机制。需要自行扩展。 |
| **LLM extraction patterns** | 高 | 这是本项目最核心的贡献。它的 `patch` / `insert` 双模式设计、JSON Schema → LLM tool calling 的映射、以及 `langmem` 库的 `create_memory_store_manager()` 都是非常值得参考的抽象。`PatchDoc` tool 的增量更新策略（避免全量重写导致信息丢失）是高质量的模式。 |
| **Adapter patterns** | 低 | 项目未涉及数据库适配器——它直接使用 LangGraph 内置 store。存储层是黑盒的。 |
| **Provenance tracking** | 低 | 没有显式的 provenance 或溯源机制。Memory 条目只包含 schema 定义的数据字段，没有来源、时间戳或版本信息。 |

**总结**：memory-template 的价值不在于它的代码实现（这太简单了），而在于它提出的 **memory 管理设计范式**——debouncing 策略、schema-driven 提取、patch vs insert 更新模式。这些是飞书 Memory Engine 可以借鉴的**架构理念**，而非直接复用的代码。
