# 当前任务看板

## 项目目标

飞书校园大赛参赛项目：OpenClaw Memory Engine — 从飞书群聊消息中提取结构化协作状态，支持中断续办。

## 当前状态

| 指标 | 值 |
|------|----|
| 阶段 | **V1.12 · 证据链完善 + 检索增强** |
| 测试数 | 176，全部通过 |
| Golden Set | 150 条 |
| RuleOnly 通过率 | **150/150 (100.0%)** |
| Hybrid 通过率 | **147/150 (98.0%)** (DeepSeek V4 Pro, temperature=0) |
| LLM only 通过率 | 待测（三模式对比） |
| LLM 后端 | DeepSeek V4 Pro + JSON mode + temperature=0 |
| 飞书端到端 | ✅ 已打通（sync→extract→state panel→send→pin） |
| 证据链 | ✅ SourceRef 含 sender+URL+excerpt 原文验证 |
| 检索能力 | 关键词 + 多条件组合 + 倒排索引 + message_id 追溯 |
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
