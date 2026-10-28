#!/usr/bin/env python3
"""Backtest the daily setups against their own buy-and-hold baseline.

    python3 scripts/backtest_patterns.py
    python3 scripts/backtest_patterns.py --tickers SPY,QQQ,IWM

Needs the price feed, so run it on a machine with network access.

Read win_rate_vs_baseline, NOT win_rate. SPY drifts upward, so a
long-only rule wins most of the time regardless of whether it knows
anything. The baseline is every bar's forward return over the same
horizons; the edge is the difference, and a small positive difference
in-sample is not evidence of anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import patterns, prices, scanner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(scanner.UNDERLYINGS))
    ap.add_argument("--json", action="store_true", help="raw JSON output")
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    allout = {}

    for tk in tickers:
        # Execution needs the tape, not the adjusted series.
        rows = prices.fetch_history(tk, series=prices.AS_TRADED)
        if not rows or len(rows) < 120:
            print(f"{tk}: no usable price history "
                  f"({0 if not rows else len(rows)} bars) - skipped")
            continue

        res = patterns.backtest(rows, ticker=tk)
        allout[tk] = res
        if args.json:
            continue

        print(f"\n{'=' * 66}")
        print(f"{tk}   {res['bars']} daily bars   "
              f"{rows[0]['date']} .. {rows[-1]['date']}")
        print("=" * 66)

        base = res["baseline"]
        print("\n  buy-and-hold baseline (what any long rule must beat)")
        for h in sorted(base, key=int):
            b = base[h]
            if b["win_rate"] is None:
                continue
            print(f"    {h:>3}d  win {b['win_rate']:5.1f}%   "
                  f"mean {b['mean_pct']:+.3f}%   n={b['n']}")

        for name, p in res["patterns"].items():
            print(f"\n  {name}   {p['signals']} signals "
                  f"({p['first']} .. {p['last']})" if p["signals"]
                  else f"\n  {name}   no signals")
            if not p["signals"]:
                continue
            print(f"    {'':>3}  {'win':>6} {'vs base':>9}   "
                  f"{'mean':>8} {'vs base':>9}   n")
            for h in sorted(p["horizons"], key=int):
                r = p["horizons"][h]
                if r["win_rate"] is None:
                    continue
                flag = ""
                if r["win_rate_vs_baseline"] is not None:
                    if r["win_rate_vs_baseline"] > 3 and r["n"] >= 30:
                        flag = "  <- worth a look"
                    elif r["win_rate_vs_baseline"] < -3:
                        flag = "  <- worse than doing nothing"
                print(f"    {h:>3}d  {r['win_rate']:5.1f}% "
                      f"{r['win_rate_vs_baseline']:+8.1f}   "
                      f"{r['mean_pct']:+7.3f}% {r['mean_vs_baseline']:+8.3f}   "
                      f"{r['n']}{flag}")

        print(f"\n  {res['caveat']}")

    if args.json:
        print(json.dumps(allout, indent=1, default=str))
    elif not allout:
        print("\nNo data fetched. The price feed was unreachable from here.")
        return 1
    else:
        print("\nIn-sample, before costs. A result that only works in-sample "
              "is not a result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
