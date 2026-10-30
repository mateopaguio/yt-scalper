"""Check claimed catalysts against actually-filed events.

A host saying "they just got FDA approval" is a claim. An FDA approval
notice in the regulatory feed is a fact. This joins the two.

THE DISTINCTION THAT MATTERS
----------------------------
"No filing found" and "these feeds cannot see this kind of event" are
completely different conclusions, and collapsing them would make the
whole thing dishonest in the direction that looks most impressive.

The configured feeds are SEC press releases and enforcement, FDA drug,
biologics and safety notices, and Fed policy. None of them carry 8-K,
10-Q or earnings releases. So an earnings claim can NEVER be confirmed
here, and marking it "unconfirmed" would imply suspicion that the data
simply cannot support. Those are reported as OUTSIDE_COVERAGE.

Only event types the feeds genuinely cover can earn UNCONFIRMED, and
even then it means "not found in our window", not "false".

Current practical limit, stated plainly: catalyst events are resolving
with almost no tickers attached, because the model is correctly refusing
to guess symbols from headline-only text. Until article bodies are
retrieved, most verification will come back NOT_VERIFIABLE. That is a
pipeline gap, not a verdict on the claims.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

# What the configured feeds can actually confirm.
COVERED = {
    "regulatory_approval", "regulatory_action", "clinical_trial",
    "recall_safety", "enforcement", "trading_suspension",
    "macro_rates", "macro_data",
}
# Real events that these feeds do not carry. Absence proves nothing.
NOT_COVERED = {
    "earnings", "guidance", "merger_acquisition", "product_launch",
    "contract_award", "leadership_change", "offering_dilution",
    "bankruptcy_distress", "other",
}

CONFIRMED = "confirmed"
UNCONFIRMED = "unconfirmed"
OUTSIDE_COVERAGE = "outside_coverage"
NOT_VERIFIABLE = "not_verifiable"
NO_CLAIM = "no_claim"

_WORD = re.compile(r"[a-z][a-z0-9'-]{2,}")
_STOP = {"the", "and", "for", "with", "its", "from", "that", "this", "has",
         "new", "will", "after", "said", "says", "company", "shares", "stock"}

# Keywords that hint at the event type of a free-text catalyst claim.
TYPE_HINTS = {
    "regulatory_approval": ("approval", "approved", "fda cleared", "clearance",
                            "pdufa", "authoris", "authoriz"),
    "clinical_trial": ("trial", "phase 1", "phase 2", "phase 3", "readout",
                       "endpoint", "study results"),
    "recall_safety": ("recall", "safety", "boxed warning", "adverse"),
    "enforcement": ("sec charges", "fraud", "investigation", "subpoena",
                    "enforcement", "lawsuit", "litigation"),
    "trading_suspension": ("suspension", "halted", "trading halt"),
    "earnings": ("earnings", "eps", "quarterly results", "beat", "miss",
                 "revenue", "guidance", "quarter"),
    "merger_acquisition": ("acquisition", "acquire", "merger", "buyout",
                           "takeover", "deal to buy"),
    "offering_dilution": ("offering", "dilution", "share sale", "raise capital",
                          "convertible"),
    "macro_rates": ("fed", "rate cut", "rate hike", "fomc", "interest rate"),
}


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def guess_event_type(claim: str) -> str:
    low = (claim or "").lower()
    best, score = "other", 0
    for etype, hints in TYPE_HINTS.items():
        n = sum(1 for h in hints if h in low)
        if n > score:
            best, score = etype, n
    return best


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def verify_entry(entry: dict, events_for_ticker: list[dict],
                 window_days: int = 14,
                 now: datetime | None = None) -> dict[str, Any]:
    """Verify the catalyst claims made about one ticker."""
    now = now or datetime.now(timezone.utc)
    items = entry.get("items") or []
    claims = [it for it in items if (it.get("catalyst") or "").strip()]

    if not claims:
        return {"status": NO_CLAIM, "checked": 0, "confirmed": 0,
                "detail": [], "feed_has_ticker": bool(events_for_ticker)}

    detail = []
    for it in claims:
        claim = it.get("catalyst", "")
        etype = guess_event_type(claim)

        if not events_for_ticker:
            # Nothing filed for this ticker at all in our feeds.
            status = (OUTSIDE_COVERAGE if etype in NOT_COVERED
                      else NOT_VERIFIABLE)
            detail.append({"claim": claim[:160], "channel": it.get("channel", ""),
                           "event_type": etype, "status": status,
                           "reason": ("feeds do not carry this event type"
                                      if status == OUTSIDE_COVERAGE
                                      else "no filed event for this ticker in the window"),
                           "matched": None})
            continue

        cterms = _terms(claim)
        best, best_overlap = None, 0.0
        for ev in events_for_ticker:
            when = _parse(ev.get("published"))
            if when and abs((now - when).days) > window_days:
                continue
            etext = (ev.get("title", "") + " " + (ev.get("summary") or ""))
            eterms = _terms(etext)
            if not cterms or not eterms:
                continue
            overlap = len(cterms & eterms) / len(cterms | eterms)
            if overlap > best_overlap:
                best, best_overlap = ev, overlap

        if best and best_overlap >= 0.12:
            detail.append({"claim": claim[:160], "channel": it.get("channel", ""),
                           "event_type": etype, "status": CONFIRMED,
                           "reason": f"matches a filed event (overlap {best_overlap:.2f})",
                           "matched": {"title": best.get("title", "")[:140],
                                       "url": best.get("url", ""),
                                       "published": best.get("published", "")}})
        elif etype in NOT_COVERED:
            detail.append({"claim": claim[:160], "channel": it.get("channel", ""),
                           "event_type": etype, "status": OUTSIDE_COVERAGE,
                           "reason": "feeds do not carry this event type",
                           "matched": None})
        else:
            detail.append({"claim": claim[:160], "channel": it.get("channel", ""),
                           "event_type": etype, "status": UNCONFIRMED,
                           "reason": "feeds cover this event type, but nothing matching was filed",
                           "matched": None})

    counts: dict[str, int] = {}
    for d in detail:
        counts[d["status"]] = counts.get(d["status"], 0) + 1

    if counts.get(CONFIRMED):
        overall = CONFIRMED
    elif counts.get(UNCONFIRMED):
        overall = UNCONFIRMED
    elif counts.get(OUTSIDE_COVERAGE):
        overall = OUTSIDE_COVERAGE
    else:
        overall = NOT_VERIFIABLE

    return {"status": overall, "checked": len(detail),
            "confirmed": counts.get(CONFIRMED, 0),
            "counts": counts, "detail": detail,
            "feed_has_ticker": bool(events_for_ticker)}


def annotate(aggregate: dict, events_by_ticker: dict,
             window_days: int = 14, now: datetime | None = None) -> dict:
    for entry in aggregate.get("tickers", []):
        entry["verification"] = verify_entry(
            entry, events_by_ticker.get(entry["ticker"]) or [], window_days, now)

    tot: dict[str, int] = {}
    for e in aggregate.get("tickers", []):
        s = e["verification"]["status"]
        tot[s] = tot.get(s, 0) + 1

    tickers_with_events = len([t for t in events_by_ticker if events_by_ticker[t]])
    aggregate["verification_meta"] = {
        "by_status": tot,
        "tickers_with_filed_events": tickers_with_events,
        "covered_event_types": sorted(COVERED),
        "uncovered_event_types": sorted(NOT_COVERED),
        "note": (
            "'unconfirmed' means the configured feeds cover that event type but "
            "nothing matching was filed in the window - not that the claim is "
            "false. 'outside_coverage' means the feeds cannot see that kind of "
            "event at all (no 8-K/10-Q/earnings source is configured), so "
            "absence there proves nothing."
        ),
        "limitation": (
            "" if tickers_with_events else
            "No catalyst event currently resolves to a ticker, so nothing can be "
            "confirmed. Catalyst articles are being summarised from headlines "
            "only, which leaves the model unable to identify the company. This "
            "is a pipeline gap, not a verdict on any claim."
        ),
    }
    return aggregate
