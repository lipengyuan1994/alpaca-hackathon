# Group A research plan — SPY and QQQ broad-tech controls

Status: independently shareable parallel research packet

Implementation commit: `cb03a7684fb67c6f0888333f6c3c2145e8645be9`

Dependency-lock hash: `sha256:b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf`

Assigned capacity: two independent family owners who cross-review without co-authoring

Primary symbol cells: `SPY`, `QQQ`

Assigned strategy families: H1 normalized intraday continuation and H2 normalized intraday VWAP reversion

## 1. Mission and authority boundary

Group A establishes the broad-market and large-cap technology reference results against which the specialized groups are compared. SPY is the broad-market/liquidity control; QQQ is the liquid technology benchmark and the parent control for TQQQ/IGV work.

The group owns the SPY/QQQ data-quality interpretation, feature specifications, H1/H2 pure signal functions, plug-in packages, group cells, artifacts, and review. It does not own Alpaca ingestion, the central registry, option contract selection, sizing, risk approval, order submission, promotion, or judged-account operation.

Researchers work from a source snapshot plus immutable data artifacts. They need no GitHub write credential, Alpaca credential, account ID, broker access, MCP trading tool, deployment secret, or order permission. Return patches/source archives and content-addressed evidence to the platform owner.

A profitable report cannot self-promote. `integration/registry_candidate.yaml` is a non-authorizing proposal with lifecycle `research_only`; only central owners may later change registry authority after every research, integration, safety, and release gate passes.

The signal plug-in never calls an LLM. Any advisory AI may leave a frozen deterministic proposal unchanged or veto it to `NO_TRADE`; it cannot change direction, family, symbol ranking, template, horizon, strike policy, size, executable fields, or lifecycle.

## 2. Team split and exact returns

- **Person 1 / A1 — H1 owner:** owns only `h1_intraday_continuation_v1`. Freeze H1 before outcome access; implement its feature contract, pure signal, canonical plug-in, package-local offline reproduction script, SPY/QQQ pair-cell evidence, prescribed sensitivities, option-proxy status, falsifications, and complete artifact tree. Independently review H2 only after Person 2 freezes it.
- **Person 2 / A2 — H2 owner:** owns only `h2_vwap_reversion_v1`. Freeze H2 before outcome access; implement its feature contract, pure signal, canonical plug-in, package-local offline reproduction script, SPY/QQQ pair-cell evidence, prescribed sensitivities, option-proxy status, falsifications, and complete artifact tree. Independently review H1 only after Person 1 freezes it.

Each person authors one family and signs the other's `pair_cell_review.json`. A reviewer may report a defect but cannot tune or directly repair the reviewed family's economic rule after seeing P&L; the owner versions any outcome-changing correction and repeats affected runs. If one person is unavailable, the remaining person may finish only their own family and must obtain a non-author reviewer from another packet.

Neither member may modify the authoritative registry, install broker credentials, run private market-data downloads, enable paper mode, or approve their own promotion. Stop before work if the checkout or `uv.lock` differs from the pinned values above.

## 3. Exact universe and controls

| Use | Symbols | Rule |
|---|---|---|
| Owned research cells | SPY, QQQ | Complete data/signal/stress/falsification output for both; run option proxy only when the global blinded feasibility manifest selects the symbol. |
| H1 candidate compatibility | SPY, QQQ, TQQQ, SMH, SOXL, IGV | Group A freezes one H1 implementation; central integration runs it unchanged on every compatible feasible symbol. |
| H2 promotion-eligible compatibility | SPY, QQQ, SMH, IGV | Leveraged ETF H2 rows are diagnostic unless a separately frozen version changes that status before outcomes. |
| Broad-market control | SPY | QQQ results must report beta/correlation and active-date overlap with SPY. |
| Technology control | QQQ | QQQ supplies the benchmark inputs consumed by the TQQQ/IGV group; Group A may not tune H1/H2 to improve those downstream results. |
| Statistical null | All viewed candidates on common dates | Use the synchronized centered five-session moving-block maximum-statistic procedure; no per-trade sign permutation. |

This packet returns two separate family packages and their SPY/QQQ `pair_cell_metrics.json` files. Those files are diagnostic evidence only: neither owner may select SPY versus QQQ, declare a champion/fallback, or claim the pair is the complete `CandidateSpecV1`. After all six families freeze, the central quant/release owner expands H1/H2 unchanged to their compatible feasible universe and writes `central_full_universe_replay.json`. That later replay alone applies cross-symbol arbitration and the family-wide selection test.

Before any alpha outcome is viewed, the data steward must sign `research/shared/selection/option_proxy_feasibility_manifest.json`, ranking all six symbols from blinded entitlement, completeness, timestamp, standard-contract, simultaneous-leg, and corporate-action fields. The global `selected_symbols` list has at most three symbols. SPY or QQQ absent from it still receives full underlying research, but its option artifacts are empty schema-valid tables plus `option_proxy_not_selected.json` with exact status `NOT_SELECTED_BY_FEASIBILITY`. Group A cannot swap, rerank, or fill a slot after seeing results.

### 3.1 Frozen cross-symbol arbitration

For each decision time, compute every compatible symbol row with the same pure family function, then:

1. remove rows failing data, feature, session, family, cluster, cooldown, or existing-exposure gates;
2. rank remaining rows by `entry_score - 1.00`, where the central entry threshold is exactly `1.00`;
3. break an exact tie by `SPY, QQQ, TQQQ, SMH, SOXL, IGV` order;
4. map score `[1.00, 1.25)` to `LOW`, `[1.25, 1.75)` to `MEDIUM`, and `>= 1.75` to `HIGH`;
5. send only the winner as a semantic request; if later option/quote/risk checks fail, record `NO_TRADE` with no fall-through;
6. allow at most one new exposure-increasing intent per decision time and one nonterminal position/order;
7. treat QQQ/TQQQ/IGV as one technology cluster and SMH/SOXL as one semiconductor cluster;
8. retain every eligible, rejected, selected, and suppressed row with a reason code.

The pair-cell reproduction script records both per-symbol rows and does not arbitrate a winner. The later central adapter must use `packages/strategy_sdk/arbitration.py` at source-file SHA-256 `864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5`. Option P&L, spread width, current quote, and future coverage can never be alpha tie-breaks.

## 4. Alpaca free-tier data rules

The data steward collects and hashes all inputs. Group A consumes immutable artifacts only.

Do not begin outcome-bearing work until `research/shared/entitlement_probe.json` confirms the approved free-tier endpoints, IEX/indicative feed behavior, requested dates, pagination, and access result. An unavailable or mismatched probe fails the data gate; it does not cause the researcher to request credentials or try another source.

- Underlying history: Alpaca stock bars with explicit `feed=iex`; never SIP, delayed SIP relabeled as IEX, or another vendor.
- Fetch raw-adjusted bars for point-in-time spot/strike matching and split-adjusted bars for continuous return features. Never pair a split-adjusted spot with a raw option strike.
- Historical option evidence: Alpaca option bars/trades only, labeled as non-executable proxies. Record `requested_feed=N/A_ENDPOINT_HAS_NO_FEED_PARAM` where the endpoint has no feed parameter.
- Current option readiness: Alpaca `feed=indicative` latest quotes/chains/snapshots only. Never call them OPRA, NBBO, or executable history.
- Consume all pagination tokens; persist endpoint/tool version, scrubbed query, requested/returned coverage, rate-limit/errors, row counts, source timestamps, adjustment type, schema hash, and raw/normalized hashes.
- No Yahoo, Polygon, Databento, Cboe/OPRA download, FRED, external news, hand-copied chain, or researcher-specific data patch.
- Missing or invalid source data produces a failed gate or `NO_TRADE`, not forward-fill, another feed, another symbol, or an invented price.

Every normalized row carries `event_time`, `available_time`, `ingested_at`, endpoint/tool, explicit feed or sentinel, source page token, and raw response hash. Features require both `event_time <= decision_time` and `available_time <= decision_time`.

## 5. Common clock and execution proxy

- Aggregate one-minute IEX bars into ET half-open 15-minute intervals. Open is first, high is maximum, low is minimum, close is last, volume is sum, and interval VWAP is volume-weighted minute VWAP.
- A missing minute, missing VWAP, or zero cumulative volume invalidates the decision interval.
- Label an interval by its end and set availability to `interval_end + 1 second`.
- Entry evaluations occur at 10:30:01, then every 30 minutes through 14:30:01 ET.
- The underlying execution proxy is the open of the first one-minute interval beginning on the next whole minute after the decision/exit time.
- Position age starts at confirmed proxy/runtime fill, never at signal time.
- No overnight positions, overlapping labels, or early-close sessions.
- Central time exit is 60 minutes from confirmed fill, capped at 15:45 ET. Diagnostics at 45 and 90 minutes cannot replace the central result.
- Evaluate open-position management after every completed 15-minute interval plus one second. For trend exits, bullish closes on `close <= session_vwap` and bearish on `close >= session_vwap`; for H2 reversion, bullish closes on `close >= session_vwap` and bearish on `close <= session_vwap`.
- Strategy-level premium profit targets and price stops are disabled. Safety/reconciliation exits are recorded separately and never tuned as alpha exits.
- On the competition final Thursday, the target policy allows no new entry after 13:30 ET, begins flatten by 15:15, and requires broker-confirmed flat by 15:30. Research replays the rule through the pinned policy semantics; durable broker-confirmed flatten evidence remains release-owned and blocks paper use, not credential-free research.
- Plug-ins are entry-only. The central position manager owns exit orders and final flatten; the named policy decisions and reduce-only construction exist at the pinned commit, while durable broker/fill/restart/confirmed-flat proof remains a release-owned paper gate.

## 6. H1 — normalized intraday continuation

At decision time `t`:

```text
r60_t = log(close_t / p_t_minus_60m)
momentum_z = (r60_t - mean_prior_20_same_time_r60)
             / max(sample_std_prior_20_same_time_r60, 1e-6)
```

At 10:30, `p_t_minus_60m` is the 09:30 session open. Otherwise it is the completed close exactly 60 minutes earlier. Never cross an overnight boundary.

Central signal:

- bullish when `momentum_z >= 1.00` and close is above completed session IEX VWAP;
- bearish when `momentum_z <= -1.00` and close is below completed session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = abs(momentum_z)`. Target exit policy is `TREND_VWAP_OR_60M_V1`: adverse completed-close VWAP cross or the hard-time deadline.

Parameter budget:

- central threshold `1.00`, the only promotion-eligible value;
- diagnostic thresholds `0.75`, `1.25` one at a time;
- central time exit 60 minutes; diagnostic 45 and 90 minutes;
- no symbol-specific thresholds, time windows, regime overlays, news, IV, Greeks, or additional feature search.

## 7. H2 — normalized VWAP reversion

At decision time `t`:

```text
deviation = log(close_t / session_vwap_t)
deviation_z = (deviation - mean_prior_20_same_time_deviation)
              / max(sample_std_prior_20_same_time_deviation, 1e-6)
```

Central signal:

- bullish when `deviation_z <= -1.50` and `abs(momentum_z) < 0.50`;
- bearish when `deviation_z >= 1.50` and `abs(momentum_z) < 0.50`;
- otherwise `NO_TRADE`.

Set `entry_score = abs(deviation_z) / 1.50`. Target exit policy is `REVERSION_VWAP_TOUCH_OR_60M_V1`: completed-close touch through session VWAP in the convergence direction or the hard-time deadline.

Parameter budget:

- central deviation threshold `1.50`, the only promotion-eligible value;
- diagnostic thresholds `1.25`, `1.75` one at a time;
- momentum-neutral gate fixed at `0.50` with no optimization;
- central time exit 60 minutes; diagnostic 45 and 90 minutes;
- H2 remains a standalone candidate, never an H1 overlay or intraday fallback.

No outcome-bearing H1/H2 comparison begins until the common H1 golden run reproduces on two native ARM64 machines.

## 8. Candidate and feature contracts

Create these complete candidate identities before viewing outcome P&L:

- `h1_intraday_continuation__all_feasible__o2_v1`, with ordered eligible set `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]`;
- `h2_vwap_reversion__spy_qqq_smh_igv__o2_v1`, with ordered eligible set `[SPY, QQQ, SMH, IGV]`.

Each `CandidateSpecV1`-equivalent strategy card freezes:

```text
signal_family_id
ordered_eligible_symbol_set
feature_schema_hash
central_config_hash
O2_expression_and_template_catalog_hash
allocator_hash
position_policy_hash
base_cost_policy_hash
```

The required Group A feature contract includes versioned, namespaced definitions for at least:

- `<SYMBOL>__r60_v1`;
- `<SYMBOL>__momentum_z_60m_same_time_v1`;
- `<SYMBOL>__session_iex_vwap_v1`;
- `<SYMBOL>__close_completed_15m_v1`;
- `<SYMBOL>__deviation_log_from_session_vwap_v1`;
- `<SYMBOL>__deviation_z_same_time_v1`;
- session/early-close and source-quality flags.

Each entry states type, unit, exact formula, lookback, source/feed, event/availability rule, maximum age, missing behavior, allowed quality flags, and worked-example hash. Missing, stale, nonfinite, or schema-mismatched features produce `NO_TRADE`.

## 9. Exact package and integration handoff

Each owner returns one canonical package. Substitute their assigned plug-in ID in this tree; do not use the flat fixture layout currently present elsewhere in the repository:

```text
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
│   └── reason_codes.py
├── scripts/
│   └── reproduce.sh
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

The separate research evidence tree is `research/candidates/<candidate_id>/` and contains `strategy_card.md`, `hypothesis.yaml`, `feature_contract.yaml`, `central_config.json`, `sensitivities.yaml`, `reason_codes.yaml`, `state_schema.json`, `data_refs.json`, `artifact_schema.json`, `runs/<run_id>/`, `integration/`, and `promotion_card.md`. Every run contains `run_manifest.json`, `pair_cell_metrics.json`, `signals.parquet`, `selected_contracts.parquet`, `proxy_leg_observations.parquet`, `trades.parquet`, `daily_returns.parquet`, `fold_metrics.parquet`, `metrics.json`, `cost_stress.json`, `limitations.md`, and plots. The reviewer adds `pair_cell_review.json`; central owners later add `central_full_universe_replay.json` outside the researcher's run.

### 9.1 Frozen integration cards

| Field | H1 owner: Person 1 | H2 owner: Person 2 |
|---|---|---|
| `plugin_id` / version | `h1_intraday_continuation` / `1.0.0` | `h2_vwap_reversion` / `1.0.0` |
| entry point | `h1_intraday_continuation_v1.plugin:Plugin` | `h2_vwap_reversion_v1.plugin:Plugin` |
| hypothesis ID | `H1_NORMALIZED_INTRADAY_CONTINUATION` | `H2_NORMALIZED_VWAP_REVERSION` |
| owner / reviewer | `person_1` / `person_2` | `person_2` / `person_1` |
| pair-cell evidence | ordered `[SPY, QQQ]` | ordered `[SPY, QQQ]` |
| later compatibility | ordered `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]` | ordered `[SPY, QQQ, SMH, IGV]` |
| position policy | `TREND_VWAP_OR_60M_V1` | `REVERSION_VWAP_TOUCH_OR_60M_V1` |
| allowed entry tuples | bullish call-debit and bearish put-debit; `INTRADAY_15_60M`, `TINY`, max TTL `300` | same |
| data requirements | `feature-vector/v1`; hash `CANDIDATE_DEFINED_AND_HASHED_BEFORE_OUTCOME_RUN`; maximum age `60`; logical positions `false` | same |

Both manifests use `api_version: strategy-plugin/v1`, `decision_schema_version: strategy-evaluation/v1`, `deterministic: true`, and `network_access: false`. Required feature keys are ordered by `SPY, QQQ, TQQQ, SMH, SOXL, IGV`, then lexicographically within symbol. H1 requires each compatible symbol's `close_completed_15m_v1`, `momentum_z_60m_same_time_v1`, and `session_iex_vwap_v1`; H2 requires `deviation_z_same_time_v1` and `momentum_z_60m_same_time_v1`. Each owner freezes and hashes the complete candidate-specific contract before outcome P&L; the release owner validates the key list and hash before integration review.

`central_config.json` is a canonical rendering of flat `StrategyConfigV1.values`. Exact H1 keys/values are `momentum_threshold="1.00"`, `same_time_lookback_sessions=20`, `std_floor="0.000001"`, `vwap_alignment_required=true`, `decision_start_et="10:30:01"`, `decision_end_et="14:30:01"`, `decision_step_minutes=30`, `max_entries_per_symbol_session=1`, `time_exit_minutes=60`, `risk_tier="TINY"`, and `intent_ttl_seconds=300`. Exact H2 keys/values are `deviation_threshold="1.50"`, `momentum_neutral_abs_max="0.50"`, `same_time_lookback_sessions=20`, `std_floor="0.000001"`, the same decision clock/entry/time-exit keys, `risk_tier="TINY"`, and `intent_ttl_seconds=300`. Decimal thresholds are strings in JSON and become `Decimal` values in `StrategyConfigV1`.

### 9.2 Output, reason, and state rules

The pure `signal.py` function and `Plugin.evaluate()` use identical logic. A bullish entry emits `CALL_DEBIT_SPREAD_V1`; bearish emits `PUT_DEBIT_SPREAD_V1`; horizon is `INTRADAY_15_60M`, risk tier is `TINY`, expiry is exactly `context.as_of + 300 seconds`, and the sole evidence reference is the input `FEATURE_VECTOR`. Score buckets are `[1.00,1.25)=LOW`, `[1.25,1.75)=MEDIUM`, and `>=1.75=HIGH`. The plug-in emits `packages.strategy_sdk.UNBOUND_PLUGIN_CONTENT_HASH`; the host owns source-hash binding.

Common `NO_TRADE` codes are exactly `DATA_MISSING`, `DATA_STALE`, `DATA_QUALITY_REJECTED`, `FEATURE_SCHEMA_MISMATCH`, `OUTSIDE_DECISION_WINDOW`, `EARLY_CLOSE_SESSION`, `DAILY_ENTRY_ALREADY_USED`, `NO_SIGNAL`, `DIRECTION_AMBIGUOUS`, `UNDERLYING_NOT_ALLOWED`, `TEMPLATE_NOT_ALLOWED`, and `TUPLE_NOT_ALLOWED`. H1 adds `H1_GATE_NOT_MET`, `H1_BULLISH_CONTINUATION`, and `H1_BEARISH_CONTINUATION`; H2 adds `H2_GATE_NOT_MET`, `H2_BULLISH_REVERSION`, and `H2_BEARISH_REVERSION`. `reason_codes.yaml` declares every code and the implementation emits no undeclared code.

`state_schema.json` freezes `strategy-state/v1`, initial sequence `0`, and payload `{}`. Every evaluation sets sequence to prior plus one and `as_of=context.as_of`. `NO_TRADE` preserves payload; entry may set only `last_entry_session_<SYMBOL>=YYYY-MM-DD`. No `PositionDirectiveV1`, clock/random/global state, I/O, raw bars, broker object, option symbol, strike, expiration, quantity, price, account, or order field is permitted.

### 9.3 Truthful reproduction command

No central historical backtester is claimed. Each owner must implement an executable, offline `scripts/reproduce.sh` accepting exactly `--data-manifest PATH --feasibility-manifest PATH --output PATH`; it refuses nonempty output, validates commit/lock/data/config hashes, runs package tests, and emits deterministically ordered evidence. The package README contains:

```zsh
./scripts/reproduce.sh \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

Baseline verification uses only the current root README commands: native ARM64 `uv sync --frozen`, `uv run python -m pytest`, and `uv run ruff check .`. A researcher may not call those a backtest. JSON uses sorted keys and decimal strings; JSONL sorts by `(candidate_id, symbol_order, decision_time, variant_id, record_id)`; Parquet uses the fixed `artifact_schema.json` column order, UTC timestamps, symbol order `SPY, QQQ, TQQQ, SMH, SOXL, IGV`, and stable row-group size `65536`.

## 10. Golden fixtures and conformance cases

Minimum H1 fixtures for both SPY and QQQ:

- bullish above threshold and VWAP;
- bearish below negative threshold and VWAP;
- equality at `+1.00` and `-1.00`;
- threshold met but VWAP misaligned;
- missing one of 20 same-time observations;
- zero/near-zero variance floor;
- stale/quality-flagged feature;
- early-close and outside-window refusal.

Minimum H2 fixtures for both symbols:

- bullish and bearish central entries;
- equality at `±1.50`;
- deviation met but `abs(momentum_z) == 0.50`, which must refuse because the gate is strict `< 0.50`;
- deviation below threshold;
- VWAP touch exit row for research parity;
- missing/stale deviation, momentum, or VWAP;
- unsupported underlying/tuple and excessive TTL;
- repeated daily entry/cooldown and state-sequence mismatch.

Common conformance fixtures for each plug-in cover tampered context/config/package hashes, wrong plug-in ID/version or metadata, disallowed underlying/template/horizon/risk tier, excessive TTL, prior-state sequence/hash mismatch, hidden exact-order fields, nondeterministic repeated evaluation, and attempted filesystem/network/environment/clock/random access. Expected behavior is a stable refusal or deployment-equivalent containment; missing host enforcement remains a failed platform gate.

For at least 20 frozen timestamps per candidate, `integration/backtest_runtime_parity.json` records feature/context/config hashes, expected direction/score, exact semantic output or refusal reason, next state, and evaluation hash. Run every context twice through the isolated runner and require byte-identical canonical output. An open platform gate is recorded honestly as failed/not implemented; the group cannot waive it.

The non-author reproduction section in `pair_cell_review.json` records reviewer, native `arm64` machine report, pinned commit/lock hash, the literal package `scripts/reproduce.sh` command, immutable data/feasibility refs, candidate hash, expected/actual artifact hashes, metric differences, one reproduced negative fixture, deviations, and timestamp. Record `host_interface_baseline=PASSED_AT_cb03a76` separately from `candidate_host_conformance=NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED`; the package script is never mislabeled as a central backtester.

## 11. Backtest and artifact requirements

Use one shared engine, fold calendar, selector, cost policy, trial ledger, and synchronized daily index. Required run artifacts include:

- `run_manifest.json`;
- `signals.parquet`, including every eligible, selected, suppressed, and refused symbol row;
- `selected_contracts.parquet` and point-in-time evidence hashes;
- `proxy_leg_observations.parquet`;
- `trades.parquet`;
- complete-market-date `daily_returns.parquet`, including zero-return dates;
- `fold_metrics.parquet`;
- `metrics.json`, `cost_stress.json`, `portfolio_replay.json`, `limitations.md`, and plots;
- `integration/registry_candidate.yaml`, golden contexts/evaluations, parity/conformance/catalog reports, and integration checklist;
- `promotion_card.md` with non-author sign-off.

Frozen periods are discovery/warm-up `2017-01-03`–`2023-12-29`, option calibration `2024-02-01`–`2024-12-31`, OOS folds `2025Q1=2025-01-02..03-31`, `Q2=04-01..06-30`, `Q3=07-01..09-30`, `Q4=10-01..12-31`, and final accept/reject `2026-01-02`–`2026-08-27`. Times are bounded by the regular-session ET calendar; half days are invalid. Clip only through a centrally reviewed pre-outcome `coverage_exception.json` applied identically to all families.

Use next-observation execution, one-session boundary embargo/purge, a complete daily market-date index, and synchronized five-session circular moving-block bootstrap with `PCG64` seed `20260829`, exactly 10,000 replications numbered `00000`–`09999`, and identical sampled date blocks for all candidates. Never pick a per-symbol winner and combine winners after outcomes.

## 12. Metrics, costs, and stress

Metric authority uses normalized equity `E_0=100000` and `r_d=E_d/E_(d-1)-1`, with valid inactive dates set to zero, `ddof=1`, 252-session annualization, and zero risk-free rate. Sharpe is `sqrt(252)*mean(r)/sample_std(r)`; Sortino uses `sqrt(mean(min(r,0)^2))`; drawdown is `E/running_max(E)-1`; Calmar is annualized geometric return divided by positive maximum-drawdown magnitude; 95% expected shortfall is the negative mean at/below the empirical 5th percentile; hit rate counts strictly positive net trades only; top-trade/day concentration divides the largest positive contribution by all positive contribution. Undefined denominators are labeled, never coerced to zero. JSON stores decimal strings and sorted keys.

Report at minimum:

- bar, contract-existence, simultaneous-leg, no-fill, and prospective quote coverage;
- signed underlying forward return, active days/trades, hit rate, excursions, date-clustered interval, and quarter/direction slices;
- gross/net option-proxy P&L, normalized $100,000 account return, drawdown, expected shortfall, worst trade/day, top-trade/day concentration, turnover, exposure time, and SPY beta/correlation;
- Sharpe, Sortino, Calmar, deflated/selection-adjusted Sharpe, and block-bootstrap probability on complete historical daily returns only;
- raw and family-wise adjusted one-sided p-values;
- overlap/correlation between H1 and H2 signals and portfolio selections.

Common O2 expression:

- debit vertical only for promotion;
- 7–14 DTE central and 15–21 DTE diagnostic; within a bucket choose minimum positive calendar DTE, then expiration ISO date and OCC symbol lexicographically;
- choose the nearest standard long strike to raw spot, with an exact-distance tie toward OTM;
- for a call choose the smallest listed same-expiry standard short strike at or above `long_strike × 1.01`; for a put choose the largest at or below `long_strike × 0.99`; no qualifying strike means infeasible;
- require simultaneous observations, positive debit strictly below spread width, and standard multiplier/deliverable validity;
- integer quantity under fee-inclusive `min($500, 0.50% × equity)` maximum loss;
- no contract reranking after observations/quotes are joined.

For one spread, fee-inclusive maximum loss is `gross_entry_debit_per_share × 100 + opening_fees + reserved_exit_fees`; quantity is the largest nonnegative integer fitting the risk budget. If one spread does not fit, return infeasible/`NO_TRADE`.

Also publish the required O1 single-long-option diagnostic using the identical expiry/long-leg ranking: one contract for instrument reporting and a separately fixed-risk account view. O1 is never promotion-eligible and cannot replace O2 or influence champion ordering; if a human uses it to select, it enters the selectable trial family and invalidates the existing freeze.

Base historical option proxy uses first common one-minute interval within five minutes and:

```text
buffer = max($0.05, 10% × bar_open, 25% × (bar_high - bar_low))
buy_proxy = bar_open + buffer
sell_proxy = max(0, bar_open - buffer)
```

Central fee assumption is $0.10 per contract, per leg, per side. Publish $0.00 and $0.25 diagnostics and a severe run using `high + $0.05` for buys, `max(0, low - $0.05)` for sells, doubled fees, and zero exit value for missing debit-structure exits. These are proxy assumptions, never fill claims.

## 13. Falsification requirements

H1 is rejected/demoted when next-observation execution removes the edge, the central sign is unstable across most folds/diagnostics, VWAP confirmation adds no defensible value, one date/regime dominates, or option costs erase the result.

H2 additionally requires a genuine low-trend reversion region. Report results with the momentum-neutral gate removed only as a falsification diagnostic. Reject/demote if reversion exists only at midpoint/close proxies, only in one volatility regime, or only after choosing a symbol/threshold from outcomes.

Both candidates fail promotion when any of these occurs:

- fewer than 75 OOS underlying trades over 40 active sessions or fewer than four populated quarters;
- one positive trade/date exceeds 25% of total positive contribution;
- severe/2× option result is negative, base result is nonpositive, or OOS maximum drawdown exceeds 4%;
- fewer than 50 option-scored OOS trades over 30 dates, insufficient simultaneous-leg coverage, or missing-exit penalty above 10%;
- current indicative quote gate fails;
- data lineage, timestamp, package hash, or independent reproduction fails;
- family-wise adjusted evidence does not pass—then the honest status is `suggestive`, shadow, or demo-only, never statistically supported alpha.

## 14. Acceptance and integration gates

| Gate | Required evidence | Failure result |
|---|---|---|
| `A0_HANDOFF` | Baseline, native lock, immutable data refs, A1/A2 roles, candidate IDs, and reviewer recorded | Do not start outcome runs |
| `A1_DATA` | SPY/QQQ feasibility cards, at least 99% expected 15-minute bars, zero duplicates/OHLC failures, explained raw/split discontinuities | Remove affected symbol or stop |
| `A2_SPEC_FREEZE` | Strategy cards, feature contracts, central/sensitivity configs, costs, exits, and trial entries hashed before P&L | New candidate/version required |
| `A3_SIGNAL` | Common-engine H1/H2 runs, next-observation behavior, prescribed diagnostics, minimum sample/fold/concentration gates | `REJECTED` or `RESEARCH_COMPLETE` only |
| `A4_OPTION_PROXY` | PIT existence, simultaneous-leg coverage, O2 base/severe output, no reranking, Monday quote gate | No option-expression support |
| `A5_PLUGIN` | Package, golden fixtures, deterministic runner output, semantic parity, no forbidden fields/I/O | Not `INTEGRATION_READY` |
| `A6_PLATFORM_PARITY` | Required `G-R1`–`G-R6` evidence from platform owners, especially feature/catalog/exit parity | Record failed/not implemented; do not claim closure |
| `A7_REPRODUCTION` | Non-author reproduces manifests, hashes, metrics, one negative fixture, and parity | Not done |
| `A8_SELECTION` | Central quant includes all viewed trials, applies 2025-only family-wide test, and freezes champion/fallback before 2026 | No promotion |

Passing Group A gates never authorizes paper trading. The release/risk/execution owners must separately close every paper-host, account, control, preflight, reservation, outbox/inbox, reconciliation, close/flatten, credential, and deployment gate.

## 15. Definition of done

Group A is done when both independently authored packages are complete and reproducible, every prescribed pair-cell central/diagnostic result is published, SPY/QQQ comparisons are honest and synchronized, feasibility exclusions and open integration gates remain visible, Person 2 signs H1, and Person 1 signs H2. Each card has exactly one truthful terminal state: `REJECTED`, `RESEARCH_COMPLETE`, `INTEGRATION_READY`, `PAPER_SHADOW`, `PAPER_DEMO_ONLY`, or `PAPER_CANDIDATE`. Neither owner may declare `PAPER_ENABLED` or produce the central full-universe replay.

Normative references: [`../plans/STRATEGY_RESEARCH_PLAN.md`](../plans/STRATEGY_RESEARCH_PLAN.md), [`../architecture/STRATEGY_API.md`](../architecture/STRATEGY_API.md), and [`../architecture/RESEARCH_INTERFACE_FREEZE.md`](../architecture/RESEARCH_INTERFACE_FREEZE.md).
