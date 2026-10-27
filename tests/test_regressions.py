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


def main() -> int:
    print("\nregression tests\n")
    check("bug1: build_digest() executes (missing import)", test_build_digest_executes)
    check("bug1: no un-imported scalper module used in run.py",
          test_every_name_used_in_run_is_importable)
    check("bug2: dispatch preserves a caller's message",
          test_dispatch_preserves_custom_message)
    check("bug2: dispatch still falls back to build_sms",
          test_dispatch_still_falls_back_when_no_message)
    print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all regression tests passed'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
