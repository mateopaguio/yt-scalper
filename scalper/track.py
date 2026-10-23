"""Creator timing ledger.

You asked to track who finds opportunities early and who shows up after
the move. Those are two different measurements with very different
requirements, and only one of them works today:

  1. EARLY RELATIVE TO OTHER CREATORS -- who in your channel set named a
     ticker first, and how far behind everyone else was. Computable from
     day one, no market data, no cost. This is what this module does.

  2. EARLY RELATIVE TO PRICE -- whether a call preceded or followed the
     move. Needs a price history source and, to be honest about it,
     point-in-time data. Not implemented here; see README. Anything
     claiming to measure it without price data is making it up.

Nothing here is a performance claim. A creator who names a ticker first
may simply be noisier, not better. First-mention counts are an input to
judgment, not a scorecard.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .analyze import clean_ticker


def build_ledger(videos: list[dict]) -> dict[str, Any]:
    """For every ticker, who mentioned it first and who followed."""
    timeline: dict[str, list[dict]] = {}

    for video in videos:
        findings = video.get("findings") or {}
        for item in findings.get("tickers") or []:
            ticker = clean_ticker(item.get("ticker", ""))
            if not ticker:
                continue
            try:
                when = datetime.fromisoformat(video["published"])
            except (ValueError, KeyError):
                continue
            timeline.setdefault(ticker, []).append({
                "channel": video["channel"],
                "tier": int(video.get("tier", 2)),
                "when": when,
                "stance": item.get("stance", "neutral"),
                "evidence": item.get("evidence", "none"),
                "url": video.get("url", ""),
            })

    ledger: dict[str, Any] = {}
    for ticker, mentions in timeline.items():
        mentions.sort(key=lambda m: m["when"])

        # One mention per channel: its earliest.
        seen: dict[str, dict] = {}
        for mention in mentions:
            if mention["channel"] not in seen:
                seen[mention["channel"]] = mention

        ordered = sorted(seen.values(), key=lambda m: m["when"])
        first = ordered[0]
        ledger[ticker] = {
            "first_channel": first["channel"],
            "first_seen": first["when"].isoformat(),
            "first_evidence": first["evidence"],
            "sequence": [
                {
                    "channel": m["channel"],
                    "tier": m["tier"],
                    "when": m["when"].isoformat(),
                    "lag_hours": round(
                        (m["when"] - first["when"]).total_seconds() / 3600, 1
                    ),
                    "stance": m["stance"],
                    "evidence": m["evidence"],
                }
                for m in ordered
            ],
            "channel_count": len(ordered),
        }
    return ledger


def creator_stats(ledger: dict[str, Any], min_tickers: int = 3) -> list[dict]:
    """Per-creator timing summary across every ticker they've discussed.

    `first_mentions` counts tickers this creator named before anyone else
    in your set. `median_lag_hours` is how far behind the first mention
    they typically are. Both describe ordering, not accuracy.
    """
    stats: dict[str, dict] = {}

    for ticker, record in ledger.items():
        for position, entry in enumerate(record["sequence"]):
            channel = entry["channel"]
            agg = stats.setdefault(channel, {
                "channel": channel,
                "tier": entry["tier"],
                "tickers_covered": 0,
                "first_mentions": 0,
                "lags": [],
                "evidence_counts": {},
                "first_mention_tickers": [],
            })
            agg["tickers_covered"] += 1
            if position == 0:
                agg["first_mentions"] += 1
                agg["first_mention_tickers"].append(ticker)
            else:
                agg["lags"].append(entry["lag_hours"])
            ev = entry["evidence"]
            agg["evidence_counts"][ev] = agg["evidence_counts"].get(ev, 0) + 1

    out = []
    for agg in stats.values():
        lags = sorted(agg["lags"])
        agg["median_lag_hours"] = (
            round(lags[len(lags) // 2], 1) if lags else None
        )
        covered = agg["tickers_covered"]
        agg["first_mention_rate"] = (
            round(agg["first_mentions"] / covered, 2) if covered else 0.0
        )
        evidence_total = sum(agg["evidence_counts"].values()) or 1
        agg["cited_evidence_rate"] = round(
            (agg["evidence_counts"].get("data", 0)
             + agg["evidence_counts"].get("reasoned", 0)) / evidence_total, 2
        )
        agg.pop("lags", None)
        agg["enough_data"] = covered >= min_tickers
        out.append(agg)

    # Creators who are often first AND usually cite something, ranked top.
    out.sort(
        key=lambda a: (a["enough_data"], a["first_mention_rate"],
                       a["cited_evidence_rate"]),
        reverse=True,
    )
    return out


def under_the_radar(aggregate: dict, ledger: dict, max_channels: int = 1) -> list[dict]:
    """Tickers with real evidence but almost no coverage across your set.

    This is the opposite of the consensus view: names where someone made
    a supported case and nobody else has picked it up yet.
    """
    out = []
    for entry in aggregate.get("tickers", []):
        if entry.get("speculative_only"):
            continue
        if len(entry.get("corroborating_channels", [])) > max_channels:
            continue
        counts = entry.get("evidence_counts") or {}
        if not (counts.get("data") or counts.get("reasoned")):
            continue
        record = ledger.get(entry["ticker"]) or {}
        out.append({
            **entry,
            "first_channel": record.get("first_channel", ""),
            "first_seen": record.get("first_seen", ""),
        })

    out.sort(
        key=lambda e: (
            (e.get("evidence_counts") or {}).get("data", 0),
            (e.get("evidence_counts") or {}).get("reasoned", 0),
        ),
        reverse=True,
    )
    return out


def tier4_candidates(
    videos: list[dict],
    min_videos: int = 5,
    hype_threshold: float = 3.5,
    unsupported_threshold: float = 0.5,
) -> list[dict]:
    """Nominate channels whose measured output looks speculative.

    Tier 4 is assigned from what a channel actually publishes -- average
    hype level, how often it is promotional, how often its claims rest on
    nothing -- not from anyone's prior opinion of the creator, and not
    from subscriber count or production quality.

    These are nominations for a human to accept or reject, never an
    automatic reclassification.
    """
    by_channel: dict[str, dict] = {}

    for video in videos:
        findings = video.get("findings") or {}
        if not findings:
            continue
        agg = by_channel.setdefault(video["channel"], {
            "channel": video["channel"],
            "current_tier": int(video.get("tier", 2)),
            "videos": 0,
            "hype_total": 0,
            "promotional": 0,
            "red_flags": 0,
            "claims": 0,
            "unsupported_claims": 0,
            "no_disclosure": 0,
        })
        agg["videos"] += 1
        agg["hype_total"] += findings.get("hype_level", 0) or 0
        if findings.get("promotional"):
            agg["promotional"] += 1
        if not findings.get("discloses_position"):
            agg["no_disclosure"] += 1
        agg["red_flags"] += len(findings.get("red_flags") or [])
        for item in findings.get("tickers") or []:
            agg["claims"] += 1
            if item.get("evidence") in ("none", "speculation"):
                agg["unsupported_claims"] += 1

    out = []
    for agg in by_channel.values():
        n = agg["videos"]
        agg["avg_hype"] = round(agg["hype_total"] / n, 2) if n else 0
        agg["promotional_rate"] = round(agg["promotional"] / n, 2) if n else 0
        agg["red_flags_per_video"] = round(agg["red_flags"] / n, 2) if n else 0
        claims = agg["claims"] or 1
        agg["unsupported_rate"] = round(agg["unsupported_claims"] / claims, 2)
        agg.pop("hype_total", None)

        agg["enough_data"] = n >= min_videos
        reasons = []
        if agg["avg_hype"] >= hype_threshold:
            reasons.append(f"average hype {agg['avg_hype']}/5")
        if agg["unsupported_rate"] >= unsupported_threshold:
            reasons.append(f"{int(agg['unsupported_rate']*100)}% of claims unsupported")
        if agg["promotional_rate"] >= 0.4:
            reasons.append(f"{int(agg['promotional_rate']*100)}% promotional")
        if agg["red_flags_per_video"] >= 1.5:
            reasons.append(f"{agg['red_flags_per_video']} red flags per video")

        agg["reasons"] = reasons
        agg["nominated"] = bool(reasons) and agg["enough_data"] and agg["current_tier"] != 4
        out.append(agg)

    out.sort(key=lambda a: (a["nominated"], a["avg_hype"]), reverse=True)
    return out
