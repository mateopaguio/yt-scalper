"""Sort processed content into what belongs on a trading dashboard.

Three buckets, deliberately conservative:

  TRADE_RELEVANT  names specific companies with a stance or a catalyst.
                  This is what the dashboard shows by default.
  MARKET_CONTEXT  real market content with no specific name attached -
                  macro, rates, sector commentary, education. Useful,
                  but it is not a lead.
  ARCHIVE         no market content found. Interviews about someone's
                  life, product reviews, lifestyle, off-topic.

NOTHING IS DELETED. Everything stays in the store and stays reachable;
this only decides what competes for attention. A misclassification
should cost a click, never a loss of data.

BIAS TOWARD KEEPING. A video with ANY ticker carrying a thesis is
trade-relevant even if the rest is chatter. Archive requires positive
evidence of irrelevance - no tickers, no macro points, and either an
explicit off-topic signal or an empty analysis. "Unsure" resolves to
market_context, which is still visible, rather than to archive.
"""
from __future__ import annotations

import re
from typing import Any

TRADE_RELEVANT = "trade_relevant"
MARKET_CONTEXT = "market_context"
ARCHIVE = "archive"
BUCKETS = (TRADE_RELEVANT, MARKET_CONTEXT, ARCHIVE)

# Titles that are usually not market research. Matched on the TITLE only,
# and never sufficient on their own - a video still needs to lack tickers
# and macro points before it is archived.
OFF_TOPIC = re.compile(
    r"\b(vlog|my (day|life|story|journey)|apartment tour|house tour|"
    r"morning routine|what i eat|day in the life|unboxing|"
    r"car review|gaming|reacts? to|reaction|podcast highlights|"
    r"full (interview|episode) with|sit[- ]down with|life advice|"
    r"workout|fitness|recipe|travel|vacation|q ?& ?a)\b",
    re.I,
)

# Real market content that happens not to name a ticker.
MACRO_HINT = re.compile(
    r"\b(fed|fomc|interest rate|inflation|cpi|ppi|jobs report|payrolls|"
    r"yield|treasury|recession|gdp|macro|market outlook|s&p|nasdaq|"
    r"dow|vix|volatility|sector rotation|earnings season|valuation|"
    r"bond|credit spread|dollar|commodit)\b",
    re.I,
)


def classify(video: dict) -> dict[str, Any]:
    """-> {bucket, reason, signals}. Never raises."""
    findings = video.get("findings") or {}
    title = video.get("title", "") or ""
    summary = findings.get("summary", "") or ""
    tickers = findings.get("tickers") or []
    macro = findings.get("macro_points") or []

    # A ticker only counts if the model actually said something about it.
    substantive = [
        t for t in tickers
        if (t.get("ticker") or "").strip()
        and ((t.get("thesis") or "").strip() or (t.get("catalyst") or "").strip())
    ]
    signals = {
        "tickers": len(tickers),
        "substantive_tickers": len(substantive),
        "macro_points": len(macro),
        "off_topic_title": bool(OFF_TOPIC.search(title)),
        "macro_language": bool(MACRO_HINT.search(title + " " + summary)),
        "analysed": bool(findings),
    }

    if substantive:
        return {"bucket": TRADE_RELEVANT, "signals": signals,
                "reason": f"{len(substantive)} company claim(s) with a thesis or catalyst"}

    if macro or signals["macro_language"]:
        return {"bucket": MARKET_CONTEXT, "signals": signals,
                "reason": "market commentary without a specific name"}

    if tickers:
        # Names mentioned in passing with nothing said about them.
        return {"bucket": MARKET_CONTEXT, "signals": signals,
                "reason": "tickers mentioned in passing, no thesis"}

    if not findings:
        # Never analysed (no transcript, skipped). Unknown, not irrelevant.
        return {"bucket": MARKET_CONTEXT, "signals": signals,
                "reason": "not analysed yet - unknown, not irrelevant"}

    if signals["off_topic_title"]:
        return {"bucket": ARCHIVE, "signals": signals,
                "reason": "off-topic title and no market content found"}

    return {"bucket": ARCHIVE, "signals": signals,
            "reason": "analysed, but no companies and no macro content"}


def annotate(videos: list[dict]) -> dict[str, Any]:
    counts = {b: 0 for b in BUCKETS}
    for v in videos:
        r = classify(v)
        v["relevance"] = r
        counts[r["bucket"]] += 1
    return {
        "counts": counts,
        "total": len(videos),
        "note": ("Nothing is deleted. Archive only hides a video from the "
                 "default feed; it stays stored and reachable. Anything "
                 "uncertain resolves to market context, which stays visible."),
    }
