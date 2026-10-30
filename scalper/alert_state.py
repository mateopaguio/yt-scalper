"""Persistent alert history, cooldowns, and which runner may send.

Two schedulers point at this repo: launchd on a MacBook and an hourly
GitHub Actions workflow. They run on different branches, and only the
cloud one commits `data/` back, so their dedup logs never meet. Both
would compute the same alert from the same event and both would push
it to the same phone.

No amount of content hashing fixes that, because the suppression
record lives on whichever machine did the sending. So notification is
OWNED. One runner sends; the other computes the same alerts, writes
them to its own history marked `suppressed_not_owner`, and stays
quiet. Ownership is one environment variable, and the Data Health
panel says which runner holds it.

History is a record, not a timestamp. `{key: when}` could answer "was
this sent?" but not "what was sent, by whom, and why was that one
suppressed?" - which is what you need at 7am when the phone buzzed and
you want to know whether to trust it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from . import redact
from .config import DATA_DIR

PATH = DATA_DIR / "alert_log.json"
KEEP_HISTORY = 500

# Who is allowed to send. Set BYRDE_NOTIFY_OWNER on each runner.
#   local           launchd on the Mac
#   github-actions  the hourly workflow
#   any             both (duplicates possible - development only)
DEFAULT_OWNER = "local"


def runner_id() -> str:
    """Which runner is this process?"""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "github-actions"
    return "local"


def notify_owner(config: dict | None = None) -> str:
    env = (os.environ.get("BYRDE_NOTIFY_OWNER") or "").strip()
    if env:
        return env
    cfg = (config or {}).get("alerts") or {}
    return cfg.get("notify_owner", DEFAULT_OWNER)


def may_send(config: dict | None = None) -> tuple[bool, str]:
    """Is this runner allowed to push to the phone?"""
    owner = notify_owner(config)
    me = runner_id()
    if owner in ("any", "all"):
        return True, "owner=any (duplicates possible)"
    if owner == me:
        return True, f"this runner owns notifications ({me})"
    return False, f"{owner} owns notifications, this is {me}"


def _load() -> dict:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {"sent": {}, "history": [], "ops": {}}
    # Older runs stored a flat {key: iso} map.
    if raw and all(isinstance(v, str) for v in raw.values()):
        return {"sent": {k: {"at": v} for k, v in raw.items()},
                "history": [], "ops": {}}
    raw.setdefault("sent", {})
    raw.setdefault("history", [])
    raw.setdefault("ops", {})
    return raw


def _save(data: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:                            # noqa: BLE001
        print(f"  ! could not write alert history: {exc}")


def sent_map() -> dict[str, str]:
    """{key: iso} for cooldown checks."""
    return {k: v.get("at", "") for k, v in (_load().get("sent") or {}).items()}


def record(alerts: list[dict], delivered: bool, reason: str = "") -> None:
    """Write what happened to every alert this run considered."""
    if not alerts:
        return
    data = _load()
    now = datetime.now(timezone.utc).isoformat()
    me = runner_id()
    for a in alerts:
        entry = {"at": now, "runner": me, "tier": a.get("tier"),
                 "ticker": a.get("ticker"), "delivered": bool(delivered),
                 "reason": redact.scrub(reason),
                 "headline": redact.scrub(a.get("headline") or "")[:160]}
        if delivered:
            # Only a delivered alert starts a cooldown. Suppressing a
            # send and then recording it as sent would silence the
            # alert everywhere for the whole cooldown window.
            data["sent"][a["key"]] = entry
        data["history"].append({**entry, "key": a["key"]})
    data["history"] = data["history"][-KEEP_HISTORY:]
    _save(data)


def history(limit: int = 20) -> list[dict]:
    return list(reversed((_load().get("history") or [])[-limit:]))


# --- operational alerting -------------------------------------------
#
# One alert when ingestion keeps failing, not one per failure. An
# outage that pushes hourly is an outage that gets muted.

def ops_should_alert(consecutive_failures: int, threshold: int,
                     cooldown_hours: int = 12) -> bool:
    if consecutive_failures < threshold:
        return False
    data = _load()
    last = (data.get("ops") or {}).get("last_failure_alert")
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when >= timedelta(hours=cooldown_hours)


def ops_mark_alerted() -> None:
    data = _load()
    data.setdefault("ops", {})["last_failure_alert"] = \
        datetime.now(timezone.utc).isoformat()
    _save(data)


def ops_mark_recovered() -> None:
    """Clear the latch so the next outage alerts again."""
    data = _load()
    if (data.get("ops") or {}).pop("last_failure_alert", None) is not None:
        _save(data)
