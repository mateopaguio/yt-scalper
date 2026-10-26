"""Is this claim original work, or a reprint of someone else's reporting?

This is the discriminator that text similarity could not provide.

Measured finding that motivates it: within a single ticker, every
speaker shares vocabulary because they are all discussing the same
company's same quarter, so word overlap cannot tell a reprint of a
Bloomberg scoop apart from original analysis reaching the opposite
conclusion. But the transcripts SAY which one they are - "following a
Bloomberg report", "attributed to a report that" - and that admission
survives into the extracted thesis.

Three classes, because they mean different things for confirmation:

  DERIVATIVE - the claim's basis is a third party's reporting. Ten of
      these are one fact, not ten confirmations.
  PRIMARY    - the basis is a company/official event everyone can read
      directly (an earnings release, a filing). Several speakers
      analysing the same release ARE independent, even though they
      share a source; they can and do disagree about it.
  ORIGINAL   - the speaker's own analysis, valuation or judgment.

Only DERIVATIVE collapses confirmation. Conflating it with PRIMARY was
the first version's bug: "reported better-than-expected earnings" is
not an echo of anything.

Regex, not an API call - it costs nothing and runs retroactively over
already-analysed videos. It is deliberately conservative: a miss leaves
a claim looking more original than it is, which is the safe direction
for a tool whose job is to resist overcounting confirmation.
"""
from __future__ import annotations

import re

# A named outlet is the strongest possible tell.
_OUTLET = r"(?:bloomberg|reuters|wsj|wall\s+street\s+journal|financial\s+times|\bft\b|cnbc|barron'?s|marketwatch|the\s+information|semianalysis|new\s+york\s+times|\bnyt\b|axios|politico|the\s+verge|techcrunch)"

# "the company reported X" / "reported revenue of" - primary, NOT a reprint.
_COMPANY_REPORTED = re.compile(
    r"\b(?:company|firm|it|they|management)\s+reported\b"
    r"|\breported\s+(?:its|their|a|an|revenue|sales|earnings|eps|results|profit|"
    r"net\s+income|better|worse|slow|strong|weak|record|adjusted|quarterly|fiscal|q[1-4])",
    re.I,
)

# Attribution to a third party's reporting.
_DERIVATIVE = re.compile(
    r"""
      # 1. a named outlet's report/story/article, or "per Bloomberg"
        \b(?:a|the)\s+""" + _OUTLET + r"""\s+(?:report|story|article|piece|scoop)\b
      | \b(?:per|citing|cites|cited|according\s+to|reported\s+by)\s+(?:the\s+)?""" + _OUTLET + r"""\b
      | \b""" + _OUTLET + r"""\s+report(?:ed|s|ing)?\b

      # 2. the move is pinned on an unnamed third-party report
      | \b(?:following|after|on|amid|amidst|tied\s+to|linked\s+to|attributed\s+to|
            in\s+response\s+to|sparked\s+by|triggered\s+by|prompted\s+by|
            on\s+the\s+back\s+of|due\s+to|because\s+of|driven\s+by)
        \s+(?:a|an|the)?\s*
        (?:\w+\s+){0,3}?
        (?:news\s+)?(?:report|reports|reporting|story|headline|article|scoop|rumou?r|leak)\b

      # 3. explicitly flagged as hearsay
      | \breported(?:ly)?\s+(?:that|talks|discussions|plans|interest|in\s+talks)\b
      | \b(?:a|the)\s+(?:flash\s+)?report\s+(?:about|that|of|on)\b
      | \brumou?r(?:ed|s)?\s+(?:that|to\s+be)\b
      | \b(?:sources|people\s+familiar)\s+(?:say|said|told)\b

      # 4. relaying a sell-side note rather than doing the work
      | \banalyst(?:s)?\s+(?:note|report|upgrade|downgrade|initiated|raised|cut|lowered)\b
      | \b(?:upgraded|downgraded|initiated)\s+(?:by|at)\s+\w+
    """,
    re.I | re.X,
)

# Back-reference to a story already in circulation. Not attribution in
# so many words, but it means the same thing: this item is one more
# print of a wave someone else started. Measured on the live corpus,
# this is how a second desk covers a scoop it did not get - "on the
# same SpaceX competition worries", "in the same telecom selloff".
_BACKREF = re.compile(
    r"""
        \bthe\s+same\s+(?:\w+[\s/-]+){0,4}?
          (?:story|stories|selloff|sell-off|worries|worry|concerns|fears|news|
             report|reports|theme|narrative|move|trade|rout|slide|rally)\b
      | \bin\s+the\s+(?:\w+[\s/-]+){0,4}?(?:story|selloff|sell-off|rout|saga)\b
      | \balongside\s+\w+\s+in\s+the\b
      | \btied\s+to\s+the\s+reported\b
      | \bon\s+the\s+same\b
      | \bsame\s+(?:\w+[\s/-]+){0,3}?(?:story|selloff|sell-off|news|worries)\b
    """,
    re.I | re.X,
)

# Company/official events anyone can read first-hand.
_PRIMARY = re.compile(
    r"""\b(?:earnings\s+(?:report|call|release|beat|miss|results)|quarterly\s+results|
          q[1-4]\s+(?:earnings|results|report)|guidance|outlook|10-[kq]|8-k|
          press\s+release|investor\s+day|shareholder\s+letter|filing|filed|
          sec\s+filing|prospectus|conference\s+call|annual\s+report|
          reported\s+(?:revenue|earnings|eps|results))\b""",
    re.I | re.X,
)


def classify(thesis: str, catalyst: str = "") -> str:
    """-> 'derivative' | 'primary' | 'original'"""
    text = ((thesis or "") + " " + (catalyst or "")).strip()
    if not text:
        return "original"

    if _BACKREF.search(text):
        return "derivative"

    if _DERIVATIVE.search(text):
        # "reported better-than-expected earnings" must not count as a
        # reprint just because the word "reported" appears.
        masked = _COMPANY_REPORTED.sub(" ", text)
        if _DERIVATIVE.search(masked):
            return "derivative"

    # A company reporting its own numbers IS a primary disclosure - the
    # same phrase that must not count as a reprint above.
    if _COMPANY_REPORTED.search(text) or _PRIMARY.search(text):
        return "primary"
    return "original"


def annotate_items(entry: dict) -> dict:
    """Tag every mention of a ticker, and summarise the mix."""
    items = entry.get("items") or []
    counts = {"derivative": 0, "primary": 0, "original": 0}
    for it in items:
        k = classify(it.get("thesis", ""), it.get("catalyst", ""))
        it["basis"] = k
        counts[k] += 1

    n = len(items) or 1
    # Channels that did their OWN work on this name. This - not the raw
    # channel count - is what deserves the word "corroboration".
    indep_ch = sorted({it.get("channel") for it in items
                       if it.get("basis") in ("primary", "original") and it.get("channel")})
    deriv_ch = sorted({it.get("channel") for it in items
                       if it.get("basis") == "derivative" and it.get("channel")})

    entry["basis"] = {
        "counts": counts,
        "derivative_share": round(counts["derivative"] / n, 2),
        "independent_channels": indep_ch,
        "reprint_channels": deriv_ch,
        # every channel covering this name was reprinting the same wire
        "wire_only": counts["derivative"] > 0 and counts["primary"] + counts["original"] == 0,
    }
    return entry


def channel_originality(entries: list[dict], min_claims: int = 3) -> list[dict]:
    """Per-channel reprint rate. Descriptive, not a quality judgment:
    a news desk reprinting the wire is doing its job. It matters only
    because reprints must not be counted as independent confirmation."""
    agg: dict[str, dict] = {}
    for e in entries:
        for it in e.get("items") or []:
            ch = it.get("channel")
            if not ch:
                continue
            a = agg.setdefault(ch, {"channel": ch, "derivative": 0,
                                    "primary": 0, "original": 0, "total": 0})
            a[it.get("basis") or "original"] += 1
            a["total"] += 1
    out = [a for a in agg.values() if a["total"] >= min_claims]
    for a in out:
        a["derivative_share"] = round(a["derivative"] / a["total"], 2)
        a["original_share"] = round(a["original"] / a["total"], 2)
    return sorted(out, key=lambda a: -a["derivative_share"])
