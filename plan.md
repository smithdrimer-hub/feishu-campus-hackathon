# OpenClaw Memory Engine — 综合审查报告 & 开发计划

基于 `Memory Engine V1.7 – Dev Spec（for AI Assistant）` 文档，对照当前项目实际代码，逐项审查可行性。

---

## 一、Dev Spec 功能审查

### ✅ 已实现（无需额外开发）

| Dev Spec 要求 | 当前状态 | 支撑代码 |
|---|---|---|
| LLM 抽取结构化记忆 | 完成（LLM + RuleBased fallback） | `extractor.py: LLMExtractor` |
| bi-temporal 双时间字段 | 完成（valid_from/valid_to/recorded_at） | `schema.py: MemoryItem` |
| supersedes 版本链 | 完成 | `store.py: upsert_items()` 三层去重 |
| project_id 过滤 | 完成 | `store.py: list_items(project_id=...)` |
| handoff/交接摘要 | 完成 | `handoff.py: generate_handoff()` |
| 关键词搜索 | **V1.9 刚完成** | `store.py: search_keywords()` |

### 🔶 部分可实现（需适配当前数据模型）

| Dev Spec 要求 | 差距 | 工作量 |
|---|---|---|
| **形态 A：群组项目状态面板** | 已有 `handoff.generate_handoff()`，但输出是纯 Markdown 文本，需按 spec 的结构化 JSON 重新聚合。render 函数要新增 | 小（2-3h）：新增 `build_group_project_state()` + `render_group_state_panel_text()` |
| **形态 C：Agent Context Pack** | 本质是 `handoff` 的结构化 JSON 版本 + 加上 `raw_snippets`。当前 `source_refs` 已有 excerpt 和 message_id | 小（1-2h）：新增 `build_agent_context_pack()` |
| `build_personal_work_context()` | 当前 `owner` 字段在 MemoryItem 上，但没有按 user_id 过滤的检索入口。`search_keywords()` 可以按 owner 搜，但需要包装 | 中（2-3h）：按 owner 过滤 + 聚合 |
| `lark-cli` 集成发消息 | 当前 `demo_sync_messages.py` 已可发消息（bot 身份），但发送富文本/Markdown 未验证 | 小（1-2h）：需要验证 `im +messages-send --markdown` 是否可行 |
| type 映射（decision/task/context/risk/note） | 当前 state_type 更细（goal/owner/decision/blocker/deadline/next_step/deferred/member_status），与 dev spec 的 decision/task/risk 不完全对应 | 中（2h）：建立映射关系，或直接扩展 state_type |

### ❌ 当前不可实现的

| 功能 | 原因 |
|---|---|
| 群消息置顶 | `lark-cli` 不支持置顶 API。当前只能发新消息，不能编辑已有消息 |
| 自动刷新（事件触发） | 需要 Webhook 监听，当前项目没有后端服务。只能在命令触发时刷新 |
| user_id 个人视图的完整实现 | 当前 MemoryItem 的 `owner` 只存姓名（张三/李四），没有飞书 open_id。如果要按 user_id 精确过滤，需要打通飞书用户身份系统 |
| 群到 project_id 自动推断 | 当前 project_id 是手动传入的，没有映射表 |
| `raw_snippets` 原始消息片段 | 当前 `raw_events.jsonl` 存了原始消息，但 `source_refs.excerpt` 只有 240 字摘要。完整原文需要重新从 JSONL 读取 |

---

## 二、更新后的开发计划（按实际可用时间排序）

每天 Claude Code Opus 4.7 约 2-3 小时的计算量估算。

### 第 1 天（2-3h）：形态 A + 形态 C 核心函数

**目标**：把现有的 `handoff.generate_handoff()` 升级为 Dev Spec 要求的两个新形态。

#### 1.1 `build_group_project_state()` — 新增（1.5h）

在 `src/memory/` 下新增 `project_state.py`（不修改现有 handoff.py，保持向前兼容）：

```python
def build_group_project_state(
    project_id: str,
    items: list[MemoryItem],
) -> dict:
    """
    从 Memory 中聚合项目状态，返回 Dev Spec 3.3 的结构化字典。
    - owners: 从 owner 类型的记忆聚合
    - open_decisions: state_type=decision 且含"待定/考虑"等
    - recent_decisions: state_type=decision 且含"确认/确定/决定"
    - active_tasks: state_type=next_step 或 owner 非空
    - risks: state_type=blocker
    - next_actions: state_type=next_step 且有 owner
    - 无数据时优雅降级（空列表，不抛错）
    """
```

#### 1.2 `render_group_state_panel_text()` — 新增（0.5h）

```python
def render_group_state_panel_text(state: dict) -> str:
    """渲染为带有 emoji 标记的 Markdown 文本，可直接发到群里。"""
```

#### 1.3 `build_agent_context_pack()` — 新增（1h）

```python
def build_agent_context_pack(
    project_id: str,
    items: list[MemoryItem],
    user_id: str | None = None,
) -> dict:
    """返回 Dev Spec 5.2 的结构化 JSON。含 project、decisions、tasks、risks 等。"""
```

#### 1.4 测试（0.5h）

`tests/test_project_state.py` — 8-10 个测试：
- 正常场景：多个决策、任务、阻塞 → 正确聚合
- 空数据：无记忆时 → 空列表 + 降级文案
- supersede 处理：被覆盖的决策不出现在 recent_decisions 中
- owner 按用户过滤

---

### 第 2 天（2-3h）：`my_state` + lark-cli 集成

#### 2.1 `build_personal_work_context()` — 新增（1.5h）

```python
def build_personal_work_context(
    user_id: str,        # 飞书 user_id 或用户名
    items: list[MemoryItem],
    project_id: str | None = None,
) -> dict:
    """
    聚合某个用户的工作上下文。
    - 按 owner 过滤出该用户相关的记忆
    - 按 search_keywords 找到用户被提及的记忆
    - 返回 Dev Spec 4.2 的结构化字典
    """
```

#### 2.2 `render_personal_context_text()` — 新增（0.5h）

```python
def render_personal_context_text(ctx: dict) -> str:
    """
    渲染为私聊可发送的 Markdown。
    - 无任务时显示"你当前没有分配给你的任务"
    - 无风险时不显示风险区块
    """
```

#### 2.3 lark-cli 验证 + 脚本集成（1h）

验证 `im +messages-send --markdown` 是否支持当前 markdown 格式。
新增 `scripts/demo_project_state.py`：

```bash
python scripts/demo_project_state.py --chat-id oc_xxx --project-id demo
# → 构建状态面板 → 用 bot 身份发到群里
```

**如果 `--markdown` 不被 `lark-cli` 支持**，则回退为 `--text` 纯文本格式。

---

### 第 3 天（2-3h）：type 映射 + owner 修复 + 文档

#### 3.1 state_type 映射（1h）

`project_state.py` 中增加映射函数，将内部 state_type 映射到 Dev Spec 的对外 type：

```
internal → external:
  decision        → decision
  blocker         → risk
  next_step       → task（有 owner）/ note（无 owner）
  goal            → project_goal
  owner           → ownership
  deadline        → deadline
  member_status   → member_info
  deferred        → deferred
```

#### 3.2 owner 正则修复（0.5h）

`extractor.py: _extract_owner()` — 修复正则，确保 `"负责人：张三负责"` 只提取 `"张三"`。

#### 3.3 documentation（1h）

- 更新 `README.md` 的能力表 + 使用示例
- 更新 `V1.5_改动说明.md`（改为全版本说明文档）

---

## 三、明确不做的事

- ❌ 群消息置顶/编辑（lark-cli 不支持）
- ❌ 自动刷新/事件驱动（需要后端服务）
- ❌ user_id 与飞书 open_id 打通（当前 owner 只存姓名）
- ❌ 向量数据库/嵌入模型
- ❌ Webhook 实时监听
- ❌ 多提取策略架构重构
- ❌ raw_snippets 完整原文回溯（当前 source_refs.excerpt 够用）

---

## 四、当前项目状态速查

| 指标 | 值 |
|---|---|
| 测试总数 | 90 |
| Golden Set (RuleBased) | 30/30 (100%) |
| Golden Set (LLM) | 30/30 (100%) |
| 功能版本 | V1.9（已完成：V1.1→1.5→1.6→1.7→1.8→1.9） |
| 最近完成 | ADD-only 提取策略、关键词搜索 |
| 计划做 | 3 个演示形态（project_state / personal context / agent pack） |