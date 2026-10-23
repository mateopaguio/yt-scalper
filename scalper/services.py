"""Reusable service layer.

Every capability the pipeline has is exposed here as a plain function
that takes arguments and returns JSON-serializable dicts. Nothing in
this module prints, writes HTML, or sends notifications.

That boundary is the point: run.py is one caller, and a web framework in
The Byrde is another. Dropping this into an existing app means importing
these functions and wrapping each in a route -- not rebuilding the
pipeline. `api.py` in the project root is a working reference
implementation of exactly that.

Every function returns {"ok": bool, ...}. Errors are returned, never raised.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import analyze, catalysts as cat, config as cfgmod, feeds, store, track


def _ok(**payload) -> dict[str, Any]:
    return {"ok": True, "generated_at": datetime.now(timezone.utc).isoformat(), **payload}


def _err(message: str, **extra) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


# --------------------------------------------------------------- channels

def list_channels(cfg=None) -> dict:
    """Every configured channel with its tier and resolution status."""
    cfg = cfg or cfgmod.load()
    channels, notes = feeds.dedupe(cfg.channels)
    state = store.channel_state()

    rows = []
    for channel in channels:
        spec = cfgmod.tier_spec(cfg, channel.tier)
        entry = state.get(channel.channel_id or "", {})
        rows.append({
            "name": channel.name,
            "handle": channel.handle,
            "channel_id": channel.channel_id,
            "tier": channel.tier,
            "tier_label": spec.get("label"),
            "interval_hours": spec.get("interval_hours"),
            "counts_toward_consensus": spec.get("counts_toward_consensus", True),
            "resolved": bool(channel.channel_id),
            "last_checked": entry.get("last_checked"),
            "checks": entry.get("checks", 0),
        })

    return _ok(
        channels=rows,
        total=len(rows),
        resolved=sum(1 for r in rows if r["resolved"]),
        unresolved=[r["name"] for r in rows if not r["resolved"]],
        duplicate_notes=notes,
    )


def channel_detail(name: str, cfg=None) -> dict:
    """One channel plus every video stored for it."""
    cfg = cfg or cfgmod.load()
    match = next(
        (c for c in cfg.channels if c.name.lower() == name.lower()), None
    )
    if not match:
        return _err(f"no channel named {name!r}")

    videos = [v for v in store.load_videos() if v.get("channel") == match.name]
    return _ok(
        channel={
            "name": match.name, "handle": match.handle,
            "channel_id": match.channel_id, "tier": match.tier,
        },
        videos=videos,
        video_count=len(videos),
    )


# ------------------------------------------------------------------ videos

def list_videos(limit: int = 50, channel: str | None = None,
                ticker: str | None = None, tier: int | None = None) -> dict:
    """Processed videos, newest first, with optional filters."""
    videos = store.load_videos()

    if channel:
        videos = [v for v in videos if v.get("channel", "").lower() == channel.lower()]
    if tier is not None:
        videos = [v for v in videos if int(v.get("tier", 2)) == tier]
    if ticker:
        want = ticker.upper()
        videos = [
            v for v in videos
            if any(
                (t.get("ticker") or "").upper() == want
                for t in ((v.get("findings") or {}).get("tickers") or [])
            )
        ]

    return _ok(videos=videos[:limit], count=len(videos[:limit]), total_matching=len(videos))


def video_detail(video_id: str) -> dict:
    video = next(
        (v for v in store.load_videos() if v.get("video_id") == video_id), None
    )
    if not video:
        return _err(f"no stored video with id {video_id!r}")
    return _ok(video=video)


# ----------------------------------------------------------- stock mentions

def stock_mentions(ticker: str | None = None, days: int = 7, cfg=None) -> dict:
    """Ticker-level view: who said what, with what support."""
    cfg = cfg or cfgmod.load()
    videos = store.recent_videos(days=days)
    aggregate = analyze.aggregate(videos, cfg.watchlist, cfgmod.consensus_tiers(cfg))

    events = cat.by_ticker(store.recent_catalysts(days=days))
    for entry in aggregate["tickers"]:
        entry["catalyst_events"] = events.get(entry["ticker"], [])

    if ticker:
        want = ticker.upper()
        found = next((t for t in aggregate["tickers"] if t["ticker"] == want), None)
        if not found:
            return _err(f"{want} not mentioned in the last {days} days")
        return _ok(ticker=found)

    return _ok(
        tickers=aggregate["tickers"],
        multi_channel=aggregate["multi_channel"],
        speculative_only=aggregate["speculative_only"],
        total=aggregate["total_tickers"],
        window_days=days,
    )


# ------------------------------------------------------- creator reliability

def creator_reliability(days: int = 90, cfg=None) -> dict:
    """Creator timing and, when price data exists, measured performance.

    Timing (who was first, and by how long) needs no market data.
    Performance needs a price provider and elapsed time; when neither is
    present this reports that plainly rather than returning zeros.
    """
    cfg = cfg or cfgmod.load()
    videos = store.recent_videos(days=days)
    ledger = track.build_ledger(videos)

    payload = {
        "timing": track.creator_stats(ledger),
        "tier4_candidates": [
            c for c in track.tier4_candidates(videos) if c["nominated"]
        ],
        "window_days": days,
        "performance": None,
        "performance_note": (
            "Price-based scoring is computed by scripts/score_performance.py, "
            "which requires network access to the price provider and enough "
            "elapsed trading days. Horizons that have not elapsed report null."
        ),
    }

    scored = store.load_performance() if hasattr(store, "load_performance") else []
    if scored:
        from . import prices
        payload["performance"] = prices.creator_performance(scored)

    return _ok(**payload)


# ------------------------------------------------------ catalyst intelligence

def catalyst_intelligence(days: int = 7, cfg=None) -> dict:
    cfg = cfg or cfgmod.load()
    events = store.recent_catalysts(days=days)
    relevant = [
        e for e in events if (e.get("findings") or {}).get("is_market_relevant")
    ]
    return _ok(
        events=relevant,
        calendar=cat.upcoming(events, cfg.catalyst_horizon_days),
        by_ticker=cat.by_ticker(events),
        total_polled=len(events),
        market_relevant=len(relevant),
        window_days=days,
    )


# ----------------------------------------------------- opportunity rankings

def opportunity_rankings(days: int = 7, limit: int = 10, cfg=None) -> dict:
    """The ranked views, kept separate rather than blended into one score.

    Deliberately NOT a single 0-100 number. A name with two independent
    analysts citing filings and a name with high mention volume are
    different things, and collapsing them hides exactly the distinction
    that matters.
    """
    cfg = cfg or cfgmod.load()
    videos = store.recent_videos(days=days)
    aggregate = analyze.aggregate(videos, cfg.watchlist, cfgmod.consensus_tiers(cfg))
    ledger = track.build_ledger(videos)
    events = cat.by_ticker(store.recent_catalysts(days=days))

    for entry in aggregate["tickers"]:
        entry["catalyst_events"] = events.get(entry["ticker"], [])

    def evidence_score(e):
        c = e.get("evidence_counts") or {}
        return c.get("data", 0) * 2 + c.get("reasoned", 0) - c.get("none", 0)

    tickers = aggregate["tickers"]
    return _ok(
        best_supported=sorted(tickers, key=evidence_score, reverse=True)[:limit],
        independent_consensus=aggregate["multi_channel"][:limit],
        under_the_radar=track.under_the_radar(aggregate, ledger)[:limit],
        most_discussed=sorted(
            tickers, key=lambda e: e["mentions"], reverse=True)[:limit],
        with_filed_catalyst=[
            e for e in tickers if e.get("catalyst_events")][:limit],
        speculative_only=aggregate["speculative_only"][:limit],
        window_days=days,
        note=(
            "These are research queues, not buy signals, and not a single "
            "composite score. Evidence labels describe how well a claim was "
            "supported by its source, not whether it is correct."
        ),
    )


# ------------------------------------------------------------ hourly report

def hourly_report(cfg=None) -> dict:
    """The full report payload — what run.py renders and an app would serve."""
    cfg = cfg or cfgmod.load()
    days = cfg.window_days
    videos = store.recent_videos(days=days)
    events = store.recent_catalysts(days=days)
    aggregate = analyze.aggregate(videos, cfg.watchlist, cfgmod.consensus_tiers(cfg))
    ledger = track.build_ledger(videos)

    by_t = cat.by_ticker(events)
    for entry in aggregate["tickers"]:
        entry["catalyst_events"] = by_t.get(entry["ticker"], [])

    pending = store.load_pending()
    return _ok(
        summary={
            "videos_in_window": len(videos),
            "tickers": aggregate["total_tickers"],
            "independent_consensus": len(aggregate["multi_channel"]),
            "speculative_only": len(aggregate["speculative_only"]),
            "catalysts": len([
                e for e in events
                if (e.get("findings") or {}).get("is_market_relevant")
            ]),
        },
        rankings=opportunity_rankings(days, cfg=cfg),
        catalysts=catalyst_intelligence(days, cfg=cfg),
        creators=creator_reliability(cfg=cfg),
        data_health=data_health(cfg=cfg),
        pending_transcripts=len(pending),
    )


# ------------------------------------------------------- historical / health

def historical_performance(ticker: str | None = None) -> dict:
    """Stored price-scored recommendations. Empty until scoring has run."""
    scored = store.load_performance() if hasattr(store, "load_performance") else []
    if ticker:
        want = ticker.upper()
        scored = [s for s in scored if s.get("ticker") == want]
    if not scored:
        return _ok(
            records=[], count=0,
            note=("No price-scored recommendations yet. Run "
                  "scripts/score_performance.py once price data is reachable; "
                  "horizons that have not elapsed will report null."),
        )
    from . import prices
    return _ok(
        records=scored,
        count=len(scored),
        by_creator=prices.creator_performance(scored),
    )


def data_health(cfg=None) -> dict:
    cfg = cfg or cfgmod.load()
    channels, notes = feeds.dedupe(cfg.channels)
    videos = store.load_videos()
    pending = store.load_pending()
    failed = [v for v in videos if v.get("skip_reason")]

    return _ok(
        channels_configured=len(channels),
        channels_resolved=sum(1 for c in channels if c.channel_id),
        channels_unresolved=[c.name for c in channels if not c.channel_id],
        duplicate_notes=notes,
        videos_stored=len(videos),
        videos_analyzed=sum(1 for v in videos if v.get("findings")),
        videos_failed=len(failed),
        failure_reasons=sorted({v["skip_reason"] for v in failed}),
        pending_transcripts=len(pending),
        catalysts_stored=len(store.load_catalysts()),
        last_notified_at=store.last_notified_at(),
    )
