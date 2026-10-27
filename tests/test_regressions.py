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
         "catalyst_events": [{"materiality": 4, "title": "8-K filed"}],
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
        "catalyst_events": [{"materiality": 4, "title": "FDA approval"}],
        "freshness": {"state": "forward_catalyst"},
        "items": [{"basis": "original", "stance": "bullish", "evidence": "data",
                   "channel": "a", "thesis": "label expansion", "price_target": "$34"}],
    }]}
    msgs = alerts.render(alerts.classify(agg), "https://x.test")
    body = msgs[alerts.CRITICAL]["body"]
    for need in ("why:", "catalyst:", "evidence:", "invalidation:"):
        assert need in body, f"critical alert missing {need!r}"
    assert "/ticker/PFE.html" in body, "no deep link to the opportunity card"


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
    print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all regression tests passed'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
