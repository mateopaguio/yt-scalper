"""Where ideas die, and why.

Every stage reports the count that entered, the count that survived, and
an explicit reason for each rejection. A funnel with unexplained losses
is just a shrinking number; the reasons are the product.

The stages mirror what the pipeline actually does, not an idealised
pipeline:

  MENTIONED   a ticker appeared in a transcript
  VALID       the symbol belongs to the company named (SpaceX was filed
              under SPCE, a real and unrelated company)
  RESEARCHED  at least one speaker did their OWN work, rather than
              reprinting someone else's reporting
  CORROBORATED  two or more independent speakers, where reprints of one
              wire story count once
  CATALYST    a filed event backs the claim
  QUALIFIED   survived the veto rules
  ALERTED     passed threshold and cooldown
"""
from __future__ import annotations

from typing import Any

STAGES = ("mentioned", "valid_symbol", "researched", "corroborated",
          "catalyst_backed", "qualified", "alerted")


def build(aggregate: dict, alerts_sent: list[dict] | None = None) -> dict[str, Any]:
    tickers = aggregate.get("tickers") or []
    rejected_syms = aggregate.get("rejected_tickers") or []
    alerted = {a.get("ticker") for a in (alerts_sent or [])}

    drops: dict[str, list[dict]] = {s: [] for s in STAGES}
    counts: dict[str, int] = {}

    # Mentioned includes symbols rejected before they ever became tickers.
    counts["mentioned"] = len(tickers) + len(rejected_syms)
    for r in rejected_syms:
        drops["valid_symbol"].append({
            "ticker": r.get("ticker"), "reason": r.get("reason"),
            "detail": (r.get("company") or "")[:70]})
    counts["valid_symbol"] = len(tickers)

    researched = []
    for e in tickers:
        indep = (e.get("basis") or {}).get("independent_channels") or []
        if indep:
            researched.append(e)
        else:
            reprints = (e.get("basis") or {}).get("reprint_channels") or []
            drops["researched"].append({
                "ticker": e["ticker"],
                "reason": "every mention reprints someone else's reporting",
                "detail": ", ".join(reprints[:3])})
    counts["researched"] = len(researched)

    corroborated = []
    for e in researched:
        indep = (e.get("basis") or {}).get("independent_channels") or []
        if len(indep) > 1:
            corroborated.append(e)
        else:
            drops["corroborated"].append({
                "ticker": e["ticker"],
                "reason": "only one source did its own work",
                "detail": indep[0] if indep else ""})
    counts["corroborated"] = len(corroborated)

    backed = []
    for e in corroborated:
        if e.get("catalyst_events"):
            backed.append(e)
        else:
            drops["catalyst_backed"].append({
                "ticker": e["ticker"],
                "reason": "no filed event behind the claim",
                "detail": (e.get("verification") or {}).get("status", "")})
    counts["catalyst_backed"] = len(backed)

    qualified = []
    for e in corroborated:              # vetoes apply to the corroborated set
        sc = e.get("score") or {}
        if sc.get("vetoed"):
            drops["qualified"].append({
                "ticker": e["ticker"],
                "reason": "; ".join(sc.get("vetoes") or []) or "vetoed",
                "detail": f"score {sc.get('total')}"})
        else:
            qualified.append(e)
    counts["qualified"] = len(qualified)

    for e in qualified:
        if e["ticker"] not in alerted:
            drops["alerted"].append({
                "ticker": e["ticker"],
                "reason": "below alert threshold or inside cooldown",
                "detail": f"score {(e.get('score') or {}).get('total')}"})
    counts["alerted"] = len([e for e in qualified if e["ticker"] in alerted])

    return {
        "counts": counts,
        "drops": drops,
        "stages": list(STAGES),
        "total_rejected": sum(len(v) for v in drops.values()),
        "note": ("Every rejection carries a reason. A funnel without them "
                 "is just a shrinking number."),
    }
