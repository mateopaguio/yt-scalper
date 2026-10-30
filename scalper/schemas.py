"""Shapes the pipeline passes around, and checks at the boundaries.

The directive asks for schemas before UI. This is not documentation for
its own sake - every rule here exists because its absence has already
cost something in this project:

  PriceBar   fetch_history() returned date+close only. patterns.py needs
             high, low and volume, so the scanner could never have
             fired, and close-only rows crashed it with KeyError: 'low'.
             A boundary check turns that into a clear message.
  Mention    the model emitted SPCE for SpaceX - a real, tradeable
             symbol for a private company. Symbols must be checked, not
             trusted.
  Catalyst   39 of 45 were summarised from a headline because the body
             fetch silently returned "". Confidence must be explicit.
  SignalEvent  signals recorded without an engine tag fell out of
             per-engine comparisons as None.
  SimTrade   an unresolved trade must never be scored as a win.

validate() REPORTS rather than raises. A malformed row should degrade
one ticker, not kill a scheduled run - that failure mode has already
taken this pipeline down twice.
"""
from __future__ import annotations

from typing import Any

# --- field contracts: (name, type, required) ---------------------------

PRICE_BAR = {
    "date": (object, True),          # datetime.date
    "close": ((int, float), True),
    "open": ((int, float), False),
    "high": ((int, float), False),
    "low": ((int, float), False),
    "volume": ((int, float), False),
    "feed": (str, False),            # which provider; IEX != full market
}

# patterns.py cannot run without these
PRICE_BAR_OHLC_REQUIRED = ("open", "high", "low")

MENTION = {
    "ticker": (str, True),           # "" is valid: rejected symbols keep the thesis
    "company": (str, False),
    "stance": (str, False),          # bullish | bearish | neutral | mixed | unclear
    "evidence": (str, False),        # data | reasoned | speculation | none
    "thesis": (str, False),
    "catalyst": (str, False),
    "channel": (str, False),
    "basis": (str, False),           # derivative | primary | original
    "asset_class": (str, False),     # equity | crypto
}

CATALYST = {
    "event_id": (str, True),
    "title": (str, True),
    "url": (str, False),
    "published": (str, False),
    "source": (str, False),
    "findings": (dict, False),
}

SIGNAL_EVENT = {
    "id": (str, True),
    "engine": (str, True),           # never None - see module docstring
    "ticker": (str, True),
    "generated_at": (str, True),
    "status": (str, True),
    "entry_price": (object, False),  # None until a feed resolves it
    "outcome": (object, False),
}

OPTION_QUOTE = {
    "symbol": (str, True),
    "strike": ((int, float), True),
    "expiry": (str, True),
    "kind": (str, True),             # call | put
    "bid": ((int, float), False),
    "ask": ((int, float), False),
    "as_of": (str, True),            # REQUIRED: a quote without a time is
                                     # unpriceable, never silently current
    "feed": (str, False),
}

SIM_TRADE = {
    "ticker": (str, True),
    "entry_date": (object, True),
    "entry_price": ((int, float), True),
    "stop": ((int, float), True),
    "shares": (int, True),
    "still_open": (bool, False),
    "net_pnl": ((int, float), False),
}

VALID_STANCE = {"bullish", "bearish", "neutral", "mixed", "unclear", ""}
VALID_EVIDENCE = {"data", "reasoned", "speculation", "none", ""}
VALID_BASIS = {"derivative", "primary", "original", ""}


def validate(record: dict, schema: dict, label: str = "") -> list[str]:
    """Return a list of problems. Never raises: one bad row must not kill
    a run, which is a failure mode this pipeline has already suffered."""
    problems = []
    for field, (typ, required) in schema.items():
        if field not in record or record[field] is None:
            if required:
                problems.append(f"{label}missing required field '{field}'")
            continue
        if typ is not object and not isinstance(record[field], typ):
            problems.append(
                f"{label}'{field}' is {type(record[field]).__name__}, "
                f"expected {getattr(typ, '__name__', typ)}")
    return problems


def check_price_bars(rows: list[dict], need_ohlc: bool = False) -> dict[str, Any]:
    """Is this series usable, and for what?

    Separates "no data" from "data that cannot drive the scanner", which
    looked identical when the scanner silently found nothing.
    """
    if not rows:
        return {"usable": False, "reason": "no rows", "bars": 0,
                "has_ohlc": False, "problems": []}

    problems: list[str] = []
    for i, r in enumerate(rows[:5]):
        problems += validate(r, PRICE_BAR, f"bar[{i}] ")

    last = rows[-1]
    missing = [f for f in PRICE_BAR_OHLC_REQUIRED if last.get(f) is None]
    has_ohlc = not missing
    usable = not problems and (has_ohlc or not need_ohlc)

    return {
        "usable": usable,
        "bars": len(rows),
        "has_ohlc": has_ohlc,
        "missing_ohlc": missing,
        "feed": last.get("feed"),
        "reason": ("" if usable else
                   f"close-only bars (missing {', '.join(missing)}) - "
                   "pattern detection needs highs and lows"
                   if missing and need_ohlc else "; ".join(problems)),
        "problems": problems,
    }


def check_option_quote(q: dict, max_age_seconds: int | None = None,
                       now_iso: str | None = None) -> dict[str, Any]:
    """An option quote with no timestamp, or a stale one, is UNPRICEABLE.

    The directive is explicit: never fabricate fills or treat delayed
    data as live. An absent as_of is not 'probably now'.
    """
    problems = validate(q, OPTION_QUOTE, "quote ")
    bid, ask = q.get("bid"), q.get("ask")
    if bid is None or ask is None:
        problems.append("missing bid or ask - cannot price a fill")
    elif ask < bid:
        problems.append("crossed quote (ask below bid)")

    stale = False
    if max_age_seconds is not None and q.get("as_of") and now_iso:
        from datetime import datetime
        try:
            t = datetime.fromisoformat(str(q["as_of"]).replace("Z", "+00:00"))
            n = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            stale = (n - t).total_seconds() > max_age_seconds
        except ValueError:
            problems.append("unreadable as_of timestamp")

    return {
        "priceable": not problems and not stale,
        "stale": stale,
        "problems": problems,
        "reason": ("stale quote" if stale else "; ".join(problems)),
    }


def audit(records: list[dict], schema: dict, label: str) -> dict[str, Any]:
    """Validate a collection and summarise, for a data-quality panel."""
    bad = []
    for i, r in enumerate(records):
        p = validate(r, schema, "")
        if p:
            bad.append({"index": i, "problems": p,
                        "id": r.get("id") or r.get("ticker") or r.get("event_id")})
    return {
        "label": label,
        "total": len(records),
        "valid": len(records) - len(bad),
        "invalid": len(bad),
        "examples": bad[:5],
    }
