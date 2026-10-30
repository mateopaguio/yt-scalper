"""Reject ticker symbols that do not belong to the company being discussed.

The extraction schema already tells the model not to invent symbols for
private companies. It does anyway, and the failure mode is worse than
invention:

  WAYMO  "Waymo (Alphabet)"   - not a symbol at all
  SPCE   "SpaceX (private/listed status not clarified in transcript)"

SPCE is a REAL, liquid, tradeable symbol - Virgin Galactic. So SpaceX's
$4.4bn net income was filed under an unrelated public company that
somebody could actually go and buy. A prompt instruction cannot be the
only thing standing between a transcript and that.

Two deterministic checks, both cheap and neither trusting the model:

1. NAMED PRIVATE COMPANY. If the company field names a company known to
   be private, there is no correct symbol, so the symbol is dropped.
2. THE MODEL'S OWN HEDGING. "(private/listed status not clarified)" is a
   confession of uncertainty. Any hedge in the company field means the
   symbol is not safe to key on.

The thesis is always KEPT. The commentary is real and worth reading;
only the tradeable symbol is removed, because that is the part that can
be acted on by mistake. Entries keep `ticker_rejected` so the reason
stays visible rather than the data silently vanishing.
"""
from __future__ import annotations

import re

# Well-known private companies that get discussed on finance channels.
#
# Matched on WORD BOUNDARIES, never as substrings. Naive substring
# matching killed Norwegian Cruise Line (NCLH) because "cruise" is also
# GM's Cruise, and Coherent because it contains "cohere". Both are large
# public companies. A false positive here deletes a real, tradeable
# ticker, so single common words are excluded entirely and anything
# ambiguous is spelled out in full.
PRIVATE_COMPANIES = {
    "spacex", "space x", "starlink", "openai", "open ai", "anthropic",
    "xai", "x.ai", "neuralink", "the boring company", "bytedance",
    "tiktok", "databricks", "epic games", "shein", "revolut",
    "deepmind", "fanatics", "plaid inc", "grammarly", "scale ai",
    "perplexity ai", "mistral ai", "cohere inc", "figure ai",
    "gm cruise", "cruise llc", "blue origin", "relativity space",
    "anduril", "waymo",
}

# Subsidiaries and brands that are not separately listed. Discussing them
# is fine; giving them their own symbol is not.
NOT_SEPARATELY_LISTED = {
    "waymo", "youtube", "instagram", "whatsapp", "aws", "azure",
    "google cloud", "deepmind", "starlink", "oculus", "linkedin",
    "github", "twitch", "chatgpt",
}

# Words that are too common to match on their own - they appear inside
# the real names of listed companies.
_AMBIGUOUS = {"cruise", "cohere", "chime", "canva", "valve", "discord",
              "stripe", "figure", "scale", "mistral", "plaid", "gemini",
              "bing", "perplexity"}


def _names_company(text: str, names) -> str:
    """Word-boundary match, so 'Cruise Line' does not match 'cruise'."""
    low = (text or "").lower()
    for name in names:
        if name in _AMBIGUOUS:
            continue
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return name
    return ""


# The model telling us it is unsure.
HEDGE = re.compile(
    r"private|not clarified|not publicly|unlisted|not listed|pre-ipo|"
    r"subsidiary|division of|unit of|owned by|parent company|"
    r"listed status|unclear|unknown|\bn/a\b",
    re.I,
)

# Not equities. Keeping them is fine; ranking them beside stocks is a
# category error, and several are not securities at all.
NON_EQUITY = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "HYPE", "USDT", "USDC",
    "BNB", "AVAX", "DOT", "LINK", "MATIC", "LTC", "TRX", "SHIB",
}


def check(ticker: str, company: str) -> tuple[bool, str]:
    """-> (ok_to_keep_symbol, reason_if_not)"""
    sym = (ticker or "").strip().upper()
    co = (company or "").strip()
    if not sym:
        return True, ""

    low = co.lower()

    hit = _names_company(co, PRIVATE_COMPANIES)
    if hit:
        return False, f"{co.split('(')[0].strip() or hit} is not publicly traded"

    for name in NOT_SEPARATELY_LISTED:
        # "Waymo (Alphabet)" - the parent is listed, this unit is not
        if low.startswith(name):
            return False, f"{name} is not separately listed"

    if HEDGE.search(co):
        return False, "listed status uncertain in the source"

    return True, ""


def classify_asset(ticker: str) -> str:
    return "crypto" if (ticker or "").upper() in NON_EQUITY else "equity"


def scrub_findings(findings: dict) -> list[dict]:
    """Strip unsafe symbols from one video's findings, in place.

    Returns a list of what was removed, for logging.
    """
    removed = []
    for it in (findings or {}).get("tickers") or []:
        sym = (it.get("ticker") or "").strip().upper()
        if not sym:
            continue
        it["asset_class"] = classify_asset(sym)
        ok, why = check(sym, it.get("company", ""))
        if not ok:
            removed.append({"ticker": sym, "company": it.get("company", ""),
                            "reason": why})
            # keep the thesis, drop only the actionable symbol
            it["ticker_rejected"] = sym
            it["ticker_rejected_reason"] = why
            it["ticker"] = ""
    return removed


def scrub_videos(videos: list[dict]) -> list[dict]:
    out = []
    for v in videos:
        for r in scrub_findings(v.get("findings") or {}):
            out.append({**r, "channel": v.get("channel", ""),
                        "video_id": v.get("video_id", "")})
    return out
