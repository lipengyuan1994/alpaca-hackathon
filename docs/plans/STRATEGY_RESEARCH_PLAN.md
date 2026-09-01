# Strategy research plan

Status: normative research and integration handoff protocol, v3; published 2026-08-29 for credential-free research

Implementation release: `cb03a7684fb67c6f0888333f6c3c2145e8645be9`

Dependency-lock hash: `sha256:b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf`

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

### How each researcher uses this document

Each of the three packet owners receives two strategy families from Section 3 and follows this sequence for both:

1. Copy the assigned strategy card into `research/candidates/<candidate_id>/strategy_card.md` and freeze the economic hypothesis, central parameters, owned symbol cells, feature contract, exit-policy ID, and falsification conditions **before viewing outcome P&L**.
2. Use immutable Alpaca datasets collected with the repository's GET-only helper or a hash-valid teammate collection. Do not use a private downloader, substitute vendor data, or fork the frozen clock, selector, cost, or marking semantics.
3. Implement exactly one assigned strategy family and its canonical plug-in package. Each package must ship its own offline, cross-platform Python reproduction module; POSIX/PowerShell wrappers may call that module. This repository does not claim that a central research backtester exists.
4. Run the central specification on the packet's two owned symbol cells and publish every prescribed sensitivity. A pair-cell result is diagnostic research evidence, not a complete deployable `CandidateSpecV1` and not permission to choose the better of the two symbols.
5. Return one complete research, plug-in, golden-fixture, and parity package per assigned family as defined in Section 12. A notebook, chart, Sharpe ratio, or `plugin.py` by itself is not a deliverable.
6. Have the designated owner of another packet reproduce both runs from hashes and sign both promotion cards. The reviewer must not alter a family they review after seeing outcomes, and the packet owner cannot promote their own lifecycle state.
7. After all six pair-cell packages are frozen, the central quant/release owner alone runs the compatible-symbol, cross-family, and full-universe replay using one frozen arbitration implementation. That later replay creates the portfolio-level candidate and cannot silently modify the researcher's signal function.
8. Stop at the first failed gate. `NO_TRADE`, `REJECTED`, `NOT_SELECTED_BY_FEASIBILITY`, and `INSUFFICIENT_EVIDENCE` are valid results and must remain visible.

There are four distinct completion states:

| State | Meaning | May run in the judged account? |
|---|---|---:|
| `RESEARCH_COMPLETE` | Reproducible signal/option-proxy report exists | No |
| `INTEGRATION_READY` | Plug-in contract, golden contexts, and backtest/runtime parity pass | No |
| `PAPER_CANDIDATE` | Independent research/promotion gates pass and platform safety blockers are closed | No, until release approval |
| `PAPER_ENABLED` | Exact source/config/catalog/evidence hashes are centrally registered and the release/risk owners approve | Yes, paper only |

### Architecture review and readiness boundary — 2026-08-29

The implementation commit and exact `uv.lock` SHA-256 above are the distributed research release. Researchers must stop on either mismatch. The dated gate status and concrete registry/catalog/fixture-feature hashes live in `docs/architecture/RESEARCH_INTERFACE_FREEZE.md`; candidate-specific feature contracts and hashes remain owner deliverables that must be frozen before outcome P&L.

Researchers need no judged-account credentials and receive no broker/order authority. They may start hypothesis definitions, pure feature/signal code, Alpaca entitlement/coverage work, and offline pair-cell backtests only against the pinned research interface. The platform owner is responsible for the following integration gates:

| Gate | Published research status | Required evidence before integration claim |
|---|---|---|
| `G-R1_REGISTRY_AUTHORITY` | Closed for the host research interface; research candidates are not registered | Candidate source/config/feature/evidence hashes, allowed scope/tuples, owner/reviewer, non-author review, central merge, and negative tests |
| `G-R2_CATALOG_PARITY` | Partial; catalog schema/hash/loader are published | Candidate fixtures proving exact OTM tie, outward strike, fee, quantity, maximum-loss, and refusal parity |
| `G-R3_OUTPUT_BINDING` | Closed for the host baseline | Run each candidate's golden and negative cases through the host after its registry proposal is reviewed |
| `G-R4_FEATURE_CONTRACT` | Closed for host shape and fixture enforcement | Each family publishes exact formulas, key order, lookback/availability/missing rules, worked examples, and candidate-specific hash |
| `G-R5_EXIT_OWNERSHIP` | Closed for credential-free research semantics | Candidate close fixtures plus release-owned durable fill/position lifecycle, restart/concurrency, broker deadlines, and confirmed-flat evidence |
| `G-R6_RUNNER_ISOLATION` | Partial; offline package research may start | Deployment-equivalent image build, OS egress/filesystem containment, and malicious-package evidence |

Paper enablement has additional non-research blockers: independently recompute defined maximum loss, use an exact paper-host allowlist, enforce daily loss/buying power/market clock/option quote freshness, bind current control state, implement durable reservation/outbox/inbox/CAS and reconciliation, implement Alpaca MCP transport, and prove close/flatten/restart behavior. Passing a strategy backtest cannot waive any of them.

The platform/release owner has published the dated `RESEARCH_INTERFACE_FREEZE` with the host registry/catalog/fixture-feature values, position-policy references, conformance commands, and implementation commit. A general repository test command is not a research backtester. Each packet owner returns a separate offline reproduction script and candidate-specific feature/reason hashes for each assigned family; the release owner validates them before central replay.

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

Each researcher may collect and cache their own data through the repository's [read-only Alpaca data collector](../research/ALPACA_DATA_COLLECTOR.md) and frozen `configs/research_data_collection_v1.yaml` specification; the operational steps are in the [individual historical-data guide](../research/ALPACA_HISTORICAL_DATA_GUIDE.md). A hash-valid `data_manifest.json` with `status=COLLECTED` (or compatible legacy `COLLECTED_UNATTESTED`) plus its bound probe/artifacts is an eligible research input. The researcher may also reuse a teammate's immutable collection. Use a separate development credential only in the collector runtime; no research task may place an order. Independent review is required for integration and promotion claims, not as a prerequisite to run a backtest.

## 3. Three-packet strategy-family assignment

The team is searching across **strategy families**, not asking three people to optimize three symbol pairs. Each packet owner receives two separately versioned hypotheses and uses the same immutable data, decision clock, folds, option selector, sizing, cost stresses, metrics, artifact schemas, and promotion gates. Each family still has its own economic hypothesis, pure signal implementation, canonical plug-in package, deterministic offline reproduction script, candidate identity, and outcome state.

| Packet recipient | Candidate families | Core questions | Required pair cells | Independent reviewer |
|---|---|---|---|---|
| Group A research owner | `intraday_continuation_v1`; `vwap_reversion_v1` | Continuation after unusually large same-time moves; reversion after unusually large VWAP deviations in a weak-trend regime | SPY, QQQ | Group B research owner reviews both packages |
| Group B research owner | `opening_range_breakout_v1`; `gap_continuation_v1` | Continuation after a confirmed opening-range break; continuation after a standardized overnight gap and first-hour confirmation | SMH, SOXL | Group C research owner reviews both packages |
| Group C research owner | `relative_strength_residual_v1`; `compression_breakout_v1` | Persistence of benchmark-residual strength; continuation after intraday compression and price/volume expansion | TQQQ, IGV, with immutable QQQ controls for relative strength | Group A research owner reviews both packages |

Section 5 defines the six central strategies. A packet owner must freeze both assigned strategies before viewing outcome P&L for either one; this prevents the first result from driving changes to the second. Corrections made after results are viewed create new candidates and enter the trial ledger. Review follows the external ring above rather than self-review within a packet.

Primary engineering ownership still applies. The research sprint adds these shared duties so “common” work has an accountable owner:

| Role | Shared research duty | Concrete handoff | Reviewer role |
|---|---|---|---|
| Release/quant lead | Candidate registry, fold calendar, trial budget, bootstrap/multiplicity, final selection | Frozen candidate/selection manifests and full comparison table | Evidence/submission owner |
| Data steward | Alpaca entitlement probe, pagination, raw/normalized cache, symbol feasibility | Immutable shared dataset manifests and six feasibility cards | Feature/platform owner |
| Feature/platform owner | Feature registry, common signal/backtest adapter, plug-in conformance | Two-machine intraday-continuation golden run and shared parity command | Data steward |
| Options/risk quant | O1/O2 selector, option proxy, fee/max-loss arithmetic | Catalog-parity fixtures and option-coverage report | Portfolio/risk integration owner |
| Portfolio/risk integration owner | Portfolio replay, quote stress, risk/position-policy integration | Base/severe portfolio replay and integration gate report | Options/risk quant |
| Evidence/submission owner | Standard plots/cards, limitations, judge-facing evidence package | Comparable report bundle and promotion-card generator | Release/quant lead |

### Pair-cell evidence versus central candidate identity

Each packet owner returns one `PairCellEvidenceV1`-equivalent package per assigned family for the packet's two symbol cells. Each package answers whether that frozen family is reproducible on those cells. It must retain both symbols and may not declare a winner, deployment universe, champion, fallback, or `PAPER_ENABLED` state.

Only the central quant/release owner constructs a `CandidateSpecV1` after all six packages are frozen. That job expands each family to its complete compatible feasible universe, runs the frozen cross-symbol arbitration, records every suppressed row, applies the common family-wise test, and selects at most one champion plus one preregistered fallback. A researcher must therefore use these labels exactly:

| Artifact | Producer | Scope | Authority |
|---|---|---|---|
| `pair_cell_metrics.json` | Family owner | Two assigned symbols, central and prescribed diagnostics | Research evidence only |
| `pair_cell_review.json` | Designated external packet reviewer | Independent reproduction of the same cells | Review evidence only |
| `central_full_universe_replay.json` | Central quant/release owner | All compatible feasible symbols and frozen arbitration | Selection evidence, still non-authorizing |
| `registry_candidate.yaml` | Family owner proposes; release owner validates | Requested plug-in capability | Lifecycle must remain `research_only` |

A central candidate is one complete deployable portfolio specification, not a chart, one trade rule on one ticker, a pair-cell package, or a post-hoc collection of winning cells:

```text
CandidateSpecV1 =
    signal_family_id
  + ordered_eligible_symbol_set
  + feature_schema_hash
  + central_config_hash
  + O2_expression_and_template_catalog_hash
  + allocator_hash
  + position_policy_hash
  + base_cost_policy_hash
```

The human-readable ID follows `<signal_family>__<symbol_scope>__o2_v1`; the canonical hash over the fields above is the actual identity. Changing the symbol set, allocation/tie-break, exit, selector, costs, or central parameter creates a new candidate before returns are viewed. Per-symbol rows are diagnostic cells inside that candidate. Combining the best symbols after seeing outcomes is a new, contaminated search—not portfolio construction.

### Common cross-symbol portfolio construction

Each owner's `signal.py` must expose a pure per-symbol evaluation that computes one score and direction. The pair-cell reproduction script records both rows independently. The later central adapter applies `packages/strategy_sdk/arbitration.py` at source-file SHA-256 `864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5` and emits one winner because V1 `StrategyEvaluationV1` can return only one entry request. Do not ship a private outcome-sensitive substitute. The frozen rules are:

1. Remove candidates that fail feature/data/session, candidate-specific, cluster, cooldown, or existing-exposure gates.
2. Normalize each strategy's score so its entry threshold equals `1.0`; rank by `entry_score - 1.0`.
3. Break an exact score tie by the frozen symbol order `SPY, QQQ, TQQQ, SMH, SOXL, IGV`; do not use option P&L, spread width, or any future outcome as an alpha tie-break.
4. Map the winning score to `signal_strength_bucket`: `[1.00, 1.25)` is `LOW`, `[1.25, 1.75)` is `MEDIUM`, and `>= 1.75` is `HIGH`.
5. Send only that semantic winner to central template/quote/risk checks. If it fails, return/record `NO_TRADE` for the cycle; do not fall through to the second-ranked symbol.
6. Permit at most one new exposure-increasing intent at a decision time and at most one nonterminal position/order for competition V1.
7. Treat QQQ/TQQQ/IGV as one technology cluster and SMH/SOXL as one semiconductor cluster. Leveraged/unleveraged pairs are correlated expressions, not independent confirmation.
8. In research artifacts, record every eligible, rejected, selected, and suppressed candidate with its reason code so portfolio P&L reconciles to per-symbol results. Runtime replay recomputes the same table from frozen context/config and `signal.py`.

Round 0 still produces six comparable **symbol feasibility cards**, but collection is self-service and cross-cutting rather than a centrally blocked alpha assignment. The cards consume only hash-valid collector artifacts: raw/split IEX bars, calendar, option-contract metadata, frozen historical option-observation requests, and separately captured indicative quote artifacts. Before any candidate return is exposed, the researcher writes a deterministic `option_proxy_feasibility_manifest.json` bound to their data manifest. It ranks all six symbols using only blinded fields: entitlement result, requested/returned dates, timestamp integrity, bar completeness, standard-contract count, simultaneous-leg observation coverage, corporate-action classification, and deterministic symbol-order tie-break. It contains no signal return, option P&L, direction, Sharpe, candidate ID, or owner preference.

The manifest has `schema_version=option-proxy-feasibility/v1`, the pinned implementation/lock/data hashes, `generated_at`, ordered scoring fields, all six ranked rows, `selected_symbols` of length at most three, `selection_cutoff_rank`, and a manifest hash. `status=READY_FOR_REPLAY` is sufficient for outcome access once hashes and bindings validate; signatures may be appended as provenance but are not required. Each packet then behaves as follows:

- an owned symbol selected by the manifest receives full historical option-proxy work;
- an owned symbol not selected still receives the complete underlying pair-cell study, but `option_proxy_status` is exactly `NOT_SELECTED_BY_FEASIBILITY`;
- its run directory includes empty schema-valid `selected_contracts.parquet`, `proxy_leg_observations.parquet`, and `trades.parquet`, plus `option_proxy_not_selected.json` referencing the feasibility manifest and reason `NOT_SELECTED_BY_FEASIBILITY`;
- no group may exchange, rerank, or fill an unused slot after seeing any outcome;
- Monday prospective quotes are accept/reject gates and never re-rank or replace the frozen subset.

If returns were visible before this manifest was hash-bound and marked `READY_FOR_REPLAY`, record `BLINDING_BREACH` and include every viewed strategy-by-symbol variant in the selection/multiple-testing family.

Resource limits for the sprint:

- at most three symbols enter expensive full historical option-proxy retrieval, selected on feasibility rather than P&L;
- at most two symbols enter the final competition deployment allowlist;
- each strategy family has one central candidate and only the sensitivities explicitly listed in Section 5;
- one plug-in becomes champion and at most one genuinely independent plug-in becomes an operational fallback; fallback is not intraday P&L switching.

## 4. Standard one-symbol feasibility scan

The researcher who owns a collection assigns or runs A–F once per symbol and records evidence paths, not just prose. Alpha owners may reuse a hash-identical shared card or create their own through the repository helper; they do not reinterpret a red data gate.

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

If more than three symbols are provisionally option-feasible, select the expensive option-research subset without P&L by this frozen lexicographic ranking:

1. `GREEN_OPTION_RESEARCH` before `PROVISIONAL_AWAITING_LIVE_QUOTES`; yellow/red cannot advance to option promotion work.
2. Higher simultaneous two-leg entry-and-exit proxy coverage on the frozen feasibility sample.
3. Higher `PIT_EXISTENCE_PROXY` coverage.
4. Lower missing-exit/no-proxy-fill rate.
5. Higher underlying 15-minute bar coverage.
6. Final tie-break by `SPY, QQQ, TQQQ, SMH, SOXL, IGV` order.

Record the full ranking and input hashes in `research/shared/selection/feasibility_selection.json`. Monday quote failure removes a frozen symbol from paper eligibility but does not open a search for the fourth-ranked replacement.

## 5. Common hypotheses

The initial scan excludes LLM output, news, IV, Greeks, and packet-owner-specific features.

All rolling calculations are produced upstream by the shared point-in-time feature builder. A runtime plug-in receives schema-validated Decimal features, not bars, DataFrames, Alpaca clients, or a clock. Universe keys use `<SYMBOL>__<feature_name>` (for example, `QQQ__momentum_z_60m_same_time_v1`); units, formula, lookback, availability, and missing-data behavior live in the candidate's `feature_contract.yaml`. Any absent, stale, nonfinite, or schema-mismatched required feature produces `NO_TRADE`.

For cross-symbol comparison, every family defines a nonnegative normalized `entry_score` whose central entry threshold is `1.0`. Score normalization is portfolio arbitration, not a probability or confidence estimate.

Common clock:

For hackathon V1, strategy plug-ins emit entries or `NO_TRADE` only. The central registry binds each plug-in to one deterministic position-policy ID, and the central position manager—not researcher code—creates close intents/plans. The pinned host implements the named policy decisions and reduce-only construction; research must replay that exact policy. A candidate cannot reach `INTEGRATION_READY` until its close fixtures pass, and it cannot reach paper use until the release owner proves the durable broker/fill/position/flatten lifecycle.

- Normalize one-minute Alpaca IEX bars into ET half-open intervals `[09:30 + 15k, 09:45 + 15k)`. Aggregate open=first, high=max, low=min, close=last, volume=sum, and interval VWAP=`sum(minute_vwap × minute_volume) / sum(minute_volume)`. An interval with a missing minute, missing VWAP, or zero cumulative volume is invalid for a decision.
- Session IEX VWAP at `t` is the same volume-weighted calculation over all valid one-minute bars from 09:30 through the completed interval at `t`; do not substitute typical price or an unweighted average.
- Label each 15-minute interval by its end and set its effective availability to `interval_end + 1 second`.
- Evaluate new entries at 10:30:01, 11:00:01, 11:30:01, 12:00:01, 12:30:01, 13:00:01, 13:30:01, 14:00:01, and 14:30:01 ET; evaluate open-position management at every completed 15-minute interval plus one second.
- The underlying execution proxy is the open of the first one-minute IEX bar whose interval starts on the next whole minute after the effective decision/exit time. The option proxy follows the common one-minute/five-minute rule in Section 4D. Store both requested and source timestamps.
- One position per symbol and no overlapping labels. The central hard exit order triggers at `min(confirmed_fill_time + 60 minutes, 15:45 ET)` independently of the 15-minute signal-management loop; proxy/runtime execution follows at the next eligible observation/order event.
- Never hold overnight in V1.
- Position age begins at the confirmed proxy/runtime fill timestamp, not signal or order-submission time.
- VWAP exits use completed-bar close only, never intrabar high/low. A bullish adverse cross is `close <= session_vwap`; bearish is `close >= session_vwap`. A bullish VWAP-reversion touch is `close >= session_vwap`; bearish is `close <= session_vwap`.
- Strategy-level premium profit targets and price-based stop-loss exits are disabled in competition V1. Alpha exits are exactly the VWAP and hard-time rules above, frozen before the first 2025 OOS run. Portfolio daily/competition stops and stale/reconciliation/orphan remediation remain mandatory safety overrides; record them separately and never tune them as alpha exits.
- Exclude Alpaca-calendar early-close sessions from both research scoring and competition entries with `EARLY_CLOSE_SESSION`; do not apply impossible fixed full-session times.
- Thursday's final-session rule overrides normal cadence: no new entries after 13:30 ET, start programmatic flatten by 15:15 ET, and require broker-confirmed flat by 15:30 ET.

### Matched null control

For every real signal, use only the synchronized centered five-session date-block bootstrap frozen in Section 9. There is no per-trade sign-permutation alternative. This measures market drift/small-sample luck and supplies the family-wise maximum-statistic control across every viewed candidate.

### Normalized intraday continuation

At decision time `t`:

- `r60_t = log(close_t / p_{t-60m})`, where `p_{t-60m}` is the completed 15-minute close exactly 60 minutes earlier, except at 10:30 when it is the 09:30 session-open price; never cross the prior close or an overnight boundary;
- over the prior 20 valid completed sessions at the same time of day, compute `mu_r60` and sample standard deviation `sigma_r60`;
- require all 20 prior observations and form `momentum_z = (r60_t - mu_r60) / max(sigma_r60, 1e-6)`;
- compute session IEX VWAP from completed bars only.

Signal:

- bullish when `momentum_z >= 1.0` and close is above session IEX VWAP;
- bearish when `momentum_z <= -1.0` and close is below session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = abs(momentum_z) / 1.0`. Use central exit policy `TREND_VWAP_OR_60M_V1`: exit at the common hard-time deadline, or earlier on an adverse completed-close VWAP cross.

### Normalized intraday VWAP reversion

At decision time `t`:

- `deviation = log(close_t / session_vwap_t)`;
- over the prior 20 valid completed sessions at the same time of day, compute `mu_deviation` and sample standard deviation `sigma_deviation`;
- require all 20 prior observations and form `deviation_z = (deviation - mu_deviation) / max(sigma_deviation, 1e-6)`.

Signal:

- bullish when `deviation_z <= -1.5` and `abs(momentum_z) < 0.5`;
- bearish when `deviation_z >= 1.5` and `abs(momentum_z) < 0.5`;
- otherwise `NO_TRADE`.

Set `entry_score = abs(deviation_z) / 1.5`. Use central exit policy `REVERSION_VWAP_TOUCH_OR_60M_V1`: exit on a completed-close VWAP touch in the convergence direction or at the common hard-time deadline.

VWAP reversion is a standalone challenger, not an intraday-continuation overlay or simultaneous fallback signal. All three packet owners may write their two strategy cards in parallel. Researchers may run the shared intraday-continuation golden fixture on any supported Windows, Linux, or macOS platform; the central owner records native-ARM64 host compatibility before integration/paper promotion. Architecture never blocks offline research.

### Opening-range breakout with participation confirmation

Define the first 30 regular-session minutes as `[09:30, 10:00)` ET:

- `or_high` and `or_low` are the maximum high and minimum low of those 30 one-minute IEX bars;
- `or_width_log = max(log(or_high / or_low), 1e-6)`;
- at a decision time `t >= 10:30:01`, `up_break_fraction = log(close_t / or_high) / or_width_log` and `down_break_fraction = log(or_low / close_t) / or_width_log`;
- `volume_ratio_t` is current completed 15-minute IEX volume divided by the median volume of the prior 20 valid sessions at the same time of day.

Signal:

- bullish when `up_break_fraction >= 0.10`, `volume_ratio_t >= 1.25`, and close is above session IEX VWAP;
- bearish when `down_break_fraction >= 0.10`, `volume_ratio_t >= 1.25`, and close is below session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = min(break_fraction / 0.10, volume_ratio_t / 1.25)` in the signaled direction. Permit only the first valid opening-range-breakout entry per symbol per session; a later re-break is not a new trial. Use central exit policy `TREND_VWAP_OR_60M_V1`.

Falsification focus: remove the volume/VWAP confirmation one at a time, check whether one opening-gap regime dominates, and prove that next-observation execution plus option costs does not erase the apparent breakout.

### Standardized overnight-gap continuation

This family evaluates only at `10:30:01 ET`:

- `gap = log(open_0930 / prior_regular_session_close)` on the split-adjusted continuous series, with both source prices separately retained in the raw audit record;
- over the prior 60 valid completed sessions, compute sample `sigma_gap` and `gap_z = gap / max(sigma_gap, 1e-6)`; prior mean/median are reported diagnostically but are not subtracted from direction;
- `first_hour_directional_return = sign(gap) * log(close_1030 / open_0930)`;
- `continuation_ratio = first_hour_directional_return / max(abs(gap), 1e-6)`.

Signal:

- direction is the sign of the raw adjusted `gap` when `abs(gap_z) >= 1.0`, `continuation_ratio >= 0.25`, and the 10:30 close is on the same side of session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = min(abs(gap_z) / 1.0, continuation_ratio / 0.25)`. There is at most one gap-continuation decision per symbol per day. Use `TREND_VWAP_OR_60M_V1`.

Falsification focus: report gap-up and gap-down results separately, exclude split/corporate-action discontinuities, compare leveraged and unleveraged pairs at equal maximum loss, and test whether a few macro-gap dates explain the result.

### Benchmark-residual relative strength

Freeze this benchmark map before results:

| Target | Benchmark |
|---|---|
| QQQ | SPY |
| TQQQ | QQQ |
| SMH | QQQ |
| SOXL | SMH |
| IGV | QQQ |
| SPY diagnostic | QQQ |

At each common decision time:

- calculate target and benchmark `r60` using the exact intraday-continuation clock;
- using the prior 60 valid same-time session pairs only, estimate `beta_t = cov(r_target, r_benchmark) / max(var(r_benchmark), 1e-8)`;
- apply that frozen-at-`t` beta to the same prior pairs, compute residual mean/standard deviation, and form `residual_z = (r_target_t - beta_t * r_benchmark_t - mu_residual) / max(sigma_residual, 1e-6)`.

Signal:

- bullish when `residual_z >= 1.25` and target close is above its session IEX VWAP;
- bearish when `residual_z <= -1.25` and target close is below its session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = abs(residual_z) / 1.25` and use `TREND_VWAP_OR_60M_V1`. Missing benchmark data invalidates the target decision. SPY is a diagnostic symmetry check and is not promotion-eligible for this relative-strength version.

Falsification focus: show raw target momentum beside residual momentum, test beta stability, and verify that TQQQ/SOXL do not win merely because leverage was left unnormalized.

### Intraday compression followed by expansion

Compression breakout starts at `11:00:01 ET`. At decision time `t`, exclude the just-completed decision interval from the compression box:

- the box spans the four completed 15-minute intervals `[t-75m, t-15m)`;
- `box_range_log = max(log(box_high / box_low), 1e-6)`;
- `compression_ratio = box_range_log / median_box_range`, where the denominator uses the prior 20 valid sessions at the same decision time;
- `up_break_fraction = log(close_t / box_high) / box_range_log` and `down_break_fraction = log(box_low / close_t) / box_range_log`;
- `volume_ratio_t` uses the same definition as opening-range breakout.

Signal:

- bullish when `compression_ratio <= 0.65`, `up_break_fraction >= 0.10`, `volume_ratio_t >= 1.25`, and close is above session IEX VWAP;
- bearish under the symmetric lower-box and below-VWAP conditions;
- otherwise `NO_TRADE`.

Set `entry_score = min(0.65 / max(compression_ratio, 1e-6), break_fraction / 0.10, volume_ratio_t / 1.25)`. Permit only the first compression-breakout entry per symbol per session and use `TREND_VWAP_OR_60M_V1`.

Falsification focus: prove that the result is not an opening-range-breakout duplicate, report the overlap in trade timestamps with intraday-continuation and opening-range-breakout signals, and require incremental portfolio evidence rather than counting correlated signals as independent discoveries.

### Prescribed stability sensitivities

Sensitivities are diagnostic, run one dimension at a time, and are not per-symbol optimization:

- Intraday-continuation `abs(momentum_z)` threshold: central `1.00`; diagnostics `0.75`, `1.25`.
- VWAP-reversion `abs(deviation_z)` threshold: central `1.50`; diagnostics `1.25`, `1.75`.
- Opening-range-breakout fraction: central `0.10`; diagnostics `0.05`, `0.15`. Volume ratio: central `1.25`; diagnostics `1.00`, `1.50`.
- Gap-continuation `abs(gap_z)`: central `1.00`; diagnostics `0.75`, `1.25`. Continuation ratio: central `0.25`; diagnostics `0.00`, `0.50`.
- Relative-strength `abs(residual_z)`: central `1.25`; diagnostics `1.00`, `1.50`.
- Compression-breakout ratio: central `0.65`; diagnostics `0.50`, `0.80`. Breakout fraction: central `0.10`; diagnostics `0.05`, `0.15`.
- All families' time exit from confirmed fill: central `60` minutes; diagnostics `45`, `90` minutes.

The central specification is the only promotion-eligible value. Owners publish every prescribed neighbor and may not report only the best run. Any unlisted value is a new candidate and counts in the selection family if its result is viewed.

## 6. Common option expressions

Evaluate the underlying signal first. The option layer expresses the frozen direction and cannot rescue or reverse it.

Research and runtime load the same committed `configs/template_catalog.yaml`; every run and order plan records its canonical hash. The catalog freezes family, DTE bucket, moneyness/width construction, ranking, atomicity, and exit rules. The advisory agent is veto-only at this boundary: it cannot change expression family, strike-width policy, DTE, selector ranking, or size. Current quotes and Greeks may reject an otherwise selected candidate or serve as diagnostics, but may not retrofit a different historical selection rule.

The catalog and selector are published host interfaces, but a candidate's option-proxy evidence is not runtime parity by assertion. Owners may complete option-proxy research using this frozen policy and must label `catalog_parity=CANDIDATE_NOT_RUN` until central fixtures prove identical DTE, strikes, fees, quantity, maximum loss, and refusal reasons. No candidate becomes `INTEGRATION_READY` or `PAPER_CANDIDATE` without that proof.

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

## 6A. Exact strategy-to-system integration contract

Researchers implement a pure semantic decision plug-in. They do **not** implement Alpaca retrieval, option-symbol selection, sizing, pricing, risk approval, order submission, position reconciliation, or lifecycle promotion.

### Required Python surface

The candidate exposes one importable `Plugin` implementing `StrategyPluginV1`:

```python
class Plugin:
    @property
    def metadata(self) -> StrategyMetadataV1: ...

    def data_requirements(self, config: StrategyConfigV1) -> DataRequirementsV1: ...

    def evaluate(
        self,
        context: StrategyContextV1,
        config: StrategyConfigV1,
    ) -> StrategyEvaluationV1: ...
```

Current V1 implementation constraints that every owner must design around:

- `plugin_id` is the assigned slug in Section 3, `plugin_version` is exactly `1.0.0` for the first handoff, `api_version` is `strategy-plugin/v1`, and `decision_schema_version` is `strategy-evaluation/v1`.
- `StrategyConfigV1.values` is a flat map of `Decimal | str | int | bool`; no list or nested config is allowed.
- `StrategyContextV1.universe_features` and `option_surface_summaries` are flat Decimal maps. The plug-in receives no raw bars or provider objects.
- The research handoff requests only `(CALL_DEBIT_SPREAD_V1, INTRADAY_15_60M, TINY, 300)` and `(PUT_DEBIT_SPREAD_V1, INTRADAY_15_60M, TINY, 300)`. `STANDARD` is structurally supported but is not researcher-authorized; the release owner may add it only through a new registry review.
- Entry output may request `CALL_DEBIT_SPREAD_V1` or `PUT_DEBIT_SPREAD_V1` for promotion-eligible V1. Long-call/put templates remain diagnostic/demo only unless separately approved.
- A valid entry uses the bullish call-debit or bearish put-debit tuple unchanged, sets `intent_expires_at = context.as_of + 300 seconds`, and includes exactly one `FEATURE_VECTOR` evidence reference bound to the input vector hash/ID. An unavailable tuple returns `TEMPLATE_NOT_ALLOWED`; it never substitutes another tuple.
- `DataRequirementsV1.maximum_observation_age_seconds` is exactly `60`, `needs_logical_positions=false`, and `required_feature_keys` are ordered first by frozen symbol order `SPY, QQQ, TQQQ, SMH, SOXL, IGV`, then lexicographically by feature key. Each owner computes the candidate-specific feature-contract hash before outcome P&L and uses it in every context, manifest, and integration proposal.
- Effective runner limits are two seconds wall time, one CPU second, 256 MiB address space where enforceable, and 128 KiB combined response/diagnostic policy. A strategy should perform only constant-time comparisons over precomputed features.
- The committed `regime_momentum_v1` is a fixture, not an intraday-continuation reference implementation: it ignores most documented continuation logic and must not be copied as research truth.

### Feature handoff

Each owner submits `feature_contract.yaml`. Every required feature entry includes:

```yaml
name: QQQ__momentum_z_60m_same_time_v1
dtype: decimal_string
unit: z_score
source: alpaca_stock_bars
feed: iex
formula: preregistered exact expression
lookback: 20 valid prior same-time sessions
event_time_rule: completed source intervals only
available_time_rule: interval_end_plus_1_second
maximum_age_seconds: 60
missing_behavior: NO_TRADE
quality_flags_allowed: []
worked_example_ref: sha256:...
```

Names are immutable API fields. A formula, unit, lookback, clock, or missing-value change requires a new feature name/version and a new candidate run; silently keeping the old name invalidates parity. The platform owner implements the shared feature builder and validates `data_requirements()` before evaluation.

### Allowed output

For each evaluation, the plug-in returns exactly one:

- `NoTradeV1` with a closed, candidate-declared reason code; or
- `EntryTemplateRequestV1` containing only underlying, allowed template, horizon, risk tier, strength bucket, expiry no more than the supplied tuple TTL, reason codes, and immutable evidence references.

The plug-in must never emit or encode an option symbol, strike, exact option expiration, leg, quantity, price, limit, time in force, account/buying power, maximum-loss amount, client/broker ID, order class, or broker operation. It may not hide executable instructions in reason codes, state, record IDs, or prose.

Required stable reason-code families include:

- data: `DATA_MISSING`, `DATA_STALE`, `DATA_QUALITY_REJECTED`, `FEATURE_SCHEMA_MISMATCH`;
- session: `OUTSIDE_DECISION_WINDOW`, `EARLY_CLOSE_SESSION`, `DAILY_ENTRY_ALREADY_USED`, `COOLDOWN_ACTIVE`;
- selection: `NO_SIGNAL`, `NOT_SELECTED_BY_ARBITRATION`, `NOT_SELECTED_BY_FEASIBILITY`, `DIRECTION_AMBIGUOUS`;
- signal: `<HYPOTHESIS>_GATE_NOT_MET`, `<HYPOTHESIS>_BULLISH`, `<HYPOTHESIS>_BEARISH`, `BENCHMARK_MISSING`;
- authority: `UNDERLYING_NOT_ALLOWED`, `TEMPLATE_NOT_ALLOWED`, `INTENT_TTL_INVALID`, `TUPLE_NOT_ALLOWED`.

Each packet freezes its exact `<HYPOTHESIS>` substitutions. `reason_codes.yaml` must declare every code, decision kind, description, and retry behavior; the implementation may emit no undeclared code. Reviewer checks that none encodes executable values or changes execution semantics. Host/registry refusal codes remain platform-owned and must not be imitated by plug-in output.

### Required output bindings and state

The plug-in must copy the request's evaluation ID, context hash, config hash, registered plug-in ID/version, and evaluation time exactly. Source authority is host-owned: plug-in code emits `packages.strategy_sdk.UNBOUND_PLUGIN_CONTENT_HASH`, and the isolated host replaces/checks it against the registry-pinned source digest. A plug-in must not hard-code a self-authorizing digest. `next_state` must:

- belong to the same plug-in ID/version;
- set `sequence = prior_state.sequence + 1`;
- use `as_of = context.as_of`;
- be deterministic and schema-limited;
- contain only small string fields needed for explicit one-entry-per-day state.

For all six entry-only families, `state_schema.json` freezes `state_schema_version=strategy-state/v1`, initial `sequence=0`, and initial `payload={}`. The only permitted payload keys are `last_entry_session_<SYMBOL>` with ISO `YYYY-MM-DD` string values for symbols the family evaluated. Keys are sorted by frozen symbol order; a `NO_TRADE` preserves payload, while an emitted entry updates only its selected symbol. A family that does not need daily-entry state must still increment sequence and preserve `{}`. `PositionDirectiveV1`, hidden module/class state, timestamps read from the clock, and arbitrary payload keys are forbidden.

The host—not the plug-in—must recheck all bindings, the centrally computed package hash, lifecycle/mode, allowed underlying/tuple, TTL, state sequence/hash, and metadata/data-requirement match. A mismatch becomes a stable refusal and never reaches planning.

### Backtest/runtime parity rule

Put the economic decision in a small pure function in `src/<plugin_id>_v1/signal.py`; both the package's offline reproduction adapter and `Plugin.evaluate()` call that function on the same normalized feature/config row. For at least 20 frozen timestamps per candidate, `integration/backtest_runtime_parity.json` records:

- canonical feature/context/config hashes;
- expected direction and normalized score;
- expected `NO_TRADE` reason or exact semantic entry tuple;
- expected next-state sequence/payload;
- canonical evaluation hash after the registered content hash is injected.

The package tests run every case twice and require byte-identical canonical output. The release owner later reruns the same golden cases through the repository's isolated runner. Minimum cases are bullish, bearish, below threshold, equality at threshold, conflicting gates, missing feature, stale/quality-flagged data, forbidden underlying, missing tuple, excessive TTL, repeated daily entry/cooldown, and state-sequence mismatch. The published host baseline command is in `RESEARCH_INTERFACE_FREEZE.md`; a package records `host_interface_baseline=PASSED_AT_cb03a76` when it passes and `candidate_host_conformance=NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED` until its own cases run through the host.

### Central integration pipeline

```text
candidate research artifacts and non-author review
→ registry candidate remains research_only
→ source/config/feature/catalog/evidence hashes frozen
→ shared conformance and backtest/runtime parity
→ complete portfolio replay through exact selector/sizer/position policy
→ NO_TRADE + risk-rejection + approved/fake-fill + close/flatten fixtures
→ paper_candidate review
→ release owner may set exact version/hash to paper_enabled
```

Adding a plug-in directory or reporting a profitable backtest never authorizes it. Researchers submit `integration/registry_candidate.yaml`; only the central registry owner merges lifecycle and authority fields.

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

Fee assumptions are stress-model parameters, not claimed Alpaca commissions or market data. Before viewing results, freeze the central assumption at **$0.10 per contract, per leg, per side**. Publish sensitivities at $0.00 and $0.25 on the same basis, plus the severe 2× central-cost scenario. Never select the fee or tick proxy that makes P&L positive; every output is labeled `bar_proxy_supported`, `bar_proxy_suggestive`, or `bar_proxy_unsupported`, never “filled,” “executable,” or “execution-quality evidence.”

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

Frozen partitions, clipped only by a shared documented Alpaca coverage failure:

- underlying discovery/warm-up: `2017-01-03T14:30:00Z` through `2023-12-29T21:00:00Z`;
- option feasibility/proxy calibration: `2024-02-01T14:30:00Z` through `2024-12-31T21:00:00Z`;
- OOS fold `2025Q1`: `2025-01-02T14:30:00Z` through `2025-03-31T20:00:00Z`;
- OOS fold `2025Q2`: `2025-04-01T13:30:00Z` through `2025-06-30T20:00:00Z`;
- OOS fold `2025Q3`: `2025-07-01T13:30:00Z` through `2025-09-30T20:00:00Z`;
- OOS fold `2025Q4`: `2025-10-01T13:30:00Z` through `2025-12-31T21:00:00Z`;
- final validation: `2026-01-02T14:30:00Z` through cutoff `2026-08-27T20:00:00Z`.

The ET decision clock and exchange calendar determine actual in-session rows; the UTC endpoints above bound the query and prevent timezone ambiguity. Half days remain invalid for research entry. Any source-coverage clipping is applied identically to all six families, recorded before outcomes in `coverage_exception.json`, and reviewed centrally; a family owner may not choose its own start/end dates.

If anyone already inspected 2026 outcomes, label the last period `final_validation`, disclose prior exposure, and do not call it a sealed holdout. A true sealed holdout needs its manifest hash and access gate recorded before inspection.

Champion, preregistered fallback, symbols, thresholds, expression, selector, and all risk/template hashes are selected using data through December 31, 2025 and frozen before any 2026 result is opened. The 2026 period is accept/reject validation only: it cannot tune or reorder candidates. If the champion fails, no previously undesignated runner-up may be promoted; only a fallback explicitly named and frozen before 2026 access may be used, and only if it independently passes the same 2026 accept/reject gate and its failover condition was preregistered.

Walk-forward rules:

- require at least 252 completed experiment-history sessions before the first OOS fold; feature values use the exact preregistered lookback in Section 5—20 valid same-time sessions for intraday continuation, VWAP reversion, opening-range breakout, and compression breakout; 60 for gap continuation and relative strength;
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

- the authorizing test uses complete 2025 OOS daily account returns for every promotion-eligible central strategy × compatible feasible-symbol scope × O2 candidate, plus any variant actually allowed to influence selection; it excludes 2024 calibration and never pools post-selection 2026 results;
- align every candidate to the same complete market-date index and include zero-return inactive dates;
- for each candidate, center its daily OOS return series under the zero-mean null;
- use NumPy-compatible `PCG64` seed `20260829`, exactly 10,000 synchronized bootstrap replications, and five-session circular moving date blocks; replication IDs are `00000` through `09999` and use the identical sampled date-block indices for every candidate;
- use the identical sampled date-block indices for all symbols, hypotheses, expressions, and selectable variants in each replication, preserving cross-symbol dependence;
- compute each candidate's studentized mean daily return and retain the maximum positive statistic per replication for the one-sided positive-edge family;
- report raw and family-wise adjusted one-sided p-values against that maximum-statistic null; `statistically_supported` and `paper_enabled` require 2025 adjusted `p <= 0.10` on the frozen central specification;
- record every viewed threshold, holding period, symbol, hypothesis, expression, fee/tick choice, and rerun in the trial ledger. O1 and declared sensitivities are excluded from the authorizing family only because policy makes them ineligible to affect selection; if a human uses one to choose or reorder, the freeze is invalid and the new selectable family must include it. Every selectable variant is also included in the deflated-Sharpe trial count.

Only the central preregistered specification is promotion-eligible. Neighboring sensitivities diagnose stability and cannot replace it. Any exception creates a new preregistered experiment and forfeits the current holdout label.

Predeclared selection sequence using data through December 31, 2025 only:

1. Candidate passes every data and falsification gate.
2. Total OOS net percentage return is positive under base conservative costs and nonnegative under the severe/2×-cost run.
3. At least 60% of populated quarterly folds are positive.
4. A candidate called `statistically_supported` or `paper_enabled` must pass the frozen family-wise adjusted positive-edge test; otherwise it may remain `suggestive`, shadow, or explicit demo-only.
5. Among evidence-backed candidates, rank by the preregistered deflated/selection-adjusted Sharpe probability on complete daily OOS returns. Values within `0.02` are treated as tied rather than economically distinct.
6. Tie-break on higher median quarterly net percentage return, then lower maximum drawdown, then higher 2024–2025 simultaneous two-leg proxy coverage, then lexicographically by stable candidate ID.

Do not select a winner independently inside each symbol or researcher report and then present six winners. Selection is over the complete strategy-family portfolio candidates, with every viewed trial retained.

Monday prospective quotes are accept/reject operational gates only. They may produce `NO_TRADE` or reject a frozen candidate, but they never re-rank the champion/fallback or select a replacement.

After candidates are frozen, the 2026 accept/reject gate requires for each predesignated candidate: positive base-cost normalized-account return, nonnegative severe/2×-cost return, maximum drawdown no greater than 4%, no data/falsification failure, and the exact trade/day concentration limits below. Report a fixed-candidate 2026 interval and p-value descriptively; it is not pooled into or substituted for the authorizing 2025 adjusted test. Failure rejects that candidate; it does not open a search.

## 10. Metrics

All metric implementations use complete exchange-date rows and the following frozen definitions. Let normalized account equity be `E_d`, with initial `E_0 = 100000`, no external cash flow, and daily net return `r_d = E_d / E_(d-1) - 1`. Inactive valid dates have `r_d = 0`; an invalid/unscorable date is retained with a quality flag and does not silently become zero. Unless stated otherwise, `N` is the number of complete OOS market dates, sample standard deviation uses `ddof=1`, annualization is 252 sessions, and the risk-free rate/minimum acceptable return is zero.

- cumulative return: `E_N / E_0 - 1`;
- annualized geometric return: `(E_N / E_0) ** (252 / N) - 1` when `N > 0`;
- Sharpe: `sqrt(252) * mean(r_d) / sample_std(r_d)`, or `UNDEFINED_ZERO_VARIANCE`;
- Sortino: `sqrt(252) * mean(r_d) / sqrt(mean(min(r_d, 0) ** 2))`, or `UNDEFINED_ZERO_DOWNSIDE`;
- drawdown: `E_d / running_max(E_d) - 1`; maximum drawdown is the minimum drawdown, reported as both signed value and positive magnitude;
- Calmar: annualized geometric return divided by positive maximum-drawdown magnitude, or `UNDEFINED_ZERO_DRAWDOWN`;
- historical 95% expected shortfall: negative mean of returns at or below the empirical 5th percentile; interpolation method must be recorded;
- hit rate: scored trades with strictly positive net P&L divided by scored trades; zero P&L is not a win;
- profit factor: positive net trade P&L sum divided by absolute negative net trade P&L sum, with zero-loss denominator reported as undefined/infinite explicitly;
- turnover: sum of absolute opening and closing premium notionals divided by mean daily normalized equity;
- exposure time: total position-open minutes divided by total eligible regular-session minutes;
- top-trade concentration: largest positive trade P&L divided by total positive trade P&L; top-day concentration is analogous on daily net P&L;
- SPY beta: sample covariance of candidate and SPY complete-date returns divided by sample variance of SPY; Pearson correlation uses the same synchronized dates.

JSON uses decimal strings for exact monetary/return fields and sorted keys. Charts may render floats but never become metric authority.

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
- synchronized centered moving-block null percentile; no separate per-trade permutation test.

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

Shared data/feasibility evidence is stored once; every **strategy-family owner** returns the same candidate and plug-in package:

```text
research/
├── shared/
│   ├── entitlement_probe.json
│   ├── trial_ledger.jsonl
│   ├── datasets/<dataset_id>/data_manifest.json
│   ├── selection/
│   │   ├── option_proxy_feasibility_manifest.json
│   │   └── candidate_selection.json
│   └── symbol_feasibility/<symbol>/
│       ├── data_quality.json
│       ├── contract_universe_manifest.json
│       ├── pit_existence_proxy.parquet
│       ├── feasibility_card.json
│       └── feasibility_card.md
└── candidates/<candidate_id>/
    ├── strategy_card.md
    ├── hypothesis.yaml
    ├── feature_contract.yaml
    ├── central_config.json
    ├── sensitivities.yaml
    ├── reason_codes.yaml
    ├── state_schema.json
    ├── data_refs.json
    ├── runs/<run_id>/
    │   ├── run_manifest.json
    │   ├── pair_cell_metrics.json
    │   ├── pair_cell_review.json
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
    ├── integration/
    │   ├── registry_candidate.yaml
    │   ├── golden_contexts/
    │   ├── golden_evaluations/
    │   ├── backtest_runtime_parity.json
    │   ├── conformance_report.json
    │   ├── catalog_parity.json
    │   └── integration_checklist.md
    └── promotion_card.md

strategy_plugins/<plugin_id>_v1/
├── pyproject.toml
├── manifest.yaml
├── README.md
├── hypothesis.yaml
├── defaults.yaml
├── src/<plugin_id>_v1/
│   ├── __init__.py
│   ├── plugin.py
│   ├── signal.py
│   ├── reason_codes.py
│   └── reproduce.py
├── scripts/
│   ├── reproduce.sh                 # optional POSIX wrapper
│   └── reproduce.ps1                # optional PowerShell wrapper
├── tests/
│   ├── fixtures/
│   ├── golden/
│   ├── test_contract.py
│   ├── test_thresholds.py
│   ├── test_no_trade.py
│   ├── test_determinism.py
│   ├── test_boundary.py
│   └── test_parity.py
└── evidence/
    └── promotion.json
```

This is the canonical core layout from `docs/architecture/STRATEGY_API.md`; the Python reproduction module, threshold/parity tests, and pair-cell evidence are required research extensions. Do not return the current fixture-style flat package as a candidate package.

`strategy_card.md` freezes the economic mechanism, eligible symbols, exact entry formula, normalized score, decision cadence, position-policy ID, option-template mapping, no-trade conditions, expected failure regimes, and one-sentence reason the edge could persist after costs.

`hypothesis.yaml` records candidate/hypothesis IDs, owner/reviewer, preregistration time, central parameters, prescribed sensitivities, discovery/OOS/final periods, null, primary metric, falsification thresholds, and hashes of the feature/selector/cost policies.

`data_manifest.json` records endpoint/tool/version, explicit feed, scrubbed query, requested/returned coverage, page completion, row count, fetch time, raw/normalized hashes, missingness, rate-limit/errors, and adjustment type. `data_refs.json` references those immutable shared hashes; candidates do not copy or mutate raw data.

`run_manifest.json` records the pinned implementation commit and lock hash, config/data/hypothesis/expression hashes, exact `template_catalog.yaml` and feasibility-manifest hashes, fold boundaries, one-minute/five-minute proxy timing rules, tick/fee assumptions, `PCG64` seed `20260829`, bootstrap specification, all viewed and tried variants, start/end time, status, owner, and reviewer.

`src/<plugin_id>_v1/reproduce.py` is supplied by the family owner and is the canonical research reproduction entry point. It must make no network or credential call, accept exactly `--data-manifest PATH --feasibility-manifest PATH --output PATH`, refuse a nonempty output directory, validate the pinned commit/lock/config/data hashes, run the package tests, and write one deterministic run tree. It may invoke repository libraries but must not claim an absent central backtester. Optional `scripts/reproduce.sh` and `scripts/reproduce.ps1` wrappers invoke this same module and contain no different logic. The package README shows this invocation literally:

```text
uv run python -m <plugin_id>_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

Researchers use these platform-neutral repository verification commands:

```text
uv sync --frozen
uv run python -m pytest
uv run ruff check .
```

Researchers may quote those commands as baseline/conformance evidence only for what they actually test. They must not relabel them as a shared historical backtest command.

Minimum tabular schemas:

- `signals.parquet`: candidate/plugin ID/version, symbol, UTC decision time, feature/context/config hashes, eligibility, direction, normalized entry score, decision kind, reason code, requested semantic tuple, position-policy ID, portfolio-selected/suppressed status, and quality flags.
- `selected_contracts.parquet`: signal ID, point-in-time contract evidence hashes, call/put, expiration/DTE, raw spot, long/short strikes, ranking fields, template/catalog hash, and selection/rejection reason. It contains research selections, never broker authority.
- `proxy_leg_observations.parquet`: requested/source timestamps, option symbol, side, OHLC/trade fields, coverage status, base/severe buy/sell proxies, and raw artifact hash.
- `trades.parquet`: signal/portfolio IDs, requested/observed entry and exit times, quantity, fee-inclusive debit/max loss, liquidation proxy, base/severe P&L and returns, exit-policy/reason, and missing-mark/no-fill penalty.
- `daily_returns.parquet`: every market date including zero-return inactive dates, start/end normalized equity, gross/net P&L, return, maximum reserved loss, exposure minutes, trade count, and data-quality status.

`metrics.json` contains the exact metrics in Section 10 for the central run and explicit evidence status; `cost_stress.json` contains every prescribed fee/tick/missing-exit scenario, never just the favorable one.

Deterministic serialization is part of the handoff:

- all timestamps are UTC RFC 3339 with `Z`; dates are ISO `YYYY-MM-DD`;
- canonical JSON is UTF-8, keys sorted recursively, no insignificant whitespace, decimal values encoded as strings, and one terminal newline;
- JSONL rows are ordered by `(candidate_id, symbol_order, decision_time, variant_id, record_id)` and each line is canonical JSON;
- Parquet files use explicit Arrow types and fixed column order from `artifact_schema.json`, UTC timestamps, stable row-group size `65536`, and the same applicable sort keys;
- symbol order is always `SPY, QQQ, TQQQ, SMH, SOXL, IGV`; nulls sort last; reason-code sets are emitted lexicographically;
- plots are non-authoritative; hashes cover the tabular/JSON inputs and plot specifications, not renderer metadata;
- a second run must produce identical authoritative artifact hashes. Nondeterministic metadata must live outside hashed evidence or be normalized.

`registry_candidate.yaml` is a **non-authorizing proposal** with lifecycle `research_only`, exact plug-in/config/feature/state/evidence hashes, requested underlyings and intent tuples, position-policy ID, owner, and reviewer. Researchers never edit `paper_enabled` in the central registry.

`backtest_runtime_parity.json` and golden directories contain the cases in Section 6A. `catalog_parity.json` proves that portfolio replay and runtime select identical contracts, quantities, maximum loss, and refusal reasons from identical frozen inputs.

`promotion_card.md` contains every gate with artifact reference, falsification attempts, overlap/correlation with other candidates, known limitations, owner/reviewer sign-off, and exactly one permitted state: `REJECTED`, `RESEARCH_COMPLETE`, `INTEGRATION_READY`, `PAPER_SHADOW`, `PAPER_DEMO_ONLY`, or `PAPER_CANDIDATE`. It cannot declare `PAPER_ENABLED`.

### Researcher definition of done

Before requesting integration review, the owner and non-author reviewer answer **yes** to all of these:

- The central hypothesis and all viewed trials are in the trial ledger.
- All inputs come from Alpaca-only immutable manifests (self-collected through the repository helper or shared) and pass availability-time checks.
- The run reproduces from one documented platform-neutral `uv` command and exact commit/lock/config hashes; the manifest records its OS and CPU architecture.
- Central and every prescribed sensitivity result are published for both owned pair cells; an unselected option-proxy cell carries the explicit `NOT_SELECTED_BY_FEASIBILITY` artifacts.
- `pair_cell_metrics.json` is labeled diagnostic and does not claim a full-universe candidate, champion, fallback, or symbol winner.
- The family package can be replayed centrally without changing `signal.py`; only the central owner produces the later full-universe replay with suppressed signals, zero-return dates, integer contracts, cluster/concurrency limits, exact selector/sizer, and exact exit policy.
- Base and severe costs, missing observations, no-fill cases, concentration, drawdown, and selection-adjusted evidence are reported.
- `signal.py` and `Plugin.evaluate()` make identical semantic decisions on every parity row.
- Package tests produce byte-identical output twice and every negative/boundary test passes; record the host baseline separately from candidate host conformance, which remains `NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED` until centrally executed.
- Output contains no exact order authority, hidden I/O, or self-promotion.
- Reviewer independently reproduces artifact hashes and records deviations; unresolved deviation means not done.

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

- Release captain reviews and commits the implementation baseline; no researcher branches from the current untracked workspace state.
- Platform owners close or explicitly track `G-R1`–`G-R6`, publish the registry/catalog/feature/reason/position-policy schemas, and pin only conformance commands that actually exist.
- Each collection owner runs and preserves a hash-bound entitlement probe; researchers may reuse a teammate's immutable collection.
- Freeze feed, timestamp, contract, and proxy rules.
- Publish the common scanner outputs and artifact schema. Each family package implements the same frozen clock/metric rules in its required offline reproduction script; parity fixtures expose semantic drift.
- Collect the six underlying histories through the repository helper, once per shared collection or independently when a researcher needs their own evidence set.
- Assign one packet per Section 3. Each packet owner writes two separate sets of `strategy_card.md`, `hypothesis.yaml`, `feature_contract.yaml`, and central/sensitivity configs before viewing P&L for either family.

### Saturday afternoon

- Complete six historical feasibility cards and generate the hash-bound `READY_FOR_REPLAY` feasibility manifest before any result exposure; signatures are optional provenance.
- Remove `RED_REMOVE` symbols.
- Implement all six pure `signal.py` functions and boundary fixtures against the frozen feature rows.
- Run the intraday-continuation golden underlying fixture on each available researcher platform and record hash comparisons. A platform difference is investigated and documented; it never blocks offline research. The central owner separately verifies native-ARM64 host compatibility before integration/paper promotion.
- Run all six central underlying strategy scans on each packet's two owned cells; log every run. Do not call these pair-cell scans full-universe candidates.
- Begin point-in-time contract-existence proxy construction.
- Researchers may implement complete plug-in packages in parallel, but nothing is called `INTEGRATION_READY` until its candidate-specific registry, feature, catalog, output-binding, close-policy, and reviewer evidence passes.

### Sunday morning

- Deep-test option expressions only for the at-most-three symbols named in the frozen blinded feasibility manifest. Emit `NOT_SELECTED_BY_FEASIBILITY` artifacts for every other owned cell.
- Cut O1/O2 where mark coverage fails.
- Run every pair-cell family package through quarterly walk-forward, purge/embargo, base/severe costs where eligible, null, concentration, and risk-adjusted controls. The central owner runs the full-universe portfolio and multiplicity comparison only after all packages freeze.
- Run every prescribed sensitivity as a diagnostic and publish it; do not use a neighbor to replace the central candidate.
- Complete plug-in conformance, runner determinism, golden contexts/evaluations, and semantic backtest/runtime parity where platform gates permit.

### Sunday afternoon

- Named reviewers independently reproduce candidate manifests, hashes, central metrics, at least one negative fixture, and catalog parity.
- Quant lead compares complete portfolio candidates—not per-packet-owner headline metrics—and records the full selection table.
- Using data through 2025 only, freeze one champion and at most one operational fallback by 18:00 ET, including its explicit session-boundary failover condition.
- Only after that freeze, open 2026 once for accept/reject validation; do not tune, reorder, or designate a new fallback from that result.
- If none passes, use `NO_TRADE` or separately authorized minimum-risk `paper_demo_only` without an alpha claim.
- Freeze strategy rules, symbol scope, feature/config/source/catalog/position-policy/evidence hashes, risk profile, and exact release candidate.

### Monday

- Capture the prospective indicative quote-quality gate.
- Start in shadow. Arm only after chain, quote, account, research, registry/catalog parity, maximum-loss/preflight, close/flatten/reconcile, control, credential, and release gates all pass.
- If any P0 architecture finding remains, do not connect or arm the judged account; demonstrate deterministic replay and continue `PAPER_DEMO_ONLY` work on a development account/fixture.
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
- [Current option snapshots API](https://docs.alpaca.markets/us/reference/optionsnapshots)
- [Current option-chain snapshots API](https://docs.alpaca.markets/us/reference/optionchain)
- [Option-contract enumeration API](https://docs.alpaca.markets/us/reference/get-options-contracts-1)
- [Historical stock bars API](https://docs.alpaca.markets/us/reference/stockbars)
- [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca multi-leg options behavior and restrictions](https://docs.alpaca.markets/us/v1.4.2/docs/options-level-3-trading)
- [Paper-trading behavior and simulation limitations](https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading)
