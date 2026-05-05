"""Trigger engine: scan memory diffs and generate executable action proposals.

V1.14: Rule-based trigger that scans upsert diffs after each ingestion,
generating ActionProposal instances for the ActionExecutor to consume.
Supports three modes: preview (display only), confirm (interactive),
and auto (execute all).

Architecture:
    upsert_items() diff → ActionTrigger.scan() → ActionProposal[] →
    ActionExecutor.execute_plan() → action_log writeback
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from memory.action_planner import ActionProposal
from memory.action_log import has_recent_action, write_action_log
from memory.date_parser import deadline_is_imminent
from memory.schema import MemoryItem


class ActionTrigger:
    """Scan upsert diffs and generate action proposals via trigger rules.

    Usage:
        trigger = ActionTrigger(engine, adapter, log_path)
        items, diff = store.upsert_items(new_items, processed_ids)
        proposals = trigger.scan(diff, project_id, chat_id, mode="auto")
        results = executor.execute_plan(proposals, context)
        trigger.write_results(results, project_id)
    """

    def __init__(
        self,
        engine: Any = None,
        log_path: str | Path = "data/action_log.jsonl",
        cooldown_seconds: float = 86400,
    ) -> None:
        """Create a trigger engine.

        Args:
            engine: MemoryEngine instance (for resolve_owner_open_id and store access).
            log_path: Path to action_log.jsonl for idempotency and audit.
            cooldown_seconds: Min seconds between repeated actions (default 24h).
        """
        self.engine = engine
        self.log_path = Path(log_path)
        self.cooldown_seconds = cooldown_seconds
        # In-memory cooldown cache (supplements file-based check)
        self._last_alert: dict[str, datetime] = {}

    # ── Public API ───────────────────────────────────────────────

    def scan(
        self,
        diff: dict[str, list[MemoryItem]],
        project_id: str,
        chat_id: str = "",
        mode: str = "preview",
    ) -> list[ActionProposal]:
        """Scan a diff and return action proposals.

        Args:
            diff: {"created": [...], "updated": [...], "unchanged": [...]}
            project_id: Current project identifier.
            chat_id: Target Feishu chat for alerts.
            mode: "preview" | "confirm" | "auto"

        Returns:
            List of ActionProposal instances (empty if nothing triggered).
        """
        proposals: list[ActionProposal] = []
        proposals.extend(self._rule_next_step_to_task(diff, project_id))
        proposals.extend(self._rule_new_blocker_alert(diff, project_id, chat_id))
        proposals.extend(self._rule_deadline_risk_warning(project_id, chat_id))
        proposals.extend(self._rule_low_confidence_question(diff, project_id, chat_id))
        proposals.extend(self._rule_blocker_resolved(diff, project_id, chat_id))
        return proposals

    def write_results(
        self,
        results: list[Any],
        project_id: str,
    ) -> None:
        """Write execution results to action_log for audit."""
        for r in results:
            proposal = getattr(r, "action", None)
            if proposal is None:
                continue
            write_action_log(
                self.log_path,
                project_id=project_id,
                action_type=getattr(proposal, "action_type", ""),
                proposal_title=getattr(proposal, "title", ""),
                idempotency_key=getattr(proposal, "idempotency_key", ""),
                success=getattr(r, "success", False),
                output_data=getattr(r, "output_data", {}),
                error=getattr(r, "error", ""),
            )

    # ── Rule 1: next_step + owner → create task ──────────────────

    def _rule_next_step_to_task(
        self, diff: dict[str, list[MemoryItem]], project_id: str,
    ) -> list[ActionProposal]:
        """Trigger: newly created next_step with an owner → create Feishu task."""
        proposals: list[ActionProposal] = []
        for item in diff.get("created", []):
            if item.state_type != "next_step":
                continue
            if not item.owner:
                continue
            if item.status != "active":
                continue
            if getattr(item, "review_status", "") == "needs_review":
                continue

            id_key = ActionProposal.make_idempotency_key(
                "rule1_next_step", project_id, item.current_value[:80]
            )
            if self._is_cooling_down(id_key):
                continue

            # Resolve owner name → open_id
            open_id = ""
            if self.engine:
                try:
                    open_id = self.engine.resolve_owner_open_id(item.owner) or ""
                except Exception:
                    pass

            evidence = [
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:80],
                 "sender": ref.sender_name}
                for ref in item.source_refs[:2]
            ]

            proposals.append(ActionProposal(
                action_type="create_task",
                title=f"[Memory] {item.current_value[:80]}",
                reason=f"检测到新任务且负责人为 {item.owner}，自动创建飞书任务",
                confidence=item.confidence,
                risk_level="low",
                requires_confirmation=not bool(open_id),  # no open_id → needs confirm
                idempotency_key=id_key,
                target_owner=item.owner,
                target_owner_open_id=open_id,
                evidence_refs=evidence,
                metadata={"source_state_type": item.state_type,
                          "source_key": item.key},
            ))
        return proposals

    # ── Rule 2: new blocker → group alert ─────────────────────────

    def _rule_new_blocker_alert(
        self, diff: dict[str, list[MemoryItem]], project_id: str, chat_id: str,
    ) -> list[ActionProposal]:
        """Trigger: newly created blocker → send alert to group chat.

        Aggregates multiple blockers from the same scan into one message.
        """
        new_blockers = [
            item for item in diff.get("created", [])
            if item.state_type == "blocker" and item.status == "active"
            and getattr(item, "review_status", "") != "needs_review"
            and self._is_unresolved_blocker(item)
        ]
        if not new_blockers or not chat_id:
            return []

        # Filter: genuinely new or upgraded blockers only
        genuine = []
        for b in new_blockers:
            if self._is_genuinely_new_blocker(b, project_id):
                genuine.append(b)

        # Filter by cooldown
        active_blockers = []
        for b in genuine:
            id_key = ActionProposal.make_idempotency_key(
                "rule2_blocker", project_id, b.identity_key()
            )
            if not self._is_cooling_down(id_key):
                active_blockers.append((b, id_key))

        if not active_blockers:
            return []

        # Build aggregated alert message
        if len(active_blockers) == 1:
            b, _ = active_blockers[0]
            alert_title = f"⚠️ 发现新阻塞：{b.current_value[:80]}"
            alert_detail = (
                f"来源：{b.source_refs[0].sender_name} "
                f"({b.source_refs[0].created_at[:10] if b.source_refs else '未知'})"
            )
        else:
            alert_title = f"⚠️ 本轮同步发现 {len(active_blockers)} 个新阻塞"
            lines = [alert_title, ""]
            for i, (b, _) in enumerate(active_blockers[:3]):
                lines.append(f"{i+1}. {b.current_value[:100]}")
            if len(active_blockers) > 3:
                lines.append(f"...等 {len(active_blockers)} 个阻塞")
            alert_detail = "\n".join(lines)

        id_key = ActionProposal.make_idempotency_key(
            "rule2_blocker_aggregated", project_id,
            "+".join(b.identity_key() for b, _ in active_blockers)[:80]
        )

        return [ActionProposal(
            action_type="send_alert",
            title=alert_title,
            reason=f"检测到 {len(active_blockers)} 个新阻塞，自动发送群提醒",
            confidence=0.75,
            risk_level="medium",
            requires_confirmation=False,
            idempotency_key=id_key,
            target_chat_id=chat_id,
            evidence_refs=[
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:80]}
                for b, _ in active_blockers
                for ref in b.source_refs[:1]
            ],
            metadata={"alert_detail": alert_detail, "blocker_count": len(active_blockers)},
        )]

    # ── Rule 3: deadline imminent + blocker → risk warning ───────

    def _rule_deadline_risk_warning(
        self, project_id: str, chat_id: str,
    ) -> list[ActionProposal]:
        """Trigger: active deadline within 3 days + active blockers → risk warning.

        This is the highest demo-value rule — it demonstrates cross-memory
        reasoning by correlating two different state_types.
        """
        if not chat_id or not self.engine:
            return []

        store = getattr(self.engine, "store", None)
        if store is None:
            return []

        items = store.list_items(project_id)
        deadlines = [i for i in items
                     if i.state_type == "deadline" and i.status == "active"
                     and getattr(i, "review_status", "") != "needs_review"]
        blockers = [i for i in items
                    if i.state_type == "blocker" and i.status == "active"
                    and getattr(i, "review_status", "") != "needs_review"
                    and self._is_unresolved_blocker(i)]

        if not deadlines or not blockers:
            return []

        # Check if any deadline is within 3 days
        imminent_dl = None
        for dl in deadlines:
            if deadline_is_imminent(dl.current_value, within_days=3):
                imminent_dl = dl
                break

        if imminent_dl is None:
            return []

        # Build risk warning
        id_key = ActionProposal.make_idempotency_key(
            "rule3_risk_warning", project_id,
            f"{imminent_dl.key}+{len(blockers)}blockers"
        )
        if self._is_cooling_down(id_key):
            return []

        severity = "high" if len(blockers) >= 3 else "medium"

        title = (
            f"🚨 风险预警：截止时间 {imminent_dl.current_value[:30]} "
            f"临近，仍有 {len(blockers)} 个未解决阻塞"
        )
        lines = [title, ""]
        lines.append(f"⏰ 截止：{imminent_dl.current_value[:60]}")
        lines.append(f"⚠️ 阻塞 ({len(blockers)} 个)：")
        for b in blockers[:5]:
            owner_hint = f"（{b.owner}）" if b.owner else ""
            lines.append(f"  - {b.current_value[:100]}{owner_hint}")
        if len(blockers) > 5:
            lines.append(f"  ...等 {len(blockers)} 个阻塞")

        return [ActionProposal(
            action_type="send_alert",
            title=title,
            reason=f"截止时间 {imminent_dl.current_value[:30]} 在 3 天内且存在 {len(blockers)} 个阻塞",
            confidence=0.80,
            risk_level=severity,
            requires_confirmation=False,
            idempotency_key=id_key,
            target_chat_id=chat_id,
            evidence_refs=[
                {"message_id": ref.message_id, "excerpt": ref.excerpt[:80]}
                for ref in imminent_dl.source_refs[:1]
            ],
            metadata={
                "alert_detail": "\n".join(lines),
                "deadline_count": len(deadlines),
                "blocker_count": len(blockers),
                "imminent_deadline": imminent_dl.current_value[:60],
            },
        )]

    # ── Rule 5: 阻塞解除通知 ──────────────────────────────────

    def _rule_blocker_resolved(
        self, diff: dict[str, list], project_id: str, chat_id: str,
    ) -> list[ActionProposal]:
        """R5: 检测到阻塞被标记为 resolved → 通知群聊。"""
        if not chat_id:
            return []
        resolved = []
        for item in diff.get("updated", []):
            if item.state_type != "blocker":
                continue
            meta = getattr(item, "metadata", None) or {}
            if meta.get("blocker_status") != "resolved":
                continue
            id_key = ActionProposal.make_idempotency_key(
                "r5_resolved", project_id, item.identity_key()
            )
            if self._is_cooling_down(id_key):
                continue
            resolved.append((item, id_key))

        if not resolved:
            return []

        if len(resolved) == 1:
            b, _ = resolved[0]
            title = f"阻塞已解决：{b.current_value[:80]}"
        else:
            title = f"{len(resolved)} 个阻塞已解决"

        return [ActionProposal(
            action_type="send_alert",
            title=title,
            reason=f"{len(resolved)} 个阻塞被标记为已解决",
            confidence=0.85,
            risk_level="low",
            requires_confirmation=False,
            idempotency_key=ActionProposal.make_idempotency_key(
                "r5_resolved_agg", project_id, "batch"),
            target_chat_id=chat_id,
            metadata={"alert_detail": title, "resolved_count": len(resolved)},
        )]

    # ── Rule 2 helpers ──────────────────────────────────────────

    def _is_genuinely_new_blocker(self, item, project_id: str) -> bool:
        """Check if a blocker is genuinely new or severity-upgraded.

        Returns True only if:
        - This identity_key has never appeared in history, OR
        - Previous occurrence was resolved and this is a new occurrence
        Skips: same-topic re-extractions of already-known blockers.
        """
        if not self.engine:
            return True  # no engine → can't check → assume new
        store = getattr(self.engine, "store", None)
        if not store:
            return True
        history = store.list_history(project_id)
        for h in history:
            if h.state_type != "blocker":
                continue
            if h.identity_key() == item.identity_key():
                # Same blocker existed before — check if it was resolved
                h_meta = getattr(h, "metadata", None) or {}
                if h_meta.get("blocker_status") != "resolved":
                    return False  # already known, not new
        return True

    # ── Rule 4: 低置信度主动提问 ───────────────────────────────

    def _rule_low_confidence_question(
        self, diff: dict[str, list], project_id: str, chat_id: str,
    ) -> list[ActionProposal]:
        """R4: 低置信度候选 → 聚合确认消息发送到群。

        五大约束：
        1. 按人聚合 — 同一人合并为一条
        2. 冷却 — 同人2h + 同候选24h
        3. 门槛 — 有owner+无自指代+有证据+非闲聊+置信度区间
        4. 上限 — 每轮1条消息，最多5个候选
        5. 延迟 — 来源消息至少30分钟前
        """
        if not chat_id:
            return []

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        delay_cutoff = (now - timedelta(minutes=30)).isoformat()
        person_cooldown = 2 * 3600  # 2 hours

        # ── 筛选候选 ──
        candidates: list[tuple] = []  # (item, owner)
        for item in diff.get("created", []):
            if item.state_type not in ("next_step", "decision"):
                continue
            owner = item.owner
            if not owner or len(owner) < 2:
                continue
            if owner in ("我", "你", "他", "她", "我们", "他们", "大家", "自己"):
                continue
            if not item.source_refs:
                continue
            # 置信度区间: 太低不值得问，太高直接通过
            if not (0.35 <= item.confidence < 0.60):
                continue
            # 非闲聊/问句/反问
            text = item.current_value
            if text.rstrip().endswith(("？", "?", "吗", "呢", "吧")):
                continue
            if any(w in text for w in ("请问", "谁知道", "有没有人", "怎么配")):
                continue
            # 来源消息延迟30分钟
            src_time = item.source_refs[0].created_at if item.source_refs else ""
            if src_time and src_time > delay_cutoff:
                continue
            # 冷却: 同候选24h
            id_key = ActionProposal.make_idempotency_key(
                "r4_question", project_id, item.current_value[:80]
            )
            if self._is_cooling_down(id_key):
                continue
            candidates.append((item, owner, id_key))

        if not candidates:
            return []

        # ── 按人聚合 ──
        by_owner: dict[str, list] = {}
        for item, owner, id_key in candidates:
            # 冷却: 同人2h
            person_key = f"r4_person_{owner}"
            if self._is_cooling_down_person(person_key, person_cooldown):
                continue
            by_owner.setdefault(owner, []).append((item, id_key))

        if not by_owner:
            return []

        # 选人数最多的一组（最多5个候选）
        best_owner = max(by_owner, key=lambda o: len(by_owner[o]))
        group = by_owner[best_owner][:5]

        # ── 生成确认消息 ──
        lines = [f"@{best_owner} 系统识别到以下可能与你相关的待办，请确认：", ""]
        for i, (item, _) in enumerate(group, 1):
            src = item.source_refs[0] if item.source_refs else None
            time_hint = src.created_at[:16] if src and src.created_at else ""
            lines.append(f"{i}. {item.current_value[:100]}（{time_hint}）")
        lines.append("")
        lines.append("以上是否需要创建飞书任务？回复\"确认1,2\"或\"都不是\"")

        msg_text = "\n".join(lines)
        id_key = ActionProposal.make_idempotency_key(
            "r4_question_aggregated", project_id, best_owner
        )

        return [ActionProposal(
            action_type="send_alert",
            title=f"确认请求：{best_owner} 的 {len(group)} 个待办候选",
            reason=f"低置信度候选 ({len(group)} 项)，发送确认请求给 {best_owner}",
            confidence=0.5,
            risk_level="low",
            requires_confirmation=False,
            idempotency_key=id_key,
            target_chat_id=chat_id,
            target_owner=best_owner,
            metadata={"alert_detail": msg_text, "candidate_count": len(group),
                      "candidate_owner": best_owner},
        )]

    def _is_cooling_down_person(self, person_key: str, cooldown_seconds: float) -> bool:
        """检查同人冷却（独立于同候选冷却）。"""
        last = self._last_alert.get(person_key)
        if last is not None:
            elapsed = (datetime.now() - last).total_seconds()
            if elapsed < cooldown_seconds:
                return True
        self._last_alert[person_key] = datetime.now()
        return False

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _is_unresolved_blocker(item) -> bool:
        """Check if a blocker is still unresolved (not resolved/obsolete)."""
        if item.state_type != "blocker":
            return False
        meta = getattr(item, "metadata", None) or {}
        bs = meta.get("blocker_status", "open")
        return bs not in ("resolved", "obsolete")

    def _is_cooling_down(self, idempotency_key: str) -> bool:
        """Check both in-memory cache and persistent action_log."""
        # In-memory cache
        last = self._last_alert.get(idempotency_key)
        if last is not None:
            elapsed = (datetime.now() - last).total_seconds()
            if elapsed < self.cooldown_seconds:
                return True
        # Persistent log
        if has_recent_action(self.log_path, idempotency_key, self.cooldown_seconds):
            return True
        # Mark as seen
        self._last_alert[idempotency_key] = datetime.now()
        return False
