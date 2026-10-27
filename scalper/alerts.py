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
}


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
            if a.get("detail"):
                lines.append(f"  {a['detail']}")
        body = "\n".join(lines)
        if site_url:
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
