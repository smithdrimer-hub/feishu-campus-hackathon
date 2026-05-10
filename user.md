建议把跨源一致性检查放在“记忆写入/更新层”，不要放在提取层频繁打断用户。提取层只生成 candidate memory，写入层再判断它与 existing/confirmed memory 的关系。

核心策略：

新信息不应自动覆盖旧记忆。
只有当新消息明确包含“改为、取消、以新方案为准、延期到、负责人换成”等变更语义时，才将旧记忆标记为 superseded，并写入新 active memory。

如果新信息与旧记忆冲突，但没有明确变更语义，应标记为 conflict，进入待确认队列，而不是直接更新。例如群聊说“下周再做”，但任务 DDL 仍是周五，系统应提示“疑似计划变更”，等待确认。

主动询问只用于高影响冲突：DDL、owner、任务完成状态、关键决策、正式周报/交接包内容。普通状态差异可批量展示，避免频繁打扰办公流程。

建议加入来源权重：任务系统更适合判断任务状态，会议纪要更适合判断正式决策，文档更适合判断方案内容，群聊更适合捕捉即时变化但权威性较低。

最终原则：**默认不打断，关键冲突才询问；默认不覆盖，明确变更才更新。**
我看下来，主流开源项目大概有四种做法，但它们大多是面向“个人 Agent 记忆”，不是企业协作记忆，所以对“计划变更是否需要确认”处理得不如你这个场景细。

**1. Mem0：偏向 update / ADD-only，而不是复杂冲突治理。**
Mem0 有显式 `update` 操作，用 memory_id 修改已有记忆，用于用户偏好变化、事实澄清、补充 metadata 等；它强调“修正或丰富已有 memory”，不是直接 delete+add。新版 OSS 算法又转向 single-pass ADD-only extraction，抽取阶段只 ADD，不做 UPDATE/DELETE，再靠检索、实体链接和后续机制处理记忆使用。这个思路适合降低抽取复杂度，但企业协作里如果只 ADD，很容易留下多个冲突状态。([docs.mem0.ai][1])

**2. Graphiti / Zep：最接近你的需求，用时间有效性处理变更。**
Graphiti 是 temporal knowledge graph，会记录事实随时间变化，并维护 provenance；边上有 `valid_at` / `invalid_at`，新事实使旧事实失效时，不删除旧事实，而是标记旧事实失效，保留历史。这个非常适合你们的 Decision Timeline 和跨源一致性：旧计划不要覆盖掉，而是变成“曾经有效、后来失效”。([help.getzep.com][2])

**3. LangMem：让 memory manager 做“创建、合并、更新、失效”的平衡。**
LangMem 明确提到 collection memory 的难点是要把新信息和旧 belief reconciliate，可能需要删除/失效、更新/整合；它提供后台 memory manager 自动抽取、consolidate、update agent knowledge。这个方向比较通用，但具体冲突规则主要靠开发者指令和系统设计，不是天然适配企业审批。([langchain-ai.github.io][3])

**4. Letta / MemGPT：让 Agent 自己编辑核心记忆。**
Letta 把记忆分成 Core Memory、Recall Memory、Archival Memory；Core Memory 是可编辑 block，Agent 可以用 `core_memory_append` 和 `core_memory_replace` 更新。这个更像“自治 Agent 自己维护上下文”，适合个人助手，但放到企业协作里风险偏高，因为它默认更信任 Agent 自主改写。([letta.com][4])

对你们项目最有参考价值的是 **Graphiti 的 temporal validity + LangMem 的 consolidation**，但要加企业场景的审核机制。也就是：

新信息不要直接覆盖旧记忆；旧记忆保留 `valid_from / valid_to / superseded_by`。
明确变更语义时，旧记忆自动失效，新记忆生效。
没有明确变更语义但冲突时，生成 conflict record，进入待确认队列。
高影响字段，比如 DDL、owner、任务完成状态、关键决策，才主动询问。

一句话：**开源项目大多解决“记忆如何更新”，但你们要解决的是“团队协作事实能否被安全更新”。** 这正好可以变成你们区别于普通 Agent Memory 的亮点。

[1]: https://docs.mem0.ai/core-concepts/memory-operations/update "Update Memory - Mem0"
[2]: https://help.getzep.com/graphiti/getting-started/overview "Overview | Zep Documentation"
[3]: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/ "Core Concepts"
[4]: https://www.letta.com/blog/introducing-the-agent-development-environment "Introducing the Agent Development Environment  | Letta"

