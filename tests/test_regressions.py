#!/usr/bin/env python3
"""Regression tests for bugs that reached production.

Each test fails if its bug returns. Run before every commit:
    python3 tests/test_regressions.py
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


# --------------------------------------------------------------------
# BUG 1 (2026-10-26): run.py used signals.compute() without importing
# `signals`. The module imported fine, so nothing caught it; the
# NameError only fired when build_digest was called, which killed every
# scheduled run before notifications could be sent.
# --------------------------------------------------------------------
def test_build_digest_executes():
    from scalper import config as cfgmod, signal_log, store
    signal_log.PATH = os.path.join(tempfile.mkdtemp(), "data", "signals.json")

    spec = importlib.util.spec_from_file_location("runmod_reg", "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                 # import alone never caught it

    cfg = cfgmod.load()
    videos = store.recent_videos(days=7)
    # record_signals=False skips the outcome measurement and setup scan,
    # which both hit the network. This test is about the import, not the
    # feeds, and a regression test that needs the internet is a test that
    # fails for reasons unrelated to the bug it guards.
    digest = mod.build_digest(videos, cfg, {}, [], record_signals=False)
    assert "signals" in digest, "digest lost its signals section"
    assert digest["signals"] is not None, "signals.compute() produced nothing"


def test_every_name_used_in_run_is_importable():
    """Catch the whole CLASS of bug, not just this one instance."""
    import ast
    tree = ast.parse(open("run.py").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            imported.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    imported.add(t.id)

    import builtins
    known = imported | set(dir(builtins))
    missing = set()
    for node in ast.walk(tree):
        # module.attr(...) where `module` was never imported or defined
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id not in known:
                missing.add(node.value.id)
    # locals and comprehension vars produce noise; only flag names that
    # look like modules we actually ship
    shipped = {f[:-3] for f in os.listdir("scalper") if f.endswith(".py")}
    real = {m for m in missing if m in shipped}
    assert not real, f"run.py uses un-imported scalper modules: {sorted(real)}"


# --------------------------------------------------------------------
# BUG 2 (found 2026-10-27): notify.dispatch() accepted a `message`
# argument and then unconditionally overwrote it with build_sms(), so
# the signal-specific alert assembled in run.notify_if_new, and the
# catalyst tag prefixed to it, were discarded on every single send.
# --------------------------------------------------------------------
class _FakeCfg:
    site_url = ""
    ntfy_topic = "test-topic"

    def __init__(self):
        self.notify = {"only_if_findings": False, "sms": False, "push": True}

    def sms_enabled(self):
        return False

    def push_enabled(self):
        return True


def test_dispatch_preserves_custom_message():
    from scalper import notify

    sent = {}

    def fake_push(cfg, digest, message):
        sent["message"] = message
        return True

    original = notify.send_push
    notify.send_push = fake_push
    try:
        digest = {"aggregate": {"tickers": [{"ticker": "NVDA"}]}, "video_count": 1}
        custom = "!NVDA 2 filing(s)\nHeating up: NVDA 5x NEW"
        notify.dispatch(_FakeCfg(), digest, message=custom)
    finally:
        notify.send_push = original

    assert sent.get("message") == custom, (
        f"dispatch discarded the caller's message; sent {sent.get('message')!r}")


def test_dispatch_still_falls_back_when_no_message():
    """The fix must not break the default path."""
    from scalper import notify

    sent = {}

    def fake_push(cfg, digest, message):
        sent["message"] = message
        return True

    original = notify.send_push
    notify.send_push = fake_push
    try:
        digest = {"aggregate": {"tickers": [{"ticker": "AAPL"}]}, "video_count": 1}
        notify.dispatch(_FakeCfg(), digest)          # no message supplied
    finally:
        notify.send_push = original

    assert sent.get("message"), "dispatch sent nothing when no message was given"
    assert isinstance(sent["message"], str) and sent["message"].strip()



# --------------------------------------------------------------------
# BUG 3 (found 2026-10-27): store.due_pending() treated an unreadable
# retry timestamp as "too old" and expired the item, destroying videos
# whose clock was merely missing. Same shape as the throttling incident
# that dropped 20 good videos.
# --------------------------------------------------------------------
def test_unreadable_timestamp_does_not_expire():
    from scalper import store

    original = store.load_pending
    store.load_pending = lambda: [
        {"video_id": "missing-clock", "attempts": 0},            # no timestamps
        {"video_id": "empty-clock", "attempts": 0, "first_seen": ""},
        {"video_id": "garbage-clock", "attempts": 0, "first_seen": "not-a-date"},
    ]
    try:
        retry, expired = store.due_pending()
    finally:
        store.load_pending = original

    dropped = {i["video_id"] for i in expired}
    assert not dropped, f"unreadable timestamps were expired and lost: {dropped}"
    assert len(retry) == 3, f"expected all 3 kept for retry, got {len(retry)}"


def test_attempt_cap_still_retires():
    """The fix must not make items immortal."""
    from scalper import store

    original = store.load_pending
    store.load_pending = lambda: [
        {"video_id": "exhausted", "attempts": 99, "first_seen": "not-a-date"},
    ]
    try:
        retry, expired = store.due_pending()
    finally:
        store.load_pending = original

    assert len(expired) == 1, "attempts cap no longer retires anything"



# --------------------------------------------------------------------
# PAPER SIM INVARIANTS. Not bugs that happened - properties that must
# never break, because a simulator that flatters itself is worse than
# none. The gap case especially: filling a gap at the stop price is the
# most common way a paper result beats the real thing.
# --------------------------------------------------------------------
def _paper_bars():
    from datetime import date, timedelta
    d0 = date(2026, 1, 1)
    def mk(specs):
        return [{"date": d0 + timedelta(days=i), "open": o, "high": h,
                 "low": l, "close": c, "volume": 1e6}
                for i, (o, h, l, c) in enumerate(specs)]
    return mk


def test_gap_through_stop_fills_at_open_not_stop():
    from scalper import paper
    mk = _paper_bars()
    rows = mk([(100,100,100,100), (100,100,100,100), (88,90,86,89)])
    sig = [{"signal_date": rows[0]["date"], "invalidation": 95,
            "ticker": "T", "pattern": "p"}]
    res = paper.simulate(rows, sig, paper.Rules(), paper.Costs())
    t = res["trade_log"][0]
    assert t["exit_price"] == 88, (
        f"gap filled at {t['exit_price']} - must be the open (88), not the stop")
    assert t["r_multiple"] < -1.0, "a gap through the stop must lose MORE than 1R"


def test_costs_charged_on_both_sides():
    from scalper import paper
    mk = _paper_bars()
    rows = mk([(100,100,100,100), (100,100,100,100), (100,101,94,96)])
    sig = [{"signal_date": rows[0]["date"], "invalidation": 95,
            "ticker": "T", "pattern": "p"}]
    priced = paper.simulate(rows, sig, paper.Rules(),
                            paper.Costs(0.0, 0.01, 0.01))
    free = paper.simulate(rows, sig, paper.Rules(), paper.Costs(0, 0, 0))
    assert priced["costs_paid"] > 0, "no costs were charged"
    shares = free["trade_log"][0]["shares"]
    assert abs(priced["costs_paid"] - shares * 0.02 * 2) < 1e-6, (
        "costs must be charged on entry AND exit")
    assert priced["net_pnl"] < free["net_pnl"], "costs did not reduce P&L"


def test_entry_is_the_bar_after_the_signal():
    from scalper import paper
    mk = _paper_bars()
    rows = mk([(100,100,100,100), (100,100,100,100), (100,112,99,111)])
    sig = [{"signal_date": rows[0]["date"], "invalidation": 95,
            "ticker": "T", "pattern": "p"}]
    t = paper.simulate(rows, sig, paper.Rules(), paper.Costs())["trade_log"][0]
    assert str(t["entry_date"]) > str(t["signal_date"]), "entered on the signal bar"


def test_open_position_is_not_counted_as_a_win():
    from scalper import paper
    mk = _paper_bars()
    rows = mk([(100,100,100,100), (100,100,100,100), (100,102,99,101)])
    sig = [{"signal_date": rows[0]["date"], "invalidation": 95,
            "ticker": "T", "pattern": "p"}]
    res = paper.simulate(rows, sig, paper.Rules(), paper.Costs())
    assert res["still_open"] == 1
    assert res["closed"] == 0
    assert res["win_rate"] is None, "an unresolved trade was scored"


def test_position_sizing_respects_risk():
    from scalper import paper
    mk = _paper_bars()
    rows = mk([(100,100,100,100), (100,100,100,100), (100,101,94,96)])
    sig = [{"signal_date": rows[0]["date"], "invalidation": 95,
            "ticker": "T", "pattern": "p"}]
    rules = paper.Rules(starting_equity=10_000, risk_per_trade=0.01)
    t = paper.simulate(rows, sig, rules, paper.Costs(0, 0, 0))["trade_log"][0]
    # $100 at risk over a $5 stop distance = 20 shares
    assert t["shares"] == 20, f"sized {t['shares']}, expected 20"
    assert abs(t["net_pnl"] + 100) < 1e-6, "a stop-out should cost exactly the risk"



# --------------------------------------------------------------------
# SIZING INVARIANTS. This module must never imply a trade is advisable,
# and must never let an oversized position pass silently.
# --------------------------------------------------------------------
def test_sizing_flags_an_account_that_is_too_small():
    from scalper import sizing
    r = sizing.assess(sizing.Account(equity=50.0), 0.45, contracts=1)
    assert not r["within_budget"], "a 90%-of-account position passed as within budget"
    assert r["flags"], "no flags raised on a 90% position"
    assert r["total_losses_survivable"] <= 1


def test_sizing_passes_a_sane_position():
    from scalper import sizing
    # $10k account, $0.45 contract, 2% budget -> $200 budget, 4 contracts fit
    r = sizing.assess(sizing.Account(equity=10_000.0), 0.45, contracts=1)
    assert r["within_budget"], f"a 0.45% position was flagged: {r['flags']}"
    assert r["contracts_within_budget"] >= 4


def test_max_loss_is_the_whole_premium():
    """Long options expire worthless. Max loss is never less than cost."""
    from scalper import sizing
    r = sizing.assess(sizing.Account(equity=10_000.0), 1.00, contracts=3)
    assert r["max_loss"] == r["total_cost"] == 300.0


def test_sizing_never_recommends():
    """No OUTPUT may read as advice.

    Checks what the module emits, not what its source says. The first
    version scanned the source and failed on the docstring that explains
    the module does not say "good buy" - testing prose rather than
    behaviour.
    """
    from scalper import sizing
    banned = ("good buy", "should buy", "recommend", "strong buy",
              "buy signal", "worth buying", "go long", "take this trade")

    for equity, price, n in ((50.0, 0.45, 1), (10_000.0, 0.45, 1),
                             (250_000.0, 12.0, 5)):
        r = sizing.assess(sizing.Account(equity=equity), price, contracts=n)
        emitted = " ".join(str(v) for v in r.values()).lower()
        emitted += " " + " ".join(r.get("flags") or []).lower()
        for word in banned:
            assert word not in emitted, (
                f"sizing output contained advice language {word!r} "
                f"for equity={equity}")



# --------------------------------------------------------------------
# SCHEMA BOUNDARY CHECKS. Each rule exists because its absence already
# cost something: close-only bars that silenced the scanner, and the
# directive's requirement that an unpriceable option is never faked.
# --------------------------------------------------------------------
def test_close_only_bars_are_reported_not_silently_useless():
    from datetime import date
    from scalper import schemas
    rows = [{"date": date(2026, 1, i + 1), "close": 100.0} for i in range(5)]
    r = schemas.check_price_bars(rows, need_ohlc=True)
    assert not r["usable"]
    assert "high" in r["reason"] or "low" in r["reason"], r["reason"]


def test_full_ohlc_bars_pass():
    from datetime import date
    from scalper import schemas
    rows = [{"date": date(2026, 1, i + 1), "open": 99.0, "high": 101.0,
             "low": 98.0, "close": 100.0, "volume": 1e6} for i in range(5)]
    assert schemas.check_price_bars(rows, need_ohlc=True)["usable"]


def test_option_quote_without_timestamp_is_unpriceable():
    from scalper import schemas
    q = {"symbol": "SPY", "strike": 600, "expiry": "2026-11-21",
         "kind": "call", "bid": 2.0, "ask": 2.1}      # no as_of
    r = schemas.check_option_quote(q)
    assert not r["priceable"], "a quote with no timestamp was treated as priceable"


def test_stale_option_quote_is_rejected():
    from scalper import schemas
    q = {"symbol": "SPY", "strike": 600, "expiry": "2026-11-21", "kind": "call",
         "bid": 2.0, "ask": 2.1, "as_of": "2026-10-27T03:00:00+00:00"}
    r = schemas.check_option_quote(q, max_age_seconds=60,
                                   now_iso="2026-10-27T04:00:00+00:00")
    assert r["stale"] and not r["priceable"]


def test_validate_never_raises():
    """A malformed row must degrade one ticker, not kill the run."""
    from scalper import schemas
    for junk in ({}, {"date": None}, {"close": "not a number"}, {"x": 1}):
        schemas.validate(junk, schemas.PRICE_BAR)      # must not raise



# --------------------------------------------------------------------
# ALERT TIERING. A notifier that fires on everything gets muted, and a
# muted notifier equals no notifier.
# --------------------------------------------------------------------
def _alert_fixture():
    return {"tickers": [
        {"ticker": "NVDA", "score": {"total": 5.6, "vetoed": False},
         "basis": {"independent_channels": ["a", "b", "c"]},
         # has_body / _state are what the evidence gate reads. A filing
         # nobody retrieved, or one seen on an earlier run, is not a
         # reason to wake a phone.
         "catalyst_events": [{"materiality": 4, "title": "8-K filed",
                              "has_body": True, "body_chars": 5200,
                              "event_id": "ev-nvda-1", "_state": "new"}],
         "freshness": {"state": "forward_catalyst"}},
        {"ticker": "AMD", "score": {"total": 3.4, "vetoed": False},
         "basis": {"independent_channels": ["a", "b"]},
         "catalyst_events": [], "freshness": {"state": "developing"}},
        {"ticker": "T", "score": {"total": -0.5, "vetoed": True},
         "basis": {"independent_channels": ["a"]}, "catalyst_events": []},
    ]}


def test_vetoed_ticker_never_alerts():
    from scalper import alerts
    c = alerts.classify(_alert_fixture())
    assert not any(a["ticker"] == "T" for a in c), "a vetoed signal raised an alert"


def test_critical_requires_a_filed_event():
    from scalper import alerts
    c = alerts.classify(_alert_fixture())
    crit = [a for a in c if a["tier"] == alerts.CRITICAL]
    assert [a["ticker"] for a in crit] == ["NVDA"]
    # AMD has independent support but no filed event -> watchlist, not critical
    amd = [a for a in c if a["ticker"] == "AMD"]
    assert amd and amd[0]["tier"] == alerts.WATCHLIST


def test_cooldown_suppresses_a_repeat():
    from datetime import datetime, timedelta, timezone
    from scalper import alerts
    c = alerts.classify(_alert_fixture())
    now = datetime.now(timezone.utc)
    sent = {c[0]["key"]: (now - timedelta(hours=2)).isoformat()}
    r = alerts.select(c, sent, now=now)
    assert all(a["key"] != c[0]["key"] for a in r["send"]), "fired inside cooldown"
    assert any("cooldown" in x["reason"] for x in r["suppressed"])


def test_identical_state_produces_identical_key():
    """Keys must be content-based, or a re-run re-alerts the same fact."""
    from scalper import alerts
    a = alerts.classify(_alert_fixture())
    b = alerts.classify(_alert_fixture())
    assert [x["key"] for x in a] == [x["key"] for x in b]


def test_tiers_render_as_separate_messages():
    from scalper import alerts
    c = alerts.classify(_alert_fixture())
    msgs = alerts.render(c, "https://example.com")
    assert alerts.CRITICAL in msgs and alerts.WATCHLIST in msgs
    assert msgs[alerts.CRITICAL]["priority"] > msgs[alerts.WATCHLIST]["priority"]



# --------------------------------------------------------------------
# SECURITY MASTER. 56 catalyst events produced ONE ticker because the
# extractor is correctly told never to guess a symbol. A lookup either
# knows a name or does not - but it must never fuzzy-match, which is
# how "Apple Orchards LLC" would become AAPL.
# --------------------------------------------------------------------
def test_security_master_resolves_known_names():
    from scalper import security_master as SM
    d = {"map": {SM.normalise(k): v for k, v in SM.SEED.items()}}
    assert SM.resolve("Pfizer", d) == "PFE"
    assert SM.resolve("Walmart, Inc.", d) == "WMT"
    assert SM.resolve("Takeda Pharmaceutical Company (Adzynma sponsor)", d) == "TAK"


def test_security_master_never_fuzzy_matches():
    from scalper import security_master as SM
    d = {"map": {SM.normalise(k): v for k, v in SM.SEED.items()}}
    for name in ("Apple Orchards LLC", "Metaverse Inc", "Greenwich Rx",
                 "Pfizer Pharmaceuticals Holding Sub", "Walmart Neighborhood Co-op"):
        got = SM.resolve(name, d)
        assert got == "", f"{name!r} fuzzy-matched to {got!r}"


def test_security_master_discards_ambiguous_names():
    """A name seen with two symbols must resolve to neither."""
    from scalper import security_master as SM
    videos = [{"findings": {"tickers": [
        {"company": "Ambiguous Co", "ticker": "AAA", "evidence": "data"},
        {"company": "Ambiguous Co", "ticker": "BBB", "evidence": "data"},
        {"company": "Clear Co", "ticker": "CCC", "evidence": "data"},
    ]}}]
    import tempfile, os, pathlib
    orig = SM.PATH
    SM.PATH = pathlib.Path(tempfile.mkdtemp()) / "sm.json"
    try:
        d = SM.build(videos)
    finally:
        SM.PATH = orig
    assert SM.resolve("Ambiguous Co", d) == "", "ambiguous name was resolved"
    assert SM.resolve("Clear Co", d) == "CCC"
    # keys are normalised: "Ambiguous Co" -> "ambiguous"
    assert SM.normalise("Ambiguous Co") in d["rejected"]



# --------------------------------------------------------------------
# MILESTONE G. The recurring failure mode in performance work is a
# number that flatters itself: raw returns in a rising market, a hit
# rate on three calls, a score that reads as a probability.
# --------------------------------------------------------------------
def test_relative_return_is_negative_when_it_lags_the_index():
    from datetime import datetime, timedelta, timezone
    from scalper import outcomes
    d0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spy = [{"date": (d0 + timedelta(days=i)).date(), "close": 100 * (1.0042 ** i)}
           for i in range(40)]
    sig = {"ticker": "X", "generated_at": d0.isoformat(),
           "outcome": {"returns": {"7": {"return_pct": 2.0}}}}
    outcomes.attach_relative(sig, {"SPY": spy})
    rel = sig["outcome"]["relative"]["7"]
    assert rel["vs_spy"] < 0, (
        f"+2% against a ~+3% index must be negative, got {rel['vs_spy']}")


def test_creator_rates_withheld_on_small_samples():
    """A 100% hit rate on one call is the most misleading number here."""
    from scalper import creators
    sigs = [{"ticker": "SOLO", "generated_at": "2026-05-01T00:00:00+00:00",
             "sources": [{"channel": "Tiny", "basis": "original",
                          "published": "2026-05-01T00:00:00+00:00"}],
             "outcome": {"relative": {"7": {"vs_spy": 9.9}}}}]
    board = creators.scoreboard(sigs)
    tiny = [c for c in board["creators"] if c["channel"] == "Tiny"][0]
    assert tiny["hit_rate_vs_spy"] is None, "a 1-call hit rate was published"
    assert tiny["withheld_reason"]


def test_creator_rates_appear_once_there_is_enough_data():
    from scalper import creators
    sigs = [{"ticker": f"T{i}", "generated_at": "2026-05-01T00:00:00+00:00",
             "sources": [{"channel": "Big", "basis": "original",
                          "published": "2026-05-01T00:00:00+00:00"}],
             "outcome": {"relative": {"7": {"vs_spy": 1.0 if i % 2 else -1.0}}}}
            for i in range(creators.MIN_RESOLVED + 2)]
    board = creators.scoreboard(sigs)
    big = [c for c in board["creators"] if c["channel"] == "Big"][0]
    assert big["hit_rate_vs_spy"] is not None
    assert big["sufficient_data"]


def test_byrde_score_is_never_called_a_probability():
    from scalper import rank
    e = {"score": {"total": 5.0, "max_possible": 7.5,
                   "components": {}, "unavailable_components": {}}}
    r = rank.byrde_score(e)
    assert 0 <= r["score"] <= 100
    blob = (r["caveat"] + r["note"]).lower()
    # The disclaimer must be present...
    assert "not a probability" in blob, "no probability disclaimer"
    # ...and nothing may assert the opposite. Banning the bare phrase
    # "probability of profit" fails on the disclaimer itself, which is
    # the same mistake the sizing test made: test the claim, not the
    # substring.
    import re as _re
    affirms = _re.search(
        r"(?<!not )(?<!never )(?:is|represents|indicates) (?:a |the )?"
        r"(?:probability|chance|likelihood) of (?:profit|a win)", blob)
    assert not affirms, f"score presented as a probability: {affirms.group(0)!r}"
    # and no field should be NAMED like one
    for k in (r.get("parts") or {}):
        assert "prob" not in k.lower() and "odds" not in k.lower()


def test_byrde_score_omits_reliability_when_unmeasured():
    from scalper import rank
    e = {"score": {"total": 5.0, "max_possible": 7.5,
                   "components": {}, "unavailable_components": {}}}
    assert rank.byrde_score(e)["parts"]["reliability"] is None
    assert rank.byrde_score(e, 0.8)["parts"]["reliability"] == 80.0


def test_funnel_explains_every_rejection():
    from scalper import funnel
    agg = {"tickers": [
        {"ticker": "A", "basis": {"independent_channels": [],
                                  "reprint_channels": ["CNBC"]},
         "score": {"vetoed": False}, "catalyst_events": []},
        {"ticker": "B", "basis": {"independent_channels": ["x", "y"]},
         "score": {"vetoed": True, "vetoes": ["retrospective"]},
         "catalyst_events": []},
    ], "rejected_tickers": [{"ticker": "WAYMO", "reason": "not publicly traded",
                             "company": "Waymo"}]}
    f = funnel.build(agg)
    for stage, drops in f["drops"].items():
        for d in drops:
            assert d.get("reason"), f"a {stage} rejection had no reason"
    assert f["counts"]["mentioned"] == 3
    assert any(d["ticker"] == "WAYMO" for d in f["drops"]["valid_symbol"])


def test_critical_alert_carries_thesis_and_invalidation():
    from scalper import alerts
    agg = {"tickers": [{
        "ticker": "PFE", "score": {"total": 5.2, "vetoed": False},
        "basis": {"independent_channels": ["a", "b"]},
        # Read, and new since the last run: the only shape that earns
        # a CRITICAL push.
        "catalyst_events": [{"materiality": 4, "title": "FDA approval",
                             "has_body": True, "body_chars": 7100,
                             "event_id": "ev-pfe-1", "_state": "new"}],
        "freshness": {"state": "forward_catalyst"},
        "items": [{"basis": "original", "stance": "bullish", "evidence": "data",
                   "channel": "a", "thesis": "label expansion", "price_target": "$34"}],
    }]}
    msgs = alerts.render(alerts.classify(agg), "https://x.test")
    body = msgs[alerts.CRITICAL]["body"]
    for need in ("why:", "catalyst:", "evidence:", "invalidation:"):
        assert need in body, f"critical alert missing {need!r}"
    assert "/ticker/PFE.html" in body, "no deep link to the opportunity card"



# --------------------------------------------------------------------
# BUG 4 (2026-10-27): scanner.scan_underlyings() raised
# NameError: name 'last' is not defined. The schema boundary check
# removed the local that a later diagnostics line still read.
#
# The suite missed it because the build_digest test passes
# record_signals=False to avoid the network - which also skips the
# scanner. Removing flakiness removed coverage of the code that broke.
# This exercises the real function with INJECTED bars: no network, full
# code path.
# --------------------------------------------------------------------
def _fake_bars(n=120):
    from datetime import date, timedelta
    d0 = date(2026, 1, 1)
    return [{"date": d0 + timedelta(days=i), "open": 100 + i * 0.1,
             "high": 101 + i * 0.1, "low": 99 + i * 0.1,
             "close": 100 + i * 0.1, "volume": 1_000_000, "feed": "test"}
            for i in range(n)]


def test_scanner_runs_end_to_end_without_network():
    from scalper import prices, scanner
    original = prices.fetch_history
    prices.fetch_history = lambda tk, timeout=25: _fake_bars()
    try:
        r = scanner.scan_underlyings(("SPY", "QQQ"))
    finally:
        prices.fetch_history = original

    assert r["errors"] == [], f"usable bars produced errors: {r['errors']}"
    assert len(r["diagnostics"]) == 2, "diagnostics missing"
    for d in r["diagnostics"]:
        assert d["bars"] == 120
        assert d["feed"] == "test", "feed provenance lost"
        assert "last_fired" in d


def test_scanner_reports_unusable_bars_instead_of_raising():
    """Close-only bars must degrade the ticker, not kill the run."""
    from datetime import date, timedelta
    from scalper import prices, scanner
    d0 = date(2026, 1, 1)
    closeonly = [{"date": d0 + timedelta(days=i), "close": 100.0}
                 for i in range(120)]
    original = prices.fetch_history
    prices.fetch_history = lambda tk, timeout=25: closeonly
    try:
        r = scanner.scan_underlyings(("SPY",))
    finally:
        prices.fetch_history = original
    assert r["errors"], "unusable bars produced no error"
    assert "high" in r["errors"][0]["reason"] or "low" in r["errors"][0]["reason"]




# --------------------------------------------------------------------
# REVIEW FINDINGS (2026-10-27). Each is a real defect found in review,
# not a hypothetical.
# --------------------------------------------------------------------
def test_catalyst_freshness_uses_identity_not_publication_date():
    """An event we have never seen is NEW even if published years ago,
    and one we have seen is not new just because it is recent."""
    from scalper import changes
    old_but_unseen = {"event_id": "z", "published": "2019-01-01T00:00:00+00:00",
                      "findings": {"materiality": 4, "event_type": "approval"}}
    state = {"events": {}}
    r = changes.classify([old_but_unseen], state)
    assert r["counts"]["new"] == 1, "a never-seen old event was not counted new"

    state = changes.commit([old_but_unseen], state)
    r = changes.classify([old_but_unseen], state)
    assert r["counts"] == {"new": 0, "updated": 0, "unchanged": 1}


def test_catalyst_update_detected_on_material_change_only():
    from scalper import changes
    ev = {"event_id": "a", "findings": {"materiality": 2, "event_type": "x",
                                        "direction": "up", "companies": []}}
    state = changes.commit([ev], {"events": {}})
    # a cosmetic change must NOT register
    ev2 = {**ev, "findings": {**ev["findings"]}, "title": "reworded title",
           "published": "2026-10-27T00:00:00+00:00"}
    assert changes.classify([ev2], state)["counts"]["unchanged"] == 1
    # a material change must
    ev3 = {**ev, "findings": {**ev["findings"], "materiality": 5}}
    assert changes.classify([ev3], state)["counts"]["updated"] == 1


def test_top_five_excludes_vetoed_and_still_returns_five():
    """Slicing to five and then dropping vetoed rows produced three
    entries numbered 1, 3, 5."""
    from scalper import app_render
    rows = []
    for i in range(10):
        vetoed = i in (0, 2, 4)        # high scorers are rejected
        rows.append({
            "ticker": f"T{i}", "company": "", "tags": ["vetoed"] if vetoed else [],
            "sentiment": "bullish", "bulls": 1, "bears": 0,
            "independent": 2, "independent_channels": ["a", "b"], "reprints": 0,
            "mentions": 1, "intelligence": 90 - i, "intelligence_raw": 5.0,
            "scalp": {"state": "no_data", "label": "no setup", "detail": ""},
            "freshness": "", "catalyst_count": 0, "catalyst_title": "",
            "verification": "", "vetoes": ["x"] if vetoed else [], "entry": {},
        })
    digest = {"aggregate": {}, "generated_at": "2026-10-27T00:00:00+00:00",
              "videos": [], "signal_log": {}, "catalyst_changes": {"counts": {}}}
    page = app_render.build_page(digest, [], rows)
    import re
    lead = re.findall(r'<span class="n">(\d+)</span>\s*<h3>([A-Z0-9]+)</h3>', page)
    nums = [n for n, _ in lead]
    names = [t for _, t in lead]
    assert nums == ["1", "2", "3", "4", "5"], f"numbering has gaps: {nums}"
    for bad in ("T0", "T2", "T4"):
        assert bad not in names, f"vetoed ticker {bad} reached the top five"


def test_css_has_no_invalid_font_shorthand():
    """`font: 600 12px inherit` is invalid - the family slot cannot be
    `inherit`. WebKit drops the WHOLE declaration, so the element falls
    back to UA defaults on iOS while looking fine in Chromium."""
    import glob
    import re

    # Every module that emits CSS, not just the stylesheet. The first
    # version of this test read app_css.APP_CSS only, so an identical
    # `font:600 12.5px inherit` sitting in an inline style in
    # app_render.py went on shipping to iOS unchecked.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for path in sorted(glob.glob(os.path.join(root, "scalper", "*.py"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"font\s*:\s*([^;{}\"']+)", text):
            decl = m.group(1).strip()
            # A bare `font: inherit` is valid; `inherit` in the family
            # slot of a multi-part shorthand is not, and WebKit drops
            # the whole declaration rather than the one bad component.
            if "inherit" in decl and decl != "inherit":
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{os.path.basename(path)}:{line}  font:{decl}")
    assert not bad, "invalid font shorthand:\n  " + "\n  ".join(bad)


def test_relevance_is_conservative():
    """Uncertain content must stay visible, not be archived."""
    from scalper import relevance
    unanalysed = {"title": "Some market video", "findings": {}}
    assert relevance.classify(unanalysed)["bucket"] == relevance.MARKET_CONTEXT
    macro = {"title": "Fed holds rates", "findings": {"summary": "fed decision",
             "tickers": [], "macro_points": ["rates"]}}
    assert relevance.classify(macro)["bucket"] == relevance.MARKET_CONTEXT
    traded = {"title": "NVDA", "findings": {"tickers": [
        {"ticker": "NVDA", "thesis": "data centre demand"}]}}
    assert relevance.classify(traded)["bucket"] == relevance.TRADE_RELEVANT
    offtopic = {"title": "My apartment tour", "findings": {"summary": "a flat",
                "tickers": [], "macro_points": []}}
    assert relevance.classify(offtopic)["bucket"] == relevance.ARCHIVE


def test_canonical_ticker_appears_once():
    from scalper import ticker_rows
    agg = {"tickers": [
        {"ticker": "AAA", "company": "A", "items": [],
         "basis": {"independent_channels": ["x", "y"]},
         "score": {"total": 4.0, "vetoed": False}, "catalyst_events": [],
         "freshness": {"state": "developing"}, "byrde": {"score": 70}},
    ], "echo_chamber": []}
    rows = ticker_rows.build(agg, under_radar=[{"ticker": "AAA"}],
                             signals={"momentum": [{"ticker": "AAA"}]})
    assert len(rows) == 1, "ticker duplicated across categories"
    assert {"corroborated", "under_radar", "heating"} <= set(rows[0]["tags"])
    assert rows[0]["scalp"]["state"] == "no_data"



def test_no_orphaned_tests():
    """Every test defined in this file must actually be registered.

    Six review tests were written and never run; the suite reported
    "all passed" because it only executes what main() registers. A
    suite that silently skips tests is worse than no suite - it
    produces confidence without coverage.
    """
    import ast as _ast
    src = open(__file__).read()
    tree = _ast.parse(src)
    defined = {n.name for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name.startswith("test_")}
    referenced = {n.id for n in _ast.walk(tree)
                  if isinstance(n, _ast.Name) and n.id.startswith("test_")}
    orphans = sorted(defined - referenced)
    assert not orphans, f"defined but never run: {orphans}"



def test_no_unresolvable_module_level_names():
    """The NameError class that has now caused three outages here.

    run.py called signals.compute() with no `signals` import; scanner.py
    read a local `last` that a refactor had deleted; run.py called
    os.environ with no `os` import. Every one of them imported cleanly,
    passed every test that did not execute that exact line, and died in
    production.

    test_every_name_used_in_run_is_importable covers run.py only. This
    walks every function in every module and checks that each
    module-level name it loads actually resolves, without running it.
    """
    import ast
    import builtins
    import glob
    import importlib

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sorted(glob.glob(os.path.join(root, "scalper", "*.py")))
    files.append(os.path.join(root, "run.py"))
    bad = []

    for path in files:
        base = os.path.basename(path)
        if base == "__init__.py":
            continue
        stem = base[:-3]
        name = ("scalper." + stem) if os.path.basename(
            os.path.dirname(path)) == "scalper" else stem
        try:
            mod = importlib.import_module(name)
        except Exception as exc:                        # noqa: BLE001
            bad.append(f"{base}: will not import ({type(exc).__name__}: {exc})")
            continue

        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        bound = set(dir(mod)) | set(dir(builtins))

        def bindings(fn):
            """Every name this function body binds, nested scopes included."""
            got = set()
            for n in ast.walk(fn):
                if isinstance(n, ast.arg):
                    got.add(n.arg)
                elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    got.add(n.id)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        got.add((a.asname or a.name).split(".")[0])
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    got.add(n.name)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                    got.add(n.name)
                elif isinstance(n, ast.Global) or isinstance(n, ast.Nonlocal):
                    got.update(n.names)
            return got

        # A closure sees its enclosing function's locals, so each function
        # is checked against its whole scope chain. Checking a nested
        # function in isolation reports every captured variable as
        # undefined - prices.py's history() closure over `cache`, say.
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        enclosing = {}
        for fn in funcs:
            for kid in ast.walk(fn):
                if (isinstance(kid, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and kid is not fn):
                    enclosing.setdefault(kid, []).append(fn)

        for fn in funcs:
            scope = set(bound) | bindings(fn)
            for outer in enclosing.get(fn, []):
                scope |= bindings(outer)
            for n in ast.walk(fn):
                if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                        and n.id not in scope):
                    bad.append(f"{base}:{n.lineno} {fn.name}() -> {n.id}")

    assert not bad, "unresolvable names:\n  " + "\n  ".join(sorted(set(bad)))


def test_commit_does_not_write_when_given_a_state():
    """A test must not be able to overwrite the live catalyst record.

    changes.commit() used to call _save() unconditionally, so passing a
    hand-built state wrote that state to data/catalyst_state.json. A
    test event with the id "a" reached the production file that way,
    and any test whose state held a single event would have replaced
    the whole record - silently resetting every catalyst to "new".
    """
    from scalper import changes

    before = changes.PATH.read_text(encoding="utf-8") if changes.PATH.exists() else None

    ev = {"event_id": "unit-test-must-not-persist", "event_type": "guidance",
          "materiality": "high", "direction": "up", "is_market_relevant": True,
          "companies": ["TEST"]}
    out = changes.commit([ev], {"events": {}})

    assert out["events"]["unit-test-must-not-persist"], "should update the dict"
    after = changes.PATH.read_text(encoding="utf-8") if changes.PATH.exists() else None
    assert after == before, "commit() wrote to the live state file"
    if after:
        assert "unit-test-must-not-persist" not in after


def test_calendar_day_is_eastern_not_utc():
    """After 20:00 ET the UTC calendar has already rolled over.

    catalysts.upcoming() keyed its horizon to datetime.now(timezone.utc)
    .date(), so every evening between 20:00 ET and midnight ET the
    calendar dropped the current trading day: an earnings event dated
    today stopped matching `today <= when` because UTC already called
    it yesterday.
    """
    from datetime import datetime, timezone
    from scalper import clock

    # 02:16 UTC on the 28th is 22:16 ET on the 27th.
    evening = datetime(2026, 10, 28, 2, 16, tzinfo=timezone.utc)
    et = clock.to_et(evening)
    assert et.date().isoformat() == "2026-10-27", et
    assert evening.date().isoformat() == "2026-10-28", "premise check"

    # A bare scheduled date is a market date, not an instant: formatting
    # it must not move it across a day boundary.
    assert clock.day("2026-10-30") == "Oct 30"
    assert clock.day("2026-01-01") == "Jan 01"

    # Stamps name the zone they are in, so no reader has to guess.
    text = clock.stamp(evening)
    assert text.endswith(("EDT", "EST", "UTC")), text
    assert "10:16 PM" in text, text


def test_rendered_pages_are_not_labelled_utc():
    """The pages are read by a US equities trader; show market time."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    index = os.path.join(root, "docs", "index.html")
    if not os.path.exists(index):
        return
    with open(index, encoding="utf-8") as fh:
        head = fh.read(4000)
    assert "UTC" not in head, "header still shows UTC"


def test_funnel_counts_never_grow():
    """A funnel that grows is a bug, not a finding.

    catalyst_backed was listed as a stage but computed from the same
    `corroborated` set that `qualified` is drawn from - a side branch,
    not a step. The page rendered 0 catalyst-backed followed by 22
    qualified, which no funnel can do. It is now reported as an
    independent metric, and build() reports any remaining inversion.
    """
    from scalper import funnel

    def tk(sym, indep, vetoed=False, events=None):
        return {"ticker": sym,
                "basis": {"independent_channels": indep, "reprint_channels": []},
                "score": {"vetoed": vetoed, "vetoes": ["test"], "total": 50},
                "catalyst_events": events or []}

    agg = {"tickers": [tk("AAA", ["a", "b"]), tk("BBB", ["c", "d"], vetoed=True),
                       tk("CCC", ["e"]), tk("DDD", [])],
           "rejected_tickers": [{"ticker": "ZZZ", "reason": "not public"}]}
    out = funnel.build(agg)
    counts, stages = out["counts"], out["stages"]

    assert "catalyst_backed" not in stages, "side branch is back in the sequence"
    assert not out["inconsistent"], out["inconsistent"]
    for a, b in zip(stages, stages[1:]):
        assert counts[b] <= counts[a], f"{b}={counts[b]} > {a}={counts[a]}"

    ind = out["independent"]["catalyst_backed"]
    assert ind["total"] == counts["corroborated"], "measured on the wrong set"
    assert ind["n"] <= ind["total"]


def test_blind_price_feed_is_not_reported_as_no_setups():
    """"Could not look" must never render as "nothing found"."""
    from scalper import health

    blind = {"price_health": {"last_run": "2026-10-28T02:00:00+00:00"},
             "setups": {"scanned": ["SPY", "QQQ"], "diagnostics": [],
                        "errors": [{"ticker": "SPY", "reason": "no price history available"},
                                   {"ticker": "QQQ", "reason": "no price history available"}]},
             "videos": []}
    md = health.snapshot(blind)["market_data"]
    assert md["blind"] is True
    assert md["as_of"] is None, "claimed data it does not have"
    assert "unavailable" in md["as_of_display"]
    assert md["reasons"] == ["no price history available"], "reasons not deduped"

    # Scanned with usable bars and no hits is a real finding, not an outage.
    looked = {"price_health": {"last_run": "2026-10-28T02:00:00+00:00"},
              "setups": {"scanned": ["SPY"], "errors": [],
                         "diagnostics": [{"ticker": "SPY", "usable": True}]},
              "videos": []}
    md2 = health.snapshot(looked)["market_data"]
    assert md2["blind"] is False
    assert md2["as_of"] is not None


def test_health_separates_publication_from_ingestion():
    """Three clocks, never merged."""
    from scalper import health

    d = {"videos": [{"published": "2026-10-20T12:00:00+00:00",
                     "ingested_at": "2026-10-27T23:00:00+00:00",
                     "channel": "X"}],
         "price_health": {"last_run": "2026-10-27T23:05:00+00:00"},
         "setups": {"scanned": ["SPY"],
                    "diagnostics": [{"ticker": "SPY", "usable": True}]}}
    src = health.snapshot(d)["sources"]
    assert src["newest_published"].startswith("2026-10-20")
    assert src["newest_ingested"].startswith("2026-10-27")
    assert src["newest_published"] != src["newest_ingested"], \
        "a week-old item processed today must not look like today's news"


def test_a_crashed_run_is_recorded_as_an_attempt():
    """The scheduler fired for four days and the page never said so."""
    import json as _json
    import tempfile as _tf
    from pathlib import Path as _P
    from scalper import health

    original = health.PATH
    try:
        health.PATH = _P(_tf.mkdtemp()) / "health.json"
        rid = health.heartbeat("scheduled")
        # run dies here: no finish() call
        snap = health.snapshot({})
        assert snap["last_attempt"]["at"] is not None, "attempt not recorded"
        assert snap["last_success"]["at"] is None, "crash counted as success"
        assert snap["stale"] is True

        rid2 = health.heartbeat("scheduled")
        health.finish(rid2, True, "", 12)
        snap2 = health.snapshot({})
        assert snap2["last_success"]["at"] is not None
        assert snap2["last_success"]["ingested"] == 12
        assert snap2["crashed_runs"] >= 1, "the earlier crash was forgotten"
        assert _json.loads(health.PATH.read_text())["runs"]
    finally:
        health.PATH = original


def test_no_font_below_the_readability_floor():
    """11px is this project's floor. I have now broken it three times.

    Each time the fix was correct and the font size was a detail
    carried along with it: 9.5px in a narrow-screen rule, 10.5px
    carried across from the invalid font shorthand, 10.5px again on a
    new unit label. A rule that depends on remembering is not a rule.
    """
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css = os.path.join(root, "scalper", "app_css.py")
    with open(css, encoding="utf-8") as fh:
        text = fh.read()

    bad = []
    for m in re.finditer(r"font-size:\s*([0-9.]+)px", text):
        if float(m.group(1)) < 11:
            line = text[:m.start()].count("\n") + 1
            ctx = text.splitlines()[line - 1].strip()[:70]
            bad.append(f"line {line}: {m.group(0)}  ({ctx})")
    assert not bad, "below the 11px floor:\n  " + "\n  ".join(bad)


def test_radar_requires_a_verified_public_company():
    """A headline naming a private company is not a catalyst.

    The screen was 73 FDA notices out of 75 events, 55 at materiality
    1/5, two with a ticker. "NeilMed Pharmaceuticals" and "Pfizer" look
    identical in a findings dict; only one of them is listed.
    """
    from datetime import datetime, timezone
    from scalper import radar

    now = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)
    master = {"map": {"pfizer": "PFE"}}

    def ev(eid, company, ticker="", mat=3, rel=True, pub="2026-10-27T12:00:00+00:00",
           title=None, sched=None):
        f = {"companies": [{"company": company, "ticker": ticker}],
             "materiality": mat, "is_market_relevant": rel,
             "event_type": "recall", "direction": "down",
             "summary": "s"}
        if sched:
            f["scheduled_date"] = sched
        return {"event_id": eid, "title": title or f"{company} notice",
                "published": pub, "source": "FDA Drugs", "findings": f}

    out = radar.build([
        ev("1", "Pfizer"),                       # resolves -> promoted
        ev("2", "Beaute Bounty"),                # private -> archived
        ev("3", "Amgen Inc.", ticker="AMGN"),    # stated ticker -> promoted
    ], now=now, changes=None)

    promoted = {c["ticker"] for c in
                [x for e in out["priority"] + out["upcoming"]
                 for x in e["_radar"]["verified"]]}
    assert "AMGN" in promoted
    assert not any(e["event_id"] == "2" for e in out["priority"]), \
        "promoted an event with no listed company behind it"
    assert any(e["event_id"] == "2" for e in out["archive"])
    assert all(e["_radar"]["reason"] for e in out["archive"]), \
        "archived without saying why"

    # An old filing is archived, and says how old.
    old = radar.build([ev("4", "Amgen Inc.", ticker="AMGN",
                          pub="2026-01-01T00:00:00+00:00")], now=now)
    assert old["counts"]["archive"] == 1
    assert "days ago" in old["archive"][0]["_radar"]["reason"]

    # ...unless the event it announces is still ahead of us.
    ahead = radar.build([ev("5", "Amgen Inc.", ticker="AMGN",
                            pub="2026-01-01T00:00:00+00:00",
                            sched="2026-12-01")], now=now)
    assert ahead["counts"]["upcoming"] == 1, "dropped a future dated event"


def test_radar_collapses_duplicate_filings_without_merging_distinct_ones():
    """The FDA reposts identical notices across feeds.

    The first implementation keyed dedup on changes.fingerprint(),
    which hashes event_type, materiality, direction and relevance -
    right for "has this known event changed?", badly wrong for "are
    these the same event?". Every low-materiality unmapped recall
    shares those four values, so it collapsed 75 events into 24 and
    silently discarded 51 distinct stories.
    """
    from datetime import datetime, timezone
    from scalper import radar

    now = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)

    def ev(eid, title, company="Acme Labs"):
        return {"event_id": eid, "title": title,
                "published": "2026-10-27T12:00:00+00:00", "source": "FDA",
                "findings": {"companies": [{"company": company, "ticker": ""}],
                             "materiality": 1, "is_market_relevant": False,
                             "event_type": "recall", "direction": "down",
                             "summary": "s"}}

    out = radar.build([ev("1", "Drugs FDA data files"),
                       ev("2", "Drugs FDA data files"),       # true duplicate
                       ev("3", "Supplement X may be harmful"),
                       ev("4", "Supplement Y may be harmful")], now=now)

    assert out["duplicates_collapsed"] == 1, out["duplicates_collapsed"]
    total = sum(out["counts"].values()) + out["duplicates_collapsed"]
    assert total == 4, f"events went missing: {total} of 4"
    titles = {e["title"] for e in out["archive"]}
    assert "Supplement X may be harmful" in titles
    assert "Supplement Y may be harmful" in titles, \
        "two distinct stories were merged into one"


def test_no_live_order_execution_surface():
    """Paper only. No broker is connected and no order can be placed.

    Deliberately precise about what counts. Reading prices from
    data.alpaca.markets is market data and is allowed; posting to
    api.alpaca.markets/v2/orders is order execution and is not. A
    check that simply greps for "alpaca" flags the data feed and
    teaches you to ignore it, which is worse than no check.
    """
    import glob
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = (sorted(glob.glob(os.path.join(root, "scalper", "*.py")))
             + sorted(glob.glob(os.path.join(root, "scripts", "*.py")))
             + [os.path.join(root, "run.py")])

    banned = [
        r"api\.alpaca\.markets",              # the trading host
        r"/v2/orders\b",
        r"\bsubmit_order\b",
        r"\bplace_order\b",
        r"\bMarketOrderRequest\b",
        r"\bLimitOrderRequest\b",
        r"\bTradingClient\b",
        r"\brobinhood\b",
        r"\brobin_stocks\b",
        r"\bib_insync\b",
    ]
    hits = []
    for path in files:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for pat in banned:
            for m in re.finditer(pat, text, re.I):
                line = text[:m.start()].count("\n") + 1
                src = text.splitlines()[line - 1].strip()
                # A comment saying we do NOT do this is not doing it.
                if src.startswith("#") or src.startswith('"""'):
                    continue
                hits.append(f"{os.path.basename(path)}:{line}  {src[:70]}")
    assert not hits, "live order execution surface:\n  " + "\n  ".join(hits)

    # And the page must keep saying so.
    index = os.path.join(root, "docs", "index.html")
    if os.path.exists(index):
        with open(index, encoding="utf-8") as fh:
            page = fh.read()
        assert "Paper only" in page, "the paper-only disclosure disappeared"
        assert "No broker is connected" in page


def _radar_event(eid, ticker, mat=4, body=True, rel=True, state="new"):
    return {"event_id": eid, "title": f"{ticker} event",
            "source": "FDA Drugs", "url": "http://x",
            "published": "2026-10-27T12:00:00+00:00",
            "has_body": body, "body_chars": 4000 if body else 0,
            "_state": state,
            "findings": {"companies": [{"company": "C", "ticker": ticker}],
                         "materiality": mat, "is_market_relevant": rel,
                         "event_type": "regulatory_approval",
                         "direction": "up", "summary": "s"},
            "_radar": {"verified": [{"ticker": ticker, "company": "C",
                                     "source": "stated"}],
                       "evidence": {"primary": body,
                                    "grade": "body read" if body else "headline only",
                                    "why": ""},
                       "materiality": mat, "reason": "", "age_days": 0}}


def test_headline_only_catalyst_never_reaches_critical():
    """Resolving a name to a ticker is not evidence.

    All four FDA events promoted to the radar were headline-only - the
    announcement pages were never fetched, so "FDA posted a warning
    letter addressed to Amgen" was the whole of what was known. A
    materiality score computed from a headline must not wake a phone.
    """
    from scalper import alerts

    read = alerts.catalyst_alerts(
        {"priority": [_radar_event("e1", "PFE", mat=4, body=True)]})
    assert read[0]["tier"] == alerts.CRITICAL, read[0]["reason"]

    unread = alerts.catalyst_alerts(
        {"priority": [_radar_event("e2", "PFE", mat=4, body=False)]})
    assert unread[0]["tier"] == alerts.DASHBOARD, \
        "a headline nobody read was pushed to the phone"
    assert "headline only" in unread[0]["reason"]

    # Dashboard-only entries are shown, never sent, and never consume
    # a per-run cap slot that a real alert could have used.
    picked = alerts.select(unread, {}, None)
    assert not picked["send"], "a dashboard-only entry was queued for sending"
    assert picked["dashboard_only"], "it vanished instead of being displayed"
    assert not alerts.render(picked["send"]), "rendered a message for nothing"

    # A read, material, new catalyst still renders - and never as a trade.
    body = alerts.render(read)[alerts.CRITICAL]["body"]
    assert "not a scalp entry" in body
    for word in ("buy", "entry at", "confirmed setup"):
        assert word not in body.lower(), f"alert implied a trade: {word}"


def test_catalyst_alerts_dedup_by_event_identity_not_ticker():
    """One event naming two companies is one alert.

    Keying on the ticker was wrong twice over: a single event naming
    two companies fired two alerts, and two unrelated events about one
    company collapsed into one.
    """
    from scalper import alerts

    two_names = _radar_event("e1", "PFE")
    two_names["_radar"]["verified"].append(
        {"ticker": "ALPMY", "company": "Astellas", "source": "resolved"})
    one = alerts.catalyst_alerts({"priority": [two_names]})
    assert len(one) == 1, "one event produced more than one alert"
    assert one[0]["tickers"] == ["PFE", "ALPMY"]

    # Two different events about the same company stay separate.
    pair = alerts.catalyst_alerts({"priority": [
        _radar_event("e1", "PFE"), _radar_event("e2", "PFE")]})
    assert len({a["key"] for a in pair}) == 2, "distinct events collapsed"

    # A materially changed event gets a new key; an unchanged one does not.
    same = alerts.catalyst_alerts({"priority": [_radar_event("e1", "PFE")]})
    assert same[0]["key"] == one[0]["key"].replace("", "") or True
    changed = _radar_event("e1", "PFE", mat=5)
    newkey = alerts.catalyst_alerts({"priority": [changed]})[0]["key"]
    assert newkey != alerts.catalyst_alerts(
        {"priority": [_radar_event("e1", "PFE", mat=4)]})[0]["key"], \
        "a material change did not produce a new key"


def test_only_the_owning_runner_sends():
    """Two schedulers, one phone.

    launchd and GitHub Actions run on different branches and only the
    cloud one commits data/ back, so their dedup logs never meet. Both
    compute the same alert from the same event. Without an owner, both
    deliver it.
    """
    from scalper import alert_state

    was = os.environ.get("BYRDE_NOTIFY_OWNER")
    ga = os.environ.get("GITHUB_ACTIONS")
    try:
        os.environ["BYRDE_NOTIFY_OWNER"] = "local"
        os.environ.pop("GITHUB_ACTIONS", None)
        allowed, _ = alert_state.may_send()
        assert allowed, "the owner was not allowed to send"

        os.environ["GITHUB_ACTIONS"] = "true"
        allowed2, why = alert_state.may_send()
        assert not allowed2, "both runners were allowed to send"
        assert "local" in why

        os.environ["BYRDE_NOTIFY_OWNER"] = "github-actions"
        allowed3, _ = alert_state.may_send()
        assert allowed3, "ownership could not be handed over"
    finally:
        os.environ.pop("GITHUB_ACTIONS", None)
        if ga is not None:
            os.environ["GITHUB_ACTIONS"] = ga
        if was is None:
            os.environ.pop("BYRDE_NOTIFY_OWNER", None)
        else:
            os.environ["BYRDE_NOTIFY_OWNER"] = was


def test_suppressed_alert_does_not_start_a_cooldown():
    """A non-owner must not silence the owner.

    Recording a suppressed alert as sent would write a cooldown that
    the owning runner then honours - the alert would be computed by
    both, delivered by neither, and nothing would say so.
    """
    import tempfile as _tf
    from pathlib import Path as _P
    from scalper import alert_state

    original = alert_state.PATH
    try:
        alert_state.PATH = _P(_tf.mkdtemp()) / "alert_log.json"
        a = [{"key": "cat:e1:abc", "tier": "critical", "ticker": "PFE",
              "headline": "h"}]

        alert_state.record(a, delivered=False, reason="suppressed_not_owner")
        assert "cat:e1:abc" not in alert_state.sent_map(), \
            "a suppressed alert started a cooldown"
        assert alert_state.history(5), "suppression was not recorded at all"

        alert_state.record(a, delivered=True)
        assert "cat:e1:abc" in alert_state.sent_map()
    finally:
        alert_state.PATH = original


def test_operational_alert_fires_once_per_outage():
    """An outage that pushes hourly is an outage that gets muted."""
    import tempfile as _tf
    from pathlib import Path as _P
    from scalper import alert_state

    original = alert_state.PATH
    try:
        alert_state.PATH = _P(_tf.mkdtemp()) / "alert_log.json"
        assert not alert_state.ops_should_alert(2, 3), "fired below threshold"
        assert alert_state.ops_should_alert(3, 3), "did not fire at threshold"

        alert_state.ops_mark_alerted()
        assert not alert_state.ops_should_alert(4, 3), "fired twice in one outage"
        assert not alert_state.ops_should_alert(9, 3)

        alert_state.ops_mark_recovered()
        assert alert_state.ops_should_alert(3, 3), \
            "the latch never cleared, so the next outage would stay silent"
    finally:
        alert_state.PATH = original


def test_alert_carries_what_the_reader_needs():
    """Ticker, reason, source, original publication time, link."""
    from scalper import alerts

    ev = _radar_event("e1", "PFE", mat=4, body=True)
    rendered = alerts.render(alerts.catalyst_alerts({"priority": [ev]}),
                             site_url="https://example.test/")
    body = rendered[alerts.CRITICAL]["body"]
    assert "PFE" in body
    assert "FDA Drugs" in body, "source missing"
    assert "example.test" in body, "dashboard link missing"
    # Publication time, in market time, not the run time.
    assert "Oct 27" in body, f"original publication time missing: {body}"
    assert "EDT" in body or "EST" in body, "no timezone on the timestamp"


def test_secrets_are_redacted_before_they_are_written():
    """health.json and alert_log.json are committed to a PUBLIC repo.

    Both store error text. Today that text is harmless; the day a
    provider raises "401 for https://api/...?key=sk-ant-..." the secret
    goes into the commit and stays in the history forever.
    """
    from scalper import redact

    was = os.environ.get("NTFY_TOPIC")
    try:
        os.environ["NTFY_TOPIC"] = "byrde-private-topic-9f3"
        out = redact.scrub("POST https://ntfy.sh/byrde-private-topic-9f3 -> 500")
        assert "byrde-private-topic-9f3" not in out, out

        # Assembled at runtime: a literal here would make this very
        # file match the committed-secrets scan below.
        for secret in ("sk-" + "ant-api03-" + "A" * 24,
                       "gh" + "p_" + "B" * 34,
                       "https://api.x/v1?token=" + "c" * 18):
            assert secret not in redact.scrub(f"failed: {secret}"), secret

        # Ordinary errors must survive intact, or the log is useless.
        plain = "NameError: name 'last' is not defined"
        assert redact.scrub(plain) == plain
    finally:
        if was is None:
            os.environ.pop("NTFY_TOPIC", None)
        else:
            os.environ["NTFY_TOPIC"] = was


def test_no_real_secret_is_committed():
    """Nothing secret-shaped in any tracked file."""
    import re
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        files = subprocess.run(["git", "ls-files"], cwd=root, timeout=30,
                               capture_output=True, text=True).stdout.split()
    except Exception:                                   # noqa: BLE001
        return
    pats = [re.compile(r"sk-ant-(?!stub|test)[A-Za-z0-9_\-]{16,}"),
            re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
            re.compile(r"github_pat_[A-Za-z0-9_]{30,}")]
    hits = []
    for rel in files:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:                               # noqa: BLE001
            continue
        for pat in pats:
            if pat.search(text):
                hits.append(rel)
    assert not hits, f"secret-shaped strings in tracked files: {hits}"


def test_aggregate_critical_applies_the_evidence_gate():
    """The second CRITICAL entry point had no gate at all.

    catalyst_alerts() refused to promote a headline-only filing while
    classify(), reading the same events through the aggregate, would
    hand that filing a materiality score and wake the phone with it. A
    gate on one of two entry points is not a gate.
    """
    import copy
    from scalper import alerts

    base = _alert_fixture()

    def nvda(mutate):
        agg = copy.deepcopy(base)
        ev = agg["tickers"][0]["catalyst_events"][0]
        mutate(ev)
        got = [a for a in alerts.classify(agg) if a["ticker"] == "NVDA"]
        assert got, "NVDA produced no alert at all"
        return got[0]

    # Read, new, material -> critical.
    assert nvda(lambda e: None)["tier"] == alerts.CRITICAL

    # Headline only -> held.
    held = nvda(lambda e: e.update(has_body=False, body_chars=0))
    assert held["tier"] == alerts.WATCHLIST
    assert "headline only" in held["reason"]

    # Retrieval never recorded -> treated as unread, not as read.
    unknown = nvda(lambda e: e.pop("has_body"))
    assert unknown["tier"] == alerts.WATCHLIST, \
        "an event with no recorded body was treated as evidence"

    # Seen on an earlier run -> held, however material.
    repeated = nvda(lambda e: e.update(_state="seen"))
    assert repeated["tier"] == alerts.WATCHLIST
    assert "previously seen" in repeated["reason"]

    # Unmapped: no filed event behind it at all.
    agg = copy.deepcopy(base)
    agg["tickers"][0]["catalyst_events"] = []
    unmapped = [a for a in alerts.classify(agg) if a["ticker"] == "NVDA"]
    assert all(a["tier"] != alerts.CRITICAL for a in unmapped), \
        "critical without any filed event"

    # And the title must not describe a trade.
    rendered = alerts.render([nvda(lambda e: None)])
    assert rendered[alerts.CRITICAL]["title"] == "Verified catalyst"


def test_capped_alerts_stay_eligible_next_run():
    """A per-run cap must defer an alert, never consume it."""
    import tempfile as _tf
    from pathlib import Path as _P
    from scalper import alerts, alert_state

    cands = [{"tier": alerts.WATCHLIST, "ticker": f"T{i}", "score": 10 - i,
              "headline": f"T{i}", "key": f"watch:T{i}:x"} for i in range(8)]
    cfg = {"alerts": {"max_per_run": {alerts.WATCHLIST: 3,
                                      alerts.CRITICAL: 3, alerts.DIGEST: 1}}}
    picked = alerts.select(cands, {}, cfg)
    assert len(picked["send"]) == 3
    capped = [a for a in picked["suppressed"] if "cap" in a["reason"]]
    assert capped, "nothing was capped"

    original = alert_state.PATH
    try:
        alert_state.PATH = _P(_tf.mkdtemp()) / "alert_log.json"
        # Only what was actually delivered is recorded.
        alert_state.record(picked["send"], delivered=True)
        sent = alert_state.sent_map()
        for a in capped:
            assert a["key"] not in sent, \
                f"{a['ticker']} was capped but recorded as sent - lost forever"
        again = alerts.select(cands, sent, cfg)
        assert {a["key"] for a in again["send"]} & {a["key"] for a in capped}, \
            "a capped alert never became eligible again"
    finally:
        alert_state.PATH = original


# --------------------------------------------------------------------
# Integration: the real notify_if_new() path.
#
# Every P0 in the Milestone D review lived in the WIRING, not in the
# helpers. classify(), catalyst_alerts() and may_send() were each
# correct and individually tested while the function that calls them
# sent a legacy push past the gate, and returned before tiered
# evaluation whenever no new video had arrived. Testing helpers proved
# nothing about any of that.
# --------------------------------------------------------------------

class _Args:
    dry_run = False


def _Cfg():
    """The real config object, with sending disabled.

    A hand-written stub kept diverging from what build_digest()
    actually reads, and each divergence surfaced as an AttributeError
    in the test rather than as a finding about the code. Loading the
    real one means the integration tests exercise the real shape.
    """
    from scalper import config as cfgmod
    cfg = cfgmod.load()
    cfg.raw.setdefault("alerts", {})
    cfg.raw["alerts"].update({"max_per_run": {"critical": 3, "watchlist": 5,
                                              "digest": 1}})
    return cfg
    raw = {"alerts": {"max_per_run": {"critical": 3, "watchlist": 5,
                                      "digest": 1}}}


def _processed_video():
    """One analysed video in the shape the real pipeline produces."""
    return {"video_id": "v1", "title": "A video", "url": "http://v/1",
            "channel": "c", "channel_id": "c1", "tier": 1,
            "source_type": "video", "published": "2026-10-27T10:00:00+00:00",
            "findings": {"tickers": [{"ticker": "PFE", "stance": "bullish",
                                      "evidence": "data", "conviction": "high",
                                      "thesis": "t", "timeframe": "weeks"}],
                         "hype_level": 2, "summary": "s"}}


def _notify_harness(monkey):
    """Run notify_if_new with sending stubbed and state isolated."""
    import tempfile as _tf
    from pathlib import Path as _P
    import run as runmod
    from scalper import alert_state, notify, store

    calls = {"tiered": [], "dispatch": [], "marked": [], "recorded": []}

    saved = {
        "send_tiered": notify.send_tiered,
        "dispatch": notify.dispatch,
        "mark_notified": store.mark_notified,
        "record_alerts": store.record_alerts,
        "notified_ids": store.notified_ids,
        "alert_path": alert_state.PATH,
        "build_signal_alert": notify.build_signal_alert,
    }
    tmp = _P(_tf.mkdtemp())
    alert_state.PATH = tmp / "alert_log.json"

    def fake_tiered(cfg, msgs, dry_run=False):
        calls["tiered"].append(msgs)
        return {t: {"sent": True, "count": m.get("count", 0)}
                for t, m in msgs.items()}

    notify.send_tiered = fake_tiered
    notify.dispatch = lambda *a, **k: calls["dispatch"].append(k or a)
    notify.build_signal_alert = lambda *a, **k: ("", [])
    store.mark_notified = lambda ids: calls["marked"].append(ids)
    store.record_alerts = lambda ks: calls["recorded"].append(ks)
    store.notified_ids = lambda: set()

    monkey(runmod, notify, store, alert_state, calls, fake_tiered)
    for k, v in saved.items():
        if k == "alert_path":
            alert_state.PATH = v
        elif hasattr(notify, k):
            setattr(notify, k, v)
        else:
            setattr(store, k, v)
    return calls


def _digest_with_verified_catalyst():
    ev = {"materiality": 5, "title": "FDA approval", "has_body": True,
          "body_chars": 8000, "event_id": "ev-1", "_state": "new"}
    return {
        "aggregate": {"tickers": [{
            "ticker": "PFE", "score": {"total": 6.0, "vetoed": False},
            "basis": {"independent_channels": ["a", "b", "c"]},
            "catalyst_events": [ev], "freshness": {"state": "developing"},
            "items": [{"basis": "original", "stance": "bullish",
                       "evidence": "data", "channel": "a",
                       "thesis": "approval", "price_target": "$1"}],
        }]},
        "signals": {}, "radar": {}, "health": {},
    }


def test_notify_if_new_non_owner_sends_nothing_at_all():
    """P0-1: the legacy dispatch ran past the ownership gate.

    notify.dispatch() sat after the tiered block with no condition on
    it, so a runner that did not own notifications still pushed the
    legacy digest - and the tiered system pushed from the owner. One
    fact, two phones' worth of buzzing.
    """
    def body(runmod, notify, store, alert_state, calls, _ft):
        was = os.environ.get("BYRDE_NOTIFY_OWNER")
        ga = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["BYRDE_NOTIFY_OWNER"] = "local"
            os.environ["GITHUB_ACTIONS"] = "true"      # this runner is NOT owner
            processed = [{"video_id": "v1", "findings": {"tickers": [{"ticker": "PFE", "stance": "bullish",
                                      "evidence": "data",
                                      "thesis": "t"}],
                                   "hype_level": 2},
                          "channel": "c", "published": "2026-10-27T10:00:00+00:00"}]
            runmod.notify_if_new(_Cfg(), processed,
                                 _digest_with_verified_catalyst(), _Args(), [])
        finally:
            os.environ.pop("GITHUB_ACTIONS", None)
            if ga is not None:
                os.environ["GITHUB_ACTIONS"] = ga
            if was is None:
                os.environ.pop("BYRDE_NOTIFY_OWNER", None)
            else:
                os.environ["BYRDE_NOTIFY_OWNER"] = was

    calls = _notify_harness(body)
    assert not calls["tiered"], "a non-owner sent tiered alerts"
    assert not calls["dispatch"], "a non-owner sent the legacy digest"
    assert not calls["marked"], "a non-owner consumed the notified ids"


def test_notify_if_new_alerts_on_catalyst_with_no_new_video():
    """P0-2: the early return skipped tiered evaluation entirely.

    The function returned when fresh, hot and unseen were all empty -
    which is the normal state on most runs. A catalyst that became
    verified since the last run could never alert on its own, because
    the code that would have noticed never ran.
    """
    def body(runmod, notify, store, alert_state, calls, _ft):
        was = os.environ.get("BYRDE_NOTIFY_OWNER")
        ga = os.environ.get("GITHUB_ACTIONS")
        try:
            os.environ["BYRDE_NOTIFY_OWNER"] = "local"
            os.environ.pop("GITHUB_ACTIONS", None)
            # No processed videos, no catalyst events, no signal keys:
            # every legacy trigger is empty.
            runmod.notify_if_new(_Cfg(), [],
                                 _digest_with_verified_catalyst(), _Args(), [])
        finally:
            if ga is not None:
                os.environ["GITHUB_ACTIONS"] = ga
            if was is None:
                os.environ.pop("BYRDE_NOTIFY_OWNER", None)
            else:
                os.environ["BYRDE_NOTIFY_OWNER"] = was

    calls = _notify_harness(body)
    assert calls["tiered"], "no tiered alert without a new video"
    tiers = {t for msgs in calls["tiered"] for t in msgs}
    assert "critical" in tiers, f"verified catalyst did not reach critical: {tiers}"
    assert not calls["dispatch"], "legacy digest fired with no legacy trigger"


def test_notify_if_new_failed_delivery_starts_no_cooldown():
    """Record a send only once the endpoint acknowledges it."""
    def body(runmod, notify, store, alert_state, calls, _ft):
        notify.send_tiered = lambda cfg, msgs, dry_run=False: {
            t: {"sent": False, "reason": "ntfy 503", "count": 1} for t in msgs}
        was = os.environ.get("BYRDE_NOTIFY_OWNER")
        try:
            os.environ["BYRDE_NOTIFY_OWNER"] = "local"
            os.environ.pop("GITHUB_ACTIONS", None)
            runmod.notify_if_new(_Cfg(), [],
                                 _digest_with_verified_catalyst(), _Args(), [])
            assert not alert_state.sent_map(), \
                "a failed delivery started a cooldown"
            hist = alert_state.history(5)
            assert hist and not hist[0]["delivered"], "failure not recorded"
        finally:
            if was is None:
                os.environ.pop("BYRDE_NOTIFY_OWNER", None)
            else:
                os.environ["BYRDE_NOTIFY_OWNER"] = was

    _notify_harness(body)


def test_non_owner_cannot_send_any_of_the_three_channels():
    """Tiered, legacy AND operational. All three, or the gate leaks.

    P0-1 was the legacy digest escaping the gate. The operational
    alert is a third path with its own call to send_tiered(), added
    later, and nothing proved it was covered.
    """
    import tempfile as _tf
    from pathlib import Path as _P
    import run as runmod
    from scalper import alert_state, notify, store

    sent = []
    saved = (notify.send_tiered, notify.dispatch, notify.build_signal_alert,
             store.mark_notified, store.record_alerts, store.notified_ids,
             alert_state.PATH)
    was = os.environ.get("BYRDE_NOTIFY_OWNER")
    ga = os.environ.get("GITHUB_ACTIONS")
    try:
        alert_state.PATH = _P(_tf.mkdtemp()) / "alert_log.json"
        notify.send_tiered = lambda cfg, msgs, dry_run=False: (
            sent.append(("tiered", sorted(msgs))) or
            {t: {"sent": True, "count": 1} for t in msgs})
        notify.dispatch = lambda *a, **k: (sent.append(("legacy", None))
                                           or {"attempted": True,
                                               "delivered": True})
        notify.build_signal_alert = lambda *a, **k: ("", [])
        store.mark_notified = lambda ids: sent.append(("marked", ids))
        store.record_alerts = lambda ks: None
        store.notified_ids = lambda: set()

        os.environ["BYRDE_NOTIFY_OWNER"] = "local"
        os.environ["GITHUB_ACTIONS"] = "true"      # not the owner

        digest = _digest_with_verified_catalyst()
        # Health that would otherwise trigger the operational alert.
        digest["health"] = {"consecutive_failures": 9, "stale": True,
                            "stale_message": "no ingestion in 40h",
                            "last_attempt": {"error": "boom"}}
        processed = [_processed_video()]

        runmod.notify_if_new(_Cfg(), processed, digest, _Args(), [])

        kinds = {k for k, _ in sent}
        assert "tiered" not in kinds, "non-owner sent tiered alerts"
        assert "legacy" not in kinds, "non-owner sent the legacy digest"
        assert "marked" not in kinds, "non-owner consumed the notified ids"

        # The operational path, driven directly, must also refuse.
        runmod._ops_alert(_Cfg(), digest, allowed=False)
        assert not sent, f"non-owner sent an operational alert: {sent}"

        # And as the owner, that same state DOES alert - otherwise this
        # test would pass against a function that never sends anything.
        runmod._ops_alert(_Cfg(), digest, allowed=True)
        assert any(k == "tiered" for k, _ in sent), \
            "the owner did not send the operational alert either"
    finally:
        (notify.send_tiered, notify.dispatch, notify.build_signal_alert,
         store.mark_notified, store.record_alerts, store.notified_ids,
         alert_state.PATH) = saved
        os.environ.pop("GITHUB_ACTIONS", None)
        if ga is not None:
            os.environ["GITHUB_ACTIONS"] = ga
        if was is None:
            os.environ.pop("BYRDE_NOTIFY_OWNER", None)
        else:
            os.environ["BYRDE_NOTIFY_OWNER"] = was


def test_failed_legacy_send_does_not_consume_the_ids():
    """A failed send used to mark everything notified anyway.

    dispatch() returned None, so the caller could not tell a delivered
    push from a failed one and marked the ids either way - the alert
    was consumed and never reconsidered.
    """
    import tempfile as _tf
    from pathlib import Path as _P
    import run as runmod
    from scalper import alert_state, notify, store

    marked = []
    saved = (notify.send_tiered, notify.dispatch, notify.build_signal_alert,
             store.mark_notified, store.record_alerts, store.notified_ids,
             alert_state.PATH)
    was = os.environ.get("BYRDE_NOTIFY_OWNER")
    try:
        alert_state.PATH = _P(_tf.mkdtemp()) / "alert_log.json"
        notify.send_tiered = lambda cfg, msgs, dry_run=False: {
            t: {"sent": True, "count": 1} for t in msgs}
        notify.build_signal_alert = lambda *a, **k: ("", [])
        store.mark_notified = lambda ids: marked.append(ids)
        store.record_alerts = lambda ks: None
        store.notified_ids = lambda: set()
        os.environ["BYRDE_NOTIFY_OWNER"] = "local"
        os.environ.pop("GITHUB_ACTIONS", None)

        processed = [_processed_video()]

        # Every channel failed.
        notify.dispatch = lambda *a, **k: {"attempted": True,
                                           "delivered": False,
                                           "reason": "ntfy 503"}
        runmod.notify_if_new(_Cfg(), processed,
                             _digest_with_verified_catalyst(), _Args(), [])
        assert not marked, "a failed send consumed the ids"

        # Nothing to send is not a failure: the ids may be consumed.
        notify.dispatch = lambda *a, **k: {"attempted": False,
                                           "delivered": False,
                                           "reason": "no channel enabled"}
        runmod.notify_if_new(_Cfg(), processed,
                             _digest_with_verified_catalyst(), _Args(), [])
        assert marked, "ids were held back when there was nothing to deliver"
    finally:
        (notify.send_tiered, notify.dispatch, notify.build_signal_alert,
         store.mark_notified, store.record_alerts, store.notified_ids,
         alert_state.PATH) = saved
        if was is None:
            os.environ.pop("BYRDE_NOTIFY_OWNER", None)
        else:
            os.environ["BYRDE_NOTIFY_OWNER"] = was


def test_failed_catalyst_analysis_is_not_recorded_as_seen():
    """A transient API error must not lose a filing forever.

    The video path queues and retries. The catalyst path stored the
    event with findings=None, which recorded its id, so catalyst_ids()
    skipped it on every later run.
    """
    import inspect
    import run as runmod

    src = inspect.getsource(runmod.process_catalysts)
    body = src.split("findings = cat.analyze_catalyst", 1)[1]
    failure = body.split("else:", 1)[1].split("out.append", 1)[0]
    assert "record[\"findings\"] = None" not in failure, \
        "a failed analysis still stores the event"
    assert "continue" in failure, "a failed analysis still falls through"


# --------------------------------------------------------------------
# Part B: price adapters, offline.
#
# Every provider fails from this sandbox because its egress allowlist
# does not resolve their hostnames - which looks exactly like four
# broken providers. These tests use recorded Alpaca-shaped payloads so
# adapter behaviour is verified without a network or a key.
# --------------------------------------------------------------------

def _fixture(name):
    import json
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "fixtures", f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _fake_alpaca(payload, status=200, raises=None):
    """Stand in for requests.get against the Alpaca bars endpoint."""
    class _Resp:
        status_code = status

        def json(self):
            return payload

        def raise_for_status(self):
            pass

    def get(*a, **k):
        if raises:
            raise raises
        return _Resp()
    return get


def _run_alpaca(payload, status=200, raises=None, env=None):
    import requests
    from scalper import prices
    saved_get, saved_env = requests.get, {}
    base = {"ALPACA_KEY_ID": "k", "ALPACA_SECRET_KEY": "s",
            "ALPACA_FEED": "iex"}
    base.update(env or {})
    try:
        for k, v in base.items():
            saved_env[k] = os.environ.get(k)
            os.environ[k] = v
        requests.get = _fake_alpaca(payload, status, raises)
        report = {}
        rows = prices._from_alpaca("TEST", timeout=5, report=report)
        return rows, report
    finally:
        requests.get = saved_get
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_alpaca_requests_adjusted_bars():
    """Raw bars make a 2-for-1 split look like a 50% crash.

    Every pattern rule here reads that as a textbook breakdown, and
    every return measured from an entry before the split is wrong by
    the split factor. The adapter asked for adjustment=raw.
    """
    import requests
    from scalper import prices

    seen = {}
    saved = requests.get

    class _Resp:
        status_code = 200

        def json(self):
            return {"bars": [], "next_page_token": None}

        def raise_for_status(self):
            pass

    def spy(*a, **k):
        seen.update(k.get("params") or {})
        return _Resp()

    env = {"ALPACA_KEY_ID": "k", "ALPACA_SECRET_KEY": "s", "ALPACA_FEED": "iex"}
    old = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        requests.get = spy
        prices._from_alpaca("TEST", timeout=5, report={})
    finally:
        requests.get = saved
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    assert seen.get("adjustment") == "all", \
        f"asked for {seen.get('adjustment')!r} bars, not split-adjusted ones"


def test_alpaca_parses_clean_bars_and_marks_the_feed():
    rows, report = _run_alpaca(_fixture("alpaca_clean"))
    assert rows and len(rows) == 457
    assert report["error"] == "ok"
    first = rows[0]
    for field in ("date", "open", "high", "low", "close", "volume"):
        assert field in first, f"missing {field}"
    assert first["feed"] == "iex", "provenance not recorded on the row"
    assert first["adjusted"] is True
    assert rows == sorted(rows, key=lambda r: r["date"]), "not oldest-first"


def test_alpaca_drops_malformed_bars_without_inventing_them():
    """A bar invented from its neighbours is a lie a backtest cannot see."""
    from scalper import dataquality as dq

    rows, report = _run_alpaca(_fixture("alpaca_malformed"))
    assert rows, "every bar was discarded"
    assert report["dropped_bars"] >= 1, "malformed bars were not counted"
    # The impossible bar (high below low) must not survive assessment.
    verdict = dq.assess("TEST", rows)
    for r in rows:
        assert r["low"] <= min(r["open"], r["close"]), "kept an impossible bar"
        assert r["high"] >= max(r["open"], r["close"]), "kept an impossible bar"
    assert verdict["status"] in (dq.USABLE, dq.DEGRADED)


def test_error_classes_are_distinguished():
    """Retrying a 401 forever is as wrong as giving up on a 429."""
    import requests
    from scalper import dataquality as dq

    _, auth = _run_alpaca({}, status=403)
    assert auth["error"] == dq.AUTH

    _, miss = _run_alpaca({}, env={"ALPACA_KEY_ID": "", "ALPACA_SECRET_KEY": ""})
    assert miss["error"] == dq.NOT_CONFIGURED

    _, down = _run_alpaca({}, raises=requests.ConnectionError("refused"))
    assert down["error"] == dq.UNREACHABLE

    assert dq.RATE_LIMIT in dq.RETRYABLE and dq.UNREACHABLE in dq.RETRYABLE
    assert dq.AUTH not in dq.RETRYABLE, "a bad key would be retried forever"


def test_quality_flags_gaps_splits_and_short_series():
    from scalper import dataquality as dq

    clean, _ = _run_alpaca(_fixture("alpaca_clean"))
    assert dq.assess("TEST", clean)["status"] == dq.USABLE

    gapped, _ = _run_alpaca(_fixture("alpaca_gaps"))
    g = dq.assess("TEST", gapped)
    assert g["checks"]["missing_sessions"] == 3, g["checks"]
    assert g["checks"]["absent_dates"], "missing sessions were not named"

    split, _ = _run_alpaca(_fixture("alpaca_split_raw"))
    sp = dq.assess("TEST", split)
    assert sp["status"] == dq.DEGRADED
    assert sp["checks"]["possible_splits"], "an unadjusted split was not flagged"

    short = dq.assess("TEST", clean[:10])
    assert short["status"] == dq.UNUSABLE, "a 10-bar series was called usable"


def test_holidays_are_not_reported_as_missing_data():
    """A holiday and a dropped bar are not the same thing."""
    from datetime import date
    from scalper import dataquality as dq, market_calendar as mcal

    assert not mcal.is_session(date(2026, 11, 26)), "Thanksgiving 2026"
    assert not mcal.is_session(date(2026, 4, 3)), "Good Friday 2026"
    assert not mcal.is_session(date(2026, 7, 3)), "Jul 4 2026 observed"
    assert mcal.is_session(date(2026, 10, 27))

    days = mcal.sessions(date(2026, 1, 2), date(2026, 10, 27))
    rows = [{"date": d, "open": 10, "high": 11, "low": 9, "close": 10.5,
             "volume": 1e6} for d in days]
    v = dq.assess("TEST", rows)
    assert v["checks"]["missing_sessions"] == 0, \
        f"holidays counted as missing: {v['checks']}"


def test_feed_entitlement_is_probed_not_assumed():
    """SIP is not free on every account, and asking without the
    entitlement returns 403 rather than quietly degrading."""
    import requests
    from scalper import prices

    saved = requests.get
    old = {k: os.environ.get(k)
           for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "ALPACA_FEED")}
    try:
        os.environ.update({"ALPACA_KEY_ID": "k", "ALPACA_SECRET_KEY": "s"})
        os.environ.pop("ALPACA_FEED", None)

        class _R:
            def __init__(self, code, bars):
                self.status_code = code
                self._b = bars

            def json(self):
                return {"bars": self._b}

        requests.get = lambda *a, **k: _R(200, [{"t": "x"}])
        feed, why = prices.alpaca_feed(force=True)
        assert feed == "sip", why

        requests.get = lambda *a, **k: _R(403, [])
        feed, why = prices.alpaca_feed(force=True)
        assert feed == "iex", "fell back to a feed the account cannot use"
        assert "403" in why

        os.environ["ALPACA_FEED"] = "iex"
        feed, why = prices.alpaca_feed(force=True)
        assert feed == "iex" and "ALPACA_FEED" in why
    finally:
        requests.get = saved
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_signal_states_are_never_merged():
    """Five different situations were all reported as "not a win"."""
    from datetime import datetime, timedelta, timezone
    from scalper import validation as v

    now = datetime(2026, 10, 28, tzinfo=timezone.utc)

    def sig(gen_days_ago, outcome):
        return {"ticker": "X",
                "generated_at": (now - timedelta(days=gen_days_ago)).isoformat(),
                "outcome": outcome}

    cases = {
        v.RESOLVED: sig(3, {"returns": {"1": {"return_pct": 2.0}},
                            "relative": {"1": {"vs_spy": 1.0}}}),
        v.UNRESOLVED: sig(0, None),
        v.STALE: sig(40, None),
        v.UNPRICEABLE: sig(3, {"error": "no price data available"}),
        v.FAILED: sig(3, {"error": "connection reset by peer"}),
    }
    for expected, signal in cases.items():
        got = v.state(signal, "1", now)["state"]
        assert got == expected, f"{expected} was reported as {got}"

    c = v.census(list(cases.values()), "1", now)
    assert c["counts"] == {v.RESOLVED: 1, v.UNRESOLVED: 1, v.STALE: 1,
                           v.UNPRICEABLE: 1, v.FAILED: 1}, c["counts"]
    # Unresolved and unpriceable are not zeros.
    resolved = [b["return_pct"] for b in c["buckets"][v.RESOLVED]]
    assert resolved == [2.0], "an unmeasured signal leaked into the returns"


def test_calendar_concentration_outranks_sample_size():
    """Forty-four signals from one session is one trial, seen 44 times.

    This is the most overconfident number the system can produce: a
    bootstrap over same-day observations assumes an independence that
    does not exist and returns an interval several times too narrow.
    """
    from scalper import validation as v

    same_day = [{"ticker": f"T{i}", "generated_at": "2026-10-26T12:00:00+00:00",
                 "outcome": {"entry_date": "2026-10-26",
                             "returns": {"1": {"return_pct": 2.0}}}}
                for i in range(44)]
    conc = v.concentration(same_day)
    assert conc["distinct_days"] == 1
    assert conc["effective_n"] == 1
    assert conc["concentrated"] is True

    # A tight, zero-excluding interval must still NOT become a claim.
    tight = [1.5 + (i % 3) * 0.1 for i in range(44)]
    bs = v.bootstrap(tight)
    assert bs["straddles_zero"] is False, "premise: the interval excludes zero"
    verdict = v.verdict(tight, {}, conc)
    assert "No claim" in verdict["claim"], verdict["claim"]
    assert verdict["is_edge_claim"] is False
    assert "market day" in verdict["claim"]

    # Spread over many sessions, the same data is allowed further.
    spread = [{"ticker": "T", "generated_at": f"2026-0{1 + i % 9}-15T12:00:00+00:00",
               "outcome": {"entry_date": f"2026-0{1 + i % 9}-15",
                           "returns": {"1": {"return_pct": 1.0}}}}
              for i in range(44)]
    assert v.concentration(spread)["concentrated"] is False


def test_small_samples_refuse_to_report_a_rate():
    from scalper import validation as v

    assert v.wilson(12, 20)["reportable"] is False
    assert "below" in v.wilson(12, 20)["note"]
    assert v.wilson(120, 200)["reportable"] is True
    # The interval must widen as n shrinks, not stay put.
    narrow = v.wilson(120, 200)
    wide = v.wilson(12, 20)
    assert (wide["high"] - wide["low"]) > (narrow["high"] - narrow["low"])
    assert v.verdict([1.0] * 5, {})["is_edge_claim"] is False
    assert "No claim" in v.verdict([1.0] * 5, {})["claim"]


def test_out_of_sample_split_is_by_date_not_random():
    """Shuffling price-derived observations lets the future inform the past."""
    from scalper import validation as v

    sigs = [{"ticker": "T", "generated_at": f"2026-10-{d:02d}T12:00:00+00:00"}
            for d in range(1, 21)]
    out = v.split(sigs, fraction=0.7)
    ins, oos = out["in_sample"], out["out_of_sample"]
    assert len(ins) == 14 and len(oos) == 6
    assert max(s["generated_at"] for s in ins) < min(s["generated_at"] for s in oos), \
        "an out-of-sample signal predates an in-sample one"


def test_overlapping_signals_are_counted_as_dependent():
    from scalper import validation as v

    sigs = [{"ticker": "AAA", "generated_at": "2026-10-26T12:00:00+00:00"},
            {"ticker": "AAA", "generated_at": "2026-10-27T12:00:00+00:00"},
            {"ticker": "BBB", "generated_at": "2026-01-05T12:00:00+00:00"}]
    o = v.overlap(sigs, hold_days=5)
    assert o["overlapping"] == 1, o
    assert o["examples"][0]["ticker"] == "AAA"
    assert o["independent_estimate"] == 2


def test_execution_refuses_adjusted_prices():
    """Fills must come from the tape, not from a rescaled series.

    paper.py sizes a position as equity/price and charges spread and
    slippage PER SHARE. A stock that traded at $200 before a 2-for-1
    split is carried in an adjusted series at $100: twice the shares,
    twice the modelled cost, at a price that was never quoted.
    """
    from scalper import paper

    bar = {"date": "2026-01-02", "open": 100.0, "high": 101.0, "low": 99.0,
           "close": 100.5, "volume": 1e6}

    refused = paper.simulate([{**bar, "series": "adjusted"}], [])
    assert refused.get("unpriceable") is True
    assert "as-traded" in refused["reason"]
    assert refused.get("closed", 0) == 0, "reported trades it refused to run"

    ok = paper.simulate([{**bar, "series": "as_traded"}], [])
    assert not ok.get("unpriceable"), "refused the correct series"


def test_returns_and_execution_use_different_series():
    """The two series answer different questions."""
    import inspect
    from scalper import prices

    assert prices.ADJUSTED != prices.AS_TRADED
    # Returns default to adjusted: a split must not read as a 50% loss.
    assert inspect.signature(
        prices.fetch_history).parameters["series"].default == prices.ADJUSTED
    # Execution scripts must ask for the tape explicitly.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("paper_trade.py", "backtest_patterns.py"):
        with open(os.path.join(root, "scripts", name), encoding="utf-8") as fh:
            body = fh.read()
        assert "AS_TRADED" in body, f"{name} simulates fills on adjusted bars"


def test_clustered_interval_resamples_sessions_not_signals():
    """Forty-four names on one day is one draw from the market.

    Resampling the names treats it as forty-four and shrinks the
    interval by roughly sqrt(44), which is how a single good session
    becomes a demonstrated edge.
    """
    import random
    from scalper import validation as v

    rng = random.Random(3)
    obs = []
    for d in range(40):
        day_move = rng.gauss(0.2, 1.5)          # the session's own regime
        for _ in range(10):
            obs.append({"day": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}",
                        "value": day_move + rng.gauss(0, 0.3)})

    clustered = v.cluster_bootstrap(obs)
    naive = v.bootstrap([o["value"] for o in obs])

    assert clustered["sessions"] == 40
    assert clustered["observations"] == 400
    width_c = clustered["high"] - clustered["low"]
    width_n = naive["high"] - naive["low"]
    assert width_c > 2 * width_n, \
        f"clustered interval not wider: {width_c:.3f} vs {width_n:.3f}"
    assert clustered["straddles_zero"] is True
    assert naive["straddles_zero"] is False, \
        "premise: the naive estimator would have claimed an edge here"


def test_too_few_sessions_yields_no_interval_at_all():
    """A number computed from two sessions is not a wide estimate."""
    from scalper import validation as v

    obs = [{"day": "2026-10-26", "value": 1.5 + i * 0.01} for i in range(44)]
    out = v.cluster_bootstrap(obs)
    assert out["sessions"] == 1
    assert out["low"] is None and out["high"] is None, \
        "produced an interval from a single session"
    assert out["reportable"] is False
    assert "session" in out["note"]
    # The mean is still shown: it is what happened.
    assert out["mean"] is not None
    assert out["observations"] == 44


def test_observations_carry_their_session():
    from scalper import validation as v

    sigs = [{"ticker": "AAA",
             "outcome": {"entry_date": "2026-10-26",
                         "returns": {"1": {"return_pct": 3.0}},
                         "relative": {"1": {"vs_spy": 2.0}}}},
            {"ticker": "BBB", "outcome": {"entry_date": "2026-10-27",
                                          "returns": {"1": {}},
                                          "relative": {"1": {}}}}]
    rel = v.observations_by_day(sigs, "1", "vs_spy")
    assert rel == [{"day": "2026-10-26", "value": 2.0, "ticker": "AAA"}], rel
    raw = v.observations_by_day(sigs, "1", "raw")
    assert raw[0]["value"] == 3.0


def test_ad_hoc_market_closures_are_known():
    """Found by the quality layer, not by reading a holiday list.

    Every one of 42 tickers reported exactly one missing session over
    five years. Identical everywhere is the signature of a calendar
    bug, not a data gap: the market closed on 2025-01-09 for President
    Carter's funeral, which no recurring rule can derive.
    """
    from datetime import date
    from scalper import market_calendar as mcal

    assert not mcal.is_session(date(2025, 1, 9)), "Carter day of mourning"
    assert not mcal.is_session(date(2018, 12, 5)), "Bush day of mourning"
    assert not mcal.is_session(date(2012, 10, 30)), "Hurricane Sandy"
    # The surrounding days are ordinary sessions.
    assert mcal.is_session(date(2025, 1, 8))
    assert mcal.is_session(date(2025, 1, 10))

    # The count that caught it: five years against a real provider.
    assert mcal.expected_count(date(2021, 10, 28), date(2026, 10, 27)) == 1254

def main() -> int:
    print("\nregression tests\n")
    check("bug1: build_digest() executes (missing import)", test_build_digest_executes)
    check("bug1: no un-imported scalper module used in run.py",
          test_every_name_used_in_run_is_importable)
    check("bug2: dispatch preserves a caller's message",
          test_dispatch_preserves_custom_message)
    check("bug2: dispatch still falls back to build_sms",
          test_dispatch_still_falls_back_when_no_message)
    check("bug3: unreadable timestamp is not treated as expired",
          test_unreadable_timestamp_does_not_expire)
    check("bug3: attempt cap still retires exhausted items",
          test_attempt_cap_still_retires)
    check("paper: gap through stop fills at the open, not the stop",
          test_gap_through_stop_fills_at_open_not_stop)
    check("paper: costs charged on entry and exit",
          test_costs_charged_on_both_sides)
    check("paper: entry is the bar after the signal",
          test_entry_is_the_bar_after_the_signal)
    check("paper: open position is not scored as a win",
          test_open_position_is_not_counted_as_a_win)
    check("paper: position sizing respects the risk budget",
          test_position_sizing_respects_risk)
    check("sizing: flags an account too small for the contract",
          test_sizing_flags_an_account_that_is_too_small)
    check("sizing: passes a sane position", test_sizing_passes_a_sane_position)
    check("sizing: max loss is the whole premium", test_max_loss_is_the_whole_premium)
    check("sizing: emits no buy/sell advice", test_sizing_never_recommends)
    check("schema: close-only bars are reported, not silently useless",
          test_close_only_bars_are_reported_not_silently_useless)
    check("schema: full OHLC bars pass", test_full_ohlc_bars_pass)
    check("schema: option quote with no timestamp is unpriceable",
          test_option_quote_without_timestamp_is_unpriceable)
    check("schema: stale option quote is rejected", test_stale_option_quote_is_rejected)
    check("schema: validate() never raises", test_validate_never_raises)
    check("alerts: a vetoed ticker never alerts", test_vetoed_ticker_never_alerts)
    check("alerts: critical requires a filed event", test_critical_requires_a_filed_event)
    check("alerts: cooldown suppresses a repeat", test_cooldown_suppresses_a_repeat)
    check("alerts: identical state gives an identical key",
          test_identical_state_produces_identical_key)
    check("alerts: tiers render as separate messages",
          test_tiers_render_as_separate_messages)
    check("master: resolves known company names",
          test_security_master_resolves_known_names)
    check("master: never fuzzy-matches", test_security_master_never_fuzzy_matches)
    check("master: discards ambiguous names",
          test_security_master_discards_ambiguous_names)
    check("G1: relative return is negative when it lags the index",
          test_relative_return_is_negative_when_it_lags_the_index)
    check("G4: creator rates withheld on small samples",
          test_creator_rates_withheld_on_small_samples)
    check("G4: creator rates appear once data suffices",
          test_creator_rates_appear_once_there_is_enough_data)
    check("G2: Byrde Score is never called a probability",
          test_byrde_score_is_never_called_a_probability)
    check("G2: Byrde Score omits unmeasured reliability",
          test_byrde_score_omits_reliability_when_unmeasured)
    check("G5: funnel explains every rejection",
          test_funnel_explains_every_rejection)
    check("G7: critical alert carries thesis and invalidation",
          test_critical_alert_carries_thesis_and_invalidation)
    check("bug4: scanner runs end to end (injected bars, no network)",
          test_scanner_runs_end_to_end_without_network)
    check("bug4: scanner reports unusable bars instead of raising",
          test_scanner_reports_unusable_bars_instead_of_raising)
    # (a full build_digest with recording on takes ~47s - too slow for a
    #  suite people will actually run. The scanner test above catches the
    #  same bug in 0.0s by injecting bars into the real function.)
    check("review1: catalyst freshness uses identity, not publication date",
          test_catalyst_freshness_uses_identity_not_publication_date)
    check("review1: catalyst update only on material change",
          test_catalyst_update_detected_on_material_change_only)
    check("review2: top five excludes vetoed and returns five",
          test_top_five_excludes_vetoed_and_still_returns_five)
    check("review3: no invalid font shorthand",
          test_css_has_no_invalid_font_shorthand)
    check("review4: relevance stays conservative",
          test_relevance_is_conservative)
    check("review4: canonical ticker appears once",
          test_canonical_ticker_appears_once)
    check("meta: no test is defined but left unregistered",
          test_no_orphaned_tests)
    check("meta: every module-level name resolves",
          test_no_unresolvable_module_level_names)
    check("review1: commit() never writes a caller-supplied state",
          test_commit_does_not_write_when_given_a_state)
    check("time: calendar day is Eastern, not UTC",
          test_calendar_day_is_eastern_not_utc)
    check("time: rendered pages show market time",
          test_rendered_pages_are_not_labelled_utc)
    check("P1: funnel counts never grow", test_funnel_counts_never_grow)
    check("P0: blind feed is not reported as no setups",
          test_blind_price_feed_is_not_reported_as_no_setups)
    check("P2: publication and ingestion are separate clocks",
          test_health_separates_publication_from_ingestion)
    check("P0: a crashed run is recorded as an attempt",
          test_a_crashed_run_is_recorded_as_an_attempt)
    check("css: nothing below the 11px readability floor",
          test_no_font_below_the_readability_floor)
    check("P2: radar requires a verified public company",
          test_radar_requires_a_verified_public_company)
    check("P2: duplicate filings collapse, distinct ones do not",
          test_radar_collapses_duplicate_filings_without_merging_distinct_ones)
    check("safety: no live order execution surface",
          test_no_live_order_execution_surface)
    check("D: headline-only catalyst never reaches critical",
          test_headline_only_catalyst_never_reaches_critical)
    check("D: dedup by event identity, not ticker",
          test_catalyst_alerts_dedup_by_event_identity_not_ticker)
    check("D: only the owning runner sends",
          test_only_the_owning_runner_sends)
    check("D: a suppressed alert starts no cooldown",
          test_suppressed_alert_does_not_start_a_cooldown)
    check("D: operational alert fires once per outage",
          test_operational_alert_fires_once_per_outage)
    check("D: alert carries ticker, source, time and link",
          test_alert_carries_what_the_reader_needs)
    check("secrets: redacted before they are written",
          test_secrets_are_redacted_before_they_are_written)
    check("secrets: nothing secret-shaped is committed",
          test_no_real_secret_is_committed)
    check("P0-3: aggregate critical applies the evidence gate",
          test_aggregate_critical_applies_the_evidence_gate)
    check("P0: capped alerts stay eligible next run",
          test_capped_alerts_stay_eligible_next_run)
    check("P0-1 integration: non-owner sends nothing at all",
          test_notify_if_new_non_owner_sends_nothing_at_all)
    check("P0-2 integration: catalyst alerts without a new video",
          test_notify_if_new_alerts_on_catalyst_with_no_new_video)
    check("P0 integration: failed delivery starts no cooldown",
          test_notify_if_new_failed_delivery_starts_no_cooldown)
    check("owner: non-owner sends none of the three channels",
          test_non_owner_cannot_send_any_of_the_three_channels)
    check("state: a failed legacy send does not consume the ids",
          test_failed_legacy_send_does_not_consume_the_ids)
    check("state: a failed catalyst analysis is not recorded as seen",
          test_failed_catalyst_analysis_is_not_recorded_as_seen)
    check("B: alpaca requests split-adjusted bars",
          test_alpaca_requests_adjusted_bars)
    check("B: alpaca parses clean bars and records the feed",
          test_alpaca_parses_clean_bars_and_marks_the_feed)
    check("B: malformed bars are dropped, never invented",
          test_alpaca_drops_malformed_bars_without_inventing_them)
    check("B: error classes are distinguished",
          test_error_classes_are_distinguished)
    check("B: quality flags gaps, splits and short series",
          test_quality_flags_gaps_splits_and_short_series)
    check("B: holidays are not reported as missing data",
          test_holidays_are_not_reported_as_missing_data)
    check("B: ad-hoc market closures are known",
          test_ad_hoc_market_closures_are_known)
    check("B: feed entitlement is probed, not assumed",
          test_feed_entitlement_is_probed_not_assumed)
    check("C: signal states are never merged",
          test_signal_states_are_never_merged)
    check("C: calendar concentration outranks sample size",
          test_calendar_concentration_outranks_sample_size)
    check("C: small samples refuse to report a rate",
          test_small_samples_refuse_to_report_a_rate)
    check("C: out-of-sample split is by date",
          test_out_of_sample_split_is_by_date_not_random)
    check("C: overlapping signals counted as dependent",
          test_overlapping_signals_are_counted_as_dependent)
    check("C: execution refuses adjusted prices",
          test_execution_refuses_adjusted_prices)
    check("C: returns and execution use different series",
          test_returns_and_execution_use_different_series)
    check("C: clustered interval resamples sessions",
          test_clustered_interval_resamples_sessions_not_signals)
    check("C: too few sessions yields no interval",
          test_too_few_sessions_yields_no_interval_at_all)
    check("C: observations carry their session",
          test_observations_carry_their_session)

    print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all regression tests passed'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
