"""Whether a result means anything, stated so it cannot be overread.

Every number here exists to make a claim harder to make, not easier.
A win rate without a sample size, a return without an interval, or a
backtest scored on the same data that chose it will all flatter a
strategy that has nothing.

Four separate concerns, deliberately not merged:

  state()        where each signal actually stands - resolved,
                 unresolved, stale, unpriceable or failed. Lumping
                 these together is how "0 wins" comes to mean five
                 different things at once.

  wilson()       an interval for a rate, which stays honest at small n
                 where the normal approximation does not.

  bootstrap()    an interval for a mean, assuming nothing about shape.

  split()        in-sample and out-of-sample by date, never at random:
                 shuffling price data lets the future inform the past.

Nothing here decides whether to trade.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

# A signal's standing.
RESOLVED = "resolved"          # measured against real prices
UNRESOLVED = "unresolved"      # too recent; the horizon has not elapsed
STALE = "stale"                # old enough to resolve, and still has not
UNPRICEABLE = "unpriceable"    # no usable price data for it
FAILED = "failed"              # the measurement itself errored

STALE_AFTER_DAYS = 10
MIN_FOR_A_RATE = 30            # below this, report the count, not a rate
MIN_FOR_A_CLAIM = 100          # below this, no claim of edge, ever


def state(signal: dict, horizon: str = "1",
          now: datetime | None = None) -> dict[str, Any]:
    """Where one signal stands, and why."""
    now = now or datetime.now(timezone.utc)
    outcome = signal.get("outcome")

    created = signal.get("generated_at") or ""
    try:
        age_days = (now - datetime.fromisoformat(created)).days
    except (TypeError, ValueError):
        age_days = None

    if not isinstance(outcome, dict):
        if age_days is not None and age_days > STALE_AFTER_DAYS:
            return {"state": STALE, "why": f"never measured, {age_days}d old",
                    "age_days": age_days}
        return {"state": UNRESOLVED, "why": "not measured yet",
                "age_days": age_days}

    if outcome.get("error"):
        err = str(outcome["error"])
        kind = UNPRICEABLE if "price" in err.lower() else FAILED
        return {"state": kind, "why": err[:80], "age_days": age_days}

    if outcome.get("skipped"):
        return {"state": UNPRICEABLE,
                "why": str(outcome.get("reason", "skipped"))[:80],
                "age_days": age_days}

    got = ((outcome.get("returns") or {}).get(horizon) or {})
    if got.get("return_pct") is not None:
        return {"state": RESOLVED, "why": "", "age_days": age_days,
                "return_pct": got["return_pct"],
                "relative": (outcome.get("relative") or {}).get(horizon) or {}}

    if age_days is not None and age_days > max(int(horizon), STALE_AFTER_DAYS):
        return {"state": STALE,
                "why": f"{age_days}d old, horizon {horizon}d never filled",
                "age_days": age_days}
    return {"state": UNRESOLVED,
            "why": f"horizon {horizon}d has not elapsed", "age_days": age_days}


def census(signals: list[dict], horizon: str = "1",
           now: datetime | None = None) -> dict[str, Any]:
    """How many signals are in each state. Shown separately, always."""
    buckets: dict[str, list] = {k: [] for k in
                                (RESOLVED, UNRESOLVED, STALE,
                                 UNPRICEABLE, FAILED)}
    for s in signals:
        v = state(s, horizon, now)
        buckets[v["state"]].append({"ticker": s.get("ticker"), **v})
    return {
        "counts": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
        "total": len(signals),
        "note": ("Only `resolved` signals carry a measured return. The "
                 "others are not zeros and must never be averaged in."),
    }


def wilson(successes: int, n: int, z: float = 1.96) -> dict[str, Any]:
    """Confidence interval for a rate. Honest at small n."""
    if n <= 0:
        return {"rate": None, "low": None, "high": None, "n": 0,
                "reportable": False,
                "note": "no resolved observations"}
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return {
        "rate": round(100 * p, 1),
        "low": round(100 * max(0.0, centre - half), 1),
        "high": round(100 * min(1.0, centre + half), 1),
        "n": n,
        "reportable": n >= MIN_FOR_A_RATE,
        "note": ("" if n >= MIN_FOR_A_RATE else
                 f"n={n} is below {MIN_FOR_A_RATE}; the interval is so wide "
                 "that the rate carries no information"),
    }


def bootstrap(values: list[float], iterations: int = 2000,
              seed: int = 7, z: float = 0.95) -> dict[str, Any]:
    """Percentile interval for a mean. Assumes nothing about shape."""
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n < 2:
        return {"mean": round(vals[0], 3) if vals else None,
                "low": None, "high": None, "n": n, "reportable": False,
                "note": "too few observations to form an interval"}
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        means.append(sum(rng.choice(vals) for _ in range(n)) / n)
    means.sort()
    lo = means[int((1 - z) / 2 * iterations)]
    hi = means[int((1 + z) / 2 * iterations) - 1]
    mean = sum(vals) / n
    straddles = lo <= 0 <= hi
    return {
        "mean": round(mean, 3), "low": round(lo, 3), "high": round(hi, 3),
        "n": n, "reportable": n >= MIN_FOR_A_RATE,
        "straddles_zero": straddles,
        "note": ("the interval includes zero, so this is consistent with "
                 "no effect" if straddles else ""),
    }


def split(signals: list[dict], fraction: float = 0.7) -> dict[str, Any]:
    """In-sample / out-of-sample, by DATE.

    A random split lets a later bar inform an earlier one through the
    same price series, which is how a strategy scores well on data it
    has effectively already seen.
    """
    dated = [s for s in signals if s.get("generated_at")]
    dated.sort(key=lambda s: s["generated_at"])
    if len(dated) < 4:
        return {"in_sample": dated, "out_of_sample": [], "cut": None,
                "note": "too few dated signals to split"}
    cut = int(len(dated) * fraction)
    return {
        "in_sample": dated[:cut],
        "out_of_sample": dated[cut:],
        "cut": dated[cut]["generated_at"][:10] if cut < len(dated) else None,
        "note": ("Split by date, never at random: shuffling price-derived "
                 "observations lets the future inform the past."),
    }


def overlap(signals: list[dict], hold_days: int = 5) -> dict[str, Any]:
    """Positions open at the same time in the same name.

    Overlapping holds in one ticker are not independent observations.
    Averaging them as if they were understates the variance and makes
    a thin result look robust.
    """
    by_ticker: dict[str, list] = {}
    for s in signals:
        d = (s.get("generated_at") or "")[:10]
        if d:
            by_ticker.setdefault(s.get("ticker") or "?", []).append(d)

    clashes = []
    for tk, days in by_ticker.items():
        days.sort()
        for a, b in zip(days, days[1:]):
            try:
                gap = (date.fromisoformat(b) - date.fromisoformat(a)).days
            except ValueError:
                continue
            if gap < hold_days:
                clashes.append({"ticker": tk, "first": a, "second": b,
                                "days_apart": gap})
    return {
        "overlapping": len(clashes),
        "examples": clashes[:8],
        "independent_estimate": max(0, len(signals) - len(clashes)),
        "note": ("Signals closer together than the hold period share price "
                 "bars, so they are not independent observations."),
    }


def verdict(resolved: list[float], census_counts: dict,
            conc: dict | None = None) -> dict[str, Any]:
    """The one sentence allowed to be said out loud."""
    n = len(resolved)

    # Calendar concentration outranks sample size. A thousand signals
    # from one session is still one session.
    if conc and conc.get("concentrated"):
        return {
            "claim": (f"No claim. {n} resolved observation(s), but they span "
                      f"only {conc['distinct_days']} market day(s) - one "
                      "regime observed many times, not many trials. The "
                      "effective sample is the number of days."),
            "resolved": n, "census": census_counts,
            "effective_n": conc.get("effective_n"),
            "is_edge_claim": False,
        }

    if n < MIN_FOR_A_RATE:
        claim = (f"No claim. {n} resolved observation(s); at least "
                 f"{MIN_FOR_A_RATE} are needed before a rate means anything.")
    elif n < MIN_FOR_A_CLAIM:
        claim = (f"Indicative only. {n} resolved; {MIN_FOR_A_CLAIM}+ before "
                 "any claim of edge.")
    else:
        bs = bootstrap(resolved)
        claim = ("No edge demonstrated: the interval around the mean "
                 "includes zero." if bs["straddles_zero"]
                 else f"Mean {bs['mean']}% with interval "
                      f"[{bs['low']}, {bs['high']}] excluding zero. This is "
                      "a measurement, not a recommendation.")
    return {"claim": claim, "resolved": n, "census": census_counts,
            "is_edge_claim": False}


def concentration(signals: list[dict]) -> dict[str, Any]:
    """How many DISTINCT market days the observations actually span.

    The most overconfident number this system can produce. Forty-four
    signals that all entered on the same session are not forty-four
    trials: they are one trial, observed forty-four times through
    different tickers. Whatever the market did that day is in every
    one of them, so a bootstrap over them assumes an independence that
    does not exist and returns an interval several times too narrow.

    Benchmark-relative returns remove the index move but not the
    regime: on a day when momentum works, every momentum name beats
    SPY together.
    """
    days: dict[str, int] = {}
    for s in signals:
        o = s.get("outcome")
        d = (o or {}).get("entry_date") if isinstance(o, dict) else None
        if d:
            days[str(d)[:10]] = days.get(str(d)[:10], 0) + 1

    n = sum(days.values())
    distinct = len(days)
    biggest = max(days.values()) if days else 0
    share = (biggest / n) if n else 0.0

    return {
        "observations": n,
        "distinct_days": distinct,
        "by_day": days,
        "largest_day_share": round(share, 3),
        # For a same-day cross-section the market factor dominates, so
        # the honest unit of observation is the day, not the signal.
        "effective_n": distinct,
        "concentrated": bool(n >= 10 and distinct <= 3),
        "note": ("Observations clustered on a few sessions share one "
                 "market regime. The effective sample size is closer to "
                 "the number of distinct days than to the number of "
                 "signals."),
    }
