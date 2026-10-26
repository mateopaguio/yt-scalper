"""Is this genuinely new information, or commentary on an old story?

Three independent signals, because each is weak alone:

  1. COVERAGE AGE - how many distinct days this ticker has been showing
     up. A name in its sixth straight day of coverage is not news.
  2. RETROSPECTION - the claim's own language. "Q3 earnings (already
     reported)", "after the selloff", "last week" all mark commentary
     on something the market has already digested.
  3. VIDEO AGE - how long ago the commentary itself was published.

LEFT-CENSORING, which matters more than it looks
------------------------------------------------
Coverage age can only be measured back to the first day of stored data.
A ticker whose first sighting IS that first day may have been running
for months beforehand - we cannot know. Those are reported as
`unknown`, never as `new`. Treating them as new would manufacture a
flood of fake "first mentions" on day one of any deployment and poison
every downstream performance comparison. The censored window shrinks by
one day for every day the system runs.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# The claim is looking backwards at something already known.
RETROSPECTIVE = re.compile(
    r"""\b(?:already\s+(?:reported|happened|priced(?:\s+in)?|announced|known|moved)
        | last\s+(?:week|month|quarter|night|session)
        | earlier\s+(?:this|in\s+the)\s+(?:week|month|quarter|year|session)
        | previously\s+(?:reported|announced|disclosed)
        | has\s+already
        | since\s+(?:reporting|the\s+report|earnings)
        | following\s+(?:last|the\s+recent)
        | after\s+(?:the\s+)?(?:recent|last\s+week'?s?)
        | in\s+the\s+wake\s+of
        | post-earnings
        | year[\s-]to[\s-]date
        | so\s+far\s+this\s+(?:year|quarter))\b""",
    re.I | re.X,
)

# The claim points at something that has NOT happened yet - the most
# valuable kind, because the move is still ahead of the market.
FORWARD = re.compile(
    r"""\b(?:upcoming|ahead\s+of|scheduled|expected\s+(?:on|in|next)
        | will\s+report | reports?\s+(?:on|next|after\s+the\s+(?:bell|close))
        | next\s+(?:week|month|quarter|earnings)
        | guidance\s+for | due\s+(?:on|in|next)
        | catalyst\s+(?:ahead|coming) | watch\s+for
        | if\s+(?:it|they|the\s+company)\s+(?:breaks?|clears?|holds?)
        | pending | awaiting | yet\s+to)\b""",
    re.I | re.X,
)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def coverage_history(videos: list[dict]) -> dict:
    """Per-ticker: which days it appeared, and where the data floor is."""
    days: dict[str, set[str]] = {}
    all_days = set()
    for v in videos:
        pub = (v.get("published") or "")[:10]
        if not pub:
            continue
        all_days.add(pub)
        f = v.get("findings") or {}
        for it in f.get("tickers") or []:
            sym = (it.get("ticker") or "").strip().upper()
            if sym:
                days.setdefault(sym, set()).add(pub)
    floor = min(all_days) if all_days else None
    return {"days": days, "data_floor": floor, "data_days": len(all_days)}


def classify(entry: dict, hist: dict, now: datetime | None = None) -> dict:
    """Freshness profile for one aggregated ticker."""
    now = now or datetime.now(timezone.utc)
    sym = (entry.get("ticker") or "").upper()
    seen = sorted(hist["days"].get(sym, set()))
    floor = hist.get("data_floor")

    items = entry.get("items") or []
    text = " ".join(((it.get("thesis") or "") + " " + (it.get("catalyst") or ""))
                    for it in items)
    retro = bool(RETROSPECTIVE.search(text))
    fwd = bool(FORWARD.search(text))

    # newest mention in this window
    ages = []
    for it in items:
        d = _parse(it.get("published") or entry.get("latest_published"))
        if d:
            ages.append((now - d).total_seconds() / 3600.0)
    newest_h = round(min(ages), 1) if ages else None

    censored = bool(seen and floor and seen[0] <= floor)
    days_covered = len(seen)

    # Classification order matters. The TEXT signals are trustworthy
    # today; the COVERAGE-AGE signals need history this deployment does
    # not have yet, so they are claimed only where actually observed.
    #
    # Critically there is no "new" state. A ticker seen once cannot be
    # distinguished from one we simply had not sampled: 58 channels
    # produce ~20 videos a day, so most names appear exactly once
    # regardless of how long the market has been discussing them.
    # Calling those "new" labelled 73% of tickers as fresh discoveries
    # and measured our own sampling sparsity, not the market's.
    if censored:
        state = "unknown"              # may predate all stored data
    elif days_covered >= 3:
        state = "running"              # observed repeatedly - real
    elif days_covered == 2:
        state = "developing"           # observed twice - real
    else:
        state = "single_sighting"      # seen once: genuinely new, or
                                       # just not sampled before

    # Language is independent of coverage history and valid immediately.
    if retro and not fwd:
        state = "late_to_story"
    elif fwd and not retro and state in ("single_sighting", "developing", "running"):
        state = "forward_catalyst"

    return {
        "state": state,
        "days_covered": days_covered,
        "first_seen": seen[0] if seen else None,
        "last_seen": seen[-1] if seen else None,
        "censored": censored,
        "retrospective": retro,
        "forward_looking": fwd,
        "newest_mention_hours": newest_h,
    }


# How freshness should move a rank. Positive = genuinely new or still
# ahead of the event; negative = arriving after the move.
WEIGHT = {
    # Only states backed by something observed carry weight. A single
    # sighting earns 0.0 - no claim either way - rather than a bonus it
    # has not earned.
    "forward_catalyst": 0.8,   # points at an event still ahead
    "developing": 0.2,         # observed twice
    "single_sighting": 0.0,    # unresolvable with current history
    "unknown": 0.0,            # censored at the data floor
    "running": -0.3,           # repeated coverage: market has it
    "late_to_story": -0.8,     # explicitly retrospective language
}

# Coverage-age states only become meaningful once the stored history is
# longer than the typical gap between a channel's mentions of a name.
MIN_DAYS_FOR_AGE_SIGNAL = 21

def annotate(aggregate: dict, videos: list[dict], now: datetime | None = None) -> dict:
    hist = coverage_history(videos)
    for e in aggregate.get("tickers", []):
        e["freshness"] = classify(e, hist, now)
    aggregate["freshness_meta"] = {
        "data_floor": hist.get("data_floor"),
        "data_days": hist.get("data_days"),
        "censored_tickers": sum(1 for e in aggregate.get("tickers", [])
                                if e["freshness"]["censored"]),
        "age_signal_reliable": hist.get("data_days", 0) >= MIN_DAYS_FOR_AGE_SIGNAL,
        "days_needed": max(0, MIN_DAYS_FOR_AGE_SIGNAL - hist.get("data_days", 0)),
        "note": ("Tickers first seen on the data floor date have unknown true age. "
                 "There is no 'new' state: a single sighting cannot be told apart "
                 "from a name we had not sampled yet. Coverage-age states need "
                 f">={MIN_DAYS_FOR_AGE_SIGNAL} days of history to mean anything; "
                 "the text-based states (late_to_story, forward_catalyst) are "
                 "valid immediately."),
    }
    return aggregate
