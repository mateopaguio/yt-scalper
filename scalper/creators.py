"""Which sources actually find something, measured against the market.

Every number here is BENCHMARK-RELATIVE. A creator whose picks returned
+2% in a month the index returned +3% did not find anything; they
tracked a rising market and you could have held SPY instead. Raw
forward returns would rank every source as good during a bull run,
which is the same error the pattern backtest caught when win rate was
compared against buy-and-hold.

Four things are measured, because "accuracy" alone is too crude:

  HIT RATE      share of resolved calls that beat the benchmark
  EDGE          mean relative return, which is what compounds
  TIMELINESS    how many days before the median mention of the same
                ticker. Being early is the whole value of a source;
                arriving after a move is not research, it is commentary.
  UNIQUENESS    share of calls where this source was the ONLY one.
                A creator repeating consensus adds nothing even when
                consensus is right.

ORIGINALITY comes from scalper.attribution: a desk reprinting a wire
story is not a source, and several of them agreeing is one fact, not
several. That distinction is enforced here rather than assumed.

INSUFFICIENT DATA IS REPORTED, NOT ESTIMATED. Below MIN_RESOLVED a
creator gets no rates at all - a 100% hit rate on two calls is noise
wearing a result's clothes, and publishing it would be the main way
this page could mislead.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

# Below this, rates are withheld entirely rather than shown with a caveat.
MIN_RESOLVED = 10
# Even above it, say loudly that this is a small sample.
CONFIDENT_RESOLVED = 30

DEFAULT_HORIZON = "7"
BENCH_KEY = "vs_spy"


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _relative(sig: dict, horizon: str, key: str = BENCH_KEY):
    rel = ((sig.get("outcome") or {}).get("relative") or {}).get(horizon)
    if not rel:
        return None
    return rel.get(key)


def timeliness(signals: list[dict]) -> dict[str, float]:
    """Days before the median first mention of the same ticker.

    Positive = earlier than the pack. Uses first-seen per channel per
    ticker, so a channel repeating a name daily is not credited twice.
    """
    first: dict[tuple, Any] = {}
    for s in signals:
        tk = s.get("ticker")
        for src in (s.get("sources") or [{"channel": s.get("channel", "")}]):
            ch = src.get("channel") or ""
            if not (tk and ch):
                continue
            when = _parse(src.get("published") or s.get("generated_at"))
            if not when:
                continue
            k = (ch, tk)
            if k not in first or when < first[k]:
                first[k] = when

    by_ticker: dict[str, list] = {}
    for (ch, tk), when in first.items():
        by_ticker.setdefault(tk, []).append((ch, when))

    lead: dict[str, list[float]] = {}
    for tk, rows in by_ticker.items():
        if len(rows) < 2:
            continue                      # no pack to be early relative to
        med = statistics.median([w.timestamp() for _, w in rows])
        for ch, when in rows:
            lead.setdefault(ch, []).append((med - when.timestamp()) / 86400.0)
    return {ch: round(statistics.mean(v), 2) for ch, v in lead.items()}


def uniqueness(signals: list[dict]) -> dict[str, float]:
    """Share of a channel's names that no other channel mentioned."""
    chans_by_ticker: dict[str, set] = {}
    chan_tickers: dict[str, set] = {}
    for s in signals:
        tk = s.get("ticker")
        for src in (s.get("sources") or []):
            ch = src.get("channel") or ""
            if tk and ch:
                chans_by_ticker.setdefault(tk, set()).add(ch)
                chan_tickers.setdefault(ch, set()).add(tk)
    out = {}
    for ch, tks in chan_tickers.items():
        if not tks:
            continue
        alone = sum(1 for t in tks if len(chans_by_ticker.get(t, ())) == 1)
        out[ch] = round(alone / len(tks), 3)
    return out


def scoreboard(signals: list[dict], horizon: str = DEFAULT_HORIZON,
               entries: list[dict] | None = None) -> dict[str, Any]:
    """Per-creator performance. Rates withheld below MIN_RESOLVED."""
    lead = timeliness(signals)
    uniq = uniqueness(signals)

    agg: dict[str, dict] = {}
    for s in signals:
        for src in (s.get("sources") or []):
            ch = src.get("channel") or ""
            if not ch:
                continue
            a = agg.setdefault(ch, {
                "channel": ch, "calls": 0, "resolved": 0, "beat": 0,
                "rel": [], "original": 0, "primary": 0, "derivative": 0,
            })
            a["calls"] += 1
            basis = src.get("basis") or "original"
            if basis in a:
                a[basis] += 1
            rel = _relative(s, horizon)
            if rel is not None:
                a["resolved"] += 1
                a["rel"].append(rel)
                if rel > 0:
                    a["beat"] += 1

    rows = []
    for ch, a in agg.items():
        n = a["resolved"]
        enough = n >= MIN_RESOLVED
        row = {
            "channel": ch,
            "calls": a["calls"],
            "resolved": n,
            "timeliness_days": lead.get(ch),
            "uniqueness": uniq.get(ch),
            "reprint_share": (round(a["derivative"] / a["calls"], 3)
                              if a["calls"] else None),
            "sufficient_data": enough,
            "confident": n >= CONFIDENT_RESOLVED,
        }
        if enough:
            row["hit_rate_vs_spy"] = round(100 * a["beat"] / n, 1)
            row["edge_mean_pct"] = round(statistics.mean(a["rel"]), 3)
            row["edge_median_pct"] = round(statistics.median(a["rel"]), 3)
        else:
            row["hit_rate_vs_spy"] = None
            row["edge_mean_pct"] = None
            row["withheld_reason"] = (
                f"only {n} resolved call(s); rates need {MIN_RESOLVED}+ "
                "to mean anything")
        rows.append(row)

    rows.sort(key=lambda r: (r["sufficient_data"],
                             r.get("edge_mean_pct") or -999), reverse=True)
    ready = [r for r in rows if r["sufficient_data"]]
    return {
        "horizon_days": horizon,
        "benchmark": "SPY",
        "creators": rows,
        "with_enough_data": len(ready),
        "total_creators": len(rows),
        "note": ("All figures are relative to SPY over the same window. A "
                 "creator whose picks rose less than the index did not find "
                 "anything. Rates are withheld below "
                 f"{MIN_RESOLVED} resolved calls rather than shown with a "
                 "caveat, because a high rate on a tiny sample is the most "
                 "misleading number this page could print."),
        "status": ("no creator has enough resolved calls yet"
                   if not ready else
                   f"{len(ready)} creator(s) have {MIN_RESOLVED}+ resolved calls"),
    }
