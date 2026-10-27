"""ONE record per ticker, with tags for every list it would have appeared in.

The page showed the same ticker under "corroborated", "under the radar",
"heating up" and "all tickers" - four cards, four scroll positions, one
company. Reading it meant recognising you had already read it.

A ticker now appears ONCE. The categories it qualifies for become TAGS
on that single row, so nothing is lost and nothing repeats.

Two scores are kept deliberately apart:
  INTELLIGENCE  how well-sourced the research is - independent work,
                evidence grade, freshness, catalyst.
  SCALP READY   whether a tradeable setup exists right now. Needs price
                data, so it is usually "no data" and says so rather
                than borrowing the intelligence score.
Collapsing them would let good research masquerade as a trade signal,
which is the distinction the whole project rests on.
"""
from __future__ import annotations

from typing import Any

# Tag, label, and the test for it.
TAGS = (
    ("corroborated", "Corroborated"),
    ("under_radar", "Under the radar"),
    ("catalyst", "Catalyst"),
    ("watchlist", "Watchlist"),
    ("echo", "Echo"),
    ("heating", "Heating up"),
    ("vetoed", "Rejected"),
    ("fresh", "New info"),
    ("stale", "After the move"),
)


def _scalp_readiness(entry: dict, setups_by_ticker: dict) -> dict[str, Any]:
    """Is there a tradeable setup, separate from how good the research is."""
    hits = setups_by_ticker.get(entry["ticker"]) or []
    if hits:
        h = hits[0]
        return {"state": "setup", "label": h.get("pattern", "").replace("_", " "),
                "invalidation": h.get("invalidation"),
                "detail": "paper only - no option priced"}
    return {"state": "no_data", "label": "no setup",
            "detail": "no price feed for this name; scalp readiness is "
                      "unknown, not zero"}


def build(aggregate: dict, ledger: dict | None = None,
          under_radar: list[dict] | None = None,
          signals: dict | None = None,
          setups: dict | None = None,
          watchlist: list[str] | None = None) -> list[dict]:
    """One row per ticker, carrying every category it belongs to."""
    radar = {e["ticker"] for e in (under_radar or [])}
    echo = {e["ticker"] for e in (aggregate.get("echo_chamber") or [])}
    hot = {d["ticker"] for d in ((signals or {}).get("momentum") or [])}
    watch = set(watchlist or [])
    setups_by = {}
    for h in ((setups or {}).get("hits") or []):
        setups_by.setdefault(h.get("ticker"), []).append(h)

    rows = []
    for e in aggregate.get("tickers", []):
        tk = e["ticker"]
        basis = e.get("basis") or {}
        sc = e.get("score") or {}
        fresh = (e.get("freshness") or {}).get("state", "")
        indep = basis.get("independent_channels") or []
        events = e.get("catalyst_events") or []

        tags = []
        if len(indep) > 1 and tk not in echo:
            tags.append("corroborated")
        if tk in radar:
            tags.append("under_radar")
        if events:
            tags.append("catalyst")
        if tk in watch:
            tags.append("watchlist")
        if tk in echo:
            tags.append("echo")
        if tk in hot:
            tags.append("heating")
        if sc.get("vetoed"):
            tags.append("vetoed")
        if fresh in ("forward_catalyst", "developing"):
            tags.append("fresh")
        if fresh == "late_to_story":
            tags.append("stale")

        own = [i for i in (e.get("items") or [])
               if i.get("basis") in ("primary", "original")]
        bulls = sum(1 for i in own if (i.get("stance") or "").lower() == "bullish")
        bears = sum(1 for i in own if (i.get("stance") or "").lower() == "bearish")
        sentiment = ("bullish" if bulls > bears else
                     "bearish" if bears > bulls else "mixed" if own else "unclear")

        rows.append({
            "ticker": tk,
            "company": e.get("company", ""),
            "tags": tags,
            "sentiment": sentiment,
            "bulls": bulls,
            "bears": bears,
            "independent": len(indep),
            "independent_channels": indep,
            "reprints": len(basis.get("reprint_channels") or []),
            "mentions": len(e.get("items") or []),
            "intelligence": e.get("byrde", {}).get("score"),
            "intelligence_raw": sc.get("total"),
            "scalp": _scalp_readiness(e, setups_by),
            "freshness": fresh,
            "catalyst_count": len(events),
            "catalyst_title": (events[0].get("title", "")[:90] if events else ""),
            "verification": (e.get("verification") or {}).get("status", ""),
            "vetoes": sc.get("vetoes") or [],
            "entry": e,          # kept for the expanded view
        })

    # Default order: best-sourced first, rejected last.
    rows.sort(key=lambda r: (
        "vetoed" in r["tags"],
        -(r["intelligence"] if r["intelligence"] is not None else -1),
    ))
    return rows


def summarise(rows: list[dict]) -> dict[str, Any]:
    counts = {t: sum(1 for r in rows if t in r["tags"]) for t, _ in TAGS}
    return {
        "total": len(rows),
        "by_tag": counts,
        "with_setup": sum(1 for r in rows if r["scalp"]["state"] == "setup"),
        "note": ("Each ticker appears once. Tags show every category it "
                 "qualifies for. Intelligence and Scalp Readiness are "
                 "separate: good research is not a trade signal."),
    }
