# OpenClaw Memory Engine — Golden Set 评测报告

## 评测环境

- **提取器**: RuleBasedExtractor（关键词规则，非 LLM）
- **去重**: 三层去重（Identity Key → Content Hash → Semantic Similarity）
- **Golden Set**: 30 条样本，12 种场景类型
- **评估日期**: 2026-04-28

## 总体指标

| 指标 | 值 |
|------|----|
| 总样本数 | 30 |
| 通过 | 26 |
| 失败 | 4 |
| 通过率 | 86.7% |

## 按场景类型

| 场景 | 通过/总数 | 通过率 | 说明 |
|------|-----------|--------|------|
| task_assignment | 2/2 | 100% | 含"负责人："关键词的任务分配 |
| owner_change | 2/2 | 100% | 多次负责人变更 + history 正确 |
| decision | 2/2 | 100% | 含"决策/确定"关键词 |
| decision_override | 2/2 | 100% | "改为/不再"识别为决策覆盖 |
| blocker | 3/3 | 100% | 含"阻塞/风险/依赖"关键词 |
| next_action | 3/3 | 100% | 含"下一步/需要/请"关键词 |
| project_goal | 1/1 | 100% | 含"目标"关键词 |
| deferred | 2/2 | 100% | 含"暂缓/暂停"关键词 |
| mixed_scenario | 1/1 | 100% | 4 条消息混合输入，全部正确提取 |
| no_memory | 8/8 | 100% | 闲聊/表情/咨询类消息不误提取 |
| member_status | 0/3 | 0% | **未实现**（V1.6 阶段五支持） |
| deadline | 0/1 | 0% | **未实现**（不在 V1.6 范围） |

## 失败案例分析

### GS-016/017/018: member_status（3 条）

- 场景：成员请假、出差、偏好表达
- 失败原因：RuleBasedExtractor 当前没有 member_status 提取方法
- 计划修复：V1.6 阶段五实现 `_extract_member_status()`

### GS-021: deadline（1 条）

- 场景：DDL 设置
- 失败原因：RuleBasedExtractor 当前没有 deadline 提取
- 计划：V1.6 不做 deadline 解析（日期解析精度不可靠）

## 已知局限

1. **RuleBasedExtractor 覆盖有限**：仅识别含明确关键词的消息。无关键词的隐式语义（如"张三在弄前端"表示负责人）无法提取。
2. **Decision override 无法关联原始决策**：`key="current_decision_override"` 的决策与原始决策的 key 不同，两者并存而非 supersede。需 LLM 级别的语义理解才能正确关联。
3. **Owner 提取含前缀文本**：`"负责人：张三负责用户中心开发"` 提取的 owner 是 `"张三负责用户中心开发"` 而非仅有 `"张三"`。正则精度有限。
4. **当前为 RuleBased 基线**：接入 LLM 后各场景通过率应显著提升，特别是 decision_override 和 no_memory 的边界判断。

## 下一步方向

1. 实现 member_status 提取（V1.6 阶段五）
2. 接入真实 LLM 并重新评估
3. 增加 LLM 提取与 RuleBased 的对比评测