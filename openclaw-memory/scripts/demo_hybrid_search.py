"""Demo script: Hybrid Search (keyword + vector semantic).

Demonstrates the difference between keyword-only and hybrid search.
Uses FakeEmbeddingProvider when no API key is available,
OpenAI text-embedding-3-small when configured.

Usage:
    python scripts/demo_hybrid_search.py
    python scripts/demo_hybrid_search.py --query "风险"
    python scripts/demo_hybrid_search.py --query "进度延迟" --mode hybrid
    python scripts/demo_hybrid_search.py --query "前端" --mode keyword
"""

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.embedding_provider import FakeEmbeddingProvider
from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor
from memory.store import MemoryStore


def _load_embedding_provider():
    """Try to load OpenAI provider from config, fallback to Fake."""
    config_path = ROOT / "config.local.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            emb_config = config.get("embedding", {})
            if emb_config and emb_config.get("provider") == "openai":
                from memory.embedding_provider import OpenAIEmbeddingProvider
                return OpenAIEmbeddingProvider(
                    api_key=emb_config.get("api_key"),
                    api_key_env=emb_config.get("api_key_env", "OPENAI_API_KEY"),
                    base_url=emb_config.get("base_url"),
                    model=emb_config.get("model", "text-embedding-3-small"),
                ), "OpenAI"
        except Exception as e:
            print(f"  [注意] 无法加载 OpenAI Embedding: {e}")

    return FakeEmbeddingProvider(dimension=128), "Fake (演示用)"


def _load_vector_store(data_dir, provider):
    """Try to create VectorStore, return None if chromadb unavailable."""
    try:
        from memory.vector_store import VectorStore
        vs = VectorStore(data_dir=data_dir, embedding_provider=provider, similarity_threshold=0.0)
        if vs.available:
            return vs
    except Exception:
        pass
    return None


DEMO_EVENTS = [
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_001",
        "text": "目标：完成 V1.6 综合优化",
        "created_at": "2026-05-01T10:00:00",
        "sender": {"name": "张三", "id": "ou_zhangsan"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_002",
        "text": "负责人：张三负责后端 API 开发",
        "created_at": "2026-05-01T10:01:00",
        "sender": {"name": "张三", "id": "ou_zhangsan"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_003",
        "text": "决策：使用 React 作为前端框架",
        "created_at": "2026-05-01T10:02:00",
        "sender": {"name": "王五", "id": "ou_wangwu"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_004",
        "text": "阻塞：UI 原型还没出，设计师在忙别的项目",
        "created_at": "2026-05-01T10:03:00",
        "sender": {"name": "李四", "id": "ou_lisi"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_005",
        "text": "下一步：李四跟设计师沟通，争取本周三前拿到设计稿",
        "created_at": "2026-05-01T10:04:00",
        "sender": {"name": "王五", "id": "ou_wangwu"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_006",
        "text": "截止时间：周五下班前必须完成集成测试",
        "created_at": "2026-05-01T10:05:00",
        "sender": {"name": "王五", "id": "ou_wangwu"},
    },
    {
        "project_id": "demo-hybrid",
        "chat_id": "chat_demo",
        "message_id": "msg_007",
        "text": "张三下周一到周三出差，这几天找李四",
        "created_at": "2026-05-01T10:06:00",
        "sender": {"name": "张三", "id": "ou_zhangsan"},
    },
]


def run_demo(query: str, mode: str):
    print("=" * 60)
    print("  OpenClaw Memory Engine — 混合搜索演示")
    print("=" * 60)
    print()

    provider, provider_name = _load_embedding_provider()
    print(f"  Embedding 模型: {provider_name}")

    temp_dir = TemporaryDirectory()
    data_dir = Path(temp_dir.name)

    store = MemoryStore(data_dir / "store")
    vector_store = _load_vector_store(data_dir / "vectors", provider)

    if vector_store:
        print(f"  向量存储: ChromaDB (可用)")
    else:
        print(f"  向量存储: 不可用 (降级为纯关键词)")

    engine = MemoryEngine(store, RuleBasedExtractor(), vector_store=vector_store)

    print(f"\n--- 步骤 1: 导入 {len(DEMO_EVENTS)} 条群聊消息 ---\n")
    for ev in DEMO_EVENTS:
        sender = ev.get("sender", {}).get("name", "?")
        print(f"  {sender}: {ev['text']}")

    engine.ingest_events(DEMO_EVENTS, debounce=False)
    items = store.list_items("demo-hybrid")
    print(f"\n  → 提取到 {len(items)} 条结构化记忆\n")

    print(f'--- 步骤 2: 搜索 "{query}" ---\n')

    if mode in ("keyword", "both"):
        print("  【关键词搜索】")
        kw_results = engine.search(query, project_id="demo-hybrid")
        if not kw_results:
            print("    （无结果）")
        for item, score in kw_results:
            print(f"    [{item.state_type}] {item.current_value}")
            print(f"      → 分数: {score:.1f} | 负责人: {item.owner or '-'}")
        print()

    if mode in ("hybrid", "both"):
        print("  【混合搜索（关键词 + 语义向量）】")
        hybrid_results = engine.search_hybrid(query, project_id="demo-hybrid")
        if not hybrid_results:
            print("    （无结果 — 向量搜索可能不可用）")
        for item, score in hybrid_results:
            print(f"    [{item.state_type}] {item.current_value}")
            print(f"      → 融合分数: {score:.4f} | 负责人: {item.owner or '-'}")
        print()

    if mode == "both" and vector_store:
        kw_ids = {item.memory_id for item, _ in kw_results} if kw_results else set()
        hybrid_ids = {item.memory_id for item, _ in hybrid_results} if hybrid_results else set()
        extra = hybrid_ids - kw_ids
        if extra:
            print(f"  ✦ 向量搜索额外找到 {len(extra)} 条关键词搜索遗漏的记忆")
        else:
            print(f"  ✦ 两种搜索结果相同（关键词已充分覆盖此查询）")

    print()
    print("=" * 60)

    if vector_store:
        stats = vector_store.stats()
        print(f"  索引统计: {stats['memories']} 条记忆向量, {stats['evidence']} 条证据向量")

    temp_dir.cleanup()


KILLER_QUERIES = [
    ("进度风险", "关键词: 0 结果 → 混合搜索找到 blocker + deadline"),
    ("人员变动", "关键词: 0 结果 → 混合搜索找到出差 + 负责人"),
    ("技术选型", "关键词: 0 结果 → 混合搜索找到 React 决策"),
    ("设计稿", "关键词: 2 结果 → 混合搜索额外找到前端框架决策"),
    ("DDL", "关键词: 0 结果 → 混合搜索找到截止时间"),
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hybrid Search Demo")
    parser.add_argument("--query", default="进度风险", help="搜索查询词")
    parser.add_argument("--mode", choices=["keyword", "hybrid", "both"], default="both",
                        help="搜索模式: keyword / hybrid / both")
    parser.add_argument("--all-demos", action="store_true",
                        help="连续运行所有推荐 Demo 查询")
    args = parser.parse_args()

    if args.all_demos:
        print("\n  推荐 Demo 查询列表：")
        for q, desc in KILLER_QUERIES:
            print(f"    「{q}」 — {desc}")
        print()
        for q, _ in KILLER_QUERIES:
            run_demo(q, "both")
            print()
    else:
        run_demo(args.query, args.mode)
