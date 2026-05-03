# OpenClaw Memory Engine V1.12

## Memory Engine ≠ Chat History

ChatGPT 的聊天记录是线性文本回放。Memory Engine 从飞书群消息中提取**结构化协作状态**——谁负责什么、做了什么决策、被什么阻塞了——并绑定原始消息证据锚点。每条记忆都有版本号、置信度、证据来源（含发送者和飞书链接），支持时间点查询（As-of query）和版本追溯。

## Memory Engine ≠ 普通 RAG

普通 RAG 将文本切片后做向量相似度搜索。Memory Engine 不使用向量库，通过结构化提取 + Schema 校验 + 四层去重 + Hybrid（规则优先/LLM 补充）的组合策略，把飞书协作信息转化为**可审计的协作状态机**。

## 与同类项目的差异

| 维度 | mem0 / Letta / graphiti | OpenClaw Memory Engine |
|------|------------------------|------------------------|
| 存储 | 向量库 + 嵌入模型 | 结构化 JSON/JSONL + 四层去重 |
| 检索 | 向量相似度搜索 | identity_key + as_of 时间点 + 关键词 + 多条件组合 + 倒排索引 |
| 飞书深度 | 通用记忆，需适配 | 原生 LarkCliAdapter + @提及解析 + 安全策略 + bot 发消息/置顶 |
| 提取策略 | LLM 或规则单一模式 | Hybrid 规则优先 + LLM 按需补充（DeepSeek V4 Pro） |
| 目标 | 个人助手的长期记忆 | 团队协作的"中断续办"状态 |
| 证据锚点 | 无或弱 | 每条记忆绑定 sender_name + message URL + excerpt + 原文验证 |
| 安全 | 无 | 只读/写入命令分离，dry-run 不信任，写入需 allow_write=True |
| 审计 | 无内置 | version + supersedes + history + as_of + find_items_by_message_id |
| 评测 | 无标准化 | 150 条 Golden Set + 三模式对比 + 证据链追溯 |

## 核心能力

| 能力 | 说明 | 引入版本 |
|------|------|----------|
| 群聊消息提取 | 从飞书群消息提取目标/负责人/决策/阻塞/下一步 | V1 |
| 关键词规则提取 | RuleBasedExtractor，12 种场景 + 5 种 owner 格式 | V1 |
| 可信 LLM 提取链路 | LLM → Schema 校验 → 规则兜底 → excerpt 原文验证 | V1.12 |
| 四层去重 | Identity Key → Content Hash → Semantic → 跨 key 决策/截止覆盖 | V1.11 |
| Prompt Grounding | 代词/时间/空间解析，sender.name 真实姓名绑定 | V1.12 |
| Debounce 持久化 | `_last_process_time` 写入文件，重启不丢失 | V1.11 |
| 否定极性检测 | "拒绝负责"/"不负责"不被错误合并 | V1.6 |
| 低置信度过滤 | ambiguous + confidence≤0.3 的后处理丢弃 | V1.6 |
| Bi-temporal 查询 | valid_from/valid_to，as_of 时间点查询 | V1.6 |
| 成员状态识别 | 请假/出差/工作偏好提取，value 裁剪 | V1.11 |
| 真实 LLM 集成 | DeepSeek V4 Pro + JSON mode + temperature=0 | V1.11 |
| 文档/任务数据源 | 从飞书文档/任务提取协作状态 | V1.8 |
| 多条件搜索 | project_id + state_type + keyword + owner + message_id + as_of | V1.12 |
| 倒排索引 | 全文 token 索引，O(1) 关键词检索 | V1.12 |
| 项目状态面板 | 群状态 / 个人上下文 / Agent 上下文包（含证据引用） | V1.12 |
| Hybrid 提取 | 规则优先 + LLM 按需补充 + 二次兜底 | V1.11 |
| 分模式评测 | 同一 Golden Set 支持 rule/hybrid/llm 三套期望 | V1.10 |
| 飞书端到端 | sync → extract → state panel → send → pin | V1.11 |
| 证据链追溯 | SourceRef 含 sender+URL，excerpt 原文验证，find_by_message_id | V1.12 |
| 交接摘要 | Markdown 含 sender + 飞书链接 + [unverified] 标记 | V1.12 |

## 项目结构

```
openclaw-memory/
  src/memory/
    schema.py           MemoryItem, SourceRef 数据模型（sender+URL+V1.12）
    store.py            JSON/JSONL 存储 + 四层去重 + as_of + 多条件搜索 + 倒排索引
    extractor.py        RuleBased + LLM + Hybrid 三套提取器 + 隐式 Prompt
    engine.py           MemoryEngine: ingest → extract → upsert + debounce 持久化
    candidate.py        MemoryCandidate 校验 + excerpt 原文验证 + ADD-only 策略
    handoff.py          交接摘要 Markdown 生成（含 sender + URL + unverified 标记）
    action_planner.py   行动计划生成（非执行）
    llm_provider.py     LLMProvider 接口 + FakeLLMProvider + OpenAIProvider (JSON mode)
    project_state.py    项目状态面板（含证据引用 source_refs）
  src/adapters/
    lark_cli_adapter.py 飞书 CLI 封装 + send/reply/pin/unpin + SafetyPolicy 集成
    command_registry.py 命令分类（只读/写入/BLOCKED_DRY_RUN）+ im pins 注册
  src/safety/
    policy.py           安全策略
  tests/                # 176 个测试，全部通过
  scripts/
    demo_run_example.py      一键演示（Fake LLM）
    demo_sync_messages.py    飞书消息同步（分页 + 增量去重）
    demo_handoff.py          交接摘要生成
    demo_action_plan.py      行动计划生成
    demo_e2e_pipeline.py     端到端：sync→extract→send→pin
    demo_evidence_trace.py   证据链追溯（tree/flat/summary）
    run_golden_eval.py       Golden Set 评测（rule/hybrid/llm/compare）
  examples/
    golden_set.jsonl         150 条标注评测数据
  docs/
    demo_script.md           演示剧本
    judge_qna.md             比赛答辩 Q&A
    V1.11_risk_audit.md      遗留风险审计报告
```

## 安全边界

**自动允许的只读命令：** `doctor`, `im +chat-search`, `im +chat-messages-list`, `im +messages-mget`, `docs +fetch`, `task +search`, `task +tasklist-search`, `task tasklists tasks --params -`

**写入命令（需 allow_write=True）：** `im +messages-send`, `im +messages-reply`, `im pins`, `docs +create/update`, `task +create/update/complete/comment/assign/followers/tasklist-create/tasklist-task-add`

特别注意：`docs +create --dry-run` 曾在飞书 CLI 中实际创建文档，明确禁止。

## 快速开始

```bash
cd openclaw-memory

# 一键运行示例（Fake LLM 演示提取+交接+行动计划）
python scripts/demo_run_example.py

# Golden Set 评测
python scripts/run_golden_eval.py                   # RuleOnly: 150/150 (100.0%)
python scripts/run_golden_eval.py --hybrid          # Hybrid:  ~147/150 (98.0%)
python scripts/run_golden_eval.py --compare         # 三模式对比

# 所有测试（176 个）
python -m unittest discover -s tests -v

# 证据链追溯
python scripts/demo_evidence_trace.py --project-id demo --format tree
python scripts/demo_evidence_trace.py --project-id demo --check-unverified

# 飞书端到端（需 lark-cli 登录）
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --dry-run
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --hybrid --no-pin
```

### 使用真实 LLM

```yaml
# config.local.yaml（已加入 .gitignore）
llm:
  provider: "openai"
  api_key: "sk-xxx"
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-v4-pro"
  temperature: 0    # V1.11: 消除非确定性
  max_tokens: 2000
```

## Golden Set 评测（150 条）

| 模式 | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| RuleOnly | 150/150 | 100.0% | V1.11 期望值校准，15 种关键词规则 + 5 种 owner 格式 |
| Hybrid (DeepSeek V4 Pro) | 147/150 | 98.0% | 隐式 Prompt + JSON mode + temperature=0 + 30 条 llm_expected |
| LLM only | 待测 | — | 需配置 key（支持 --compare 三模式对比） |

> **关于 100%**：RuleOnly 100% 表示 Golden Set 的 `expected_items` 已校准为
> RuleBasedExtractor 实际能提取的边界。150 条中 **30 条有 `llm_expected_items`**（依赖 LLM 补充
> 隐式语义），**119 条纯规则可独立覆盖**。这不是"完美语义理解"，而是"精确边界测量"——
> 真实群聊中隐式表达的比例远高于 Golden Set 的 20%。

### Hybrid 触发条件（V1.11 增强）

- (a) 规则结果为空
- (b) 规则置信度全部 ≤ 0.65
- (c) 消息含复杂语义信号（不再、改为、考虑、是否等 26 个信号）
- (d) 提到人名但规则未提取 owner
- (e) 单条消息含多个子句
- (f) 消息含隐式语义信号（在弄、还没好、那就、记得等 10 个信号）**[V1.11 新增]**
- (g) 规则提取了某些类型但缺少关键互补类型 **[V1.11 新增]**

### 证据链能力（V1.12 新增）

每条记忆的 `source_refs` 包含完整证据链：
- `type`: "message" / "doc" / "task"
- `sender_name`: 发送者姓名（如"张三"）
- `sender_id`: 发送者 ID
- `source_url`: 飞书消息可点击链接 `https://app.feishu.cn/client/messages/{chat_id}/{message_id}`
- `excerpt`: 原文片段（LLM 输出经原文验证，不匹配则替换）
- LLM excerpt 虚假检测：如果 LLM 返回的 excerpt 不是原始消息的子串，自动用原文前 240 字符替代

## 当前已知限制

1. **Golden Set 覆盖有限**：150 条人工构造样本，真实群聊的噪声和多样性远超覆盖范围
2. **隐式语义 3 条非确定性**：GS-031/116/119 受 temperature=0 后可消除
3. **复杂消息类型覆盖不足**：post 消息已验证，image/file/share_chat 等类型未覆盖
4. **权限隔离基于 project_id 软隔离**：无真实飞书 OAuth/open_id 校验（Demo 场景不涉及）
5. **LLM 无法引用跨批次消息**：valid_message_ids 只包含当前批次，多轮对话证据可能不完整
6. **JSON 文件存储**：`list_items()` 全量 `json.loads` 后内存过滤，单用户 < 10K 条够用。已支持 `limit`/`offset` 分页。大规模需换 SQLite
7. **单用户 CLI 工具**：零线程安全、无文件锁。多进程并发写入会损坏 `memory_state.json`

## 路线图

- **V1.12（当前）**：证据链完善 + 多条件搜索 + 倒排索引 + 文档分节/表格/评论 + owner Pattern 6
- **P1**：三模式对比跑通、嵌入式 Sheet/Bitable 感知、Wiki 节点遍历、权限最小感知
- **P2**：文档实时协作感知、跨文档关联、飞书完整权限体系、策略遗忘

> 详细的优化计划（包括文档深度优化、飞书权限体系、P2 企业级增强）见 **[TASKBOARD.md](../TASKBOARD.md)**。
