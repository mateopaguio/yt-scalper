"""Position sizing and risk checks. Arithmetic, not advice.

WHAT THIS IS NOT
It does not say whether a contract is worth buying. That needs an edge
this system has not demonstrated - the backtest showed one pattern
losing to buy-and-hold over 330 signals and the other resting on 22 -
and it needs chain data that is not configured. A tool that printed
"good buy" would be manufacturing confidence out of neither.

WHAT IT IS
Given an account size and a contract price you read off your broker,
it answers: how many can you buy inside a sane risk budget, what it
costs if it expires worthless, and how many losses in a row your
account survives. Sizing cannot make a bad trade good. It can stop a
good trade from being fatal, and it can show when an account is simply
too small for the instrument.

LONG OPTIONS CAN GO TO ZERO. The premium is the max loss and it is
reached often, not rarely. That is why risk here is the whole premium,
not a stop distance: there is no stop on an expiring contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTRACT_MULTIPLIER = 100          # US equity options: 1 contract = 100 shares

# Risk ceilings. Conventional, conservative, and not tuned to make any
# particular trade look acceptable.
MAX_RISK_PCT = 0.02                # at most 2% of the account on one idea
HARD_CAP_PCT = 0.05                # above this it is a gamble, not a position
MIN_SURVIVABLE_LOSSES = 10         # an account should survive a bad streak


@dataclass
class Account:
    equity: float
    max_risk_pct: float = MAX_RISK_PCT


def assess(account: Account, contract_price: float, contracts: int = 1,
           commission_per_contract: float = 0.0,
           bid: float | None = None, ask: float | None = None) -> dict[str, Any]:
    """Cost, risk and survivability for a proposed options position."""
    if contract_price <= 0:
        return {"error": "contract price must be positive"}

    per_contract = contract_price * CONTRACT_MULTIPLIER + commission_per_contract
    total = per_contract * max(contracts, 0)
    pct = (total / account.equity * 100) if account.equity > 0 else float("inf")

    budget = account.equity * account.max_risk_pct
    affordable = int(budget // per_contract) if per_contract > 0 else 0

    # A long option's max loss is the whole premium, so "losses survived"
    # is simply how many of these the account absorbs.
    survivable = int(account.equity // total) if total > 0 else 0

    spread_cost = None
    spread_pct = None
    if bid is not None and ask is not None and ask > 0:
        spread_cost = (ask - bid) * CONTRACT_MULTIPLIER * max(contracts, 0)
        mid = (ask + bid) / 2
        if mid > 0:
            # round trip: you cross the spread entering AND exiting
            spread_pct = (ask - bid) / mid * 100

    flags: list[str] = []
    if pct > HARD_CAP_PCT * 100:
        flags.append(
            f"{pct:.0f}% of the account on one contract set - above the "
            f"{HARD_CAP_PCT * 100:.0f}% line this stops being a position "
            "and becomes a bet")
    elif pct > account.max_risk_pct * 100:
        flags.append(f"{pct:.1f}% of the account, over your "
                     f"{account.max_risk_pct * 100:.0f}% risk budget")
    if survivable < MIN_SURVIVABLE_LOSSES:
        flags.append(
            f"only {survivable} total loss(es) before the account is gone; "
            f"{MIN_SURVIVABLE_LOSSES} consecutive losers is normal, not rare")
    if affordable < 1:
        flags.append(
            "one contract already exceeds the risk budget - the account is "
            "too small for this contract, which is a fact about size, not "
            "about the trade")
    if spread_pct is not None and spread_pct > 5:
        flags.append(
            f"{spread_pct:.0f}% round-trip spread - the position must move "
            f"{spread_pct:.0f}% just to break even")

    return {
        "contracts": contracts,
        "cost_per_contract": round(per_contract, 2),
        "total_cost": round(total, 2),
        "pct_of_account": round(pct, 2),
        "max_loss": round(total, 2),          # long options: premium is the lot
        "risk_budget": round(budget, 2),
        "contracts_within_budget": affordable,
        "total_losses_survivable": survivable,
        "spread_cost": round(spread_cost, 2) if spread_cost is not None else None,
        "spread_pct_round_trip": round(spread_pct, 1) if spread_pct is not None else None,
        "within_budget": pct <= account.max_risk_pct * 100 and affordable >= 1,
        "flags": flags,
        "note": ("Sizing only. This says nothing about whether the trade is "
                 "worth taking - no validated edge and no chain data. A long "
                 "option's max loss is the full premium and it happens often."),
    }


def breakeven(strike: float, premium: float, kind: str = "call") -> float:
    """Underlying price at expiry where the contract returns the premium."""
    return strike + premium if kind.lower() == "call" else strike - premium


def minimum_viable_account(contract_price: float,
                           max_risk_pct: float = MAX_RISK_PCT) -> float:
    """Smallest account where ONE contract fits the risk budget.

    Useful because the honest answer to many sizing questions is that the
    account is too small for the instrument, and no amount of sizing
    arithmetic fixes that.
    """
    return (contract_price * CONTRACT_MULTIPLIER) / max_risk_pct
