"""Memory engine that turns raw events into current structured state.

V1.5 改进：
- Debounce 提取：trailing-edge debounce 避免频繁触发 LLM
- 最后处理时间追踪：用于 debounce 判断
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from memory.extractor import BaseExtractor, RuleBasedExtractor
from memory.schema import MemoryItem, raw_event_id, utc_now_iso
from memory.store import MemoryStore


class MemoryEngine:
    """Coordinate storage, extraction, and state updates for V1.

    V1.5 改进：
    - Debounce 提取：coalescing 合并避免频繁触发 LLM 提取
      注意：当前实现是安全的 coalescing（新消息会 append 到文件，下次非 debounce 时处理）
      不是真正的异步 trailing-edge debounce（无后台调度器）
    - 最后处理时间追踪：用于 debounce 判断
    """

    def __init__(
        self,
        store: MemoryStore,
        extractor: BaseExtractor | None = None,
        debounce_seconds: int = 60,
        adapter: Any = None,
    ) -> None:
        """Create an engine with a store and extractor.

        Args:
            store: 记忆存储器实例
            extractor: 记忆提取器实例，默认为 RuleBasedExtractor
            debounce_seconds: Debounce 时间窗口（秒），默认 60 秒
            adapter: V1.8: LarkCliAdapter 实例，用于 sync_doc/sync_tasks
        """
        self.store = store
        self.extractor = extractor or RuleBasedExtractor()
        self.debounce_seconds = debounce_seconds
        self.adapter = adapter

        # 记录每个 project 的最后处理时间（内存缓存，重启后重置）
        # 格式：{project_id: datetime}
        self._last_process_time: dict[str, datetime] = {}

    def ingest_events(self, events: list[dict], debounce: bool = True) -> list[MemoryItem]:
        """Append raw events, process new events with optional debounce.

        V1.5 改进：支持 debounce 选项，避免频繁触发 LLM。

        Args:
            events: 原始事件列表
            debounce: 是否启用 debounce（默认 True）

        Returns:
            活跃记忆项列表
        """
        self.store.append_raw_events(events)
        return self.process_new_events(debounce=debounce)

    def process_new_events(
        self, project_id: str | None = None, debounce: bool = True
    ) -> list[MemoryItem]:
        """Extract memory from unprocessed raw events with coalescing debounce.

        V1.5 改进：Coalescing debounce 逻辑
        - 检查是否在 debounce 窗口内
        - 如果在窗口内，跳过处理（事件已 append 到文件，等待下次非 debounce 时处理）
        - 如果不在窗口内，正常处理所有未处理事件

        注意：当前实现是安全的 coalescing，不是真正的异步 trailing-edge debounce。
        debounce 跳过的事件不会被标记为 processed，因此后续非 debounce 调用仍会处理。
        生产环境需要后台调度器来确保 debounce 窗口结束后自动触发处理。

        Args:
            project_id: 项目 ID，用于过滤事件
            debounce: 是否启用 debounce（默认 True）

        Returns:
            活跃记忆项列表
        """
        # === Debounce 检查 ===
        if debounce:
            should_process, delay_reason = self._should_process_now(project_id)

            if not should_process:
                # 在 debounce 窗口内，跳过处理
                # 新事件已通过 append_raw_events 写入文件且未被标记为 processed
                # 等待下次非 debounce 或窗口结束后再处理
                return self.store.list_items(project_id)

        # === 正常处理流程 ===
        processed = set(self.store.processed_event_ids())
        events = [
            event
            for event in self.store.read_raw_events(project_id)
            if raw_event_id(event) not in processed
        ]

        if not events:
            return self.store.list_items(project_id)

        # 使用 LLM 或规则提取器提取记忆
        new_items = self.extractor.extract(events)
        processed_ids = [raw_event_id(event) for event in events]

        if not new_items:
            # 没有提取到记忆，标记为已处理
            self.store.mark_processed(processed_ids)
            return self.store.list_items(project_id)

        # 更新记忆存储（包含三层去重逻辑）
        result = self.store.upsert_items(new_items, processed_ids)

        # === 更新最后处理时间 ===
        self._set_last_process_time(project_id, datetime.now())

        return result

    def sync_doc(self, doc_id: str, project_id: str | None = None) -> list[MemoryItem]:
        """V1.8: 从飞书文档拉取内容并提取记忆。

        依赖 LarkCliAdapter.fetch_doc（只读操作）。
        将文档 markdown 作为"事件"注入 extractor，走原有提取链路。

        Args:
            doc_id: 飞书文档 ID（doc_xxx 或 URL token）
            project_id: 项目 ID，默认从文档 ID 自动生成

        Returns:
            提取出的活跃记忆项列表
        """
        if self.adapter is None:
            raise RuntimeError("sync_doc requires adapter (LarkCliAdapter) to be set on engine")

        result = self.adapter.fetch_doc(doc_id)
        if result.returncode != 0:
            print(f"sync_doc: lark-cli failed for {doc_id}: {result.stderr or result.stdout}")
            return []

        doc_data = result.data or {}
        inner = doc_data.get("data", doc_data)
        markdown = inner.get("markdown", "")
        title = inner.get("title", "未命名文档")
        resolved_project = project_id or f"doc_{doc_id}"

        doc_event = {
            "project_id": resolved_project,
            "chat_id": doc_id,
            "message_id": doc_id,
            "text": f"【文档更新】{title}\n{markdown[:2000]}",
            "content": markdown,
            "created_at": utc_now_iso(),
            "source_type": "doc",
            "sender": {"id": "doc_sync", "sender_type": "system"},
        }

        print(f"sync_doc: 已读取文档 '{title}' ({len(markdown)} 字符)")
        return self.ingest_events([doc_event])

    def sync_tasks(self, query: str, project_id: str = "default") -> list[MemoryItem]:
        """V1.8: 从飞书拉取任务并提取记忆。

        依赖 LarkCliAdapter.search_tasks（只读操作）。
        每个任务作为独立事件注入 extractor。

        Args:
            query: 任务搜索关键词
            project_id: 项目 ID

        Returns:
            提取出的活跃记忆项列表
        """
        if self.adapter is None:
            raise RuntimeError("sync_tasks requires adapter (LarkCliAdapter) to be set on engine")

        result = self.adapter.search_tasks(query)
        if result.returncode != 0:
            print(f"sync_tasks: lark-cli failed: {result.stderr or result.stdout}")
            return []

        payload = result.data or {}
        tasks = payload.get("data", {}).get("items", []) or []
        if not tasks:
            print("sync_tasks: 未找到匹配的任务")
            return []

        task_events = []
        for task in tasks:
            summary = task.get("summary", "")
            status = task.get("status", "unknown")
            description = task.get("description", "")
            task_id = task.get("guid", "")
            created = task.get("created_at", utc_now_iso())

            task_events.append({
                "project_id": project_id,
                "chat_id": "task_source",
                "message_id": task_id,
                "text": f"【任务】{summary} - 状态：{status}\n{description[:500]}",
                "content": description,
                "created_at": created,
                "source_type": "task",
                "sender": {"id": "task_sync", "sender_type": "system"},
            })

        print(f"sync_tasks: 已拉取 {len(tasks)} 个任务")
        return self.ingest_events(task_events)

    def search(
        self,
        query: str,
        project_id: str | None = None,
        as_of: str | None = None,
        top_k: int = 10,
    ) -> list[tuple[MemoryItem, float]]:
        """V1.9: 基于关键词搜索已提取的记忆。

        委托给 MemoryStore.search_keywords() 实现。
        不涉及向量/语义搜索，仅在当前活跃记忆的文本字段中做关键词匹配。

        Args:
            query: 搜索关键词
            project_id: 可选的项目 ID 过滤
            as_of: 可选的时间点过滤
            top_k: 最大结果数

        Returns:
            (MemoryItem, score) 列表，按相关度降序
        """
        return self.store.search_keywords(
            query=query,
            project_id=project_id,
            as_of=as_of,
            top_k=top_k,
        )

    def _should_process_now(self, project_id: str | None) -> tuple[bool, str]:
        """Check if extraction should proceed based on debounce timing.

        V1.5 新增：Coalescing debounce 判断逻辑。
        当距上次处理不足 debounce_seconds 时跳过处理。
        安全保证：跳过的事件不会被标记为 processed，后续一定可处理。

        Args:
            project_id: 项目 ID（用于区分不同作用域的 debounce）

        Returns:
            (是否应该立即处理，原因说明)
        """
        last_time = self._get_last_process_time(project_id)

        if last_time is None:
            # 从未处理过，立即处理
            return True, "首次处理"

        elapsed = (datetime.now() - last_time).total_seconds()

        if elapsed < self.debounce_seconds:
            # 在 debounce 窗口内，延迟处理
            remaining = self.debounce_seconds - elapsed
            return False, f"在 debounce 窗口内，剩余{remaining:.0f}秒"

        # 超过 debounce 窗口，可以处理
        return True, f"距离上次处理已{elapsed:.0f}秒"

    def _get_last_process_time(self, project_id: str | None) -> datetime | None:
        """Get the last process time for a project.

        Args:
            project_id: 项目 ID

        Returns:
            最后处理时间，如果没有则返回 None
        """
        return self._last_process_time.get(project_id)

    def _set_last_process_time(self, project_id: str | None, time: datetime) -> None:
        """Set the last process time for a project.

        Args:
            project_id: 项目 ID
            time: 处理时间
        """
        self._last_process_time[project_id] = time
