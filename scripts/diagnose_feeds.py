#!/usr/bin/env python3
"""Which price providers work from THIS machine, and what they return.

Run it where the pipeline runs. Network reachability is a property of
the machine, not of the code: all four providers fail from a sandbox
whose egress allowlist does not include them, and that looks identical
to four broken providers.

    python3 scripts/diagnose_feeds.py            # SPY QQQ
    python3 scripts/diagnose_feeds.py NVDA TSLA
"""
from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scalper import dataquality as dq, prices          # noqa: E402

HOSTS = {
    "alpaca": "data.alpaca.markets",
    "yfinance": "query2.finance.yahoo.com",
    "stooq": "stooq.com",
    "yahoo": "query1.finance.yahoo.com",
}


def reachable(host: str, port: int = 443, timeout: float = 6.0) -> str:
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return f"reachable ({(time.time() - start) * 1000:.0f} ms)"
    except socket.gaierror:
        return "DNS does not resolve"
    except (socket.timeout, TimeoutError):
        return "timed out"
    except OSError as exc:
        return f"refused ({exc.__class__.__name__})"


def main(argv: list[str]) -> int:
    tickers = [t.upper() for t in argv[1:]] or ["SPY", "QQQ"]

    print("=" * 68)
    print("1. Can this machine reach the providers at all?")
    print("=" * 68)
    for name, host in HOSTS.items():
        print(f"  {name:<10} {host:<28} {reachable(host)}")

    print()
    print("=" * 68)
    print("2. Credentials (presence only - no value is printed)")
    print("=" * 68)
    for var in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "ALPACA_FEED"):
        val = os.environ.get(var)
        print(f"  {var:<20} {'set (' + str(len(val)) + ' chars)' if val else 'NOT SET'}")

    if prices.alpaca_configured():
        feed, why = prices.alpaca_feed(force=True)
        print(f"\n  entitlement probe -> feed={feed}")
        print(f"  {why}")
    else:
        print("\n  Alpaca not configured, so no entitlement probe was made.")
        print("  Set ALPACA_KEY_ID and ALPACA_SECRET_KEY to enable it.")

    print()
    print("=" * 68)
    print("3. Per-ticker fetch and quality")
    print("=" * 68)
    for t in tickers:
        print(f"\n  --- {t} ---")
        rows = prices.fetch_history(t, timeout=20)
        v = prices.LAST_QUALITY.get(t, {})
        print(f"  status   : {v.get('status', '?')}")
        print(f"  provider : {v.get('provider') or '-'}   feed: {v.get('feed') or '-'}")
        print(f"  bars     : {v.get('bars', 0)}"
              + (f"   {v.get('first')} .. {v.get('last')}" if rows else ""))
        print(f"  detail   : {v.get('detail', '-')}")
        checks = v.get("checks") or {}
        for k in ("missing_sessions", "zero_volume_bars", "stale_days"):
            if checks.get(k):
                print(f"    {k}: {checks[k]}")
        if checks.get("absent_dates"):
            print(f"    absent sessions: {', '.join(checks['absent_dates'][:6])}")
        if checks.get("possible_splits"):
            print(f"    possible unadjusted splits: {checks['possible_splits']}")
        for a in v.get("attempts") or []:
            print(f"    tried {a['provider']:<10} -> {a.get('error')}"
                  + (f" (HTTP {a['status_code']})" if a.get("status_code") else ""))
        if rows:
            r = rows[-1]
            print(f"  newest   : {r['date']}  O{r['open']} H{r['high']} "
                  f"L{r['low']} C{r['close']} V{r['volume']:.0f}"
                  + ("  [adjusted]" if r.get("adjusted") else "  [RAW - splits not adjusted]"))

    print()
    summary = dq.summarise(list(prices.LAST_QUALITY.values()))
    print("=" * 68)
    print(f"4. Summary: {summary['counts']}")
    print("=" * 68)
    for row in summary["unusable"]:
        print(f"  unusable  {row['ticker']}: {row['detail']}")
    for row in summary["degraded"]:
        print(f"  degraded  {row['ticker']}: {row['detail']}")
    if summary["usable"]:
        print(f"  usable    {', '.join(summary['usable'])}")
    return 0 if summary["usable"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
