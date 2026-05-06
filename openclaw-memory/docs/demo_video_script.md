# 🎬 Demo Video Script · OpenClaw Memory Engine

> **双用脚本**：评委可读、AI 视频工具（Seedance 2 / Sora / Runway）可直接生成参考。
>
> 总时长：5-9 分钟（推荐 7 分钟）。
>
> 渲染策略：3 种素材混排
> 1. **真实飞书截图**（主体，60%）：你已经发出去的 6 张卡片 + AI Agent 卡 + 任务通知
> 2. **AI 生成镜头**（40%）：人物/办公室/抽象网络可视化，Seedance 2 + 参考图
> 3. **打字幕 / Apple keynote 风格 transition**（10%）

---

## 🎯 一线串联的 Spine

**主角：一条 blocker —— "登录页设计稿 v2 还没出"。**

这条 blocker 在 movie demo 真实数据中存在（msg_id `mv_018`，发送者：前端-吴凡）。
它从一条普通群消息诞生 → **被系统从 8 个角度依次"看见"** → 最终被 AI 转成飞书任务并指派给真人。

每一幕的"主语"都是它：

```
诞生（一条消息）
  ↓ 1. 被晨报看见 — 出差回来的人在地铁里第一眼见到它
  ↓ 2. 被编排器看见 — 它卡住 2 个下游任务，成为 P0 解阻塞口
  ↓ 3. 被 AI agent 看见 — Memory + Pattern → 风险分析 + 行动建议
  ↓ 4. 被 ActionExecutor 看见 — 真实创建飞书任务，指派给真人
  ↓ 5. 被 Pattern 看见 — 与另一条设计稿阻塞合并成"组织级热点"
  ↓ 6. 被站会摘要看见 — Yesterday/Today/Blockers 三段式结构化
  ↓ 7. 被审核台看见 — AI 写回的决策需要 CTO 裁定
  ↓ 8. 被交接摘要看见 — 突发离场时，作为 8 维之一传给接手人
```

**核心信息（log line）**：

> 你不在的时候，团队仍在协作。Memory Engine 让你回来时无缝接住每一根线头。

---

## 📐 单幕格式（每幕都按这个填）

```
ACT N  ·  时长 X 秒  ·  时间 HH:MM
─────────────────────────────────────
🎥 视觉
  - 主镜头描述（POV / 构图 / 节奏）
  - 关键帧描述（用作 Seedance 参考图）
  - 真实素材：[飞书消息 ID 或 demo_movie scene]

🔊 旁白 / 字幕
  「中文台词，简洁有力」
  
🎵 音乐 / 音效
  - 风格：calm / tense / build-up
  - 音效：keystroke / notification / hush
  
🔗 spine 进度
  - 这一幕里，blocker 的状态从 X → Y
  - 引用：其他出现过的 memory id

📝 production note
  - 镜头切换时机
  - 文字浮现时间点
  - 真实截图 vs AI 生成的占比
```

---

## 🎬 Cold Open · 0:00 – 0:30

```
ACT 0  ·  时长 30 秒  ·  时间 黑屏 → 02:14 凌晨
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：纯黑屏。白色字幕逐字出现：
        「被嫌弃的 blocker 的一生」
        — A Memory Engine Story —
    字体：思源宋体细 / Apple keynote 风
  
  - 镜头 2 (10s)：办公楼远景，深夜。一扇窗还亮着。
    缓慢推拉镜头，带轻微噪点（电影感）。
    AI 生成参考：cyberpunk 现代写字楼 + 一盏暖黄色窗灯
  
  - 镜头 3 (10s)：飞书群聊截图（真实），屏幕滚动定格在 mv_018：
        「[前端-吴凡] 阻塞：登录页设计稿 v2 还没出，前端动不了」
    时间戳：18:00。镜头放大到这条消息，光影聚焦。
    
  - 镜头 4 (5s)：消息变为一个发光"光点"，从聊天界面飞起，
    淡入到下一幕（化为 Memory 的"种子"）。

🔊 旁白
  「这是一条普通的群消息。」
  「但它即将开始一段不普通的旅程——」
  「被系统从 8 个角度依次看见。」

🎵 音乐
  - 起：极简钢琴单音（一个键，重复）
  - 末：轻微弦乐渐入

🔗 spine 进度
  - blocker.state = born
  - msg_id = mv_018
  - confidence = 0.85（规则识别）

📝 production note
  - 真实截图 70%，AI 生成 30%
  - 字幕"被嫌弃的 blocker 的一生"是全片唯一的玩笑点，必须留
  - 镜头 4 的"光点飞起"是全片视觉母题（之后每幕开头都会有一次）
```

---

## 🎬 ACT 1 · 诞生（0:30 – 1:15）

```
ACT 1  ·  时长 45 秒  ·  时间 5/5 18:00 → 5/6 08:32
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (8s)：从镜头 4 延续。光点继续上升，分裂成 8 个数据流，
    各自飞向不同方向（暗示后面 8 幕）。
    AI 生成参考：宫崎骏《千与千寻》中神明列车的票飞起的镜头
    
  - 镜头 2 (10s)：黑屏切到飞书桌面客户端真实截图：
    一夜过去，群聊滚动了 30 多条消息，blocker 那条已被淹没在最上方。
    
  - 镜头 3 (12s)：切到 06:00 通勤地铁内，POV 第一视角。
    手机屏幕亮起，飞书图标，未读 247。
    AI 生成参考：日本动画风格的地铁内部 + 暖色车窗光
    
  - 镜头 4 (10s)：手指点开飞书，但停在群聊列表，没点进去。
    人物深呼吸（看不到脸，只看手）。
    
  - 镜头 5 (5s)：屏幕弹出私信通知："Memory Bot · 早安卡片"
    手指点击。

🔊 旁白
  「过去的 12 小时，团队聊了 247 条消息。」
  「其中关于这条 blocker 的，有 8 条。」
  「但你出差刚回来，没有时间一条条翻——」

🎵 音乐
  - 钢琴单音延续
  - 通勤场景叠加微弱白噪音（地铁/雨/呼吸）

🔗 spine 进度
  - blocker.state = born → indexed
  - 系统已经悄悄做完了：extraction → store → pattern derivation
  - 但用户什么都没做

📝 production note
  - 重点呈现"信息暴雨"vs"用户的疲惫"对比
  - 镜头 5 的私信弹窗是衔接到 ACT 2 的钩子
  - 真实素材占比 80%（飞书截图 + 通勤场景照片合成 + 私信截图）
```

## 🎬 ACT 2 · 第一次被看见（晨报）（1:15 – 2:00）

```
ACT 2  ·  时长 45 秒  ·  时间 08:32 通勤路上
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：手机屏幕展开为全屏 — 真实截图：飞书 SCENE 1 的早安卡。
    卡片 message_id = om_x100b508dfafa938cb2572bcabaff0fe（已发出去）
    
  - 镜头 2 (15s)：相机沿着卡片缓慢竖向滚动。每滚到一节，
    屏幕短暂高亮 + 浮起一段字幕：
    
        ↳ "📥 你不在的 2 天里"            （1.5s）
        ↳ "🔥 等你处理的事"               （1.5s）
        ↳ "⏰ 临近截止日"                 （1.5s）
        ↳ "👥 队友状态：赵六请假"         （1.5s）
        ↳ "▶️ 系统为你排好的优先级"      （1.5s）
        
  - 镜头 3 (12s)：画面定格在"🔥 等你处理的事"那一节，
    "登录页设计稿 v2 还没出"那条文字泛起一圈红色微光。
    旁白同时点出"你的 blocker 浮起来了"。
    
  - 镜头 4 (8s)：人物（仍 POV 视角）放下咖啡，深呼吸，
    嘴角微松。屏幕上是井然有序的工作清单。
    AI 生成参考：电影《Her》中 Theodore 看屏幕的特写

  - 镜头 5 (5s)：黑场 + 白字幕："0 秒进入工作状态。"
    单帧停留，下一幕硬切。

🔊 旁白
  「Memory Engine 替你翻完了 247 条消息。」
  「它知道你的领域：登录模块前端。」
  「它知道你的 blocker：设计稿 v2。」
  「它知道你今天该先做什么。」
  
  （短暂停顿）
  
  「你回到工作的时间：0 秒。」

🎵 音乐
  - 钢琴单音 → 加入轻柔弦乐
  - 当镜头 5 字幕出现时：音乐 swell 一下

🔗 spine 进度
  - blocker 第一次被一个人"看到"
  - 此时它的视角：individual / personal urgency / for me
  - 数据来自：build_morning_briefing(user_id="吴凡", project_id, items, last_seen_at="...")

📝 production note
  - 这一幕的主素材必须是真实的飞书卡片截图
  - 滚动节奏要像 Apple keynote — 慢、有节制、给人时间读
  - 视觉母题"光点"在镜头 3 那个红色微光复现
```

---

## 🎬 ACT 3 · 第二次被看见（编排）（2:00 – 2:50）

```
ACT 3  ·  时长 50 秒  ·  时间 09:00 项目大群
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (4s)：硬切。飞书项目大群的截图 — 真实：SCENE 2 编排卡。
    message_id = om_x100b508db54c70a0b101455c936e2a2
    
  - 镜头 2 (12s)：卡片高亮"🔗 阻塞依赖链"那一节。
    依赖图动效（After Effects）：
    
        [设计稿 v2] ←—卡住—— 吴凡 ←—下游—— 测试
                                     ←—下游—— 联调
                                     
    每个节点用呼吸光点表示。链条一闪一闪。
    
  - 镜头 3 (10s)：镜头放大到"P1 @设计-小林"那一行，
    @ 提及 effect 像短信发出的飞行轨迹。
    
  - 镜头 4 (12s)：分屏 4 格 — 每格一个团队成员同时看群消息。
    每个人的脸（AI 生成，不重要的远景，不需要识别）短暂浮现，
    嘴角放松。
    AI 生成参考：苹果 keynote 中"distributed users"那种风格化插画
    
  - 镜头 5 (12s)：镜头拉远，从飞书界面变成一张俯视依赖图，
    blocker 站在多米诺骨牌的最前面，点亮一下，倒下去 —
    后面的牌依次被推倒。
    每张牌上分别标着任务名："切图" "测试" "联调"。

🔊 旁白
  「在传统团队，这一幕需要一次早会，30 分钟。」
  「现在不用了。」
  「Memory Engine 看见整个团队的状态——」
  「谁卡住了谁、谁能解锁谁。」
  
  （短暂停顿）
  
  「它找到那一个堵塞口。」
  「拉开它，5 个下游同时亮起来。」

🎵 音乐
  - 弦乐渐强
  - 多米诺倒下时配 5 个轻巧的 chime（每张牌一个）

🔗 spine 进度
  - blocker 第二次被看到
  - 此时它的视角：team / structural / unblocking key
  - 数据来自：orchestrate(project_id, items)
  - 它从"我的烦恼"升级成"全队的 P0"

📝 production note
  - 多米诺动画是全片第一个 mini-climax，做工要细
  - 真实卡片截图 + 抽象 motion graphics 50:50
  - 视觉母题"光点"以呼吸节点形式贯穿整幕
```

## 🎬 ACT 4 · 第三次被看见（AI Agent）（2:50 – 3:50）

```
ACT 4  ·  时长 60 秒  ·  时间 11:08 群里求救
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：飞书项目群截图（真实），产品-小李发了一条:
        "@bot 周五能上线吗？我有点担心"
    时间戳 11:08。
    
  - 镜头 2 (8s)：屏幕右下角弹出一个"⏳ AI Agent 正在思考…"
    的小圆球，配 3 个跳动的圆点。
    与此同时屏幕一角浮现台词字幕：
        「Polling 4s 间隔检测到触发词」
        「加载 14 条 Memory + 6 类 Pattern」
        「构造 prompt 2474 字符 → DeepSeek-chat」
    （这是给评委看的"内部时序"）
    
  - 镜头 3 (12s)：12 秒后（视频里压缩到 2 秒），
    AI Agent 卡片"咚"一声出现在群聊里 — 真实截图 SCENE 4 / demo_agent_loop。
    
  - 镜头 4 (15s)：相机沿卡片滚动展示：
        🚨 风险等级：HIGH
        关键判断点（每条带 📎 原文证据）
        🎯 行动建议（P0 / P1 / P2）
        ⚙️ 引用的协作模式：handoff_risk · responsibility_domain
    
    每个 📎 标记泛起红色微光（spine 母题），
    特别是引用 mv_018（我们的 blocker）的那一条，光最亮。
    
  - 镜头 5 (8s)：镜头拉远，从手机变成一张"信息流网络图":
    blocker 这个节点，被 14 条 memory 包围，
    其中两条 pattern 的"圈"放大并推它生成一个新的"AI 行动"节点。
    AI 生成参考："Inception"梦境层视觉
    
  - 镜头 6 (12s)：人物（POV）看完卡片，没有回复——
    但他默默把"评估调整范围"加进了下午议程。
    旁白点出关键。

🔊 旁白
  「这不是简单的'智能助手回答问题'。」
  「这是 AI 真的读了 Memory，引用了 Pattern，给出了一个项目经理级的判断。」
  
  （短暂停顿）
  
  「它把一个普通员工的疑问，」
  「变成了管理者级的决策。」
  
  「12 秒，端到端。」

🎵 音乐
  - 弦乐 + 一个轻微 cyber bleep
  - 卡片"咚"一声 = 短促 piano hit
  - 镜头 6 时音乐降下来一点

🔗 spine 进度
  - blocker 第三次被看到
  - 此时它的视角：AI's reasoning / cited evidence / decisional weight
  - 数据来自：run_agent_loop() with build_agent_context_pack
  - 它从"被动状态"变成了"AI 推理的输入"

📝 production note
  - 镜头 2 的"AI 内部时序"是给评委看的硬技术亮点，必须出现，
    停留时间够让人读完
  - 镜头 5 的网络图是全片第二个视觉高潮
  - 真实截图 70%（飞书原卡），抽象 30%（网络图）
```

---

## 🎬 ACT 5 · 第四次被看见（climax · AI 真去做事）（3:50 – 4:50）

```
ACT 5  ·  时长 60 秒  ·  时间 11:30 系统后台
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：黑底 + 一行白字:
        「但 AI 不止给建议——」
    顿一拍。
        「它真的能 act。」
    
  - 镜头 2 (10s)：硬切 mac 终端窗口（真实录屏）。
    用户敲入：
        $ python scripts/demo_movie.py --scene auto_action --feishu
    回车。
    日志开始流动：
        [setup] Bootstrapping memory ... 14 memories
        🎬 SCENE 4 · AI 自动执行
        action_plan size=6 (其中 create_task=3)
    
  - 镜头 3 (15s)：日志继续：
        ✅ assigned to 徐悦 (ou_2a6d090309c…)
        executor: success=True · 1.6s · task_guid=2bb6d35b…
        飞书卡片已发：om_x100b508eae4accacb48714ed1f92f98
    
    每一行流出来时屏幕上有一个绿色 ✓ 微光。
    
  - 镜头 4 (15s)：屏幕分成两半:
        左：飞书群 — SCENE 4 任务卡（真实 message_id 的截图）
              📋 自动创建任务 · 已指派
              👤 负责人 @徐悦
              🆔 task_guid: 2bb6d35b…
              [📂 在飞书任务里查看] 按钮
        右：飞书任务页面 — 一条新任务真的出现在徐悦的"待办"里
              （需要徐悦本人录屏她飞书"我负责的"页面）
    
  - 镜头 5 (10s)：镜头继续拉远，看到本地终端的 action_log.jsonl 
    被打开 — 一行新的 JSON entry 飞入：
        {"timestamp":"...","action_type":"create_task","success":true,
         "idempotency_key":"...","output_data":{"task_guid":"2bb6d35b…",
         "assignee":"ou_2a6d090309c…"}}
    
  - 镜头 6 (5s)：闪回到 ACT 1 的"光点"母题 — 
    那个原始 blocker 现在被一条金色弧线连到了一个新生成的"任务"光点。
    数据流闭环。

🔊 旁白
  「飞书任务真的创建了。」
  「徐悦真的收到了通知。」
  「action_log 真的写了一条审计。」
  
  （加重）
  
  「Memory → ActionPlanner → ActionExecutor → lark-cli → 飞书任务 → 审计落盘。」
  「完整闭环。每一步可追溯。」

🎵 音乐
  - 节奏加快
  - 镜头 4 那一刻音乐 swell（climax）
  - 镜头 6 闭环时一声温暖的 piano resolve

🔗 spine 进度
  - blocker 第四次被看到，且第一次"被推动了"
  - 此时它的视角：actor / executable / written-back
  - 数据来自：generate_action_plan + ActionExecutor.execute
  - 它从一条文本变成了一条真实的飞书任务

📝 production note
  - 这是全片 climax，是赛题"完整闭环"最直接的硬证据
  - 真实素材占比 90%（终端 + 飞书截图）
  - 镜头 4 的双屏需要徐悦真的录屏她的飞书"我负责的"页面
  - 镜头 6 的金色弧线是回到母题，让观众潜意识里"啊，故事接上了"
```

## 🎬 ACT 6 · 第五次被看见（Pattern Memory 组织级洞察）（4:50 – 5:30）

```
ACT 6  ·  时长 40 秒  ·  时间 12:50 系统主动播报
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (3s)：黑底字幕：
        「单条阻塞 ≠ 单点问题」
        「重复出现的阻塞 = 组织瓶颈」
    
  - 镜头 2 (10s)：飞书群截图（真实 SCENE 5 卡片）：
        message_id = om_x100b508db155cca0b4b636f26d4a5d1
        
        📍 阻塞热点预警
        阻塞热点：设计。共 2 条阻塞，占未解决 blocker 的 67%。
        关联任务：登录页设计稿 v2 / 支付页设计稿 v2
        建议：与设计组对齐交付节奏
    
  - 镜头 3 (15s)：抽象动效：
    画面上出现 3 个 blocker 节点（用 mv_018 / mv_021 / mv_023 标注）。
    其中 2 个被同一个橙色"圆圈"圈住（设计 domain）。
    上方浮现"blocker_hotspot pattern"标签。
    一个圆圈向"设计组"图标延伸出箭头。
    
  - 镜头 4 (8s)：人物（项目经理 POV）看完，
    第一次有了"这不是单点问题，是流程问题"的表情。
    AI 生成参考：电影《Moneyball》中 Billy Beane 顿悟那一刻
    
  - 镜头 5 (4s)：黑场 + 白字：
        「6 类二阶 Pattern · 由 14 条 Memory 自动推导。」

🔊 旁白
  「我们的 blocker 已经不是孤立的了。」
  「Pattern Memory 把它和另一条设计稿阻塞合并成『组织级热点』。」
  
  （短暂停顿）
  
  「第一次有人意识到——」
  「不是哪个具体的设计稿在卡，」
  「是整个设计交付节奏在卡。」

🎵 音乐
  - 弦乐叠加单音 marimba
  - 镜头 4 顿悟时刻：音乐定格 1 拍，再 swell

🔗 spine 进度
  - blocker 第五次被看到
  - 此时它的视角：organizational / pattern / domain-level signal
  - 数据来自：generate_blocker_hotspot(items, project_id)
  - 它从"个人烦恼 → 团队 P0 → AI 行动 → 组织流程问题"已经走过 5 个抽象层

📝 production note
  - 这一幕主要靠抽象动画讲清楚"二阶推理"
  - 关键是镜头 3 — 评委要看明白"两个 blocker 同 domain → hotspot"
  - 真实截图 40%，抽象 60%
```

---

## 🎬 ACT 7 · 第六次被看见（站会回看一天）（5:30 – 6:10）

```
ACT 7  ·  时长 40 秒  ·  时间 18:00 站会自动播报
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (3s)：飞书群消息列表，时间跳到 18:00。
    群里几乎没人在打字，大家都下班了。
    
  - 镜头 2 (12s)：突然蹦出一张卡片（真实 SCENE 7 截图）:
        message_id = om_x100b508e4ba4b8a4b3e5ddf440e28ee
        
        📊 今日站会摘要
        
        📅 Yesterday — 昨天发生了什么
          ✅ 决策：用 Remix
          ⏸️ 暂缓：国际化下个迭代再做
          
        🎯 Today — 今天在做的事
          ▶️ Remix 路由适配 @吴凡
          ▶️ ...
          
        🚨 Blockers — 现在还卡着什么
          🔴 [前端-吴凡] 登录页设计稿 v2 还没出 → 依赖 设计-小林
    
  - 镜头 3 (10s)：镜头放大到"Blockers"那一节，
    "登录页设计稿 v2 还没出"那一行又是熟悉的红色微光。
    
    旁边浮起字幕：
        「这是它今天第六次被看见」
    
  - 镜头 4 (10s)：远景。办公楼空了。屏幕里这条卡片仍亮着。
    
  - 镜头 5 (5s)：背景音乐淡去，只剩一句字幕：
        「无早会 · 无日报 · 自动从 26 条群消息聚合」

🔊 旁白
  「你已经下班了。」
  「但 Memory Engine 替你写完了今天的 standup。」
  「Yesterday / Today / Blockers — 三段式，结构化，可点击。」

🎵 音乐
  - 节奏放缓
  - 钢琴回到最初的单音

🔗 spine 进度
  - blocker 第六次被看到
  - 此时它的视角：daily summary / temporal capsule / the day reflected
  - 数据来自：render_standup_summary(items)
  - 它从一条事件升格成"今天的故事的一部分"

📝 production note
  - 时间感是这一幕的灵魂 — 让观众感受到一天结束了
  - 真实截图 80%，远景空办公楼镜头是 AI 生成
  - 镜头 5 的字幕"无早会"对评委来说是 ROI 数据点
```

## 🎬 ACT 8 · 第七次被看见（审核台 · CTO 夜间复核）（6:10 – 6:50）

```
ACT 8  ·  时长 40 秒  ·  时间 18:30 CTO 私聊
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：飞书私聊界面，CTO 视角（POV）。
    一条新私信弹出，标题"⚖️ 决策审核台"。
    
  - 镜头 2 (15s)：真实卡片（SCENE 8 截图）:
        message_id = om_x100b508e5a3544a0b4b4f3260f53ebc
        
        ⚖️ 决策审核台
        今日产出 14 条新记忆，3 条需要你确认
        
        1. [decision] 🤖 AI · decision_strength=tentative
           > 周五上线风险高，需确认设计稿时间
           [✅ approve] [❌ reject] [✏️ modify] [🔀 merge]
           
        2. [decision] 👤 人 · 🚨 冲突
           > 改用 Remix 框架
           ⚠️ 与 5/3 旧决策（用 React 18）矛盾
    
  - 镜头 3 (10s)：手指点击 "✅ approve" 第 1 条。
    画面上 AI 生成的决策从 needs_review 变成 approved，
    一个金色光圈扩散开。
    
  - 镜头 4 (10s)：屏幕分屏。
    左：原 AI 决策（来自 ACT 4-5 的 actor_type=ai_agent）
    右：CTO 修改了它的 current_value—— 把"加班"改成"调整范围"
    旁白点出：「人有最终裁定权。AI 是助手，不是替代。」

🔊 旁白
  「AI 写的决策、人类写的决策，在审核台并排出现。」
  「actor_type=ai_agent 让每一条都可追溯。」
  「decision_strength 让风险分级。」
  
  （短暂停顿）
  
  「不是 AI 取代决策——」
  「是 AI 帮你看见，人来定夺。」

🎵 音乐
  - 沉稳的低音弦乐
  - approve 时一个温暖的 chime

🔗 spine 进度
  - blocker（衍生出的 AI decision）第七次被看到
  - 此时它的视角：governance / human verdict / audit trail
  - 数据来自：demo_review_desk + decision_strength + conflict detection
  - 它从"AI 候选"被人类正式接收为"团队记忆"

📝 production note
  - 这一幕的核心信息：治理 = 护城河
  - 镜头 4 的修改动作必须特写，让评委看到"人能改 AI 写的内容"
  - 真实截图 70%，POV 镜头 30%
```

---

## 🎬 ACT 9 · 第八次被看见（突发交接）（6:50 – 7:30）

```
ACT 9  ·  时长 40 秒  ·  时间 19:50 紧急离场
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：手机屏幕特写。一条来电:
        "幼儿园-王老师"
    POV 接听，画面外杂音："孩子发烧了，能来一下吗"
    AI 生成参考：日剧《孤独的美食家》家庭电话特写感
    
  - 镜头 2 (5s)：人物（吴凡）手忙脚乱收拾东西。
    最后一个动作：打开飞书，按下一个按钮"为我生成交接摘要"。
    
  - 镜头 3 (15s)：真实卡片（SCENE 9 截图）:
        message_id = om_x100b508e44718ca0b303e899998fffc
        
        📋 项目交接摘要 · 给王五
        🎯 项目目标
        👥 负责人
        ✅ 关键决策
        ⏰ 截止时间
        🚨 当前阻塞
            🔴 登录页设计稿 v2 还没出 → 依赖 设计-小林
            🔴 ...
        ⏸️ 暂缓事项
        👤 成员状态
        ▶️ 当前任务
    
    镜头沿卡片快速滚动 — 8 维度依次刷过。
    
  - 镜头 4 (10s)：吴凡把这条直接转给王五。
    王五打开手机，看到。
    第一帧：王五一脸茫然
    第二帧（3 秒后）：王五眼神聚焦，点头。
    第三帧：王五开始打字。
    
    旁白："3 秒。"
  
  - 镜头 5 (5s)：黑屏 + 字幕：
        「这是离职级别的复杂交接 —— 3 秒搞定。」
        
🔊 旁白
  「孩子发烧了，得马上走。」
  「8 维度交接摘要 + Pattern Memory 衍生 + 每条带证据。」
  「王五打开就能直接接手。」
  
  （短暂停顿）
  
  「这是离职级别的复杂交接——」
  「3 秒搞定。」

🎵 音乐
  - 紧迫感（电话来的瞬间）
  - 卡片生成时音乐回归温柔
  - 镜头 5 字幕静默

🔗 spine 进度
  - blocker 第八次被看到 — 也是最后一次
  - 此时它的视角：handoff dimension / inheritance / the file you pass on
  - 数据来自：generate_handoff + 8 个 SECTION_TITLES
  - 它从"今日热点"变成"交给下一个人的钥匙之一"

📝 production note
  - 这是全片情感峰值 — 电话突发 + 3 秒响应的对比
  - 真实截图 70%，POV 30%
  - 王五接收的瞬间是关键情感锚点（必须录到他/她的真实表情）
```

---

## 🎬 EPILOGUE · 我们的 blocker 终于"被解决了"（7:30 – 8:30）

```
EPILOGUE  ·  时长 60 秒  ·  时间 周四 09:00 / 第二天
─────────────────────────────────────
🎥 视觉
  - 镜头 1 (5s)：日出。城市俯瞰，办公楼亮起。
    AI 生成参考：宫崎骏《天空之城》的日出
    
  - 镜头 2 (10s)：飞书群截图（伪造，但风格保持一致）:
        [设计-小林] 设计稿 v2 来啦 [文件] login_v2.fig
        [前端-王五] 收到，开始切图
        [Memory Bot] 🎉 阻塞已解除：登录页设计稿 v2 → resolved
        
  - 镜头 3 (8s)：闪回：把 ACT 1-9 中那些"红色微光"瞬间用 mosaic 形式
    快速串起来——同一条 blocker 在 8 个不同视角的样子。
    
  - 镜头 4 (15s)：黑屏 + 关键数据飞入:
        ⏱️ 12s   AI Agent 端到端
        🎯 100%  Golden Set RuleOnly 通过
        🤖 327   单元测试全过
        📋 1     真实创建的飞书任务
        🔁 8     blocker 被看见的视角
        🔒 1     完整闭环
    
  - 镜头 5 (10s)：视觉母题最后一次复现 ——
    那个原始光点从最初的群消息升起，
    经过 8 个节点（每个对应一个 ACT），
    最终汇入一个温暖的核心——"Memory Engine"。
    
  - 镜头 6 (10s)：白底黑字 keynote 风:
        「OpenClaw Memory Engine」
        「The OS for hybrid human-AI teams.」
        
        github.com/smithdrimer-hub/feishu-campus-hackathon
        飞书校园大赛 · OpenClaw 赛道
        
  - 镜头 7 (2s)：纯黑 fade out。
    最后一句字幕（复用片头副标题，呼应）：
        「被嫌弃的 blocker，也有被记住的一生。」

🔊 旁白
  「一条普通的 blocker。」
  「被 8 个角度看过。」
  「被一个真人 + 一个 AI 协作解决。」
  
  （转折，温暖）
  
  「这是 5 个人 + N 个 AI Agent 同时工作的样子。」
  「这是 Memory Engine 让团队真正连接起来的样子。」
  
  （最后一句留白，等字幕浮起）

🎵 音乐
  - 钢琴主题完整回归
  - 镜头 4 数字飞入时音乐 build-up
  - 镜头 5 母题闭合时一声 piano resolve
  - 镜头 7 fade 时只剩呼吸声

🔗 spine 进度
  - blocker 完成它的"一生"
  - 它从一个被嫌弃的"我动不了"，变成了团队记忆的一根线
  - 这根线让无数次"重建上下文"的成本归零

📝 production note
  - 镜头 4 的数据飞入是赛题"提速 / 节省 / 完整闭环"的最直接答案
  - 镜头 7 的最后一句字幕是 callback to cold open
  - 真实素材 50%，AI 生成 50%
  - 整片在这一刻完成 spine 的闭环
```

---

# 🎬 PRODUCTION PACKAGE

> 这一节是制作素材，给视频生成工具 + 剪辑用。

## 📸 关键帧清单（Storyboard）

按场景列出每幕需要的素材 + 来源：

| ACT | 帧 # | 类型 | 素材 | 备注 |
|-----|------|------|------|------|
| 0 | 1 | 文字 | "被嫌弃的 blocker 的一生" | 思源宋体细白 |
| 0 | 3 | **真实截图** | 飞书群 mv_018 消息 | 我们的数据集 |
| 1 | 3 | AI 生成 | 日漫风地铁 + 暖光车窗 | Seedance prompt #1 |
| 2 | 1 | **真实截图** | SCENE 1 早安卡（om_…8df）| ✅ 已发出 |
| 2 | 4 | AI 生成 | 通勤者放下咖啡的特写 | Seedance prompt #2 |
| 3 | 1 | **真实截图** | SCENE 2 编排卡（om_…db5）| ✅ 已发出 |
| 3 | 5 | After Effects | 多米诺骨牌动效 | 5 张牌 |
| 4 | 3 | **真实截图** | AI Agent 卡（om_…07a / demo_agent_loop）| ✅ 已发出 |
| 4 | 5 | AI 生成 | "信息流网络"风格化 | Seedance prompt #3 |
| 5 | 2-3 | **真实录屏** | 终端运行 demo_movie auto_action | 屏幕录制 |
| 5 | 4 | **真实截图** | SCENE 4 任务卡（om_…2f98）+ 飞书"我负责的"页面 | 需徐悦录屏 |
| 5 | 5 | **真实截图** | data/demo_movie/action_log.jsonl 内容 | 编辑器截图 |
| 6 | 2 | **真实截图** | SCENE 5 hotspot 卡（om_…155）| ✅ 已发出 |
| 6 | 4 | AI 生成 | "顿悟者"特写（参考 Moneyball）| Seedance prompt #4 |
| 7 | 2 | **真实截图** | SCENE 7 站会卡（om_…28e）| ✅ 已发出 |
| 7 | 4 | AI 生成 | 空办公楼夜景，一扇灯亮 | Seedance prompt #5 |
| 8 | 2 | **真实截图** | SCENE 8 审核台卡（om_…ebc）| ✅ 已发出 |
| 9 | 1 | AI 生成 | 父亲接幼儿园电话 | Seedance prompt #6 |
| 9 | 3 | **真实截图** | SCENE 9 交接卡（om_…ffc）| ✅ 已发出 |
| EP | 1 | AI 生成 | 城市日出 + 办公楼亮起 | Seedance prompt #7 |
| EP | 3 | 编辑合成 | mosaic：8 个 ACT 的"红色微光"瞬间 | 用前面截图剪辑 |
| EP | 4 | 文字动画 | 6 个数字飞入 | After Effects |

**统计**：共需要约 22 帧，其中 12 帧（55%）已经是真实截图（不用 AI 生成）。

---

## 🎨 Seedance 2 / Sora / Runway prompt 模板

> 所有 AI 生成镜头建议风格统一：**电影质感 · 暖色调 · 浅景深 · 节制的镜头运动**。
> 共同 negative prompt：`卡通, 过度饱和, 镜头剧烈晃动, 文字字幕, 不真实的人脸, 七彩光斑`

### Prompt #1 — 通勤地铁（ACT 1, 镜头 3）

```
A cinematic medium shot from a first-person POV inside a Tokyo metro train at
6:00 AM. Soft warm sunrise filters through the windows. Empty seats, slight
motion blur from train movement. The viewer's hand holds a smartphone with
"飞书 247 unread" notification visible. Mood: tired but contemplative.
Reference: Studio Ghibli's quiet morning sequences in 'Kiki's Delivery Service'.
Color: muted teal + amber sunlight. Aspect 16:9. 24fps.
```

### Prompt #2 — 放下咖啡的人（ACT 2, 镜头 4）

```
Close-up of a 30-year-old male hand placing a paper coffee cup down on a desk,
exhaling a slow breath of relief. POV-style framing, no face visible, focus on
hand and cup. Background slightly out of focus showing a laptop screen with
calm UI elements. Mood: settled, no longer overwhelmed.
Reference: similar to the moment in 'Her' (2013) when Theodore reads a letter.
Cinematic 35mm film grain. Warm natural daylight.
```

### Prompt #3 — 信息流网络（ACT 4, 镜头 5）

```
Abstract 3D visualization of a memory network. Dozens of glowing data nodes
connected by thin gold light streams, all swirling around a central
"AI" core. Two larger pattern nodes (orange ring) pulse outward and push
a new node into existence. Camera slowly pulls back to reveal the network's
scale. Inspiration: 'Inception' dream layers + Apple keynote tech aesthetic.
Dark navy background, gold/orange highlights only. No text overlays.
```

### Prompt #4 — 顿悟者特写（ACT 6, 镜头 4）

```
Close-up of a project manager's face in front of a monitor, lit by the screen
in low-key office lighting. The expression slowly transforms from focused
confusion to clear realization — a single beat where they understand a
deeper pattern. No words. Reference: Brad Pitt's quiet moments in
'Moneyball'. 50mm lens shallow depth of field. Mood: subtle epiphany.
```

### Prompt #5 — 空办公楼夜景（ACT 7, 镜头 4）

```
Wide aerial shot of a modern office building at 9 PM, city lights below.
Most windows dark, but one floor's window glows warm amber — that's the
floor where the team's project is. Slow horizontal drone movement.
Atmosphere: hushed, end of day, but something is still working.
Cinematic: blade runner soft neon hues, slight rain in the air.
```

### Prompt #6 — 接孩子电话（ACT 9, 镜头 1）

```
Close-up of a phone vibrating on an office desk. Caller ID reads
"幼儿园 王老师" (kindergarten teacher). A male hand reaches for the phone.
Background: end-of-day office, half-empty. Anxiety building.
Reference: solitary domestic moments from Japanese drama
'Kodoku no Gurume'. Natural office light, no music.
```

### Prompt #7 — 城市日出（EPILOGUE, 镜头 1）

```
Slow aerial flyover of a Chinese tier-1 city skyline at 5:30 AM.
Office buildings begin lighting up one floor at a time. A new day
beginning. Soft golden hour light spreading. Reference: Studio
Ghibli 'Castle in the Sky' opening dawn sequence. 4K, 24fps.
Slow zoom in toward one specific building (the team's office).
```

---

## 🎵 音乐 / 音效清单

| 时刻 | 类型 | 描述 |
|------|------|------|
| 全片 | BGM 主线 | 极简钢琴 → 弦乐渐入 → 高潮段加 marimba | 推荐参考：Max Richter "On the Nature of Daylight" 简化版 |
| ACT 0 末 | sting | 单帧"咚"piano hit | |
| ACT 3 | layered chime | 多米诺倒下时 5 个不同音高的 chime | |
| ACT 4 | sting | 卡片"咚"出现的瞬间 | |
| ACT 5 | crescendo | climax，弦乐 swell | |
| ACT 6 | rest | 顿悟瞬间，1 拍静默 | |
| ACT 7 | fade | 钢琴回到最初的单音 | |
| ACT 9 | tension → release | 电话铃声 → 卡片浮现转回温柔 | |
| EPILOGUE | full theme | 完整钢琴主题 + 数据飞入时 build-up | |
| 全片末 | breathing | 只剩呼吸声 | |

工具：[Epidemic Sound](https://www.epidemicsound.com) "emotional minimal" 类。

---

## ⏱️ 总时长 + 节奏

```
00:00 – 00:30   ACT 0    Cold open（钩子）
00:30 – 01:15   ACT 1    诞生
01:15 – 02:00   ACT 2    晨报 ← 真实卡片 1
02:00 – 02:50   ACT 3    编排 ← 真实卡片 2
02:50 – 03:50   ACT 4    AI Agent ← 真实卡片 3
03:50 – 04:50   ACT 5    Climax 真创建任务 ← 真实终端 + 卡片 4
04:50 – 05:30   ACT 6    Pattern hotspot ← 真实卡片 5
05:30 – 06:10   ACT 7    站会 ← 真实卡片 6
06:10 – 06:50   ACT 8    审核台 ← 真实卡片 7
06:50 – 07:30   ACT 9    交接 ← 真实卡片 8
07:30 – 08:30   EPILOGUE  闭环
─────────────────
                总长 8:30
                可裁剪到 5:30（保留 ACT 0/2/4/5/EP 五幕，删 1/3/6/7/8/9 中的若干）
```

---

## 🚦 三档实施方案（按时间预算）

### 方案 A · 完整 8 分钟版（理想，约 10 小时制作）

按上面脚本完整生成 22 帧素材 + 配音 + 剪辑。Seedance 2 大约需要每幕 30 分钟生成 + 选片。

### 方案 B · 5 分钟精华版（推荐，约 4 小时）

只做以下 5 幕 + cold open + epilogue：

```
ACT 0 cold open (30s) + ACT 2 (晨报 1m) + ACT 4 (AI agent 1m) +
ACT 5 (climax 1m30s) + ACT 9 (交接 40s) + EPILOGUE (1m) = 5:40
```

跳过 ACT 1/3/6/7/8。spine 仍然成立（blocker 在 5 幕中各被看一次）。

### 方案 C · 文档版 + 演示动图（兜底，约 1 小时）

不做视频，做一份：

1. 富文本 markdown（即本文档作为剧本）
2. 嵌入 6 张飞书卡片真实截图（已经发出去了）
3. 用 ScreenStudio 录 1 段 60 秒终端 demo（运行 `demo_movie.py --all --feishu`）
4. 用 LICEcap / Kap 录几段关键卡片浏览的 GIF 嵌入
5. 旁白用 ElevenLabs 生成音频，加在 GIF 旁

提交时附 README + 这份脚本 + 截图组 + 1 段 60 秒终端 GIF + 8 个旁白音频片段。

---

## ✅ 录制素材任务清单（你/男友需要做的事）

按优先级：

- [ ] **P0** 录屏：终端运行 `python scripts/demo_movie.py --scene auto_action --feishu` 全过程（ACT 5 climax 必需）
- [ ] **P0** 截图：8 张飞书卡片当前已发的 message_id（按 ACT 顺序整理到一个文件夹）
- [ ] **P0** 录屏：徐悦打开飞书"我负责的"页面，看到自动创建的任务（ACT 5 镜头 4 必需）
- [ ] **P1** 截图：data/demo_movie/action_log.jsonl 文件内容（ACT 5 镜头 5）
- [ ] **P1** 截图：demo_evidence_trace 输出（benchmark report 已经引用过）
- [ ] **P2** 用 Seedance 生成 7 幕 AI 镜头（ACT 1/2/4/6/7/9/EP）
- [ ] **P2** 配音：用 ElevenLabs 生成 9 段中文旁白
- [ ] **P3** 剪辑：用 CapCut/Premiere 串起来 + 加 BGM

---

## 📝 评委导引（脚本评审视角）

如果评委直接读这份脚本（视频还没出来），关注这几点：

| 评分维度 | 哪几幕直接对应 |
|---------|--------------|
| **完整性 50%**（解决什么痛点 + AI 关键作用 + 闭环 + 价值）| ACT 2 / 4 / 5 / 9 |
| **创新性 25%**（AI 一等公民 + Pattern + Hybrid Selector）| ACT 4 / 5 / 6 |
| **技术实现 25%**（架构 + 工程规范 + 稳定性）| ACT 5 镜头 2 / EPILOGUE 数据飞入 |

每一幕末尾的"spine 进度"段落给出了**这一幕证明了哪个评分项**，直接当 reading guide。

---

*Last updated: 2026-05-07. Script for OpenClaw Memory Engine submission.*






