"""Work Pattern Memory — second-order layer on existing MemoryItems.

V1.18: Derives higher-level collaboration patterns from existing structured
memories without re-scanning raw messages. All patterns default to
needs_review and require evidence from at least 2 source MemoryItems.

Three pattern types:
  1. handoff_risk    — people with tasks+deadlines+blockers+leave status
  2. dependency_blocker — blocker chains: who blocks whom via what dependency
  3. responsibility_domain — what modules each person has recently owned
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from memory.schema import MemoryItem


@dataclass
class PatternMemoryItem:
    """A derived collaboration pattern from multiple MemoryItems."""

    pattern_type: str       # handoff_risk / dependency_blocker / responsibility_domain
    scope: str              # "project" or "user:{name}"
    summary: str            # 人类可读描述
    time_window: str        # "7d" / "30d"
    confidence: float
    source_memory_ids: list[str] = field(default_factory=list)
    evidence_refs: list[dict] = field(default_factory=list)
    review_status: str = "needs_review"

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ── Pattern generators ─────────────────────────────────────────

def generate_handoff_risk(
    items: list[MemoryItem],
    project_id: str = "",
    owner_name: str = "",
) -> list[PatternMemoryItem]:
    """Find people with active tasks + deadlines/blockers + leave status."""
    from memory.date_parser import deadline_is_imminent

    # Group by owner
    by_owner: dict[str, list[MemoryItem]] = {}
    for item in items:
        if item.project_id != project_id and project_id:
            continue
        owner = item.owner or ""
        if owner:
            by_owner.setdefault(owner, []).append(item)

    patterns = []
    for owner, mems in by_owner.items():
        if owner_name and owner != owner_name:
            continue
        tasks = [m for m in mems if m.state_type == "next_step" and m.status == "active"]
        blockers = [m for m in mems if m.state_type == "blocker" and m.status == "active"]
        deadlines = [m for m in mems if m.state_type == "deadline" and m.status == "active"]
        leave = [m for m in mems if m.state_type == "member_status"]

        # Needs: active tasks AND (deadline imminent OR has blocker OR on leave)
        imminent_dl = [d for d in deadlines if deadline_is_imminent(d.current_value, 3)]
        has_risk = tasks and (imminent_dl or blockers or leave)

        if not has_risk:
            continue

        source_ids = [m.memory_id for m in tasks[:3] + blockers[:2] + imminent_dl[:2]]

        parts = [f"{owner} 当前负责 {len(tasks)} 个活跃任务"]
        if imminent_dl:
            parts.append(f"其中 {len(imminent_dl)} 个截止时间在 3 天内")
        if blockers:
            parts.append(f"存在 {len(blockers)} 个阻塞")
        if leave:
            parts.append("该成员当前请假/不可用")
        parts.append("建议在交接或站会中优先关注")

        patterns.append(PatternMemoryItem(
            pattern_type="handoff_risk",
            scope=f"user:{owner}",
            summary="。".join(parts),
            time_window="7d",
            confidence=0.70,
            source_memory_ids=source_ids,
            evidence_refs=[_build_evidence(m) for m in tasks[:2] + blockers[:1]],
        ))

    return patterns


def generate_dependency_blockers(
    items: list[MemoryItem],
    project_id: str = "",
) -> list[PatternMemoryItem]:
    """Find blocker dependency chains: A is blocked, depends on B."""
    blockers = [m for m in items
                if m.state_type == "blocker" and m.status == "active"
                and (m.project_id == project_id if project_id else True)]

    # Filter: blockers with a dependency_owner set
    deps = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        dep_owner = meta.get("dependency_owner", "")
        if dep_owner:
            deps.append((b, dep_owner))

    if len(deps) < 1:
        return []

    # Build dependency chain description
    chains = []
    for b, dep_owner in deps:
        owner = b.owner or "?"
        chains.append(f"{owner} 的任务被阻塞 → 依赖 {dep_owner}：{b.current_value[:60]}")

    summary = "当前依赖阻塞链：\n" + "\n".join(f"- {c}" for c in chains[:5])
    if len(chains) > 5:
        summary += f"\n- ...共 {len(chains)} 条依赖链"

    return [PatternMemoryItem(
        pattern_type="dependency_blocker",
        scope=f"project:{project_id}",
        summary=summary,
        time_window="7d",
        confidence=0.72,
        source_memory_ids=[b.memory_id for b, _ in deps[:5]],
        evidence_refs=[_build_evidence(b) for b, _ in deps[:3]],
    )]


def generate_domain_responsibility(
    items: list[MemoryItem],
    project_id: str = "",
    owner_name: str = "",
) -> list[PatternMemoryItem]:
    """Count what modules each person has recently owned (facts only)."""
    owners = [m for m in items
              if m.state_type == "owner" and m.owner
              and (m.project_id == project_id if project_id else True)]

    if owner_name:
        owners = [m for m in owners if m.owner == owner_name]

    # Group by owner, count domain keywords from key
    by_owner: dict[str, list[str]] = {}
    for m in owners:
        name = m.owner or ""
        # Extract domain from key: owner_frontend → 前端, owner_api_module → API模块
        key = m.key
        if key.startswith("owner_"):
            domain = key[6:][:30]  # remove "owner_" prefix
            domain = domain.replace("_", " ")  # restore spaces
            by_owner.setdefault(name, []).append(domain)

    patterns = []
    for name, domains in by_owner.items():
        if len(domains) < 1:
            continue
        # Count domain frequency
        from collections import Counter
        counter = Counter(domains)
        top = counter.most_common(3)
        domain_desc = "、".join(f"{d}(x{c})" for d, c in top)

        summary = f"{name} 近期负责：{domain_desc}"
        source_ids = [m.memory_id for m in owners if m.owner == name][:5]

        patterns.append(PatternMemoryItem(
            pattern_type="responsibility_domain",
            scope=f"user:{name}",
            summary=summary,
            time_window="30d",
            confidence=0.68,
            source_memory_ids=source_ids,
            evidence_refs=[_build_evidence(m) for m in owners if m.owner == name][:3],
        ))

    return patterns


# ── V1.18 P0: 阻塞热点识别 ─────────────────────────────────

# 轻量 domain keyword tagger（足够演示，无需NLP）
_BLOCKER_DOMAIN_MAP = {
    "设计": ("设计稿", "交互稿", "Figma", "UI", "原型", "视觉", "Sketch", "设计"),
    "后端接口": ("API", "接口", "联调", "后端", "服务端", "数据库", "服务"),
    "前端": ("前端", "页面", "组件", "样式", "JS", "React", "Vue"),
    "测试": ("测试", "QA", "用例", "回归", "压测"),
    "环境部署": ("部署", "服务器", "环境", "运维", "发布", "上线", "云资源"),
    "审批": ("审批", "申请", "权限", "流程"),
    "文档": ("文档", "需求", "PRD", "方案", "确认"),
    "第三方依赖": ("第三方", "外部", "SDK", "插件", "支付", "短信", "推送"),
    "数据": ("数据", "统计", "报表", "日志", "监控", "埋点"),
    "安全": ("安全", "加密", "认证", "鉴权", "扫描", "漏洞"),
}


def _tag_blocker_domain(text: str) -> str:
    """Tag a blocker text with its most likely domain."""
    for domain, keywords in _BLOCKER_DOMAIN_MAP.items():
        if any(kw in text for kw in keywords):
            return domain
    return "其他"


def generate_blocker_hotspot(
    items: list[MemoryItem],
    project_id: str = "",
) -> list[PatternMemoryItem]:
    """找出阻塞最集中的模块/依赖环节。"""
    blockers = [m for m in items
                if m.state_type == "blocker" and m.status == "active"
                and (m.project_id == project_id if project_id else True)]

    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)

    if len(unresolved) < 2:
        return []

    # Tag each blocker by domain
    domain_counts: dict[str, list] = {}
    for b in unresolved:
        domain = _tag_blocker_domain(b.current_value)
        domain_counts.setdefault(domain, []).append(b)

    # Find the top domain
    top_domain = max(domain_counts, key=lambda d: len(domain_counts[d]))
    top_blockers = domain_counts[top_domain]
    pct = len(top_blockers) / len(unresolved) * 100

    # Check for imminent deadlines in same domain
    from memory.date_parser import deadline_is_imminent
    deadlines = [m for m in items if m.state_type == "deadline"]
    imminent = [d for d in deadlines if deadline_is_imminent(d.current_value, 3)]

    summary = (
        f"阻塞热点：{top_domain}。共 {len(top_blockers)} 条阻塞，"
        f"占未解决 blocker 的 {pct:.0f}%。"
    )
    if top_blockers:
        summary += f" 关联任务：{'、'.join(b.current_value[:40] for b in top_blockers[:3])}。"
    if imminent:
        summary += f" 同时存在 {len(imminent)} 个临近 DDL。"
    summary += f" 建议优先确认 {top_domain} 环节的负责人与交付时间。"

    return [PatternMemoryItem(
        pattern_type="blocker_hotspot",
        scope=f"project:{project_id}",
        summary=summary,
        time_window="7d",
        confidence=0.72,
        source_memory_ids=[b.memory_id for b in top_blockers[:5]],
        evidence_refs=[_build_evidence(b) for b in top_blockers[:3]],
    )]


# ── V1.18 P0: 长期无更新任务 ─────────────────────────────────

def generate_stale_task(
    items: list[MemoryItem],
    project_id: str = "",
    stale_days: int = 1,
) -> list[PatternMemoryItem]:
    """找出超过 N 天无更新的活跃任务。"""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()

    tasks = [m for m in items
             if m.state_type == "next_step" and m.status == "active"
             and (m.project_id == project_id if project_id else True)]

    stale = [t for t in tasks if t.updated_at and t.updated_at < cutoff]

    if len(stale) < 1:
        return []

    # Check if stale tasks have deadlines or blockers
    with_dl = [t for t in stale if any(
        d.current_value and t.owner and t.owner in (d.owner or "")
        for d in items if d.state_type == "deadline"
    )]

    summary = f"发现 {len(stale)} 个超过 {stale_days} 天未更新的任务"
    if with_dl:
        summary += f"，其中 {len(with_dl)} 个有截止日期"
    summary += "，建议在站会中确认进度或重新分配。"

    return [PatternMemoryItem(
        pattern_type="stale_task",
        scope=f"project:{project_id}",
        summary=summary,
        time_window=f"{stale_days}d",
        confidence=0.65,
        source_memory_ids=[t.memory_id for t in stale[:5]],
        evidence_refs=[_build_evidence(t) for t in stale[:3]],
    )]


# ── V1.18 P0: 截止风险评分 ───────────────────────────────────

def generate_deadline_risk_score(
    items: list[MemoryItem],
    project_id: str = "",
) -> list[PatternMemoryItem]:
    """轻量规则风险评分：高/中/低。"""
    from memory.date_parser import deadline_is_imminent

    deadlines = [m for m in items
                 if m.state_type == "deadline" and m.status == "active"
                 and (m.project_id == project_id if project_id else True)]
    blockers = [m for m in items
                if m.state_type == "blocker" and m.status == "active"
                and (m.project_id == project_id if project_id else True)]
    unresolved = []
    for b in blockers:
        meta = getattr(b, "metadata", None) or {}
        if meta.get("blocker_status", "open") not in ("resolved", "obsolete"):
            unresolved.append(b)
    leave = [m for m in items if m.state_type == "member_status"]

    high_risk_tasks = []
    for dl in deadlines:
        if not deadline_is_imminent(dl.current_value, 3):
            continue
        # Find tasks near this deadline
        related = [m for m in items
                   if m.state_type == "next_step" and m.owner
                   and (dl.owner and m.owner in (dl.owner or ""))]
        for t in related:
            has_blocker = any(b.owner == t.owner for b in unresolved)
            on_leave = any(l.owner == t.owner for l in leave)
            if has_blocker or on_leave:
                high_risk_tasks.append((t, has_blocker, on_leave))

    if not high_risk_tasks:
        return []

    parts = ["高风险任务预警："]
    for t, has_b, has_l in high_risk_tasks[:5]:
        risk_tags = []
        if has_b:
            risk_tags.append("有阻塞")
        if has_l:
            risk_tags.append("负责人请假")
        parts.append(
            f"- {t.current_value[:60]}（{t.owner}）{' + '.join(risk_tags)}"
        )

    return [PatternMemoryItem(
        pattern_type="deadline_risk_score",
        scope=f"project:{project_id}",
        summary="\n".join(parts),
        time_window="3d",
        confidence=0.75,
        source_memory_ids=[t.memory_id for t, _, _ in high_risk_tasks[:5]],
        evidence_refs=[_build_evidence(t) for t, _, _ in high_risk_tasks[:3]],
    )]


def generate_all_patterns(
    items: list[MemoryItem],
    project_id: str = "",
    owner_name: str = "",
) -> list[PatternMemoryItem]:
    """Generate all pattern types from existing MemoryItems."""
    patterns = []
    patterns.extend(generate_handoff_risk(items, project_id, owner_name))
    patterns.extend(generate_dependency_blockers(items, project_id))
    patterns.extend(generate_domain_responsibility(items, project_id, owner_name))
    patterns.extend(generate_blocker_hotspot(items, project_id))
    patterns.extend(generate_stale_task(items, project_id))
    patterns.extend(generate_deadline_risk_score(items, project_id))
    return patterns


# ── helpers ────────────────────────────────────────────────────

def _build_evidence(item: MemoryItem) -> dict:
    ref = item.source_refs[0] if item.source_refs else None
    return {
        "memory_id": item.memory_id,
        "state_type": item.state_type,
        "value": item.current_value[:80],
        "sender": ref.sender_name if ref else "",
        "source_url": ref.source_url if ref else "",
    }
