#!/usr/bin/env python3
"""Run the setups as paper trades and report what they would have done.

    python3 scripts/paper_trade.py
    python3 scripts/paper_trade.py --tickers SPY,QQQ --equity 10000 --risk 0.01

PAPER ONLY. No broker is contacted, no order is placed, no money moves.
Needs the price feed, so run it where the network works.

Read `costs_as_pct_of_gross`. If costs ate most of the gross profit, the
strategy is a transfer to the broker no matter how good the win rate
looks. That is the number the whole exercise exists to produce.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import patterns, paper, prices, scanner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(scanner.UNDERLYINGS))
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--pattern", default="", help="only this pattern")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rules = paper.Rules(starting_equity=args.equity, risk_per_trade=args.risk,
                        target_r=args.target_r, max_hold_days=args.hold)
    costs = paper.Costs()
    out = {}

    for tk in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        # Execution needs the tape, not the adjusted series.
        rows = prices.fetch_history(tk, series=prices.AS_TRADED)
        if not rows or len(rows) < 120:
            print(f"{tk}: no usable price history - skipped")
            continue

        sigs = patterns.scan_history(rows, tk)
        if args.pattern:
            sigs = [s for s in sigs if s["pattern"] == args.pattern]
        res = paper.simulate(rows, sigs, rules, costs)
        out[tk] = res
        if args.json:
            continue

        print(f"\n{'=' * 64}")
        print(f"{tk}  {len(rows)} bars  {rows[0]['date']} .. {rows[-1]['date']}")
        print(f"{'=' * 64}")
        print(f"  signals found      {len(sigs)}")
        print(f"  trades taken       {res['trades']}  "
              f"(closed {res['closed']}, open {res['still_open']})")
        if not res["closed"]:
            print("  nothing closed - no result to report")
            continue
        print(f"  win rate           {res['win_rate']}%  "
              f"({res['wins']}W / {res['losses']}L)")
        print(f"  net P&L            ${res['net_pnl']:,.2f}  "
              f"({res['return_pct']:+.2f}% on ${res['starting_equity']:,.0f})")
        print(f"  gross P&L          ${res['gross_pnl']:,.2f}")
        print(f"  costs paid         ${res['costs_paid']:,.2f}"
              + (f"   = {res['costs_as_pct_of_gross']}% of gross"
                 if res['costs_as_pct_of_gross'] is not None else ""))
        print(f"  expectancy/trade   ${res['expectancy_per_trade']:,.2f}  "
              f"(avg {res['avg_r']}R)")
        print(f"  profit factor      {res['profit_factor']}")
        print(f"  max drawdown       {res['max_drawdown_pct']}%")
        print(f"  gapped past stop   {res['gapped_through_stop']} trades")
        print(f"  exits              {res['exit_reasons']}")

    if args.json:
        print(json.dumps(out, indent=1, default=str))
    elif out:
        print("\nPaper only - no order placed, no money moved.")
        print("Options on these same signals would cost far more and are not")
        print("simulated; no chain data is configured.")
    else:
        print("\nNo data fetched. The price feed was unreachable from here.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
