"""Turn a transcript into structured findings using the Claude API.

The schema is deliberately opinionated: every ticker mention has to
come back with a stance, the reasoning actually given on the video,
and an honest read on whether that reasoning rests on anything. That
last field is what separates a useful digest from a hype feed.
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import Anthropic, APIError

from . import validate

_TICKER_OK = __import__("re").compile(r"^[A-Z]{1,6}(?:[.\-][A-Z]{1,2})?$")


def clean_ticker(raw: str) -> str:
    """Normalize a ticker, or return '' if it does not look like one.

    Accepts plain symbols (NVDA) and share-class forms (BRK.B, BF-B).
    Rejects prose, numbers, and anything over-long.
    """
    sym = (raw or "").upper().strip().lstrip("$").rstrip(".,;:")
    return sym if _TICKER_OK.match(sym) else ""


EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_findings",
    "description": "Record the investment-related content of a video transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "2-4 sentences: what this video actually covered, in plain "
                    "language. Lead with the substance, not the framing."
                ),
            },
            "overall_stance": {
                "type": "string",
                "enum": ["bullish", "bearish", "mixed", "neutral"],
                "description": "The video's overall posture on the market.",
            },
            "hype_level": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": (
                    "1 = measured analysis with caveats and numbers. "
                    "5 = urgency language, guaranteed-return claims, "
                    "countdowns, 'before it's too late' framing."
                ),
            },
            "discloses_position": {
                "type": "boolean",
                "description": "Does the host say whether they own what they discuss?",
            },
            "promotional": {
                "type": "boolean",
                "description": (
                    "Is this substantially an ad for a paid service, course, "
                    "newsletter, or Discord?"
                ),
            },
            "tickers": {
                "type": "array",
                "description": (
                    "One entry per company meaningfully discussed. Skip passing "
                    "mentions with no view attached."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": (
                                "Symbol in caps, ONLY for companies listed on a public "
                                "exchange. Empty string for private companies (SpaceX, "
                                "Anthropic, Bose, OpenAI and the like), for subsidiaries "
                                "that do not trade separately, and for sector or index "
                                "commentary. Never invent a plausible-looking symbol for "
                                "something that is not publicly traded."
                            ),
                        },
                        "company": {"type": "string"},
                        "stance": {
                            "type": "string",
                            "enum": ["bullish", "bearish", "neutral", "unclear"],
                        },
                        "thesis": {
                            "type": "string",
                            "description": (
                                "The reasoning given on the video, in 1-2 sentences "
                                "and in your own words. Do not quote at length."
                            ),
                        },
                        "catalyst": {
                            "type": "string",
                            "description": (
                                "The specific event expected to move it (earnings, "
                                "FDA decision, product launch). Empty if none given."
                            ),
                        },
                        "price_target": {
                            "type": "string",
                            "description": "Any target or range stated. Empty if none.",
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "e.g. 'next earnings', '6-12 months'. Empty if none.",
                        },
                        "conviction": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "How strongly the host pushed this.",
                        },
                        "evidence": {
                            "type": "string",
                            "enum": ["data", "reasoned", "speculation", "none"],
                            "description": (
                                "'data' = cites financials, filings, or figures. "
                                "'reasoned' = coherent argument, no hard numbers. "
                                "'speculation' = guesswork or vibes. "
                                "'none' = assertion with nothing behind it."
                            ),
                        },
                        "timestamp": {
                            "type": "string",
                            "description": "Transcript timestamp where this starts, e.g. '12:40'.",
                        },
                        "sector": {
                            "type": "string",
                            "description": (
                                "Broad sector in lowercase, chosen from: technology, "
                                "semiconductors, software, ai infrastructure, healthcare, "
                                "biotech, energy, financials, industrials, consumer "
                                "discretionary, consumer staples, utilities, materials, "
                                "real estate, communication, transport, crypto, other. "
                                "Empty if the video does not make it clear."
                            ),
                        },
                        "breakout_signal": {
                            "type": "string",
                            "enum": ["none", "new_coverage", "price_move", "catalyst_driven",
                                     "guidance_change", "unusual_volume"],
                            "description": (
                                "Only if the video explicitly says so: is this name moving "
                                "on something specific right now? 'none' is the normal answer."
                            ),
                        },
                    },
                    "required": [
                        "ticker", "company", "stance", "thesis",
                        "conviction", "evidence", "timestamp",
                    ],
                },
            },
            "macro_points": {
                "type": "array",
                "description": "Broader market/economic claims, if any.",
                "items": {"type": "string"},
            },
            "red_flags": {
                "type": "array",
                "description": (
                    "Things a careful viewer should notice: guaranteed returns, "
                    "undisclosed promotion, pump-style urgency, no risk discussion, "
                    "penny-stock promotion."
                ),
                "items": {"type": "string"},
            },
        },
        "required": [
            "summary", "overall_stance", "hype_level",
            "discloses_position", "promotional", "tickers",
        ],
    },
}

SYSTEM_PROMPT = """\
You summarize finance content -- YouTube videos and written articles -- for \
someone who wants the substance without reading or watching all of it.

Report what the video claims and how well-supported those claims are. You \
are not evaluating whether a call is correct and you are not giving \
investment advice — you are a faithful, skeptical note-taker.

Rules:
- Paraphrase. Never reproduce long passages of the transcript. Keep any \
direct quote under 15 words, and use at most one.
- Record the reasoning the host gave, even when it is thin. If it is thin, \
say so through the `evidence` field rather than by editorializing in the thesis.
- Do not add analysis, context, or price information the video did not contain.
- A ticker belongs in the list only if the video expressed some view on it. \
Companies named only in passing do not count.
- Private companies still belong in the list when the video has a view on them, \
but with an EMPTY ticker. Inventing a symbol for a company that does not trade \
is worse than leaving it blank.
- Be strict about `hype_level` and `red_flags`. Urgency language, promises of \
guaranteed gains, and absent risk discussion are exactly what the reader needs \
flagged.
- If the piece is not really about markets at all, return an empty ticker list \
and say so in the summary.
- For articles, treat analyst quotes as the article's reporting, not as the \
author's own view, and note who is being quoted in the thesis."""


def analyze(
    client: Anthropic,
    video: dict,
    transcript_text: str,
    model: str = "claude-haiku-5-5",
    max_tokens: int = 4096,
) -> dict[str, Any] | None:
    """Extract structured findings for one video. Returns None on failure."""
    is_article = video.get("source_type") == "article"
    label = "Publication" if is_article else "Channel"
    body_header = (
        "Article text:"
        if is_article
        else (
            "Transcript (auto-generated captions, so expect mis-hearings "
            "and missing punctuation):"
        )
    )

    user_content = (
        f"{label}: {video['channel']}\n"
        f"Title: {video['title']}\n"
        f"Published: {video['published']}\n"
        f"URL: {video['url']}\n\n"
        f"{body_header}\n\n"
        f"{transcript_text}"
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_findings"},
            messages=[{"role": "user", "content": user_content}],
        )
    except APIError as exc:
        print(f"    ! Claude API error on {video['video_id']}: {exc}")
        return None

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            findings = dict(block.input)
            findings["_usage"] = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            return findings

    print(f"    ! no structured output for {video['video_id']}")
    return None


def aggregate(
    videos: list[dict],
    watchlist: list[str] | None = None,
    consensus_tiers: set[int] | None = None,
) -> dict[str, Any]:
    """Roll individual videos up into a cross-channel view.

    The useful signal is not any one host's pick — it is which names
    several independent channels landed on in the same window.
    """
    watchlist = [t.upper() for t in (watchlist or [])]
    # Tier 4 (speculative/opinion-heavy) is counted but never treated as
    # corroboration. Otherwise three hype channels pushing the same name
    # would outrank one analyst citing filings.
    consensus_tiers = consensus_tiers if consensus_tiers is not None else {1, 2, 3}
    by_ticker: dict[str, dict[str, Any]] = {}

    rejected: list[dict] = []

    for video in videos:
        findings = video.get("findings") or {}
        for item in findings.get("tickers") or []:
            symbol = clean_ticker(item.get("ticker", ""))
            if not symbol:
                continue

            # The schema tells the model not to invent symbols for private
            # companies; it does anyway. SpaceX arrived tagged SPCE, which
            # is Virgin Galactic - a real company somebody could buy by
            # mistake. Checked here rather than at write time so it applies
            # to everything already stored, and so the raw model output is
            # preserved for audit instead of being edited after the fact.
            ok, why = validate.check(symbol, item.get("company", ""))
            if not ok:
                rejected.append({
                    "ticker": symbol,
                    "company": item.get("company", ""),
                    "reason": why,
                    "channel": video.get("channel", ""),
                    "video_id": video.get("video_id", ""),
                    "thesis": (item.get("thesis") or "")[:200],
                })
                continue

            entry = by_ticker.setdefault(
                symbol,
                {
                    "ticker": symbol,
                    "company": item.get("company", ""),
                    "mentions": 0,
                    "channels": [],
                    "corroborating_channels": [],
                    "speculative_channels": [],
                    "bullish": 0,
                    "bearish": 0,
                    "neutral": 0,
                    "evidence_counts": {},
                    "on_watchlist": symbol in watchlist,
                    "items": [],
                },
            )

            tier = int(video.get("tier", 2))
            entry["mentions"] += 1
            if video["channel"] not in entry["channels"]:
                entry["channels"].append(video["channel"])
            bucket = (
                "corroborating_channels" if tier in consensus_tiers
                else "speculative_channels"
            )
            if video["channel"] not in entry[bucket]:
                entry[bucket].append(video["channel"])
            if not entry["company"] and item.get("company"):
                entry["company"] = item["company"]

            stance = item.get("stance", "neutral")
            if stance in ("bullish", "bearish", "neutral"):
                entry[stance] += 1

            evidence = item.get("evidence", "none")
            entry["evidence_counts"][evidence] = (
                entry["evidence_counts"].get(evidence, 0) + 1
            )

            entry["items"].append(
                {
                    "channel": video["channel"],
                    "tier": tier,
                    "source_type": video.get("source_type", "video"),
                    "video_title": video["title"],
                    "url": video["url"],
                    "timestamp": item.get("timestamp", ""),
                    "stance": stance,
                    "thesis": item.get("thesis", ""),
                    "catalyst": item.get("catalyst", ""),
                    "price_target": item.get("price_target", ""),
                    "timeframe": item.get("timeframe", ""),
                    "conviction": item.get("conviction", ""),
                    "evidence": evidence,
                }
            )

    def rank(entry: dict) -> tuple:
        # Evidence quality and independent corroboration drive the ranking.
        # Raw mention count is the weakest tiebreaker, so volume alone
        # cannot push a name to the top.
        quality = (
            entry["evidence_counts"].get("data", 0) * 2
            + entry["evidence_counts"].get("reasoned", 0)
            - entry["evidence_counts"].get("none", 0)
        )
        return (
            entry["on_watchlist"],
            len(entry["corroborating_channels"]),
            quality,
            entry["mentions"],
        )

    ranked = sorted(by_ticker.values(), key=rank, reverse=True)
    for entry in ranked:
        entry["consensus"] = (
            "bullish" if entry["bullish"] > entry["bearish"]
            else "bearish" if entry["bearish"] > entry["bullish"]
            else "split"
        )

    for entry in ranked:
        entry["speculative_only"] = (
            not entry["corroborating_channels"] and bool(entry["speculative_channels"])
        )

    return {
        "tickers": ranked,
        # Genuine corroboration: more than one channel that counts, agreeing.
        "multi_channel": [
            t for t in ranked if len(t["corroborating_channels"]) > 1
        ],
        # Named only by speculative channels — shown, but never as consensus.
        "speculative_only": [t for t in ranked if t["speculative_only"]],
        "total_tickers": len(ranked),
        # Symbols the model produced that do not belong to the company it
        # named. Surfaced rather than silently dropped, so a bad extraction
        # is visible instead of just absent.
        "rejected_tickers": rejected,
    }
