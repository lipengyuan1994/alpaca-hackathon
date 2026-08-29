# Strategy research plan

Status: normative research protocol, v1 draft

Universe: SPY, QQQ, TQQQ, SMH, SOXL, IGV and their listed options

Data boundary: Alpaca Basic/free-tier API or individually allowlisted read-only Alpaca MCP tools only

## 1. Research objective

Answer three questions separately:

1. Does a timestamp-safe underlying signal show repeatable forward-return evidence?
2. Can that frozen signal be expressed through the symbol's options using only free Alpaca data without optimistic marking?
3. Is the resulting strategy operationally safe and sufficiently supported for paper deployment?

These are not interchangeable:

- Underlying evidence does not prove option profitability.
- Historical option-bar/trade results are implementation proxies, not executable OPRA backtests.
- Four competition sessions are descriptive telemetry, not proof of alpha or a durable Sharpe ratio.
- A profitable competition path cannot promote a research candidate that failed its preregistered gates.

## 2. Hard data and entitlement boundary

Historical and current research data may come only from:

- Alpaca Market Data API or Trading API market-data/contract endpoints;
- official Alpaca MCP tools individually allowlisted for read-only market data;
- the official Alpaca SDK when it calls those same endpoints.

Forbidden substitutes include Yahoo, Polygon, Databento, Cboe/OPRA downloads, FRED, other vendors, external option chains, hand-copied prices, and externally sourced news. Alpaca News may be studied later only if its free entitlement and `available_time` semantics are verified and the hypothesis is separately preregistered; it is not in the common V1 scan.

Current free-tier assumptions to verify with a recorded entitlement probe:

| Asset | Explicit source | Documented coverage/limit | Research consequence |
|---|---|---|---|
| Underlyings | `feed=iex` | Historical stocks/ETFs since 2016; 200 historical calls/minute; 30 streamed equity symbols; recent 15 minutes restricted for Basic | IEX is a single-exchange sample, not SIP. Every price, volume, and VWAP claim says IEX. |
| Current options | `feed=indicative` on endpoints that accept a feed parameter | 200 calls/minute; 200 streamed option quotes; recent 15 minutes restricted | Indicative quotes are not OPRA and indicative trades are delayed. Never call them NBBO/OPRA/executable history. |
| Historical option observations | Alpaca option bars and trades; endpoint has no feed query parameter | Coverage begins February 2024; API/official SDK exposes historical bars/trades, while current SDK exposes latest quotes/snapshots rather than a historical option-quote series | Record `requested_feed=N/A_ENDPOINT_HAS_NO_FEED_PARAM` plus entitlement-probe evidence. Never invent a `feed` query; results remain explicitly labeled bar/trade proxies, never reconstructed bid/ask. |
| Current option chain | Indicative latest chain/snapshots | Latest trade/quote and sometimes IV/Greeks; 0DTE Greeks may be unavailable | Current fields gate prospective feasibility only; never inject them into historical rows. |
| Contract metadata | Alpaca option-contract endpoint | Status, expiration, strike, multiplier, deliverables, and dated open interest where returned | Use only fields demonstrably available by the decision timestamp. |

Every request that accepts a feed parameter specifies it explicitly and consumes all pagination tokens. For historical option bars/trades, record the sentinel `requested_feed=N/A_ENDPOINT_HAS_NO_FEED_PARAM`, endpoint schema hash, and observed entitlement instead. An invalid invented parameter, undocumented fallback, entitlement upgrade, mixed feed, or missing page is a failed data gate.

One designated data steward performs and caches shared downloads. Credentials remain with the collector; the six researchers consume immutable hashed artifacts. No researcher retrieves the same range independently or places any order through a research task.

## 3. Six-member initial assignment

All owners run the same code, hypotheses, thresholds, cost models, metrics, and artifact schemas. A member owns a symbol report—not a private backtester or personalized winning parameter.

| Member | Symbol | Special issue | Cross-review partner |
|---|---|---|---|
| Person 1 | SPY | Broad-market and options-liquidity control | IGV owner |
| Person 2 | QQQ | Technology benchmark and TQQQ comparison anchor | TQQQ owner |
| Person 3 | TQQQ | Leveraged-ETF path dependence, splits, gaps, and option affordability | QQQ owner |
| Person 4 | SMH | Semiconductor concentration and option coverage | SOXL owner |
| Person 5 | SOXL | Leveraged semiconductor tail/path risk and SMH comparison | SMH owner |
| Person 6 | IGV | Software concentration, chain depth, adjusted/nonstandard contracts | SPY owner |

QQQ/TQQQ and SMH/SOXL are evidence clusters, not independent confirmations. Pooled inference clusters by session/date and does not count leveraged/unleveraged variants as separate discoveries.

Round 0 produces six comparable feasibility cards. Before any candidate P&L is exposed, the data steward freezes the advancing subset from blinded entitlement, coverage, timestamp integrity, and prospective-liquidity fields only. If the team views returns before that freeze, all six symbols remain in the selection/multiple-testing family even if some are later operationally excluded. Only the top **feasible** subset—not the best in-sample P&L subset—advances:

- at most three symbols enter full walk-forward option research;
- at most two symbols enter the competition deployment allowlist;
- one plug-in becomes champion and at most one genuinely independent plug-in becomes fallback.

## 4. Standard one-symbol feasibility scan

Each owner completes A–F and records evidence paths, not just prose.

### A. Entitlement and surface probe

Record:

- API/MCP and SDK versions;
- exact endpoint/tool, explicit feed, and scrubbed query;
- earliest/latest accessible timestamps and recent-window behavior;
- rate-limit headers and observed 429 behavior;
- whether inactive/expired contracts can be enumerated;
- availability of option bars, historical trades, latest quotes, snapshots, IV, and Greeks;
- pagination completion and response schema hashes.

If the deployed surface contradicts this document, stop, preserve the probe, and update the common plan before symbol-specific work.

### B. Underlying data quality

Fetch both series when supported:

- `adjustment=raw` for point-in-time spot/strike matching;
- `adjustment=split` for continuous return features.

Validate:

- expected regular-session 15-minute intervals using Alpaca's calendar;
- monotonic, unique UTC timestamps;
- `low <= open/close <= high`, positive prices, nonnegative volume/trade count;
- missing/zero-volume bars, early closes, and session boundaries;
- split discontinuities between raw and adjusted series;
- extreme-return dates and corporate-action explanation;
- IEX volume/trade-count sparsity.

Initial green gate:

- at least 99% of expected full-session 15-minute bars;
- zero duplicate timestamps;
- all OHLC invariants pass;
- every material raw/adjusted discontinuity is explained.

Never combine a split-adjusted spot price with a raw option strike.

### C. Point-in-time contract-existence proxy

For each historical signal time:

1. Enumerate active and inactive contracts for the underlying and relevant expiration range with `show_deliverables=true` where the endpoint exposes it; consume every page and hash the scrubbed query/schema.
2. Apply one frozen standard-contract predicate: exact underlying; unadjusted OCC/root symbol matching the underlying; American style; multiplier 100; call/put type; expiration after the decision time; and no explicit cash, extra-security, non-100-share, or other adjusted deliverable. Reject any adjusted root or record whose deliverable/multiplier cannot be classified rather than guessing.
3. Require at least one Alpaca option observation at or before the decision time as evidence the contract existed by then.
4. Never use current status, current close, current Greeks, or current open interest as a historical feature.
5. Use open interest only when `open_interest_date <= decision_time`.
6. Store the exact contract record and its raw hash.

This procedure establishes only a `PIT_EXISTENCE_PROXY`. It does not reconstruct the chain that a trader saw, prove quote availability, or prove historical tradability. If existence cannot be established without guessing a symbol/listing date, set `PIT_EXISTENCE_PROXY_UNAVAILABLE`. Underlying-signal research may continue, but option evidence cannot be promoted. Historical proxy rows never authorize a runtime trade; only prospective schema-limited chain and quote snapshots can establish current tradability.

### D. Historical option coverage

For every candidate signal, measure:

- discoverable qualifying standard contract;
- first eligible entry observation for every required leg;
- exit observation for every required leg;
- simultaneous entry/exit interval coverage for both vertical legs;
- delay from requested execution to first eligible observation;
- signals lost to missing observations;
- zero, duplicate, or malformed records;
- observation density by DTE/moneyness.

The historical proxy uses one-minute option bars. Entry must be found within five minutes after the simulated order time, and all legs of a vertical must use the same one-minute interval. Exit uses the first common one-minute interval at or after the intended exit, also within five minutes. Store requested time, bar start/end, and each leg's source event timestamps. If a complete common interval is unavailable, record `NO_PROXY_FILL`; never pair legs from different minutes in the base proxy.

Never forward-fill an option price. A missing bar/trade is neither zero return nor evidence of executability.

| Status | Single-leg entry/exit coverage | Two-leg simultaneous coverage |
|---|---:|---:|
| Green | at least 90% | at least 80% |
| Yellow | 70%–90% | 50%–80% |
| Red | below 70% | below 50% |

A vertical cannot pass on single-leg coverage alone. If the historical API surface lacks quotes, quote-derived IV rank, skew, spread, size, and historical Greek strategies are automatically out of scope.

### E. Prospective quote-quality gate

Weekend research remains provisional until Monday. At predeclared windows near actual decision times—initially 10:30, 12:30, and 14:30 ET—capture the exact free indicative subset the selector would consider, with three successive snapshots five seconds apart per window. These samples are an operational readiness check, not a statistically meaningful liquidity study; runtime repeats the gate at every decision.

Initial quote pass:

- positive two-sided bid/ask;
- ask at least bid;
- quote age no greater than polling interval plus five seconds, initially capped at 15 seconds;
- midpoint at least $0.50;
- absolute width at most $0.25;
- relative width at most 20% of midpoint;
- for a spread, both legs pass and timestamps are within two seconds;
- executable entry debit `long ask - short bid` is positive and below spread width;
- immediate liquidation credit `max(0, long bid - short ask)` is recorded;
- entry debit is between 5% and 90% of spread width;
- fee-inclusive `entry_cash_debit = entry_debit × 100 + opening_fees` and `liquidation_cash_credit = max(0, liquidation_credit × 100 - exit_fees)`;
- immediate round-trip friction `(entry_cash_debit - liquidation_cash_credit) / entry_cash_debit` is no more than 30%.

The aggregate green gate requires at least eight of the exact nine frozen snapshots to pass, with no decision-time window having zero passing snapshots.

Preferred diagnostics:

- median relative width no more than 10%;
- 90th percentile no more than 20%;
- stale/crossed/missing rate below 1%.

Indicative sizes and volumes are recorded but never treated as true displayed liquidity or capacity.

### F. Feasibility card

Return exactly one status:

- `GREEN_OPTION_RESEARCH`: underlying, contract-existence proxy, historical option-proxy coverage, and current quote gates pass.
- `YELLOW_UNDERLYING_ONLY`: signal research is possible but option evidence cannot support promotion.
- `RED_REMOVE`: underlying or contract evidence is materially defective.
- `PROVISIONAL_AWAITING_LIVE_QUOTES`: weekend gates passed; Monday quote gate remains.

## 5. Common hypotheses

The initial scan excludes LLM output, news, IV, Greeks, and member-specific features.

Common clock:

- Normalize one-minute Alpaca IEX bars into ET half-open intervals `[09:30 + 15k, 09:45 + 15k)`. Aggregate open=first, high=max, low=min, close=last, volume=sum, and interval VWAP=`sum(minute_vwap × minute_volume) / sum(minute_volume)`. An interval with a missing minute, missing VWAP, or zero cumulative volume is invalid for a decision.
- Session IEX VWAP at `t` is the same volume-weighted calculation over all valid one-minute bars from 09:30 through the completed interval at `t`; do not substitute typical price or an unweighted average.
- Label each 15-minute interval by its end and set its effective availability to `interval_end + 1 second`.
- Evaluate new entries at 10:30:01, 11:00:01, 11:30:01, 12:00:01, 12:30:01, 13:00:01, 13:30:01, 14:00:01, and 14:30:01 ET; evaluate open-position management at every completed 15-minute interval plus one second.
- The underlying execution proxy is the open of the first one-minute IEX bar whose interval starts on the next whole minute after the effective decision/exit time. The option proxy follows the common one-minute/five-minute rule in Section 4D. Store both requested and source timestamps.
- One position per symbol and no overlapping labels. The central hard exit order triggers at `min(confirmed_fill_time + 60 minutes, 15:45 ET)` independently of the 15-minute signal-management loop; proxy/runtime execution follows at the next eligible observation/order event.
- Never hold overnight in V1.
- Position age begins at the confirmed proxy/runtime fill timestamp, not signal or order-submission time.
- VWAP exits use completed-bar close only, never intrabar high/low. A bullish adverse cross is `close <= session_vwap`; bearish is `close >= session_vwap`. A bullish H2 touch is `close >= session_vwap`; bearish is `close <= session_vwap`.
- Strategy-level premium profit targets and price-based stop-loss exits are disabled in competition V1. Alpha exits are exactly the VWAP and hard-time rules above, frozen before the first 2025 OOS run. Portfolio daily/competition stops and stale/reconciliation/orphan remediation remain mandatory safety overrides; record them separately and never tune them as alpha exits.
- Exclude Alpaca-calendar early-close sessions from both research scoring and competition entries with `EARLY_CLOSE_SESSION`; do not apply impossible fixed full-session times.
- Thursday's final-session rule overrides normal cadence: no new entries after 13:30 ET, start programmatic flatten by 15:15 ET, and require broker-confirmed flat by 15:30 ET.

### H0 — matched null control

For every real signal, use only the synchronized centered five-session date-block bootstrap frozen in Section 9. There is no per-trade sign-permutation alternative. This measures market drift/small-sample luck and supplies the family-wise maximum-statistic control across every viewed candidate.

### H1 — normalized intraday continuation

At decision time `t`:

- `r60_t = log(close_t / p_{t-60m})`, where `p_{t-60m}` is the completed 15-minute close exactly 60 minutes earlier, except at 10:30 when it is the 09:30 session-open price; never cross the prior close or an overnight boundary;
- over the prior 20 valid completed sessions at the same time of day, compute `mu_r60` and sample standard deviation `sigma_r60`;
- require all 20 prior observations and form `momentum_z = (r60_t - mu_r60) / max(sigma_r60, 1e-6)`;
- compute session IEX VWAP from completed bars only.

Signal:

- bullish when `momentum_z >= 1.0` and close is above session IEX VWAP;
- bearish when `momentum_z <= -1.0` and close is below session IEX VWAP;
- otherwise `NO_TRADE`.

Exit at the common hard time deadline, or earlier on an adverse completed-close VWAP cross.

### H2 — normalized intraday VWAP reversion

At decision time `t`:

- `deviation = log(close_t / session_vwap_t)`;
- over the prior 20 valid completed sessions at the same time of day, compute `mu_deviation` and sample standard deviation `sigma_deviation`;
- require all 20 prior observations and form `deviation_z = (deviation - mu_deviation) / max(sigma_deviation, 1e-6)`.

Signal:

- bullish when `deviation_z <= -1.5` and `abs(momentum_z) < 0.5`;
- bearish when `deviation_z >= 1.5` and `abs(momentum_z) < 0.5`;
- otherwise `NO_TRADE`.

Exit on a completed-close VWAP touch or at the common hard time deadline.

H2 is a standalone challenger, not an H1 overlay or simultaneous fallback signal. It begins only after the shared H1 pipeline reproduces on two machines.

Sensitivity values test stability; they are not per-symbol optimization:

- H1 threshold: 0.75, 1.00, 1.25;
- H2 threshold: 1.25, 1.50, 1.75;
- time-exit delay from confirmed fill: 45, 60, 90 minutes (three, four, or six 15-minute bars).

The central value is the declared candidate. Owners may not report only the best neighboring value.

## 6. Common option expressions

Evaluate the underlying signal first. The option layer expresses the frozen direction and cannot rescue or reverse it.

Research and runtime load the same committed `configs/template_catalog.yaml`; every run and order plan records its canonical hash. The catalog freezes family, DTE bucket, moneyness/width construction, ranking, atomicity, and exit rules. The advisory agent is veto-only at this boundary: it cannot change expression family, strike-width policy, DTE, selector ranking, or size. Current quotes and Greeks may reject an otherwise selected candidate or serve as diagnostics, but may not retrofit a different historical selection rule.

Historical selection may use only raw underlying spot, contract type, strike, expiration, standard multiplier/deliverable, and evidence the contract existed by the decision time. It may not use historical Greeks, current snapshots, future volume, or post-decision open interest.

### O1 — single long option diagnostic

- Directional call or put.
- Nearest standard strike to raw spot; an exact distance tie chooses the OTM strike (higher for calls, lower for puts).
- Primary DTE bucket 7–14 calendar days; 15–21 DTE is one predeclared stability bucket, not another optimization search.
- Within a DTE bucket, choose the minimum positive calendar DTE; ties use expiration ISO date then OCC symbol lexicographically.
- One contract for instrument-level reporting, then separately size to fixed account risk.
- Diagnostic only: O1 cannot become `paper_candidate` in competition V1 and cannot replace O2 after any holdout is viewed.

### O2 — debit vertical sole promotion-eligible expression

- Select expiry and long leg by the exact O1 ranking.
- For a call, set short target to `long_strike × 1.01` and choose the smallest listed same-expiry standard strike at or above that target. For a put, set target to `long_strike × 0.99` and choose the largest listed same-expiry standard strike at or below it. If none exists, the expression is infeasible.
- Same DTE policy as O1.
- Debit positive and strictly below spread width.
- Both legs require simultaneous historical observation coverage.
- At each historical/runtime decision, set `risk_budget = min($500, 0.50% × current_account_equity)` and buy the largest nonnegative integer quantity whose fee-inclusive maximum loss fits it.

For one debit spread, fee-inclusive maximum loss is `gross_entry_debit_per_share × 100 + opening_fees + reserved_exit_fees`; the same arithmetic and integer floor are used in research portfolio replay and runtime. If the risk budget cannot buy one spread, the expression is infeasible. O1 diagnoses whether paying two leg spreads destroys the signal; it is not a second opportunity to cherry-pick P&L.

Contract ranking uses only the frozen metadata/raw-spot fields and is completed before option observations or current quote quality are joined. Missing historical bars for the chosen leg(s) yield `NO_PROXY_FILL`; do not rerank to a contract with better coverage. A failed current quote gate yields `NO_TRADE`; do not walk the chain to a different width/DTE/strike.

## 7. No-lookahead protocol

Every raw/normalized record includes:

```text
event_time
available_time
ingested_at
endpoint_or_tool
explicit_feed
scrubbed_query
source_page_token
raw_response_hash
```

Rules:

1. Features require `event_time <= decision_time` and `available_time <= decision_time`.
2. For a 15-minute IEX bar, define `available_time = interval_end + 1 second`; a decision at the printed close timestamp cannot consume that bar until this latency has elapsed.
3. A bar-close signal executes no earlier than the next eligible observation.
4. Historical indicative option trades/bars are delayed/non-executable research data and are used only after the decision as fill/outcome proxies. They never form signals, contract choices, or claims about what was executable; manifests retain Alpaca's documented 15-minute indicative-trade delay.
5. For a current prospective option snapshot, `available_time = ingested_at`; freshness is separately measured from its source event timestamp.
6. Contract metadata fields without a documented historical availability timestamp support `PIT_EXISTENCE_PROXY` only; they are not decision-time chain or liquidity features.
7. A later next-bar price may be a realized proxy but is never an input to the decision.
8. Current option snapshots/Greeks never enter historical rows.
9. Missing option data is never forward-filled.
10. Rolling normalization uses prior completed sessions only.
11. Raw spot chooses strikes; split-adjusted prices compute continuous returns.
12. Exclude adjusted roots/nonstandard deliverables.
13. Freeze code/config/data hashes before final validation.
14. Any post-validation change creates a new experiment and invalidates the prior holdout label.
15. Use the Alpaca market calendar, including early closes.

## 8. Conservative option proxies and current marks

Free Alpaca history does not expose a historical executable option NBBO series. Publish two explicit proxies; never call either a fill reconstruction.

### Base bar proxy

Use the first complete common one-minute interval within the five-minute fill window defined in Section 4D. Freeze `minimum_tick_proxy = $0.05` per option contract when historical metadata does not expose a decision-time tick rule. For each leg with bar open `O`, high `H`, and low `L`:

```text
buffer = max(minimum_tick, 10% × O, 25% × (H - L))
buy_proxy  = O + buffer
sell_proxy = max(0, O - buffer)
```

For a debit vertical:

- gross entry debit per share = long buy proxy − short sell proxy;
- gross exit credit per share = long sell proxy − short buy proxy;
- reject gross entry debit at or below zero or at/above spread width;
- clamp gross exit credit to `[0, spread_width]`;
- net dollars per spread = `(gross_exit_credit_per_share - gross_entry_debit_per_share) × 100 - opening_fees - exit_fees`.

### Severe execution stress

- Every buy uses `H + minimum_tick`.
- Every sell uses `max(0, L - minimum_tick)`.
- Pair leg extremes even if they were not simultaneous.
- Double every frozen fee/cost assumption.

This is intentionally punitive. If missing exit observations occur, assign zero exit value for the long/debit structure and report the penalty rate; if that penalty dominates, classify the result `UNSCORABLE_OPTION_HISTORY` rather than profitable/unprofitable.

Fee assumptions are stress-model parameters, not claimed Alpaca commissions or market data. Before viewing results, freeze the central placeholder at **$0.10 per contract, per leg, per side**. Publish sensitivities at $0.00 and $0.25 on the same basis, plus the severe 2× central-cost scenario. Never select the fee or tick proxy that makes P&L positive; every output is labeled `bar_proxy_supported`, `bar_proxy_suggestive`, or `bar_proxy_unsupported`, never “filled,” “executable,” or “execution-quality evidence.”

### Prospective and paper shadow marks

When current indicative quotes are available:

- long liquidation mark = bid;
- short liability mark = ask;
- spread liquidation value = long bid − short ask;
- entry preview = long ask − short bid;
- midpoint is diagnostic only.

If a required quote is stale/missing/crossed, do not invent a mark. Display `MARK_UNAVAILABLE`, block new risk, and use remaining defined maximum loss for the risk bound.

Keep separate fields/panels:

- `underlying_signal_return`;
- `historical_option_bar_proxy_return`;
- `broker_reported_paper_account_return`;
- `conservative_shadow_return`.

## 9. Validation design

Proposed partitions, conditional on coverage and prior researcher exposure:

- Underlying discovery: 2017–2023.
- Option feasibility/proxy calibration: February–December 2024.
- Quarterly walk-forward OOS: January–December 2025.
- Intended final validation: January 1–August 27, 2026.

If anyone already inspected 2026 outcomes, label the last period `final_validation`, disclose prior exposure, and do not call it a sealed holdout. A true sealed holdout needs its manifest hash and access gate recorded before inspection.

Champion, preregistered fallback, symbols, thresholds, expression, selector, and all risk/template hashes are selected using data through December 31, 2025 and frozen before any 2026 result is opened. The 2026 period is accept/reject validation only: it cannot tune or reorder candidates. If the champion fails, no previously undesignated runner-up may be promoted; only a fallback explicitly named and frozen before 2026 access may be used, and only if it independently passes the same 2026 accept/reject gate and its failover condition was preregistered.

Walk-forward rules:

- require at least 252 completed experiment-history sessions before the first OOS fold, while each feature value uses exactly the most recent 20 valid same-time observations defined in Section 5;
- fixed common rule; rolling scale estimates may update;
- test one calendar quarter at a time;
- purge observations spanning a boundary and embargo one full session;
- aggregate inference by daily account returns, not overlapping raw trades;
- use the frozen synchronized five-session moving-block bootstrap defined below;
- cluster pooled symbol results by date;
- treat QQQ/TQQQ and SMH/SOXL as correlated families;
- run a block-bootstrap maximum-statistic control across every candidate/variant whose result was viewed and could have influenced selection;
- report raw and multiplicity-adjusted evidence;
- compute deflated/selection-adjusted Sharpe only on the full historical OOS record with all tried variants logged.

Frozen null/resampling specification:

- the authorizing test uses complete 2025 OOS daily account returns for every promotion-eligible central H1/H2 × symbol × O2 candidate, plus any variant actually allowed to influence selection; it excludes 2024 calibration and never pools post-selection 2026 results;
- align every candidate to the same complete market-date index and include zero-return inactive dates;
- for each candidate, center its daily OOS return series under the zero-mean null;
- use a fixed seed and 10,000 synchronized bootstrap replications of five-session moving date blocks;
- use the identical sampled date-block indices for all symbols, hypotheses, expressions, and selectable variants in each replication, preserving cross-symbol dependence;
- compute each candidate's studentized mean daily return and retain the maximum positive statistic per replication for the one-sided positive-edge family;
- report raw and family-wise adjusted one-sided p-values against that maximum-statistic null; `statistically_supported` and `paper_enabled` require 2025 adjusted `p <= 0.10` on the frozen central specification;
- record every viewed threshold, holding period, symbol, hypothesis, expression, fee/tick choice, and rerun in the trial ledger. O1 and declared sensitivities are excluded from the authorizing family only because policy makes them ineligible to affect selection; if a human uses one to choose or reorder, the freeze is invalid and the new selectable family must include it. Every selectable variant is also included in the deflated-Sharpe trial count.

Only the central preregistered specification is promotion-eligible. Neighboring sensitivities diagnose stability and cannot replace it. Any exception creates a new preregistered experiment and forfeits the current holdout label.

Predeclared selection sequence using data through December 31, 2025 only:

1. Candidate passes every data and falsification gate.
2. Total OOS net percentage return is positive.
3. At least 60% of populated quarterly folds are positive.
4. Select highest median quarterly net percentage return among remaining candidates.
5. Tie-break on lower maximum drawdown, then higher 2024–2025 two-leg simultaneous proxy-coverage rate, then lexicographically by stable candidate ID.

Do not select a winner independently inside each symbol and present six winners.

Monday prospective quotes are accept/reject operational gates only. They may produce `NO_TRADE` or reject a frozen candidate, but they never re-rank the champion/fallback or select a replacement.

After candidates are frozen, the 2026 accept/reject gate requires for each predesignated candidate: positive base-cost normalized-account return, nonnegative severe/2×-cost return, maximum drawdown no greater than 4%, no data/falsification failure, and the exact trade/day concentration limits below. Report a fixed-candidate 2026 interval and p-value descriptively; it is not pooled into or substituted for the authorizing 2025 adjusted test. Failure rejects that candidate; it does not open a search.

## 10. Metrics

### Data and feasibility

- expected/observed underlying bar ratio;
- `PIT_EXISTENCE_PROXY` coverage rate;
- option entry/exit and simultaneous-leg coverage;
- prospective quote pass rate, median/p90 width, stale/crossed/missing rate;
- time to first eligible option observation;
- API errors, 429s, page completeness, and cache hit rate.

### Underlying signal

- mean/median signed forward percentage return;
- hit rate and favorable/adverse excursion;
- active days and trades;
- session-clustered standard error and five-day block-bootstrap interval;
- year/quarter/direction/regime slices;
- matched-null/permutation percentile.

### Option/account performance

- gross/net dollar P&L;
- percentage return on premium/maximum loss;
- normalized $100,000 account return;
- geometric cumulative return;
- mean/median trade return, hit rate, payoff ratio, profit factor;
- turnover/time in market;
- maximum drawdown/time under water;
- historical 95% expected shortfall/CVaR;
- worst day/trade and top-trade/top-day concentration;
- cost as a percentage of gross edge;
- missing-mark/no-fill penalty rate;
- beta/correlation to SPY IEX returns.

### Risk-adjusted measures

Use complete daily account returns, including zero-return market days:

- Sharpe;
- Sortino;
- Calmar;
- annualized return only for histories longer than one year;
- deflated or selection-adjusted Sharpe;
- block-bootstrap probability that mean daily return is positive.

Competition reporting always includes raw dollars and:

```text
competition_return_pct =
    (final_equity - baseline_equity - external_cash_flows) / baseline_equity
```

The expected baseline is $100,000 at 09:30 ET on Monday, August 31. Do not calculate or market an annualized Sharpe/Sortino or statistical confidence from four competition sessions; show `INSUFFICIENT_SESSIONS` instead.

## 11. Promotion and falsification

Research lifecycle:

```text
data_unchecked
→ data_feasible
→ signal_supported
→ option_expression_supported
→ paper_shadow
→ paper_candidate
```

Separate non-evidence state: `paper_demo_only`.

### `signal_supported`

Requires:

- green underlying-data gate;
- at least 75 OOS trades across at least 40 active sessions;
- at least four quarterly folds with trades;
- positive overall OOS signed underlying return;
- at least 60% of populated folds positive;
- no positive trade above 25% of `sum(max(net_trade_pnl, 0))` and no positive date above 25% of `sum(max(daily_pnl, 0))` over the evaluated OOS period;
- central parameter retains return sign in at least two-thirds of neighboring sensitivity runs;
- next-bar execution does not eliminate the edge.

### `option_expression_supported`

Additionally requires:

- available `PIT_EXISTENCE_PROXY` for every selected leg;
- green historical coverage for the chosen structure;
- at least 50 option-scored OOS trades across at least 30 active dates;
- at least four quarterly folds with at least five option-scored trades per fold;
- missing-exit penalty rate no greater than 10%;
- positive total OOS normalized-account return under base conservative costs;
- nonnegative total OOS normalized-account return under severe/2× cost;
- maximum OOS drawdown no greater than 4%;
- prospective Monday quote gate passes;
- independent artifact/hash reproduction;
- multiplicity-adjusted evidence reported.

Before `paper_candidate`, replay the exact champion and the preregistered fallback **separately** as complete portfolios using integer contracts, the frozen runtime selector ranking, current buying-power rules, concurrency limits, QQQ/TQQQ/IGV and SMH/SOXL cluster caps, maximum daily loss, total remaining maximum-loss limits, entry cadence, and Thursday flatten logic. Runtime enables only one alpha plug-in at a time. Failover is a predeclared, audited session-boundary state change for operational unavailability—not intraday P&L switching or simultaneous signal blending. A failure returns that candidate to `paper_shadow` or `paper_demo_only`.

The report may use `statistically_supported` only when the frozen one-sided family-wise adjusted maximum-statistic test has `p <= 0.10`. Otherwise, if safety/feasibility gates pass, label the result `suggestive`; it may enter shadow or explicitly `paper_demo_only`, not evidence-backed alpha.

Immediate falsification/demotion:

- historical contract existence cannot be established;
- profit exists only at close/mid prices;
- next-observation execution makes the central result nonpositive, or severe/2× costs produce negative total OOS normalized-account return;
- result changes sign across most folds/adjacent parameters;
- one day/trade/year dominates;
- option observations are too sparse;
- Monday indicative quotes fail width/freshness gates;
- leveraged-ETF advantage vanishes after fixed-risk normalization;
- result uses current Greeks/open interest/future contract fields;
- independent rerun cannot reproduce hashes and metrics.

## 12. Standard artifacts

Every symbol owner returns the same package:

```text
research/<symbol>/
├── hypothesis_H1.yaml
├── hypothesis_H2.yaml
├── entitlement_probe.json
├── data_manifest.json
├── data_quality.json
├── contract_universe_manifest.json
├── pit_existence_proxy.parquet
├── feasibility_card.json
├── feasibility_card.md
├── runs/<run_id>/
│   ├── run_manifest.json
│   ├── signals.parquet
│   ├── selected_contracts.parquet
│   ├── proxy_leg_observations.parquet
│   ├── trades.parquet
│   ├── daily_returns.parquet
│   ├── fold_metrics.parquet
│   ├── metrics.json
│   ├── cost_stress.json
│   ├── portfolio_replay.json
│   ├── limitations.md
│   └── plots/
└── promotion_card.md
```

`data_manifest.json` records endpoint/tool/version, explicit feed, scrubbed query, requested/returned coverage, page completion, row count, fetch time, raw/normalized hashes, missingness, rate-limit/errors, and adjustment type.

`run_manifest.json` records Git commit, config/data/hypothesis/expression hashes, exact `template_catalog.yaml` hash, fold boundaries, one-minute/five-minute proxy timing rules, tick/fee assumptions, bootstrap seed/specification, all viewed and tried variants, start/end time, status, owner, and reviewer.

`promotion_card.md` contains every gate with artifact reference, falsification attempts, limitations, owner/reviewer sign-off, and exact permitted state: rejected, shadow, demo-only, or candidate.

Competition telemetry is separate:

```text
telemetry/competition/
├── account_equity.jsonl
├── orders.parquet
├── fills.parquet
├── risk_decisions.parquet
├── broker_reported_paper_pnl.json
├── conservative_shadow_pnl.json
└── telemetry_summary.md
```

## 13. Delegated delivery schedule

### Saturday morning

- Data steward runs one shared entitlement probe.
- Freeze feed, timestamp, contract, and proxy rules.
- Build one common scanner/artifact schema.
- Bulk-fetch the six underlying histories once.
- Assign one symbol per member; forbid member-specific backtest engines.

### Saturday afternoon

- Complete six historical feasibility cards.
- Remove `RED_REMOVE` symbols.
- Run H1 underlying scan on all green/yellow symbols.
- Begin point-in-time contract-existence proxy construction.
- Do not start H2 until H1 reproduces on two machines.

### Sunday morning

- Run H2 under the frozen common rule.
- Deep-test option expressions for at most three symbols selected by data/coverage quality—not P&L.
- Cut O1/O2 where mark coverage fails.
- Run quarterly walk-forward, purge/embargo, cost stress, null and multiplicity controls.

### Sunday afternoon

- Cross-review and independently reproduce.
- Using data through 2025 only, freeze one champion and at most one operational fallback by 18:00 ET, including its explicit session-boundary failover condition.
- Only after that freeze, open 2026 once for accept/reject validation; do not tune, reorder, or designate a new fallback from that result.
- If none passes, use `NO_TRADE` or separately authorized minimum-risk `paper_demo_only` without an alpha claim.
- Freeze strategy rules, symbols, config, proxy assumptions, and hashes.

### Monday

- Capture the prospective indicative quote-quality gate.
- Start in shadow; arm only after chain, quote, account, risk, and release gates pass.
- Any parameter change creates a new version and voids prior forward-evidence continuity.

### Tuesday–Thursday

- Collect competition telemetry and fix operational defects only.
- Do not tune direction, thresholds, DTE, strike, or exits from competition outcomes.
- Stop new entries Thursday 13:30 ET; API-flatten by the times in the root plan.

### Friday

- Reconcile and submit; do not depend on Friday morning P&L.
- Do not present four-day outcomes as risk-adjusted or statistically significant.

## 14. Required submission wording

Use language equivalent to:

> Historical research used only Alpaca Basic IEX underlying data and Alpaca's free indicative options data. Underlying signals were evaluated with point-in-time, next-observation, purged walk-forward tests. Because free historical options data does not provide an executable OPRA bid/ask series, option results are labeled conservative bar-proxy estimates, while hackathon results are shown separately as paper-account telemetry. Four competition sessions demonstrate system operation, not durable alpha.

This evidence boundary is part of the product's credibility, not a disclaimer to hide in the appendix.

## 15. Official research references

- [Alpaca Basic and Algo Trader Plus market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Historical option-data coverage and indicative/OPRA definitions](https://docs.alpaca.markets/us/docs/historical-option-data)
- [Historical option bars API](https://docs.alpaca.markets/us/reference/optionbars)
- [Historical option trades API](https://docs.alpaca.markets/us/reference/optiontrades)
- [Option-contract enumeration API](https://docs.alpaca.markets/us/reference/get-options-contracts-1)
- [Historical stock bars API](https://docs.alpaca.markets/us/reference/stockbars)
- [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Paper-trading behavior and simulation limitations](https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading)
