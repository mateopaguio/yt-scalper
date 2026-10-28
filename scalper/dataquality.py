"""Per-ticker verdicts on price data, and why a fetch failed.

Two jobs that used to be one undifferentiated "no price data":

  classify_error()  separates a wrong key from a rate limit from an
                    outage from an empty-but-valid answer. They need
                    different responses - retrying a 401 forever is
                    pointless, and giving up on a 429 is premature.

  assess()          says whether the bars we DID get are usable, per
                    ticker, with the reason attached.

Nothing here repairs data. A bar with a missing low is dropped and
counted, never filled from its neighbours: an invented price is worse
than a gap, because a gap is visible and an invention is not.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import market_calendar as mcal

# --- error classes ---------------------------------------------------
AUTH = "auth"                 # key wrong, missing, or not entitled
RATE_LIMIT = "rate_limit"     # back off and retry
UNREACHABLE = "unreachable"   # DNS, TLS, timeout, connection refused
EMPTY = "empty"               # provider answered, had nothing
MALFORMED = "malformed"       # answered with something unusable
NOT_CONFIGURED = "not_configured"
# The adapter returned nothing and did not say why. Distinct from EMPTY,
# which asserts the provider answered with no bars - a claim we can only
# make about adapters that report back.
UNKNOWN = "unknown"
OK = "ok"

RETRYABLE = frozenset({RATE_LIMIT, UNREACHABLE})

# --- quality verdicts ------------------------------------------------
USABLE = "usable"
DEGRADED = "degraded"         # usable, with a caveat worth showing
UNUSABLE = "unusable"

MIN_BARS = 60                 # below this no pattern rule is meaningful
MAX_GAP_RATIO = 0.05          # >5% of expected sessions missing
SPLIT_MOVE = 0.35             # a single-day move this large smells of a split


def classify_error(status: int | None = None, exc: BaseException | None = None,
                   rows: list | None = None) -> str:
    """One of the classes above. Never guesses OK."""
    if status in (401, 403):
        return AUTH
    if status == 429:
        return RATE_LIMIT
    if status is not None and status >= 500:
        return UNREACHABLE
    if exc is not None:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "timeout" in name or "timeout" in text:
            return UNREACHABLE
        if any(w in name for w in ("connection", "ssl", "dns", "proxy")):
            return UNREACHABLE
        if any(w in text for w in ("connection", "resolve", "unreachable",
                                   "tunnel", "refused", "certificate")):
            return UNREACHABLE
        if "json" in name or "decode" in name or "value" in name:
            return MALFORMED
        return UNREACHABLE
    if rows is not None and not rows:
        return EMPTY
    return OK


def ohlc_valid(row: dict) -> bool:
    try:
        o, h, l, c = (float(row["open"]), float(row["high"]),
                      float(row["low"]), float(row["close"]))
    except (KeyError, TypeError, ValueError):
        return False
    if min(o, h, l, c) <= 0:
        return False
    # The invariant that defines a bar: the high is the highest thing
    # that happened and the low is the lowest.
    return l <= min(o, c) and h >= max(o, c) and l <= h


def assess(ticker: str, rows: list[dict] | None,
           feed: str = "", error: str = "") -> dict[str, Any]:
    """A usability verdict for one ticker's bars."""
    if error and error != OK:
        return {"ticker": ticker, "status": UNUSABLE, "reason": error,
                "detail": _ERROR_TEXT.get(error, error), "bars": 0,
                "feed": feed, "checks": {}}
    if not rows:
        return {"ticker": ticker, "status": UNUSABLE, "reason": EMPTY,
                "detail": "the provider returned no bars", "bars": 0,
                "feed": feed, "checks": {}}

    bad_shape = [r for r in rows if not ohlc_valid(r)]
    clean = [r for r in rows if ohlc_valid(r)]
    notes: list[str] = []

    if not clean:
        return {"ticker": ticker, "status": UNUSABLE, "reason": MALFORMED,
                "detail": f"all {len(rows)} bar(s) failed the OHLC invariant",
                "bars": 0, "feed": feed, "checks": {}}

    dates = sorted(r["date"] for r in clean)
    first, last = dates[0], dates[-1]
    expected = mcal.expected_count(first, last)
    missing = max(0, expected - len(set(dates)))
    gap_ratio = (missing / expected) if expected else 0.0

    # Sessions we have no bar for, excluding holidays and weekends.
    have = set(dates)
    absent = [d for d in mcal.sessions(first, last) if d not in have]

    zero_vol = sum(1 for r in clean if float(r.get("volume") or 0) <= 0)

    # A raw (unadjusted) series shows a split as an overnight halving.
    # Flagging it matters: a 50% "gap down" is a textbook breakdown
    # signal that never happened.
    splits = []
    for prev, cur in zip(clean, clean[1:]):
        try:
            move = (float(cur["close"]) - float(prev["close"])) / float(prev["close"])
        except (ZeroDivisionError, TypeError, ValueError):
            continue
        if abs(move) >= SPLIT_MOVE:
            splits.append({"date": str(cur["date"]), "move_pct": round(move * 100, 1)})

    if bad_shape:
        notes.append(f"{len(bad_shape)} bar(s) dropped: failed the OHLC invariant")
    if missing:
        notes.append(f"{missing} of {expected} session(s) missing")
    if zero_vol:
        notes.append(f"{zero_vol} bar(s) with zero volume")
    if splits:
        notes.append(f"{len(splits)} move(s) >={int(SPLIT_MOVE * 100)}% - "
                     "possible unadjusted split")

    stale_days = (date.today() - last).days
    if stale_days > 5:
        notes.append(f"newest bar is {stale_days} days old")

    status = USABLE
    if len(clean) < MIN_BARS:
        status = UNUSABLE
        notes.append(f"only {len(clean)} bar(s); {MIN_BARS} needed")
    elif gap_ratio > MAX_GAP_RATIO or splits or stale_days > 5 or bad_shape:
        status = DEGRADED

    return {
        "ticker": ticker,
        "status": status,
        "reason": OK if status == USABLE else "quality",
        "detail": "; ".join(notes) or "complete",
        "bars": len(clean),
        "dropped": len(bad_shape),
        "feed": feed,
        "first": str(first),
        "last": str(last),
        "checks": {
            "expected_sessions": expected,
            "missing_sessions": missing,
            "gap_ratio": round(gap_ratio, 4),
            "absent_dates": [str(d) for d in absent[:10]],
            "zero_volume_bars": zero_vol,
            "possible_splits": splits[:5],
            "stale_days": stale_days,
        },
    }


_ERROR_TEXT = {
    AUTH: "the provider rejected the credentials, or the account is not "
          "entitled to this feed",
    RATE_LIMIT: "rate limited; this is retryable",
    UNREACHABLE: "could not reach the provider (network, DNS or TLS)",
    EMPTY: "the provider answered with no bars",
    UNKNOWN: "the adapter returned nothing without reporting a reason",
    MALFORMED: "the provider answered with something unusable",
    NOT_CONFIGURED: "no credentials configured for this provider",
}


def summarise(reports: list[dict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in reports:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "counts": counts,
        "usable": [r["ticker"] for r in reports if r["status"] == USABLE],
        "degraded": [{"ticker": r["ticker"], "detail": r["detail"]}
                     for r in reports if r["status"] == DEGRADED],
        "unusable": [{"ticker": r["ticker"], "detail": r["detail"]}
                     for r in reports if r["status"] == UNUSABLE],
        "note": ("A degraded series is still used, with the caveat shown. "
                 "An unusable one is never silently replaced."),
    }
