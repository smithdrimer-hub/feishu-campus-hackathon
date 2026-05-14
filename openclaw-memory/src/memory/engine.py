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
        vector_store: Any = None,
    ) -> None:
        """Create an engine with a store and extractor.

        Args:
            store: 记忆存储器实例
            extractor: 记忆提取器实例，默认为 RuleBasedExtractor
            debounce_seconds: Debounce 时间窗口（秒），默认 60 秒
            adapter: V1.8: LarkCliAdapter 实例，用于 sync_doc/sync_tasks
            vector_store: VectorStore 实例，用于语义搜索（None = 禁用向量搜索）
        """
        self.store = store
        self.extractor = extractor or RuleBasedExtractor()
        self.debounce_seconds = debounce_seconds
        self.adapter = adapter
        self.vector_store = vector_store

        # 记录每个 project 的最后处理时间
        # V1.11: 持久化到文件，重启后不丢失
        self._last_process_time: dict[str, datetime] = {}
        self._load_debounce_state()

        # V1.12: 身份感知 + 群聊绑定
        self._identity: dict[str, str] = {}
        self._chat_project_map: dict[str, str] = {}
        self._load_identity_state()
        # V1.15: 累积 diff（多次 sync 操作合并，供触发引擎使用）
        self.last_diff: dict[str, list] = {
            "created": [], "updated": [], "unchanged": [], "conflicts": [],
        }
        # BUG-5 fix: 维护节流计数器（避免高频同步时重复扫描全量 items）
        self._maintenance_counter: int = 0
        self._last_maintenance_time: datetime | None = None

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

        V1.14: Also stores the upsert diff in self.last_diff for trigger engine.

        Args:
            project_id: 项目 ID，用于过滤事件
            debounce: 是否启用 debounce（默认 True）

        Returns:
            活跃记忆项列表
        """
        # V1.18: 每次 pipeline 运行时重置，防止长期运行内存无界增长
        self.last_diff = {"created": [], "updated": [], "unchanged": [], "conflicts": []}

        if debounce:
            should_process, delay_reason = self._should_process_now(project_id)
            if not should_process:
                return self.store.list_items(project_id)

        processed = set(self.store.processed_event_ids())
        events = [
            event
            for event in self.store.read_raw_events(project_id)
            if raw_event_id(event) not in processed
        ]

        if not events:
            return self.store.list_items(project_id)

        # V1.19 P1-A fix: engine 内部统一 normalize，确保非文本消息不走 pipeline 也生成占位
        events = self._normalize_events(events)

        new_items = self.extractor.extract(events)
        new_items = self._sanitize_items(new_items)
        processed_ids = [raw_event_id(event) for event in events]

        if not new_items:
            self.store.mark_processed(processed_ids)
            return self.store.list_items(project_id)

        result, diff = self.store.upsert_items(new_items, processed_ids)
        for key in ("created", "updated", "unchanged", "conflicts"):
            self.last_diff[key].extend(diff.get(key, []))

        if self.vector_store and getattr(self.vector_store, "available", False):
            for item in result:
                self.vector_store.index_item(item)

        user = self._identity.get("open_id", "anonymous")
        for item in new_items:
            self.store.audit_log(
                user, "write",
                project_id=item.project_id,
                state_type=item.state_type,
                detail=f"extracted: {item.current_value[:80]}",
            )

        self._set_last_process_time(project_id, datetime.now())

        # V1.19: 轻量维护（每 5 次处理或距上次 > 10 分钟才执行，避免高频同步时重复扫描）
        self._maintenance_counter += 1
        now = datetime.now()
        if (self._maintenance_counter >= 5
                or self._last_maintenance_time is None
                or (now - self._last_maintenance_time).total_seconds() > 600):
            self.store.maintenance()
            self._maintenance_counter = 0
            self._last_maintenance_time = now

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
        total_length = inner.get("total_length", len(markdown))
        markdown = markdown.replace("\\\\n", "\n").replace("\\n", "\n")
        doc_url = f"https://www.feishu.cn/docx/{doc_id}"
        resolved_project = project_id or f"doc_{doc_id}"

        # D5: 更新检测——对比上次 hash
        import hashlib, json as _json
        content_hash = hashlib.sha1(markdown.encode("utf-8")).hexdigest()[:12]
        hash_path = self.store.data_dir / f"doc_hash_{doc_id}.json"
        last_hash = ""
        if hash_path.exists():
            try:
                last_hash = _json.loads(hash_path.read_text(encoding="utf-8")).get("hash", "")
            except Exception:
                pass
        if last_hash == content_hash:
            print(f"sync_doc: '{title}' 未变化，跳过")
            return []
        hash_path.write_text(_json.dumps({"hash": content_hash, "title": title},
                                         ensure_ascii=False), encoding="utf-8")

        # D6: 长文档分页拉取
        if total_length > len(markdown) and total_length > 1000:
            print(f"sync_doc: 文档较长 ({total_length} 字符)，尝试分页...")
            offset = len(markdown)
            while offset < total_length:
                r2 = self.adapter.fetch_doc(doc_id, offset=offset)
                if r2.returncode != 0:
                    break
                d2 = r2.data or {}
                i2 = d2.get("data", d2)
                more_md = i2.get("markdown", "")
                if not more_md:
                    break
                more_md = more_md.replace("\\\\n", "\n").replace("\\n", "\n")
                markdown += "\n" + more_md
                offset += len(more_md)
        chunks = self._chunk_doc_markdown(markdown, title)

        doc_events = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"doc_{doc_id}_{content_hash}_{i}"
            hints = {}
            for k in ("detected_type", "detected_owner", "extraction_hint"):
                if chunk.get(k):
                    hints[k] = chunk[k]
            doc_events.append({
                "project_id": resolved_project,
                "chat_id": "",
                "message_id": chunk_id,
                "text": chunk["text"],
                "content": chunk["text"],
                "created_at": utc_now_iso(),
                "source_type": "doc",
                "source_url": doc_url,
                "section": chunk["section"],
                "sender": {"id": "doc_sync", "sender_type": "user",
                           "name": f"文档《{title}》"},
                "extraction_hints": hints,
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
                    # FEAT-2: 检测列头中的结构化字段
                    owner_cols = []
                    ddl_cols = []
                    for j, h in enumerate(headers):
                        hl = h.lower()
                        if any(kw in hl for kw in ("负责人", "owner", "assignee", "经办人", "责任人")):
                            owner_cols.append(j)
                        if any(kw in hl for kw in ("ddl", "截止", "deadline", "期限", "到期")):
                            ddl_cols.append(j)
                    for _, cells in data_lines:
                        pairs = []
                        detected_owner = ""
                        detected_type = ""
                        extraction_hint = ""
                        for j, cell in enumerate(cells):
                            h = headers[j] if j < len(headers) else f"col{j}"
                            pairs.append(f"{h}: {cell}")
                            if j in owner_cols and cell.strip():
                                detected_owner = cell.strip()
                                detected_type = "owner"
                                extraction_hint = f"owner={detected_owner}"
                            if j in ddl_cols and cell.strip():
                                if not detected_type:
                                    detected_type = "deadline"
                                extraction_hint = (
                                    f"{extraction_hint};deadline={cell.strip()}"
                                    if extraction_hint else f"deadline={cell.strip()}"
                                )
                        row_text = " ".join(pairs)
                        chunks.append({
                            "section": section_title,
                            "text": f"【文档】{title} › {section_title}\n{row_text}",
                            "detected_type": detected_type,
                            "detected_owner": detected_owner,
                            "extraction_hint": extraction_hint,
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
                    # FEAT-2: 从列表项中检测结构化信号
                    detected_owner = ""
                    detected_type = ""
                    extraction_hint = ""
                    owner_match = re.search(
                        r"(?:负责人|负责|owner)[：:\s]*(.{1,20})", item_text)
                    ddl_match = re.search(
                        r"(?:DDL|截止|deadline|期限|到期)[：:\s]*(.{1,30})", item_text,
                        re.IGNORECASE)
                    if owner_match:
                        detected_owner = owner_match.group(1).strip()
                        detected_type = "owner"
                        extraction_hint = f"owner={detected_owner}"
                    if ddl_match:
                        if not detected_type:
                            detected_type = "deadline"
                        ddl_val = ddl_match.group(1).strip()
                        extraction_hint = (
                            f"{extraction_hint};deadline={ddl_val}"
                            if extraction_hint else f"deadline={ddl_val}"
                        )
                    chunks.append({
                        "section": section_title,
                        "text": f"【文档】{title} › {section_title}\n{item_text}",
                        "detected_type": detected_type,
                        "detected_owner": detected_owner,
                        "extraction_hint": extraction_hint,
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

        # V1.17 P1: 分页拉取所有任务
        all_tasks = []
        page_token = None
        pages = 0
        while pages < 10:  # max 10 pages
            result = self.adapter.search_tasks(query, page_token=page_token)
            if result.returncode != 0:
                break
            payload = result.data or {}
            page_tasks = payload.get("data", {}).get("items", []) or []
            if not page_tasks:
                break
            all_tasks.extend(page_tasks)
            pages += 1
            has_more = payload.get("data", {}).get("has_more", False)
            page_token = payload.get("data", {}).get("page_token", "")
            if not has_more or not page_token:
                break

        if not all_tasks:
            print("sync_tasks: 未找到匹配的任务")
            return []

        tasks = all_tasks

        task_events = []
        for task in tasks:
            summary = task.get("summary", "")
            status = task.get("status", "unknown")
            description = task.get("description", "")
            task_id = task.get("guid", "")
            created = task.get("created_at", utc_now_iso())
            assignee = task.get("assignee", task.get("assignee_name", ""))
            due = task.get("due_at", task.get("due", ""))

            # V1.17 P1: 从描述中解析结构化字段
            import re as _re
            if not assignee:
                m = _re.search(r"负责人[：:]\s*(.{1,20})", description or "")
                if m: assignee = m.group(1).strip()
            if not due:
                m = _re.search(r"DDL[：:]\s*(.{1,30})", description or "")
                if m: due = m.group(1).strip()

            # 构建提取友好文本
            text_parts = [f"【任务】{summary}"]
            if assignee and "负责人" not in (description or ""):
                text_parts.append(f"负责人：{assignee}")
            if status:
                text_parts.append(f"状态：{status}")
            if due:
                text_parts.append(due)
            if description:
                text_parts.append(description[:500])
            text = "\n".join(text_parts)

            task_url = task.get("url", "")
            task_events.append({
                "project_id": project_id,
                "chat_id": "task_source",
                "message_id": task_id,
                "text": text,
                "content": description,
                "created_at": created,
                "source_type": "task",
                "source_url": task_url,
                "sender": {"id": "task_sync", "sender_type": "user",
                           "name": assignee or "任务负责人"},
            })
            # V1.17 T2: 有截止日期时额外生成 deadline 事件
            if due and status not in ("completed", "done"):
                task_events.append({
                    "project_id": project_id,
                    "chat_id": "task_source",
                    "message_id": f"{task_id}_deadline",
                    "text": f"【任务截止】{summary}\n截止日期：{due}",
                    "content": f"DDL：{due}",
                    "created_at": created,
                    "source_type": "task",
                    "source_url": task_url,
                    "sender": {"id": "task_sync", "sender_type": "user",
                               "name": assignee or "任务负责人"},
                })

        print(f"sync_tasks: 已拉取 {len(tasks)} 个任务")
        return self.ingest_events(task_events, debounce=False)

    def sync_calendar(self, start: str, end: str,
                       project_id: str = "default") -> list[MemoryItem]:
        """V1.13: 同步飞书日历日程并提取记忆。"""
        if self.adapter is None:
            raise RuntimeError("sync_calendar requires adapter")
        result = self.adapter.list_calendar_events(start, end)
        if result.returncode != 0:
            print(f"sync_calendar: failed: {result.stderr or result.stdout}")
            return []
        raw = result.data or {}
        if isinstance(raw, list):
            items = raw
        else:
            inner = raw.get("data", raw)
            items = inner if isinstance(inner, list) else inner.get("items", []) or []
        if not items:
            print("sync_calendar: 未找到日程")
            return []
        events = []
        for ev in items:
            # C1: 组织者信息
            organizer = ev.get("event_organizer", {}) or {}
            org_name = organizer.get("display_name", "")
            org_id = organizer.get("user_id", "")

            text = f"【日程】{ev.get('summary', '')}"
            if org_name:
                text += f"\n负责人：{org_name}"
            desc = ev.get("description", "")
            if desc:
                text += f"\n{desc[:500]}"
            cal_link = ev.get("app_link", "")

            # C5: 视频会议信息
            vchat = ev.get("vchat", {}) or {}
            if isinstance(vchat, dict) and vchat.get("meeting_url"):
                text += f"\n视频会议：{vchat.get('meeting_url', '')}"

            # C4: 参会人
            calendar_id = ev.get("organizer_calendar_id", "primary")
            event_id = ev.get("event_id", "")
            attendee_names = []
            if event_id:
                try:
                    att_result = self.adapter.list_event_attendees(
                        calendar_id, event_id)
                    if att_result.returncode == 0:
                        att_data = att_result.data or {}
                        att_items = (att_data.get("data", {}) or {}).get(
                            "items", att_data.get("items", [])) or []
                        for a in att_items:
                            if isinstance(a, dict):
                                name = a.get("display_name", a.get("name", ""))
                                if name and name != org_name:
                                    attendee_names.append(name)
                except Exception:
                    pass
            if attendee_names:
                text += f"\n参会人：{', '.join(attendee_names[:10])}"

            events.append({
                "project_id": project_id, "chat_id": "",
                "message_id": f"cal_{ev.get('event_id', '')}",
                "text": text, "content": text,
                "created_at": str(ev.get("start_time", utc_now_iso()))[:25],
                "source_type": "calendar",
                "source_url": cal_link,
                "sender": {"id": org_id,
                           "sender_type": "user",
                           "name": org_name or "日程创建者"},
            })

            # C3: 空闲/忙碌 → member_status
            free_busy = ev.get("free_busy_status", "")
            if free_busy == "busy" and org_name:
                start_raw = ev.get("start_time", "")
                start_t = start_raw if isinstance(start_raw, str) else (
                    start_raw.get("datetime", "") if isinstance(start_raw, dict) else str(start_raw))
                events.append({
                    "project_id": project_id, "chat_id": "",
                    "message_id": f"cal_{ev.get('event_id', '')}_status",
                    "text": f"【成员状态】{org_name} 在 {start_t[:16]} 有日程，状态忙碌",
                    "content": f"忙碌：{ev.get('summary', '')}",
                    "created_at": start_t or utc_now_iso(),
                    "source_type": "calendar",
                    "source_url": cal_link,
                    "sender": {"id": org_id, "sender_type": "user",
                               "name": org_name},
                })
        print(f"sync_calendar: {len(items)} 日程 → {len(events)} 事件")
        return self.ingest_events(events, debounce=False)

    def sync_minutes(self, start: str, end: str,
                      project_id: str = "default") -> list[MemoryItem]:
        """V1.13: 同步飞书会议纪要并提取记忆。

        会议纪要是价值最高的数据源——飞书 AI 已生成总结和待办项。
        """
        if self.adapter is None:
            raise RuntimeError("sync_minutes requires adapter")
        result = self.adapter.search_minutes(start, end)
        if result.returncode != 0:
            print(f"sync_minutes: failed: {result.stderr or result.stdout}")
            return []
        minutes_list = (result.data or {}).get("data", {}).get(
            "minutes", (result.data or {}).get("data", {}).get("items", []),
        ) or []
        if not minutes_list:
            print("sync_minutes: 未找到会议纪要")
            return []
        events = []
        for m in minutes_list[:10]:
            token = m.get("token", "")
            title = m.get("title", m.get("name", "未命名会议"))
            detail = {}
            if token:
                dr = self.adapter.get_minute_detail(token)
                if dr.returncode == 0:
                    detail = (dr.data or {}).get("data", {})
            text = f"【会议纪要】{title}"
            summary = detail.get("summary", m.get("summary", ""))
            if summary:
                text += f"\n总结: {summary[:1000]}"
            action_items = detail.get("action_items", []) or []
            for ai in action_items[:5]:
                text += f"\n待办: {ai.get('content','')} → {ai.get('assignee_name','')}"
            events.append({
                "project_id": project_id, "chat_id": "",
                "message_id": f"minute_{token}",
                "text": text[:2000], "content": text,
                "created_at": m.get("create_time", utc_now_iso()),
                "source_type": "meeting",
                "sender": {"id": "minute_sync",
                           "sender_type": "minute_sync"},
            })
        print(f"sync_minutes: {len(minutes_list)} 纪要 → {len(events)} 事件")
        return self.ingest_events(events, debounce=False)

    def sync_approvals(self, status: str = "pending",
                        project_id: str = "default") -> list[MemoryItem]:
        """V1.13: 同步飞书审批实例并提取记忆。

        审批中 = blocker，已通过/拒绝 = decision。
        """
        if self.adapter is None:
            raise RuntimeError("sync_approvals requires adapter")
        result = self.adapter.list_approval_instances(status)
        if result.returncode != 0:
            print(f"sync_approvals: failed: {result.stderr or result.stdout}")
            return []
        raw = result.data or {}
        if isinstance(raw, list):
            items = raw
        else:
            inner = raw.get("data", raw)
            items = inner if isinstance(inner, list) else inner.get("items", []) or []
        if not items:
            print(f"sync_approvals: 未找到 {status} 审批")
            return []
        events = []
        for inst in items[:10]:
            inst_status = inst.get("status", status)
            text = f"【审批】{inst.get('approval_name','')} — {inst_status}"
            # Include status in the message_id so that the same approval
            # instance transitioning pending -> approved/rejected produces
            # distinct events instead of being deduped by raw-event id.
            events.append({
                "project_id": project_id, "chat_id": "",
                "message_id": f"approval_{inst.get('instance_id','')}_{inst_status}",
                "text": text, "content": text,
                "created_at": inst.get("start_time", utc_now_iso()),
                "source_type": "approval",
                "sender": {"id": inst.get("applicant_id", ""),
                           "sender_type": "approval_sync"},
            })
        print(f"sync_approvals: {len(items)} {status} → {len(events)} 事件")
        return self.ingest_events(events, debounce=False)

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

    def search_hybrid(
        self,
        query: str,
        project_id: str | None = None,
        as_of: str | None = None,
        top_k: int = 10,
        keyword_weight: float = 0.7,
    ) -> list[tuple[MemoryItem, float]]:
        """V1.13: 混合搜索 — 关键词 + 向量语义融合。

        如果 vector_store 不可用，自动降级为纯关键词搜索。

        Args:
            query: 搜索查询
            project_id: 可选的项目 ID 过滤
            as_of: 可选的时间点过滤
            top_k: 最大结果数
            keyword_weight: 关键词权重 (0-1)，向量权重 = 1 - keyword_weight

        Returns:
            (MemoryItem, fused_score) 列表，按混合相关度降序
        """
        return self.store.search_hybrid(
            query=query,
            project_id=project_id,
            vector_store=self.vector_store,
            top_k=top_k,
            keyword_weight=keyword_weight,
            as_of=as_of,
        )

    # ── V1.15: 任务状态回流 ─────────────────────────────────────

    def sync_task_status(self, data_dir: str | None = None) -> int:
        """V1.19 P1 FEAT-1: 任务状态回流——检测飞书中已完成的任务→更新 MemoryItem。

        读取 task_map.jsonl 中的 task_guid，查飞书任务状态。
        completed/done → 对应 next_step 标为 task_status="completed"。
        同步扫描关联 blocker 并标记 resolved，填充 last_diff 供 R5 可见。
        """
        import json as _json
        task_map_path = Path(data_dir or self.store.data_dir) / "task_map.jsonl"
        if not task_map_path.exists():
            return 0

        entries = []
        for line in task_map_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass

        if not entries or self.adapter is None:
            return 0

        updated = 0
        updated_ids: list[str] = []
        seen = set()
        for entry in entries[-50:]:
            guid = entry.get("task_guid", "")
            if not guid or guid in seen:
                continue
            seen.add(guid)

            result = self.adapter.search_tasks(guid)
            if result.returncode != 0:
                continue
            data = result.data or {}
            tasks = data.get("data", {}).get("items", []) or []
            for task in tasks:
                if task.get("guid") != guid:
                    continue
                status = task.get("status", "")
                if status in ("completed", "done"):
                    project = entry.get("project_id", "")
                    items = self.store.list_items(project)
                    summary = entry.get("summary", "")
                    matched = False
                    for item in items:
                        if item.state_type != "next_step":
                            continue
                        if summary[:30] in item.current_value or \
                           item.current_value[:30] in summary:
                            result_item = self.store.update_item_metadata(
                                item.memory_id,
                                {"task_status": "completed", "task_guid": guid},
                            )
                            if result_item:
                                updated_ids.append(item.memory_id)
                                updated += 1
                            matched = True
                            break
                    if not matched:
                        break

                    # FEAT-1: 任务完成 → 扫描关联 blocker → 标记 resolved
                    for blk in items:
                        if blk.state_type != "blocker":
                            continue
                        meta = dict(getattr(blk, "metadata", {}) or {})
                        if meta.get("blocker_status", "open") in ("resolved", "obsolete"):
                            continue
                        if (summary[:30] in blk.current_value
                                or blk.current_value[:30] in summary):
                            self.store.update_blocker_status(
                                blk.memory_id, "resolved",
                                {"resolved_by": "task_backflow",
                                 "blocking_reason": summary},
                            )
                            updated_ids.append(blk.memory_id)
                            updated += 1

                break  # one match per task_guid is enough

        if updated_ids:
            # FEAT-1: 填充 last_diff 使 trigger R5 可检测到阻塞解除
            all_items = self.store.list_items(
                entries[0].get("project_id", "") if entries else "")
            for item in all_items:
                if item.memory_id in updated_ids:
                    self.last_diff["updated"].append(item)

        return updated

    @staticmethod
    def _normalize_events(events: list[dict]) -> list[dict]:
        """V1.19 P1-A fix: 统一 normalize，确保非文本消息生成占位文本。

        对 text 外的 msg_type 调用 MessageParser，在 engine 内部消除静默丢弃。
        已带有 parser 产物的 event（从 pipeline 的 _normalize_event 来）不会重复处理。
        """
        from memory.message_parser import get_parser
        parser = get_parser()
        out = []
        for ev in events:
            msg_type = ev.get("msg_type", "text")
            if msg_type == "text":
                out.append(ev)
                continue
            # 如果已经在 pipeline 层 normalize 过，跳过
            if ev.get("media_refs") or ev.get("has_unsupported_media"):
                out.append(ev)
                continue
            content = ev.get("content", "") or ev.get("text", "")
            parsed = parser.parse_content(str(content), str(msg_type))
            ev = dict(ev)
            if parsed.text:
                ev["text"] = parsed.text
                ev["content"] = parsed.text
            if parsed.media_refs:
                ev["media_refs"] = parsed.media_refs
            if parsed.has_unsupported_media:
                ev["has_unsupported_media"] = True
            if parsed.mentions:
                ev["at_list"] = parsed.mentions
            if parsed.links:
                ev["links"] = parsed.links
            out.append(ev)
        return out

    @staticmethod
    def _sanitize_items(items: list[MemoryItem]) -> list[MemoryItem]:
        """1.3: 统一数据净化——提取后、存储前集中清洗。

        处理内容：
        - 剥离 sender 前缀（"李四：在弄了"→"在弄了"）
        - 丢弃 @bot 命令残留
        - 丢弃文档/任务同步噪音
        - 裸名 owner 降级为 needs_review
        - 丢弃无意义短阻塞文本
        """
        import re as _re
        cleaned: list[MemoryItem] = []
        for item in items:
            # 非 message 来源（doc/task/calendar 等）→ 直接保留
            src_type = item.source_refs[0].type if item.source_refs else "message"
            if src_type != "message":
                cleaned.append(item)
                continue

            # ── 剥离 sender 前缀 ──
            sender = item.source_refs[0].sender_name if item.source_refs else ""
            val = item.current_value or ""
            if sender and len(sender) >= 2:
                # "李四：李四：xxx" → "xxx"
                dup = _re.match(r'^(.{1,10})[：:]\1[：:]\s*', val)
                if dup:
                    val = val[dup.end():]
                # "李四：xxx" → "xxx"
                elif val.startswith(sender + "：") or val.startswith(sender + ":"):
                    val = val[len(sender) + 1:]
            item.current_value = val.strip()

            # ── 丢弃 @bot 命令 ──
            if val.strip().startswith("@bot"):
                continue

            # ── 丢弃纯文档/任务引用（无实质协作内容）──
            if (val.startswith("【文档】") or val.startswith("【任务】")) and len(val) < 50:
                continue

            # ── 丢弃无意义短阻塞文本 ──
            _blocker_noise = ("阻塞清单", "风险列表", "问题列表", "待办清单")
            if item.state_type == "blocker" and val.strip() in _blocker_noise:
                continue
            if item.state_type == "blocker" and len(val.strip()) < 4:
                continue

            # ── 裸名 owner → needs_review ──
            if item.state_type == "owner":
                owner_val = (item.owner or "").strip()
                if val.strip() == owner_val and len(owner_val) <= 4:
                    item.review_status = "needs_review"
                    item.confidence = min(item.confidence, 0.40)

            # ── member_status 去重 + 过滤 ──
            if item.state_type == "member_status":
                _core_status = ("请假", "不在", "休假", "出差", "习惯用", "擅长")
                if len(val.strip()) >= 4:
                    pass
                elif any(kw in val for kw in _core_status):
                    pass
                else:
                    continue
                if any(i.state_type == "member_status"
                       and i.current_value.strip() == val.strip()
                       for i in cleaned):
                    continue

            # ── blocker 含收尾/庆祝词 → 丢弃（PM收尾消息不是阻塞）──
            if item.state_type == "blocker":
                _closing = ("感谢", "辛苦", "庆祝", "收官", "太棒", "周末愉快",
                           "冲刺回顾", "大家加油", "做得不错")
                if any(kw in val for kw in _closing):
                    continue

            # ── decision 含总结词但无决策词 → 丢弃 ──
            if item.state_type == "decision":
                _summary = ("总结", "同步一下", "汇报", "过一下", "对齐", "站会",
                           "进度同步", "下午同步")
                _decision = ("决定", "确认", "采用", "改为", "不再", "废弃", "定了")
                if any(kw in val for kw in _summary) and not any(kw in val for kw in _decision):
                    continue

            # ── next_step 含生活词但无任务动词 → 丢弃 ──
            if item.state_type == "next_step":
                _casual = ("吃饭", "下午茶", "奶茶", "火锅", "下班", "周末",
                          "老板请客", "聚餐", "打游戏", "请假", "休息")
                _task_verb = ("完成", "提交", "修复", "上线", "开发", "测试",
                            "部署", "整理", "确认", "补充", "发送", "同步")
                if any(kw in val for kw in _casual) and not any(kw in val for kw in _task_verb):
                    continue
            # Prompt 语用分类（A1-A3）让 LLM 在源头不产出这些噪音；
            # RuleOnly 产生的同类噪音量小且会被 needs_review 标记。

            # ── 确认回复残留 ──
            if val.strip() in ("确认1", "确认2", "确认3", "都不是", "驳回1", "驳回2"):
                continue

            cleaned.append(item)
        return cleaned

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
        self._debounce_state_path.parent.mkdir(parents=True, exist_ok=True)
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
        self._identity_path.parent.mkdir(parents=True, exist_ok=True)
        self._identity_path.write_text(
            json.dumps(self._identity, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._chat_map_path.parent.mkdir(parents=True, exist_ok=True)
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
