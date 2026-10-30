#!/usr/bin/env python3
"""Catch the class of bug that broke production for 2 runs on 2026-10-26.

A missing import inside build_digest is invisible to `python -c "import
run"` - the module loads fine and the NameError only fires when the
function is actually called. `signals.compute(...)` was used without
`signals` being imported, every scheduled run crashed, and no
notification could fire because the crash came first.

So this exercises the real call path against the real stored data, with
no API calls and no writes to the live signal log. Run it before every
commit:  python3 tests/smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED: list[str] = []


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
    except Exception as exc:                      # noqa: BLE001
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
        FAILED.append(name)


def main() -> int:
    from scalper import (analyze, attribution, config as cfgmod, freshness,
                         narratives, rank as ranking, signal_log, store, track)

    # never touch the real log from a test
    signal_log.PATH = os.path.join(tempfile.mkdtemp(), "data", "signals.json")

    cfg = cfgmod.load()
    videos = store.recent_videos(days=365)
    print(f"\n{len(videos)} stored videos\n")

    print("modules:")
    check("attribution classifies the three bases", lambda: [
        _eq(attribution.classify(t), w) for t, w in (
            ("Shares fell on a Bloomberg report of tower talks", "derivative"),
            ("Reported revenue up 26% and strong guidance", "primary"),
            ("Host argues the 8.8x multiple understates the dividend", "original"),
        )])

    agg = {}

    def build():
        nonlocal agg
        agg = analyze.aggregate(videos, cfg.watchlist, cfgmod.consensus_tiers(cfg))
        narratives.annotate(agg)
        freshness.annotate(agg, videos)
        ranking.rank(agg)

    check("aggregate + annotate chain", build)
    check("every ticker scored", lambda: _true(
        all("score" in e and "basis" in e and "freshness" in e
            for e in agg["tickers"]), "a ticker is missing an annotation"))
    check("consensus counts only own work", lambda: _true(
        all(len(e["basis"]["independent_channels"]) > 1 for e in agg["multi_channel"]),
        "a consensus ticker has <2 independent channels"))
    check("vetoed signals are kept, not dropped", lambda: _true(
        len(agg["ranked"]) + len(agg["vetoed"]) == len(agg["tickers"]),
        "tickers vanished between ranked and vetoed"))
    check("market-data components declared unavailable", lambda: _true(
        agg["tickers"] and agg["tickers"][0]["score"]["unavailable_components"],
        "missing components were silently defaulted"))
    check("no entry price invented at decision time", lambda: _true(
        all(s["entry_price"] is None
            for s in signal_log.load().get("signals", [])),
        "an entry price was fabricated"))

    ledger = track.build_ledger(videos)
    check("ledger", lambda: ledger)
    check("under_the_radar", lambda: track.under_the_radar(agg, ledger))
    check("creator_stats", lambda: track.creator_stats(ledger))

    # --- the one that would have caught the outage ---
    print("\nreal call path:")
    spec = importlib.util.spec_from_file_location("runmod", "run.py")
    mod = importlib.util.module_from_spec(spec)
    check("run.py imports", lambda: spec.loader.exec_module(mod))
    check("build_digest() actually executes", lambda: _true(
        mod.build_digest(videos, cfg, {}, [])["aggregate"]["total_tickers"] >= 0,
        "build_digest returned nothing"))

    # guarded: if build_digest is broken we want a clean FAIL line, not a
    # traceback that hides every check after it
    d = {}
    try:
        d = mod.build_digest(videos, cfg, {}, [])
    except Exception as exc:                      # noqa: BLE001
        print(f"  FAIL digest keys: build_digest raised "
              f"{type(exc).__name__}: {exc}")
        FAILED.append("digest keys")
    if d:
        for key in ("aggregate", "signals", "signal_log", "ranked", "vetoed",
                    "under_the_radar", "creator_stats", "calendar"):
            check(f"digest has '{key}'", lambda k=key: _true(k in d, "absent"))

    print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all checks passed'}")
    return 1 if FAILED else 0


def _eq(got, want):
    if got != want:
        raise AssertionError(f"got {got!r}, wanted {want!r}")


def _true(cond, msg):
    if not cond:
        raise AssertionError(msg)
    return True


if __name__ == "__main__":
    sys.exit(main())
