"""Append-only record of every signal, including the ones we rejected.

This is the foundation for any later claim about whether the system
works. Nothing downstream - hit rate, creator leaderboard, veto
calibration - can be computed retroactively, because the inputs that
existed at decision time are gone by the time the outcome is known.
Every run without this is a day that can never be evaluated.

Four rules it exists to enforce:

1. WRITE AT DECISION TIME. The score, the components, the independent
   channel list and the freshness state are snapshotted as they were
   when the signal fired, never recomputed later from newer data. A
   score re-derived after the fact silently imports hindsight.

2. KEEP THE REJECTS. Vetoed signals are recorded with the same detail
   as accepted ones. Without them "the vetoes work" is untestable, and
   the surviving sample is selected on exactly the variable under test.

3. NO ENTRY PRICE HERE. Deliberately. There is no market feed at
   generation time, and writing an estimate would be fabricated data.
   Only the timestamp is stored; prices.py resolves the first close at
   or after it, which is the look-ahead-safe definition already used
   there.

4. NEVER REWRITE. Updates touch only `last_seen`, `times_seen` and
   `status`. Scores and components from an earlier observation are
   immutable. Status transitions are appended to `history`, so a signal
   that went WATCHLIST -> INVALIDATED still shows it was once promoted.

A signal is re-recorded only when its state MATERIALLY changes, so a
name mentioned every three hours for a week is one signal with an
observation count, not 56 duplicates inflating the sample.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

PATH = os.path.join("data", "signals.json")

# From the 2.0 spec. DISCOVERED is where everything enters.
STATUSES = ("DISCOVERED", "RESEARCH", "WATCHLIST", "SETUP_DETECTED",
            "CONFIRMED", "INVALIDATED", "EXPIRED", "PASS")

# Score moves smaller than this are noise, not a new signal.
MATERIAL_SCORE_DELTA = 0.75


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def signal_id(ticker: str, generated_at: str) -> str:
    h = hashlib.sha256(f"{ticker}|{generated_at}".encode()).hexdigest()[:12]
    return f"{ticker}-{h}"


def load() -> dict:
    if not os.path.exists(PATH):
        return {"signals": [], "version": 1}
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # Never destroy the log on a parse error - that is the one file
        # whose loss cannot be repaired by re-running anything.
        return {"signals": [], "version": 1, "load_error": True}
    data.setdefault("signals", [])
    return data


def save(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, PATH)        # atomic: a crash cannot truncate the log


def _initial_status(entry: dict) -> str:
    sc = entry.get("score") or {}
    if sc.get("vetoed"):
        return "PASS"
    n = sc.get("independent_channels", 0)
    if n >= 2 and sc.get("total", 0) >= 4.0:
        return "WATCHLIST"
    if n >= 1:
        return "RESEARCH"
    return "DISCOVERED"


def _snapshot(entry: dict, aggregate: dict, run_id: str) -> dict[str, Any]:
    """Everything that was knowable at decision time, and nothing else."""
    sc = entry.get("score") or {}
    basis = entry.get("basis") or {}
    fresh = entry.get("freshness") or {}
    items = entry.get("items") or []
    own = [it for it in items if it.get("basis") in ("primary", "original")]

    ts = _now()
    return {
        "id": signal_id(entry.get("ticker", "?"), ts),
        "ticker": entry.get("ticker", ""),
        "company": entry.get("company", ""),
        # Engines are scored separately and never pooled: a technical
        # setup on SPY and a creator thesis on a small cap are different
        # claims with different base rates, and one leaderboard over both
        # would be meaningless.
        "engine": "creator_consensus",
        "generated_at": ts,
        "run_id": run_id,
        "status": _initial_status(entry),
        "history": [{"at": ts, "status": _initial_status(entry),
                     "reason": "first observation"}],

        # --- the decision, frozen ---
        "score_total": sc.get("total"),
        "score_max_possible": sc.get("max_possible"),
        "components": sc.get("components", {}),
        "vetoed": sc.get("vetoed", False),
        "vetoes": sc.get("vetoes", []),
        "unavailable_components": sc.get("unavailable_components", {}),

        # --- the evidence base, frozen ---
        "independent_channels": basis.get("independent_channels", []),
        "reprint_channels": basis.get("reprint_channels", []),
        "basis_counts": basis.get("counts", {}),
        "derivative_share": basis.get("derivative_share"),
        "freshness_state": fresh.get("state"),
        "freshness_censored": fresh.get("censored"),
        "days_covered": fresh.get("days_covered"),

        # --- provenance: which videos said it ---
        "sources": [{
            "channel": it.get("channel", ""),
            "video_id": it.get("video_id", ""),
            "url": it.get("url", ""),
            "published": it.get("published", ""),
            "stance": it.get("stance", ""),
            "evidence": it.get("evidence", ""),
            "basis": it.get("basis", ""),
            "thesis": (it.get("thesis") or "")[:400],
            "catalyst": (it.get("catalyst") or "")[:200],
            "price_target": it.get("price_target", ""),
            "timeframe": it.get("timeframe", ""),
        } for it in items],
        "stance_mix": {s: sum(1 for it in own
                              if (it.get("stance") or "").lower() == s)
                       for s in ("bullish", "bearish", "neutral")},

        # --- bookkeeping ---
        "times_seen": 1,
        "last_seen": ts,
        # No entry price: there is no market feed at decision time and an
        # estimate would be fabricated. prices.py resolves the first close
        # at or after generated_at.
        "entry_price": None,
        "outcome": None,
    }


def _material_change(old: dict, entry: dict) -> str | None:
    """Has this signal changed enough to be a new decision?"""
    sc = entry.get("score") or {}
    basis = entry.get("basis") or {}

    if bool(old.get("vetoed")) != bool(sc.get("vetoed")):
        return "veto status flipped"

    old_t, new_t = old.get("score_total"), sc.get("total")
    if isinstance(old_t, (int, float)) and isinstance(new_t, (int, float)):
        if abs(new_t - old_t) >= MATERIAL_SCORE_DELTA:
            return f"score moved {old_t:+.2f} -> {new_t:+.2f}"

    new_ch = set(basis.get("independent_channels") or [])
    old_ch = set(old.get("independent_channels") or [])
    if new_ch - old_ch:
        return f"new independent channel: {', '.join(sorted(new_ch - old_ch))}"

    if old.get("freshness_state") != (entry.get("freshness") or {}).get("state"):
        return "freshness state changed"
    return None


def record(aggregate: dict, run_id: str | None = None,
           data: dict | None = None) -> dict:
    """Record every ticker - ranked AND vetoed. Returns a summary."""
    data = data if data is not None else load()
    run_id = run_id or _now()
    sigs = data["signals"]

    # newest open observation per ticker
    latest: dict[str, dict] = {}
    for s in sigs:
        t = s.get("ticker")
        if s.get("status") in ("EXPIRED", "INVALIDATED"):
            continue
        if t not in latest or (s.get("last_seen") or "") > (latest[t].get("last_seen") or ""):
            latest[t] = s

    created, updated, unchanged = [], [], 0
    for entry in aggregate.get("tickers", []):
        tk = entry.get("ticker")
        if not tk:
            continue
        prev = latest.get(tk)
        if prev is None:
            snap = _snapshot(entry, aggregate, run_id)
            sigs.append(snap)
            created.append({"ticker": tk, "id": snap["id"],
                            "status": snap["status"],
                            "score": snap["score_total"]})
            continue

        reason = _material_change(prev, entry)
        if reason is None:
            # same decision, seen again - count it, change nothing else
            prev["times_seen"] = int(prev.get("times_seen", 1)) + 1
            prev["last_seen"] = _now()
            unchanged += 1
            continue

        # Materially different -> a NEW signal. The old one is closed
        # rather than edited, so the original decision stays auditable.
        ts = _now()
        prev["status"] = "EXPIRED"
        prev.setdefault("history", []).append(
            {"at": ts, "status": "EXPIRED", "reason": f"superseded: {reason}"})
        prev["last_seen"] = ts
        snap = _snapshot(entry, aggregate, run_id)
        snap["supersedes"] = prev.get("id")
        snap["history"][0]["reason"] = reason
        sigs.append(snap)
        updated.append({"ticker": tk, "id": snap["id"], "reason": reason,
                        "score": snap["score_total"]})

    data["last_run"] = run_id
    data["total_signals"] = len(sigs)
    save(data)
    return {"created": created, "updated": updated,
            "unchanged": unchanged, "total": len(sigs)}


def summary(data: dict | None = None) -> dict:
    data = data if data is not None else load()
    sigs = data.get("signals", [])
    by_status: dict[str, int] = {}
    for s in sigs:
        by_status[s.get("status", "?")] = by_status.get(s.get("status", "?"), 0) + 1
    vetoed = [s for s in sigs if s.get("vetoed")]
    measurable = [s for s in sigs if s.get("outcome")]
    return {
        "total": len(sigs),
        "by_status": by_status,
        "vetoed": len(vetoed),
        "accepted": len(sigs) - len(vetoed),
        "with_outcome": len(measurable),
        "awaiting_outcome": len(sigs) - len(measurable),
        "earliest": min((s.get("generated_at", "") for s in sigs), default=None),
        "note": ("Outcomes require elapsed time. Statistical conclusions need "
                 "a few hundred resolved signals; see scripts/score_performance.py."),
    }


TECHNICAL_ENGINE = "technical_daily"


def record_pattern_signals(hits: list[dict], run_id: str | None = None,
                           data: dict | None = None) -> dict:
    """Record daily technical setups as paper signals.

    Kept in the same log so outcomes accrue through the same path, but
    under a separate engine so the two are never pooled.

    These are signals on the UNDERLYING. No option is priced, because no
    chain data is configured and a made-up spread would defeat the point
    of measuring execution cost.
    """
    data = data if data is not None else load()
    run_id = run_id or _now()
    sigs = data["signals"]

    # dedup: one signal per ticker+pattern+signal_date, ever
    seen = {(s.get("ticker"), s.get("pattern"), s.get("signal_date"))
            for s in sigs if s.get("engine") == TECHNICAL_ENGINE}

    created = []
    for h in hits:
        key = (h.get("ticker"), h.get("pattern"), str(h.get("signal_date")))
        if key in seen:
            continue
        ts = _now()
        snap = {
            "id": signal_id(f"{h.get('ticker','?')}-{h.get('pattern','?')}", ts),
            "engine": TECHNICAL_ENGINE,
            "ticker": h.get("ticker", ""),
            "pattern": h.get("pattern", ""),
            "direction": h.get("direction", "long"),
            "signal_date": str(h.get("signal_date", "")),
            "generated_at": ts,
            "run_id": run_id,
            "status": "SETUP_DETECTED",
            "history": [{"at": ts, "status": "SETUP_DETECTED",
                         "reason": "pattern detected on last completed bar"}],
            "signal_close": h.get("signal_close"),
            "invalidation": h.get("invalidation"),
            "evidence": h.get("evidence", {}),
            # Entry is the NEXT bar's close - the signal bar's close is not
            # knowable in time to act on it.
            "entry_rule": "close of the first session after the signal bar",
            "paper_only": True,
            "option_priced": False,
            "option_note": ("no options chain configured - execution cost "
                            "cannot be measured, only the underlying move"),
            "times_seen": 1,
            "last_seen": ts,
            "entry_price": None,
            "outcome": None,
        }
        sigs.append(snap)
        seen.add(key)
        created.append({"ticker": snap["ticker"], "pattern": snap["pattern"],
                        "date": snap["signal_date"], "id": snap["id"]})

    data["total_signals"] = len(sigs)
    save(data)
    return {"created": created, "total": len(sigs)}
