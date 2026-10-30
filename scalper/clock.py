"""Eastern time, for everything the user reads.

Timestamps are STORED in UTC and always will be: it is unambiguous, it
sorts correctly, and it does not jump an hour twice a year. Only the
display is converted. Nothing here writes a converted time back into
the data.

Eastern rather than the machine's local zone, because this is a US
equities tool: the session runs 09:30-16:00 ET, earnings land before
the open or after the close ET, and a scheduled date on a filing is a
US market date. A reader in another zone still wants to see market
time, not their own.

Market-data date arithmetic (which bar an entry falls on, the
look-ahead boundary in patterns/paper/outcomes) deliberately does NOT
use this module. Those rules are tested against recorded results, and
shifting their day boundary would silently change every backtest.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                       # noqa: BLE001
    # No tzdata on this machine. Showing UTC labelled UTC is wrong but
    # honest; showing UTC labelled ET would not be.
    ET = None


def _parse(value) -> datetime | None:
    """Accept a datetime or an ISO string; return an aware datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_et(value) -> datetime | None:
    dt = _parse(value)
    if dt is None:
        return None
    return dt.astimezone(ET) if ET else dt.astimezone(timezone.utc)


def now() -> datetime:
    """Current time, Eastern."""
    return datetime.now(timezone.utc).astimezone(ET or timezone.utc)


def today() -> date:
    """Today's date in Eastern.

    Not the UTC date. After 20:00 ET the UTC calendar has already
    rolled over, so a UTC 'today' drops the current trading day from
    anything keyed to it.
    """
    return now().date()


def stamp(value, fmt: str = "%b %d, %-I:%M %p") -> str:
    """A timestamp for a human, with the zone named."""
    dt = to_et(value)
    if dt is None:
        return ""
    try:
        text = dt.strftime(fmt)
    except ValueError:                                  # %-I is not portable
        text = dt.strftime(fmt.replace("%-I", "%I")).replace(" 0", " ")
    return f"{text} {dt.strftime('%Z') or 'UTC'}"


def day(value, fmt: str = "%b %d") -> str:
    """A bare calendar date, no zone conversion and no zone label.

    A scheduled date like '2026-10-30' is a market date, not an
    instant; converting it would move it.
    """
    if isinstance(value, str) and len(value.strip()) == 10:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").strftime(fmt)
        except ValueError:
            return value
    dt = to_et(value)
    return dt.strftime(fmt) if dt else (value or "")
