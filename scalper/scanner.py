"""Daily setup scan on liquid ETFs.

Deliberately narrow: SPY and QQQ only. Liquid enough that a delayed
daily feed is not fatal, and widely enough traded that a pattern with a
real edge there would be surprising - which is the point of testing it
honestly rather than hunting the universe for something that fits.

Produces paper signals on the underlying. Not advice, not an options
recommendation, and no option is priced.
"""
from __future__ import annotations

from typing import Any

from . import patterns, prices, signal_log

UNDERLYINGS = ("SPY", "QQQ")


def scan_underlyings(tickers: tuple[str, ...] = UNDERLYINGS) -> dict[str, Any]:
    hits: list[dict] = []
    errors: list[dict] = []
    for tk in tickers:
        rows = prices.fetch_history(tk)
        if not rows or len(rows) < 80:
            errors.append({"ticker": tk,
                           "reason": "no price history available"
                                     if not rows else "insufficient history"})
            continue
        for h in patterns.scan(rows):
            hits.append({**h, "ticker": tk})
    return {"hits": hits, "errors": errors, "scanned": list(tickers)}


def run(record: bool = True) -> dict[str, Any]:
    out = scan_underlyings()
    if record and out["hits"]:
        out["recorded"] = signal_log.record_pattern_signals(out["hits"])
    out["note"] = ("Paper signals on the underlying. Daily bars only, so no "
                   "intraday setup is attempted. No option is priced.")
    return out
