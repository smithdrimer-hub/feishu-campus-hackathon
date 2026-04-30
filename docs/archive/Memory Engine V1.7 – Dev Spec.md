# Memory Engine V1.7 – Dev Spec（for AI Assistant）

## 0. 背景 & 目标

- 平台：飞书（Lark）+ `lark-cli`
- 已有能力（V1.6，大致情况）：
  - 能从指定群读取消息（含线程/回复）
  - 通过 LLM 抽取出结构化的「协作记忆」：决策、任务、上下文等
  - 以 JSON 形式存储，包含 `project_id`、`type`、`status`、`actors`、`supersedes` 等字段
  - 具备「双时间（bi-temporal）」思想：记录**事件发生时间**和**被记录进系统的时间**
  - 有一个 handoff 脚本，可以为人类生成「项目交接说明」

- V1.7 的目标：在不大改底层 Memory Engine 的前提下，把现有能力产品化为 3 个可演示形态：
  1. **群组项目状态面板（Group Project State Panel）**
  2. **个人工作上下文视图（Personal Work Context View）**
  3. **Agent Context Pack（供其他 Agent/模型调用的上下文包）**

- 优先级：
  - **MUST HAVE**：形态 A、形态 C 做到可用；形态 B 做到基础版本
  - **NICE TO HAVE**：时间线视图更完整、美化输出卡片等

---

## 1. 现有数据模型（V1.6 假定）

> 如果项目里已有更精确的 schema，请在实现时以实际代码为准，这里是「目标结构」。

```jsonc
{
  "id": "mem_123",
  "project_id": "proj_abc",
  "chat_id": "oc_xxx",         // 飞书群 ID
  "message_ids": ["om_x1"],    // 相关消息 ID 列表
  "type": "decision",          // "decision" | "task" | "context" | "risk" | "note"
  "title": "决定采用方案B",
  "content": "更详细的描述 …",
  "actors": ["user_a", "user_b"],
  "status": "open",            // 对决策: "open" | "confirmed" | "reverted"
                               // 对任务: "todo" | "in_progress" | "done"
  "supersedes": ["mem_120"],   // 当前条目覆盖/更新了哪些旧条目
  "created_at": "2024-04-24T10:00:00Z",    // 事件发生时间
  "recorded_at": "2024-04-24T10:05:00Z",   // 写入记忆系统的时间
  "last_updated_at": "2024-04-24T10:05:00Z"
}
```

假定已有：
- 读写记忆的基础 API，例如：
  - `load_memories(project_id, chat_id=None) -> List[Memory]`
  - `save_memory(memory: Memory) -> None`
- 从飞书消息到 Memory 的抽取流程已存在，我们暂时不改。

---

## 2. V1.7 总体输出

**V1.7 要交付的是 3 个「视图/包装器」：**

1. **Group Project State Panel**  
   - 面向：项目群里的所有成员  
   - 载体：一条固定的群消息（推荐：文本 + 富格式 / 卡片），最好能用群「置顶」或「公告」能力保持常驻  
   - 行为：定期/手动刷新，展示项目当前状态

2. **Personal Work Context View**  
   - 面向：单个用户（在同一个群或私聊里 @bot）  
   - 行为：从 Memory 中筛出与该用户相关的信息，给出「我现在在这个项目/这几个讨论里的位置」

3. **Agent Context Pack**  
   - 面向：后续要接入的其他 Agent / 工具  
   - 形式：一个结构化 JSON，对指定 `project_id`（和可选 `user_id`）给出「最有用的上下文包」

---

## 3. 形态 A：群组项目状态面板（Group Project State Panel）

### 3.1 目标

在指定项目群（`chat_id`）中，生成/更新一条「项目状态概览」消息，包含：

- 项目标题 / 简要描述
- 当前阶段 / 里程碑
- 最近一次重要决策
- 当前打开的关键决策（尚未定案）
- 进行中的任务总结
- 重大风险 / 阻塞
- 下一步行动（含负责人和预估时间）

### 3.2 触发方式（可以二选一或都实现）

1. **命令触发**
   - 用户在群里发送：`@bot 项目状态` 或 `/project_state`
   - Bot 回复一条状态面板消息

2. **自动刷新（可选）**
   - 当某些关键类型的 Memory 写入（例如新决策、新风险、新已完成任务）时，自动刷新群里的状态面板（更新原消息，而不是新发一条）。

> 如果暂时不好做自动刷新，可以先只做命令触发。

### 3.3 状态面板的数据结构（内部聚合结果）

请实现一个函数（或等价能力）：

```python
def build_group_project_state(
    project_id: str,
    chat_id: str
) -> dict:
    """
    从 Memory 中聚合项目状态，用于渲染群状态面板。
    """
```

返回结构建议如下（可适当调整字段名，但要保证清晰）：

```jsonc
{
  "project_id": "proj_abc",
  "chat_id": "oc_xxx",
  "project_title": "飞书 AI 记忆引擎 Demo",
  "project_description": "为多成员项目交接和中断恢复提供长程记忆支持。",
  "current_phase": "实现与 Demo 打磨",
  "last_major_update_at": "2024-04-27T09:00:00Z",

  "owners": [
    {
      "user_id": "user_smith",
      "role": "Core Engineer"
    },
    {
      "user_id": "user_flewolf",
      "role": "Product & Research"
    }
  ],

  "open_decisions": [
    {
      "id": "mem_200",
      "title": "是否接入多 Agent 演示？",
      "status": "open",
      "last_discussed_at": "2024-04-26T12:00:00Z"
    }
  ],

  "recent_decisions": [
    {
      "id": "mem_190",
      "title": "V1.7 主要展示三种形态",
      "status": "confirmed",
      "decided_at": "2024-04-25T20:00:00Z"
    }
  ],

  "active_tasks": [
    {
      "id": "mem_210",
      "title": "实现群项目状态面板渲染",
      "assignees": ["user_smith"],
      "status": "in_progress",
      "due": "2024-04-28"
    }
  ],

  "risks": [
    {
      "id": "mem_220",
      "description": "时间紧张，V1.7 可能只能实现核心路径。",
      "severity": "medium"
    }
  ],

  "next_actions": [
    {
      "title": "确定 Demo 路径与剧本",
      "owner": "user_flewolf",
      "target_time": "2024-04-28"
    }
  ]
}
```

### 3.4 群内消息渲染 & 置顶

用 `lark-cli` 做两件事：

1. **发送/更新状态面板消息**

   推荐输出格式为 Markdown / 富文本，示例：

   ```text
   【项目状态】飞书 AI 记忆引擎 Demo
   阶段：实现与 Demo 打磨
   最近一次重要更新：2024-04-27 17:00

   👥 Owner
   - @Smith Drimer（Core Engineer）
   - @Flewolf（Product & Research）

   ✅ 最近决策
   - [已定] V1.7 主要展示三种形态（2024-04-25）

   ❓ 打开的关键决策
   - [待定] 是否接入多 Agent 演示？

   📌 进行中任务
   - 实现群项目状态面板渲染（Owner: @Smith，Due: 4-28）

   ⚠️ 风险
   - 时间紧张，V1.7 可能只能实现核心路径。

   ▶️ 下一步
   - 确定 Demo 路径与剧本（Owner: @Flewolf，目标：4-28）
   ```

   可以实现一个类似的函数：

   ```python
   def render_group_state_panel_text(state: dict) -> str:
       """
       把 build_group_project_state 的结果渲染为文本/Markdown。
       """
   ```

2. **尝试用置顶/公告保持常驻（如果 `lark-cli` 支持）**

   - 如果 CLI 支持「置顶消息」：
     - 第一次生成状态面板时，发送消息并置顶
     - 后续刷新时，编辑这条消息内容，保持置顶不变
   - 如果不支持置顶，则只需要确保：
     - 每次命令触发时，生成一条新的状态消息即可

   > 如果确认 CLI 暂时不支持置顶，请在代码注释中说明假设与限制。

---

## 4. 形态 B：个人工作上下文视图（Personal Work Context View）

### 4.1 目标

当用户在群或私聊中对 Bot 发送命令（例如：

- 在项目群里：`@bot 我的现在状态` 或 `/my_state`
- 或在私聊里：`/my_state proj_abc`

Bot 返回一个**仅对该用户相关**的上下文视图，包括：

- TA 当前参与的项目状态简表
- 在当前项目中的角色 / 当前任务
- 近期与 TA 相关的决策 / 讨论节点
- 推荐的「下一步动作」

### 4.2 聚合函数

实现函数：

```python
def build_personal_work_context(
    user_id: str,
    project_id: str | None = None,
    chat_id: str | None = None
) -> dict:
    """
    聚合某个用户在某个项目（或某个群）中的当前工作上下文。
    - 如果只给 chat_id，尝试推断对应 project_id；
    - 如果给了 project_id，则以 project_id 为主。
    """
```

返回结构示例：

```jsonc
{
  "user_id": "user_flewolf",
  "project_id": "proj_abc",
  "chat_id": "oc_xxx",

  "role_in_project": "Product & Research",
  "my_open_tasks": [
    {
      "id": "mem_300",
      "title": "完成 V1.7 延展地图文档",
      "status": "in_progress",
      "due": "2024-04-28"
    }
  ],

  "my_recent_decisions_involved": [
    {
      "id": "mem_190",
      "title": "同意用三种形态展示 V1.7",
      "role": "proposer",  // "proposer" | "approver" | "participant"
      "decided_at": "2024-04-25T20:00:00Z"
    }
  ],

  "my_related_risks": [
    {
      "id": "mem_310",
      "description": "个人对飞书企业协作场景经验不足，可能影响 use case 选择。",
      "mitigation": "通过用户访谈/官方文档补足场景理解。"
    }
  ],

  "suggested_next_actions": [
    {
      "title": "和 Smith 对齐 V1.7 文档优先级，并确定 Demo 剧本。",
      "target_time": "2024-04-28"
    }
  ]
}
```

再实现一个简单渲染：

```python
def render_personal_context_text(ctx: dict) -> str:
    """
    把个人上下文打成一段可发给用户的文本/Markdown。
    """
```

输出示例：

```text
【你的当前状态 @ 飞书 AI 记忆引擎 Demo】

角色：Product & Research

📌 你当前的任务
- 完成 V1.7 延展地图文档（状态：进行中，目标：4-28）

🧠 最近你参与的关键决策
- 同意用三种形态展示 V1.7（2024-04-25，角色：发起）

⚠️ 与你相关的风险
- 对飞书企业协作场景经验不足 → 建议：通过用户访谈/官方文档补足场景理解

▶️ 推荐下一步
- 和 Smith 对齐 V1.7 文档优先级，并确定 Demo 剧本。
```

---

## 5. 形态 C：Agent Context Pack

### 5.1 目标

为未来要对接的其他 Agent（例如「Dev Agent」「QA Agent」）提供一个统一入口：

- 输入：`project_id`（可选 `user_id`）
- 输出：一个结构化 JSON，上面聚合好：
  - 项目关键元信息
  - 最新且有效的决策集（已自动处理 supersedes）
  - 当前任务列表
  - 最近 N 条高价值讨论片段
  - 如果给了 `user_id`，再附加该用户视角的上下文

这个 JSON 应该是**机器可直接使用**的，不需要人类阅读友好。

### 5.2 函数接口

实现函数：

```python
def build_agent_context_pack(
    project_id: str,
    user_id: str | None = None,
    max_items_per_section: int = 20
) -> dict:
    """
    构造给其他 Agent 使用的上下文包。
    不做自然语言渲染，只做结构化聚合和筛选。
    """
```

建议返回结构：

```jsonc
{
  "project": {
    "project_id": "proj_abc",
    "title": "飞书 AI 记忆引擎 Demo",
    "description": "为多成员项目交接和中断恢复提供长程记忆支持。",
    "current_phase": "实现与 Demo 打磨"
  },

  "decisions": [
    {
      "id": "mem_190",
      "title": "V1.7 主要展示三种形态",
      "status": "confirmed",
      "decided_at": "2024-04-25T20:00:00Z",
      "supersedes": ["mem_150", "mem_160"],
      "raw_snippets": [
        {
          "chat_id": "oc_xxx",
          "message_id": "om_1",
          "text": "我觉得可以把 Memory Engine 做成三种对外形态……"
        }
      ]
    }
  ],

  "tasks": [
    {
      "id": "mem_210",
      "title": "实现群项目状态面板渲染",
      "status": "in_progress",
      "assignees": ["user_smith"],
      "due": "2024-04-28"
    }
  ],

  "risks": [
    {
      "id": "mem_220",
      "description": "时间紧张，V1.7 可能只能实现核心路径。",
      "severity": "medium"
    }
  ],

  "recent_discussion_snippets": [
    {
      "chat_id": "oc_xxx",
      "message_id": "om_2",
      "sender": "user_smith",
      "sent_at": "2024-04-25T19:50:00Z",
      "text": "lark-cli 现在能直接做到这些 action，我先搭一个 V1.6 出来……",
      "tags": ["engineering_plan", "feasibility"]
    }
  ],

  "user_perspective": {
    "user_id": "user_flewolf",
    "role_in_project": "Product & Research",
    "open_tasks": [
      {
        "id": "mem_300",
        "title": "完成 V1.7 延展地图文档",
        "status": "in_progress",
        "due": "2024-04-28"
      }
    ]
  }
}
```

要求：

- 在构造 `decisions` 时，自动处理 `supersedes`：
  - 对于被后续决策覆盖的条目（在其 `supersedes` 列表中），只保留最新版本
- `recent_discussion_snippets` 可以通过：
  - 优先从与「决策/任务」关联的消息中选取摘要片段
  - 或简单按时间取最近 N 条高价值消息（如果已有打分）

---

## 6. 与 lark-cli 的集成（最小要求）

### 6.1 命令约定（可以按实际 CLI 设计调整）

在 CLI 中实现以下指令（或等价功能）：

1. `project_state`  
   - 参数：`--chat_id` 或从当前会话环境读取  
   - 行为：
     - 推断/获取对应 `project_id`
     - 调用 `build_group_project_state` + `render_group_state_panel_text`
     - 发送到群，并（如支持）置顶或更新已有状态消息

2. `my_state`  
   - 参数：`--user_id`、`--project_id?`、`--chat_id?`  
   - 行为：
     - 调用 `build_personal_work_context` + `render_personal_context_text`
     - 发送到对应聊天（群里 @ 用户 或 私聊）

3. `agent_context_pack`（供调试/后续接链路）  
   - 参数：`--project_id`、`--user_id?`  
   - 行为：
     - 打印 JSON 到 stdout，便于其他进程/Agent 消费

### 6.2 假设与限制

在代码中请用注释明确：

- 如果你对 `project_id` 推断有简化假设（例如：一个群只对应一个项目），请写清楚
- 如果 `lark-cli` 不支持某个操作（例如编辑历史消息、置顶），请在调用前后用注释写：  
  -「这里理想行为是 XXX，目前用 YYY 方式代替」

---

## 7. 简单验收标准（Acceptance Criteria）

1. **Group Project State Panel**
   - 在一个测试群中执行 `project_state`，至少能生成一条包含：
     - 项目标题
     - 当前阶段
     - 至少一个任务和至少一个决策（如果有）
   - 若无数据（例如暂无任务/决策），文案需要优雅降级（不报错）

2. **Personal Work Context View**
   - 对给定 `user_id` + `project_id`，能返回 JSON 结构（即使某些字段为空列表）
   - 渲染文本中至少包含：1 个任务 或 一句「你当前没有分配给你的任务」

3. **Agent Context Pack**
   - 调用 `agent_context_pack` 指令，能得到结构化 JSON，字段名稳定
   - 如果一个决策被多次修改，仅保留一条最新决策出现在 `decisions` 列表中

---

## 8. 给 AI 的实施建议（可选）

当你根据本文档写代码时，可以遵循：

- 优先实现纯函数：
  - `build_group_project_state`
  - `render_group_state_panel_text`
  - `build_personal_work_context`
  - `render_personal_context_text`
  - `build_agent_context_pack`
- 与 `lark-cli` 的交互（发消息、置顶等）放在单独模块/函数中，方便之后替换
- 对外暴露简单 CLI 命令或脚本入口，方便人工测试和 Demo 录制

如果对任何需求有不确定，可以在代码注释中用 `TODO:` 标明，并写上你的假设。
```

---

