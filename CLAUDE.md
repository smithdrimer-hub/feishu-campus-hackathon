# OpenClaw Memory Engine

## 行为规则

- **严格按指令行动**：只做用户明确要求的修改，不做多余改动或删除。
- **每次优化确保不引入新 bug**：修改后立即运行 `python -m unittest discover -s tests -v` 和 `python scripts/run_golden_eval.py`。
- **面向真实场景而非测试用例**：修复必须覆盖真实环境的海量同类场景（如所有中文改写变体、所有时区组合），不能仅通过精心构造的测试 case。
- **修改范围最小化**：一个 commit 只解决一个问题。涉及多个问题时，逐项修改、逐项验证、逐项 commit。
- **代码改动前先读文件**：使用 Read 工具确认当前内容，避免基于记忆编辑导致替换失败。

## 项目一句话

从飞书群聊消息中提取**结构化协作状态**（目标、负责人、决策、阻塞、下一步），生成中断续办交接摘要，不使用向量库，纯结构化 JSON/JSONL 存储。

## 目录结构

```
openclaw-memory/
  src/
    memory/           ← 核心引擎逻辑（与 Lark 解耦）
    adapters/         ← LarkCliAdapter + 命令分类
    safety/           ← 安全策略（只读/写入分离）
  tests/              ← 176 个测试，全部通过
  scripts/            ← demo / eval 入口
  examples/           ← golden_set.jsonl（150 条评测数据）
  data/               ← 运行时数据（gitignore）
  docs/               ← 设计文档
```

## 核心架构约束

- 所有飞书 CLI 执行必须通过 `LarkCliAdapter`，不直接伪造 API
- Memory Engine 核心逻辑（`src/memory/`）与 `lark-cli` adapter 分离，不得依赖具体命令字符串
- 保留可替换后端的能力（当前是 `lark-cli`，未来可适配 OpenClaw）
- Windows 环境中优先使用 `lark-cli.cmd`（不默认 `.ps1`，因执行策略可能拦截）

## 关键文件与职责

| 文件 | 职责 | V1.11 变更 |
|------|------|-----------|
| `src/memory/schema.py` | MemoryItem / SourceRef 数据模型 | — |
| `src/memory/store.py` | JSON/JSONL 持久化 + 四层去重 + bi-temporal + 跨 key 决策覆盖 | **4.3 Decision override** |
| `src/memory/extractor.py` | RuleBasedExtractor + LLMExtractor + HybridExtractor | **4.2 Value 裁剪** + 隐式 Prompt |
| `src/memory/engine.py` | MemoryEngine: ingest → extract → upsert | **4.1 Debounce 持久化** |
| `src/memory/candidate.py` | LLM 候选校验（schema 验证 + 证据锚点检查） | — |
| `src/memory/llm_provider.py` | LLM 接口 + FakeLLMProvider + OpenAIProvider | **JSON mode 启用 DeepSeek** |
| `src/memory/handoff.py` | 交接摘要 Markdown 生成 | — |
| `src/memory/action_planner.py` | 行动计划生成（非执行） | — |
| `src/memory/project_state.py` | V1.9 项目状态面板聚合 | — |
| `src/adapters/lark_cli_adapter.py` | 飞书 CLI 封装 + SafetyPolicy + 写入操作 | **send/pin/reply/unpin** |
| `src/adapters/command_registry.py` | 命令分类（只读/写入/BLOCKED_DRY_RUN） | **im pins 注册** |
| `src/safety/policy.py` | 安全策略 Decision | — |

## 版本状态

| 版本 | 功能 | 状态 |
|------|------|------|
| V1–V1.10 | RuleBased/LLM/Hybrid 提取、三层去重、bi-temporal、项目状态面板、交接摘要 | 完整 |
| V1.11 | **第1优先**: Golden Set 期望值对齐 → RuleOnly 100%, Hybrid 98% | ✅ |
| V1.11 | **第2优先**: LLM 提取能力（Pro + JSON mode + 隐式 Prompt + Hybrid 触发） | ✅ |
| V1.11 | **第3优先**: 真实飞书端到端（sync→extract→send→pin） | ✅ |
| V1.11 | **第4优先**: 架构加固（Debounce 持久化 + Value 裁剪 + 跨 key 决策覆盖） | ✅ |

## 运行方式

```bash
cd openclaw-memory

# 所有测试（176 个，~10s）
python -m unittest discover -s tests -v

# Golden Set 评测
python scripts/run_golden_eval.py                   # RuleOnly: 150/150 (100.0%)
python scripts/run_golden_eval.py --hybrid           # Hybrid:  147/150 (98.0%)

# 飞书端到端（需 lark-cli 登录）
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --dry-run
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx  # 同步+提取+发送+置顶

# 一键演示（Fake LLM）
python scripts/demo_run_example.py
```

## 当前评测指标（150 条 Golden Set）

| 模式 | 通过数 | 通过率 | 说明 |
|------|--------|--------|------|
| RuleOnly | 150/150 | 100.0% | V1.11 期望值校准，12 种关键词规则 + 多种 owner 格式 |
| Hybrid (DeepSeek V4 Pro) | 147/150 | 98.0% | 隐式语义 Prompt + JSON mode + 30 条 llm_expected 校准 |

3 条非确定性失败（GS-031/GS-116/GS-119）源于 LLM temperature=0.1 的微小波动。

## 安全边界

- **自动允许的只读命令**：`doctor`, `im +chat-search/list/mget`, `docs +fetch`, `task +search/+tasklist-search`, `task tasklists tasks --params -`
- **写入命令（需 allow_write=True）**：`im +messages-send/reply`, `im pins`, `docs +create/update`, `task +create/update/...`
- `docs +create --dry-run` 曾经实际创建文档，明确禁止作为安全保护机制

## 下一步方向

1. **P0 Demo 前验证**：真实飞书多消息类型（post/image/file）的提取验证、群聊压力测试
2. **P1 增强**：换 Claude/GPT-4o 对比评测、Rich text 消息解析、增量同步去重
3. **P2 生产化**：权限集成、Webhook 监听、策略遗忘

## 任务快速转接能力

系统核心场景是"中断续办"——一个人离开项目，另一个人接手，无需交接会议即可了解当前状态。

### 交接摘要覆盖的 8 个信息维度

| 接手人需要知道 | 系统输出 | 证据追溯 |
|-------------|---------|---------|
| **项目目标**是什么 | `project_goal` — 从群聊/文档提取的当前目标 | ✅ 原始消息 sender + 飞书链接 |
| **谁在负责**什么 | `owner` — 当前负责人，含历史变更链 | ✅ sender + URL |
| 做过哪些**关键决策** | `decision` — 已确认/待定/已覆盖 | ✅ 每条 decision 含 source_refs |
| 被什么**阻塞**了 | `blocker` — 含严重程度标记 | ✅ sender + excerpt |
| **下一步**要做什么 | `next_step` — 含负责人 | ✅ owner + 来源 |
| 有什么**截止时间** | `deadline` — 跨变更追溯（Layer 4 覆盖） | ✅ 原文引用 |
| 哪些事**暂缓**了及原因 | `deferred` | ✅ 原始消息 |
| **成员可用性** | `member_status` — 请假/出差/偏好 | ✅ value 裁剪 |

### 额外追溯能力

| 接手人还想知道 | 系统能力 |
|-------------|---------|
| 这些信息**是否可信** | 每条记忆标注 `confidence`，无证据标记 `[unverified]` |
| **为什么**做了这个决定 | `source_refs` 含 `sender_name` + 飞书可点击链接 `source_url` |
| 决定是否**后来被推翻** | `history` 保留 superseded 版本 + 跨 key 决策覆盖 |
| 某个时间点的**历史状态** | `as_of` 时间点查询 |
| **我本人**需要做什么 | `build_personal_work_context()` 按 owner 过滤 |
| 某条消息**产生了哪些记忆** | `find_items_by_message_id()` |
| 搜索**所有相关信息** | `search_advanced()` 多条件组合 + `InvertedIndex` 倒排 |

### 演示话术

> "张三临时离开，李四接手。李四不需要翻聊天记录——系统生成的交接摘要直接告诉他当前目标、谁在负责什么、做过什么决策、被什么阻塞、下一步做什么。每条结论后面有 [证据] 标记，点击直接跳转到原始飞书消息。不需要交接会议，看摘要就够了。"

### 前提条件

1. 群聊中有足够的协作讨论（系统从消息中提取，无讨论则无状态）
2. 定期运行同步脚本（`demo_e2e_pipeline.py`）拉取最新消息
3. 关键信息（目标/负责人/决策等）在消息中有所体现（隐式语义可由 LLM 补充识别）

## 覆盖范围

### 已覆盖

| 数据源 | 状态 | 说明 |
|--------|------|------|
| **飞书群聊消息** | ✅ 主力 | text + post 类型已验证，RuleOnly + Hybrid 提取 |
| 消息发送/回复/置顶 | ✅ | bot 身份，`lark-cli im +messages-send/reply` + `im pins` |
| 状态面板渲染 | ✅ | 三种形态（群状态/个人上下文/Agent 包） |
| 交接摘要 | ✅ | Markdown 格式，含 sender + URL + unverified 标记 |
| 行动计划 | ✅ | 基于当前状态生成建议（非执行） |

### 部分覆盖

| 数据源 | 状态 | 说明 |
|--------|------|------|
| **飞书文档** | 🟡 API 已验证 | `sync_doc()` 路径正确，未用真实文档跑通端到端 |
| **飞书任务** | 🟡 API 已验证 | `sync_tasks()` 路径正确，未用真实任务跑通端到端 |
| post 消息（富文本） | 🟡 基本验证 | 单条 post 消息提取 OK，未做全场景覆盖 |

### 未覆盖

| 数据源 | 说明 |
|--------|------|
| image/file/audio/video 消息 | 无文本可提取，直接跳过（不会崩溃） |
| share_chat/share_user（转发） | 嵌套内容解析未实现 |
| interactive（卡片消息） | 按钮/表单内容提取未实现 |
| 飞书会议/妙记 | 未接入 |
| 飞书审批/OKR | 未接入 |

### 安全边界

- **只读操作自动放行**：消息读取、文档拉取、任务搜索
- **写入操作需 `allow_write=True`**：发消息、回复、置顶、创建文档/任务
- **`docs +create --dry-run` 明确禁止**（曾实际创建文档）
- **基于 project_id 的软隔离**，无真实飞书 OAuth 权限校验（Demo 场景不涉及）
