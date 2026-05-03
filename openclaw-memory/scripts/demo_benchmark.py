"""Simple latency benchmark for Memory Engine extraction modes.

用法:
  python scripts/demo_benchmark.py --mode rule
  python scripts/demo_benchmark.py --mode hybrid
  python scripts/demo_benchmark.py --mode all --messages 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Memory Engine latency benchmark")
    parser.add_argument("--mode", default="all", choices=["rule", "hybrid", "all"],
                        help="提取模式")
    parser.add_argument("--messages", type=int, default=20, help="测试消息数")
    parser.add_argument("--runs", type=int, default=3, help="重复次数（取中位数）")
    return parser.parse_args()


def _sample_events(n: int) -> list[dict]:
    """生成 n 条模拟协作消息（含明确和隐式两种模式）。"""
    templates = [
        "目标：完成 V{m} 版本迭代",
        "负责人：张三负责 API 模块开发",
        "决策：采用 React 作为前端框架",
        "阻塞：数据库连接池不够，需要扩容",
        "下一步：编写单元测试覆盖新增代码",
        "DDL 改到下周五",
        "我这周请假，有事找李四",
        "张三在弄前端的接口",
        "考虑使用 Kubernetes 部署",
        "API 还没好，前端动不了",
        "那就先做登录功能吧",
        "改为使用 Vue 替代 React",
        "下周三之前把测试报告发出来",
    ]
    events = []
    for i in range(n):
        tmpl = templates[i % len(templates)]
        events.append({
            "project_id": "bench",
            "chat_id": "chat_bench",
            "message_id": f"msg_bench_{i}",
            "text": tmpl.format(m=i // len(templates)),
            "created_at": f"2026-05-03T10:{i:02d}:00",
            "sender": {"id": f"user_{i%3}", "sender_type": "user",
                        "name": ["张三", "李四", "王五"][i % 3]},
        })
    return events


def main() -> None:
    args = parse_args()

    from memory.engine import MemoryEngine
    from memory.extractor import RuleBasedExtractor, HybridExtractor, LLMExtractor
    from memory.store import MemoryStore

    events = _sample_events(args.messages)
    timings: dict[str, list[float]] = {}

    # ── RuleOnly ──
    if args.mode in ("rule", "all"):
        times = []
        for _ in range(args.runs):
            with TemporaryDirectory() as td:
                store = MemoryStore(Path(td))
                extractor = RuleBasedExtractor()
                engine = MemoryEngine(store, extractor=extractor)
                t0 = time.perf_counter()
                engine.ingest_events(events)
                elapsed = time.perf_counter() - t0
                times.append(elapsed)
        timings["RuleOnly"] = times

    # ── Hybrid ──
    if args.mode in ("hybrid", "all"):
        provider = None
        config_path = ROOT / "config.local.yaml"
        if config_path.exists():
            try:
                import yaml
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                llm_cfg = cfg.get("llm", {})
                if llm_cfg.get("api_key"):
                    from memory.llm_provider import OpenAIProvider
                    provider = OpenAIProvider(
                        api_key=llm_cfg["api_key"],
                        base_url=llm_cfg.get("base_url"),
                        model=llm_cfg.get("model", "gpt-4o-mini"),
                        temperature=llm_cfg.get("temperature", 0),
                    )
            except Exception:
                pass

        if provider is None:
            print("[WARN] LLM 未配置，Hybrid 降级为纯规则模式")
            timings["Hybrid"] = timings.get("RuleOnly", [0.01])
        else:
            times = []
            for _ in range(args.runs):
                with TemporaryDirectory() as td:
                    store = MemoryStore(Path(td))
                    extractor = HybridExtractor(
                        rule_extractor=RuleBasedExtractor(),
                        llm_extractor=LLMExtractor(provider, fallback=RuleBasedExtractor()),
                    )
                    engine = MemoryEngine(store, extractor=extractor)
                    t0 = time.perf_counter()
                    engine.ingest_events(events)
                    elapsed = time.perf_counter() - t0
                    times.append(elapsed)
            timings["Hybrid"] = times

    # ── 输出 ──
    print(f"{'='*50}")
    print(f"Memory Engine Latency Benchmark")
    print(f"消息数: {args.messages} | 重复: {args.runs} 次")
    print(f"{'='*50}")
    for mode, times in timings.items():
        times_sorted = sorted(times)
        median = times_sorted[len(times_sorted) // 2]
        avg = sum(times) / len(times)
        per_msg = median / args.messages * 1000
        print(f"  {mode:12s}: median={median:.3f}s  avg={avg:.3f}s  per_msg={per_msg:.1f}ms")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
