# Market data: what we need, what it costs, what to do first

No subscription purchased. No live brokerage execution. This is a
comparison and a recommended order of operations.

---

## What this system actually requires

Two different problems, with very different price tags.

**1. Daily equity OHLCV.** Feeds `patterns`, `paper`, `outcomes` and
every benchmark-relative number. Needs ~5+ years so a backtest has
enough bars, and split/dividend adjustment so returns are not wrong.
This is a solved, free problem.

**2. Contemporaneous option quotes.** This is the hard one, and our
own rules make it harder. `schemas.OPTION_QUOTE` requires `as_of` on
every quote, and the project rule is to mark a simulation
**unpriceable** rather than invent a fill. The stated goal was
*"evaluate execution costs instead of simply whether the stock moved
in the predicted direction"* — which means the **bid/ask spread at
signal time**, not a closing mark.

That single requirement rules out most cheap options data. Free and
entry tiers overwhelmingly ship end-of-day *prices* without quotes. A
last-trade price cannot tell you what you would have paid, and
inventing a spread would defeat the entire point of measuring cost.

---

## Equities — do this first, it is free

| Source | Cost | History | Notes |
|---|---|---|---|
| **Alpaca Basic** | $0 | ~10y daily + 1-minute, from 2016 | 200 req/min. **Already implemented** in `prices.py` (`_from_alpaca`); needs only `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY`. |
| Stooq | $0 | long daily | No key. Currently our fallback; unreliable and blocked from this sandbox. |
| yfinance | $0 | long | Unofficial scrape. Breaks without warning — it is the proximate cause of today's "no price data for SPY/QQQ". |
| Tiingo | $0 / $10 | 30+y EOD | Free tier is limited but documented and stable. |

**The current price-feed outage is a free problem with a free fix.**
Three providers are wired up and all three are failing: yfinance is
being refused, Stooq and Yahoo are unreachable. Alpaca is the only one
of the three that is a real API with a key rather than a scraped page,
and the code path already exists. Set the two environment variables
and Scalp Lab stops saying "could not scan".

Note `ALPACA_FEED` defaults to `iex` in our code. For free historical
daily bars `sip` is the better default — IEX is a single venue and
roughly 2% of volume, so its daily bars can differ from consolidated
ones. Worth changing when the key is added.

---

## Options — nothing here is urgent, and nothing here is free

Ordered by what it would cost us per month.

| Provider | Price | Quotes (bid/ask)? | History | Fit |
|---|---|---|---|---|
| **ThetaData Free** | $0 | **No** | EOD from 2023-06 (1-day delay) | Fails the core requirement. Useful only for sanity-checking contract existence. |
| **ThetaData Value** | ~$40 | 1-minute snapshots, ~4y | from ~2020-01 | **Best candidate.** Verify quote coverage before buying. |
| EODHD options add-on | ~$30 | EOD bid/ask + IV/Greeks | 2y | Cheapest with any bid/ask. EOD only, so no entry-time spread. |
| Massive (Polygon) Starter | $29 | No (15-min delayed) | 2y | Quotes only at Advanced. |
| Massive Advanced | $199 | Tick quotes | 5y+ | Correct data, well above need. |
| ThetaData Standard | ~$80 | Tick trade+quote | from 2016 | If 1-minute proves too coarse. |
| **Databento (OPRA)** | $125 free credit, then per-GB | Tick top-of-book | from 2013 | **Pay-per-use, not a subscription.** Best way to buy a *bounded* historical pull for one backtest. |
| ORATS | $199+ | EOD/1-min with Greeks | 2007 | Research-grade, priced for it. |
| OptionMetrics / IVolatility | $159–quote | Yes, deep | 1996 / 2005 | Academic depth. Not our problem. |

### The honest recommendation

**Do not buy an options subscription yet.** Two reasons, both from our
own measurements:

1. `pullback_in_uptrend` is **below buy-and-hold** across 330 signals,
   and `range_breakout` has n=22 and fails Bonferroni correction
   (threshold 0.0031, best observed p=0.046). Paying for option data to
   price signals that have not yet shown an edge on the underlying
   spends money to measure something we already suspect.
2. **Zero signals have resolved at any horizon**, because the price
   feed is down. We cannot evaluate a monthly subscription while the
   free equities feed is broken.

The sequence that costs nothing until it has to:

1. Add Alpaca keys → equities feed works → signals start resolving.
2. Run 4–8 weeks. Let `outcomes` accumulate resolved signals and let
   the creator leaderboard reach its 10-call minimum.
3. If, and only if, a strategy shows an edge on the underlying, spend
   the **Databento $125 credit** on a bounded historical pull for that
   one strategy's signal dates. No subscription, no recurring cost.
4. If that pull shows the edge survives realistic spreads, then a
   ThetaData Value subscription (~$40) is justified by a measured
   result rather than a hope.

### What we must never do

Price an option from a last trade, a mid-point we invented, or a
model, and present the result as a fill. `paper.py` and
`schemas.check_option_quote()` already refuse this. Any provider we
adopt must supply `as_of` with every quote, or the simulation stays
marked unpriceable — which is the correct answer, not a failure.

---

## Sources

- [Options Data API (2026): 7 Providers Compared](https://www.quantvps.com/blog/options-data-api)
- [Historical Options Data (2026): 7 Free and Paid Sources](https://www.quantvps.com/blog/download-historical-options-data)
- [ThetaData subscription tiers](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html)
- [Options Data Pricing Compared](https://flashalpha.com/articles/options-data-pricing-comparison-flashalpha-thetadata-polygon-spotgamma-squeezemetrics)
- [ORB backtest using Alpaca free data](https://concretumgroup.com/orb-strategy-backtest-in-python-using-alpaca-10-years-of-free-data/)

Prices were checked on 2026-10-28 and several sources are vendor
marketing. Verify on the vendor's own pricing page before paying.
