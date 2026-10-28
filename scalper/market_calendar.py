"""Which days the US equity market was actually open.

Needed to tell a missing bar from a day with no trading. Without it, a
holiday looks exactly like a provider that dropped data, and the only
way to tell them apart is to know the calendar.

Computed, not fetched: the NYSE holiday rules are fixed and public, so
a dependency and a network call would buy nothing. Early closes are
ignored on purpose - a half session is still a session, and we only
ask whether a daily bar should exist.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 \
        else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A holiday on Saturday is taken Friday; on Sunday, Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _good_friday(year: int) -> date:
    # Anonymous Gregorian algorithm, then back up two days from Easter.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month, day = divmod(h + l - 7 * m + 90, 25)
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day) - timedelta(days=2)


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset:
    out = {
        _observed(date(year, 1, 1)),                    # New Year's Day
        _nth_weekday(year, 1, 0, 3),                    # MLK Day
        _nth_weekday(year, 2, 0, 3),                    # Washington's Birthday
        _good_friday(year),
        _last_weekday(year, 5, 0),                      # Memorial Day
        _observed(date(year, 7, 4)),                    # Independence Day
        _nth_weekday(year, 9, 0, 1),                    # Labor Day
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving
        _observed(date(year, 12, 25)),                  # Christmas
    }
    if year >= 2022:                                    # Juneteenth, from 2022
        out.add(_observed(date(year, 6, 19)))
    return frozenset(out)


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def sessions(start: date, end: date) -> list[date]:
    """Every trading day in [start, end]."""
    out, cur = [], start
    while cur <= end:
        if is_session(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def expected_count(start: date, end: date) -> int:
    return len(sessions(start, end))
