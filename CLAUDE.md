# OpenClaw Memory Engine

## 项目一句话

从飞书群聊消息中提取**结构化协作状态**（目标、负责人、决策、阻塞、下一步），生成中断续办交接摘要，不使用向量库，纯结构化 JSON/JSONL 存储。

## 目录结构

```
openclaw-memory/
  src/
    memory/           ← 核心引擎逻辑（与 Lark 解耦）
    adapters/         ← LarkCliAdapter + 命令分类
    safety/           ← 安全策略（只读/写入分离）
  tests/              ← 156 个测试，全部通过
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

| 文件 | 职责 |
|------|------|
| `src/memory/schema.py` | MemoryItem / SourceRef 数据模型 |
| `src/memory/store.py` | JSON/JSONL 持久化 + 三层去重 + bi-temporal 查询 |
| `src/memory/extractor.py` | RuleBasedExtractor + LLMExtractor + HybridExtractor |
| `src/memory/engine.py` | MemoryEngine: ingest → extract → upsert |
| `src/memory/candidate.py` | LLM 候选校验（schema 验证 + 证据锚点检查） |
| `src/memory/llm_provider.py` | LLM 接口 + FakeLLMProvider + OpenAIProvider |
| `src/memory/handoff.py` | 交接摘要 Markdown 生成 |
| `src/memory/action_planner.py` | 行动计划生成（非执行） |
| `src/memory/project_state.py` | V1.9 项目状态面板聚合 |
| `src/adapters/lark_cli_adapter.py` | 飞书 CLI 封装 + SafetyPolicy 集成 |
| `src/adapters/command_registry.py` | 命令分类（只读/写入/BLOCKED_DRY_RUN） |
| `src/safety/policy.py` | 安全策略 Decision |

## 版本状态

| 版本 | 功能 | 状态 |
|------|------|------|
| V1 | RuleBasedExtractor（12 种场景）、MemoryStore、安全策略 | 完整 |
| V1.1 | LLMExtractor + Schema 校验 + 规则兜底 + 证据锚点验证 | 完整 |
| V1.5 | 三层去重 + Prompt Grounding + Debounce + @提及解析 | 完整 |
| V1.6 | 否定极性检测 + 低置信度过滤 + Bi-temporal + member_status | 完整 |
| V1.7 | 真实 LLM 集成（OpenAIProvider、DeepSeek 已验证） | 完整 |
| V1.8 | 文档/任务数据源（sync_doc / sync_tasks）+ deadline 提取 | 半成品 |
| V1.9 | 关键词搜索 + 项目状态面板（project_state.py） | 完整 |
| V1.10 | HybridExtractor（规则优先 + LLM 补充）+ 分模式 Golden Set | 完整 |

## 运行方式

```bash
cd openclaw-memory

# 所有测试（156 个，~10s）
python -m unittest discover -s tests -v

# Golden Set 评测
python scripts/run_golden_eval.py                   # RuleOnly: 122/150 (81.3%)
python scripts/run_golden_eval.py --hybrid           # Hybrid:  127/150 (84.7%)

# 一键演示（Fake LLM）
python scripts/demo_run_example.py
```

## 当前评测指标（150 条 Golden Set）

| 模式 | 通过数 | 通过率 |
|------|--------|--------|
| RuleOnly | 122/150 | 81.3% |
| Hybrid (DeepSeek V4 Flash) | 127/150 | 84.7% |

Hybrid 模式规则优先，当规则结果为空、低置信度、含复杂语义信号时调用 LLM 补充。当前使用 DeepSeek V4 Flash，配置在 `config.local.yaml`。

## 安全边界

- **自动允许的只读命令**：`doctor`, `im +chat-search/list/mget`, `docs +fetch`, `task +search/+tasklist-search`, `task tasklists tasks --params -`
- **默认拦截的写入命令**：`im +messages-send/reply`, `docs +create/update`, `task +create/update/complete/comment/assign/followers/tasklist-create/tasklist-task-add`
- `docs +create --dry-run` 曾经实际创建文档，明确禁止作为安全保护机制

## 下一步方向

1. **修正 Golden Set 中 implicit_* 类的混合模式期望值**：给 8+ 个 case 加 `llm_expected_items`，使 hybrid 评测能正确评估 LLM 的隐式提取能力
2. **打通真实飞书端到端流程**：`lark-cli auth login` → 同步真实群消息 → 提取 → 交接摘要
3. **换更强 LLM**（如 GPT-4o 或 Claude）：当前 DeepSeek V4 Flash 无法处理"考虑/是否"等隐式决策