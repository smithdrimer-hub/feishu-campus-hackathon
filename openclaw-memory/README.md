# OpenClaw Memory Engine V1.6

## Memory Engine ≠ Chat History

ChatGPT 的聊天记录是线性文本回放。Memory Engine 从飞书群消息中提取**结构化协作状态**——谁负责什么、做了什么决策、被什么阻塞了——并绑定原始消息证据锚点。每条记忆都有版本号、置信度、证据来源，支持时间点查询（As-of query）和版本追溯。

## Memory Engine ≠ 普通 RAG

普通 RAG 将文本切片后做向量相似度搜索。Memory Engine 不使用向量库，而是通过结构化提取 + Schema 校验 + 规则兜底 + 三层去重的组合策略，把飞书协作信息转化为**可审计的协作状态机**。

## 与同类项目的差异

| 维度 | mem0 / Letta / graphiti | OpenClaw Memory Engine |
|------|------------------------|------------------------|
| 存储 | 向量库 + 嵌入模型 | 结构化 JSON/JSONL + 三层去重 |
| 检索 | 向量相似度搜索 | identity_key 精确匹配 + as_of 时间点查询 |
| 飞书深度 | 通用记忆，需适配 | 原生 LarkCliAdapter + @提及解析 + 安全策略 |
| 目标 | 个人助手的长期记忆 | 团队协作的"中断续办"状态 |
| 证据锚点 | 无或弱 | 每条记忆绑定原始消息 ID + 摘要 |
| 安全 | 无 | 只读/写入命令分离，dry-run 不信任 |
| 审计 | 无内置 | version + supersedes + history + as_of |

## Memory 的定义

只保留"当前仍会影响执行"的状态：项目目标、负责人、关键决策、暂缓事项、阻塞风险、下一步任务、成员状态。被后续消息推翻的旧决策标记为 `superseded` 并保留在 `history` 中。每条结构化状态都绑定原始消息证据锚点。

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
| Golden Set 评测 | 30 条样本，12 种场景，持续追踪准确率 | V1.6 |
| 成员状态识别 | 请假/出差/工作偏好提取 | V1.6 |

## 安全边界

**自动允许的只读命令：** `doctor`, `im +chat-search`, `im +chat-messages-list`, `im +messages-mget`, `docs +fetch`, `task +search`, `task +tasklist-search`, `task tasklists tasks --params -`

**默认拦截的写入命令：** `im +messages-send/reply`, `docs +create/update`, `task +create/update/complete/comment/assign/followers/tasklist-create/tasklist-task-add`

特别注意：`docs +create --dry-run` 曾在飞书 CLI 中实际创建文档，V1 明确禁止。

## 快速开始

```bash
cd openclaw-memory

# 一键运行示例（Fake LLM 演示提取+交接+行动计划）
python scripts/demo_run_example.py

# 运行 Golden Set 评测（29/30 场景通过）
python scripts/run_golden_eval.py

# 运行所有测试
python -m unittest discover -s tests -v
```

从真实飞书群同步消息：

```powershell
python scripts/demo_sync_messages.py --chat-id <chat_id> --limit 100 --project-id demo
python scripts/demo_handoff.py --project-id demo
python scripts/demo_action_plan.py --project-id demo
```

## 项目结构

```
openclaw-memory/
  src/memory/
    schema.py       MemoryItem, SourceRef 数据模型
    store.py        JSON/JSONL 存储 + 三层去重 + bi-temporal
    extractor.py    RuleBasedExtractor + LLMExtractor + author_map
    engine.py       MemoryEngine: ingest → extract → upsert
    candidate.py    MemoryCandidate 校验 + 证据锚点验证
    handoff.py      交接摘要生成
    action_planner.py 行动计划生成
    llm_provider.py LLMProvider 接口 + FakeLLMProvider
  src/adapters/
    lark_cli_adapter.py  飞书 CLI 封装
    command_registry.py  命令分类（只读/写入/dry-run）
  src/safety/
    policy.py       安全策略
  tests/
    test_v15_improvements.py  59 个测试
  examples/
    golden_set.jsonl  30 条标注评测数据
  scripts/
    demo_run_example.py  一键演示
    demo_sync_messages.py 飞书消息同步
    run_golden_eval.py    Golden Set 评测
  docs/
    demo_script.md   演示剧本
    evaluation_report.md  评测报告
    judge_qna.md     Q&A
```

## 当前已知限制

1. **关键词规则覆盖率有限**：仅识别含明确关键词的消息。真实场景需接入 LLM。
2. **只处理群聊消息**：文档/任务/会议数据源是 P1 计划。
3. **Debounce 无后台调度器**：安全的 coalescing，非异步 trailing-edge。
4. **权限隔离**：基于 project_id 的软隔离，无真实身份认证。
5. **Fake LLM Provider**：不接真实外部 LLM。
6. **无 deadline 解析**：日期表达多样，留待 LLM 处理。

## 路线图

- **V1.8**（当前）：文档/任务数据源接入、真实 LLM 集成
- **P1 候选**：语义搜索入口、deadline 解析 prompt 修复、decision_override 关联
- **P2**：策略遗忘、Recency Re-rank、权限集成、质量评估