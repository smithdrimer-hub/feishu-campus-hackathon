# OpenClaw Memory Engine · 能力全景 Inventory

> 项目代码库规模实测：**19 模块 + 21 脚本 + 327 测试 + 4 docs**。
> 此文档作为剩余开发/演示/视频脚本撰写时的 reference table。

---

## 1. 模块全景（19 个）

### 1.1 核心引擎层（5）

| 模块 | 关键 API | 一句话 |
|------|---------|--------|
| `schema.py` | `MemoryItem` / `SourceRef` / `source_ref_from_event/doc/task` | 数据模型 + 证据锚点 |
| `store.py` | `MemoryStore` + `InvertedIndex` | 4 层去重 / history / as-of 时间点 / 倒排索引 |
| `engine.py` | `MemoryEngine.ingest_events` | ingest → extract → upsert + debounce 持久化 |
| `extractor.py` | `RuleBased` / `LLM` / `Hybrid` (含 Selector) | 三套提取器 + 12 关键词规则 + LLM JSON mode |
| `candidate.py` | `MemoryCandidate` / `validate_candidate_dict` | LLM 输出 schema 校验 + excerpt 原文锚点验证 |

### 1.2 推理层（3）

| 模块 | 关键 API | 一句话 |
|------|---------|--------|
| `pattern_memory.py` | 6 个 `generate_*` + `generate_all_patterns` | 6 类二阶模式（handoff_risk / blocker_hotspot / dependency_blocker / stale_task / responsibility_domain / deadline_risk_score） |
| `orchestrator.py` | `build_dependency_graph` / `orchestrate` | 多米诺解阻塞 + 名字归一 + dependency_owner 优先 |
| `action_planner.py` | `generate_action_plan` / `PlannedAction` | 从 memory 推 next_step / blocker / DDL 等行动提议 |

### 1.3 状态视图层（1 文件，7 视图）

`project_state.py` 是个**视图工厂**，提供 7 种角度：

| 视图函数 | 给谁看 | 用在哪 |
|---------|-------|-------|
| `build_group_project_state` + `render_group_state_panel_text` | 群成员 | 飞书状态面板 |
| `build_agent_context_pack` ⭐ | **AI Agent** | 给 LLM 喂的结构化 context pack（含 source_refs）|
| `build_personal_work_context` + `render_personal_context_text` ⭐ | 个人 | "我"的任务/决策/风险（静态清单） |
| `build_cross_project_context` + `render_cross_project_text` ⭐ | 高管 | 跨项目工作总览 |
| `render_standup_summary` | 群成员 | yesterday/today/blockers 三段式 |
| `render_confirmation_checklist` ⭐ | 会议主持 | 决策/任务/阻塞确认清单 |
| `build_morning_briefing` + `render_morning_briefing_text` | 个人 | **早安卡**（动态：变化/等你/deadline/队友/行动） |

⭐ = 至今未在飞书演示过。

### 1.4 动作层（3）

| 模块 | 关键 API | 一句话 |
|------|---------|--------|
| `action_trigger.py` | `ActionTrigger` | V1.14 的 3 触发规则：next_step→task / blocker→alert / deadline+blocker→warning |
| `action_executor.py` | `ActionExecutor` | 把 PlannedAction 真的执行成 lark-cli 调用（创建任务/发消息/@提及） |
| `action_log.py` | `write_action_log` / `has_recent_action` | 幂等性 + 冷却 + 审计 |

### 1.5 输出层（2）

| 模块 | 一句话 |
|------|-------|
| `handoff.py` | 8 维度 Markdown 交接摘要（含 sender + URL + unverified 标记） |
| `reply_handler.py` | @bot 指令路由（状态/风险/审核/站会/交接）+ 确认回复解析 |

### 1.6 LLM / 搜索 / 工具（5）

| 模块 | 一句话 |
|------|-------|
| `llm_provider.py` | LLMProvider 接口 + FakeLLMProvider + OpenAIProvider (DeepSeek/OpenAI/Claude 都接) |
| `vector_store.py` | ChromaDB 向量存储（可选依赖） |
| `embedding_provider.py` | embedding 抽象（FakeEmbedding / OpenAIEmbedding） |
| `date_parser.py` | 中文相对日期解析 + `deadline_is_imminent` |
| `__init__.py` | 包入口 |

---

## 2. 脚本全景（21 个）

| # | 脚本 | 我们用到了？| 在故事中的作用 |
|---|-----|------------|--------------|
| 1 | `demo_run_example.py` | ✅ smoke test | 1 行命令验证全栈跑通（Fake LLM）|
| 2 | `demo_sync_messages.py` | 文档引用 | 同步飞书群聊到 raw_events |
| 3 | `demo_sync_doc.py` | 文档引用 | 同步飞书文档+任务 |
| 4 | `demo_e2e_pipeline.py` | 文档引用 | 端到端：sync→extract→send→pin |
| 5 | `demo_handoff.py` | ❌ 未演 | 8 维度交接 markdown（被 demo_movie SCENE 9 吸收）|
| 6 | `demo_action_plan.py` | **❌ 未演** ⭐ | **从 memory → 行动计划（含执行）** |
| 7 | `demo_review_desk.py` | 思路被 SCENE 8 吸收 | 审核台 CLI 操作 |
| 8 | `demo_evidence_trace.py` | **❌ 未演** ⭐ | **证据链 tree 可视化** |
| 9 | `run_golden_eval.py` | ✅ benchmark report 引用 | Golden Set 150 条 P/R/F1 |
| 10 | `demo_benchmark.py` | ✅ benchmark report 引用 | 延迟基准 |
| 11 | `run_benchmark.py` | ✅ benchmark report 引用 | 5 真实场景（我们做的）|
| 12 | `run_realistic_scenarios.py` | 探索阶段用过 | 早期口语场景 runner |
| 13 | `verify_p0.py` | ❌ | V1.16 P0 修复验证 |
| 14 | `demo_hybrid_search.py` | **❌ 未演** ⭐ | **vector + keyword + entity 三路召回** |
| 15 | `auto_runner.py` | 引用 | daemon 模式 + WebSocket |
| 16 | `agent_listener.py` (我们) | ❌ | WebSocket 备用版 |
| 17 | `agent_listener_poll.py` (我们) | ✅ 12s 端到端 | Polling 触发 AI agent |
| 18 | `demo_card_handoff.py` (我们) | ✅ 探索阶段 | 卡片版交接 |
| 19 | `demo_full_loop.py` (我们) | ✅ Rule vs Hybrid | 口语对比 3 卡 |
| 20 | `demo_full_show.py` (我们) | ✅ 一键 3 幕剧 | 评委一行命令看完 |
| 21 | `demo_movie.py` (我们) | ✅ **6 幕剧 hero** | day-in-life 主演 |
| 22 | `demo_agent_loop.py` (我们) | ✅ 12s 闭环 | AI Agent 单次推理 |

⭐ = 没演但**应该演**的功能。

---

## 3. 当前 Demo 覆盖度评估

### 3.1 故事场景 → 模块映射

| 场景 | 用到的模块 |
|------|----------|
| SCENE 1 早安 | `build_morning_briefing` ✅ |
| SCENE 2 编排 | `orchestrator.orchestrate` ✅（有 dependency_owner seed）|
| SCENE 5 阻塞热点 | `pattern_memory.generate_blocker_hotspot` ✅ |
| SCENE 7 站会 | items 直接构卡片（**没用** `render_standup_summary`，质量损失）|
| SCENE 8 审核台 | `review_status` + `decision_strength` + `conflict_status` ✅ |
| SCENE 9 交接 | items + `generate_all_patterns` ✅（没用 `handoff.generate_handoff`）|
| AI Agent | `run_agent_loop` + `pattern_memory` + DeepSeek ✅ |

### 3.2 已用到的能力（绿）

`schema` · `store` · `engine` · `extractor` · `pattern_memory` · `orchestrator` · `morning_briefing` · `LarkCliAdapter` · DeepSeek · `selector_mode` · `Hybrid` · `decision_strength` · `review_status` · 6 类 Pattern · `actor_type/agent_id` · 卡片端 LLM 防幻觉

### 3.3 **未用到但应该露面的能力（红）**

| 能力 | 为何重要 | 怎么补 |
|------|---------|-------|
| 🔴 **action_planner + action_executor + action_log** | rubric "完整闭环" 硬证据：系统不只是建议，是真的去做事 | demo 里加 1 个"系统自动创建飞书任务"场景 |
| 🔴 **action_trigger** 3 条规则 | V1.14 的核心：从 memory diff 自动触发动作 | README 添加触发规则表 |
| 🔴 **demo_evidence_trace** 证据链 tree | rubric "可审计" 硬证据 | 视频脚本里展示一段 tree 输出 |
| 🔴 **build_agent_context_pack** | rubric "AI 关键作用" 最直接的回答 | README 加一段说明 + 在 demo_agent_loop 里引用 |
| 🔴 **bi-temporal `as_of` 查询** | rubric "时序记忆" 硬证据 | benchmark report 加一段"时间旅行"演示 |
| 🟡 **render_standup_summary** | 现有，质量比我手搓的好 | 替换 SCENE 7 实现 |
| 🟡 **handoff.generate_handoff** | 现有，质量比我手搓的好 | SCENE 9 兜底用它 |
| 🟡 **demo_hybrid_search** | rubric "检索" 项 | README 加一段说明 |
| 🟡 **render_confirmation_checklist** | 会议纪要场景 | （时间够再做） |
| 🟢 **R4 主动提问** | 低置信度反过来问人 | 视频脚本旁白带过 |

---

## 4. 路线评估：偏不偏？

### 4.1 路线没偏（核心证据）

- 比赛 rubric 三维度都有充分证据：
  - **完整性 50%**：6 张飞书卡片 + AI Agent 12s 闭环 + 5 场景 benchmark
  - **创新 25%**：AI 一等公民 + Hybrid Selector + 卡片防幻觉 + 多米诺解阻塞
  - **技术实现 25%**：327 tests + 4 层去重 + Schema 校验 + 6 层正交架构
- 提交物三件套齐全：白皮书（README）+ 可运行 Demo + Benchmark Report

### 4.2 但还能拉高的地方

1. **"完整闭环"还能更硬**：现在演到 AI Agent 给建议，但**没演到 action_executor 真的创建飞书任务**。补一个场景就完整了。
2. **"可审计"还能更亮**：`demo_evidence_trace` 输出树图很 wow，没用是浪费。
3. **"时序记忆"是赛题原话**：bi-temporal as_of 查询应该在 benchmark report 加一节。
4. **`build_agent_context_pack` 是赛题"AI 关键作用"的最佳答案**：完全没提。

---

## 5. 故事讲好还能加什么（优先级排序）

| 优先 | 补什么 | 时长 | 在哪 |
|-----|-------|-----|------|
| 🔥 P0 | "action 自动执行"场景：AI 风险分析 → 创建飞书任务 → action_log 审计 | 30 min | 加 SCENE 4 |
| 🔥 P0 | benchmark report 加一节"时间旅行（bi-temporal as_of）"演示 | 15 min | docs/benchmark_report |
| ⚡ P1 | README 加 `action_trigger` 3 规则表 + `build_agent_context_pack` 说明 | 15 min | README |
| ⚡ P1 | demo_evidence_trace tree 输出截图 / 视频片段 | 10 min | 视频脚本 |
| 📌 P2 | SCENE 7 / SCENE 9 改用现有 `render_standup_summary` / `generate_handoff` | 20 min | demo_movie |
| 📌 P2 | demo_hybrid_search 1 段话提及 | 5 min | README |

---

## 6. 这份 inventory 怎么用

每次进入新阶段（写文档/写视频脚本/录屏前），先打开本表查 3 件事：

1. **此阶段相关的能力都在 §3 里吗？** 红色项必须 cover 或显式说明跳过原因。
2. **此阶段相关的脚本都在 §2 里吗？** 没用到的脚本是不是该一句话提一下。
3. **此阶段对应 rubric 哪个维度？** 有没有 §4.2 的可拉高点。

每次审视完，把发现 commit 到本文档，作为团队记忆。
