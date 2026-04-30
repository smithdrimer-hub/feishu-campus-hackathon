# Memory Engine V1 Demo 代码框架说明

## 当前 V1 / V1.1 实现内容

本次实现了一个最小可运行的“中断续办”Memory Engine 框架，代码位于 `openclaw-memory/`。V1 的闭环是：

飞书群消息读取 -> 保存 `raw_events.jsonl` -> 提取结构化协作状态 -> 更新 `memory_state.json` -> 生成交接摘要 -> 生成下一步行动计划。

Memory 的定位不是聊天摘要，而是保存当前仍会影响执行的协作状态，包括负责人、项目目标、关键决策、暂缓事项、阻塞风险、下一步任务，并保留原始消息证据锚点。

V1.1 在 V1 基础上新增“LLM 结构化提取 + schema 校验 + 规则兜底”的可信提取模块。当前 LLM 后端是 `FakeLLMProvider`，用于演示严格 JSON 输出、候选校验和 fallback 链路，不接真实外部 LLM。

## 目录结构

```text
openclaw-memory/
  README.md
  config.example.yaml
  data/
  scripts/
    demo_sync_messages.py
    demo_handoff.py
    demo_action_plan.py
    demo_run_example.py
  src/
    adapters/
      command_registry.py
      lark_cli_adapter.py
    memory/
      candidate.py
      schema.py
      store.py
      extractor.py
      llm_provider.py
      engine.py
      handoff.py
      action_planner.py
    safety/
      policy.py
      confirmation.py
    utils/
      logger.py
    main.py
  tests/
    test_memory_update.py
    test_conflict_resolution.py
    test_safety_policy.py
```

## 核心文件作用

- `adapters/lark_cli_adapter.py`：所有飞书 CLI 调用的唯一入口，业务代码不得绕过它直接调用 `subprocess`。
- `adapters/command_registry.py`：维护 V1 已验证命令表，区分只读命令、写入命令和禁止 dry-run 命令。
- `safety/policy.py`：默认只允许自动执行只读命令，拦截写入命令。
- `memory/schema.py`：定义 `MemoryItem` 和 `SourceRef`，每条 Memory 都绑定原始消息证据。
- `memory/candidate.py`：定义 `MemoryCandidate` 和 schema 校验逻辑，非法 LLM 输出不能进入 memory state。
- `memory/llm_provider.py`：定义 `LLMProvider` 接口和 `FakeLLMProvider` 示例后端。
- `memory/store.py`：使用 JSON/JSONL 存储原始事件和当前结构化状态。
- `memory/extractor.py`：包含 `BaseExtractor`、`RuleBasedExtractor` 和 `LLMExtractor`，LLM 失败时自动规则兜底。
- `memory/engine.py`：串联原始事件、提取器和状态更新逻辑。
- `memory/handoff.py`：生成“中断续办”交接摘要。
- `memory/action_planner.py`：生成下一步行动计划，只生成 plan，不执行写入。

## Demo 脚本运行方式

一键运行 V1.1 示例：

```powershell
python .\openclaw-memory\scripts\demo_run_example.py
```

从飞书群同步消息：

```powershell
python .\openclaw-memory\scripts\demo_sync_messages.py --chat-id <chat_id> --limit 100 --project-id openclaw-memory-demo
```

生成中断续办交接摘要：

```powershell
python .\openclaw-memory\scripts\demo_handoff.py --project-id openclaw-memory-demo
```

生成下一步行动计划：

```powershell
python .\openclaw-memory\scripts\demo_action_plan.py --project-id openclaw-memory-demo
```

运行测试：

```powershell
python -m unittest discover -s .\openclaw-memory\tests
```

## 安全边界

V1 自动允许的只读命令包括：

- `doctor`
- `im +chat-search`
- `im +chat-messages-list`
- `im +messages-mget`
- `docs +fetch`
- `task +search`
- `task +tasklist-search`
- `task tasklists tasks --params -`

V1 默认禁止自动执行所有写入命令，包括发消息、回复消息、创建或更新文档、创建或更新任务、完成任务、评论任务、分配任务、创建任务清单等。

特别注意：`docs +create --dry-run` 曾经实际创建文档，因此当前实现明确禁止把它作为安全保护机制。

## V1.1 校验策略

LLM 输出只接受严格 JSON：`{"candidates": [...]}`。不接受 markdown code fence、自由摘要或额外解释文本。

每个 candidate 必须包含 `project_id`、`state_type`、`key`、`current_value`、`rationale`、`owner`、`status`、`confidence`、`source_refs`、`detected_at`。

`source_refs` 必须非空，并且每个证据锚点必须包含 `chat_id`、`message_id`、`excerpt`、`created_at`。其中 `message_id` 必须能匹配输入 raw events，避免生成没有来源依据的状态。

任一 candidate 校验失败时，整批 LLM 输出被丢弃，不写入 `memory_state.json`，系统回退到 `RuleBasedExtractor`。

## 当前限制

- V1.1 的 LLM provider 是 Fake 实现，只验证可信提取链路，不代表真实模型效果。
- 行动计划只生成建议，不执行真实写入。
- 文档类高级命令没有 `schema docs...`，当前只把 `docs +fetch` 放入自动只读能力。
- 消息全文搜索、文档搜索、user 身份发消息仍需要额外 scope，未纳入 V1 自动闭环。
- 本地存储使用 JSON/JSONL，适合 demo，不适合多用户并发生产系统。

## 后续扩展方向

- 将 `FakeLLMProvider` 替换为真实 OpenAI、本地模型或 OpenClaw provider。
- 增加人工确认后的 `--execute` 写入流程，但继续通过 `LarkCliAdapter` 执行。
- 增加 OpenClaw backend adapter，保持 Memory Engine 核心逻辑不依赖具体 CLI 命令。
- 增加更完整的证据回放、状态 diff 和观察员演示页面。
- 在确认飞书 scope 与频控后，扩展消息全文搜索、文档搜索和任务更新能力。
