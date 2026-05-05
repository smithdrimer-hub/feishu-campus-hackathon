你这个感觉是对的：**纯关键词搜索确实不靠谱，但“全交给 LLM”也不是成熟方案的终点。**其他比较成熟的 memory / RAG / agent memory 项目，基本都是走“多信号召回 + LLM 抽取/判断 + schema 校验 + 人类审核/可追溯”的路线，不会只靠关键词，也不会无脑全量 LLM。

几个典型做法可以借鉴。

Mem0 新版不是简单关键词，它强调 **ADD-only extraction、hybrid search、entity linking**：也就是新增记忆时用 LLM 做抽取，但检索时同时结合语义、BM25 关键词和实体匹配，而不是只靠一种信号。它们还强调新算法降低抽取延迟，说明真实系统也在意速度和成本，不是把所有东西都直接扔给 LLM 慢慢判断。([Mem0][1])

Letta / MemGPT 系列把记忆分层：长期 archival memory 用语义搜索和标签管理；但它也明确说，频繁变化的当前状态不适合放在 archival memory 里，而应该放在更“当前态”的 memory blocks / scratchpad 里。这个对你们非常重要：你们的项目状态、任务、阻塞、DDL，其实不是普通知识库，而是**高频变化的协作状态**，不能只做向量库搜索。([Letta Docs][2])

Microsoft GraphRAG 的思路也值得借鉴。标准 GraphRAG 会用 LLM 从文本里抽取实体、关系、claim，再生成图结构和社区摘要；但它也提供 FastGraphRAG，用传统 NLP/规则做一部分实体和关系抽取，减少 LLM reasoning 的成本。这说明成熟方案也不是“关键词 vs LLM”二选一，而是根据任务成本做分层。([GitHub上的Microsoft][3])

所以你们现在最应该从“关键词提取”升级成：

**多信号候选生成，而不是关键词判断。**

不要让关键词直接决定“这就是记忆”。关键词只负责“这句话可能有用”。然后再结合：

语义 embedding 相似度：这句话和“任务分配/阻塞/决策/DDL”的典型表达是否接近。

实体识别：有没有人名、模块名、文档名、任务名、时间。

上下文窗口：前后 3-5 条消息是不是在讨论同一个事项。

句式强度：是“确定/最终/就这么定了”，还是“考虑/建议/要不要”。

来源权重：会议纪要、任务系统、文档标题、群聊闲聊，可信度不一样。

历史记忆关系：是否覆盖旧决策、是否和已有 DDL 冲突、是否重复。

这样系统不再是“看到关键词就提取”，而是“多种信号共同投票”。

我会建议你们把架构改成三层：

第一层是 **candidate generation**：规则、关键词、embedding、实体识别一起找“可能有价值的消息”。这一层可以宽一点，宁可多召回。

第二层是 **semantic classification**：只对候选消息做 LLM 判断，判断它到底是不是 decision / blocker / next_step / deadline，并输出结构化 JSON。不是全量消息都跑 LLM。

第三层是 **governance**：高风险、低置信、冲突、覆盖旧记忆的内容进入审核台。低风险高置信内容自动通过。

你现在最头疼的“关键词不靠谱”，真正解决方案不是“全量 LLM”，而是把关键词降级成候选召回信号之一。

对你们项目来说，我建议优先这样改：

把 `next_step` 从规则直接提取改成 **候选 + LLM 判定**。因为你们文档里已经承认 next_step 是精度最低的类型，规则精确率只有 0.65，大约三条里一条误判。 这个类型最值得用 LLM，不然自动创建任务风险很高。

把 `decision` 改成 **规则识别强 confirmed，LLM 判断模糊语义**。比如“确定用 React”规则能处理；“那先这样吧”“算了还是用 X”“不如先本地跑”交给 LLM 判断强度。

把 `blocker` 改成 **规则识别显式阻塞，LLM 识别隐式阻塞**。比如“阻塞：等待设计稿”规则识别；“这个接口没权限，前端现在动不了”交给 LLM。

把检索从关键词搜索升级成 **hybrid search**：BM25/关键词 + embedding + entity filter。比如搜“数据库优化”，不仅找包含“数据库优化”的句子，也找“SQL 查询慢”“索引没建”“后端查询性能”这类语义相关内容。

主办方免费 key 可以用，但我建议只用于 `hybrid` 模式，不要做唯一主流程。因为你们自己的说明里写 LLM 约 65 秒 / 50 条消息，如果全量跑，demo 可能还能忍，真实办公和比赛现场就很容易慢、超时、限流。更稳的是：规则先筛到 10%-30% 的候选，再让 LLM 判断。

可以让 Claude Code 下一步做这个，不要再继续堆关键词：

```text
请规划并实施“多信号候选生成 + LLM 语义判定”的提取升级，不要继续只堆关键词，也不要全量消息都调用 LLM。

目标：
把关键词从“最终判断依据”降级为“候选召回信号”，提升 next_step、decision、blocker 这三类的真实办公可靠性，同时控制 LLM 调用成本和速度。

重点设计：
1. 增加 candidate generation 层：综合关键词、正则、实体、人名、时间、上下文窗口、历史记忆关系，生成候选项。
2. 增加 semantic classifier：只对候选项调用 LLM，输出结构化 JSON，字段包括 type、value、confidence、reason、evidence_span、risk_level。
3. 优先覆盖三类：
   - next_step：降低“请/需要”导致的误判。
   - decision：区分 discussion / preference / tentative / confirmed。
   - blocker：识别“动不了/卡住/没权限/还没好”等隐式阻塞。
4. 增加 hybrid search：关键词/BM25 + embedding + entity filter，不再只做关键词搜索。
5. LLM 结果必须经过 schema 校验，不能直接写入正式记忆。
6. 高风险、低置信、冲突、覆盖旧记忆的结果进入审核台。
7. 增加缓存：message_id + content_hash 相同则不重复调用 LLM。
8. 保留 fast / hybrid / deep 三种模式：
   - fast：规则候选，不调用 LLM。
   - hybrid：只对候选调用 LLM，默认推荐。
   - deep：全量 LLM 审计，仅用于离线评测。
9. 不做无关重构；允许为完成本任务必要调整 extractor、search、config、tests。
10. 必须补无 API 测试，并提供一个小型 demo，展示关键词误判被 LLM 纠正。
```

一句话判断：**你们不要再往“关键词规则库”里硬塞表达了，应该转成“候选召回 + 语义裁判 + 审核兜底”。**这就是从玩具规则系统走向真实办公产品的关键一步。

[1]: https://docs.mem0.ai/migration/oss-v2-to-v3 "Open Source: Migrating to the New Memory Algorithm - Mem0"
[2]: https://docs.letta.com/guides/core-concepts/memory/archival-memory/ "Archival memory | Letta Docs"
[3]: https://microsoft.github.io/graphrag/index/methods/ "Methods - GraphRAG"
