# Feishu Campus Hackathon

飞书校园大赛项目仓库。核心代码在 `openclaw-memory/`。

## 快速开始

```bash
cd openclaw-memory

# 运行所有测试（156 个）
python -m unittest discover -s tests -v

# Golden Set 评测
python scripts/run_golden_eval.py                  # RuleOnly: 122/150 (81.3%)
python scripts/run_golden_eval.py --hybrid         # Hybrid:  127/150 (84.7%)

# 一键演示（Fake LLM）
python scripts/demo_run_example.py
```

## 文档

- [`CLAUDE.md`](CLAUDE.md) — Agent 入口 / 项目概览
- [`openclaw-memory/README.md`](openclaw-memory/README.md) — 完整项目说明
- [`AGENTS.md`](AGENTS.md) — 项目执行约束
- [`opensourse_code/`](opensourse_code/) — 开源项目调研分析