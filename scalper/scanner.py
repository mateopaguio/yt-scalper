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

from . import patterns, prices, schemas, signal_log

UNDERLYINGS = ("SPY", "QQQ")


def scan_underlyings(tickers: tuple[str, ...] = UNDERLYINGS) -> dict[str, Any]:
    """Scan, and report WHY nothing fired when nothing fires.

    "0 setups, no errors" is ambiguous: it can mean the market offered
    no setup today, or that the bars lack the high/low/volume the
    detectors need and every pattern quietly stood down. Those need
    telling apart, so each ticker reports its bar count, which fields
    are present, and how recently each pattern last fired.
    """
    hits: list[dict] = []
    errors: list[dict] = []
    diag: list[dict] = []

    for tk in tickers:
        rows = prices.fetch_history(tk)
        if not rows or len(rows) < 80:
            errors.append({"ticker": tk,
                           "reason": "no price history available"
                                     if not rows else
                                     f"insufficient history ({len(rows)} bars)"})
            continue

        # Boundary check. A close-only series makes every detector stand
        # down, which looked identical to a quiet market.
        shape = schemas.check_price_bars(rows, need_ohlc=True)
        if not shape["usable"]:
            errors.append({"ticker": tk, "reason": shape["reason"]})
            continue
        missing = shape["missing_ohlc"]
        for h in patterns.scan(rows):
            hits.append({**h, "ticker": tk})

        # When did each pattern last fire? If the answer is "never in
        # 1000 bars", the detector is broken, not the market quiet.
        last_fired = {}
        for fn in patterns.DETECTORS:
            seen = None
            for i in range(len(rows) - 1, max(79, len(rows) - 400), -1):
                if fn(rows, i):
                    seen = str(rows[i]["date"])
                    break
            last_fired[fn.__name__] = seen

        diag.append({
            "ticker": tk,
            "bars": len(rows),
            "range": f"{rows[0]['date']} .. {rows[-1]['date']}",
            "missing_fields": missing,
            "usable": not missing,
            "last_fired": last_fired,
            "feed": last.get("feed"),
        })

    return {"hits": hits, "errors": errors, "scanned": list(tickers),
            "diagnostics": diag}


def run(record: bool = True) -> dict[str, Any]:
    out = scan_underlyings()
    if record and out["hits"]:
        out["recorded"] = signal_log.record_pattern_signals(out["hits"])
    out["note"] = ("Paper signals on the underlying. Daily bars only, so no "
                   "intraday setup is attempted. No option is priced.")
    return out
