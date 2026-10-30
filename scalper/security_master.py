"""Company name -> ticker, so catalysts can join to the names discussed.

THE PROBLEM THIS SOLVES
56 stored catalyst events named 7 companies and produced exactly ONE
ticker. Pfizer resolved to nothing. The extraction schema tells the
model never to guess a symbol, which is the right instruction - it is
what stopped SpaceX being filed under SPCE - but it also means
well-known names arrive unresolved, and a catalyst with no ticker can
never join to a ticker. That emptied the entire critical alert tier.

The fix is a lookup, not a smarter prompt. A deterministic table either
knows a name or does not; it never invents.

LEARNED, NOT HAND-TYPED
Most of the table is built from pairs the extractor already produced
across the video corpus, where a company name and a symbol appeared
together. A name mapping to more than one symbol is DISCARDED rather
than resolved by majority vote - ambiguity is the exact condition under
which guessing causes the SPCE class of error.

Matching is exact after normalisation. No fuzzy matching, no prefix
matching, no edit distance. "Greenwich Rx" must not resolve to anything
merely because it shares letters with a listed company.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import DATA_DIR
from . import validate

PATH = DATA_DIR / "security_master.json"

# Legal suffixes and noise that should not affect a match.
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|companies|ltd|limited|"
    r"plc|llc|lp|holdings?|group|technologies|technology|industries|"
    r"international|intl|sa|nv|ag|se|the)\b\.?",
    re.I,
)
_PAREN = re.compile(r"\([^)]*\)")
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def normalise(name: str) -> str:
    s = _PAREN.sub(" ", (name or "").lower())      # "Takeda (Adzynma sponsor)"
    s = _NONWORD.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    return " ".join(s.split())


# A small seed for names the FDA and SEC feeds carry but finance videos
# rarely discuss. Deliberately short: every entry is a megacap whose
# symbol is unambiguous. Anything doubtful is left out, because a wrong
# mapping is worse than no mapping.
SEED: dict[str, str] = {
    "pfizer": "PFE", "merck": "MRK", "johnson johnson": "JNJ",
    "eli lilly": "LLY", "lilly": "LLY", "astrazeneca": "AZN",
    "novartis": "NVS", "sanofi": "SNY", "glaxosmithkline": "GSK",
    "gsk": "GSK", "bristol myers squibb": "BMY", "amgen": "AMGN",
    "gilead sciences": "GILD", "gilead": "GILD", "biogen": "BIIB",
    "regeneron pharmaceuticals": "REGN", "vertex pharmaceuticals": "VRTX",
    "moderna": "MRNA", "novo nordisk": "NVO", "takeda pharmaceutical": "TAK",
    "takeda": "TAK", "astellas pharma": "ALPMY", "bayer": "BAYRY",
    "abbvie": "ABBV", "abbott laboratories": "ABT", "abbott": "ABT",
    "walmart": "WMT", "boeing": "BA", "general electric": "GE",
}


def _load() -> dict[str, Any]:
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"map": {}, "rejected": {}}


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    import os
    os.replace(tmp, PATH)


def build(videos: list[dict]) -> dict[str, Any]:
    """Learn the table from pairs the extractor already produced."""
    from collections import Counter, defaultdict
    seen: dict[str, Counter] = defaultdict(Counter)

    for v in videos:
        for it in ((v.get("findings") or {}).get("tickers") or []):
            co = normalise(it.get("company", ""))
            tk = (it.get("ticker") or "").strip().upper()
            if not co or not tk:
                continue
            ok, _ = validate.check(tk, it.get("company", ""))
            if not ok:                       # private company, hedged, etc.
                continue
            seen[co][tk] += 1

    table: dict[str, str] = {}
    rejected: dict[str, list[str]] = {}
    for co, counts in seen.items():
        syms = [s for s in counts if validate.classify_asset(s) == "equity"]
        if len(set(syms)) == 1:
            table[co] = syms[0]
        elif len(set(syms)) > 1:
            # Ambiguous. Never resolved by majority vote: ambiguity is
            # exactly when guessing produces the SPCE class of error.
            rejected[co] = sorted(set(syms))

    for name, sym in SEED.items():
        table.setdefault(normalise(name), sym)

    data = {"map": table, "rejected": rejected,
            "learned": len(table) - len(SEED), "seeded": len(SEED)}
    _save(data)
    return data


def resolve(company: str, data: dict | None = None) -> str:
    """Exact match after normalisation, or "". Never fuzzy."""
    if not company:
        return ""
    data = data if data is not None else _load()
    return (data.get("map") or {}).get(normalise(company), "")


def stats(data: dict | None = None) -> dict[str, Any]:
    data = data if data is not None else _load()
    return {
        "entries": len(data.get("map") or {}),
        "ambiguous_rejected": len(data.get("rejected") or {}),
        "examples": sorted((data.get("map") or {}).items())[:5],
    }
