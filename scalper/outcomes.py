"""Attach realised price outcomes to recorded signals.

The decision and the outcome are kept strictly apart. A signal's score,
components and evidence are frozen at generation time and never touched
here; only `outcome` accrues. That separation is what makes the later
performance question answerable: the inputs cannot be quietly revised
once the result is known.

Re-measured every run, because horizons elapse. A signal generated
today has a 1-day return tomorrow and a 30-day return next month. An
unelapsed horizon is stored as null - never estimated, interpolated or
carried forward from a shorter one.

NOTHING IS FABRICATED WHEN THE FEED IS DOWN. If prices cannot be
fetched, the signal records why and stays unmeasured. A missing outcome
and a zero outcome are different facts, and a performance table built
from silently-zeroed misses is worse than no table. `diagnostics()`
reports feed health so a dead source is visible on the dashboard rather
than looking like "no signals have resolved yet".
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import prices, signal_log, validate

# Stooq serves US equities. Crypto and index tickers are not in that
# namespace, so they are skipped explicitly rather than failing per-run.
SKIP_REASONS = {
    "crypto": "not a US equity - no price source configured",
}


def _parse(ts: str):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _entry_reference(sig: dict) -> datetime | None:
    """The earliest moment this signal could actually have been entered.

    Creator signals: the generation time. prices.measure() then takes the
    first close at or after it.

    Technical signals: the day AFTER the signal bar. The pattern is
    detected from a completed bar, so that bar's own close was already
    gone by the time it was knowable - entering on it would be
    look-ahead, and would silently disagree with backtest(), which enters
    on the next bar. Passing signal_date + 1 makes the live path and the
    backtest use the same rule.
    """
    if sig.get("engine") == signal_log.TECHNICAL_ENGINE and sig.get("signal_date"):
        d = _parse(sig["signal_date"]) or _parse(sig["signal_date"] + "T00:00:00+00:00")
        if d:
            return d + timedelta(days=1)
    return _parse(sig.get("generated_at"))


def _needs_measuring(sig: dict) -> bool:
    """Measure if never measured, or if a horizon may have since elapsed."""
    out = sig.get("outcome")
    if not out:
        return True
    if out.get("error") or out.get("skipped"):
        # retry transient fetch failures, but never retry a skip
        return not out.get("skipped")
    returns = out.get("returns") or {}
    # any horizon still null may have elapsed since the last attempt
    return any(v is None for v in returns.values())


def measure_all(data: dict | None = None, limit: int = 400,
                max_tickers: int = 40, delay: float = 1.2,
                give_up_after: int = 3,
                now: datetime | None = None) -> dict[str, Any]:
    """Measure signals that need it, pacing requests at the free feed.

    max_tickers caps DISTINCT price fetches per run. 100+ symbols every
    three hours gets a free source to rate-limit or ban, which would
    look exactly like a broken feed. Whatever is not reached this run
    stays pending and is picked up next run; horizons are measured from
    the stored generation time, so nothing is lost by waiting.
    """
    data = data if data is not None else signal_log.load()
    sigs = data.get("signals", [])
    now = now or datetime.now(timezone.utc)

    todo = [s for s in sigs if _needs_measuring(s)][:limit]
    history: dict[str, list | None] = {}       # one fetch per ticker
    stats = {"measured": 0, "skipped": 0, "fetch_failed": 0,
             "no_entry_day": 0, "tickers_fetched": 0, "tickers_failed": []}

    for sig in todo:
        tk = (sig.get("ticker") or "").upper()
        when = _entry_reference(sig)
        if not tk or not when:
            continue

        if validate.classify_asset(tk) == "crypto":
            sig["outcome"] = {"skipped": True, "reason": SKIP_REASONS["crypto"],
                              "checked_at": now.isoformat()}
            stats["skipped"] += 1
            continue

        if tk not in history:
            # Circuit breaker. Every failed fetch costs a full network
            # timeout, so a dead feed would otherwise burn 40 x 25s on
            # EVERY scheduled run. If the first few all fail and nothing
            # has succeeded, the feed is down: stop now, record it, and
            # let the next run try again.
            if stats["tickers_fetched"] == 0 and \
                    len(stats["tickers_failed"]) >= give_up_after:
                stats["circuit_open"] = True
                break
            if stats["tickers_fetched"] + len(stats["tickers_failed"]) >= max_tickers:
                stats["deferred"] = stats.get("deferred", 0) + 1
                continue        # next run picks it up
            if history:
                time.sleep(delay)
            rows = prices.fetch_history(tk)
            history[tk] = rows
            if rows:
                stats["tickers_fetched"] += 1
            else:
                stats["tickers_failed"].append(tk)

        rows = history[tk]
        if not rows:
            # Feed down or ticker unknown. Record the miss; do NOT write a
            # zero, which would read as "measured, no move".
            sig["outcome"] = {
                "error": "no price data available",
                "checked_at": now.isoformat(),
                "attempts": int((sig.get("outcome") or {}).get("attempts", 0)) + 1,
            }
            stats["fetch_failed"] += 1
            continue

        result = prices.measure(rows, when)
        # Record which feed this was measured on. IEX is one exchange and
        # a small share of volume; a later comparison must not silently
        # mix it with full-market data as though they were the same.
        feed = rows[0].get("feed") if rows else None
        if feed:
            result["feed"] = feed
        if result.get("error"):
            sig["outcome"] = {**result, "checked_at": now.isoformat()}
            stats["no_entry_day"] += 1
            continue

        sig["outcome"] = {**result, "checked_at": now.isoformat()}
        stats["measured"] += 1

    if stats.get("circuit_open"):
        stats["note"] = (f"stopped after {give_up_after} consecutive fetch "
                         "failures - price feed appears to be down")
    # Benchmarks once per run, then relative returns on everything
    # measured. Raw forward returns on a drifting market flatter every
    # strategy; the relative figure is the one downstream code reads.
    if stats["measured"]:
        bench: dict[str, list] = {}
        wanted = set(BENCHMARKS)
        for e in {(s.get("sector") or "").strip().lower() for s in sigs}:
            if e in SECTOR_ETF:
                wanted.add(SECTOR_ETF[e])
        for b in sorted(wanted):
            rows = history.get(b) or prices.fetch_history(b)
            if rows:
                bench[b] = rows
        if bench:
            for sig in sigs:
                if (sig.get("outcome") or {}).get("returns"):
                    attach_relative(sig, bench, sig.get("sector", ""))
            stats["benchmarked"] = sum(
                1 for s in sigs if (s.get("outcome") or {}).get("relative"))
            stats["benchmarks_used"] = sorted(bench)
        else:
            stats["benchmarked"] = 0
            stats["benchmark_error"] = (
                "benchmark prices unavailable - relative performance cannot "
                "be computed, and raw returns must not be read as skill")

    data["outcomes_last_run"] = now.isoformat()
    signal_log.save(data)
    stats["pending"] = sum(1 for s in sigs if _needs_measuring(s))
    return stats



# Sector proxies, used only when a ticker's sector is actually known.
SECTOR_ETF = {
    "technology": "XLK", "tech": "XLK", "semiconductors": "SMH",
    "healthcare": "XLV", "health care": "XLV", "biotech": "XBI",
    "financials": "XLF", "finance": "XLF", "energy": "XLE",
    "industrials": "XLI", "consumer discretionary": "XLY",
    "consumer staples": "XLP", "utilities": "XLU", "materials": "XLB",
    "real estate": "XLRE", "communication services": "XLC",
}
BENCHMARKS = ("SPY", "QQQ")


def _benchmark_return(rows: list[dict], when, horizon_key: str,
                      entry_idx_cache: dict) -> float | None:
    """Benchmark return over the SAME window, from the SAME entry rule."""
    found = prices._first_close_on_or_after(rows, when.astimezone(timezone.utc).date())
    if not found:
        return None
    i, entry = found
    try:
        h = int(horizon_key)
    except (TypeError, ValueError):
        return None
    j = i + h
    if j >= len(rows) or entry["close"] <= 0:
        return None
    return (rows[j]["close"] / entry["close"] - 1) * 100


def attach_relative(sig: dict, bench_rows: dict[str, list[dict]],
                    sector: str = "") -> None:
    """Add benchmark-relative returns to a measured signal, in place.

    THE REASON THIS EXISTS
    A signal that returned +2% while SPY returned +3% LOST money in the
    only sense that matters - you could have held the index and done
    better with less work. Raw forward returns on a drifting market make
    every strategy look good, which is exactly the error the pattern
    backtest caught when win rate was compared against buy-and-hold.
    Every performance number downstream - creator accuracy, strategy
    expectancy, the Byrde Score's reliability input - is read from the
    RELATIVE figure, never the raw one.
    """
    out = sig.get("outcome") or {}
    returns = out.get("returns") or {}
    if not returns:
        return
    when = _parse(sig.get("generated_at"))
    if not when:
        return

    rel: dict[str, Any] = {}
    for hk, r in returns.items():
        if not r or r.get("return_pct") is None:
            rel[hk] = None
            continue
        row = {"raw_pct": r["return_pct"]}
        for b in BENCHMARKS:
            rows = bench_rows.get(b)
            if not rows:
                continue
            br = _benchmark_return(rows, when, hk, {})
            if br is not None:
                row[f"{b.lower()}_pct"] = round(br, 3)
                row[f"vs_{b.lower()}"] = round(r["return_pct"] - br, 3)
        etf = SECTOR_ETF.get((sector or "").strip().lower())
        if etf and bench_rows.get(etf):
            sr = _benchmark_return(bench_rows[etf], when, hk, {})
            if sr is not None:
                row["sector_etf"] = etf
                row["sector_pct"] = round(sr, 3)
                row["vs_sector"] = round(r["return_pct"] - sr, 3)
        rel[hk] = row

    out["relative"] = rel
    out["benchmark_note"] = (
        "vs_spy / vs_qqq are the return MINUS the benchmark over the same "
        "window from the same entry rule. A positive raw return with a "
        "negative vs_spy underperformed the index.")
    sig["outcome"] = out


def resolved(data: dict | None = None, horizon: str = "7") -> list[dict]:
    """Signals with a real return at `horizon`. The only safe input to
    any performance claim."""
    data = data if data is not None else signal_log.load()
    out = []
    for s in data.get("signals", []):
        r = ((s.get("outcome") or {}).get("returns") or {}).get(horizon)
        if r and r.get("return_pct") is not None:
            out.append(s)
    return out


def diagnostics(data: dict | None = None) -> dict[str, Any]:
    """Feed health, so a dead price source is visible rather than being
    mistaken for 'nothing has resolved yet'."""
    data = data if data is not None else signal_log.load()
    sigs = data.get("signals", [])
    total = len(sigs)
    measured = sum(1 for s in sigs if (s.get("outcome") or {}).get("returns"))
    failed = sum(1 for s in sigs if (s.get("outcome") or {}).get("error"))
    skipped = sum(1 for s in sigs if (s.get("outcome") or {}).get("skipped"))
    never = sum(1 for s in sigs if not s.get("outcome"))

    by_h = {}
    for h in ("1", "7", "30", "90", "180"):
        by_h[h] = len(resolved(data, h))

    healthy = measured > 0 or total == 0
    return {
        "total_signals": total,
        "measured": measured,
        "fetch_failed": failed,
        "skipped_non_equity": skipped,
        "never_attempted": never,
        "resolved_by_horizon": by_h,
        "price_feed_healthy": healthy,
        "last_run": data.get("outcomes_last_run"),
        "warning": (
            "" if healthy else
            "No signal has a measured price outcome. The price feed is "
            "unreachable, so performance cannot be evaluated. This is a "
            "broken data source, not an absence of results."
        ),
        "note": ("Statistical conclusions need a few hundred resolved signals. "
                 f"At the 7-day horizon there are {by_h['7']}."),
    }
