"""Price history and recommendation performance tracking.

Provider: Stooq daily CSV. Free, no key, no registration. Columns are
read from the header rather than assumed by position, so a format change
fails loudly instead of silently mis-reading prices as volume.

Honesty constraints baked in here:
  - A recommendation is scored from the FIRST CLOSE AT OR AFTER the
    video's publication, never the close before it. Using the same-day
    close for a video published after the bell would be look-ahead bias.
  - Horizons are TRADING days, counted from actual returned rows, not
    calendar days.
  - A horizon that has not elapsed yet returns None. It is never
    estimated, interpolated, or filled with the latest price.
  - If the provider returns nothing for a ticker, that is recorded as a
    failure. No synthetic prices, ever.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

STOOQ = "https://stooq.com/q/d/l/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; yt-scalper/1.1)"}

BENCHMARK = "SPY"
# Sector ETF proxies, used only when a sector is explicitly known.
SECTOR_ETF = {
    "technology": "XLK", "semiconductors": "SMH", "biotech": "XBI",
    "healthcare": "XLV", "energy": "XLE", "financials": "XLF",
    "industrials": "XLI", "consumer discretionary": "XLY",
    "consumer staples": "XLP", "utilities": "XLU", "materials": "XLB",
    "real estate": "XLRE", "communication": "XLC",
}

HORIZONS = (1, 7, 30, 90, 180)


def _stooq_symbol(ticker: str) -> str:
    """Stooq namespaces US equities with a .us suffix."""
    return ticker.lower().replace(".", "-") + ".us"


def fetch_history(ticker: str, timeout: int = 25) -> list[dict] | None:
    """Daily OHLCV for a ticker, oldest first. None if unavailable."""
    try:
        resp = requests.get(
            STOOQ,
            params={"s": _stooq_symbol(ticker), "i": "d"},
            headers=UA,
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    ! price fetch failed for {ticker}: {exc}")
        return None

    text = resp.text.strip()
    # Stooq answers an unknown symbol with a short plain-text message.
    if not text or "," not in text.splitlines()[0]:
        print(f"    ! no price data for {ticker} ({text[:60]!r})")
        return None

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None

    cols = {name.strip().lower(): name for name in reader.fieldnames}
    if "date" not in cols or "close" not in cols:
        print(f"    ! unexpected price columns for {ticker}: {reader.fieldnames}")
        return None

    rows: list[dict] = []
    for raw in reader:
        try:
            rows.append({
                "date": datetime.strptime(raw[cols["date"]], "%Y-%m-%d").date(),
                "close": float(raw[cols["close"]]),
            })
        except (ValueError, KeyError, TypeError):
            continue

    rows.sort(key=lambda r: r["date"])
    return rows or None


def _first_close_on_or_after(rows: list[dict], when) -> tuple[int, dict] | None:
    for idx, row in enumerate(rows):
        if row["date"] >= when:
            return idx, row
    return None


def measure(
    rows: list[dict],
    recommended_at: datetime,
    horizons: tuple[int, ...] = HORIZONS,
    drawdown_days: int | None = None,
) -> dict[str, Any]:
    """Return at each horizon, measured in trading days from entry."""
    entry_date = recommended_at.astimezone(timezone.utc).date()
    found = _first_close_on_or_after(rows, entry_date)
    if not found:
        return {"error": "no trading day at or after the recommendation date"}

    entry_idx, entry_row = found
    entry = entry_row["close"]
    if entry <= 0:
        return {"error": "invalid entry price"}

    out: dict[str, Any] = {
        "entry_date": entry_row["date"].isoformat(),
        "entry_close": round(entry, 4),
        "trading_days_available": len(rows) - entry_idx - 1,
        "returns": {},
    }

    for horizon in horizons:
        target = entry_idx + horizon
        if target >= len(rows):
            # Not enough elapsed trading days. Report honestly, do not guess.
            out["returns"][str(horizon)] = None
            continue
        row = rows[target]
        out["returns"][str(horizon)] = {
            "date": row["date"].isoformat(),
            "close": round(row["close"], 4),
            "return_pct": round((row["close"] / entry - 1) * 100, 2),
        }

    # Drawdown is measured over the full holding window actually available,
    # independent of which return horizons were requested -- otherwise asking
    # for a 1-day return would silently report a meaningless 0% drawdown.
    span = drawdown_days if drawdown_days is not None else max(HORIZONS)
    window = rows[entry_idx: entry_idx + span + 1]
    if len(window) > 1:
        peak = window[0]["close"]
        worst = 0.0
        for row in window[1:]:
            peak = max(peak, row["close"])
            if peak > 0:
                worst = min(worst, (row["close"] / peak - 1) * 100)
        out["max_drawdown_pct"] = round(worst, 2)
        out["drawdown_window_days"] = len(window) - 1

    return out


def score_recommendation(
    ticker: str,
    recommended_at: datetime,
    sector: str | None = None,
    cache: dict | None = None,
) -> dict[str, Any]:
    """Score one recommendation against the stock, SPY, and its sector."""
    cache = cache if cache is not None else {}

    def history(sym: str):
        if sym not in cache:
            cache[sym] = fetch_history(sym)
        return cache[sym]

    rows = history(ticker)
    if not rows:
        return {"ticker": ticker, "error": "no price history available"}

    result = {
        "ticker": ticker,
        "recommended_at": recommended_at.isoformat(),
        **measure(rows, recommended_at),
    }
    if result.get("error"):
        return result

    bench_rows = history(BENCHMARK)
    if bench_rows:
        bench = measure(bench_rows, recommended_at)
        result["benchmark"] = {"symbol": BENCHMARK, "returns": bench.get("returns", {})}
        result["excess_vs_benchmark"] = {
            h: round(
                result["returns"][h]["return_pct"]
                - bench["returns"][h]["return_pct"], 2
            )
            if result["returns"].get(h) and bench["returns"].get(h) else None
            for h in result["returns"]
        }

    etf = SECTOR_ETF.get((sector or "").strip().lower())
    if etf:
        sector_rows = history(etf)
        if sector_rows:
            sec = measure(sector_rows, recommended_at)
            result["sector_benchmark"] = {"symbol": etf, "returns": sec.get("returns", {})}

    return result


def creator_performance(scored: list[dict], min_calls: int = 5) -> list[dict]:
    """Aggregate scored recommendations per creator.

    Reports results only at horizons that have actually elapsed, and
    marks a creator as statistically thin below `min_calls`. Risk-adjusted
    figures are omitted entirely rather than computed on tiny samples,
    which is what 'where statistically meaningful' has to mean in practice.
    """
    by_creator: dict[str, dict] = {}

    for item in scored:
        creator = item.get("creator")
        if not creator or item.get("error"):
            continue
        agg = by_creator.setdefault(creator, {
            "creator": creator,
            "calls": 0,
            "per_horizon": {str(h): {"returns": [], "excess": []} for h in HORIZONS},
            "errors": 0,
        })
        agg["calls"] += 1
        excess = item.get("excess_vs_benchmark") or {}
        for h, value in (item.get("returns") or {}).items():
            if value is None:
                continue
            agg["per_horizon"].setdefault(h, {"returns": [], "excess": []})
            agg["per_horizon"][h]["returns"].append(value["return_pct"])
            if excess.get(h) is not None:
                agg["per_horizon"][h]["excess"].append(excess[h])

    out = []
    for agg in by_creator.values():
        summary = {}
        for h, data in agg["per_horizon"].items():
            rets = data["returns"]
            if not rets:
                summary[h] = None   # horizon has not elapsed for any call
                continue
            wins = sum(1 for r in rets if r > 0)
            entry = {
                "n": len(rets),
                "mean_return_pct": round(sum(rets) / len(rets), 2),
                "median_return_pct": round(sorted(rets)[len(rets) // 2], 2),
                "hit_rate": round(wins / len(rets), 2),
                "avg_win_pct": round(
                    sum(r for r in rets if r > 0) / wins, 2) if wins else None,
                "avg_loss_pct": round(
                    sum(r for r in rets if r <= 0) / (len(rets) - wins), 2
                ) if len(rets) - wins else None,
            }
            if data["excess"]:
                entry["mean_excess_vs_spy_pct"] = round(
                    sum(data["excess"]) / len(data["excess"]), 2
                )
            # Only compute a risk-adjusted figure on a sample that supports it.
            if len(rets) >= min_calls:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
                sd = var ** 0.5
                entry["return_per_unit_risk"] = round(mean / sd, 2) if sd > 0 else None
            summary[h] = entry

        out.append({
            "creator": agg["creator"],
            "calls_scored": agg["calls"],
            "statistically_thin": agg["calls"] < min_calls,
            "horizons": summary,
        })

    out.sort(key=lambda a: a["calls_scored"], reverse=True)
    return out


def collect_recommendations(videos: list[dict]) -> list[dict]:
    """Flatten stored videos into scoreable recommendations."""
    from .analyze import clean_ticker

    out = []
    for video in videos:
        findings = video.get("findings") or {}
        for item in findings.get("tickers") or []:
            ticker = clean_ticker(item.get("ticker", ""))
            if not ticker or item.get("stance") not in ("bullish", "bearish"):
                continue
            try:
                when = datetime.fromisoformat(video["published"])
            except (ValueError, KeyError):
                continue
            out.append({
                "ticker": ticker,
                "creator": video["channel"],
                "tier": int(video.get("tier", 2)),
                "stance": item.get("stance"),
                "evidence": item.get("evidence", "none"),
                "recommended_at": when,
                "video_url": video.get("url", ""),
            })
    return out
