"""Demo script: sync Feishu docs and tasks into memory extraction pipeline.

V1.8: 演示 Memory Engine 从文档和任务数据源提取记忆。

用法:
    python scripts/demo_sync_doc.py --doc-id <doc_id>      # 从文档提取
    python scripts/demo_sync_doc.py --task-query "V1"      # 从任务提取
    python scripts/demo_sync_doc.py --doc-id <id> --task-query "test"  # 两者都做
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import LLMExtractor, RuleBasedExtractor
from memory.handoff import generate_handoff
from memory.llm_provider import OpenAIProvider, FakeLLMProvider
from memory.store import MemoryStore


def get_provider():
    """尝试创建 LLM provider，失败则用 FakeLLMProvider."""
    config_path = ROOT / "config.local.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            llm_cfg = cfg.get("llm", {})
            if llm_cfg.get("provider") == "openai" and llm_cfg.get("api_key"):
                return OpenAIProvider(
                    api_key=llm_cfg["api_key"],
                    base_url=llm_cfg.get("base_url"),
                    model=llm_cfg.get("model", "gpt-4o-mini"),
                )
        except Exception:
            pass
    return FakeLLMProvider()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从飞书文档/任务提取协作记忆")
    parser.add_argument("--doc-id", default=None, help="飞书文档 ID (doc_xxx)")
    parser.add_argument("--task-query", default=None, help="任务搜索关键词")
    parser.add_argument("--project-id", default="doc-demo", help="项目 ID")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "doc_demo"), help="数据目录")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.doc_id and not args.task_query:
        print("请指定 --doc-id 或 --task-query（或两者都指定）")
        sys.exit(1)

    # 初始化
    from adapters.lark_cli_adapter import LarkCliAdapter
    store = MemoryStore(Path(args.data_dir))
    provider = get_provider()
    extractor = LLMExtractor(provider, fallback=RuleBasedExtractor())
    engine = MemoryEngine(store, extractor=extractor, adapter=LarkCliAdapter())

    print(f"\n{'='*60}")
    print(f"Memory Engine — 多数据源记忆提取")
    print(f"{'='*60}")
    print(f"数据目录: {args.data_dir}")
    print(f"提取器: {'LLM (OpenAI)' if isinstance(provider, OpenAIProvider) else 'Fake LLM'}")
    print(f"{'='*60}\n")

    # 文档提取
    if args.doc_id:
        print(f">>> 从文档提取: {args.doc_id}")
        try:
            items = engine.sync_doc(args.doc_id, project_id=args.project_id)
            print(f"    提取到 {len(items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    失败: {e}\n")

    # 任务提取
    if args.task_query:
        print(f">>> 搜索任务: '{args.task_query}'")
        try:
            items = engine.sync_tasks(args.task_query, project_id=args.project_id)
            print(f"    提取到 {len(items)} 条记忆\n")
        except RuntimeError as e:
            print(f"    失败: {e}\n")

    # 输出 handoff
    print(f"{'='*60}")
    print(f"Handoff 摘要")
    print(f"{'='*60}")
    all_items = store.list_items(args.project_id)
    if all_items:
        print(generate_handoff(args.project_id, all_items))
    else:
        print("未提取到任何记忆。\n")

    print(f"数据已写入: {Path(args.data_dir).resolve()}")


if __name__ == "__main__":
    main()