# Demo & Tests Design — 可借鉴模式

来源：基于 mem0, graphiti, agent-memory-server, cognee, openclaw-memory（当前项目）的 Demo 和测试组织方式分析。

---

## 1. Demo 脚本组织（当前项目已有 + 改进建议）

**当前项目结构**（`openclaw-memory/scripts/`）：

```
openclaw-memory/scripts/
├── demo_sync_messages.py    # 从飞书同步消息
├── demo_handoff.py          # 生成交接摘要
├── demo_action_plan.py      # 生成行动计划
└── demo_run_example.py      # 一键运行示例数据
```

**推荐改进**（借鉴 cognee 的 example 目录 + graphiti 的 quickstart）：

```
openclaw-memory/examples/
├── README.md                # Demo 指南
├── 01_quickstart.py         # 5 分钟快速开始（Fake LLM）
├── 02_sync_from_feishu.py   # 从飞书同步真实消息
├── 03_handoff_demo.py       # 中断续办交接
├── 04_action_plan_demo.py   # 行动计划生成
├── 05_multi_scope_demo.py   # 多作用域（doc/chat/meeting）
└── scenarios/
    ├── handoff_scenario_01.jsonl   # 示例数据
    ├── conflict_scenario.jsonl     # 冲突处理场景
    └── temporal_scenario.jsonl     # 时序场景
```

**Quickstart 示例**（借鉴 graphiti 的 `examples/quickstart/`）：

```python
"""
OpenClaw Memory Engine V1.1 — 5 分钟快速开始

运行：
    python examples/01_quickstart.py

输出：
    - Raw events (JSONL)
    - Memory state (JSON)
    - Handoff summary
    - Action plan
"""

import json
from pathlib import Path

from openclaw_memory import MemoryEngine, MemoryStore, RuleBasedExtractor

def main():
    # 1. 准备示例数据
    example_events = [
        {
            "project_id": "demo-001",
            "chat_id": "chat_001",
            "message_id": "msg_001",
            "text": "我们决定下周发布 v1.0 版本",
            "created_at": "2025-04-20T10:00:00Z",
            "author_id": "user_001",
        },
        {
            "project_id": "demo-001",
            "chat_id": "chat_001",
            "message_id": "msg_002",
            "text": "@张三 你负责 API 文档",
            "created_at": "2025-04-20T10:05:00Z",
            "author_id": "user_002",
        },
        {
            "project_id": "demo-001",
            "chat_id": "chat_001",
            "message_id": "msg_003",
            "text": "发布前需要先修复 bug #123",
            "created_at": "2025-04-20T10:10:00Z",
            "author_id": "user_001",
        },
    ]
    
    # 2. 初始化 Engine
    data_dir = Path("./data/demo_run")
    store = MemoryStore(data_dir)
    extractor = RuleBasedExtractor()  # 或使用 LLMExtractor
    engine = MemoryEngine(store, extractor)
    
    # 3. Ingest 事件
    print("=" * 50)
    print("Ingesting events...")
    print("=" * 50)
    active_items = engine.ingest_events(example_events)
    
    # 4. 输出当前记忆状态
    print("\n" + "=" * 50)
    print("Current Memory State:")
    print("=" * 50)
    for item in active_items:
        print(f"  [{item.state_type}] {item.key}: {item.current_value}")
        print(f"    Rationale: {item.rationale}")
        print(f"    Owner: {item.owner}")
        print(f"    Sources: {len(item.source_refs)} refs")
        print()
    
    # 5. 保存结果
    state = store.load_state()
    with open(data_dir / "memory_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print(f"\nResults saved to {data_dir}")
    print("Run 'python examples/03_handoff_demo.py' for handoff summary.")


if __name__ == "__main__":
    main()
```

**借鉴来源**：
- `graphiti/examples/quickstart/` — Quickstart 示例
- `openclaw-memory/examples/handoff_scenario_01.jsonl` — 当前项目的示例数据
- `cognee/examples/` — 多场景示例

---

## 2. 测试组织方式（推荐当前项目 + agent-memory-server）

**当前项目测试结构**（`openclaw-memory/tests/`）：

```
openclaw-memory/tests/
├── test_safety_policy.py        # 安全策略测试
├── test_memory_update.py        # 记忆更新测试
├── test_conflict_resolution.py  # 冲突解决测试
└── __init__.py
```

**推荐扩展**（借鉴 agent-memory-server 的测试覆盖）：

```
openclaw-memory/tests/
├── __init__.py
├── conftest.py                  # Pytest fixture（如果用 pytest）
│
├── # Unit tests
├── test_schema.py               # MemoryItem, SourceRef 数据模型测试
├── test_store.py                # MemoryStore CRUD 测试
├── test_extractor.py            # Extractor 测试
├── test_engine.py               # MemoryEngine 编排测试
├── test_safety_policy.py        # 安全策略测试
│
├── # Integration tests
├── test_lark_adapter.py         # 飞书适配器集成测试
├── test_handoff_integration.py  # 交接摘要集成测试
│
├── # Scenario tests
├── test_conflict_resolution.py  # 冲突解决场景
├── test_temporal_handling.py    # 时序处理场景
├── test_multi_scope.py          # 多作用域场景
│
└── # Fixtures
    └── fixtures/
        ├── sample_events.json
        ├── expected_state.json
        └── feishu_responses.json
```

**测试用例示例**（借鉴 agent-memory-server 的 `tests/`）：

```python
# tests/test_store.py

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw_memory.memory import MemoryStore, MemoryItem, SourceRef


class TestMemoryStore:
    """MemoryStore 单元测试。"""
    
    @pytest.fixture
    def temp_store(self):
        """创建临时 store。"""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            yield store
    
    def test_ensure_files(self, temp_store):
        """测试文件创建。"""
        temp_store.ensure_files()
        
        assert temp_store.raw_events_path.exists()
        assert temp_store.memory_state_path.exists()
    
    def test_append_raw_events(self, temp_store):
        """测试原始事件追加。"""
        events = [
            {"chat_id": "chat_001", "message_id": "msg_001", "text": "Hello"},
            {"chat_id": "chat_001", "message_id": "msg_002", "text": "World"},
        ]
        
        written = temp_store.append_raw_events(events)
        assert written == 2
        
        # 读取验证
        stored = temp_store.read_raw_events()
        assert len(stored) == 2
        assert stored[0]["message_id"] == "msg_001"
    
    def test_upsert_items(self, temp_store):
        """测试记忆插入/更新。"""
        item1 = MemoryItem(
            project_id="proj_001",
            state_type="task",
            key="api-docs",
            current_value="张三负责 API 文档",
            rationale="消息 msg_002 分配任务",
            owner="张三",
            status="active",
            confidence=0.8,
            source_refs=[
                SourceRef(
                    type="message",
                    chat_id="chat_001",
                    message_id="msg_002",
                    excerpt="@张三 你负责 API 文档",
                    created_at="2025-04-20T10:05:00Z",
                )
            ],
        )
        
        items = temp_store.upsert_items([item1])
        assert len(items) == 1
        assert items[0].memory_id == item1.memory_id
        
        # 测试更新（supersede）
        item2 = MemoryItem(
            project_id="proj_001",
            state_type="task",
            key="api-docs",
            current_value="李四负责 API 文档",  # 变更
            rationale="消息 msg_010 重新分配",
            owner="李四",
            status="active",
            confidence=0.9,
            source_refs=[
                SourceRef(
                    type="message",
                    chat_id="chat_001",
                    message_id="msg_010",
                    excerpt="API 文档改由李四负责",
                    created_at="2025-04-21T14:00:00Z",
                )
            ],
        )
        
        items = temp_store.upsert_items([item2])
        assert len(items) == 1
        assert items[0].current_value == "李四负责 API 文档"
        assert items[0].version == 2
        assert item1.memory_id in items[0].supersedes
        
        # 验证历史
        history = temp_store.list_history()
        assert len(history) == 1
        assert history[0].memory_id == item1.memory_id
    
    def test_processed_event_ids(self, temp_store):
        """测试已处理事件 ID 追踪。"""
        events = [
            {"chat_id": "chat_001", "message_id": "msg_001", "text": "Hello"},
            {"chat_id": "chat_001", "message_id": "msg_002", "text": "World"},
        ]
        
        temp_store.append_raw_events(events)
        temp_store.mark_processed(["msg_001"])
        
        assert "msg_001" in temp_store.processed_event_ids()
        assert "msg_002" not in temp_store.processed_event_ids()
```

**冲突解决测试**（当前项目已有 `test_conflict_resolution.py`，借鉴 graphiti 的 bi-temporal）：

```python
# tests/test_conflict_resolution.py

import pytest
from openclaw_memory.memory import MemoryEngine, MemoryStore, RuleBasedExtractor


class TestConflictResolution:
    """冲突解决场景测试。"""
    
    @pytest.fixture
    def engine(self):
        """创建测试 Engine。"""
        with TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir))
            extractor = RuleBasedExtractor()
            engine = MemoryEngine(store, extractor)
            yield engine
    
    def test_conflicting_decisions(self, engine):
        """测试冲突决策处理。"""
        
        # 事件 1：决定使用方案 A
        event1 = {
            "project_id": "proj_001",
            "chat_id": "chat_001",
            "message_id": "msg_001",
            "text": "我们决定使用方案 A",
            "created_at": "2025-04-20T10:00:00Z",
            "author_id": "user_001",
        }
        
        # 事件 2：推翻，改用方案 B
        event2 = {
            "project_id": "proj_001",
            "chat_id": "chat_001",
            "message_id": "msg_002",
            "text": "重新考虑后，改用方案 B",
            "created_at": "2025-04-21T14:00:00Z",
            "author_id": "user_001",
        }
        
        # Ingest 两个事件
        engine.ingest_events([event1])
        engine.ingest_events([event2])
        
        # 验证当前状态
        items = engine.store.list_items(project_id="proj_001")
        decision_items = [i for i in items if i.state_type == "decision"]
        
        assert len(decision_items) == 1
        assert decision_items[0].current_value == "方案 B"
        
        # 验证历史
        history = engine.store.list_history()
        decision_history = [h for h in history if h.state_type == "decision"]
        
        assert len(decision_history) == 1
        assert decision_history[0].current_value == "方案 A"
        assert decision_history[0].memory_id in decision_items[0].supersedes
    
    def test_temporal_query(self, engine):
        """测试时序查询（借鉴 graphiti 的 bi-temporal）。"""
        
        # TODO: 实现 bi-temporal 支持后添加此测试
        # 查询 "2025-04-20T12:00:00Z 时刻的有效决策"
        # 应该返回 "方案 A"
        # 查询 "2025-04-21T18:00:00Z 时刻的有效决策"
        # 应该返回 "方案 B"
        pass
```

**借鉴来源**：
- `openclaw-memory/tests/` — 当前项目的测试结构
- `agent-memory-server/tests/` — 完整的测试覆盖
- `graphiti/examples/` — 场景示例

---

## 3. Fake LLM Provider（当前项目已有，推荐保持）

**当前设计**（`openclaw-memory/src/memory/llm_provider.py`）：

```python
class FakeLLMProvider:
    """Fake LLM provider for demo and testing."""
    
    async def chat_completion(
        self,
        prompt: str,
        response_format: dict | None = None,
    ) -> str:
        """返回预定义的 JSON 响应。"""
        return json.dumps({
            "candidates": [
                {
                    "state_type": "task",
                    "key": "api-docs",
                    "current_value": "张三负责 API 文档",
                    "rationale": "消息 msg_002 分配任务",
                    "owner": "张三",
                    "status": "active",
                    "confidence": 0.8,
                    "source_refs": [
                        {
                            "type": "message",
                            "chat_id": "chat_001",
                            "message_id": "msg_002",
                            "excerpt": "@张三 你负责 API 文档",
                            "created_at": "2025-04-20T10:05:00Z",
                        }
                    ],
                }
            ]
        })
```

**优势**：
- 无外部依赖
- 测试可重复
- 适合 Demo 演示
- 可扩展更多场景响应

**推荐扩展**：支持多场景响应（根据 Prompt 内容返回不同响应）。

---

## 4. 测试清单（V1.1 推荐）

**Unit Tests**（单元测试）：
- [ ] `test_schema.py` — MemoryItem, SourceRef 序列化/反序列化
- [ ] `test_store.py` — CRUD, upsert, history, processed_event_ids
- [ ] `test_extractor.py` — RuleBasedExtractor, LLMExtractor
- [ ] `test_engine.py` — ingest_events, process_new_events
- [ ] `test_safety_policy.py` — 命令分类，确认逻辑

**Integration Tests**（集成测试）：
- [ ] `test_lark_adapter.py` — 飞书 CLI 适配器（需要 mock CLI）
- [ ] `test_handoff.py` — 交接摘要生成
- [ ] `test_action_plan.py` — 行动计划生成

**Scenario Tests**（场景测试）：
- [ ] `test_conflict_resolution.py` — 冲突决策处理
- [ ] `test_temporal_handling.py` — 时序处理（bi-temporal）
- [ ] `test_multi_scope.py` — 多作用域（doc/chat/meeting）
- [ ] `test_dedup.py` — 去重逻辑

---

## 5. 推荐飞书 Memory Engine 的 Demo 路线

**V1.1 最小 Demo**：

1. **准备示例数据**（`examples/scenarios/handoff_scenario_01.jsonl`）
   - 5-10 条飞书消息
   - 包含决策、任务分配、风险、下一步

2. **一键运行**（`python examples/01_quickstart.py`）
   - Fake LLM 提取
   - 输出 memory state
   - 生成交接摘要

3. **飞书同步**（`python examples/02_sync_from_feishu.py --chat-id XXX`）
   - 真实 CLI 拉取消息
   - 提取记忆
   - 生成交接摘要

4. **行动计划**（`python examples/04_action_plan_demo.py`）
   - 读取记忆状态
   - 生成可执行计划

**测试覆盖目标**：
- Unit tests: 80%+ 覆盖率
- Integration tests: 核心流程覆盖
- Scenario tests: 冲突、时序、多作用域

---

## 6. 总结：关键借鉴点

| 项目 | Demo/测试亮点 | 飞书可借鉴 |
|------|--------------|-----------|
| **openclaw-memory** | 当前项目已有良好测试基础，Fake LLM | 保持并扩展 |
| **graphiti** | Quickstart + 多场景示例 | 示例目录组织 |
| **agent-memory-server** | 完整测试覆盖，场景测试 | 测试组织方式 |
| **cognee** | 多示例 Notebook，场景丰富 | 示例多样性 |

**推荐**：
1. **保持当前项目的 Fake LLM 设计** — 适合 Demo 和测试
2. **扩展示例目录** — `examples/` + `examples/scenarios/`
3. **添加 Quickstart** — 5 分钟快速开始脚本
4. **完善测试覆盖** — Unit + Integration + Scenario
