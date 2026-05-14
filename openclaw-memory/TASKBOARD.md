# OpenClaw Memory Engine — 任务看板

> 最后更新：2026-05-14 | V1.19 → V1.20

---

## P0：修 Bug（已完成 ✅）

6/6 已修复，442 测试全过。

---

## P1：功能闭环（已完成 ✅）

7/7 已实施。FEAT-1~FEAT-7 全部到位，442 测试全过。

---

## V1.20 Phase A：干净演示数据（已完成 ✅）

| 任务 | 文件 | 状态 |
|------|------|------|
| A1 演示脚本 | `data/demo_script_v2.md` — 10天×6角色 177条 | [x] |
| A2 消息发送器 | `scripts/gen_demo_v2.py` | [x] |
| A3 新演示群 | `oc_0bea5e19a6171a54bcb7cfcda6cd9676` — 177条已发送 | [x] |
| A4 @bot 修复 | `extractor.py` — `_is_bot_query()` 三重检测 | [x] |
| A5 --include-app-msgs | `demo_e2e_pipeline.py` — 演示模式不跳过bot消息 | [x] |

---

## V1.20 Phase B：卡片去污 + 视觉升级

| 任务 | 描述 | 状态 |
|------|------|------|
| B1 证据去前缀 | `_evidence_note()` 去 `[src]`，过滤系统来源 | [x] |
| B2 徽章精简 | `_status_badge()` ASCII → 单字符 | [x] |
| B3 节标题去标记 | 去除 `[GOAL][TEAM][!!][NEXT]` 等前缀 | [x] |
| B4 脏数据过滤 | `_is_displayable()` + `_strip_sender_prefix()` | [x] |
| B5 数据重提取 | demo1 重新 process（@bot污染清零） | [x] |
| B6 证据智能去重 | 相似度跳过 + 飞书原生回复替代内嵌证据 | [x] |
| F1-F5 提取质量 | 目标去回顾 + owner去裸名 + 阻塞去噪音 + 文档去空段 + 卡片去裸owner | [x] |

### B6 详情

**B6a — 相似度跳过**：当前 `_evidence_note()` 仅跳过 `excerpt == body` 精确匹配。改为相似度匹配：剥离sender前缀后，前40字符相同、或excerpt是body子串（反之）→ 跳过。

**B6b — 引用格式**：证据改用 `> sender：excerpt` 引用格式（lark_md 支持），体现精确溯源。

改动范围：`card_renderer.py` `_evidence_note()` 内部 ~10 行。

---

## V1.20 Phase C：Agent Memory Document（待实施）

| 任务 | 描述 | 状态 |
|------|------|------|
| C1 文档生成器 | `src/memory/agent_memory.py` — 11段Markdown，全部复用现有数据源 | [ ] |
| C2 流水线接入 | `demo_e2e_pipeline.py` 新增 `--agent-doc` flag | [ ] |
| C3 飞书发布 | 调用 `adapter.create_doc()` → 群内发送文档链接 + 置顶 | [ ] |

**可复用数据源**：`build_agent_context_pack()`, `build_group_project_state()`, `build_dependency_graph()`, `generate_all_patterns()`, `VectorStore.search()`。只需一个组装函数。

---

## V1.20 Phase D：端到端演示（待实施）

新群 `oc_0bea5e19a6171a54bcb7cfcda6cd9676` 完整演示流程。

---

## P2：扩展数据源（待实施）

| 任务 | 依赖 | 状态 |
|------|------|------|
| INT-1 飞书知识库 | lark-wiki adapter | [ ] |
| INT-2 飞书 OKR | lark-okr adapter | [ ] |
| INT-3 多维表格读写 | lark-base adapter | [ ] |
| INT-4 考勤集成 | lark-attendance adapter | [ ] |
| INT-5 会议闭环 | 现有 minutes + handoff | [ ] |

## P3：长期规划（待实施）

团队分析仪表盘 / 跨项目依赖 / 冲刺回顾 / 风险预测 / 向量搜索入主线 / SQLite 优化
