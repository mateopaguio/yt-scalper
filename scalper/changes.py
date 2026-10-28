"""What is genuinely new since the last completed run.

The first version decided freshness from a relative-time LABEL - if
_ts() rendered "2h ago" the catalyst counted as new. That is wrong in
both directions: an event published two hours ago was reported as new on
every run until it aged past 48h, and an event we had never seen was
called old purely because its publication timestamp was stale.

Freshness is a fact about OUR RECORD, not about a publication date. It
is answered by persistent identity:

  NEW        this event_id has never been recorded
  UPDATED    recorded before, but its material content changed
  UNCHANGED  recorded before, content identical

"Material content" deliberately excludes volatile fields. A re-fetch
that changes only a timestamp or a summary rewording is not an update;
materiality, direction, event type and the companies named are.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .config import DATA_DIR

PATH = DATA_DIR / "catalyst_state.json"

# Fields whose change means the event itself changed.
MATERIAL = ("event_type", "materiality", "direction", "is_market_relevant")


def _load() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"events": {}}


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    import os
    os.replace(tmp, PATH)


def fingerprint(event: dict) -> str:
    f = event.get("findings") or {}
    parts = [str(f.get(k, "")) for k in MATERIAL]
    parts.append("|".join(sorted(
        (c.get("ticker") or c.get("company") or "")
        for c in (f.get("companies") or []))))
    return hashlib.sha256("~".join(parts).encode()).hexdigest()[:16]


def classify(events: list[dict], state: dict | None = None,
             now: str | None = None) -> dict[str, Any]:
    """Split events into new / updated / unchanged against stored state.

    Pure: does not write. Call commit() once the run has succeeded, so a
    crashed run does not mark events as seen.
    """
    state = state if state is not None else _load()
    seen = state.get("events") or {}
    now = now or datetime.now(timezone.utc).isoformat()

    new, updated, unchanged = [], [], []
    for ev in events:
        eid = ev.get("event_id")
        if not eid:
            continue
        fp = fingerprint(ev)
        prev = seen.get(eid)
        if prev is None:
            new.append(ev)
        elif prev.get("fingerprint") != fp:
            updated.append({**ev, "_previous": prev.get("fingerprint")})
        else:
            unchanged.append(ev)

    return {
        "new": new, "updated": updated, "unchanged": unchanged,
        "counts": {"new": len(new), "updated": len(updated),
                   "unchanged": len(unchanged)},
        "known_events": len(seen),
        "note": ("Freshness is measured against our own record, not against "
                 "a publication date. An event is new because we have never "
                 "stored it, not because it was published recently."),
    }


def commit(events: list[dict], state: dict | None = None,
           now: str | None = None, persist: bool | None = None) -> dict:
    """Record these events as seen. Call AFTER a run completes.

    Writes to disk only when it owns the state - that is, when the
    caller passed none and we loaded it ourselves. A caller supplying
    its own dict gets the updated dict back and the file is untouched.

    This used to save unconditionally, so every test that passed a
    hand-built state wrote that state over the live record. A test
    event with the id "a" reached production that way, and a test
    whose state held one event would have replaced all seventy.
    Pass persist=True to override.
    """
    owned = state is None
    state = state if state is not None else _load()
    seen = state.setdefault("events", {})
    now = now or datetime.now(timezone.utc).isoformat()
    for ev in events:
        eid = ev.get("event_id")
        if not eid:
            continue
        fp = fingerprint(ev)
        rec = seen.get(eid)
        if rec is None:
            seen[eid] = {"fingerprint": fp, "first_seen": now, "last_seen": now,
                         "revisions": 0}
        else:
            if rec.get("fingerprint") != fp:
                rec["revisions"] = int(rec.get("revisions", 0)) + 1
                rec["fingerprint"] = fp
            rec["last_seen"] = now
    # bounded
    if len(seen) > 20000:
        for k in sorted(seen, key=lambda x: seen[x].get("last_seen", ""))[:len(seen) - 20000]:
            seen.pop(k, None)
    state["updated_at"] = now
    if persist if persist is not None else owned:
        _save(state)
    return state
