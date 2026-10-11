"""Strict, pure parsing of the specification's closed date intervals."""
import calendar
from datetime import date
import re
from typing import Optional, Tuple

Interval = Tuple[date, date]


def parse_anchor(value: Optional[str]) -> Optional[Interval]:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"[0-9]{4}", value):
            year = int(value)
            return date(year, 1, 1), date(year, 12, 31)
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            year, month = map(int, value.split("-"))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            day = date.fromisoformat(value)
            return day, day
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            start, end = map(date.fromisoformat, value.split("/"))
            return (start, end) if start <= end else None
    except ValueError:
        return None
    return None


def covers(period: Optional[str], anchor: Optional[str]) -> bool:
    outer, inner = parse_anchor(period), parse_anchor(anchor)
    return bool(outer and inner and outer[0] <= inner[0] and outer[1] >= inner[1])

