"""Deterministic relative-date normalization."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date
    source: str

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "source": self.source}


def normalize_dates(text: str, *, today: date | None = None) -> DateRange | None:
    anchor = today or date.today()
    low = " ".join(text.lower().split())
    explicit = re.search(r"\b(\d{4}-\d{2}-\d{2})(?:\s+(?:to|through|until)\s+(\d{4}-\d{2}-\d{2}))?\b", low)
    if explicit:
        start = date.fromisoformat(explicit.group(1))
        end = date.fromisoformat(explicit.group(2) or explicit.group(1))
        if end < start:
            raise ValueError("date range ends before it starts")
        return DateRange(start, end, explicit.group(0))
    if "today" in low:
        return DateRange(anchor, anchor, "today")
    if "tomorrow" in low:
        value = anchor + timedelta(days=1)
        return DateRange(value, value, "tomorrow")
    if "next week" in low:
        start = anchor + timedelta(days=(7 - anchor.weekday()))
        return DateRange(start, start + timedelta(days=6), "next week")
    if "this week" in low:
        start = anchor - timedelta(days=anchor.weekday())
        return DateRange(start, start + timedelta(days=6), "this week")
    month = re.search(r"\bnext month\b", low)
    if month:
        year, number = (anchor.year + 1, 1) if anchor.month == 12 else (anchor.year, anchor.month + 1)
        return DateRange(date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1]), "next month")
    return None
