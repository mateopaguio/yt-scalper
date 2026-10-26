"""Independent confirmation vs. one story reprinted N times.

aggregate() counts distinct channels. That overstates confirmation
whenever several desks are working from one wire story.

WHAT WAS TRIED AND REJECTED
---------------------------
Clustering theses by word overlap. It cannot work, and the live corpus
shows why: within a single ticker everyone shares vocabulary, because
they are all discussing the same company's same quarter. Measured
pairs, raw overlap:

  0.217  META  "AI monetization concrete, FCF should turn positive"
                vs "negative FCF in Q3, must raise capital"      OPPOSED
  0.182  T     "fell 7% on a report SpaceX may lease towers"
                vs "down 5.5% on a report SpaceX may lease towers"  ECHO

Overlap ranks genuine disagreement ABOVE genuine echo, so no threshold
separates them. IDF weighting was tried to privilege rare shared terms;
it moved the pair to 0.155 vs 0.165 and did not fix the inversion.
That approach is abandoned rather than shipped at a tuned threshold.

WHAT ACTUALLY WORKS
-------------------
Two things, for two different reasons.

1. Attribution (scalper.attribution). The transcripts simply SAY when a
   claim is second-hand - "following a Bloomberg report", "in the same
   telecom selloff". Classifying each claim as derivative / primary /
   original gives the discriminator that text similarity could not, and
   costs nothing. Only DERIVATIVE claims collapse confirmation; several
   speakers arguing over the same earnings release are independent.

2. Cross-ticker event grouping (below). ACROSS tickers, shared rare
   vocabulary IS informative - nothing but a common source would make a
   CCI thesis resemble a TMUS thesis. This correctly recovers the SpaceX
   cell-tower story across T/TMUS/VZ/CCI and the oil complex across
   CVX/XOM/SLB/HAL. Restricted to groups touched by >=2 channels, below
   which it just picks up one host listing several names in one video.
"""
from __future__ import annotations

import math
import re
from typing import Any

from . import attribution

STOP = {
    "the","a","an","and","or","but","if","then","than","that","this","these","those",
    "is","are","was","were","be","been","being","to","of","in","on","at","for","with",
    "by","from","as","it","its","he","she","they","them","their","his","her","we","you",
    "i","will","would","could","should","may","might","can","has","have","had","do",
    "does","did","not","no","so","up","down","out","about","into","over","after",
    "host","says","said","say","video","stock","stocks","shares","company","companies",
    "quarter","quarterly","expects","expect","guest","hosts","segment","report","reports",
    "own","view","views","offered","day","week","year","years","also","more","most",
    "percent","around","near","toward","towards","while","whether","which","who","sees",
}
WORD = re.compile(r"[a-z][a-z0-9'&.-]*[a-z0-9]|[a-z]")


def _terms(text: str, drop: set[str] | None = None) -> set[str]:
    out = set()
    for w in WORD.findall((text or "").lower()):
        w = w.strip(".-&'")
        if len(w) < 3 or w in STOP or (drop and w in drop):
            continue
        out.add(w)
    return out


def _self_words(entry: dict) -> set[str]:
    bag = {(entry.get("ticker") or "").lower()}
    for w in WORD.findall((entry.get("company") or "").lower()):
        if len(w) > 2:
            bag.add(w)
    bag.discard("")
    return bag


def build_idf(entries: list[dict]) -> dict[str, float]:
    docs = []
    for e in entries:
        drop = _self_words(e)
        for it in e.get("items") or []:
            docs.append(_terms((it.get("thesis") or "") + " " +
                               (it.get("catalyst") or ""), drop))
    n = max(len(docs), 1)
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) + 0.1 for t, c in df.items()}


def weighted_similarity(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    num = sum(idf.get(t, 1.0) for t in a & b)
    den = sum(idf.get(t, 1.0) for t in a | b)
    return num / den if den else 0.0


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.p[max(rx, ry)] = min(rx, ry)

    def groups(self):
        g: dict[int, list[int]] = {}
        for i in range(len(self.p)):
            g.setdefault(self.find(i), []).append(i)
        return list(g.values())


def cross_ticker_events(entries: list[dict], idf: dict[str, float],
                        threshold: float = 0.17,
                        min_channels: int = 2) -> list[dict]:
    """Group mentions across DIFFERENT tickers that trace to one story.

    One event moving four telecom names is one event, not four
    independent sector signals. Requires >=2 distinct channels, below
    which the group is usually a single host listing names in one video.
    """
    recs = []
    for e in entries:
        drop = _self_words(e)
        for it in e.get("items") or []:
            recs.append({
                "ticker": e["ticker"],
                "channel": it.get("channel", ""),
                "bag": _terms((it.get("thesis") or "") + " " +
                              (it.get("catalyst") or ""), drop),
                "thesis": it.get("thesis", ""),
                "basis": it.get("basis") or attribution.classify(
                    it.get("thesis", ""), it.get("catalyst", "")),
            })

    dsu = _DSU(len(recs))
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if recs[i]["ticker"] == recs[j]["ticker"]:
                continue
            if weighted_similarity(recs[i]["bag"], recs[j]["bag"], idf) >= threshold:
                dsu.union(i, j)

    out = []
    for g in dsu.groups():
        tickers = sorted({recs[i]["ticker"] for i in g})
        chans = sorted({recs[i]["channel"] for i in g if recs[i]["channel"]})
        if len(tickers) < 2 or len(chans) < min_channels:
            continue
        deriv = sum(1 for i in g if recs[i]["basis"] == "derivative")
        out.append({
            "tickers": tickers,
            "channels": chans,
            "mentions": len(g),
            "derivative_mentions": deriv,
            "wire_driven": deriv > 0,
            "representative_thesis": max(
                (recs[i] for i in g),
                key=lambda r: len(r["thesis"]))["thesis"],
        })
    return sorted(out, key=lambda s: (-len(s["tickers"]), -s["mentions"]))


def annotate(aggregate: dict, threshold: float = 0.17) -> dict:
    """Classify every claim's basis, then re-derive consensus honestly.

    `multi_channel` used to mean 'more than one channel mentioned it'.
    It now means 'more than one channel did its OWN work on it' - which
    is what the word consensus was always claiming.
    """
    entries = aggregate.get("tickers", [])
    for e in entries:
        attribution.annotate_items(e)

    idf = build_idf(entries)
    aggregate["shared_events"] = cross_ticker_events(entries, idf, threshold)

    aggregate["multi_channel"] = [
        e for e in entries
        if len(e["basis"]["independent_channels"]) > 1
    ]
    # several channels covered it, but not two of them did their own work
    aggregate["echo_chamber"] = [
        e for e in entries
        if len(e.get("corroborating_channels", [])) > 1
        and len(e["basis"]["independent_channels"]) < 2
    ]
    aggregate["channel_originality"] = attribution.channel_originality(entries)
    return aggregate


def similarity(a: str, b: str) -> float:
    """Unweighted overlap. Diagnostics only - see module docstring for
    why this must never drive clustering."""
    ta, tb = _terms(a), _terms(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0
