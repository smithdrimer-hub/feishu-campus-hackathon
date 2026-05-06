# Feishu Campus Hackathon · OpenClaw Memory Engine

> **The OS for hybrid human-AI teams.** 让 5 个人 + N 个 AI Agent 像 6 个真同事一样协作。

飞书 OpenClaw 赛道（企业级长程协作 Memory 系统）参赛项目。**核心代码在 [`openclaw-memory/`](openclaw-memory/)**。

---

## 🎬 30 秒看懂

OpenClaw Memory Engine 从飞书群消息、文档、任务、日历中**持续提取结构化协作状态**（目标 / 负责人 / 决策 / 阻塞 / 截止日 / 下一步 / 暂缓 / 成员状态），绑定原始消息证据，并把它们以**正确的形式**主动送到正确的**人或 AI Agent** 面前。

它的真正使命不是"记住聊天记录"——是消除企业协作中的**状态转移成本**（context rebuild），让团队的有效工作时间从 50% 提到 80%+。

```
人类用户 ←→ 飞书群聊 / 文档 / 任务
              ↓
        Memory Engine
        ↓        ↓        ↓
     个人简报  全组编排  AI Agent
        ↓        ↓        ↓
       回到飞书 · 卡片 · 证据链 · actor_type 审计
```

---

## 🎬 一天里的 6 张飞书卡片（真实演示）

```bash
cd openclaw-memory
python scripts/demo_movie.py --all --feishu     # 一键发完 6 张飞书卡片
```

| # | 时刻 | 卡片 | 系统能力 |
|---|------|------|----------|
| 🌅 | 08:32 通勤路上 | 个人晨报 — "你不在的 2 天里发生了 5 件事" | `morning_briefing` |
| 🎯 | 09:00 项目大群 | 全组任务编排 — 拉开堵塞口，多米诺式解锁 | `orchestrator` |
| 🚨 | 11:08 群里求救 | AI 风险分析 — 12 秒给出 P0/P1 行动建议 | `demo_agent_loop` |
| 📍 | 12:50 系统播报 | 阻塞热点预警 — 组织级洞察看趋势 | `blocker_hotspot` |
| 📊 | 18:00 站会自动 | 站会摘要 — Yesterday/Today/Blockers | `standup_summary` |
| ⚖️ | 18:30 CTO 私聊 | 决策审核台 — AI 候选 + 人裁定 | `review_desk` |
| 📋 | 19:50 突发离场 | 8 维度交接摘要 — 0 秒上岗 | `handoff` |

---

## 快速开始

```bash
cd openclaw-memory

# 全部测试（327 个）
python -m unittest discover -s tests -v

# Golden Set 评测
python scripts/run_golden_eval.py                  # RuleOnly: 150/150 (100.0%)
python scripts/run_golden_eval.py --hybrid         # Hybrid:   147/150 (98.0%)

# 5 场景 Benchmark（真实场景效能）
python scripts/run_benchmark.py --verbose

# 一键 3 幕剧 demo（口语对比 + AI Agent + 审计）
python scripts/demo_full_show.py --feishu

# 6 场景电影 demo（一天里的全场景）
python scripts/demo_movie.py --all --feishu

# 实时 AI Agent 监听（在群里说"风险大不大"自动触发）
python scripts/agent_listener_poll.py
```

---

## 文档导览

| 文档 | 用途 |
|------|------|
| [`openclaw-memory/README.md`](openclaw-memory/README.md) | **完整项目说明**（含比赛维度对应、AI Agent 闭环、Hybrid 调度策略） |
| [`CLAUDE.md`](CLAUDE.md) | Agent 入口 / 项目概览 |
| [`AGENTS.md`](AGENTS.md) | 项目执行约束（lark-cli adapter / 安全策略） |
| [`opensourse_code/`](opensourse_code/) | 同类开源项目调研（mem0 / Letta / GraphRAG） |
| [`TASKBOARD.md`](TASKBOARD.md) | 后续优化路线图 |

---

## 核心数据

| 维度 | 数字 |
|------|------|
| 单元测试通过 | **327 / 327**（10 skipped 是 chromadb 可选依赖） |
| Golden Set 评测 | RuleOnly 100.0% · Hybrid 98.0% |
| AI Agent 端到端 | **12 秒**（飞书发问 → AI 卡片送回） |
| 6 张飞书卡片 demo | 全部真实投递、可截图验证 |
| 测试场景类型 | 抗干扰 / 矛盾更新 / 多日演进 / 人员交接 / 效能对比 |
| Hybrid LLM 调用率 | 约 40%（vs 全 LLM 100%，节省 60% API 成本） |

---

## License

MIT
