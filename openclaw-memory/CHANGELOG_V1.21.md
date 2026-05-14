# V1.21 变更报告

> 基准版本：V1.19 (83ff331)  
> 目标：从"可演示"升级为"可信"  
> 生成日期：2026-05-15

---

## 总览

| 维度 | V1.19 | V1.21 |
|------|-------|-------|
| 测试 | 420 | **440** |
| Golden Set | 150/150 | **149/150** |
| 提取质量 | @bot 污染、裸名 owner、PM 汇报当 blocker | 0 噪音、member_status 精确、blocker 干净 |
| 卡片 | ASCII 前缀 `[GOAL][!!][src]` | 无前缀、干净正文、证据智能跳过 |
| Agent 入口 | 无 | MCP Server + Agent Memory Document |
| Demo 数据 | demo1 含测试命令污染 | demo_v2 177 条精心编写消息 |

---

## Phase A：干净的演示数据

- `data/demo_script_v2.md`：10 天 × 6 角色、177 条自然群聊（80% 协作 + 20% 生活干扰）
- `scripts/gen_demo_v2.py`：脚本化消息发送器
- 新演示群：`oc_0bea5e19a6171a54bcb7cfcda6cd9676`
- `--include-app-msgs` flag：演示模式不跳过 bot 身份消息

## Phase B：卡片视觉升级

- 移除全部 ASCII 前缀：`[GOAL]` `[TEAM]` `[!!]` `[src]` `[LOOP]` `[WARN]` 等
- `_evidence_note()` 去前缀，智能跳过与正文相同的证据（相似度匹配）
- `_strip_sender_prefix()`：剥离"李四：李四："重复前缀
- `_is_displayable()`：卡片级脏数据过滤
- 证据改为飞书原生回复（`send_evidence_replies()`），可点击跳转原消息
- `_status_badge()`：ASCII 徽章 → 单字符标记

## Phase C：Agent Memory Document

- `src/memory/agent_memory.py`：11 段 Agent Memory Pack 生成器
- `--agent-doc` flag：流水线集成
- 飞书发布：创建文档 → 写入内容 → 群内发送链接
- 文档含：项目概览、团队、目标、决策表、任务表、阻塞看板+依赖图、DDL、协作模式、待确认项、近期变更、语义索引

## Phase D：提取质量系统化

### @bot 过滤 (`extractor.py`)
- `_is_bot_query()`：三重检测——文本含 @bot、at_list 标记、状态关键词+疑问词组合
- 9 种查询模式全部正确过滤

### 目标提取修复 (`extractor.py`)
- `_extract_goal()`：区分"设定目标"和"回顾目标"
- "辛苦大家了 冲刺目标基本达成"→`needs_review`，不再当高置信度 goal

### Owner 提取修复 (`extractor.py`)
- `_build_owner_item()`：无职责描述的裸名不提取
- "张三: 张三"→ 不再出现

### Blocker 提取修复 (`extractor.py`)
- `_extract_blocker()`：过滤"阻塞清单"等纯列表标题

### Prompt 语用分类 (`extractor.py`)
- 新增"消息语用分类"规则：信号发布 / 信号引用 / 无关消息
- 新增"多话题消息处理"规则
- 禁止提取规则从 6 条扩展到 10 条

### Sanitize 净化层 (`engine.py`)
- `_sanitize_items()`：提取后、存储前集中清洗
- sender 前缀剥离、@bot 丢弃、文档噪音过滤
- member_status 裸名过滤 + 同值去重（core: 请假/不在/休假/出差/习惯用/擅长）
- blocker 收尾词过滤（感谢/庆祝/收官 → 丢弃）
- decision 总结词 AND-NOT 决策词过滤
- next_step 生活词 AND-NOT 任务动词过滤
- 非 message 来源（doc/task）豁免净化

### Hybrid 事件级替换 (`extractor.py`)
- `_get_suspicious_message_ids()`：识别可疑条目
- `extract()`：可疑事件 → LLM 重提取 → 替换 RuleOnly 结果

## 其他修复

### P0 Bug 修复 (6个)
- BUG-1: reply_handler 确认误判（"不确认"被误判为确认）
- BUG-2: action_trigger 跨时区冷却失效
- BUG-3: 跨源冲突检测盲区
- BUG-4: 编排器名字误匹配
- BUG-5: maintenance() 过度调用
- BUG-6: 卡片文本静默截断

### P1 功能闭环 (7个)
- FEAT-1: 任务闭环（sync_task_status 回流生成 diff 事件）
- FEAT-2: 文档结构化提取（_chunk_doc_markdown 输出 extraction_hints）
- FEAT-3: 卡片↔Markdown 生命周期对齐
- FEAT-4a: send_alert dispatch 修复
- FEAT-4b: 编排器→执行器桥接（bridge_orchestrated_to_actions）
- FEAT-5: 新成员入职触发（handle_member_added）
- FEAT-6: 多规则工作流扩展（R6-R11）
- FEAT-7: 内联任务确认卡片（render_confirmation_card）

---

## 新增文件

| 文件 | 用途 |
|------|------|
| `src/memory/agent_memory.py` | Agent Memory Document 生成器 |
| `scripts/mcp_server.py` | MCP Server（Agent 可调用工具） |
| `scripts/gen_demo_v2.py` | Demo v2 消息发送器 |
| `scripts/start_demo_listener.py` | @bot 命令监听器 |
| `data/demo_script_v2.md` | 10 天演示消息脚本 |
| `tests/test_p1_closed_loop.py` | P1 闭环测试（22 case） |
| `CHANGELOG_V1.21.md` | 本文件 |

## Agent 如何使用本项目

**方式 1 — MCP Server**：
```
python scripts/mcp_server.py --data-dir data/demo --project-id aurora-sprint
```
暴露两个 tool：`get_project_state`（结构化项目状态 JSON）和 `search_memories`（语义搜索记忆）。

**方式 2 — Agent Memory Document**：
```
python scripts/demo_e2e_pipeline.py --agent-doc ...
```
生成飞书文档，人类和 agent 均可阅读。文档末尾含 `<agent-data>` JSON 块。

---

## 已知限制

- 1 个已知测试失败（冲突检测 + sender 前缀剥离边界）
- 飞书不支持消息深链（证据用回复消息替代）
- Hybrid LLM 替换路径已实现但仅做增量补充，RuleOnly 结果仍占主导
