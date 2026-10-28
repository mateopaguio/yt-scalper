"""What belongs on the Catalyst Radar, and what belongs in the archive.

The screen was 73 FDA notices out of 75 events, 55 of them materiality
1/5, two with a ticker. A recall of a dietary supplement sold by a
company nobody can buy shares in is not a catalyst; it is a headline
that happens to mention a product.

The gate is a VERIFIED public-company relationship. A company name the
model emitted is not one: "NeilMed Pharmaceuticals" and "Pfizer" look
alike in a findings dict, and only one of them is listed. A name earns
promotion when it resolves to a ticker through the security master -
an exact match on a mapping learned from the corpus, never fuzzy - or
when it already carries a ticker that survives validation.

Nothing is deleted. Everything unmapped, stale or immaterial is
archived with the reason it was archived, so the judgement can be
inspected and argued with instead of silently applied.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import clock, security_master

STALE_DAYS = 7
MIN_MATERIALITY = 2


def primary_evidence(event: dict) -> dict:
    """Did anyone actually read the announcement, or just its headline?

    Resolving "Pfizer" to PFE proves the company is listed. It proves
    nothing about what the filing says. All four events this gate was
    written for were headline-only: the FDA pages were never fetched,
    so "FDA posted a warning letter addressed to Amgen" is the whole
    of what is known - not the violation, not the scope, not whether
    it touches anything that moves the stock.

    Headline-only events stay on the radar, because they are real
    information. They are barred from waking a phone.
    """
    has_body = event.get("has_body")
    if has_body is None:
        return {"primary": False, "grade": "unknown",
                "why": "body retrieval was not recorded for this event"}
    if has_body:
        n = event.get("body_chars")
        return {"primary": True, "grade": "body read",
                "why": f"{n} characters of announcement text" if n
                       else "announcement text retrieved"}
    return {"primary": False, "grade": "headline only",
            "why": "the announcement body was never retrieved; only the "
                   "headline is known"}


def _dedup_key(event: dict) -> str:
    """Two events are the same story if they say the same thing."""
    title = re.sub(r"[^a-z0-9]+", " ",
                   (event.get("title") or "").lower()).strip()
    findings = event.get("findings") or {}
    names = sorted((c.get("company") or "").lower().strip()
                   for c in (findings.get("companies") or []))
    return title + "|" + ",".join(n for n in names if n)


def verify_companies(event: dict, master: dict | None = None) -> list[dict]:
    """Companies in this event that are verifiably public, with tickers."""
    findings = event.get("findings") or {}
    out, seen = [], set()
    for co in findings.get("companies") or []:
        name = (co.get("company") or "").strip()
        given = (co.get("ticker") or "").strip().upper()
        resolved = given or security_master.resolve(name, master)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        out.append({"ticker": resolved, "company": name,
                    "source": "stated" if given else "resolved"})
    return out


def _published(event: dict) -> datetime | None:
    return clock._parse(event.get("published") or "")


def classify(event: dict, now: datetime | None = None,
             master: dict | None = None) -> dict[str, Any]:
    """Where this event goes, and why."""
    now = now or datetime.now(timezone.utc)
    findings = event.get("findings") or {}
    verified = verify_companies(event, master)
    evidence = primary_evidence(event)

    try:
        materiality = int(findings.get("materiality") or 0)
    except (TypeError, ValueError):
        materiality = 0

    scheduled = (findings.get("scheduled_date") or "").strip()
    upcoming = False
    if scheduled:
        try:
            when = datetime.strptime(scheduled, "%Y-%m-%d").date()
            upcoming = when >= clock.today()
        except ValueError:
            upcoming = False

    pub = _published(event)
    age_days = (now - pub).total_seconds() / 86400 if pub else None

    # Order matters: a dated event ahead of us stays on the radar even
    # if the filing that announced it is old, because the thing itself
    # has not happened yet.
    if upcoming and verified:
        return {"bucket": "upcoming", "reason": "", "verified": verified, "evidence": evidence,
                "materiality": materiality, "age_days": age_days}
    if not verified:
        return {"bucket": "archive",
                "reason": "no verified public company behind it",
                "verified": [], "evidence": evidence,
                "materiality": materiality, "age_days": age_days}
    if not findings.get("is_market_relevant"):
        return {"bucket": "archive", "reason": "not market relevant",
                "verified": verified, "materiality": materiality,
                "age_days": age_days}
    if materiality < MIN_MATERIALITY:
        return {"bucket": "archive",
                "reason": f"materiality {materiality}/5",
                "verified": verified, "materiality": materiality,
                "age_days": age_days}
    if age_days is not None and age_days > STALE_DAYS and not upcoming:
        return {"bucket": "archive",
                "reason": f"filed {int(age_days)} days ago",
                "verified": verified, "materiality": materiality,
                "age_days": age_days}
    if upcoming:
        return {"bucket": "upcoming", "reason": "", "verified": verified, "evidence": evidence,
                "materiality": materiality, "age_days": age_days}
    return {"bucket": "priority", "reason": "", "verified": verified,
            "evidence": evidence, "materiality": materiality,
            "age_days": age_days}


def build(events: list[dict], now: datetime | None = None,
          changes: dict | None = None) -> dict[str, Any]:
    """Bucket every event, collapsing duplicates, newest first."""
    now = now or datetime.now(timezone.utc)
    master = security_master._load()

    # Same story filed twice is one row on the screen. The FDA reposts
    # identical notices across its feeds, which is how the radar came
    # to show NeilMed twice in a row.
    #
    # Keyed on the title, NOT on changes.fingerprint(): that hashes
    # event_type, materiality, direction and relevance, which is right
    # for asking "has this known event changed?" and badly wrong for
    # asking "are these two events the same?". Every low-materiality
    # unmapped recall shares those four values, so it collapsed 75
    # events into 24 and threw away 51 distinct stories.
    by_key: dict[str, dict] = {}
    duplicates = 0
    for ev in events:
        key = _dedup_key(ev)
        if key in by_key:
            by_key[key].setdefault("_duplicates", []).append(
                ev.get("source") or "")
            duplicates += 1
            continue
        by_key[key] = dict(ev)

    # new / updated / unchanged, if the caller measured it
    state = {}
    for key in ("new", "updated"):
        for ev in (changes or {}).get(key) or []:
            if ev.get("event_id"):
                state[ev["event_id"]] = key

    out: dict[str, list] = {"upcoming": [], "priority": [], "archive": []}
    for ev in by_key.values():
        verdict = classify(ev, now, master)
        ev["_radar"] = verdict
        # Preserve a state the caller already stamped. Defaulting to
        # "seen" whenever no changes result is supplied would silently
        # downgrade events that were classified upstream - and "seen"
        # is the value that blocks a CRITICAL.
        ev["_state"] = state.get(ev.get("event_id"),
                                 ev.get("_state", "seen"))
        out[verdict["bucket"]].append(ev)

    out["upcoming"].sort(key=lambda e: (e.get("findings") or {})
                         .get("scheduled_date") or "")
    for k in ("priority", "archive"):
        out[k].sort(key=lambda e: (-(e["_radar"]["materiality"] or 0),
                                   e.get("published") or ""), reverse=False)
        out[k].sort(key=lambda e: e.get("published") or "", reverse=True)

    promoted = out["upcoming"] + out["priority"]
    headline_only = [ev for ev in promoted
                     if not ev["_radar"]["evidence"]["primary"]]

    reasons: dict[str, int] = {}
    for ev in out["archive"]:
        r = ev["_radar"]["reason"]
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "upcoming": out["upcoming"],
        "priority": out["priority"],
        "archive": out["archive"],
        "counts": {k: len(v) for k, v in out.items()},
        "duplicates_collapsed": duplicates,
        "headline_only_promoted": len(headline_only),
        "primary_source_backed": len(promoted) - len(headline_only),
        "archive_reasons": reasons,
        "note": ("Promotion requires a company that resolves to a listed "
                 "ticker. Archived events are kept, with the reason they "
                 "were archived."),
    }
