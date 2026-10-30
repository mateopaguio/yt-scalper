#!/usr/bin/env python3
"""Can you afford this contract, and what does it cost you if it dies?

    python3 scripts/size_position.py --account 50 --price 0.45
    python3 scripts/size_position.py --account 5000 --price 2.10 \
            --bid 2.05 --ask 2.15 --contracts 2 --strike 600

Sizing arithmetic only. It does NOT say whether to buy anything: that
needs an edge this system has not demonstrated and chain data that is
not configured. Read the contract price off your broker and type it in.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import sizing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=float, required=True, help="account equity in $")
    ap.add_argument("--price", type=float, required=True,
                    help="contract price per share, e.g. 0.45 (not 45)")
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--bid", type=float)
    ap.add_argument("--ask", type=float)
    ap.add_argument("--strike", type=float)
    ap.add_argument("--put", action="store_true")
    ap.add_argument("--risk", type=float, default=sizing.MAX_RISK_PCT,
                    help="max fraction of account to risk (default 0.02)")
    ap.add_argument("--commission", type=float, default=0.0)
    args = ap.parse_args()

    acct = sizing.Account(equity=args.account, max_risk_pct=args.risk)
    r = sizing.assess(acct, args.price, args.contracts,
                      commission_per_contract=args.commission,
                      bid=args.bid, ask=args.ask)
    if r.get("error"):
        print(r["error"])
        return 1

    print(f"\n  account            ${args.account:,.2f}")
    print(f"  risk budget        ${r['risk_budget']:,.2f}  "
          f"({args.risk * 100:.0f}% of account)")
    print(f"\n  {args.contracts} contract(s) @ ${args.price:.2f}")
    print(f"  total cost         ${r['total_cost']:,.2f}   "
          f"({r['pct_of_account']:.1f}% of the account)")
    print(f"  max loss           ${r['max_loss']:,.2f}   "
          "(a long option's whole premium, and it happens)")
    if r["spread_pct_round_trip"] is not None:
        print(f"  round-trip spread  {r['spread_pct_round_trip']}%  "
              f"(${r['spread_cost']:,.2f}) - must move this much to break even")
    if args.strike:
        be = sizing.breakeven(args.strike, args.price,
                              "put" if args.put else "call")
        print(f"  breakeven at expiry ${be:,.2f}")

    print(f"\n  within budget      {r['contracts_within_budget']} contract(s)")
    print(f"  losses survivable  {r['total_losses_survivable']}")
    print(f"  min account for 1  ${sizing.minimum_viable_account(args.price, args.risk):,.0f}")

    if r["flags"]:
        print("\n  flags:")
        for f in r["flags"]:
            print(f"    ! {f}")
    else:
        print("\n  within your stated risk budget.")

    print(f"\n  {r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
