# Trading foundation for this research sprint

Read this before choosing a signal, interpreting a backtest, or writing a plug-in. It is a compact common vocabulary for the team—not trading advice, not an order tutorial, and not permission to use a broker account. All research remains offline, credential-free, and non-authorizing.

## 1. The one-sentence model

A strategy uses completed **underlying ETF data** to decide whether there is a directional opportunity; a separate, centrally controlled **option expression** turns that view into a defined-risk paper trade only after data, quote, risk, and execution gates pass.

The researcher owns the first part: a deterministic `BUY`, `SELL`, or `NO_TRADE` signal with an auditable score and reason codes. The platform, not the researcher, chooses an exact contract, checks current quotes, sizes it, manages exits, and may still return `NO_TRADE`.

```text
completed ETF bars -> frozen features -> strategy signal -> central option selector
                                             |                    |
                                          NO_TRADE           quote/risk/execution gates
                                                                  |
                                                     paper order or NO_TRADE
```

## 2. The instruments in plain English

| Term | Meaning in this project | Why it matters |
|---|---|---|
| Underlying | The ETF whose bars create the signal: SPY, QQQ, TQQQ, SMH, SOXL, or IGV. | This is the alpha/data research surface. |
| ETF share | One unit of the underlying ETF. | Share returns are a diagnostic proxy, not an option fill. |
| Option contract | A time-limited right or obligation on the underlying, usually covering 100 shares. | Premiums, P&L, and risk are materially different from ETF shares. |
| Call | Gives its holder the right to buy at the strike. A long call generally benefits from an upward move. | Used only when the central expression policy permits it. |
| Put | Gives its holder the right to sell at the strike. A long put generally benefits from a downward move. | Used only when the central expression policy permits it. |
| Strike | The contract's fixed exercise price. | It must be compared with the correctly adjusted underlying price. |
| Expiration / DTE | The date when the option expires / days until that date. | Time decay and liquidity change sharply as expiration approaches. |
| Premium | Option price, quoted per share; one standard contract normally represents 100 shares. | A $1.20 premium is roughly $120 before fees for one contract. |
| Bid / ask | The current prices buyers offer and sellers request. | Buy near ask and sell near bid in conservative research; midpoint is not a fill. |
| Spread | Difference between ask and bid, or a multi-leg option structure. | A wide quote spread can remove a seemingly good signal's expected edge. |

An option's displayed price is not a stock price. A 5% move in the ETF does **not** imply a 5% move in the option: delta, implied volatility, time remaining, and bid/ask friction also matter.

## 3. The limited option vocabulary required here

### Long call and long put

For a long call held to expiration, the approximate intrinsic payoff is

```text
100 × max(spot_at_expiry - strike, 0) - premium_paid × 100
```

For a long put, replace `spot_at_expiry - strike` with `strike - spot_at_expiry`. The maximum loss for either long option is the premium paid, but the entire premium can be lost.

### Defined-risk vertical spreads — the default research expression

A **bull call spread** buys a lower-strike call and sells a higher-strike call with the same expiration. A **bear put spread** buys a higher-strike put and sells a lower-strike put with the same expiration. Both are debit spreads with bounded loss and bounded gain.

For a debit vertical with width `W` and quoted entry debit `D`, per-contract economics are approximately:

```text
maximum_loss = 100 × D + fees
maximum_gain = 100 × (W - D) - fees
```

The two legs must be priced in the same, sufficiently fresh observation interval. Do not subtract independent midpoints from different times and call the result tradable.

### Combining stock and options

There are two legitimate meanings of “stock plus options,” and researchers should name which one they mean:

1. **Underlying-led option expression (the V1 design):** ETF bars create a long or short directional signal; a defined-risk option spread expresses it. The ETF is the information source, not a share position.
2. **Share position with an option hedge:** for example, 100 ETF shares plus a protective put, or 100 shares plus a put and a short call (a collar). This creates a one-contract-to-100-share hedge ratio and consumes substantially more capital. It is a separate strategy/economic candidate, not a cosmetic add-on to a signal.

For this sprint, do not invent a share hedge inside a plug-in. State the intended option expression in the strategy card and let the central selector/risk boundary decide whether an approved, quoteable expression exists.

## 4. Greeks and implied volatility: enough to avoid common mistakes

- **Delta:** approximate first-order option-price change for a one-dollar underlying move. It changes as price and time change.
- **Gamma:** how quickly delta changes. It can make near-expiry P&L very nonlinear.
- **Theta:** time decay, all else equal. Long options usually lose value as time passes.
- **Vega:** sensitivity to implied volatility (IV). An option can lose value even after a correct directional move if IV falls enough.
- **Implied volatility:** the volatility input consistent with the market option price; it is not a forecast guaranteed to be realized.

The free data surface may not provide reliable historical quote, IV, Greek, or open-interest coverage. Therefore, no research family may use them as historical alpha features unless the shared entitlement and point-in-time coverage gates explicitly pass. Current indicative quotes are a runtime accept/reject gate—not a source for rewriting historical results.

## 5. Price data and time: the rules that prevent fictional backtests

Each bar has open, high, low, close, volume, and a timestamp. A strategy can use a value only after that bar is complete and available. In this project, a 15-minute decision interval is labelled by its **end** and becomes available one second later.

These four rules are non-negotiable:

1. Never enter using the close of the same bar that supplied the signal. The baseline proxy enters at the next eligible one-minute open.
2. Never use a future bar, a revised corporate-action series, a late quote, or an option contract discovered only after the decision time.
3. Never forward-fill a missing option quote/bar or use zero as its price.
4. Never compare split-adjusted ETF returns with raw option strikes. Use the right series for each purpose and retain the raw/adjusted audit.

## 6. How a research result becomes useful

A profitable chart alone is not a strategy. A useful candidate has all of the following:

- a single economic hypothesis written before outcomes;
- a pure, deterministic signal function that returns a result or `NO_TRADE`;
- identical feature calculation in offline research and plug-in evaluation;
- fixed decisions, costs, exit policy, and sensitivity budget;
- point-in-time timestamps, missing-data behavior, and reason codes;
- returns, drawdown, turnover, trade count, and cost-stress metrics—not just headline P&L;
- immutable raw-data references, configs, manifests, tests, and reproduction command.

Short competition-window results are useful **demonstration evidence**; they do not establish a durable Sharpe ratio. A candidate can be technically correct and still be rejected if its data, option proxy, costs, or reproducibility are weak.

## 7. A small worked example

At 10:30:01 ET, a completed QQQ bar is above session VWAP and the frozen continuation score exceeds the entry threshold. The signal package returns:

```text
direction=LONG
entry_score=1.32
reason_code=MOMENTUM_AND_VWAP_CONFIRMED
```

It does **not** choose a strike, expiration, quantity, or order price. The central selector may later find that the approved bull-call-spread legs have stale or wide indicative quotes. The correct final result is then `NO_TRADE`, even though the underlying signal was valid.

That is not a bug. It is the design: alpha, contract economics, and execution feasibility are distinct questions.

## 8. Before you start your assigned packet

1. Read this page, the [quant trading basics](quant_trading_basic.md), your assigned packet, and the [strategy API](../architecture/STRATEGY_API.md).
2. Use the supplied immutable data manifest; do not request credentials or fetch a private alternative dataset.
3. Freeze the strategy card, feature contract, costs, sensitivity grid, and falsification criteria before reading outcome P&L.
4. Return one separately versioned package per strategy, whether it is rejected or promising.

For current endpoint semantics, pagination, and access errors, use Alpaca's primary documentation for [stock historical bars](https://docs.alpaca.markets/us/reference/stockbars), [option historical bars](https://docs.alpaca.markets/us/reference/optionbars), and the [options trading overview](https://docs.alpaca.markets/us/docs/options-trading-overview). Those sources describe API capability; the project research protocol remains the binding scope and safety contract.
