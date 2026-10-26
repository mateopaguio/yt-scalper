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
from datetime import datetime, timezone
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
        when = _parse(sig.get("generated_at"))
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
        if result.get("error"):
            sig["outcome"] = {**result, "checked_at": now.isoformat()}
            stats["no_entry_day"] += 1
            continue

        sig["outcome"] = {**result, "checked_at": now.isoformat()}
        stats["measured"] += 1

    if stats.get("circuit_open"):
        stats["note"] = (f"stopped after {give_up_after} consecutive fetch "
                         "failures - price feed appears to be down")
    data["outcomes_last_run"] = now.isoformat()
    signal_log.save(data)
    stats["pending"] = sum(1 for s in sigs if _needs_measuring(s))
    return stats


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
