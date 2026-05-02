"""Memory engine that turns raw events into current structured state.

V1.5 改进：
- Debounce 提取：trailing-edge debounce 避免频繁触发 LLM
- 最后处理时间追踪：用于 debounce 判断
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
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

        # 记录每个 project 的最后处理时间
        # V1.11: 持久化到文件，重启后不丢失
        self._last_process_time: dict[str, datetime] = {}
        self._load_debounce_state()

        # V1.12: 身份感知 + 群聊绑定
        self._identity: dict[str, str] = {}
        self._chat_project_map: dict[str, str] = {}
        self._load_identity_state()

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

        # 更新记忆存储（包含四层去重逻辑）
        result = self.store.upsert_items(new_items, processed_ids)

        # V1.12: 审计日志
        user = self._identity.get("open_id", "anonymous")
        for item in new_items:
            self.store.audit_log(
                user, "write",
                project_id=item.project_id,
                state_type=item.state_type,
                detail=f"extracted: {item.current_value[:80]}",
            )

        # === 更新最后处理时间 ===
        self._set_last_process_time(project_id, datetime.now())

        return result

    def sync_doc(self, doc_id: str, project_id: str | None = None) -> list[MemoryItem]:
        """V1.12: 从飞书文档拉取内容并提取记忆。

        修复：sender_type 改为 doc_sync（不再被 LLM 跳过）、
        message_id 使用 content hash 允许重取更新后的文档、
        text 完整不截断、chat_id 留空、debounce=False。
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
        # 飞书 API 返回的 markdown 中换行是转义的 \\n，需转为真实换行
        markdown = markdown.replace("\\n", "\n")
        resolved_project = project_id or f"doc_{doc_id}"

        # V1.12: 将文档按章节/表格/列表拆分为多个事件
        import hashlib
        content_hash = hashlib.sha1(markdown.encode("utf-8")).hexdigest()[:12]
        chunks = self._chunk_doc_markdown(markdown, title)

        doc_events = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"doc_{doc_id}_{content_hash}_{i}"
            doc_events.append({
                "project_id": resolved_project,
                "chat_id": "",
                "message_id": chunk_id,
                "text": chunk["text"],
                "content": chunk["text"],
                "created_at": utc_now_iso(),
                "source_type": "doc",
                "section": chunk["section"],
                "sender": {"id": "doc_sync", "sender_type": "doc_sync"},
            })

        print(f"sync_doc: 已读取文档 '{title}' ({len(markdown)} 字符) → {len(doc_events)} 个事件")
        return self.ingest_events(doc_events, debounce=False)

    @staticmethod
    def _chunk_doc_markdown(markdown: str, title: str) -> list[dict[str, str]]:
        """V1.12: 将文档 markdown 按章节/表格/列表拆分为独立事件。

        拆分策略：
        1. 按 ## 标题分节（每个节成为独立事件，含节标题上下文）
        2. 节内检测 markdown 表格，每行拆为独立事件
        3. 节内检测列表项，含协作关键词的列表项拆为独立事件
        """
        import re
        chunks: list[dict[str, str]] = []

        # ── V1.12 DOC-1: 检测嵌入对象 ──
        embed_patterns = [
            (r"<sheet\s+token=[\"']([^\"']+)[\"'][^>]*>", "sheet"),
            (r"<bitable\s+token=[\"']([^\"']+)[\"'][^>]*>", "bitable"),
            (r"<cite\s+[^>]*file-type=[\"'](sheets|bitable)[\"'][^>]*token=[\"']([^\"']+)[\"'][^>]*>", "cite"),
        ]
        for pattern, embed_type in embed_patterns:
            for match in re.finditer(pattern, markdown):
                token = match.group(1) if embed_type != "cite" else match.group(2)
                real_type = match.group(1) if embed_type == "cite" else embed_type
                chunks.append({
                    "section": "嵌入对象",
                    "text": (
                        f"【文档】{title} › 嵌入{real_type}\n"
                        f"本文档包含嵌入的飞书{real_type}（token: {token}）。"
                        f"请使用 lark-cli {real_type} 命令查看详细数据。"
                    ),
                })

        # 按 ## 标题拆分（处理文档开头无 ## 的情况）
        raw_sections = re.split(r"\n(?=## )", markdown)
        sections = []
        for sec in raw_sections:
            sec = sec.strip()
            if sec:
                sections.append(sec)

        if not sections:
            chunks.append({"section": title, "text": f"【文档】{title}\n{markdown}"})
            return chunks

        for section in sections:
            header_match = re.match(r"## (.+?)(?:\n|$)", section)
            if header_match:
                section_title = header_match.group(1).strip()
                body_start = header_match.end()
                section_body = section[body_start:].strip()
            else:
                section_title = title
                section_body = section

            if not section_body:
                continue

            # ── 检测并拆分表格 ──
            table_lines = re.findall(r"^\|(.+)\|$", section_body, re.MULTILINE)
            if len(table_lines) >= 2:
                # 跳过纯分隔符行（如 |---|---|）
                data_lines = []
                headers = []
                for i, line in enumerate(table_lines):
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    # 检测是否为分隔符行（全是 -, :, 空格）
                    if all(re.match(r"^[-:\s]+$", c) for c in cells):
                        if i == 0:
                            headers = cells  # 无意义，跳过
                        continue
                    if i == 0 or (not headers and i == 0):
                        headers = cells
                    else:
                        data_lines.append((i, cells))

                if not headers and data_lines:
                    # 无头表格：每行作为独立事件，内容按列拼接
                    for _, cells in data_lines:
                        row_text = " | ".join(cells)
                        chunks.append({
                            "section": section_title,
                            "text": f"【文档】{title} › {section_title}\n{row_text}",
                        })
                elif headers:
                    for _, cells in data_lines:
                        pairs = []
                        for j, cell in enumerate(cells):
                            h = headers[j] if j < len(headers) else f"col{j}"
                            pairs.append(f"{h}: {cell}")
                        row_text = " ".join(pairs)
                        chunks.append({
                            "section": section_title,
                            "text": f"【文档】{title} › {section_title}\n{row_text}",
                        })
                continue

            # ── 拆分列表项 ──
            collab_kws = ("负责", "目标", "决策", "阻塞", "下一步", "截止", "DDL",
                         "延期", "休假", "请假", "owner", "goal", "todo")
            list_pattern = re.compile(r"^[-*]\s+(.+)$|^\d+\.\s+(.+)$", re.MULTILINE)
            list_items = list_pattern.findall(section_body)
            collab_items = []
            other_items = []
            for item in list_items:
                item_text = (item[0] or item[1]).strip()
                if any(kw in item_text for kw in collab_kws):
                    collab_items.append(item_text)
                else:
                    other_items.append(item_text)

            if collab_items:
                for item_text in collab_items:
                    chunks.append({
                        "section": section_title,
                        "text": f"【文档】{title} › {section_title}\n{item_text}",
                    })
                # 非协作列表项合并回正文
                remaining_lines = [
                    l for l in section_body.split("\n")
                    if not list_pattern.match(l) or any(
                        (l.strip()[2:] if l.startswith("- ") else l.strip()[3:]).strip() == it
                        for it in other_items
                    )
                ]
                remaining = "\n".join(remaining_lines).strip()
                remaining = re.sub(r"\n\s*\n\s*\n", "\n\n", remaining).strip()
                if remaining and len(remaining) > 20:
                    chunks.append({
                        "section": section_title,
                        "text": f"【文档】{title} › {section_title}\n{remaining}",
                    })
                continue

            # ── 默认：整个节作为一个事件 ──
            chunks.append({
                "section": section_title,
                "text": f"【文档】{title} › {section_title}\n{section_body}",
            })

        # V1.12: 长文档限制事件数，超过时合并短节
        max_chunks = 20
        if len(chunks) > max_chunks:
            # 保留前 max_chunks-1 个，其余合并为一个摘要事件
            overflow = chunks[max_chunks - 1:]
            chunks = chunks[:max_chunks - 1]
            merged_text = "\n\n".join(
                f"## {c['section']}\n{c['text'].split(chr(10), 1)[-1] if chr(10) in c['text'] else c['text']}"
                for c in overflow
            )
            chunks.append({
                "section": f"{title}（续）",
                "text": f"【文档】{title}\n{merged_text[:2000]}",
            })

        if not chunks:
            chunks.append({"section": title, "text": f"【文档】{title}\n{markdown}"})

        return chunks

    def sync_doc_comments(self, doc_id: str, project_id: str | None = None) -> list[MemoryItem]:
        """V1.12: 同步文档评论并提取记忆。

        评论中的协作信号（负责人变更、决策讨论、阻塞反馈）也纳入提取。
        """
        if self.adapter is None:
            raise RuntimeError("sync_doc_comments requires adapter")

        result = self.adapter.fetch_doc_comments(doc_id)
        if result.returncode != 0:
            print(f"sync_doc_comments: lark-cli failed: {result.stderr or result.stdout}")
            return []

        data = result.data or {}
        items = (data.get("data", data) or {}).get("items", []) or []
        if not items:
            return []

        resolved_project = project_id or f"doc_{doc_id}"
        comment_events = []
        for comment in items:
            comment_id = comment.get("comment_id", "")
            user_id = comment.get("user_id", "")
            replies = comment.get("reply_list", {}).get("replies", []) or []

            # 收集所有回复文本
            reply_texts = []
            for reply in replies:
                elements = reply.get("content", {}).get("elements", []) or []
                text = " ".join(
                    e.get("text_run", {}).get("text", "")
                    for e in elements
                    if isinstance(e, dict)
                ).strip()
                if text:
                    reply_texts.append(text)

            if reply_texts:
                comment_text = " | ".join(reply_texts)
                comment_events.append({
                    "project_id": resolved_project,
                    "chat_id": "",
                    "message_id": f"comment_{comment_id}",
                    "text": f"【文档评论】{comment_text}",
                    "content": comment_text,
                    "created_at": utc_now_iso(),
                    "source_type": "doc_comment",
                    "sender": {"id": user_id, "sender_type": "user"},
                })

        if not comment_events:
            return []

        print(f"sync_doc_comments: {len(items)} 条评论 → {len(comment_events)} 个事件")
        return self.ingest_events(comment_events, debounce=False)

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
                "sender": {"id": "task_sync", "sender_type": "task_sync"},
            })

        print(f"sync_tasks: 已拉取 {len(tasks)} 个任务")
        return self.ingest_events(task_events, debounce=False)

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

    @property
    def _debounce_state_path(self) -> Path:
        """Path to the debounce persistence file."""
        return Path(self.store.data_dir) / "debounce_state.json"

    def _load_debounce_state(self) -> None:
        """V1.11: 从文件加载 debounce 状态，重启后恢复最后处理时间。"""
        import json
        path = self._debounce_state_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for project_id, iso_str in data.items():
                try:
                    self._last_process_time[project_id] = datetime.fromisoformat(iso_str)
                except (ValueError, TypeError):
                    pass
        except (json.JSONDecodeError, OSError):
            pass

    def _save_debounce_state(self) -> None:
        """V1.11: 将 debounce 状态持久化到文件。"""
        import json
        data = {
            pid: dt.isoformat()
            for pid, dt in self._last_process_time.items()
        }
        self._debounce_state_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8",
        )

    # ── V1.12 身份感知 + 群聊绑定 ────────────────────────────────

    @property
    def _identity_path(self) -> Path:
        return Path(self.store.data_dir) / "identity.json"

    @property
    def _chat_map_path(self) -> Path:
        return Path(self.store.data_dir) / "chat_project_map.json"

    def _load_identity_state(self) -> None:
        """V1.12: 加载身份信息和群聊-项目绑定。"""
        import json
        for attr, path in [("_identity", self._identity_path),
                           ("_chat_project_map", self._chat_map_path)]:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                setattr(self, attr, data)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_identity_state(self) -> None:
        """V1.12: 持久化身份和绑定信息。"""
        import json
        self._identity_path.write_text(
            json.dumps(self._identity, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._chat_map_path.write_text(
            json.dumps(self._chat_project_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_identity(self, open_id: str = "", name: str = "",
                     tenant_key: str = "") -> None:
        """V1.12: 设置当前用户身份。自动从 lark-cli doctor 获取。"""
        self._identity = {
            "open_id": open_id,
            "name": name,
            "tenant_key": tenant_key,
        }
        self._save_identity_state()

    def get_identity(self) -> dict[str, str]:
        """V1.12: 获取当前用户身份。"""
        return dict(self._identity)

    def bind_chat_to_project(self, chat_id: str, project_id: str) -> None:
        """V1.12: 绑定群聊到项目。后续可通过 chat_id 自动识别 project_id。"""
        self._chat_project_map[chat_id] = project_id
        self._save_identity_state()

    def get_project_for_chat(self, chat_id: str) -> str | None:
        """V1.12: 根据 chat_id 查找绑定的 project_id。"""
        return self._chat_project_map.get(chat_id)

    def resolve_owner_open_id(self, owner_name: str) -> str | None:
        """V1.12: 通过飞书通讯录解析 owner 姓名为 open_id。

        用于在 SourceRef 中同时保存 name + open_id，
        后续可基于 open_id 做权限过滤。
        """
        if not self.adapter or not owner_name:
            return None
        result = self.adapter.search_contact(owner_name)
        if result.returncode != 0:
            return None
        data = result.data or {}
        users = data.get("data", {}).get("items", []) or data.get("items", []) or []
        if users:
            return users[0].get("open_id", "")
        return None

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

        V1.11: 同时持久化到文件，重启后不丢失。

        Args:
            project_id: 项目 ID
            time: 处理时间
        """
        self._last_process_time[project_id] = time
        self._save_debounce_state()
