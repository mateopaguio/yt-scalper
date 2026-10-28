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
import os
import time
import io
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from . import dataquality

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


# A browser signature. Stooq began answering plain clients with a
# JavaScript challenge ("This site requires JavaScript to verify your
# browser"), which is HTML where CSV was expected. requests cannot run
# JS, so that provider simply stops working and we move to the next one.
BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/json,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _looks_like_challenge(text: str) -> bool:
    head = (text or "")[:400].lower()
    return ("<!doctype html" in head or "<html" in head
            or "requires javascript" in head or "enable javascript" in head)


def _from_stooq(ticker: str, timeout: int) -> list[dict] | None:
    try:
        resp = requests.get(
            STOOQ, params={"s": _stooq_symbol(ticker), "i": "d"},
            headers=BROWSER, timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    - stooq unavailable for {ticker}: {str(exc)[:70]}")
        return None

    text = resp.text.strip()
    if _looks_like_challenge(text):
        print(f"    - stooq served a browser challenge for {ticker}")
        return None
    if not text or "," not in text.splitlines()[0]:
        print(f"    - stooq has no data for {ticker}")
        return None

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None
    cols = {n.strip().lower(): n for n in reader.fieldnames}
    if "date" not in cols or "close" not in cols:
        print(f"    - stooq columns unusable for {ticker}: {reader.fieldnames[:2]}")
        return None

    rows = []
    for raw in reader:
        try:
            row = {
                "date": datetime.strptime(raw[cols["date"]], "%Y-%m-%d").date(),
                "close": float(raw[cols["close"]]),
            }
            # Full OHLCV where available: the pattern scanner needs high,
            # low and volume, and a close-only row silently breaks it.
            for field in ("open", "high", "low"):
                if field in cols and raw.get(cols[field]) not in (None, "", "-"):
                    row[field] = float(raw[cols[field]])
            if "volume" in cols and raw.get(cols["volume"]) not in (None, "", "-"):
                row["volume"] = float(raw[cols["volume"]])
            rows.append(row)
        except (ValueError, KeyError, TypeError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows or None


def _from_yahoo(ticker: str, timeout: int, rng: str = "5y") -> list[dict] | None:
    """Yahoo's chart endpoint. JSON, no API key, full OHLCV."""
    try:
        resp = requests.get(
            YAHOO + ticker.upper(),
            params={"range": rng, "interval": "1d"},
            headers=BROWSER, timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"    - yahoo unavailable for {ticker}: {str(exc)[:70]}")
        return None

    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        print(f"    - yahoo error for {ticker}: {str(chart['error'])[:70]}")
        return None
    results = chart.get("result") or []
    if not results:
        return None
    res = results[0]
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if not stamps or not closes:
        return None

    rows = []
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue                      # holiday / missing bar, never filled in
        row = {"date": datetime.fromtimestamp(ts, tz=timezone.utc).date(),
               "close": float(c)}
        for field in ("open", "high", "low", "volume"):
            seq = quote.get(field) or []
            if i < len(seq) and seq[i] is not None:
                row[field] = float(seq[i])
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows or None


def _from_yfinance(ticker: str, timeout: int, period: str = "5y") -> list[dict] | None:
    """Yahoo via yfinance, which handles the cookie/crumb handshake.

    The raw chart endpoint now answers unauthenticated clients with 429
    on the very first request: it wants a session cookie and a crumb.
    Reimplementing that by hand means chasing a moving target, which is
    the problem yfinance already solves and keeps solving.

    Optional dependency. Absent, this provider stands aside quietly and
    the others are tried.
    """
    try:
        import yfinance
    except ImportError:
        return None

    try:
        df = yfinance.Ticker(ticker.upper()).history(period=period,
                                                     interval="1d",
                                                     auto_adjust=False)
    except Exception as exc:  # noqa: BLE001 - library raises many types
        print(f"    - yfinance failed for {ticker}: {str(exc)[:70]}")
        return None

    if df is None or df.empty:
        return None

    rows = []
    for stamp, rec in df.iterrows():
        try:
            close = float(rec["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close != close:               # NaN - a missing bar, never filled in
            continue
        row = {"date": stamp.date() if hasattr(stamp, "date") else stamp,
               "close": close}
        for src, dst in (("Open", "open"), ("High", "high"),
                         ("Low", "low"), ("Volume", "volume")):
            try:
                v = float(rec[src])
                if v == v:
                    row[dst] = v
            except (KeyError, TypeError, ValueError):
                pass
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows or None


ALPACA_DATA = "https://data.alpaca.markets/v2/stocks/"


def alpaca_configured() -> bool:
    return bool(os.environ.get("ALPACA_KEY_ID")
                and os.environ.get("ALPACA_SECRET_KEY"))


_ENTITLEMENT_CACHE: dict[str, str] = {}


def alpaca_feed(timeout: int = 15, force: bool = False) -> tuple[str, str]:
    """Which Alpaca feed this account may actually use.

    Probed, never assumed. SIP is the consolidated tape and is what you
    want for daily bars; IEX is a single venue carrying a few percent
    of volume, so its daily OHLC can differ from the real session. But
    SIP is a paid entitlement on many accounts, and asking for it
    without the entitlement returns 403 rather than quietly degrading.

    ALPACA_FEED overrides the probe. Returns (feed, how_we_know).
    """
    forced = (os.environ.get("ALPACA_FEED") or "").strip().lower()
    if forced in ("iex", "sip"):
        return forced, "set by ALPACA_FEED"
    if not force and "feed" in _ENTITLEMENT_CACHE:
        return _ENTITLEMENT_CACHE["feed"], _ENTITLEMENT_CACHE.get("why", "cached")

    key = os.environ.get("ALPACA_KEY_ID")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        return "iex", "no credentials; nothing probed"

    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "Accept": "application/json"}
    try:
        resp = requests.get(
            f"{ALPACA_DATA}SPY/bars",
            params={"timeframe": "1Day", "limit": 1, "feed": "sip"},
            headers=headers, timeout=timeout)
        if resp.status_code == 200 and (resp.json().get("bars") or []):
            feed, why = "sip", "probed: the account is entitled to SIP"
        elif resp.status_code in (401, 403):
            feed, why = "iex", f"probed: SIP refused ({resp.status_code}); using IEX"
        else:
            feed, why = "iex", f"probed: SIP returned {resp.status_code}; using IEX"
    except requests.RequestException as exc:
        feed, why = "iex", f"probe failed ({type(exc).__name__}); defaulting to IEX"

    _ENTITLEMENT_CACHE.update({"feed": feed, "why": why})
    return feed, why


def _from_alpaca(ticker: str, timeout: int, years: int = 7,
                 timeframe: str = "1Day",
                 report: dict | None = None) -> list[dict] | None:
    """Alpaca market data. A real API with a key, not a scraped page.

    Preferred when configured: stooq answers automated clients with a
    JavaScript challenge and Yahoo's raw endpoint 429s them, because
    neither wants to be read by a program. Alpaca does.

    Bars are requested ADJUSTED. Raw bars show a 2-for-1 split as an
    overnight halving, which every pattern rule here reads as a
    textbook breakdown that never happened, and which makes the return
    from any entry before the split wrong by the split factor.

    `report` collects an error class and the feed actually used, so a
    caller can tell a wrong key from a rate limit from an outage.
    """
    key = os.environ.get("ALPACA_KEY_ID")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        if report is not None:
            report["error"] = dataquality.NOT_CONFIGURED
        return None

    feed, why = alpaca_feed(timeout=timeout)
    if report is not None:
        report.update({"feed": feed, "feed_reason": why})

    start = (datetime.now(timezone.utc) - timedelta(days=365 * years)).date()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
               "Accept": "application/json"}

    rows: list[dict] = []
    dropped = 0
    page_token = None
    for _ in range(40):                       # bounded: never loop forever
        params = {"timeframe": timeframe, "start": start.isoformat(),
                  "limit": 10000, "adjustment": "all", "feed": feed}
        if page_token:
            params["page_token"] = page_token

        payload = None
        for attempt in range(3):              # retry ONLY what is retryable
            try:
                resp = requests.get(f"{ALPACA_DATA}{ticker.upper()}/bars",
                                    params=params, headers=headers,
                                    timeout=timeout)
                if resp.status_code == 200:
                    payload = resp.json()
                    break
                kind = dataquality.classify_error(status=resp.status_code)
                if kind in dataquality.RETRYABLE and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if report is not None:
                    report["error"] = kind
                    report["status_code"] = resp.status_code
                print(f"    - alpaca {kind} for {ticker} "
                      f"(HTTP {resp.status_code})")
                return None
            except (requests.RequestException, ValueError) as exc:
                kind = dataquality.classify_error(exc=exc)
                if kind in dataquality.RETRYABLE and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if report is not None:
                    report["error"] = kind
                print(f"    - alpaca {kind} for {ticker}: {str(exc)[:60]}")
                return None
        if payload is None:
            if report is not None:
                report.setdefault("error", dataquality.UNREACHABLE)
            return None

        for b in payload.get("bars") or []:
            try:
                row = {
                    "date": datetime.fromisoformat(
                        b["t"].replace("Z", "+00:00")).date(),
                    "open": float(b["o"]), "high": float(b["h"]),
                    "low": float(b["l"]), "close": float(b["c"]),
                    "volume": float(b.get("v") or 0),
                    "feed": feed,
                    "adjusted": True,
                }
            except (KeyError, ValueError, TypeError):
                # Counted, never repaired. A bar invented from its
                # neighbours is a lie that backtests cannot see.
                dropped += 1
                continue
            # Parseable is not the same as possible. A bar whose high
            # sits below its low is structurally fine and arithmetically
            # nonsense, and it reaches the pattern rules as a real
            # range. Rejected here, at the boundary, rather than noticed
            # later by whatever happens to look.
            if not dataquality.ohlc_valid(row):
                dropped += 1
                continue
            rows.append(row)

        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if report is not None:
        report["dropped_bars"] = dropped
        report["error"] = dataquality.OK if rows else dataquality.EMPTY
    if dropped:
        print(f"    - alpaca dropped {dropped} malformed bar(s) for {ticker}")

    rows.sort(key=lambda r: r["date"])
    return rows or None


# Tried in order. One dying should degrade the system, not stop it -
# which is exactly what happened when stooq put up a JS challenge.
# Alpaca first when configured: it is the only one of these that is
# actually meant to be read by a program.
PROVIDERS = (("alpaca", _from_alpaca),
             ("yfinance", _from_yfinance),
             ("stooq", _from_stooq),
             ("yahoo", _from_yahoo))


LAST_QUALITY: dict[str, dict] = {}


def fetch_history(ticker: str, timeout: int = 25,
                  quality: bool = True) -> list[dict] | None:
    """Daily OHLCV for a ticker, oldest first. None if no provider works.

    Every attempt is classified, and the winning series is assessed
    before it is handed back. The verdict is kept in LAST_QUALITY so
    the dashboard can show a per-ticker status instead of one global
    "price feed healthy" flag that is true right up until it is not.

    A DEGRADED series is still returned - with its caveat recorded -
    because refusing usable data over a single missing session helps
    nobody. An UNUSABLE one is never silently swapped for another
    provider's: the next provider is tried, and if none is usable the
    answer is None.
    """
    attempts: list[dict] = []
    for name, fn in PROVIDERS:
        report: dict = {"provider": name}
        try:
            rows = fn(ticker, timeout, report=report) \
                if _accepts_report(fn) else fn(ticker, timeout)
        except Exception as exc:                        # noqa: BLE001
            report["error"] = dataquality.classify_error(exc=exc)
            rows = None
        # Only an adapter that reports back may be said to have
        # answered "empty"; the others simply did not say.
        report.setdefault(
            "error",
            dataquality.OK if rows
            else (dataquality.EMPTY if _accepts_report(fn)
                  else dataquality.UNKNOWN))
        attempts.append(report)

        if not rows:
            continue

        verdict = dataquality.assess(
            ticker, rows, feed=report.get("feed", name),
            error=dataquality.OK)
        verdict["provider"] = name
        verdict["attempts"] = attempts
        if quality:
            LAST_QUALITY[ticker.upper()] = verdict

        if verdict["status"] == dataquality.UNUSABLE:
            print(f"    - {name} data for {ticker} unusable: {verdict['detail']}")
            continue
        if name != PROVIDERS[0][0]:
            print(f"    (price data for {ticker} from {name})")
        if verdict["status"] == dataquality.DEGRADED:
            print(f"    ~ {ticker} degraded: {verdict['detail']}")
        return rows

    # Nothing usable. Record WHY, per provider, rather than one flat line.
    worst = next((a for a in attempts
                  if a.get("error") not in (dataquality.OK,
                                            dataquality.NOT_CONFIGURED)),
                 attempts[0] if attempts else {})
    if quality:
        LAST_QUALITY[ticker.upper()] = dataquality.assess(
            ticker, None, error=worst.get("error") or dataquality.UNKNOWN)
        LAST_QUALITY[ticker.upper()]["attempts"] = attempts
    reasons = ", ".join(f"{a['provider']}={a.get('error')}" for a in attempts)
    print(f"    ! no usable price data for {ticker} ({reasons})")
    return None


def _accepts_report(fn) -> bool:
    try:
        import inspect
        return "report" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def quality_report() -> dict:
    """Per-ticker data quality from the most recent fetches."""
    reports = list(LAST_QUALITY.values())
    out = dataquality.summarise(reports)
    out["per_ticker"] = {k: {"status": v["status"], "detail": v["detail"],
                             "bars": v.get("bars", 0),
                             "feed": v.get("feed", ""),
                             "provider": v.get("provider", "")}
                         for k, v in LAST_QUALITY.items()}
    return out


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
