"""Two daily technical setups on liquid ETFs, defined before testing.

SCOPE, HONESTLY
---------------
The free feed gives DAILY bars. Opening-range breakouts, liquidity
sweeps and anything else intraday cannot be detected from a daily
candle, so they are not attempted rather than approximated.

These produce PAPER SIGNALS ON THE UNDERLYING. They are not options
recommendations. Measuring what an option would have cost needs chain
data - bid/ask, open interest, implied vol - and none is configured.
Inventing a spread would defeat the entire purpose of measuring
execution cost, so option P&L is simply not reported.

NO LOOK-AHEAD
-------------
A setup is detected from COMPLETED bars only, using data up to and
including bar t. Entry is the close of bar t+1, never bar t's own
close: that close is not knowable until the session ends, and the
scanner runs against the last completed bar. Every window below is
backward-looking.

NOT TUNED
---------
20/50-day averages, a 20-day range, 1.2x volume. Conventional values,
fixed in advance, never optimised against the sample. A threshold
chosen because it scored well is a threshold that will not survive out
of sample. If these need tuning to work, they do not work.

THE BASELINE IS THE WHOLE POINT
-------------------------------
SPY drifts upward, so any long-only rule wins most of the time. A 58%
hit rate means nothing unless buy-and-hold over the same horizons is
below it. backtest() always reports the baseline alongside, and the
only number worth reading is the difference.
"""
from __future__ import annotations

from typing import Any

LONG = "long"

# Fixed, conventional. Do not tune these to improve a result.
TREND_FAST = 20
TREND_SLOW = 50
SLOPE_LOOKBACK = 10
PULLBACK_TOLERANCE = 0.01      # within 1% of the 20-day average
PULLBACK_WINDOW = 3            # pullback must be recent
RANGE_WINDOW = 20
RANGE_MAX_WIDTH = 0.06         # 20-day range under 6% = contraction
VOLUME_MULTIPLE = 1.2


def _sma(rows: list[dict], idx: int, n: int, key: str = "close") -> float | None:
    if idx + 1 < n:
        return None
    window = rows[idx - n + 1: idx + 1]
    vals = [r.get(key) for r in window if r.get(key) is not None]
    return sum(vals) / len(vals) if len(vals) == n else None


def pullback_in_uptrend(rows: list[dict], i: int) -> dict | None:
    """Uptrend, a dip to the short average, then a close back up.

    Uses only bars <= i.
    """
    if i < TREND_SLOW + SLOPE_LOOKBACK:
        return None
    fast = _sma(rows, i, TREND_FAST)
    slow = _sma(rows, i, TREND_SLOW)
    slow_prev = _sma(rows, i - SLOPE_LOOKBACK, TREND_SLOW)
    if fast is None or slow is None or slow_prev is None:
        return None

    bar = rows[i]
    prev = rows[i - 1]

    if not (bar["close"] > slow and slow > slow_prev):
        return None                      # not an uptrend

    # a recent bar dipped to (or through) the short average
    touched = False
    for j in range(max(0, i - PULLBACK_WINDOW + 1), i + 1):
        f = _sma(rows, j, TREND_FAST)
        if f and rows[j]["low"] <= f * (1 + PULLBACK_TOLERANCE):
            touched = True
            break
    if not touched:
        return None

    if bar["close"] <= prev["high"]:
        return None                      # no reversal confirmation

    return {
        "pattern": "pullback_in_uptrend",
        "direction": LONG,
        "signal_date": bar["date"],
        "signal_close": round(bar["close"], 4),
        "evidence": {
            "sma20": round(fast, 4),
            "sma50": round(slow, 4),
            "sma50_10d_ago": round(slow_prev, 4),
            "closed_above_prior_high": round(prev["high"], 4),
        },
        # mechanical invalidation, stated with the signal rather than after
        "invalidation": round(min(r["low"] for r in rows[i - 2: i + 1]), 4),
    }


def range_breakout(rows: list[dict], i: int) -> dict | None:
    """A tight 20-day range, then a close above it on heavier volume."""
    if i < RANGE_WINDOW + 1:
        return None
    prior = rows[i - RANGE_WINDOW: i]          # excludes bar i
    hi = max(r["high"] for r in prior)
    lo = min(r["low"] for r in prior)
    bar = rows[i]
    if bar["close"] <= 0 or hi <= 0:
        return None

    width = (hi - lo) / bar["close"]
    if width > RANGE_MAX_WIDTH:
        return None                      # not a contraction
    if bar["close"] <= hi:
        return None                      # no breakout

    vols = [r.get("volume") or 0 for r in prior]
    avg_vol = sum(vols) / len(vols) if vols else 0
    bar_vol = bar.get("volume") or 0
    if avg_vol > 0 and bar_vol < avg_vol * VOLUME_MULTIPLE:
        return None                      # breakout without participation

    return {
        "pattern": "range_breakout",
        "direction": LONG,
        "signal_date": bar["date"],
        "signal_close": round(bar["close"], 4),
        "evidence": {
            "range_high": round(hi, 4),
            "range_low": round(lo, 4),
            "range_width_pct": round(width * 100, 2),
            "volume_vs_avg": round(bar_vol / avg_vol, 2) if avg_vol else None,
        },
        "invalidation": round(lo, 4),
    }


DETECTORS = (pullback_in_uptrend, range_breakout)


def scan(rows: list[dict], at: int | None = None) -> list[dict]:
    """Setups on the last completed bar (or bar `at`)."""
    if not rows:
        return []
    i = len(rows) - 1 if at is None else at
    out = []
    for fn in DETECTORS:
        hit = fn(rows, i)
        if hit:
            out.append(hit)
    return out


def backtest(rows: list[dict], horizons: tuple[int, ...] = (1, 5, 10, 20),
             ticker: str = "") -> dict[str, Any]:
    """Run the detectors across history and compare against buy-and-hold.

    Entry is the close of the bar AFTER the signal bar. The baseline is
    every bar's forward return over the same horizons, which is what a
    long-only rule on a drifting index has to beat to mean anything.
    """
    results: dict[str, Any] = {"ticker": ticker, "bars": len(rows), "patterns": {}}

    # Baseline first: forward returns from every eligible bar.
    base: dict[str, list[float]] = {str(h): [] for h in horizons}
    for i in range(len(rows) - 1):
        entry = rows[i + 1]["close"]
        if entry <= 0:
            continue
        for h in horizons:
            j = i + 1 + h
            if j < len(rows):
                base[str(h)].append((rows[j]["close"] / entry - 1) * 100)

    results["baseline"] = {
        h: {
            "n": len(v),
            "win_rate": round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None,
            "mean_pct": round(sum(v) / len(v), 3) if v else None,
        } for h, v in base.items()
    }

    for fn in DETECTORS:
        name = fn.__name__
        rets: dict[str, list[float]] = {str(h): [] for h in horizons}
        dates = []
        for i in range(len(rows) - 1):
            if not fn(rows, i):
                continue
            entry = rows[i + 1]["close"]       # next bar - no look-ahead
            if entry <= 0:
                continue
            dates.append(rows[i]["date"])
            for h in horizons:
                j = i + 1 + h
                if j < len(rows):
                    rets[str(h)].append((rows[j]["close"] / entry - 1) * 100)

        per_h = {}
        for h, v in rets.items():
            b = results["baseline"][h]
            wr = round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None
            mean = round(sum(v) / len(v), 3) if v else None
            per_h[h] = {
                "n": len(v),
                "win_rate": wr,
                "mean_pct": mean,
                # the only numbers that matter
                "win_rate_vs_baseline": (round(wr - b["win_rate"], 1)
                                         if wr is not None and b["win_rate"] is not None
                                         else None),
                "mean_vs_baseline": (round(mean - b["mean_pct"], 3)
                                     if mean is not None and b["mean_pct"] is not None
                                     else None),
            }
        results["patterns"][name] = {
            "signals": len(dates),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "horizons": per_h,
        }

    results["caveat"] = (
        "Win rate alone is meaningless on a drifting index: compare "
        "win_rate_vs_baseline. This is in-sample across the whole history "
        "and no costs are deducted, so treat it as a filter for ideas that "
        "are obviously not working, not as evidence that one is."
    )
    return results
