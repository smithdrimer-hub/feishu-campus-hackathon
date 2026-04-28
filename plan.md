# OpenClaw Memory Engine — 下阶段计划（基于开源项目分析）

## 开源项目借鉴总结

### 已使用的借鉴
- agent-memory-server：Prompt Grounding、三层去重、debounce 模式 ✓
- mem0：FakeLLMProvider 模式 ✓
- graphiti：bi-temporal valid_from/valid_to 概念 ✓

### 未使用但高价值的

| 项目 | 可借鉴能力 | 价值 | 复现成本 |
|------|-----------|------|---------|
| **mem0** | ADD-only 提取策略：LLM 只做添加不做更新，下游去重处理 | 降低 LLM 幻觉风险 | 低（只改 prompt 策略，不改代码架构） |
| **mem0** | Multi-level scope：user_id / session_id / agent_id 三级 | 支持"个人记忆 vs 群聊记忆 vs 项目记忆"隔离 | 中（需扩展 SourceRef 和 store 的过滤器） |
| **graphiti** | Episode 机制：原始数据不可变，所有推导事实可溯源到原始 Episode | 解决"记忆被错误合并后无法恢复"的问题 | 低（当前 `raw_events.jsonl` 天然是 Episode） |
| **agent-memory-server** | 4 种提取策略：Discrete / Summary / Preferences / Custom | 支持多类型记忆提取（不只有协作状态） | 中（需扩展 extractor 架构） |
| **langmem** | TrustCall 结构化提取（Pydantic schema 强制 + function calling） | 比纯 JSON prompt 更可靠的 LLM 输出 | 低（当前已有 validate_candidate_dict） |
| **cognee** | 多数据源 pipeline：文档→cognify→图谱的 ECL 管线 | 已做的 sync_doc/sync_tasks 可扩展 | 低（管线已打通） |
| **OpenMemory** | 衰减引擎：salience + recency + coactivation | 自动遗忘不重要的记忆 | 高（需新模块） |

## 下一阶段（提交前 7 天可用时间）

### P0: ADD-only 提取策略 + Episode 溯源完善（1 天）

**借鉴 mem0 + graphiti**，当前项目已具备基础但策略可优化。

**改什么**：
1. `LLMExtractor._build_prompt()`：在 prompt 末尾增加指令——"只提取新的协作信息，不判断是否与已有记忆冲突。下游系统会处理去重和版本管理。"
2. `store.py` 的 `append_raw_events()` 已经是不可变的 Episode 存储。在 `save_state()` 中每次写入时保留一个指向 `raw_events.jsonl` 行号的引用，让每条记忆可追溯回原始消息。
3. Golden Set 中增加 2-3 条"LLM 应添加但不更新"的样本

**不动**：memory_item 结构、upsert 逻辑、engine 流程。

### P1: Multi-level Scope 隔离（1 天）

**借鉴 mem0 的 user_id/session_id/agent_id 三级 scope**，当前只有 `project_id` 一个维度。

**改什么**：
1. `schema.py` 的 `MemoryItem` 增加可选字段 `scope_type: str`（"chat"/"doc"/"task"/"user"）和 `scope_id: str`
2. `store.py` 的 `list_items()` 增加 `scope_type` / `scope_id` 过滤参数
3. `extractor.py`：prompt 中标注每条消息的 scope 来源
4. `handoff.py`：支持按 scope 过滤输出

**不动**：engine 核心流程、提取方法、去重逻辑。

### P2: 语义搜索入口（1 天）

**不引入向量库**，而是在当前 `list_items()` 上增加按 `current_value` 关键词全文搜索。

**改什么**：
1. `store.py` 增加 `search_keywords(query: str, project_id=None)` 方法——遍历 `memory_state.json` 的 `current_value` / `rationale` / `source_refs.excerpt` 做子串匹配
2. `engine.py` 增加 `search()` 方法，返回排序后的 `ScoredMemory`（基础频率排序）
3. `run_golden_eval.py` 的对比模式可输出搜索测试结果

**不动**：向量嵌入、RRF 融合、reranker。

### P3: Decision Override 关联 + Owner 精度修复（0.5 天）

**现有问题修复**。

**改什么**：
1. `RuleBasedExtractor._extract_decision()`：当 key 是 `"current_decision_override"` 时，尝试用相同 `project_id:decision` 前缀匹配已有 item，复用其 key
2. `RuleBasedExtractor._extract_owner()`：正则改进，确保 `"负责人：张三负责"` 只提取 `"张三"`
3. 测试更新

### P4: 演示文档 + README 更新 + Golden Set 指标发布（0.5 天）

---

## 不做的项（明确排除）

- ❌ 向量数据库/嵌入模型（时间不够，且演示无视觉冲击）
- ❌ 图数据库（Neo4j/FalkorDB 部署成本高）
- ❌ Webhook 实时监听
- ❌ 策略遗忘 / 衰减引擎（OpenMemory 方案，需新模块）
- ❌ 多提取策略架构大重构
- ❌ 接入向量搜索的 RRF 融合

---

## 优先级说明

优先做 P0 和 P1，因为：
- ADD-only + Episode 溯源是对评委"LLM 提取错了怎么办"的最佳回答补充
- Multi-level Scope 是对评委"只做了群聊"的进一步回答——不是只做了群聊，是 chat/doc/task/user 都有结构区分了

P2 和 P3 可以并行。总共约 3 天工作量。