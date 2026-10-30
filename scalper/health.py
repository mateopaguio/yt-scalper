"""Is this thing actually running, and is what it shows current?

The page looked healthy for four days while the scheduler crashed on
every firing. Nothing lied: the digest rendered, the tickers were
there, the timestamp updated whenever anyone rebuilt by hand. The page
simply had no way to say "nothing new has come in since Tuesday".

Three clocks, never merged, because they answer different questions:

  published    when the source put it out
  ingested     when it entered this system
  market data  what the price feed is as of

A heartbeat is written at the very start of every invocation, before
anything can fail. That turns "did the scheduler fire?" from an
inference about log mtimes into a recorded fact - the attempt is on
disk even when the run dies two lines later.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from . import clock
from . import redact
from .config import DATA_DIR

PATH = DATA_DIR / "health.json"
KEEP = 200
STALE_HOURS = 6          # default; override with alerts.expected_interval_hours
OPS_FAILURE_THRESHOLD = 3


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:                            # noqa: BLE001
        print(f"  ! could not write health record: {exc}")


def heartbeat(trigger: str = "unknown") -> str:
    """Record that a run STARTED. Call first, before anything else."""
    data = _load()
    runs = data.setdefault("runs", [])
    rid = f"{datetime.now(timezone.utc).isoformat()}|{os.getpid()}"
    runs.append({
        "id": rid,
        "started": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "finished": None,
        "ok": None,
        "error": None,
        "ingested": None,
    })
    data["runs"] = runs[-KEEP:]
    _save(data)
    return rid


def finish(rid: str, ok: bool, error: str = "", ingested: int | None = None) -> None:
    """Record how the run ended. A run with no finish line crashed."""
    data = _load()
    for r in reversed(data.get("runs") or []):
        if r.get("id") == rid:
            r["finished"] = datetime.now(timezone.utc).isoformat()
            r["ok"] = bool(ok)
            r["error"] = redact.scrub(error or "")[:300]
            r["ingested"] = ingested
            break
    _save(data)


def _newest(values) -> str | None:
    vals = sorted(v for v in values if v)
    return vals[-1] if vals else None


def consecutive_failures() -> int:
    """Finished-and-failed, or started-and-never-finished, most recent first."""
    runs = (_load().get("runs") or [])[::-1]
    n = 0
    for r in runs:
        if r.get("ok"):
            break
        if r.get("finished") is None and n == 0:
            continue                 # the run calling this is still in flight
        n += 1
    return n


def _alerting_state() -> dict[str, Any]:
    """Which runner may push, so a silent phone has a visible reason."""
    try:
        from . import alert_state
        allowed, why = alert_state.may_send()
        return {"runner": alert_state.runner_id(),
                "owner": alert_state.notify_owner(),
                "may_send": allowed, "why": why,
                "recent": alert_state.history(6)}
    except Exception as exc:                            # noqa: BLE001
        return {"runner": "?", "owner": "?", "may_send": None,
                "why": f"unavailable: {exc}", "recent": []}


def snapshot(digest: dict | None = None, videos: list[dict] | None = None,
             expected_hours: float | None = None) -> dict[str, Any]:
    """Everything the Data Health panel needs, as plain facts."""
    data = _load()
    runs = data.get("runs") or []
    now = datetime.now(timezone.utc)

    attempted = runs[-1] if runs else None
    succeeded = next((r for r in reversed(runs) if r.get("ok")), None)
    # A run that started and never finished did not stop politely.
    crashed = [r for r in runs if r.get("finished") is None and r is not attempted]

    def age_h(iso):
        try:
            return (now - datetime.fromisoformat(iso)).total_seconds() / 3600
        except Exception:                               # noqa: BLE001
            return None

    videos = videos if videos is not None else ((digest or {}).get("videos") or [])
    pub = _newest(v.get("published") for v in videos)
    ing = _newest(v.get("ingested_at") for v in videos)

    price = (digest or {}).get("price_health") or {}
    setups = (digest or {}).get("setups") or {}
    feed_errors = setups.get("errors") or []
    scanned = setups.get("scanned") or []

    # The distinction that matters on the Scalp Lab screen: a scanner
    # that found nothing is not a scanner that could not look.
    usable = [d for d in (setups.get("diagnostics") or []) if d.get("usable")]
    market_blind = bool(scanned) and not usable

    expected = float(expected_hours or STALE_HOURS)
    ok_age = age_h(succeeded["started"]) if succeeded else None
    stale = ok_age is None or ok_age > expected
    overdue_by = (ok_age - expected) if (ok_age and ok_age > expected) else 0.0

    return {
        "last_attempt": {
            "at": attempted.get("started") if attempted else None,
            "display": clock.stamp(attempted.get("started")) if attempted else "never",
            "trigger": attempted.get("trigger") if attempted else None,
            "ok": attempted.get("ok") if attempted else None,
            "error": (attempted.get("error") or "") if attempted else "",
            "age_hours": age_h(attempted["started"]) if attempted else None,
        },
        "last_success": {
            "at": succeeded.get("started") if succeeded else None,
            "display": clock.stamp(succeeded.get("started")) if succeeded else "never",
            "trigger": succeeded.get("trigger") if succeeded else None,
            "age_hours": ok_age,
            "ingested": succeeded.get("ingested") if succeeded else None,
        },
        "stale": stale,
        "expected_hours": expected,
        "overdue_hours": round(overdue_by, 1),
        "stale_message": (
            f"No successful ingestion in {ok_age:.1f}h - "
            f"{overdue_by:.1f}h past the expected {expected:.0f}h schedule"
            if stale and ok_age is not None else
            ("No successful ingestion on record" if stale else "")),
        "consecutive_failures": consecutive_failures(),
        "crashed_runs": len(crashed),
        "sources": {
            "count": len(videos),
            "channels": len({v.get("channel") for v in videos if v.get("channel")}),
            "newest_published": pub,
            "newest_published_display": clock.stamp(pub) if pub else "unknown",
            "newest_ingested": ing,
            "newest_ingested_display": (
                clock.stamp(ing) if ing
                else "not recorded before this release"),
        },
        "market_data": {
            # price_health["last_run"] is when WE last tried, which is not
            # when the data is from. Showing the attempt time beside the
            # label "price feed as of" claimed the quotes were current at
            # the very moment the feed was down.
            "attempted": price.get("last_run"),
            "as_of": None if market_blind else price.get("last_run"),
            "as_of_display": (
                "unavailable - feed down" if market_blind
                else (clock.stamp(price.get("last_run"))
                      if price.get("last_run") else "unknown")),
            "attempted_display": clock.stamp(price.get("last_run"))
                                 if price.get("last_run") else "unknown",
            "feed_healthy": bool(price.get("price_feed_healthy")),
            "scanned": len(scanned),
            "usable": len(usable),
            "failed": [e.get("ticker") for e in feed_errors],
            "reasons": sorted({(e.get("reason") or "").strip()
                               for e in feed_errors if e.get("reason")}),
            "blind": market_blind,
            "warning": price.get("warning") or "",
        },
        "recent": [
            {"started": r.get("started"),
             "display": clock.stamp(r.get("started"), "%a %-I:%M %p"),
             "trigger": r.get("trigger"), "ok": r.get("ok"),
             "error": (r.get("error") or "")[:120],
             "ingested": r.get("ingested")}
            for r in runs[-8:][::-1]
        ],
        "alerting": _alerting_state(),
        "note": ("published is when the source put it out; ingested is when "
                 "this system saw it; market data is what the price feed is "
                 "as of. They are three different clocks."),
    }
