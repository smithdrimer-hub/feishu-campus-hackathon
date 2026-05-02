"""Evidence trace debug tool: show which messages produced which memories.

V1.12 新增。用于验证证据链完整性。

用法:
  python scripts/demo_evidence_trace.py --project-id demo
  python scripts/demo_evidence_trace.py --project-id demo --format tree
  python scripts/demo_evidence_trace.py --project-id demo --message-id om_xxx
  python scripts/demo_evidence_trace.py --project-id demo --check-unverified
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evidence chain trace tool")
    parser.add_argument("--project-id", default=None, help="项目 ID 过滤")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="数据目录")
    parser.add_argument("--format", default="tree", choices=["tree", "flat", "summary"],
                        help="输出格式")
    parser.add_argument("--message-id", default=None, help="从指定消息反查记忆")
    parser.add_argument("--check-unverified", action="store_true",
                        help="只显示无证据来源的记忆")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from memory.store import MemoryStore

    store = MemoryStore(Path(args.data_dir))

    if args.message_id:
        _trace_by_message(store, args.message_id, args)
        return

    items = store.list_items(args.project_id)
    history = store.list_history(args.project_id)

    if args.check_unverified:
        _check_unverified(items, history)
        return

    if args.format == "tree":
        _format_tree(items, history, args.project_id)
    elif args.format == "flat":
        _format_flat(items, history)
    else:
        _format_summary(items, history, args.project_id)


def _trace_by_message(store, message_id: str, args) -> None:
    """从一条消息反查所有相关记忆。"""
    items = store.find_items_by_message_id(message_id)
    if args.project_id:
        items = [i for i in items if i.project_id == args.project_id]

    print(f"消息 {message_id} 产生的记忆 ({len(items)} 条):\n")
    for item in items:
        status = "active" if item.status == "active" else f"history(v{item.version})"
        print(f"  [{item.state_type}] {status}")
        print(f"    key: {item.key}")
        print(f"    value: {item.current_value[:80]}")
        print(f"    confidence: {item.confidence}")
        print(f"    所有证据来源:")
        for ref in item.source_refs:
            print(f"      - {ref.type}: {ref.sender_name} @ {ref.created_at}")
            print(f"        excerpt: \"{ref.excerpt[:80]}\"")
            print(f"        url: {ref.source_url}")
        print()


def _check_unverified(items, history) -> None:
    """检查没有证据来源的记忆。"""
    all_items = list(items) + list(history)
    unverified = [i for i in all_items if not i.source_refs]
    if unverified:
        print(f"[unverified] 发现 {len(unverified)} 条无证据来源的记忆:\n")
        for item in unverified:
            print(f"  [{item.state_type}] {item.current_value[:80]}")
    else:
        print("所有记忆都有证据来源。")

    # 检查 source_refs 中字段完整性
    missing_sender = 0
    missing_url = 0
    for item in all_items:
        for ref in item.source_refs:
            if not ref.sender_name:
                missing_sender += 1
            if not ref.source_url:
                missing_url += 1
    if missing_sender:
        print(f"\n[sender] {missing_sender} 条 source_ref 缺少 sender_name")
    if missing_url:
        print(f"\n[url] {missing_url} 条 source_ref 缺少 source_url")


def _format_tree(items, history, project_id) -> None:
    """树形结构显示证据链。"""
    title = f"项目 {project_id}" if project_id else "全部项目"
    print(f"证据链: {title}")
    print(f"活跃记忆: {len(items)} 条 | 历史记忆: {len(history)} 条")
    print()

    for item in items:
        print(f"  [{item.state_type}] {item.current_value[:70]}")
        print(f"    confidence={item.confidence} version={item.version}")
        if not item.source_refs:
            print(f"    [unverified] 无证据来源")
            continue
        for ref in item.source_refs:
            sender = ref.sender_name or "unknown"
            url = ref.source_url or "(无链接)"
            print(f"    ├── {ref.type}: {sender} @ {ref.created_at}")
            print(f"    │   \"{ref.excerpt[:70]}\"")
            print(f"    │   {url}")
        print()

    if history:
        print(f"  历史版本 ({len(history)} 条):")
        for item in history:
            print(f"    [{item.state_type}] {item.current_value[:50]} (v{item.version})")
        print()


def _format_flat(items, history) -> None:
    """平铺格式，适合 grep。"""
    for item in items:
        for ref in item.source_refs:
            print(
                f"{item.project_id}\t{item.state_type}\t{item.key}\t"
                f"{ref.message_id}\t{ref.sender_name}\t{ref.excerpt[:60]}\t"
                f"{ref.source_url}"
            )


def _format_summary(items, history, project_id) -> None:
    """统计摘要。"""
    all_items = list(items) + list(history)
    total_refs = sum(len(i.source_refs) for i in all_items)
    unverified = sum(1 for i in all_items if not i.source_refs)

    types = {}
    for i in all_items:
        types[i.state_type] = types.get(i.state_type, 0) + 1

    source_types = {}
    for i in all_items:
        for ref in i.source_refs:
            source_types[ref.type] = source_types.get(ref.type, 0) + 1

    print(f"项目: {project_id or '全部'}")
    print(f"记忆总数: {len(all_items)} (活跃 {len(items)}, 历史 {len(history)})")
    print(f"证据引用总数: {total_refs}")
    print(f"无证据记忆: {unverified}")
    print(f"\n按状态类型:")
    for t, n in sorted(types.items()):
        print(f"  {t}: {n}")
    print(f"\n按证据来源类型:")
    for t, n in sorted(source_types.items()):
        print(f"  {t}: {n}")

    # V1.12 D6: 文档来源预览
    doc_items = [i for i in all_items
                 if any(ref.type in ("doc", "doc_comment") for ref in i.source_refs)]
    if doc_items:
        print(f"\n── 文档来源记忆 ({len(doc_items)} 条) ──")
        doc_urls = set()
        for item in doc_items:
            for ref in item.source_refs:
                if ref.type in ("doc", "doc_comment") and ref.source_url:
                    doc_urls.add(ref.source_url)
            print(f"  [{item.state_type}] {item.current_value[:60]}")
        if doc_urls:
            print(f"\n  关联文档:")
            for url in sorted(doc_urls):
                print(f"    {url}")


if __name__ == "__main__":
    main()
