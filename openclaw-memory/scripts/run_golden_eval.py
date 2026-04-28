"""Golden set evaluation: measure precision/recall/F1 for memory extraction.

Supports RuleBasedExtractor (default), LLM extraction (--llm),
and comparison mode (--compare).

Usage:
    python scripts/run_golden_eval.py                        # RuleBased
    python scripts/run_golden_eval.py --llm                  # OpenAI
    python scripts/run_golden_eval.py --compare              # Both
    python scripts/run_golden_eval.py --llm --scenario blocker  # Filter
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.store import MemoryStore


def get_llm_provider():
    """Create OpenAIProvider from config.local.yaml or OPENAI_API_KEY env var.

    优先级：config.local.yaml > 环境变量
    """
    config_path = ROOT / "config.local.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            llm_cfg = config.get("llm", {})
            if llm_cfg.get("provider") == "openai":
                # api_key 优先于 api_key_env
                api_key = llm_cfg.get("api_key") or os.environ.get(llm_cfg.get("api_key_env", "OPENAI_API_KEY"), "")
                if api_key:
                    return OpenAIProvider(
                        api_key=api_key,
                        base_url=llm_cfg.get("base_url"),
                        model=llm_cfg.get("model", "gpt-4o-mini"),
                        temperature=llm_cfg.get("temperature", 0.1),
                        max_tokens=llm_cfg.get("max_tokens", 2000),
                    )
        except Exception:
            pass  # fall through to env var

    # Fallback: 环境变量
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider()

    return None


def load_golden_set(path: Path) -> list[dict]:
    """加载 golden_set.jsonl 并返回样本列表。"""
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        samples.append(json.loads(line))
    return samples


def eval_case(sample: dict, extractor_name: str = "rule") -> dict:
    """评估一个 golden set case。

    Args:
        sample: golden set 样本
        extractor_name: "rule" 或 "llm"

    Returns:
        dict with: case_id, pass, extracted, expected, errors, scenario_type
    """
    case_id = sample["case_id"]
    scenario_type = sample["scenario_type"]
    expected_items = sample.get("expected_items", [])
    expected_history = sample.get("expected_history", [])
    should_not_extract = sample.get("should_not_extract", [])

    if extractor_name == "llm":
        provider = get_llm_provider()
        if provider is None:
            return {
                "case_id": case_id,
                "scenario_type": scenario_type,
                "pass": False,
                "extracted": [],
                "history": [],
                "extractor": "llm",
                "errors": ["LLM not configured. Set OPENAI_API_KEY env var or create config.local.yaml"],
            }
        extractor = LLMExtractor(provider, fallback=RuleBasedExtractor())
    else:
        extractor = RuleBasedExtractor()

    with TemporaryDirectory() as tmpdir:
        store = MemoryStore(Path(tmpdir))
        engine = MemoryEngine(store, extractor)
        engine.ingest_events(sample["input_events"])

        active = store.list_items()
        history = store.list_history()

    errors: list[str] = []

    # 检查 active 条目数量
    if len(expected_items) == 0:
        if len(active) > 0:
            errors.append(f"expected 0 items but got {len(active)}")
    else:
        if len(active) < len(expected_items):
            errors.append(f"expected at least {len(expected_items)} items, got {len(active)}")

    # 检查每条期望项
    for exp in expected_items:
        exp_type = exp.get("state_type")
        exp_owner = exp.get("owner")
        exp_status = exp.get("status")

        matches = [item for item in active if item.state_type == exp_type]
        if not matches:
            errors.append(f"missing expected item with state_type={exp_type}")
            continue

        if exp_owner is not None:
            # owner 匹配：exp_owner 应在 actual_owner 中（因为 RuleBased 可能提取更多文本）
            owner_match = [m for m in matches if m.owner is not None and exp_owner in m.owner]
            if not owner_match:
                errors.append(f"expected {exp_type} with owner containing '{exp_owner}', "
                              f"found owners={[m.owner for m in matches]}")
        if exp_status is not None:
            status_match = [m for m in matches if m.status == exp_status]
            if not status_match:
                errors.append(f"expected {exp_type} with status={exp_status}")

    # 检查 history
    if len(expected_history) > 0:
        for exp_hist in expected_history:
            exp_hist_type = exp_hist.get("state_type")
            exp_hist_value = exp_hist.get("current_value", "")
            matches = [h for h in history
                       if h.state_type == exp_hist_type]
            # 如果 exp_hist_value 较短，检查当前 value 是否包含它
            if exp_hist_value:
                matches = [h for h in matches if exp_hist_value in h.current_value]
            if not matches:
                errors.append(f"expected history with {exp_hist_type} containing '{exp_hist_value}', "
                              f"found types={[h.state_type for h in history]}")

    # 检查不应提取的内容
    for forbid_type in should_not_extract:
        if any(item.state_type == forbid_type for item in active):
            errors.append(f"should not extract state_type={forbid_type} but found in active")

    # 提取结果摘要
    extracted_summary = [
        {"state_type": item.state_type, "owner": item.owner, "value": item.current_value[:40]}
        for item in active
    ]
    history_summary = [
        {"state_type": h.state_type, "owner": h.owner, "value": h.current_value[:40]}
        for h in history
    ]

    return {
        "case_id": case_id,
        "scenario_type": scenario_type,
        "pass": len(errors) == 0,
        "extracted": extracted_summary,
        "history": history_summary,
        "extractor": extractor_name,
        "errors": errors,
    }


def compute_metrics(results: list[dict]) -> dict:
    """计算 precision/recall/F1。

    对每个 case，pass=true 视为正确，false 视为错误。
    """
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    failed = total - passed

    # 按场景类型细分
    by_type: dict[str, list[dict]] = {}
    for r in results:
        by_type.setdefault(r["scenario_type"], []).append(r)

    type_metrics = {}
    for st, cases in sorted(by_type.items()):
        type_passed = sum(1 for c in cases if c["pass"])
        type_metrics[st] = {
            "total": len(cases),
            "passed": type_passed,
            "failed": len(cases) - type_passed,
            "pass_rate": f"{type_passed / len(cases) * 100:.1f}%" if cases else "N/A",
        }

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{passed / total * 100:.1f}%",
        "by_type": type_metrics,
    }


def main():
    parser = argparse.ArgumentParser(description="Run golden set evaluation")
    parser.add_argument("--golden-set", default=str(ROOT / "examples" / "golden_set.jsonl"),
                        help="Path to golden set JSONL file")
    parser.add_argument("--scenario", default=None,
                        help="Filter by scenario type (e.g. 'no_memory', 'decision')")
    parser.add_argument("--verbose", action="store_true", help="Show per-case details")
    parser.add_argument("--llm", action="store_true",
                        help="Use LLM extractor (OpenAI) instead of RuleBased")
    parser.add_argument("--compare", action="store_true",
                        help="Run both RuleBased and LLM, show comparison")
    args = parser.parse_args()

    golden_path = Path(args.golden_set)
    if not golden_path.exists():
        print(f"Error: golden set not found at {golden_path}")
        sys.exit(1)

    samples = load_golden_set(golden_path)
    if args.scenario:
        samples = [s for s in samples if s.get("scenario_type") == args.scenario]
        print(f"\nFiltered to scenario: {args.scenario} ({len(samples)} cases)")

    extractors_to_run = ["llm"] if args.llm else ["rule"]
    if args.compare:
        extractors_to_run = ["rule", "llm"]

    for ext_name in extractors_to_run:
        ext_label = "LLM (OpenAI)" if ext_name == "llm" else "RuleBasedExtractor"
        print(f"\n{'='*60}")
        print(f"Golden Set Evaluation: {len(samples)} cases")
        print(f"{'='*60}")
        print(f"Extractor: {ext_label}")
        print(f"{'='*60}\n")

        all_results = []
        for sample in samples:
            result = eval_case(sample, extractor_name=ext_name)
            all_results.append(result)

        # 输出失败案例详情
        failed_cases = [r for r in all_results if not r["pass"]]
        if failed_cases and args.verbose:
            llm_conf_errors = [r for r in failed_cases if "not configured" in str(r.get("errors", []))]
            if llm_conf_errors:
                print("  [LLM not configured — set OPENAI_API_KEY or create config.local.yaml]")
            else:
                print("--- Failed Cases ---")
                for r in failed_cases:
                    print(f"  [{r['case_id']}] ({r['scenario_type']})")
                    for err in r["errors"]:
                        print(f"    - {err}")
                    print(f"    extracted: {r['extracted']}")
                    print(f"    history:   {r['history']}")
            print()

        # 输出指标
        metrics = compute_metrics(all_results)
        print(f"--- Overall ---")
        print(f"  Total: {metrics['total']}")
        print(f"  Passed: {metrics['passed']}")
        print(f"  Failed: {metrics['failed']}")
        print(f"  Pass Rate: {metrics['pass_rate']}")
        print()

        print(f"--- By Scenario Type ---")
        for st, m in sorted(metrics["by_type"].items()):
            print(f"  {st:25s}: {m['passed']:2d}/{m['total']:2d} pass ({m['pass_rate']})")
        print()

        # 列出失败的 case_id
        if failed_cases and not args.compare:
            print("--- Failures ---")
            for r in failed_cases:
                print(f"  {r['case_id']} ({r['scenario_type']}): {'; '.join(r['errors'])}")
        elif not failed_cases:
            print("All cases passed!")

    # 对比模式输出汇总
    if args.compare:
        print(f"\n{'='*60}")
        print("Comparison Summary")
        print(f"{'='*60}")
        # Re-run both and compare
        rule_results = [eval_case(s, "rule") for s in samples]
        llm_results = [eval_case(s, "llm") for s in samples]
        rule_pass = sum(1 for r in rule_results if r["pass"])
        llm_pass = sum(1 for r in llm_results if r["pass"])
        print(f"  RuleBasedExtractor: {rule_pass}/{len(samples)} pass ({rule_pass/len(samples)*100:.1f}%)")
        print(f"  LLM (OpenAI):       {llm_pass}/{len(samples)} pass ({llm_pass/len(samples)*100:.1f}%)")
        # 显示 LLM 新增通过的 case
        new_passes = []
        for r_rule, r_llm, s in zip(rule_results, llm_results, samples):
            if not r_rule["pass"] and r_llm["pass"]:
                new_passes.append(s["case_id"])
        if new_passes:
            print(f"  Cases LLM fixed: {', '.join(new_passes)}")


if __name__ == "__main__":
    main()