"""What's heating up, not what you told it to watch.

Four dynamic signals, computed from the stored window rather than a
fixed watchlist:

  momentum   - mentions accelerating vs the preceding days
  emerging   - first seen recently, with real evidence behind it
  news_linked- discussed on video AND carrying a filed SEC/FDA/Fed event
  sectors    - where attention is concentrating

None of these is a recommendation. They rank where to look, using
evidence quality as a tiebreaker so volume alone can't win.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

EV_W = {"data": 2.0, "reasoned": 1.0, "speculation": -0.5, "none": -1.0}


def _when(video: dict):
    try:
        return datetime.fromisoformat(video["published"])
    except (KeyError, ValueError):
        return None


def _quality(counts: dict) -> float:
    return sum(EV_W.get(k, 0) * n for k, n in (counts or {}).items())


def momentum(videos: list[dict], recent_hours: int = 24,
             baseline_days: int = 7) -> list[dict]:
    """Tickers mentioned more in the last day than their recent norm."""
    now = datetime.now(timezone.utc)
    rcut = now - timedelta(hours=recent_hours)
    bcut = now - timedelta(days=baseline_days)

    recent: dict[str, int] = defaultdict(int)
    base: dict[str, int] = defaultdict(int)
    chans: dict[str, set] = defaultdict(set)
    counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))

    from .analyze import clean_ticker
    for v in videos:
        w = _when(v)
        if not w:
            continue
        for t in ((v.get("findings") or {}).get("tickers") or []):
            sym = clean_ticker(t.get("ticker", ""))
            if not sym:
                continue
            counts[sym][t.get("evidence", "none")] += 1
            if w >= rcut:
                recent[sym] += 1
                chans[sym].add(v.get("channel", ""))
            elif w >= bcut:
                base[sym] += 1

    out = []
    span = max(baseline_days - recent_hours / 24, 0.5)
    for sym, n in recent.items():
        prior_rate = base[sym] / span            # mentions/day before
        lift = n - prior_rate
        out.append({
            "ticker": sym,
            "recent_mentions": n,
            "prior_per_day": round(prior_rate, 2),
            "lift": round(lift, 2),
            "channels": sorted(c for c in chans[sym] if c),
            "evidence_counts": dict(counts[sym]),
            "quality": round(_quality(counts[sym]), 1),
            "is_new": base[sym] == 0,
        })
    out.sort(key=lambda d: (d["lift"], d["quality"], len(d["channels"])), reverse=True)
    return out


def emerging(videos: list[dict], first_seen_hours: int = 48,
             min_quality: float = 1.0) -> list[dict]:
    """Names that only just appeared and are actually supported."""
    from .analyze import clean_ticker
    first: dict[str, datetime] = {}
    counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    meta: dict[str, dict] = {}

    for v in videos:
        w = _when(v)
        if not w:
            continue
        for t in ((v.get("findings") or {}).get("tickers") or []):
            sym = clean_ticker(t.get("ticker", ""))
            if not sym:
                continue
            counts[sym][t.get("evidence", "none")] += 1
            if sym not in first or w < first[sym]:
                first[sym] = w
                meta[sym] = {
                    "company": t.get("company", ""),
                    "sector": (t.get("sector") or "").lower(),
                    "channel": v.get("channel", ""),
                    "thesis": t.get("thesis", ""),
                    "breakout_signal": t.get("breakout_signal", "none"),
                }

    cut = datetime.now(timezone.utc) - timedelta(hours=first_seen_hours)
    out = []
    for sym, w in first.items():
        if w < cut:
            continue
        q = _quality(counts[sym])
        if q < min_quality:
            continue
        out.append({"ticker": sym, "first_seen": w.isoformat(),
                    "quality": round(q, 1), "evidence_counts": dict(counts[sym]),
                    **meta[sym]})
    out.sort(key=lambda d: d["quality"], reverse=True)
    return out


def news_linked(aggregate: dict, events_by_ticker: dict) -> list[dict]:
    """Discussed on video AND carrying a filed regulatory/macro event.

    This is the tightest signal here: chatter corroborated by a primary
    source rather than by more chatter.
    """
    out = []
    for e in aggregate.get("tickers", []):
        evs = events_by_ticker.get(e["ticker"]) or []
        if not evs:
            continue
        out.append({
            "ticker": e["ticker"],
            "company": e.get("company", ""),
            "consensus": e.get("consensus", "split"),
            "channels": e.get("corroborating_channels", []),
            "evidence_counts": e.get("evidence_counts", {}),
            "events": evs,
            "max_materiality": max((x.get("materiality") or 0) for x in evs),
            "quality": round(_quality(e.get("evidence_counts")), 1),
        })
    out.sort(key=lambda d: (d["max_materiality"], d["quality"]), reverse=True)
    return out


def sectors(videos: list[dict]) -> list[dict]:
    """Where attention is concentrating, weighted by evidence quality."""
    from .analyze import clean_ticker
    agg: dict[str, dict] = defaultdict(
        lambda: {"mentions": 0, "tickers": set(), "channels": set(),
                 "counts": defaultdict(int), "bull": 0, "bear": 0})
    for v in videos:
        for t in ((v.get("findings") or {}).get("tickers") or []):
            sec = (t.get("sector") or "").strip().lower()
            if not sec:
                continue
            sym = clean_ticker(t.get("ticker", ""))
            a = agg[sec]
            a["mentions"] += 1
            if sym:
                a["tickers"].add(sym)
            a["channels"].add(v.get("channel", ""))
            a["counts"][t.get("evidence", "none")] += 1
            if t.get("stance") == "bullish":
                a["bull"] += 1
            elif t.get("stance") == "bearish":
                a["bear"] += 1

    out = []
    for sec, a in agg.items():
        out.append({
            "sector": sec,
            "mentions": a["mentions"],
            "tickers": sorted(a["tickers"]),
            "channels": len([c for c in a["channels"] if c]),
            "bull": a["bull"], "bear": a["bear"],
            "evidence_counts": dict(a["counts"]),
            "quality": round(_quality(a["counts"]), 1),
            "tilt": ("bullish" if a["bull"] > a["bear"]
                     else "bearish" if a["bear"] > a["bull"] else "split"),
        })
    out.sort(key=lambda d: (d["quality"], d["mentions"]), reverse=True)
    return out


def compute(videos: list[dict], aggregate: dict, events_by_ticker: dict) -> dict[str, Any]:
    return {
        "momentum": momentum(videos),
        "emerging": emerging(videos),
        "news_linked": news_linked(aggregate, events_by_ticker),
        "sectors": sectors(videos),
    }
