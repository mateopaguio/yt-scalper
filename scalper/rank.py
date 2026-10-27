"""Explainable ticker ranking, with the missing inputs declared.

Every component is recorded separately so any rank can be taken apart
and argued with. The total is a weighted sum - deliberately boring,
because a score nobody can audit is worse than a simple one.

WHAT IS DELIBERATELY ABSENT
---------------------------
The 2.0 spec asks for price action, relative volume, liquidity and
options execution quality in the score. There is no market-data feed
wired in, so those components are reported as UNAVAILABLE and omitted
from the total rather than defaulted, imputed or quietly set to zero.
A score that silently treats "no data" as "neutral data" ranks a name
nobody can price above one that genuinely looks weak.

`max_possible` therefore reflects only the components actually scored.
A rank from this module is a RESEARCH PRIORITY ORDER - which names
deserve a human's attention first - not a trade signal and not a
prediction. Nothing here has been validated against outcomes; that
needs the signal log plus elapsed time.
"""
from __future__ import annotations

import math
from typing import Any

from . import freshness

# Best evidence grade seen among channels that did their own work.
EVIDENCE = {"data": 2.0, "reasoned": 1.0, "speculation": -0.5, "none": -1.0}

COMPONENTS = {
    # name:            (weight, what it measures)
    "independent_consensus": (3.0, "channels that did their own work on this name"),
    "evidence_quality":      (2.0, "strongest evidence grade among those channels"),
    "freshness":             (1.5, "new information vs. commentary on an old move"),
    "catalyst_present":      (1.0, "an identifiable event behind the claim"),
    "hype_penalty":          (2.0, "promotional framing, unsupported claims"),
}

UNAVAILABLE = {
    "price_action":    "no market-data feed configured",
    "relative_volume": "no market-data feed configured",
    "liquidity":       "no market-data feed configured",
    "options_quality": "no options-chain feed configured",
    "sector_alignment": "no sector index data configured",
}


def _hype(entry: dict) -> tuple[float, list[str]]:
    """Penalty in [0,1] from promotional signals on the source videos."""
    items = entry.get("items") or []
    if not items:
        return 0.0, []
    flags, worst = [], 0.0
    for it in items:
        h = it.get("hype_level")
        if isinstance(h, (int, float)) and h >= 4:
            worst = max(worst, 0.6 if h == 4 else 1.0)
            flags.append(f"hype {h}/5 ({it.get('channel','?')})")
        if it.get("promotional") and not it.get("discloses_position"):
            worst = max(worst, 0.5)
            flags.append(f"promotional, undisclosed ({it.get('channel','?')})")
        if it.get("evidence") in ("none", "speculation") and it.get("price_target"):
            worst = max(worst, 0.4)
            flags.append(f"price target without evidence ({it.get('channel','?')})")
    return worst, flags


def score(entry: dict) -> dict[str, Any]:
    """Score one aggregated ticker. Returns components, vetoes and total."""
    basis = entry.get("basis") or {}
    fresh = entry.get("freshness") or {}
    indep_ch = basis.get("independent_channels") or []
    items = entry.get("items") or []

    # Only claims where the speaker did their own work can corroborate.
    own = [it for it in items if it.get("basis") in ("primary", "original")]
    best_ev = max((EVIDENCE.get(it.get("evidence"), -1.0) for it in own),
                  default=-1.0)

    # Diminishing returns, but never flat: the 10th independent channel
    # should still outrank the 4th, or the top of the list is all ties.
    # log scale reaches 1.0 at 12 channels.
    n = len(indep_ch)
    consensus = 0.0 if n <= 1 else min(1.0, math.log(n) / math.log(12))

    hype_pen, hype_flags = _hype(entry)
    has_cat = any((it.get("catalyst") or "").strip() for it in own)

    raw = {
        "independent_consensus": consensus,
        "evidence_quality": best_ev / 2.0,                     # -> [-0.5, 1.0]
        "freshness": freshness.WEIGHT.get(fresh.get("state"), 0.0),
        "catalyst_present": 1.0 if has_cat else 0.0,
        "hype_penalty": -hype_pen,
    }

    detail, total, max_possible = {}, 0.0, 0.0
    for k, (w, desc) in COMPONENTS.items():
        val = raw[k]
        contrib = round(w * val, 3)
        detail[k] = {"raw": round(val, 3), "weight": w,
                     "contribution": contrib, "measures": desc}
        total += contrib
        if w > 0 and k != "hype_penalty":
            max_possible += w

    # --- veto rules: reasons this must not be presented as corroborated ---
    vetoes = []
    if n == 0:
        vetoes.append("no channel did its own work - every mention is a reprint")
    elif n == 1 and len(entry.get("corroborating_channels", [])) > 1:
        vetoes.append("apparent multi-channel coverage is one story reprinted")
    if best_ev <= -1.0 and own:
        vetoes.append("no supporting evidence in any original claim")
    if hype_pen >= 1.0:
        vetoes.append("maximum hype score with no disclosure")
    if fresh.get("state") == "late_to_story":
        vetoes.append("retrospective commentary - the move already happened")
    if not own:
        vetoes.append("no original or primary-source claim at all")

    return {
        "total": round(total, 3),
        "max_possible": round(max_possible, 3),
        "components": detail,
        "vetoes": vetoes,
        "vetoed": bool(vetoes),
        "unavailable_components": dict(UNAVAILABLE),
        "independent_channels": n,
        "basis_counts": basis.get("counts", {}),
        "freshness_state": fresh.get("state"),
    }


def rank(aggregate: dict) -> dict:
    """Score every ticker; split into ranked and vetoed, both recorded."""
    for e in aggregate.get("tickers", []):
        e["score"] = score(e)

    scored = aggregate.get("tickers", [])
    aggregate["ranked"] = sorted(
        [e for e in scored if not e["score"]["vetoed"]],
        key=lambda e: -e["score"]["total"])
    # kept, not discarded - a vetoed signal is still a recorded prediction
    aggregate["vetoed"] = sorted(
        [e for e in scored if e["score"]["vetoed"]],
        key=lambda e: -e["score"]["total"])
    aggregate["rank_meta"] = {
        "components_scored": list(COMPONENTS),
        "components_unavailable": dict(UNAVAILABLE),
        "caveat": ("Research priority order, not a trade signal. No component "
                   "has been validated against realised outcomes yet."),
    }
    return aggregate


# --- Byrde Score -------------------------------------------------------
# A 0-100 presentation of the same components, because a raw sum is hard
# to read at a glance. It is NOT a probability of profit and must never
# be shown as one: no component has been validated against outcomes, and
# a number that looks like a percentage invites being read as one.
BYRDE_FLOOR = 0.0


def byrde_score(entry: dict, reliability: float | None = None) -> dict:
    """Scale the component total to 0-100 and name what is missing.

    `reliability` is the measured, benchmark-relative hit rate of the
    sources behind this name, in [0,1], or None when too few of their
    calls have resolved. It is folded in ONLY when present - inventing a
    prior would make the score look informed when it is not.
    """
    sc = entry.get("score") or {}
    total = sc.get("total")
    max_possible = sc.get("max_possible") or 0
    if total is None or max_possible <= 0:
        return {"score": None, "reason": "not scored"}

    base = max(0.0, min(1.0, total / max_possible))
    parts = {"components": round(base * 100, 1)}

    if reliability is None:
        score = base * 100
        parts["reliability"] = None
        note = ("no reliability input: too few of these sources' calls have "
                "resolved against a benchmark")
    else:
        r = max(0.0, min(1.0, reliability))
        score = (base * 0.75 + r * 0.25) * 100
        parts["reliability"] = round(r * 100, 1)
        note = "includes measured source reliability vs SPY"

    return {
        "score": round(score, 1),
        "parts": parts,
        "unvalidated": dict(UNAVAILABLE),
        "note": note,
        "caveat": ("Research priority on a 0-100 scale. NOT a probability "
                   "of profit, and not a forecast - no component has been "
                   "validated against realised outcomes."),
    }
