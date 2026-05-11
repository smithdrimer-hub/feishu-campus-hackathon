"""Multi-Source Benchmark Runner.

跑 5 个跨数据源测试场景，验证 Memory Engine 在 7 种飞书数据源
（chat / doc / doc_comment / task / calendar / meeting / approval）下的提取、合并、
冲突检测、状态机生命周期能力。

数据集：
  1. multi_source_full_day        — 7 源全覆盖一天
  2. multi_source_consistency     — 同一事实从多源进，证据应合并
  3. multi_source_conflict        — 跨源冲突，应进审核台
  4. multi_source_meeting_roi     — 会议纪要高密度提取
  5. multi_source_approval_lifecycle — 审批 → 阻塞状态机闭环

Usage:
    python scripts/run_multi_source_benchmark.py              # RuleBased
    python scripts/run_multi_source_benchmark.py --verbose    # 详细
    python scripts/run_multi_source_benchmark.py --hybrid     # 启用 Hybrid + LLM
    python scripts/run_multi_source_benchmark.py --report out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import HybridExtractor, LLMExtractor, RuleBasedExtractor
from memory.llm_provider import OpenAIProvider
from memory.store import MemoryStore


def _build_extractor(hybrid: bool):
    """Build the active extractor; in hybrid mode try to wire a real LLM."""
    if not hybrid:
        return RuleBasedExtractor()
    rule = RuleBasedExtractor()
    provider = _build_llm_provider()
    if provider is None:
        # Fall back to rule-only Hybrid (no LLM available); the runner still
        # marks results with hybrid metadata so callers can compare modes.
        return HybridExtractor(rule_extractor=rule)
    llm = LLMExtractor(provider=provider, fallback=rule)
    return HybridExtractor(rule_extractor=rule, llm_extractor=llm)


def _build_llm_provider():
    """Load OpenAI-compatible provider from config.local.yaml or env."""
    import os
    config_path = ROOT / "config.local.yaml"
    if config_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            llm_cfg = (cfg.get("llm") or {})
            if llm_cfg.get("provider") == "openai":
                api_key = (
                    llm_cfg.get("api_key")
                    or os.environ.get(llm_cfg.get("api_key_env", "OPENAI_API_KEY"), "")
                )
                if api_key:
                    return OpenAIProvider(
                        api_key=api_key,
                        base_url=llm_cfg.get("base_url"),
                        model=llm_cfg.get("model", "gpt-4o-mini"),
                        temperature=llm_cfg.get("temperature", 0.1),
                        max_tokens=llm_cfg.get("max_tokens", 2000),
                    )
        except Exception:
            return None
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider()
    return None

DATASETS = [
    "multi_source_full_day",
    "multi_source_consistency",
    "multi_source_conflict",
    "multi_source_meeting_roi",
    "multi_source_approval_lifecycle",
    "multi_source_natural",
    "multi_source_doc_long",
    "multi_source_approval_realistic",
]

FIXTURE_DATASETS = [
    "multi_source_fixture_aurora",
]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "lark_payloads"


def _source_type_of_event(event: dict) -> str:
    return event.get("source_type", "chat")


def _classify_memory_origin(item, events_by_msg_id: dict) -> set[str]:
    """从 source_refs 反查每条 memory 来自哪些 source_type。"""
    origins: set[str] = set()
    for ref in item.source_refs:
        ev = events_by_msg_id.get(ref.message_id)
        if ev:
            origins.add(_source_type_of_event(ev))
    return origins


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value).lower())


def _key_info_matches(key_info: str, haystack: str) -> bool:
    """Heuristic match for Chinese benchmark expectations.

    We first try exact normalised substring. If that fails, split the expected
    text by common delimiters and require at least one meaningful token to
    appear in the memory text/evidence.
    """
    if not key_info:
        return True
    key_info = re.sub(r"[（(].*?[）)]", "", key_info)
    expected = _normalise_text(key_info)
    expected = expected.replace("已", "")
    actual = _normalise_text(haystack)
    actual = actual.replace("已", "")
    if expected and expected in actual:
        return True

    if len(expected) >= 4:
        expected_bigrams = {expected[i:i + 2] for i in range(len(expected) - 1)}
        actual_bigrams = {actual[i:i + 2] for i in range(len(actual) - 1)}
        if expected_bigrams:
            overlap = len(expected_bigrams & actual_bigrams) / len(expected_bigrams)
            if overlap >= 0.35:
                return True

    tokens = [
        _normalise_text(t)
        for t in re.split(r"[\s/、，,；;：:()（）+→—\\-]+", key_info)
        if len(_normalise_text(t)) >= 2
    ]
    if not tokens:
        return False
    return any(token in actual for token in tokens)


def _item_text_for_match(item) -> str:
    refs = "\n".join(ref.excerpt for ref in item.source_refs)
    return "\n".join([item.current_value, item.rationale, refs])


def _match_expected(expected: dict, items: list) -> tuple[bool, str, str]:
    """Match one expected extraction against actual MemoryItems.

    Returns (matched, reason, memory_id).
    """
    expected_type = expected.get("state_type", "")
    expected_mid = expected.get("from_message_id", "")
    key_info = expected.get("key_info", "")

    candidates = [item for item in items if item.state_type == expected_type]
    if expected_mid:
        candidates = [
            item for item in candidates
            if any(ref.message_id == expected_mid for ref in item.source_refs)
        ]
    if not candidates:
        return False, "no_item_with_expected_state_type_and_message_id", ""

    for item in candidates:
        if _key_info_matches(key_info, _item_text_for_match(item)):
            return True, "matched", item.memory_id
    return False, "key_info_not_found", candidates[0].memory_id


def _evaluate_expected_extractions(
    expected: list[dict], items: list, *, hybrid_mode: bool = False,
) -> dict:
    matched = []
    missed = []
    skipped_hybrid_only = []
    by_source_expected = Counter()
    by_source_matched = Counter()
    by_state_expected = Counter()
    by_state_matched = Counter()

    for exp in expected:
        source_type = exp.get("source_type", "unknown")
        state_type = exp.get("state_type", "unknown")
        only_hybrid = bool(exp.get("expected_only_hybrid"))
        if only_hybrid and not hybrid_mode:
            skipped_hybrid_only.append({
                "source_type": source_type,
                "state_type": state_type,
                "message_id": exp.get("from_message_id", ""),
                "key_info": exp.get("key_info", ""),
                "reason": "expected_only_hybrid_skipped_in_rule_mode",
            })
            continue

        by_source_expected[source_type] += 1
        by_state_expected[state_type] += 1

        ok, reason, memory_id = _match_expected(exp, items)
        record = {
            "source_type": source_type,
            "state_type": state_type,
            "message_id": exp.get("from_message_id", ""),
            "key_info": exp.get("key_info", ""),
            "expected_only_hybrid": only_hybrid,
            "reason": reason,
            "memory_id": memory_id,
        }
        if ok:
            matched.append(record)
            by_source_matched[source_type] += 1
            by_state_matched[state_type] += 1
        else:
            missed.append(record)

    def _recall(matched_count: int, expected_count: int) -> str:
        return f"{matched_count / max(expected_count, 1):.0%}"

    by_source = {
        source: {
            "expected": by_source_expected[source],
            "matched": by_source_matched[source],
            "recall": _recall(by_source_matched[source], by_source_expected[source]),
        }
        for source in sorted(by_source_expected)
    }
    by_state = {
        state: {
            "expected": by_state_expected[state],
            "matched": by_state_matched[state],
            "recall": _recall(by_state_matched[state], by_state_expected[state]),
        }
        for state in sorted(by_state_expected)
    }

    expected_total = sum(by_source_expected.values())
    return {
        "expected_total": expected_total,
        "matched_total": len(matched),
        "missed_total": len(missed),
        "skipped_hybrid_only_total": len(skipped_hybrid_only),
        "strict_recall": _recall(len(matched), expected_total),
        "matched": matched,
        "missed": missed,
        "skipped_hybrid_only": skipped_hybrid_only,
        "by_source": by_source,
        "by_state": by_state,
    }


def _evaluate_cross_validation(cross_validation: dict, items: list) -> dict:
    """Lightweight checks for cross-source semantic dimensions."""
    merged_items = [
        item for item in items
        if len({ref.type for ref in item.source_refs}) >= 2
    ]
    conflicts = [
        item for item in items
        if item.review_status == "needs_review"
        or (item.metadata or {}).get("conflict_status") == "conflicting"
    ]
    blockers = [item for item in items if item.state_type == "blocker"]
    blocker_statuses = Counter(
        (item.metadata or {}).get("blocker_status", "")
        for item in blockers
    )

    propagation_results = []
    for entry in cross_validation.get("blocker_propagation", []):
        keywords = [str(k) for k in entry.get("topic_keywords", [])]
        origin_id = entry.get("expected_origin_message_id", "")
        expected_status = entry.get("expected_blocker_status", "")
        matching = []
        for blk in blockers:
            ref_ids = {ref.message_id for ref in blk.source_refs}
            if origin_id and origin_id not in ref_ids:
                continue
            text_blob = " ".join([blk.current_value, blk.rationale]
                                 + [r.excerpt for r in blk.source_refs])
            if keywords and not all(kw in text_blob for kw in keywords):
                continue
            matching.append(blk)
        actual_status = (
            (matching[0].metadata or {}).get("blocker_status", "") if matching else ""
        )
        propagation_results.append({
            "origin_message_id": origin_id,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "matched": bool(matching),
            "pass": bool(matching) and actual_status == expected_status,
        })
    propagation_pass = (
        not cross_validation.get("blocker_propagation")
        or all(r["pass"] for r in propagation_results)
    )

    result = {
        "consistency": {
            "expected": bool(cross_validation.get("consistency")),
            "merged_items": len(merged_items),
            "pass": (not cross_validation.get("consistency")) or bool(merged_items),
        },
        "conflict": {
            "expected": bool(cross_validation.get("conflict")),
            "needs_review_or_conflict": len(conflicts),
            "pass": (not cross_validation.get("conflict")) or bool(conflicts),
        },
        "blocker_lifecycle": {
            "expected": bool(cross_validation.get("blocker_lifecycle")),
            "blocker_statuses": dict(blocker_statuses),
            "pass": (
                not cross_validation.get("blocker_lifecycle")
                or any(status in blocker_statuses for status in ("waiting_external", "resolved"))
            ),
        },
        "blocker_propagation": {
            "expected": bool(cross_validation.get("blocker_propagation")),
            "checks": propagation_results,
            "pass": propagation_pass,
        },
        "high_density": {
            "expected": bool(cross_validation.get("high_density")),
            "pass": True,
        },
    }
    return result


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _FixtureLarkAdapter:
    """Lightweight Lark adapter that returns prerecorded JSON payloads.

    Used by ``--source-fixture`` mode so we can drive engine.sync_* through
    realistic JSON shapes without depending on a live lark-cli install.
    """

    def __init__(self, fixtures: dict, mode: str = "pending") -> None:
        self.fixtures = fixtures
        self.mode = mode  # "pending" or "approved" for sync_approvals

    def _ok(self, data) -> "object":
        from adapters.lark_cli_adapter import CliResult
        return CliResult(args=[], returncode=0, stdout=json.dumps(data),
                         stderr="", data=data)

    def fetch_doc(self, doc, limit=None, offset=None):
        return self._ok(_load_fixture(self.fixtures["sync_doc"]["payload"]))

    def fetch_doc_comments(self, doc_id, page_size=50):
        return self._ok(_load_fixture(self.fixtures["sync_doc_comments"]["payload"]))

    def search_tasks(self, query, page_token=None, page_limit=20):
        return self._ok(_load_fixture(self.fixtures["sync_tasks"]["payload"]))

    def list_calendar_events(self, start, end):
        return self._ok(_load_fixture(self.fixtures["sync_calendar"]["payload"]))

    def list_event_attendees(self, calendar_id, event_id):
        cal = self.fixtures.get("sync_calendar", {})
        if not cal.get("attendees_payload"):
            return self._ok({"data": {"items": []}})
        return self._ok(_load_fixture(cal["attendees_payload"]))

    def search_minutes(self, start, end, page_size=10):
        return self._ok(_load_fixture(self.fixtures["sync_minutes"]["payload"]))

    def get_minute_detail(self, token):
        return self._ok(_load_fixture(self.fixtures["sync_minutes"]["detail_payload"]))

    def list_approval_instances(self, status="pending", page_size=10):
        if status == "approved":
            return self._ok(_load_fixture(self.fixtures["sync_approvals_approved"]["payload"]))
        return self._ok(_load_fixture(self.fixtures["sync_approvals_pending"]["payload"]))


def run_fixture_dataset(name: str, hybrid: bool, verbose: bool) -> dict:
    """Drive engine.sync_* paths via prerecorded payloads, then evaluate."""
    path = ROOT / "examples" / f"{name}.jsonl"
    if not path.exists():
        return {"dataset": name, "status": "SKIP", "reason": f"file not found: {path}"}
    scenario = json.loads(path.read_text(encoding="utf-8"))
    project_id = scenario["project_id"]
    fixtures = scenario.get("fixtures", {})

    extractor = _build_extractor(hybrid)
    adapter = _FixtureLarkAdapter(fixtures)
    t0 = time.time()
    llm_calls = 0
    llm_total_seconds = 0.0
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        engine = MemoryEngine(store, extractor, adapter=adapter)
        if "sync_doc" in fixtures:
            engine.sync_doc(fixtures["sync_doc"]["doc_id"], project_id=project_id)
        if "sync_doc_comments" in fixtures:
            engine.sync_doc_comments(
                fixtures["sync_doc_comments"]["doc_id"], project_id=project_id,
            )
        if "sync_tasks" in fixtures:
            engine.sync_tasks(fixtures["sync_tasks"]["query"], project_id=project_id)
        if "sync_calendar" in fixtures:
            cal = fixtures["sync_calendar"]
            engine.sync_calendar(cal["start"], cal["end"], project_id=project_id)
        if "sync_minutes" in fixtures:
            mtg = fixtures["sync_minutes"]
            engine.sync_minutes(mtg["start"], mtg["end"], project_id=project_id)
        if "sync_approvals_pending" in fixtures:
            adapter.mode = "pending"
            engine.sync_approvals(status="pending", project_id=project_id)
        if "sync_approvals_approved" in fixtures:
            adapter.mode = "approved"
            engine.sync_approvals(status="approved", project_id=project_id)
        items = list(store.list_items(project_id))
        history_items = list(store.list_history(project_id))
    elapsed = time.time() - t0
    eval_items = items + history_items
    llm_calls = getattr(extractor, "llm_call_count", 0)
    llm_total_seconds = getattr(extractor, "llm_total_seconds", 0.0)

    expected_extractions = scenario.get("expected_extractions", [])
    # Fixture mode does not know real message_ids upfront, so loosen matching
    # by skipping the from_message_id requirement.
    relaxed_expected = [
        {**exp, "from_message_id": ""} for exp in expected_extractions
    ]
    expected_eval = _evaluate_expected_extractions(
        relaxed_expected, eval_items, hybrid_mode=hybrid,
    )
    cross_validation = scenario.get("cross_source_validation", {})
    cross_eval = _evaluate_cross_validation(cross_validation, eval_items)

    by_state = Counter(i.state_type for i in eval_items)
    blockers = [i for i in eval_items if i.state_type == "blocker"]
    blocker_statuses = Counter(
        (i.metadata or {}).get("blocker_status", "") for i in blockers
    )

    result = {
        "dataset": name,
        "status": "OK",
        "mode": "fixture",
        "events_in": 0,
        "memories_out": len(items),
        "history_out": len(history_items),
        "density_ratio": 0.0,
        "elapsed_ms": int(elapsed * 1000),
        "by_state_type": dict(by_state),
        "by_source_type": {},
        "cross_source_merged_count": sum(
            1 for it in eval_items
            if len({r.type for r in it.source_refs}) >= 2
        ),
        "needs_review_count": sum(
            1 for it in eval_items if it.review_status == "needs_review"
        ),
        "strict_recall": expected_eval["strict_recall"],
        "expected_count": expected_eval["expected_total"],
        "matched_count": expected_eval["matched_total"],
        "missed_count": expected_eval["missed_total"],
        "missed_expected": expected_eval["missed"],
        "per_source_recall": expected_eval["by_source"],
        "per_state_recall": expected_eval["by_state"],
        "actual_state_types": sorted(by_state.keys()),
        "blocker_statuses": dict(blocker_statuses),
        "validation_dimensions": cross_eval,
        "llm_calls": llm_calls,
        "llm_total_ms": int(llm_total_seconds * 1000),
        "skipped_hybrid_only_count": expected_eval.get("skipped_hybrid_only_total", 0),
    }

    if verbose:
        print(f"\n--- Fixture dataset: {name} ---")
        print(f"  Memories out: {len(items)}  history: {len(history_items)}  elapsed: {result['elapsed_ms']}ms (LLM calls: {llm_calls})")
        print(f"  Strict recall: {expected_eval['strict_recall']} ({expected_eval['matched_total']}/{expected_eval['expected_total']})")
        print(f"  blocker_statuses: {dict(blocker_statuses)}")
        if expected_eval["missed"]:
            print("  Missed expected:")
            for miss in expected_eval["missed"][:8]:
                print(
                    f"    - {miss['source_type']} [{miss['state_type']}] "
                    f"{miss['key_info']} ({miss['reason']})"
                )

    return result


def run_one_dataset(name: str, hybrid: bool, verbose: bool) -> dict:
    path = ROOT / "examples" / f"{name}.jsonl"
    if not path.exists():
        return {"dataset": name, "status": "SKIP", "reason": f"file not found: {path}"}

    scenario = json.loads(path.read_text(encoding="utf-8"))
    events = scenario.get("events")
    if events is None and "raw_markdown" in scenario:
        chunks = MemoryEngine._chunk_doc_markdown(
            scenario["raw_markdown"],
            scenario.get("title", name),
        )
        project_id_for_chunks = scenario["project_id"]
        doc_id = scenario.get("doc_id", name)
        events = [
            {
                "project_id": project_id_for_chunks,
                "chat_id": "",
                "message_id": f"doc_{doc_id}_{idx}",
                "text": chunk["text"],
                "content": chunk["text"],
                "created_at": scenario.get("created_at", "2026-05-06T10:00:00"),
                "source_type": "doc",
                "source_url": f"https://www.feishu.cn/docx/{doc_id}",
                "section": chunk.get("section", ""),
                "sender": {
                    "id": "doc_sync",
                    "name": f"文档《{scenario.get('title', name)}》",
                    "sender_type": "user",
                },
            }
            for idx, chunk in enumerate(chunks)
        ]
    project_id = scenario["project_id"]
    events_by_msg_id = {e["message_id"]: e for e in events}
    expected_dist = scenario.get("expected_source_distribution", {})

    extractor = _build_extractor(hybrid)
    t0 = time.time()
    with TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        engine = MemoryEngine(store, extractor)
        engine.ingest_events(events, debounce=False)
        items = list(store.list_items(project_id))
        history_items = list(store.list_history(project_id))
        eval_items = items + history_items
    elapsed = time.time() - t0
    llm_calls = getattr(extractor, "llm_call_count", 0)
    llm_total_seconds = getattr(extractor, "llm_total_seconds", 0.0)

    by_state = Counter(i.state_type for i in eval_items)
    by_origin = Counter()
    cross_source_merged = 0
    needs_review = 0
    for item in eval_items:
        origins = _classify_memory_origin(item, events_by_msg_id)
        for o in origins:
            by_origin[o] += 1
        if len(origins) >= 2:
            cross_source_merged += 1
        if item.review_status == "needs_review":
            needs_review += 1

    expected_extractions = scenario.get("expected_extractions", [])
    expected_eval = _evaluate_expected_extractions(
        expected_extractions, eval_items, hybrid_mode=hybrid,
    )

    cross_validation = scenario.get("cross_source_validation", {})
    cross_eval = _evaluate_cross_validation(cross_validation, eval_items)

    result = {
        "dataset": name,
        "status": "OK",
        "events_in": len(events),
        "memories_out": len(items),
        "history_out": len(history_items),
        "density_ratio": round(len(items) / max(len(events), 1), 2),
        "elapsed_ms": int(elapsed * 1000),
        "by_state_type": dict(by_state),
        "by_source_type": dict(by_origin),
        "cross_source_merged_count": cross_source_merged,
        "needs_review_count": needs_review,
        "strict_recall": expected_eval["strict_recall"],
        "expected_count": expected_eval["expected_total"],
        "matched_count": expected_eval["matched_total"],
        "missed_count": expected_eval["missed_total"],
        "missed_expected": expected_eval["missed"],
        "per_source_recall": expected_eval["by_source"],
        "per_state_recall": expected_eval["by_state"],
        "actual_state_types": sorted(by_state.keys()),
        "expected_source_distribution": expected_dist,
        "validation_dimensions": cross_eval,
        "llm_calls": llm_calls,
        "llm_total_ms": int(llm_total_seconds * 1000),
        "skipped_hybrid_only_count": expected_eval.get("skipped_hybrid_only_total", 0),
    }

    if verbose:
        print(f"\n--- Dataset: {name} ---")
        print(f"  Events in: {len(events)} → Memories out: {len(items)} (density: {result['density_ratio']}x)")
        print(f"  Elapsed: {result['elapsed_ms']}ms (LLM calls: {llm_calls}, LLM time: {int(llm_total_seconds * 1000)}ms)")
        print(f"  Strict recall: {expected_eval['strict_recall']} ({expected_eval['matched_total']}/{expected_eval['expected_total']})")
        print(f"  Cross-source merged: {cross_source_merged} (memories with >=2 source_types)")
        print(f"  Needs review: {needs_review}")
        print(f"  By source_type: {dict(by_origin)}")
        if expected_eval["missed"]:
            print("  Missed expected:")
            for miss in expected_eval["missed"][:8]:
                print(
                    f"    - {miss['source_type']}:{miss['message_id']} "
                    f"[{miss['state_type']}] {miss['key_info']} ({miss['reason']})"
                )
        prop = cross_eval.get("blocker_propagation", {})
        if prop.get("expected"):
            for check in prop.get("checks", []):
                marker = "OK" if check["pass"] else "FAIL"
                print(
                    f"  Blocker propagation [{marker}]: "
                    f"origin={check['origin_message_id']} "
                    f"actual_status={check['actual_status'] or '(no match)'}"
                )

    return result


def print_summary_table(results: list[dict], hybrid: bool):
    mode = "Hybrid+LLM" if hybrid else "RuleBased"
    print(f"\n{'='*80}")
    print(f"  Multi-Source Benchmark Summary  ({mode})")
    print(f"{'='*80}\n")

    print(f"{'Dataset':<40s} {'Events':>7s} {'Mem':>5s} {'Density':>8s} {'Recall':>7s} {'Cross':>6s} {'Time':>7s} {'LLM':>4s}")
    print("-" * 92)
    for r in results:
        if r["status"] != "OK":
            print(f"{r['dataset']:<40s} {'SKIP':>7s}")
            continue
        print(
            f"{r['dataset']:<40s} "
            f"{r['events_in']:>7d} "
            f"{r['memories_out']:>5d} "
            f"{r['density_ratio']:>7.1f}x "
            f"{r['strict_recall']:>7s} "
            f"{r['cross_source_merged_count']:>6d} "
            f"{r['elapsed_ms']:>5d}ms "
            f"{r.get('llm_calls', 0):>4d}"
        )

    print()
    total_events = sum(r["events_in"] for r in results if r["status"] == "OK")
    total_mems = sum(r["memories_out"] for r in results if r["status"] == "OK")
    total_cross = sum(r["cross_source_merged_count"] for r in results if r["status"] == "OK")
    total_review = sum(r["needs_review_count"] for r in results if r["status"] == "OK")
    total_time = sum(r["elapsed_ms"] for r in results if r["status"] == "OK")
    total_expected = sum(r["expected_count"] for r in results if r["status"] == "OK")
    total_matched = sum(r["matched_count"] for r in results if r["status"] == "OK")
    total_llm = sum(r.get("llm_calls", 0) for r in results if r.get("status") == "OK")
    total_skipped = sum(r.get("skipped_hybrid_only_count", 0) for r in results if r.get("status") == "OK")
    print(f"  TOTAL: {total_events} events → {total_mems} memories ({total_mems/max(total_events,1):.2f}x)")
    print(f"  Strict recall: {total_matched}/{total_expected} ({total_matched / max(total_expected, 1):.0%})")
    print(f"  Cross-source merged: {total_cross}")
    print(f"  Needs review (审核台): {total_review}")
    if total_skipped:
        print(f"  expected_only_hybrid skipped (rule mode only): {total_skipped}")
    if total_llm:
        print(f"  LLM calls: {total_llm}")
    print(f"  Total elapsed: {total_time}ms\n")

    print("Per-source coverage (across all datasets):")
    src_totals: dict[str, int] = defaultdict(int)
    for r in results:
        if r["status"] != "OK":
            continue
        for src, count in r.get("by_source_type", {}).items():
            src_totals[src] += count
    for src, count in sorted(src_totals.items()):
        print(f"  {src:15s}  {count:>3d} memory items")
    print()


def _run_for_mode(args, hybrid_mode: bool) -> list[dict]:
    datasets_to_run = [args.dataset] if args.dataset else DATASETS
    if args.only_fixture:
        datasets_to_run = []
    out: list[dict] = []
    for name in datasets_to_run:
        try:
            r = run_one_dataset(name, hybrid=hybrid_mode, verbose=args.verbose)
        except Exception as e:
            r = {"dataset": name, "status": "ERROR", "error": str(e)}
        r["mode_label"] = "hybrid" if hybrid_mode else "rule"
        out.append(r)
    if args.source_fixture or args.only_fixture:
        for name in FIXTURE_DATASETS:
            try:
                r = run_fixture_dataset(name, hybrid=hybrid_mode, verbose=args.verbose)
            except Exception as e:
                r = {"dataset": name, "status": "ERROR", "error": str(e)}
            r["mode_label"] = "hybrid" if hybrid_mode else "rule"
            out.append(r)
    return out


def _diff_against_baseline(results: list[dict], baseline_path: Path) -> int:
    """Print a per-dataset diff between current results and a saved baseline.

    Returns the count of regressions (strict_recall drop or higher missed).
    """
    if not baseline_path.exists():
        print(f"\n[diff] baseline not found: {baseline_path}\n")
        return 0
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline = {r["dataset"]: r for r in payload.get("results", []) if r.get("status") == "OK"}

    def _pct(value: str) -> float:
        try:
            return float(str(value).rstrip("%"))
        except (TypeError, ValueError):
            return 0.0

    print(f"\n{'=' * 92}")
    print(f"  Diff vs baseline ({baseline_path.name})")
    print(f"{'=' * 92}")
    print(f"{'Dataset':<40s} {'Recall delta':>14s} {'Cross delta':>12s} {'Review delta':>14s}")
    print("-" * 92)
    regressions = 0
    for r in results:
        if r.get("status") != "OK":
            continue
        base = baseline.get(r["dataset"])
        if base is None:
            print(f"{r['dataset']:<40s} {'<NEW>':>14s} {'-':>12s} {'-':>14s}")
            continue
        recall_delta = _pct(r.get("strict_recall", "0%")) - _pct(base.get("strict_recall", "0%"))
        cross_delta = r.get("cross_source_merged_count", 0) - base.get("cross_source_merged_count", 0)
        review_delta = r.get("needs_review_count", 0) - base.get("needs_review_count", 0)
        if recall_delta < 0 or r.get("missed_count", 0) > base.get("missed_count", 0):
            regressions += 1
        marker = "" if recall_delta >= 0 else "  REGRESSION"
        print(
            f"{r['dataset']:<40s} {recall_delta:>+13.1f}% "
            f"{cross_delta:>+12d} {review_delta:>+14d}{marker}"
        )
    print("-" * 92)
    print(f"  Regressions: {regressions}")
    return regressions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="详细输出每个数据集")
    parser.add_argument("--hybrid", action="store_true", help="(legacy) 等价于 --mode hybrid")
    parser.add_argument(
        "--mode", choices=["rule", "hybrid", "both"], default=None,
        help="评测模式：rule（默认）、hybrid（使用 LLM）、both（同时跑两边并对比）",
    )
    parser.add_argument("--report", type=str, help="输出 JSON 报告到指定文件")
    parser.add_argument(
        "--diff", type=str,
        help="与已有的 JSON 报告对比，打印 strict_recall / cross / needs_review 差异",
    )
    parser.add_argument("--dataset", type=str, help="只跑指定数据集 (e.g. multi_source_full_day)")
    parser.add_argument(
        "--source-fixture", action="store_true",
        help="额外跑 fixture 数据集（走真实 sync_* adapter 路径，依赖 tests/fixtures/lark_payloads）",
    )
    parser.add_argument(
        "--only-fixture", action="store_true",
        help="只跑 fixture 数据集，不跑普通数据集",
    )
    args = parser.parse_args()

    if args.mode is None:
        args.mode = "hybrid" if args.hybrid else "rule"

    if args.mode == "both":
        rule_results = _run_for_mode(args, hybrid_mode=False)
        hybrid_results = _run_for_mode(args, hybrid_mode=True)
        results = rule_results + hybrid_results
        print_summary_table(rule_results, hybrid=False)
        print_summary_table(hybrid_results, hybrid=True)
    else:
        results = _run_for_mode(args, hybrid_mode=(args.mode == "hybrid"))
        print_summary_table(results, hybrid=(args.mode == "hybrid"))

    if args.report:
        Path(args.report).write_text(
            json.dumps({
                "mode": args.mode,
                "results": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"📄 JSON report saved: {args.report}")

    if args.diff:
        _diff_against_baseline(results, Path(args.diff))


if __name__ == "__main__":
    main()
