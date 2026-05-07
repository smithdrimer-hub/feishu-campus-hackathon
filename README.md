# OpenClaw Memory Engine

> **飞书生态的企业协作记忆引擎** — 从群聊、文档、任务、日历、会议纪要中自动提取结构化协作状态，通过审核台治理，形成可追溯、可闭环执行的组织记忆。

## 解决什么痛点

企业协作中，沟通在飞书群里发生，但状态散落在不同时间、不同人的消息中。有人请假或离职时，接手的人需要翻几十页聊天记录才能搞清楚"现在是什么情况"。

系统从一个群聊的散落消息中，自动识别出 8 种协作状态（目标/负责人/决策/DDL/阻塞/下一步/暂缓/成员状态），并生成 6 种二阶协作模式（交接风险/依赖阻塞链/阻塞热点/停滞任务/截止风险/责任域归纳）。每条记忆都绑定原始消息证据和飞书链接。

## 30 秒看懂

```
飞书群聊/文档/任务/日历/会议纪要
              ↓
       Memory Engine（提取 → 审核 → 推理）
              ↓
   ┌──────────┼──────────┐
   ↓          ↓          ↓
状态面板   触发执行   交接摘要
(交互式卡片)(任务/提醒)(8 维度)
   ↓          ↓          ↓
       回到飞书 · 证据链可追溯
```

## 核心亮点

- **Selector 模式**：精确信号→规则提取，模糊信号→LLM，纯问题→跳过。不是简单"规则不够就调 LLM"
- **P0 可信度体系**：决策分 4 级、冲突检测并排展示、审核台管家裁定、阻塞 5 状态生命周期
- **闭环执行**：5 条触发规则（任务创建/阻塞提醒/风险预警/低置信度提问/阻塞解除通知），24h 冷却防刷屏
- **Work Pattern Memory**：已有记忆的二阶归纳层，6 种模式，不扫原始消息，纯基于结构化记忆
- **可审计**：每条记忆带 sender_name + 飞书消息 URL + evidence anchor

## 量化指标

| 指标 | 数值 |
|------|------|
| 单元测试 | **327** 全部通过 |
| Golden Set | **150/150** (100%) |
| 按类型 P/R/F1 | 已分解（next_step P=0.65 诚实标注短板） |
| 版本跨度 | V1.0 → V1.18 (10 天 18 个版本) |
| 真实飞书端到端 | 6 次全链路验证（user/bot、全数据源、Hybrid+向量、WebSocket） |

## 快速开始

```bash
cd openclaw-memory

# 一键演示
python scripts/demo_run_example.py

# 运行所有测试（327 个）
python -m unittest discover -s tests -v

# Golden Set 评测
python scripts/run_golden_eval.py                   # RuleOnly: 150/150 (100.0%)
python scripts/run_golden_eval.py --compare         # 三模式对比

# 端到端流水线
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --trigger --mode auto

# 审核台
python scripts/demo_review_desk.py --data-dir data/ --project-id xxx --chat-id oc_xxx

# 自动化运行 + WebSocket 监听
python scripts/unified_listener.py --hybrid
python scripts/auto_runner.py --once
```

## 文档

| 文档 | 说明 |
|------|------|
| [`openclaw-memory/README.md`](openclaw-memory/README.md) | 完整项目说明 + 演示场景 + 架构 + 安全边界 |
| [`CLAUDE.md`](CLAUDE.md) | 项目概览 / Agent 开发入口 |
| [`AGENTS.md`](AGENTS.md) | 项目执行约束 |
| [`用户使用手册.md`](用户使用手册.md) | 面向用户的功能说明 + 功能边界 |
| [`演示指南.md`](演示指南.md) | 10 分钟演示流程 + 命令速查 |
| [`opensourse_code/`](opensourse_code/) | 开源项目调研（mem0/agent-memory-server/langmem 等） |

## 项目结构

```
openclaw-memory/
  src/memory/     核心引擎 (schema/store/extractor/engine/pattern/trigger/executor/card)
  src/adapters/   飞书适配 (CLI 封装/WebSocket 事件监听/命令注册)
  src/safety/     安全策略 (只读自动/写入确认/多用户隔离)
  scripts/        演示脚本 (端到端/审核台/自动化/统一监听/卡片/编排/demo 电影)
  tests/          327 个单元测试
  examples/       Golden Set 150 条 + 10 场景数据
```

## 团队成员

| 成员 | 主要负责 |
|------|---------|
| 沈哲熙 | V1.0~V1.18 核心引擎全栈（schema/store/extractor/engine/pattern/trigger/executor/card/review/listener） |
| 徐悦 | AI Agent Loop / 6 场景电影 Demo / Orchestrator / Morning Briefing / 演示文档 |

---

> 系统沉淀的不是对人的主观评价，而是可审计的协作事实。所有结论都有原始飞书证据支持，并可由项目管家审核。
