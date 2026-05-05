"""Golden set evaluation: measure pass rate for memory extraction.

Supports rule_only (default), hybrid (--hybrid), LLM-only (--llm),
and comparison modes.

V1.10 新增:
- --hybrid: 规则优先 + 必要时 LLM 补充的 hybrid 模式
- --report: 输出结构化 report 到 JSON 文件
- 对比模式下输出详细的"hybrid 修复了哪些 rule-only 失败的案例"

Usage:
    python scripts/run_golden_eval.py                          # RuleOnly (default)
    python scripts/run_golden_eval.py --hybrid                 # Rule-first + LLM supplement
    python scripts/run_golden_eval.py --llm                    # LLM only (OpenAI)
    python scripts/run_golden_eval.py --compare                # All three: rule vs hybrid vs llm
    python scripts/run_golden_eval.py --hybrid --scenario blocker  # Filter
    python scripts/run_golden_eval.py --report report.json     # Save report to file
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
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
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
        extractor_name: "rule" / "hybrid" / "llm"

    Returns:
        dict with: case_id, pass, extracted, expected, errors, scenario_type, extractor
    """
    case_id = sample["case_id"]
    scenario_type = sample["scenario_type"]
    uses_llm = extractor_name in ("hybrid", "llm")
    if uses_llm and "llm_expected_items" in sample:
        expected_items = sample.get("llm_expected_items", [])
        expected_history = sample.get("llm_expected_history", sample.get("expected_history", []))
        should_not_extract = sample.get("llm_should_not_extract", sample.get("should_not_extract", []))
    else:
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
    elif extractor_name == "hybrid":
        provider = get_llm_provider()
        if provider is None:
            # 没有 LLM 配置时 hybrid 降级为纯规则模式
            extractor = HybridExtractor(rule_extractor=RuleBasedExtractor(), llm_extractor=None)
        else:
            llm_extractor = LLMExtractor(provider, fallback=RuleBasedExtractor())
            extractor = HybridExtractor(rule_extractor=RuleBasedExtractor(), llm_extractor=llm_extractor)
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

    # 提取结果摘要（含 state_type 用于 P/R/F1 统计）
    extracted_summary = [
        {"state_type": item.state_type, "owner": item.owner, "value": item.current_value[:40]}
        for item in active
    ]
    extracted_types = list({item.state_type for item in active})
    history_summary = [
        {"state_type": h.state_type, "owner": h.owner, "value": h.current_value[:40]}
        for h in history
    ]

    return {
        "case_id": case_id,
        "scenario_type": scenario_type,
        "pass": len(errors) == 0,
        "extracted": extracted_summary,
        "extracted_types": extracted_types,
        "history": history_summary,
        "extractor": extractor_name,
        "errors": errors,
    }


def _expected_types_from_sample(sample: dict, extractor_name: str) -> set[str]:
    """Extract the set of state_types expected in this sample."""
    if extractor_name in ("hybrid", "llm") and "llm_expected_items" in sample:
        items = sample.get("llm_expected_items", [])
    else:
        items = sample.get("expected_items", [])
    return {it.get("state_type", "") for it in items if it.get("state_type")}


def compute_metrics(results: list[dict], samples: list[dict] | None = None,
                    extractor_name: str = "rule") -> dict:
    """V1.15: 计算总体通过率 + 按 state_type 的 precision/recall/F1。

    TP = 期望提取且实际提取了该类型
    FP = 实际提取了该类型但期望中没有
    FN = 期望提取但实际未提取该类型
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

    # V1.15: 按 state_type 的 precision/recall/F1
    state_stats: dict[str, dict[str, int]] = {}
    for i, r in enumerate(results):
        sample = samples[i] if samples and i < len(samples) else {}
        expected = _expected_types_from_sample(sample, extractor_name)
        actual = set(r.get("extracted_types", []))

        for t in expected | actual:
            if t not in state_stats:
                state_stats[t] = {"TP": 0, "FP": 0, "FN": 0}
            in_exp = t in expected
            in_act = t in actual
            if in_exp and in_act:
                state_stats[t]["TP"] += 1
            elif in_act and not in_exp:
                state_stats[t]["FP"] += 1
            elif in_exp and not in_act:
                state_stats[t]["FN"] += 1

    prf_by_type = {}
    for t, s in sorted(state_stats.items()):
        tp, fp, fn = s["TP"], s["FP"], s["FN"]
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        prf_by_type[t] = {
            "TP": tp, "FP": fp, "FN": fn,
            "precision": f"{p:.2f}", "recall": f"{r:.2f}", "f1": f"{f1:.2f}",
        }

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{passed / total * 100:.1f}%" if total else "N/A",
        "by_type": type_metrics,
        "by_state_type": prf_by_type,
    }


def main():
    parser = argparse.ArgumentParser(description="Run golden set evaluation")
    parser.add_argument("--golden-set", default=str(ROOT / "examples" / "golden_set.jsonl"),
                        help="Path to golden set JSONL file")
    parser.add_argument("--scenario", default=None,
                        help="Filter by scenario type (e.g. 'no_memory', 'decision')")
    parser.add_argument("--verbose", action="store_true", help="Show per-case details")
    parser.add_argument("--hybrid", action="store_true",
                        help="Use HybridExtractor (rule-first + LLM supplement)")
    parser.add_argument("--llm", action="store_true",
                        help="Use LLM extractor (OpenAI) instead of RuleBased")
    parser.add_argument("--compare", action="store_true",
                        help="Run all modes and show comparison: rule_only vs hybrid vs llm")
    parser.add_argument("--report", default=None,
                        help="Save structured report JSON to path")
    args = parser.parse_args()

    golden_path = Path(args.golden_set)
    if not golden_path.exists():
        print(f"Error: golden set not found at {golden_path}")
        sys.exit(1)

    samples = load_golden_set(golden_path)
    if args.scenario:
        samples = [s for s in samples if s.get("scenario_type") == args.scenario]
        print(f"\nFiltered to scenario: {args.scenario} ({len(samples)} cases)")

    if args.compare:
        extractors_to_run = ["rule", "hybrid", "llm"]
    elif args.llm:
        extractors_to_run = ["llm"]
    elif args.hybrid:
        extractors_to_run = ["hybrid"]
    else:
        extractors_to_run = ["rule"]

    all_mode_results: dict[str, list[dict]] = {}

    for ext_name in extractors_to_run:
        ext_labels = {"rule": "RuleOnly", "hybrid": "Hybrid (Rule+LLM)", "llm": "LLM only"}
        ext_label = ext_labels.get(ext_name, ext_name)
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
        metrics = compute_metrics(all_results, samples, ext_name)
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

        # V1.15: 按 state_type 的 precision/recall/F1（仅 RuleOnly）
        prf = metrics.get("by_state_type", {})
        if prf:
            print(f"--- By State Type (Precision / Recall / F1) ---")
            print(f"  {'state_type':20s} {'P':>6s} {'R':>6s} {'F1':>6s}  {'TP':>4s} {'FP':>4s} {'FN':>4s}")
            print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6}  {'-'*4} {'-'*4} {'-'*4}")
            for t, s in sorted(prf.items()):
                print(f"  {t:20s} {s['precision']:>6s} {s['recall']:>6s} {s['f1']:>6s}  {s['TP']:>4d} {s['FP']:>4d} {s['FN']:>4d}")
            print()
            print(f"  (以上指标仅反映 {metrics['total']} 条 Golden Set 上的表现，不代表真实办公场景)")
            print()

        # 列出失败的 case_id（非对比模式）
        if not args.compare:
            if failed_cases:
                print("--- Failures ---")
                for r in failed_cases:
                    print(f"  {r['case_id']} ({r['scenario_type']}): {'; '.join(r['errors'][:2])}")
            else:
                print("All cases passed!")

    # === 对比模式：rule vs hybrid vs llm ===
    if args.compare:
        print(f"\n{'='*60}")
        print("Comparison Report")
        print(f"{'='*60}")

        rule_results = all_mode_results.get("rule", [])
        hybrid_results = all_mode_results.get("hybrid", [])
        llm_results = all_mode_results.get("llm", [])

        rule_pass = sum(1 for r in rule_results if r["pass"])
        hybrid_pass = sum(1 for r in hybrid_results if r["pass"]) if hybrid_results else 0
        llm_pass = sum(1 for r in llm_results if r["pass"]) if llm_results else 0

        hybrid_available = len(hybrid_results) > 0
        llm_available = len(llm_results) > 0 and not any(
            "not configured" in str(r.get("errors", "")) for r in llm_results[:3]
        )

        print(f"\n--- Pass Rate Comparison ---")
        print(f"  RuleOnly:          {rule_pass:3d}/{len(samples):3d} pass ({rule_pass/len(samples)*100:.1f}%)")
        if hybrid_available:
            print(f"  Hybrid (Rule+LLM): {hybrid_pass:3d}/{len(samples):3d} pass ({hybrid_pass/len(samples)*100:.1f}%)")
        if llm_available:
            print(f"  LLM only:          {llm_pass:3d}/{len(samples):3d} pass ({llm_pass/len(samples)*100:.1f}%)")

        # Hybrid vs RuleOnly
        if hybrid_available:
            hybrid_fixed = []
            for r_rule, r_hyb, s in zip(rule_results, hybrid_results, samples):
                if not r_rule["pass"] and r_hyb["pass"]:
                    hybrid_fixed.append(s["case_id"])
            hybrid_regressed = []
            for r_rule, r_hyb, s in zip(rule_results, hybrid_results, samples):
                if r_rule["pass"] and not r_hyb["pass"]:
                    hybrid_regressed.append(s["case_id"])

            print(f"\n--- Hybrid vs RuleOnly ---")
            if hybrid_fixed:
                print(f"  RuleOnly failed but Hybrid PASSED ({len(hybrid_fixed)} cases):")
                sample_map = {s["case_id"]: s for s in samples}
                for cid in hybrid_fixed:
                    s = sample_map[cid]
                    print(f"    {cid} ({s['scenario_type']})")
            else:
                print("  Hybrid did not fix any additional cases (no LLM configured?)")

            if hybrid_regressed:
                print(f"  RuleOnly passed but Hybrid FAILED ({len(hybrid_regressed)} cases):")
                for cid in hybrid_regressed:
                    print(f"    {cid}")

        # LLM vs RuleOnly
        if llm_available:
            llm_fixed = []
            for r_rule, r_llm, s in zip(rule_results, llm_results, samples):
                if not r_rule["pass"] and r_llm["pass"]:
                    llm_fixed.append(s["case_id"])

            print(f"\n--- LLM only vs RuleOnly ---")
            if llm_fixed:
                print(f"  RuleOnly failed but LLM PASSED ({len(llm_fixed)} cases):")
                for cid in llm_fixed:
                    print(f"    {cid}")
            else:
                print("  LLM only did not fix any additional cases.")

        # Summary
        print(f"\n--- Summary ---")
        if hybrid_available:
            total_fixed = len([1 for r_rule, r_hyb in zip(rule_results, hybrid_results) if not r_rule["pass"] and r_hyb["pass"]])
            total_rule_fails = len([r for r in rule_results if not r["pass"]])
            print(f"  Hybrid fixed {total_fixed}/{total_rule_fails} rule failures")
        if llm_available:
            total_llm_fixed = len([1 for r_rule, r_llm in zip(rule_results, llm_results) if not r_rule["pass"] and r_llm["pass"]])
            total_rule_fails = len([r for r in rule_results if not r["pass"]])
            print(f"  LLM fixed {total_llm_fixed}/{total_rule_fails} rule failures")

    # === 结构化 report 输出 ===
    if args.report:
        report = {
            "golden_set_file": str(golden_path),
            "total_cases": len(samples),
            "modes": {},
        }
        for ext_name, results in all_mode_results.items():
            rc = sum(1 for r in results if r["pass"])
            report["modes"][ext_name] = {
                "pass_count": rc,
                "fail_count": len(results) - rc,
                "pass_rate": f"{rc/len(results)*100:.1f}%",
                "failed_cases": [
                    {"case_id": r["case_id"], "scenario_type": r["scenario_type"], "errors": r["errors"]}
                    for r in results if not r["pass"]
                ],
            }

        if args.compare:
            rule_r = all_mode_results.get("rule", [])
            hybrid_r = all_mode_results.get("hybrid", [])
            report["comparison"] = {}
            if hybrid_r:
                report["comparison"]["hybrid_fixed"] = [
                    s["case_id"] for r_rule, r_hyb, s in zip(rule_r, hybrid_r, samples)
                    if not r_rule["pass"] and r_hyb["pass"]
                ]

        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()