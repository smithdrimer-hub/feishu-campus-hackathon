# OpenClaw Memory Engine — Evaluation Report

> **⚠️ 此文档已被替代** ：旧版仅覆盖 30 条样本（V1.6 阶段，2026-04-28），不反映当前能力。
>
> 完整最新评测：**[benchmark_report.md](./benchmark_report.md)**
> 包含 Golden Set 150 条 + 5 真实场景 + 3 模式对比 + AI Agent 端到端 + Pattern Memory 触发率。

## 历史里程碑

| 版本 | 时间 | Golden Set | 通过率 | 备注 |
|------|------|-----------|-------|------|
| V1.6 | 2026-04-28 | 30 条 | 86.7% | 初版规则提取（无 member_status/deadline）|
| V1.11 | 2026-05-02 | 150 条 | 81.3% (rule) / 84.7% (hybrid) | 期望值校准前 |
| V1.18 | 2026-05-06 | 150 条 | 100.0% (rule) / 98.0% (hybrid) | Selector 模式 + 隐式 Prompt |
| **V1.19+** (当前 demo 分支) | 2026-05-07 | 150 条 + 5 场景 + 26 条电影 | 见新报告 | AI Agent 闭环 + Pattern Memory |

## 当前评测命令

```bash
python scripts/run_golden_eval.py             # Golden Set RuleOnly
python scripts/run_golden_eval.py --hybrid    # + DeepSeek
python scripts/run_golden_eval.py --compare   # 三模式对比
python scripts/run_benchmark.py               # 5 真实场景
python scripts/demo_benchmark.py              # 延迟基准
```

详见 **[`benchmark_report.md`](./benchmark_report.md)**。
