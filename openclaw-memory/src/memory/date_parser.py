"""Parse Chinese relative deadline expressions into concrete dates.

V1.14: Deterministic date parsing for trigger rule 3 (deadline proximity check).
Covers common Feishu chat patterns without requiring LLM or external NLP libraries.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# 中文星期名 → Python weekday (Monday=0, Sunday=6)
_WEEKDAY_MAP: dict[str, int] = {
    "周一": 0, "星期一": 0,
    "周二": 1, "星期二": 1,
    "周三": 2, "星期三": 2,
    "周四": 3, "星期四": 3,
    "周五": 4, "星期五": 4,
    "周六": 5, "星期六": 5,
    "周日": 6, "星期天": 6, "星期七": 6, "周天": 6,
}

# 数字 → int
_DIGIT_MAP: dict[str, int] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def parse_relative_deadline(text: str, reference: date | None = None) -> date | None:
    """Parse a Chinese relative time expression into a concrete date.

    Args:
        text: The deadline value extracted from memory (e.g. "周五", "下周三", "5月10日").
        reference: The reference date for relative expressions. Defaults to today.

    Returns:
        A concrete date, or None if no recognized time expression was found.
    """
    ref = reference or date.today()
    text = text.strip()

    # ── Pattern 1: "明天" / "后天" / "今天" ──
    if text in ("明天", "明晚", "明早"):
        return ref + timedelta(days=1)
    if text in ("后天", "后晚", "后早"):
        return ref + timedelta(days=2)
    if text in ("今天", "今晚", "今早"):
        return ref
    if text in ("昨天",):
        return ref - timedelta(days=1)

    # ── Pattern 2: "N天后" / "N天后" ──
    m = re.match(r"(\d+|[一二两三四五六七八九十])天[后内之]", text)
    if m:
        n = _to_int(m.group(1))
        return ref + timedelta(days=n)

    # ── Pattern 3: "下周X" / "周X" ──
    has_next = text.startswith("下") or text.startswith("下个")
    has_this = text.startswith("这") or text.startswith("这个") or text.startswith("本")

    # Strip prefix and try to match weekday
    clean = text
    for prefix in ("下个", "下", "这个", "这", "本"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break

    if clean in _WEEKDAY_MAP:
        target_wd = _WEEKDAY_MAP[clean]
        today_wd = ref.weekday()
        diff = target_wd - today_wd

        if has_next:
            # "下周五" → target in next week (diff + 7)
            if diff <= 0:
                diff += 7
            diff += 7
        elif has_this or diff > 0:
            # "本周五" or Friday already this week → this week
            pass
        elif diff == 0:
            # "周五" on a Friday → today (already this week)
            pass
        else:
            # "周五" when today is Saturday → next week
            diff += 7

        return ref + timedelta(days=diff)

    # ── Pattern 4: "M月D日" / "M月D号" ──
    m = re.match(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            result = date(ref.year, month, day)
            if result < ref:
                result = date(ref.year + 1, month, day)
            return result
        except ValueError:
            return None

    # ── Pattern 5: "下周" / "下个月" (vague, return next Monday / 1st) ──
    if text in ("下周", "下星期"):
        days_until_monday = 7 - ref.weekday()
        return ref + timedelta(days=days_until_monday if days_until_monday < 7 else 7)
    if text == "下个月":
        if ref.month == 12:
            return date(ref.year + 1, 1, 1)
        return date(ref.year, ref.month + 1, 1)

    # ── Pattern 6: "周末" / "月底" ──
    if text in ("周末",):
        days_until_saturday = 5 - ref.weekday()
        if days_until_saturday <= 0:
            days_until_saturday += 7
        return ref + timedelta(days=days_until_saturday)

    return None


def deadline_is_imminent(
    deadline_text: str,
    within_days: int = 3,
    reference: date | None = None,
) -> bool:
    """Check if a deadline is within N days of the reference date.

    Args:
        deadline_text: The deadline value from memory.current_value.
        within_days: How many days is considered "imminent" (default 3).
        reference: Reference date (defaults to today).

    Returns:
        True if the deadline can be parsed and is ≤ within_days away.
    """
    ref = reference or date.today()
    dl_date = parse_relative_deadline(deadline_text, ref)
    if dl_date is None:
        return False
    delta = (dl_date - ref).days
    return 0 <= delta <= within_days


def _to_int(s: str) -> int:
    """Convert a Chinese or Arabic digit string to int."""
    if s.isdigit():
        return int(s)
    return _DIGIT_MAP.get(s, 1)
