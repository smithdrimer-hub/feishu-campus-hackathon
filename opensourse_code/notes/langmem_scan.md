# langmem (langchain-ai/langmem) - 快速扫描

## 1. 项目定位
**LangChain 官方**的记忆管理工具库，定位为"LangGraph Agent 的内置记忆方案"。不构建独立记忆基础设施，而是提供工具和背景处理器，让 LangGraph Agent 能够主动管理记忆或在后台自动提取知识。

## 2. 主要语言和技术栈
- **主要语言**: Python 3.10+
- **核心依赖**: LangChain/LangGraph, LangSmith, TrustCall (结构化 LLM 提取), LangChain OpenAI/Anthropic
- **存储**: 依赖 LangGraph 的 BaseStore 抽象，不自带存储
- **构建工具**: uv (hatchling)

## 3. 顶层目录结构
| 目录 | 用途 |
|------|------|
| `src/langmem/` | 核心库源码 |
| `src/langmem/knowledge/` | 记忆管理：工具创建 + 知识提取 |
| `src/langmem/graphs/` | LangGraph 图定义：背景处理器 |
| `src/langmem/short_term/` | 短期记忆：对话摘要 |
| `src/langmem/prompts/` | Prompt 优化 |
| `src/langmem/reflection.py` | `ReflectionExecutor` — 后台记忆管理 |
| `docs/` | 文档 |
| `examples/` | 示例 Notebook |

## 4. README 中对 memory 的定义
两种模式：
- **Hot Path**: Agent 在对话中主动使用工具（`manage_memory`/`search_memory`）来记录/检索记忆
- **Background**: `ReflectionExecutor` 异步运行，自动提取、整合、更新 Agent 知识
- 记忆类型：语义记忆（事实/偏好）、情景记忆（事件/经历）、程序性记忆（行为/流程）、用户画像
- "Core memory API that works with any storage system"

## 5. 关键源码路径
| 文件路径 | 说明 |
|----------|------|
| `src/langmem/__init__.py` | 入口，导出公开 API |
| `src/langmem/knowledge/tools.py` | 记忆工具：create/update/delete, search |
| `src/langmem/knowledge/extraction.py` | 知识提取引擎 (~84KB)，TrustCall 驱动 |
| `src/langmem/reflection.py` | `ReflectionExecutor` (~17KB)，后台记忆管理器 |
| `src/langmem/short_term/summarization.py` | 短期记忆摘要 (~39KB) |
| `src/langmem/graphs/semantic.py` | 语义记忆 LangGraph 子图 |
| `src/langmem/prompts/optimization.py` | Prompt 优化逻辑 |

## 6. 是否值得深入阅读
**Yes** — 轻量但功能完整 (~2000 行核心代码)，TrustCall 结构化提取模式比纯 Prompt 更可靠。

## 7. 对飞书协作 Memory Engine 的潜在价值
**Medium**

| 评估维度 | 价值 | 原因 |
|----------|------|------|
| Multi-scope support | Medium | LangGraph BaseStore namespace 隔离，扁平化 |
| Auditability | Low | 无显式记忆变更历史接口 |
| LLM extraction | **High** | TrustCall 结构化提取，Pydantic schema 定义，比 free-form 更可靠 |
| Adapter patterns | Low | 完全依赖 LangGraph BaseStore，不用 LangGraph 则无复用价值 |
| Provenance tracking | Low | 有时间戳但无原始对话溯源 |
