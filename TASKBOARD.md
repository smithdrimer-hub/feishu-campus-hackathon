# 当前任务看板

## 项目目标

飞书校园大赛参赛项目：OpenClaw Memory Engine — 从飞书群聊消息中提取结构化协作状态，支持中断续办。

## 当前状态

| 指标 | 值 |
|------|----|
| 阶段 | **V1.15 · P0 可信度 + 闭环执行 + 审核台 + Hybrid 兜底** |
| 测试数 | **310**，全部通过 |
| Golden Set | 150 条 |
| RuleOnly 通过率 | **150/150 (100.0%)** |
| 按类型 P/R/F1 | 已分解（next_step P=0.65 最低） |
| LLM 后端 | DeepSeek V4 Pro + JSON mode (temperature=0) |
| 飞书端到端 | ✅ 真实测试群完整验证（含 user 身份发消息/任务/文档创建） |
| 提取准确率 | Hybrid 8 条件触发，V1.15 新增条件(h)覆盖 4 种规则冲突场景 |
| 审核台 | ✅ CLI + 飞书群成员身份验证 + 证据原文定位 + 合并/修改/阻塞状态 |
| 决策分层 | ✅ 4 级强度 + 审核台过滤 |
| 阻塞生命周期 | ✅ 5 状态流转 + 7天 sweep |
| 冲突检测 | ✅ 同主题决策差异自动标记 |
| 触发引擎 | ✅ 3 规则 + review_status 过滤 + 冷却机制 |
| 比赛时间 | 2026-05 月 |

---

## 已完成记录

| 日期 | 里程碑 | 详情 |
|------|--------|------|
| 2026-04-25 | V1.1 可信提取 | LLMExtractor + Schema 校验 + 规则兜底 |
| 2026-04-26 | V1.5 P0 核心 | 三层去重 + Prompt Grounding + Debounce |
| 2026-04-28 | V1.6 可靠性 | 否定极性 + Bi-temporal + member_status + 30 条 Golden Set |
| 2026-04-29 | V1.8 多数据源 | sync_doc/sync_tasks + deadline 提取 |
| 2026-04-29 | V1.9 面板聚合 | project_state.py 三种形态 |
| 2026-04-30 | V1.10 Hybrid | HybridExtractor + 150 条 Golden Set + DeepSeek 集成 |
| 2026-05-01 | **V1.11 四大优先** | Golden Set 对齐(100%) + LLM 优化(Pro/隐式Prompt) + 飞书端到端 + 架构加固 |
| 2026-05-02 | **V1.11 P0/P1** | Post消息验证 + 群聊完整流程 + temperature=0 + 分页同步 + Deadline跨key + Hybrid兜底 |
| 2026-05-02 | **V1.12 证据链** | SourceRef sender+URL + LLM excerpt原文验证 + 状态面板证据 + Handoff可追溯 + message_id检索 |
| 2026-05-02 | **V1.12 检索增强** | 多条件组合搜索 + 倒排索引 + 证据链调试脚本 |

---

## 下一迭代：P1 三模式对比 + 质量冲刺

### A：三模式完整对比评测

| # | 任务 | 优先级 | 预计 | 说明 |
|---|------|--------|------|------|
| A1 | **RuleOnly vs Hybrid vs LLM-only 三模式跑通** | **P0** | 1h | `--compare` 输出三种模式完整通过率，目前 LLM-only 列一直是"待测" |
| A2 | **temperature=0 确定性验证** | P1 | 0.5h | 连续跑 3 次 Hybrid eval，确认 GS-031/116/119 不再波动 |
| A3 | **换 Claude/GPT-4o 做对比** | P1 | 1h | 如果 API key 可用，跑一次对比看通过率是否可到 100% |

### B：质量冲刺

| # | 任务 | 优先级 | 预计 | 说明 |
|---|------|--------|------|------|
| B1 | **Golden Set 扩展到 200+ 条** | P1 | 3h | 重点补充真实群聊消息、噪声消息、混合话题消息 |
| B2 | **真实飞书 post/image 消息全覆盖测试** | P1 | 1h | 发送各类消息到测试群，验证每种类型的提取 |
| B3 | **演示剧本排练** | P1 | 1h | 按 demo_script.md 完整走一遍，计时、优化措辞 |

### C：证据链 + 检索增强（已完成 V1.12）

| # | 任务 | 状态 |
|---|------|------|
| C1 | SourceRef 增加 sender/URL | ✅ |
| C2 | LLM excerpt 原文验证 | ✅ |
| C3 | 状态面板含证据引用 | ✅ |
| C4 | Handoff 可追溯性 | ✅ |
| C5 | message_id 检索 | ✅ |
| C6 | 多条件组合搜索 | ✅ |
| C7 | 倒排索引 | ✅ |
| C8 | 证据链调试脚本 | ✅ |

---

## 下一迭代：P2 生产化增强

### D：架构完整性

| # | 任务 | 优先级 | 预计 | 说明 |
|---|------|--------|------|------|
| D1 | **跨批次证据引用** | P2 | 2h | LLM 可引用之前同步的消息 ID，突破 valid_message_ids 批次限制 |
| D2 | **策略遗忘机制** | P2 | 2h | 长时间未更新的记忆自动标记为 stale/archived，避免交接摘要膨胀 |
| D3 | **Recency Re-rank** | P2 | 1h | 较新的记忆在搜索/摘要中权重更高 |
| D4 | **权限集成（飞书 OAuth）** | P2 | 4h | 基于 open_id 的真实权限隔离，替换 project_id 软隔离 |

### E：可选增强

| # | 任务 | 优先级 | 预计 | 说明 |
|---|------|--------|------|------|
| E1 | **向量库可选语义召回** | P2 | 4h | ChromaDB + text-embedding-3-small，索引 source_refs.excerpt，关键词优先+向量 rerank |
| E2 | **Rich text 全覆盖** | P2 | 2h | image/file/share_chat/interactive 消息类型的文本提取 |
| E3 | **自动化质量评估** | P2 | 3h | CI 中自动跑 Golden Set，输出趋势图 |
| E4 | **飞书卡片消息渲染** | P2 | 2h | 状态面板用飞书卡片消息（interactive）替代纯 markdown |

---

## 关键命令速查

```bash
cd openclaw-memory

# 测试
python -m unittest discover -s tests -v                   # 176 个测试

# 评测
python scripts/run_golden_eval.py                         # RuleOnly: 150/150
python scripts/run_golden_eval.py --hybrid                # Hybrid: ~147/150
python scripts/run_golden_eval.py --compare               # 三模式对比（需要 LLM key）
python scripts/run_golden_eval.py --hybrid --verbose      # 详情

# 演示
python scripts/demo_run_example.py                        # 一键演示（Fake LLM）
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --dry-run

# 证据链
python scripts/demo_evidence_trace.py --project-id demo --format tree
python scripts/demo_evidence_trace.py --project-id demo --check-unverified
python scripts/demo_evidence_trace.py --message-id om_xxx

# 飞书端到端（需 auth login）
python scripts/demo_e2e_pipeline.py --chat-id oc_xxx --hybrid --no-pin
```

## 注意事项

- `config.local.yaml` 包含 DeepSeek API key，已加入 `.gitignore`，**不要提交**
- 新 agent 接手时：先读 `CLAUDE.md`，再看本文件
- 所有修改后必须跑 `python -m unittest discover -s tests -v` 确认无回归
- 飞书测试群：`[LARK-CLI-VERIFY] Memory Sandbox` (oc_e1c6a2c2a42b67606b91ad69bab226f4)

---

## 文档能力深度优化计划

> 当前文档提取已覆盖：fetch API、分节 chunking、表格行解析、列表项拆分、评论接入、owner 修复。
> 以下为可进一步提升文档场景覆盖率的优化项，按难度和优先级排序。

### P1：中等难度 — 建议比赛前完成

| # | 任务 | 难度 | 预计 | 说明 |
|---|------|------|------|------|
| DOC-1 | **嵌入式 Sheet/Bitable 感知** | ★★★ | 1h | 扫描 markdown 中的 `<sheet token="xxx">` / `<bitable token="xxx">` 标签，调用 lark-sheets / lark-base 读取嵌入表格数据，提取为结构化记忆 |
| DOC-2 | **文档 @提及解析** | ★★★ | 1h | 飞书文档含 `@user_id` 格式的提及，当前 markdown 丢失此信息。需通过 API 的 block 结构或正则回溯提取 |
| DOC-3 | **Wiki 知识库节点遍历** | ★★★★ | 2h | 支持 `--wiki-node` 参数，递归遍历知识库节点树，将父节点标题作为子文档的 project context |
| DOC-4 | **文档富文本 block 解析** | ★★★ | 1.5h | 当前仅取 markdown 纯文本。飞书文档是 block tree：callout(高亮块)、grid(分栏)、checkbox(任务列表) 等。需用 `docs +fetch --output-format full` 获取 block 结构后解析 |

### P2：高难度 — 比赛后迭代

| # | 任务 | 难度 | 预计 | 说明 |
|---|------|------|------|------|
| DOC-5 | **文档版本历史对比** | ★★★★ | 3h | 调用 `drive +file-version-list` 获取版本历史，对比前后版本的 markdown diff，提取"谁在什么时候改了什么决策" |
| DOC-6 | **文档协作实时感知** | ★★★★★ | 4h | 通过 `lark-event` WebSocket 监听文档编辑事件，文档更新时自动触发 `sync_doc`，实现"文档改了→记忆自动更新" |
| DOC-7 | **跨文档关联图谱** | ★★★★★ | 4h | 多文档之间的引用关系（`<cite type="doc" token="...">`），构建文档→记忆→文档的关联图，支持"这个决策影响了哪些文档"的追溯 |
| DOC-8 | **文档中的图片 OCR 提取** | ★★★★ | 3h | 调用 `docs +media-download` 下载文档中嵌入的图片，通过 OCR（tesseract/飞书 OCR API）提取图片中的文字信息 |
| DOC-9 | **飞书幻灯片/思维导图接入** | ★★★★ | 3h | 扩展 `sync_doc` 为 `sync_node`，支持 slides/mindnote 类型。幻灯片解析演讲者备注，思维导图解析节点树 |

### 已完成

| # | 任务 | 状态 |
|---|------|------|
| DOC-0.1 | 文档 API fetch + markdown 提取 | ✅ |
| DOC-0.2 | ## 标题分节 chunking | ✅ |
| DOC-0.3 | 表格行 → 独立事件 (分隔符/无头/多列) | ✅ |
| DOC-0.4 | 列表项协作信号拆分 | ✅ |
| DOC-0.5 | sender_type=doc_sync (LLM 不再跳过) | ✅ |
| DOC-0.6 | 文档 source_url + content hash 重取 | ✅ |
| DOC-0.7 | 文档评论接入 (sync_doc_comments) | ✅ |
| DOC-0.8 | 列表项 owner 提取 (Pattern 6) | ✅ |
| DOC-0.9 | 长文档滚动窗口 (max_chunks=20) | ✅ |

---

## 飞书权限系统深度优化计划

> 目标：让 Memory Engine 完美融入飞书生态，利用飞书原生权限体系实现真实的多用户隔离和访问控制，提升办公效率。

### 飞书权限体系概述

飞书采用三层权限模型：

```
应用层 (Scopes)
  ├── im:message:read / im:message:send_as_bot
  ├── doc:document:read / doc:document.comment:read
  ├── task:read / task:write
  ├── drive:drive:readonly
  └── contact:user:read

身份层 (Identity)
  ├── 用户身份: open_id (ou_xxx), union_id, tenant_key
  ├── 应用身份: app_id (cli_xxx)
  └── Bot 身份: 企业自建应用 / 商店应用

资源层 (ACL)
  ├── 群聊: 成员列表 (im +chat-members-list)
  ├── 文档: 权限成员 (drive +permission-members-list)
  ├── 任务: 任务成员/关注者
  └── 知识库: Wiki 空间成员
```

### 当前状态

| 能力 | 状态 |
|------|------|
| 身份感知 (lark-cli doctor → user_id) | ✅ |
| Bot 消息发送/置顶 | ✅ |
| project_id 软隔离 | ✅ (Demo 可用) |
| 真实 open_id 权限校验 | ❌ |
| 群成员校验 | ❌ |
| 文档权限校验 | ❌ |
| 审计日志 | ❌ |

### P1：Demo 可用（最小权限感知，约 2h）

| # | 任务 | 难度 | 预计 | 说明 |
|---|------|------|------|------|
| AUTH-1 | **身份感知增强** | ★★ | 0.5h | `lark-cli doctor` 获取当前用户 open_id/name，自动写入 memory metadata；在交接摘要中显示"提取者: XXX" |
| AUTH-2 | **群聊-项目自动绑定** | ★★ | 0.5h | `chat_id → project_id` 自动映射表（`data/chat_project_map.json`），首次使用 `--chat-id oc_xxx` 时自动创建绑定，后续自动识别 project_id |
| AUTH-3 | **Owner open_id 解析** | ★★ | 0.5h | `_extract_owner` 提取姓名后，调用 `lark-cli contact +search --query "张三"` 查询真实 open_id，在 SourceRef 中同时保存 name + open_id |
| AUTH-4 | **基于 open_id 的访问过滤** | ★★ | 0.5h | `list_items(project_id, user_id)` — 只返回该用户所在群聊/有权限的记忆。未登录用户只能看到自己的记忆 |

### P2：生产可用（完整权限集成，约 6h）

| # | 任务 | 难度 | 预计 | 说明 |
|---|------|------|------|------|
| AUTH-5 | **飞书 OAuth 登录流程** | ★★★★ | 2h | Web 回调 → 获取 user access token + refresh_token → 存储到 `data/auth/{open_id}.json`。支持多用户登录 |
| AUTH-6 | **多用户数据隔离** | ★★★ | 1h | 按 `open_id` 分片存储：`data/users/{open_id}/raw_events.jsonl` + `memory_state.json`。每个用户有独立的记忆空间 |
| AUTH-7 | **群成员权限校验** | ★★★ | 1h | 调用 `im +chat-members-list` 获取群成员列表，验证请求者是否在群内。不在群内的用户无法读取该群的记忆 |
| AUTH-8 | **文档权限校验** | ★★★ | 1h | 调用 `drive +permission-members-list` 获取文档权限成员，验证请求者是否有查看/编辑权限。无权限文档的记忆不返回 |
| AUTH-9 | **操作审计日志** | ★★ | 1h | 每次记忆读取/写入/删除记录：operator_id (open_id), operation (read/write/delete), project_id, state_type, timestamp。写入 `data/audit.jsonl` |

### P3：企业级（未来迭代，约 8h）

| # | 任务 | 难度 | 预计 | 说明 |
|---|------|------|------|------|
| AUTH-10 | **飞书审批集成** | ★★★★★ | 3h | 敏感操作（如批量删除记忆）需通过飞书审批流程。调用 `lark-approval` API 创建审批实例，审批通过后才执行 |
| AUTH-11 | **部门级权限策略** | ★★★★★ | 3h | 调用 `lark-contact` 获取组织架构，按部门设置权限策略："开发部全员可读"、"产品部仅可读自己的记忆" |
| AUTH-12 | **数据导出/删除合规** | ★★★★ | 2h | 用户请求数据导出（GDPR）时，按 open_id 导出所有记忆为 JSON；用户离职时，自动标记其记忆为 archived |

### 权限优化对办公效率的提升

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 新人接手项目 | 需要手动告诉 chat_id 和 project_id | 自动识别群聊，一键生成个人上下文 |
| 跨部门协作 | 所有人看到所有记忆 | 只看到自己所在群的记忆 |
| 文档协作 | 文档记忆与群聊记忆混淆 | 文档权限独立校验，无权限文档的记忆不可见 |
| 离职交接 | 离职人员的记忆残留 | archived 标记 + 历史版本可追溯 |
| 合规审计 | 无法追溯谁看了什么 | audit.jsonl 完整记录每次操作 |

---

## 历史优化修复质量审计

> 审计日期: 2026-05-02。逐项检查历次优化是否存在"虚假修复"（仅通过测试但真实场景无效）。

### 审计结论总览

| 等级 | 数量 | 说明 |
|------|------|------|
| ✅ 真修复 | 28 | 修复了广泛的问题类 |
| 🟡 部分修复 | 5 | 覆盖了大部分场景但有边界遗漏 |
| ❌ 虚假修复 | 1 | 改了代码但真实场景无效（FIX-11 原版） |

### 逐项审计

| 优化项 | 版本 | 审计 | 证据 |
|--------|------|------|------|
| Golden Set 期望值对齐 | V1.11 | ✅ | 150 case 全部通过 RuleOnly，31 llm_expected 语义校准有据 |
| LLM Pro + JSON mode | V1.11 | ✅ | 隐式 owner/decision/blocker/goal 全部正确识别 |
| Hybrid 触发条件 (f)/(g) | V1.11 | ✅ | 10 隐式信号 + 跨类型互补，真实触发覆盖 |
| state_type 别名映射 | V1.11 | ✅ | 21 别名覆盖 LLM 常见输出变体 |
| 飞书端到端 | V1.11 | ✅ | sync→extract→send→pin 全链路真实验证 |
| Debounce 持久化 | V1.11 | ✅ | JSON 文件持久化，Engine 新实例正确恢复 |
| Value 裁剪 (4.2) | V1.11 | 🟡 | 6/7 真实变体通过。"临时请个假"（"请个假"不含"请假"子串）被遗漏 |
| Layer 4 跨 key 覆盖 (4.3) | V1.11 | 🟡 | 直接 token 重叠 OK。**3 段式稀疏链断裂**：d1="用React" → d2="换Vue" → d3="还是React"，d2 无共享词导致全链断裂 |
| Owner Pattern 6 | V1.12 | ✅ | 非名词语义过滤生效，"模块/功能/需求"未被误提取 |
| 文档分节 chunking | V1.12 | ✅ | `\\n` 修复 + `##` 分节，7 事件 6 类型提取 |
| 文档表格解析 | V1.12 | ✅ | 分隔符检测 + 无头表格 + 多列对齐 |
| 文档评论接入 | V1.12 | ✅ | API 已验证，评论→事件→提取链路完整 |
| 嵌入对象检测 | V1.12 | ✅ | `<sheet>`/`<bitable>`/`<cite>` 标签检测 |
| 长文档窗口 | V1.12 | ✅ | max_chunks=20 限制，超出合并 |
| SourceRef sender+URL | V1.12 | ✅ | sender_name/sender_id/source_url 全链路传递 |
| sender.name 传递 | V1.12 | ✅ | API→event→extractor→SourceRef 完整 |
| LLM excerpt 验证 (原版) | V1.12 | ❌ | **虚假修复**：`SequenceMatcher` 字符相似度 0.6 阈值，7/7 真实改写不通过。仅通过测试 case（90% 字符重叠的"改写"） |
| LLM excerpt 验证 (修复后) | V1.12 | ✅ | 改用 token 2-gram 重叠计数，6/7 真实改写通过 |
| as_of 时区 bug | P0 | ✅ | `datetime.timezone.utc` 标准库修复，3 跨时区 test 通过 |
| subprocess 编码 | P0 | ✅ | UTF-8 strict + GBK fallback，两份编码覆盖 |
| 安全测试加固 | P0 | ✅ | 参数重排/空格/大小写/未知命令 4 真实绕过测试 |
| 跨项目用户视图 | P1 | ✅ | 20 项目聚合 + 姓名变体匹配 + 空数据降级 |
| Layer 4 边界测试 | P1 | ✅ | 4 测试覆盖 decision/deadline 同/不同主题 |

### 仍需修复

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| REAL-1 | **member_status "请个假" 遗漏** | P2 | "临时请个假"中"请假"被"个"分隔，keyword 子串匹配失败。需改成字符级邻近匹配 |
| REAL-2 | **Layer 4 稀疏链断裂** | P2 | d1→d2→d3 链中 d2 无共享词时全链断裂。需改成传递闭包（d3 与 d1 共享时应追溯覆盖 d2） |
| REAL-3 | **"switch vs migrate" 同义词** | P2 | token 方法无法识别同义词改写。需 LLM 判断或英文词嵌入 |

---

## 用户质疑审核（来自 user.md）

> 审核日期: 2026-05-04。逐条验证用户对当前实现和文档表述的质疑是否成立。

### 质疑一："6 个飞书模块统一分析"说得太满

**审核结论：✅ 质疑成立。代码验证如下：**

| 数据源 | 实际状态 | 代码证据 |
|--------|---------|---------|
| 群聊消息 | 完整实现，自动化程度最高 | `demo_sync_messages.py` + pipeline Step 1 |
| 飞书文档 | API 已验证但未用真实文档做端到端 | `engine.sync_doc()` 路径正确，`--doc-id` 手动触发 |
| 飞书任务 | 同上，未用真实任务验证 | `engine.sync_tasks()` 路径正确，`--task-query` 手动触发 |
| 日历 | 已接入，手动触发 | `--sync-calendar`，本周日程拉取 |
| 会议纪要 | 已接入，手动触发 | `--sync-minutes`，依赖妙记 AI 总结质量 |
| 审批 | 已接入，手动触发 | `--sync-approvals`，仅 pending 状态 |

**修正措辞**：应该说"支持接入 6 个数据源"而非"6 个模块统一分析"。群聊是主力验证过的，文档/任务 API 通路已验证但缺乏真实数据端到端测试，日历/妙记/审批为 V1.13 新增，成熟度最低。建议使用手册中标注每个数据源的成熟度（已验证 / 通路就绪 / 初版）。

### 质疑二："理解中文自然表达"要谨慎，决策识别不能直接当作可靠

**审核结论：✅ 质疑成立。代码验证如下：**

**RuleBased 提取器的决策逻辑**（`extractor.py:544-561`）：
- 触发词：`决定, 决策, 确定, 采用, 改为, 不再, instead, decide`（8 个关键词）
- 含"改为/不再/推翻"时 key=`current_decision_override`（硬编码，全局只有一个覆盖键）
- value = 整条消息原文（未裁剪）
- confidence = 固定 0.75

**问题**：
1. 无 `decision_strength` 分层。用户举的例子完全正确——"那先这样吧""不用 React 了？"都会被当前逻辑当作正式决策
2. `current_decision_override` 是全局唯一键，意味着不同主题的决策覆盖会互相打架
3. 没有区分"讨论中""偏好表达""初步意向""正式确认"

**建议的 `decision_strength` 分层** 在工程上完全可行——在 MemoryItem 上增加一个 `strength` 字段，RuleBased 默认为 `tentative`，只有显式确认信号（"确定""就这么定了""最终方案"）才标 `confirmed`。`confirmed` 级别才进入正式决策面板。

### 质疑三：98% 准确率不能直接等于真实办公场景

**审核结论：✅ 质疑成立。这是最严重的表述问题。** 

当前 Golden Set 的实际情况：
- 150 条人工构造样本，12 个场景类型
- RuleOnly 150/150 (100%) 是在 V1.11 将 expected_items 校准为"规则实际能提取的值"之后达成的
- 这意味着 100% 衡量的是"规则输出和校准后期望值的对齐率"，而非"语义理解准确率"
- Hybrid 147/150 (98%) 有 3 条非确定性失败来自 temperature=0.1 波动
- **没有** precision/recall/F1 分解，没有按 state_type 分别报告

**建议修正**：拆成按 state_type 的 precision/recall 报告；声明"在当前 150 条标注评测集上达到 X"而非"真实办公场景准确率 X"；强调系统用置信度、证据追溯和人工确认机制降低误判风险。

### 质疑四："自动创建任务"与"只读默认"叙事冲突

**审核结论：✅ 质疑成立。代码确实有这个设计但文档没说清楚。**

代码事实：
- `ActionExecutor` 默认 `auto_confirm=False`，`requires_confirmation=True` 的 action 会被阻止
- `ActionTrigger` 的规则 1（next_step→task）中，如果 owner open_id 解析成功则 `requires_confirmation=False`
- 但触发引擎只有在用户显式传 `--trigger --mode auto` 时才会真正执行
- 没有任何后台自动触发机制

**所以真正的行为是**：系统默认生成 action proposals 但不执行。只有用户主动选择 `--mode auto` 才会自动创建任务。这与"只读默认"不矛盾——但手册里"检测到下一步行动和负责人后自动创建任务"的措辞没有说明这个前提。

### 质疑五：负责人模型是"单 owner"模式，不够用

**审核结论：✅ 质疑成立。这是数据模型层面的设计缺陷。**

代码事实（`extractor.py`）：
- `_extract_owner()` 的 Pattern 5 支持多人（"张三负责前端，李四负责后端"），会创建多个 MemoryItem
- 但每个 item 的 key 都是 `current_owner`（硬编码），所以 upsert 去重层会把多个 owner 互相覆盖
- 最终只会保留最后一个被处理的 owner

**这是一个 bug，不只是设计选择。** 修复方向：改为 `owner_{module_name}` 这样的 key 格式，让不同模块的负责人可以共存。role-based（owner/co_owner/reviewer/backup）是更高级的方案，可以在 key 或 metadata 中编码角色。

### 质疑六：阻塞需要完整生命周期

**审核结论：✅ 质疑成立。当前 blocker 只有 active 一个状态。**

当前 blocker 的生命周期：
1. 提取出来 → status="active"
2. 可能被 supersede（相同 identity_key 的新 blocker 覆盖旧 blocker）
3. 仅此而已。没有 resolved、acknowledged、waiting_external 等状态。

**技术可行性**：MemoryItem.status 字段已经是字符串，可以扩展状态值。需要在 store 层增加一个 `update_item_status(memory_id, new_status)` 方法，以及一条触发规则检测 resolved 变化。

### 质疑七："5 分钟上手"是硬承诺

**审核结论：✅ 质疑成立。建议改为"目标是在数分钟内看到核心项目状态，前提是信息源已接入且最近同步完成"。**

---

## 用户建议评估（来自 user.md）

逐条评估 8 个建议的可行性和重要性。

### 建议一：高风险记忆审核台 / 项目管家视图

**可行性：高。** 已有基础设施：
- MemoryItem 存储层完整，增加 `review_status` 字段只需修改 schema.py + store.py
- ActionProposal 已有 `requires_confirmation` 字段，审核台可以复用这个模式
- `list_items()` 已支持过滤，加 `review_status="needs_review"` 过滤即可

**需要新建**：
- `src/memory/review_desk.py` — 审核台逻辑（列出待审项、确认/驳回/修改）
- 触发审核的规则：decision 变更 → needs_review；DDL 变更 → needs_review；无证据记忆 → needs_review
- 审核 CLI 入口或飞书交互

**重要性：P0。** 这是解决"AI 自动提取不可信"问题的核心方案。让管家（项目负责人）审核高风险变更，普通记忆自动通过。完美契合用户"需要人工审核关键部分"的需求。

### 建议二：会议后确认卡片

**可行性：中。** 
- 概念清晰：sync_minutes → extract → 生成确认卡片 → 发送到群 → 成员确认
- 技术挑战：飞书卡片消息（interactive）的发送不经过 lark-cli 的简单 `--text`/`--markdown` 接口，需要调用飞书开放 API 的 `im/v1/messages` 接口并构造 `interactive` 类型的消息体
- 退而求其次：用 markdown 格式发送"确认清单"，成员通过回复或 emoji reaction 确认

**重要性：P1。** 对 Demo 非常有价值——"开完会后系统自动生成确认卡片"比"静默创建任务"更可信、更直观。但卡片消息的技术栈需要额外评估。

### 建议三：冲突检测

**可行性：中高。** 已有基础：
- Layer 4 跨 key 覆盖逻辑（`_is_same_topic`）已经能检测"同主题的 decision/deadline"
- `upsert_items` diff 返回 created/updated/unchanged，可以扩展 conflict 类别
- SourceRef 证据链完整，可以并排展示冲突双方的证据

**可检测的冲突类型**：
1. 同一模块多个 owner → 检测 identity_key 前缀冲突
2. 同一项目多个 DDL → 检测 deadline 类型 + 不同日期值
3. 群聊决策 vs 文档决策不一致 → 检测不同 source_type 的同主题 decision
4. 会议待办 vs 任务状态不一致 → 需要任务回流（建议四）配合

**重要性：P1。** 差异化竞争力——"我们不是强行统一，而是发现冲突交给人判断"。

### 建议四：任务状态回流

**可行性：中。** 已有基础：
- `ActionExecutor` 执行 create_task 后记录 task_guid 到 action_log
- `adapter.search_tasks()` 可以查询任务状态
- MemoryItem 有 status 字段可以更新

**缺失**：
- task_guid → memory_id 的映射关系未持久化（action_log 有但不方便查询）
- 没有定期回流机制
- 需要 `update_item_status()` 方法

**重要性：P1。** 不做回流，项目面板上的任务永远是 active，即使已经在飞书里完成了。这是"闭环"的关键缺失。

### 建议五：请假/不可用的有效期和替代人

**可行性：高。** MemoryItem 已有 `valid_from` / `valid_to` 字段。需要：
- `_extract_member_status()` 增强，尝试提取日期范围（可复用 date_parser）
- 提取 backup person（"找李四"→ backup_owner）
- 显示时标注有效期

**重要性：P2。** 提升真实感，但不影响核心 Demo 流程。

### 建议六：文档变更影响分析

**可行性：低-中。** 
- `sync_doc()` 已有 content hash 机制可以检测文档是否更新
- 但缺少"哪些记忆来自这个文档"的追溯链路
- 全文 diff + 影响分析需要较重的工程投入

**轻量替代**：检测文档更新后，重新提取 → diff 显示新增/变更的记忆项 → 标记为 `needs_review`

**重要性：P2。** 场景真实但实现成本高，Demo 中可以用预设场景模拟。

### 建议七：站会摘要（yesterday/today/blockers 格式）

**可行性：中。** 
- "blockers"有现成的
- "today"就是当前的 active next_step 列表
- "yesterday"需要时间维度的变更追踪——哪些 next_step 在昨天被标记为 completed 或 updated

当前缺少"已完成"的追踪。需要增加 MemoryItem 的 completed_at 字段或定期对比快照。

**重要性：P2。** 格式本身容易生成，但"昨天完成了什么"需要更完整的任务生命周期管理。

### 建议八：飞书机器人指令

**可行性：低-中（当前阶段）。**
- 需要一个常驻进程监听飞书消息事件
- lark-event skill 提供 WebSocket 长连接能力
- 指令解析（`@MemoryBot 状态`）需要消息路由逻辑
- 最重要的是：需要一个**飞书应用**（有 app_id/app_secret）和事件订阅配置

**当前障碍**：项目使用的是 lark-cli（命令行工具），不是飞书应用服务端。要支持 @bot 指令，需要部署一个 HTTP/WebSocket 服务。

**轻量替代**：不做实时监听，而是做一个简单的 HTTP 服务（Flask/FastAPI），接收飞书事件回调。或者更简单地，在 Demo 中用预编排的交互流程展示。

**重要性：P2（比赛）/ P0（产品化）。** 比赛 Demo 可以用命令行展示核心能力，@bot 指令是产品化后的自然延伸。

---

## 综合评估结论（2026-05-05 更新）

### 已完成项（自原评估以来）

| 建议 | 状态 | 实现 |
|------|------|------|
| ① 高风险审核台 | ✅ 已完成 | `review_status` + `demo_review_desk.py` + 管家身份验证（飞书群成员校验） |
| 质疑二（决策分层） | ✅ 已完成 | `decision_strength` + 审核台过滤 |
| 质疑五（owner 覆盖） | ✅ 已完成 | domain-based key |
| 质疑六（阻塞生命周期） | ✅ 已完成 | `blocker_status` (open/acknowledged/waiting_external/resolved/obsolete) + 7天 sweep |
| 质疑三（准确率表述） | ✅ 已完成 | 按 state_type 的 P/R/F1 分解 + 使用手册更新 |

### 剩余建议的必要性重新审核

#### 建议三：冲突检测 → 升级为 P0

**必要性：高。** 这是当前最重要的差异化能力——"不是强行统一，而是发现冲突交给人判断"。审核台已经就绪，冲突检测是审核台的自然延伸：不只是审核"不确定"的记忆，更要审核"互相矛盾"的记忆。

**当前基础**：Layer 4 跨 key 覆盖逻辑（`_is_same_topic`）已能检测同主题 decision/deadline。扩展为冲突检测只需：识别两个 item 属于同主题但 value 不同 → 标记为 conflict → 进审核台展示双方证据。

**预期效果**：Demo 中展示——群聊里说用 React，文档里写用 Vue → 系统不强行覆盖，而是高亮冲突，展示双方证据，等管家仲裁。评委一眼看出"这个系统在思考，不是机械执行"。

#### 建议四：任务状态回流 → 保持 P1

**必要性：中高。** 闭环完整性的关键缺口。已创建的飞书任务如果在飞书里被完成了，项目面板上应该自动更新。但实现需要：
- task_guid → memory_id 持久化映射（action_log 中有但不方便查）
- 定期/手动触发回流扫描
- 这本质上是一个"同步"操作，可以作为流水线的一个可选步骤

**演示时的替代方案**：在 Demo 中手动演示回流——先展示飞书任务已完成，再运行回流命令，面板自动更新。比完全自动化更可控。

#### 建议七：站会摘要格式 → 升级为 P1

**必要性：中高。** 用户原始需求就是"站会摘要"。当前状态面板是"项目状态"视角，不是"今日站会"视角。改为 yesterday/today/blockers 三段式更贴近实际使用场景。

**实现简单**——就是 `project_state.py` 的一个新渲染函数，从现有数据中按时间分组。不需要新字段。"yesterday"可以基于 `updated_at` 在过去 24 小时内变化的 item，"today"是当前 active item，"blockers"已有。

#### 建议二：会议确认卡片 → 保持 P1

**必要性：中。** Demo 价值高，但飞书卡片消息（interactive）需要原生 API，lark-cli 目前不支持。**轻量替代可用**：用 markdown 发送确认清单（类似审核台输出），成员通过命令回复确认。比赛评分看的是"系统有确认机制"而非"用了卡片消息"。

#### 建议五：请假有效期 → 保持 P2

**必要性：低。** 提升真实感但场景狭窄。当前 member_status 提取量很少，大部分群聊不会讨论请假。Demo 中可以预设一条请假消息来展示能力——现有的 `_extract_member_status()` 已经够用。

#### 建议六/八：文档变更影响 / 飞书机器人指令 → P3（比赛后）

**必要性：低（对比赛）。** 都是"有了更好"而非"没有不行"。比赛 Demo 15 分钟，展示核心差异化（审核台、冲突检测、闭环执行）已足够。

---

## 更新后的实施优先级

| 优先级 | 建议 | 工作量 | Demo 价值 |
|--------|------|--------|----------|
| **P0** | 建议三：冲突检测 | ~120 行 | 最高——差异化竞争力 |
| **P1** | 建议四：任务状态回流 | ~100 行 | 闭环完整性 |
| **P1** | 建议七：站会摘要格式 | ~40 行 | 贴近真实使用场景 |
| **P1** | 建议二：会议确认清单（markdown 版） | ~50 行 | 确认机制叙事 |
| P2 | 建议五：请假有效期 | ~40 行 | 锦上添花 |
| P3 | 建议六/八：文档变更/飞书机器人 | 重 | 比赛后迭代 |

### 建议下一步（比赛交付终版）

```
P0: 冲突检测 → P1: 站会摘要 + 任务回流 + 确认清单
```

**四件事、总计约 300 行、预计 1-2 小时。** 完成后系统具备完整叙事：
1. 群聊消息进入 → 自动提取记忆（感知）
2. 冲突和新 decision 进入审核台（治理）
3. 确认后的记忆触发任务/提醒（执行）
4. 任务状态回流 + 站会摘要（闭环）

