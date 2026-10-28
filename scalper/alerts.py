"""Three notification tiers, so the phone stays worth listening to.

A notifier that fires on everything gets muted, and a muted notifier is
the same as no notifier. Tiering exists to keep the loud channel rare.

  CRITICAL  wake the phone. Something new, independently sourced, and
            corroborated by a filed event. Rare by construction.
  WATCHLIST normal push. A high-ranked idea gained independent support
            or crossed a threshold.
  DIGEST    silent. The scheduled summary; no sound, no interruption.

Every alert carries a dedup KEY and a COOLDOWN. The same fact must not
fire twice because a later run recomputed it - that is how an alerting
system trains its owner to ignore it. Keys are content-based, not
timestamp-based, so a re-run of identical state produces an identical
key and is suppressed.

Thresholds live in config so they can be tuned without editing code.

Nothing here decides whether to trade. An alert says "this changed and
you said you wanted to know", never "act on this".
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

CRITICAL = "critical"
WATCHLIST = "watchlist"
DIGEST = "digest"

TIERS = (CRITICAL, WATCHLIST, DIGEST)

# ntfy priorities. 5 breaks through a silent phone; 1 arrives quietly.
NTFY_PRIORITY = {CRITICAL: 5, WATCHLIST: 3, DIGEST: 1}
NTFY_TAGS = {CRITICAL: "rotating_light", WATCHLIST: "eyes", DIGEST: "newspaper"}

DEFAULTS = {
    "critical_min_independent": 2,      # channels that did their OWN work
    "critical_min_materiality": 4,      # filed event materiality 1-5
    "critical_min_score": 4.0,
    "watchlist_min_score": 3.0,
    "watchlist_min_independent": 2,
    "cooldown_hours": {CRITICAL: 12, WATCHLIST: 24, DIGEST: 6},
    "max_per_run": {CRITICAL: 3, WATCHLIST: 5, DIGEST: 1},

    # A catalyst may only wake the phone if somebody actually read the
    # announcement. Resolving "Pfizer" to PFE proves the company is
    # listed; it proves nothing about what the filing says. Every one
    # of the four FDA events promoted to the radar was headline-only.
    "critical_require_primary_evidence": True,
    "critical_require_market_relevant": True,
    "catalyst_min_materiality": 3,
    "catalyst_allowed_freshness": ["new", "updated"],
}


def _clip(text: str, n: int) -> str:
    """Trim on a word boundary; a hard slice reads as corrupted data."""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    sp = cut.rfind(" ")
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:.-") + "\u2026"


def _clock_stamp(iso: str) -> str:
    """Original publication time, in market time, never the run time."""
    try:
        from . import clock
        return clock.stamp(iso) or iso
    except Exception:                                   # noqa: BLE001
        return iso


def _cfg(config: dict | None, key: str):
    c = (config or {}).get("alerts") or {}
    return c.get(key, DEFAULTS[key])


def key_for(kind: str, ticker: str, detail: str) -> str:
    """Content-based, so identical state produces an identical key."""
    h = hashlib.sha256(f"{kind}|{ticker}|{detail}".encode()).hexdigest()[:10]
    return f"{kind}:{ticker}:{h}"


def _recent(sent: dict, key: str, hours: int, now: datetime) -> bool:
    ts = (sent or {}).get(key)
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - when < timedelta(hours=hours)


def _card(entry: dict, events: list[dict]) -> dict:
    """The facts a critical alert must carry.

    A push saying only "NVDA is up" is useless at the moment it arrives:
    the reader cannot tell what the claim is, what supports it, or what
    would make it wrong. Thesis, evidence, catalyst and invalidation
    travel WITH the alert so it can be judged without opening anything.
    """
    items = entry.get("items") or []
    own = [i for i in items if i.get("basis") in ("primary", "original")]
    rank = {"data": 3, "reasoned": 2, "speculation": 1}
    best = max(own, key=lambda i: rank.get(i.get("evidence"), 0), default=None)

    bulls = [i for i in own if (i.get("stance") or "").lower() == "bullish"]
    bears = [i for i in own if (i.get("stance") or "").lower() == "bearish"]

    inval = ""
    for i in own:
        if i.get("price_target"):
            inval = f"thesis rests on {i['price_target']}"
            break
    if not inval and bears:
        inval = f"counter-case: {(bears[0].get('thesis') or '')[:110]}"
    if not inval:
        inval = "no explicit invalidation stated on the source material"

    return {
        "thesis": (best.get("thesis") if best else "")[:220],
        "evidence_grade": (best.get("evidence") if best else "none"),
        "catalyst": (events[0].get("title", "")[:140] if events else
                     (best.get("catalyst") if best else "")),
        "sources": sorted({i.get("channel", "") for i in own if i.get("channel")}),
        "disagreement": (f"{len(bulls)} bull / {len(bears)} bear among "
                         f"independent sources" if bears and bulls else ""),
        "invalidation": inval,
        "freshness": (entry.get("freshness") or {}).get("state", ""),
    }


def classify(aggregate: dict, config: dict | None = None) -> list[dict]:
    """Build candidate alerts from the digest. Does not send anything."""
    out: list[dict] = []
    min_ind = _cfg(config, "critical_min_independent")
    min_mat = _cfg(config, "critical_min_materiality")
    min_crit = _cfg(config, "critical_min_score")
    min_watch = _cfg(config, "watchlist_min_score")
    min_watch_ind = _cfg(config, "watchlist_min_independent")

    for e in aggregate.get("tickers", []):
        tk = e.get("ticker") or ""
        sc = e.get("score") or {}
        basis = e.get("basis") or {}
        if sc.get("vetoed"):
            continue                      # a vetoed signal never alerts

        indep = len(basis.get("independent_channels") or [])
        total = sc.get("total") or 0.0
        events = e.get("catalyst_events") or []
        top_mat = max((ev.get("materiality") or 0 for ev in events), default=0)
        fresh = (e.get("freshness") or {}).get("state", "")

        # CRITICAL: independent work AND a filed event behind it.
        if (indep >= min_ind and total >= min_crit and top_mat >= min_mat):
            out.append({
                "tier": CRITICAL,
                "ticker": tk,
                "headline": f"{tk} — {indep} independent, filed event "
                            f"(materiality {top_mat}/5)",
                "detail": (events[0].get("title", "")[:140] if events else ""),
                "score": round(total, 2),
                "key": key_for("crit", tk, f"{indep}|{top_mat}"),
                "card": _card(e, events),
            })
            continue

        # WATCHLIST: real independent support, no filed event yet.
        if indep >= min_watch_ind and total >= min_watch:
            bits = [f"{indep} channels did own work"]
            if fresh in ("forward_catalyst", "developing"):
                bits.append(fresh.replace("_", " "))
            out.append({
                "tier": WATCHLIST,
                "ticker": tk,
                "headline": f"{tk} — " + ", ".join(bits),
                "detail": "",
                "score": round(total, 2),
                "key": key_for("watch", tk, str(indep)),
            })

    out.sort(key=lambda a: (TIERS.index(a["tier"]), -a["score"]))
    return out


def merge(*groups: list[dict]) -> list[dict]:
    """Combine alert sources, filed events first within each tier.

    A filed company event is a harder fact than "four channels talked
    about it", so when the per-run cap bites, the chatter is what gets
    dropped.
    """
    out = [a for g in groups for a in g]
    out.sort(key=lambda a: (TIERS.index(a["tier"]),
                            0 if a.get("is_catalyst") else 1,
                            -(a.get("score") or 0)))
    return out


def select(candidates: list[dict], already_sent: dict | None = None,
           config: dict | None = None, now: datetime | None = None) -> dict:
    """Apply cooldowns and per-run caps. Returns what to send and why not."""
    now = now or datetime.now(timezone.utc)
    sent = already_sent or {}
    cooldowns = _cfg(config, "cooldown_hours")
    caps = _cfg(config, "max_per_run")

    chosen: list[dict] = []
    suppressed: list[dict] = []
    per_tier: dict[str, int] = {t: 0 for t in TIERS}

    for a in candidates:
        tier = a["tier"]
        hours = cooldowns.get(tier, 12)
        if _recent(sent, a["key"], hours, now):
            suppressed.append({**a, "reason": f"cooldown ({hours}h)"})
            continue
        if per_tier[tier] >= caps.get(tier, 3):
            suppressed.append({**a, "reason": f"per-run cap ({caps.get(tier)})"})
            continue
        per_tier[tier] += 1
        chosen.append(a)

    return {"send": chosen, "suppressed": suppressed, "counts": per_tier}


def render(alerts: list[dict], site_url: str = "") -> dict[str, Any]:
    """One message per tier, so a critical alert is never buried in a digest."""
    out = {}
    for tier in TIERS:
        rows = [a for a in alerts if a["tier"] == tier]
        if not rows:
            continue
        lines = []
        for a in rows:
            lines.append(a["headline"])
            c = a.get("card")
            if tier == CRITICAL and c:
                # A critical alert must be judgeable without opening a page.
                if c.get("thesis"):
                    lines.append(f"  why: {c['thesis']}")
                if c.get("catalyst"):
                    lines.append(f"  catalyst: {c['catalyst']}")
                lines.append(f"  evidence: {c.get('evidence_grade', 'none')}"
                             + (f" | {c['freshness'].replace('_', ' ')}"
                                if c.get("freshness") else ""))
                if c.get("sources"):
                    lines.append(f"  sources: {', '.join(c['sources'][:4])}")
                if c.get("disagreement"):
                    lines.append(f"  disagreement: {c['disagreement']}")
                lines.append(f"  invalidation: {c['invalidation']}")
            elif a.get("is_catalyst"):
                # Everything needed to judge it without opening anything:
                # what changed, why it is at this tier, who filed it,
                # when they filed it, and where to read more.
                if a.get("detail"):
                    lines.append(f"  {a['detail']}")
                if a.get("reason"):
                    lines.append(f"  why: {a['reason']}")
                if a.get("source"):
                    lines.append(f"  source: {a['source']}")
                if a.get("published"):
                    lines.append(f"  published: {_clock_stamp(a['published'])}")
                if a.get("state"):
                    lines.append(f"  status: {a['state']} information")
                lines.append("  paper research only - not a scalp entry")
            elif a.get("detail"):
                lines.append(f"  {a['detail']}")
        body = "\n".join(lines)
        if site_url:
            if tier == CRITICAL and len(rows) == 1:
                body += f"\n{site_url.rstrip('/')}/ticker/{rows[0]['ticker']}.html"
            else:
                body += f"\n{site_url}"
        out[tier] = {
            "title": {CRITICAL: "Confirmed setup",
                      WATCHLIST: "Worth a look",
                      DIGEST: "Digest"}[tier],
            "body": body,
            "priority": NTFY_PRIORITY[tier],
            "tags": NTFY_TAGS[tier],
            "keys": [a["key"] for a in rows],
            "count": len(rows),
        }
    return out


def catalyst_alerts(radar_result: dict, config: dict | None = None) -> list[dict]:
    """Alerts from filed events, keyed by EVENT identity, not by ticker.

    Keying on the ticker was wrong twice over: one event naming two
    companies fired two alerts, and two unrelated events about one
    company collapsed into one. The key is the event id plus a hash of
    its material content, so a materially changed event alerts again
    and an unchanged one never does.

    The evidence gate is the point of this function. An event whose
    announcement body was never retrieved cannot reach CRITICAL,
    whatever its materiality says, because nothing has been read.
    """
    from .changes import fingerprint

    require_primary = _cfg(config, "critical_require_primary_evidence")
    require_rel = _cfg(config, "critical_require_market_relevant")
    min_mat = _cfg(config, "catalyst_min_materiality")
    allowed_fresh = _cfg(config, "catalyst_allowed_freshness")

    out = []
    promoted = (radar_result.get("upcoming") or []) + \
               (radar_result.get("priority") or [])

    for ev in promoted:
        rd = ev.get("_radar") or {}
        findings = ev.get("findings") or {}
        verified = rd.get("verified") or []
        evidence = rd.get("evidence") or {}
        if not verified:
            continue

        mat = rd.get("materiality") or 0
        state = ev.get("_state", "seen")
        symbols = [v["ticker"] for v in verified]

        blocked = []
        if require_primary and not evidence.get("primary"):
            blocked.append(evidence.get("grade") or "no primary source")
        if require_rel and not findings.get("is_market_relevant"):
            blocked.append("not market relevant")
        if mat < min_mat:
            blocked.append(f"materiality {mat}/5 below {min_mat}")
        if state not in allowed_fresh:
            blocked.append(f"{state} information")

        tier = WATCHLIST if blocked else CRITICAL
        reason = ("; ".join(blocked) if blocked
                  else f"materiality {mat}/5, body read, {state}")

        out.append({
            "tier": tier,
            "ticker": ", ".join(symbols),
            "tickers": symbols,
            "headline": f"{', '.join(symbols)} \u2014 "
                        f"{(findings.get('event_type') or 'event').replace('_', ' ')}"
                        f" (materiality {mat}/5)",
            "detail": _clip(findings.get("summary") or ev.get("title", ""), 200),
            "score": float(mat),
            "state": state,
            "reason": reason,
            "blocked": blocked,
            "evidence": evidence,
            "source": ev.get("source") or "",
            "published": ev.get("published") or "",
            "url": ev.get("url") or "",
            # Event identity, not ticker. Two tickers in one event share
            # one alert; a material change produces a new key.
            "key": f"cat:{ev.get('event_id', '?')}:{fingerprint(ev)[:8]}",
            "is_catalyst": True,
        })

    out.sort(key=lambda a: (TIERS.index(a["tier"]), -a["score"]))
    return out
