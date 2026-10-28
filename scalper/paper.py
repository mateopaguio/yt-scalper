"""Paper trading simulation. No broker, no orders, no money.

Takes the setups from patterns.py and runs them as if they had been
traded, so the question "would this have made money after costs" has an
answer instead of a guess.

Everything here is deliberately pessimistic. A backtest that flatters
itself is worse than none, because it produces confidence rather than
information.

HOW FILLS ARE MODELLED
  Entry   the close of the session AFTER the signal bar. The signal
          bar's own close is not knowable in time to act on it, which
          is the same rule patterns.backtest() and outcomes use.
  Stop    the pattern's own invalidation level. If the bar's low pierces
          it the trade is closed - but if the bar OPENS below the stop,
          the fill is the open, not the stop. Gaps do not politely fill
          you at your price, and assuming they do is the single most
          common way a paper result beats a real one.
  Exit    stop, target, or a time limit, whichever comes first. An open
          position at the end of the data is marked to the last close
          and flagged, never silently dropped or counted as a win.

COSTS
  Charged on BOTH sides, entry and exit. Defaults are for liquid US
  equities (SPY/QQQ): commission-free, a penny of spread, a penny of
  slippage. Those are small because the underlying is liquid. OPTIONS
  ARE NOT MODELLED - no chain data is configured, and an invented
  spread would defeat the purpose. Options costs on the same strategy
  would be an order of magnitude higher.

SIZING
  Risk-based: each position risks a fixed fraction of current equity,
  sized by the distance from entry to stop. A wider stop buys fewer
  shares. Equity compounds, so a losing streak shrinks position size
  automatically. Positions are capped as a share of equity so a very
  tight stop cannot produce an absurd position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Costs:
    """Per-side trading costs. Charged on entry AND exit."""
    commission: float = 0.0        # most US brokers, equities
    spread_per_share: float = 0.01  # half-spread paid crossing
    slippage_per_share: float = 0.01

    def per_share(self) -> float:
        return self.spread_per_share + self.slippage_per_share


@dataclass
class Rules:
    starting_equity: float = 10_000.0
    risk_per_trade: float = 0.01     # 1% of equity at risk per position
    max_position_pct: float = 0.25   # never more than 25% of equity in one name
    max_hold_days: int = 20          # time stop
    target_r: float = 2.0            # take profit at 2x the risked distance
    allow_concurrent: bool = False   # one position at a time, per ticker


@dataclass
class Trade:
    ticker: str
    pattern: str
    signal_date: Any
    entry_date: Any
    entry_price: float
    stop: float
    target: float
    shares: int
    exit_date: Any = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    still_open: bool = False
    gapped_through_stop: bool = False

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("signal_date", "entry_date", "exit_date"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d


def _size(equity: float, entry: float, stop: float, rules: Rules) -> int:
    """Shares to buy so that being stopped out costs `risk_per_trade`."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return 0
    by_risk = (equity * rules.risk_per_trade) / risk_per_share
    by_cap = (equity * rules.max_position_pct) / entry
    return max(0, int(min(by_risk, by_cap)))


def simulate(rows: list[dict], signals: list[dict], rules: Rules | None = None,
             costs: Costs | None = None) -> dict[str, Any]:
    """Run signals through the bars and report what would have happened.

    `signals` are dicts with signal_date, invalidation and pattern, as
    produced by patterns.scan/backtest. Only bars at or after a signal
    are ever consulted.
    """
    rules = rules or Rules()
    costs = costs or Costs()

    # Fills must come from AS-TRADED prices.
    #
    # Position size is equity/price and spread and slippage are charged
    # per share, so an adjusted series silently rescales both. A stock
    # that changed hands at $200 before a 2-for-1 split is carried in
    # an adjusted series at $100: twice the shares, twice the modelled
    # cost, at a price the tape never printed.
    #
    # Refused rather than approximated. An unpriceable simulation is a
    # correct answer; a plausible one computed from the wrong series is
    # not.
    series = {r.get("series") for r in rows if r.get("series")}
    if series and series != {"as_traded"}:
        return {
            "unpriceable": True,
            "reason": (f"bars are {'/'.join(sorted(series))}, not as-traded; "
                       "fills cannot be simulated from a price that was "
                       "never quoted"),
            "trades": 0, "closed": 0,
            "caveat": ("Returns may still be measured on an adjusted "
                       "series - that is what adjustment is for. Only "
                       "execution needs the as-traded tape."),
        }

    by_date = {r["date"]: i for i, r in enumerate(rows)}
    equity = rules.starting_equity
    trades: list[Trade] = []
    curve = [{"date": rows[0]["date"] if rows else None, "equity": equity}]
    open_until = -1          # index the current position closes on

    for sig in sorted(signals, key=lambda s: str(s.get("signal_date"))):
        sd = sig.get("signal_date")
        i = by_date.get(sd)
        if i is None or i + 1 >= len(rows):
            continue                      # no session after the signal yet
        if not rules.allow_concurrent and i + 1 <= open_until:
            continue                      # already in a position

        entry_bar = rows[i + 1]
        entry = float(entry_bar["close"])
        stop = float(sig.get("invalidation") or 0)
        if stop <= 0 or stop >= entry:
            continue                      # no usable risk definition

        shares = _size(equity, entry, stop, rules)
        if shares <= 0:
            continue

        risk_per_share = entry - stop
        target = entry + rules.target_r * risk_per_share

        t = Trade(ticker=sig.get("ticker", ""), pattern=sig.get("pattern", ""),
                  signal_date=sd, entry_date=entry_bar["date"],
                  entry_price=entry, stop=stop, target=target, shares=shares)

        exit_price = exit_date = None
        reason = ""
        last = min(i + 1 + rules.max_hold_days, len(rows) - 1)

        for j in range(i + 2, last + 1):
            bar = rows[j]
            o = float(bar.get("open", bar["close"]))
            lo = float(bar.get("low", bar["close"]))
            hi = float(bar.get("high", bar["close"]))

            # Gap check first: a bar that opens below the stop fills at the
            # open, not the stop. Assuming otherwise is how paper results
            # quietly beat real ones.
            if o <= stop:
                exit_price, exit_date, reason = o, bar["date"], "gap_through_stop"
                t.gapped_through_stop = True
                break
            if lo <= stop:
                exit_price, exit_date, reason = stop, bar["date"], "stop"
                break
            if hi >= target:
                exit_price, exit_date, reason = target, bar["date"], "target"
                break
        else:
            bar = rows[last]
            exit_price, exit_date, reason = float(bar["close"]), bar["date"], "time_stop"
            if last >= len(rows) - 1:
                t.still_open = True
                reason = "open_at_end_of_data"

        t.exit_price, t.exit_date, t.exit_reason = exit_price, exit_date, reason
        t.bars_held = by_date.get(exit_date, last) - (i + 1)
        t.gross_pnl = (exit_price - entry) * shares
        t.costs = shares * costs.per_share() * 2 + costs.commission * 2
        t.net_pnl = t.gross_pnl - t.costs
        t.r_multiple = (t.net_pnl / (risk_per_share * shares)
                        if risk_per_share * shares else 0.0)

        equity += t.net_pnl
        trades.append(t)
        curve.append({"date": exit_date, "equity": round(equity, 2)})
        open_until = by_date.get(exit_date, last)

    return _report(trades, curve, rules, costs)


def _report(trades: list[Trade], curve: list[dict], rules: Rules,
            costs: Costs) -> dict[str, Any]:
    closed = [t for t in trades if not t.still_open]
    wins = [t for t in closed if t.net_pnl > 0]
    losses = [t for t in closed if t.net_pnl <= 0]
    net = sum(t.net_pnl for t in closed)
    gross = sum(t.gross_pnl for t in closed)
    paid = sum(t.costs for t in closed)

    peak = rules.starting_equity
    max_dd = 0.0
    for pt in curve:
        peak = max(peak, pt["equity"])
        if peak > 0:
            max_dd = min(max_dd, (pt["equity"] / peak - 1) * 100)

    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))

    return {
        "trades": len(trades),
        "closed": len(closed),
        "still_open": sum(1 for t in trades if t.still_open),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "starting_equity": rules.starting_equity,
        "ending_equity": round(rules.starting_equity + net, 2),
        "net_pnl": round(net, 2),
        "return_pct": round(100 * net / rules.starting_equity, 2),
        "gross_pnl": round(gross, 2),
        "costs_paid": round(paid, 2),
        # What the costs actually ate. If this is most of the gross, the
        # strategy is a transfer to the broker, however good the win rate.
        "costs_as_pct_of_gross": (round(100 * paid / abs(gross), 1)
                                  if gross else None),
        "avg_win": round(sum(t.net_pnl for t in wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(t.net_pnl for t in losses) / len(losses), 2) if losses else None,
        "expectancy_per_trade": round(net / len(closed), 2) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_r": round(sum(t.r_multiple for t in closed) / len(closed), 3) if closed else None,
        "max_drawdown_pct": round(max_dd, 2),
        "gapped_through_stop": sum(1 for t in closed if t.gapped_through_stop),
        "exit_reasons": {r: sum(1 for t in closed if t.exit_reason == r)
                         for r in {t.exit_reason for t in closed}},
        "rules": rules.__dict__,
        "costs": costs.__dict__,
        "equity_curve": curve,
        "trade_log": [t.as_dict() for t in trades],
        "caveat": ("Paper only. No order was placed and no money moved. "
                   "Costs are modelled for liquid US equities; options on "
                   "the same signals would cost far more and are not "
                   "simulated, because no chain data is configured."),
    }
