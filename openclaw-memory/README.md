# OpenClaw Memory Engine V1.10

## Memory Engine ≠ Chat History

ChatGPT 的聊天记录是线性文本回放。Memory Engine 从飞书群消息中提取**结构化协作状态**——谁负责什么、做了什么决策、被什么阻塞了——并绑定原始消息证据锚点。每条记忆都有版本号、置信度、证据来源，支持时间点查询（As-of query）和版本追溯。

## Memory Engine ≠ 普通 RAG

普通 RAG 将文本切片后做向量相似度搜索。Memory Engine 不使用向量库，通过结构化提取 + Schema 校验 + 三层去重 + Hybrid（规则优先/LLM 补充）的组合策略，把飞书协作信息转化为**可审计的协作状态机**。

## 与同类项目的差异

| 维度 | mem0 / Letta / graphiti | OpenClaw Memory Engine |
|------|------------------------|------------------------|
| 存储 | 向量库 + 嵌入模型 | 结构化 JSON/JSONL + 三层去重 |
| 检索 | 向量相似度搜索 | identity_key 精确匹配 + as_of 时间点查询 |
| 飞书深度 | 通用记忆，需适配 | 原生 LarkCliAdapter + @提及解析 + 安全策略 |
| 提取策略 | LLM 或规则单一模式 | Hybrid 规则优先 + LLM 按需补充 |
| 目标 | 个人助手的长期记忆 | 团队协作的"中断续办"状态 |
| 证据锚点 | 无或弱 | 每条记忆绑定原始消息 ID + 摘要 |
| 安全 | 无 | 只读/写入命令分离，dry-run 不信任 |
| 审计 | 无内置 | version + supersedes + history + as_of |
| 评测 | 无标准化 | 150 条 Golden Set + 三模式对比 |

## 核心能力

| 能力 | 说明 | 引入版本 |
|------|------|----------|
| 群聊消息提取 | 从飞书群消息提取目标/负责人/决策/阻塞/下一步 | V1 |
| 关键词规则提取 | RuleBasedExtractor，12 种场景 | V1 |
| 可信 LLM 提取链路 | LLM → Schema 校验 → 规则兜底 | V1.1 |
| 三层去重 | Identity Key → Content Hash → Semantic Similarity | V1.5 |
| Prompt Grounding | 代词/时间/空间解析，author_map 绑定 | V1.5 |
| Debounce Coalescing | 避免频繁触发 LLM 的安全合并 | V1.5 |
| 否定极性检测 | "拒绝负责"/"不负责"不被错误合并 | V1.6 |
| 低置信度过滤 | ambiguous + confidence≤0.3 的后处理丢弃 | V1.6 |
| Bi-temporal 查询 | valid_from/valid_to，as_of 时间点查询 | V1.6 |
| 成员状态识别 | 请假/出差/工作偏好提取 | V1.6 |
| 真实 LLM 集成 | DeepSeek / OpenAI / Poe 兼容 | V1.7 |
| 文档数据源 | 从飞书文档提取协作状态 | V1.8 |
| 任务数据源 | 从飞书任务提取协作状态 | V1.8 |
| 关键词搜索 | 基于 token 的活跃记忆搜索 | V1.9 |
| 项目状态面板 | 群状态 / 个人上下文 / Agent 上下文包 | V1.9 |
| Hybrid 提取 | 规则优先 + LLM 按需补充 + 内容相似度合并 | V1.10 |
| 分模式评测 | 同一 Golden Set 支持 rule/hybrid/llm 三套期望 | V1.10 |

## 项目结构

```
openclaw-memory/
  src/memory/
    schema.py           MemoryItem, SourceRef 数据模型（bi-temporal）
    store.py            JSON/JSONL 存储 + 三层去重 + as_of 查询
    extractor.py        RuleBased + LLM + Hybrid 三套提取器
    engine.py           MemoryEngine: ingest → extract → upsert → debounce
    candidate.py        MemoryCandidate 校验 + 证据锚点验证 + ADD-only 策略
    handoff.py          交接摘要 Markdown 生成
    action_planner.py   行动计划生成（非执行）
    llm_provider.py     LLMProvider 接口 + FakeLLMProvider + OpenAIProvider
    project_state.py    项目状态面板（群/个人/Agent 三种形态）
  src/adapters/
    lark_cli_adapter.py 飞书 CLI 封装 + SafetyPolicy 集成
    command_registry.py 命令分类（只读/写入/BLOCKED_DRY_RUN）
  src/safety/
    policy.py           安全策略
    confirmation.py     写入确认辅助
  src/utils/
    logger.py           日志工具
  tests/                # 当前 156 个测试
    test_v15_improvements.py
    test_llm_extractor.py
    test_llm_provider_openai.py
    test_multi_source.py
    test_project_state.py
    test_hybrid.py
    test_e2e.py
    test_conflict_resolution.py
    test_memory_update.py
    test_safety_policy.py
  scripts/
    demo_run_example.py     一键演示（Fake LLM）
    demo_sync_messages.py   飞书消息同步
    demo_handoff.py         交接摘要生成
    demo_action_plan.py     行动计划生成
    run_golden_eval.py      Golden Set 评测（rule/hybrid/llm/compare）
    verify_p0.py            P0 验证脚本
  examples/
    golden_set.jsonl        150 条标注评测数据
  docs/
    demo_script.md          演示剧本
    evaluation_report.md    评测报告（历史数据，最新见 run_golden_eval 输出）
    judge_qna.md            比赛答辩 Q&A
```

## 安全边界

**自动允许的只读命令：** `doctor`, `im +chat-search`, `im +chat-messages-list`, `im +messages-mget`, `docs +fetch`, `task +search`, `task +tasklist-search`, `task tasklists tasks --params -`

**默认拦截的写入命令：** `im +messages-send/reply`, `docs +create/update`, `task +create/update/complete/comment/assign/followers/tasklist-create/tasklist-task-add`

特别注意：`docs +create --dry-run` 曾在飞书 CLI 中实际创建文档，明确禁止。

## 快速开始

```bash
cd openclaw-memory

# 一键运行示例（Fake LLM 演示提取+交接+行动计划）
python scripts/demo_run_example.py

# 运行 Golden Set 评测
python scripts/run_golden_eval.py                         # RuleOnly: 122/150 (81.3%)
python scripts/run_golden_eval.py --hybrid                # Hybrid:  127/150 (84.7%)
python scripts/run_golden_eval.py --hybrid --verbose      # 详情模式
python scripts/run_golden_eval.py --compare               # 三模式对比

# 运行所有测试（156 个）
python -m unittest discover -s tests -v
```

### 使用真实 LLM

配置 `config.local.yaml`（已在 `.gitignore` 中）：

```yaml
llm:
  provider: "openai"
  api_key: "sk-xxx"
  base_url: "https://api.deepseek.com/v1"    # 兼容 OpenAI/DeepSeek/Poe
  model: "deepseek-v4-flash"
  temperature: 0.1
  max_tokens: 2000
```

### 从真实飞书群同步消息

```bash
# 先确保 lark-cli 已登录
lark-cli.cmd auth login

# 同步消息 → 提取 → 交接摘要
python scripts/demo_sync_messages.py --chat-id <chat_id> --limit 100 --project-id demo
python scripts/demo_handoff.py --project-id demo
python scripts/demo_action_plan.py --project-id demo
```

## Golden Set 评测（150 条）

| 模式 | 通过 | 通过率 | 说明 |
|------|------|--------|------|
| RuleOnly | 122/150 | 81.3% | 12 种关键词规则，覆盖明确关键词场景 |
| Hybrid (DeepSeek) | 127/150 | 84.7% | 规则优先 + LLM 补充，首次超过 RuleOnly |
| LLM only | — | — | 需配置 key（支持 --compare 对比） |

### Hybrid 触发条件

规则结果不充分时自动调用 LLM 补充：
- (a) 规则结果为空
- (b) 规则置信度全部 ≤ 0.65
- (c) 消息含复杂语义信号（不再、改为、考虑、是否、我来等）
- (d) 提到人名但规则未提取 owner
- (e) 单条消息含多个子句

## 当前已知限制

1. **关键词规则覆盖率有限**：仅识别含明确关键词的消息。隐式语义（如"张三在弄前端"）需 LLM。
2. **只处理群聊消息**：文档/任务/会议数据源是 P1 计划，sync_doc/sync_tasks 已实现但未在真实飞书验证。
3. **Debounce 无后台调度器**：安全的 coalescing，非异步 trailing-edge（重启后丢失）。
4. **权限隔离**：基于 project_id 的软隔离，无真实身份认证。
5. **DeepSeek 对隐式决策不敏感**："考虑""是否"等无关键词的决策，DeepSeek V4 Flash 无法识别（换 GPT-4o/Claude 可改善）。
6. **Golden Set implicit_* 类期望值未对齐**：8 个 case 的 `llm_expected_items` 需根据 LLM 实际输出微调。

## 路线图

- **V1.10（当前）**：HybridExtractor + 分模式 Golden Set + DeepSeek 集成
- **P1 候选**：Golden Set implicit 期望值对齐、换更强 LLM、打通真实飞书端到端、语义搜索
- **P2**：策略遗忘、Recency Re-rank、权限集成、质量评估自动化