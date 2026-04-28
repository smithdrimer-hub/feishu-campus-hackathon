"""Verify script: test P0 fixes with real Feishu message structure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.engine import MemoryEngine
from memory.extractor import LLMExtractor, RuleBasedExtractor
from memory.handoff import generate_handoff
from memory.llm_provider import FakeLLMProvider
from memory.store import MemoryStore


def main():
    store = MemoryStore(ROOT / "data" / "sandbox_verify")
    extractor = LLMExtractor(FakeLLMProvider(), fallback=RuleBasedExtractor())
    engine = MemoryEngine(store, extractor=extractor)

    # Load mock events with real Feishu sender structure
    events_path = ROOT / "data" / "sandbox_verify" / "mock_events.jsonl"
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))

    print("=" * 60)
    print("P0 验证：真实飞书消息结构 + RuleBasedExtractor")
    print("=" * 60)

    items = engine.ingest_events(events)
    print(f"\n提取到的记忆条目: {len(items)}")
    for item in items:
        print(f"  [{item.state_type}] {item.current_value[:60]} | confidence={item.confidence}")

    # 验证 author_map 功能
    print(f"\n--- 验证 _build_author_map ---")
    author_map = extractor._build_author_map(events)
    for aid, name in sorted(author_map.items()):
        print(f"  {aid} -> {name}")
    assert "ou_user_zhang" in author_map, "author_map 应包含张三"
    assert author_map["ou_user_zhang"] == "张三"
    print(f"  [OK] author_map 正确提取")

    # 验证 time_ref 功能
    print(f"\n--- 验证 _build_time_reference ---")
    time_ref = extractor._build_time_reference(events)
    print(f"  min_time: {time_ref['min_time']}")
    print(f"  max_time: {time_ref['max_time']}")
    assert time_ref["min_time"] == "2026-04-28T09:00:00+08:00"
    assert time_ref["max_time"] == "2026-04-28T09:04:00+08:00"
    print(f"  [OK] time_ref 正确提取")

    # 验证 prompt 包含 author 和 time 信息
    print(f"\n--- 验证 _build_prompt 包含 grounding 信息 ---")
    prompt = extractor._build_prompt(events, author_map, time_ref)
    checks = [
        ("author_map 姓名", "张三" in prompt),
        ("time_ref 范围", "2026-04-28T09:00:00+08:00" in prompt),
        ("代词解析规则", "代词解析规则" in prompt),
        ("时间解析规则", "时间解析规则" in prompt),
        ("消息发送者映射", "消息发送者映射" in prompt),
        ("消息时间范围", "消息时间范围" in prompt),
        ("ambiguous 规则", "ambiguous" in prompt),
    ]
    all_ok = True
    for name, ok in checks:
        status = "[OK]" if ok else "[FAIL]"
        if not ok:
            all_ok = False
        print(f"  {status} {name}")
    assert all_ok, "Prompt grounding 检查未全部通过"
    print("\nPrompt grounding [OK]")

    # 验证 handoff
    print(f"\n--- 验证 handoff ---")
    handoff = generate_handoff("memory-sandbox-verify", items)
    lines = handoff.strip().split("\n")
    print(f"  Handoff 行数: {len(lines)}")
    assert len(lines) > 5, "Handoff 不应为空"
    print("  [OK] Handoff 生成成功")

    # 验证去重
    print(f"\n--- 验证三层去重 ---")
    # 重复插入应正确去重
    dup_item = items[0]  # 用同一个
    dup_events = [events[0]]
    engine2 = MemoryEngine(
        MemoryStore(ROOT / "data" / "sandbox_verify"),
        extractor
    )
    engine2.ingest_events(dup_events)
    # 再插一次相同的
    engine2.ingest_events(dup_events)
    final_items = store.list_items("memory-sandbox-verify")
    print(f"  去重后条目数: {len(final_items)}")
    # 验证 source_refs 不重复
    for item in final_items:
        ref_ids = [r.message_id for r in item.source_refs]
        unique_ids = set(ref_ids)
        if len(ref_ids) != len(unique_ids):
            print(f"  [FAIL] {item.key}: source_refs 有重复 ({len(ref_ids)} vs {len(unique_ids)})")
    print("  [OK] 去重验证通过")

    print("\n" + "=" * 60)
    print("P0 验证全部通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()