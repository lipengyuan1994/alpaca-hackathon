# Group C research plan — TQQQ leveraged technology and IGV software

Status: independently shareable parallel research packet

Implementation commit: `cb03a7684fb67c6f0888333f6c3c2145e8645be9`

Dependency-lock hash: `sha256:b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf`

Assigned capacity: one packet owner responsible for two independently versioned strategy families

Primary symbol cells: `TQQQ`, `IGV`

Assigned strategies: benchmark-residual relative strength and intraday compression breakout

## 1. Mission and grouping rationale

Group C tests technology-cluster signals that require explicit benchmark and dependence controls. `TQQQ` is leveraged QQQ beta with daily-reset/path risk; `IGV` is concentrated software exposure. They share QQQ as a required control, but they are not independent replications and must not be pooled as two confirmations of the same technology event.

The group owns TQQQ/IGV data-quality interpretation, both feature specifications and pure signal functions, plug-in packages, group cells, artifacts, falsification, and review. It does not own Alpaca ingestion, benchmark publication, the central registry, portfolio arbitration, option contract selection, sizing, risk approval, order submission, promotion, or judged-account operation.

Researchers work from a source snapshot plus immutable data artifacts. They need no GitHub write credential, Alpaca credential, account ID, broker access, MCP trading tool, deployment secret, or order permission. Return patches/source archives and content-addressed evidence to the platform owner.

A profitable report cannot self-promote. `integration/registry_candidate.yaml` is a non-authorizing proposal with lifecycle `research_only`; only central owners may later change registry authority after every research, integration, safety, and release gate passes.

The signal plug-in never calls an LLM. Any advisory AI may leave a frozen deterministic proposal unchanged or veto it to `NO_TRADE`; it cannot change direction, family, symbol ranking, template, horizon, strike policy, size, executable fields, or lifecycle.

## 2. Packet ownership and exact returns

- **Relative-strength deliverable:** `relative_strength_residual_v1`, including its feature contract, pure signal, canonical plug-in, package-local offline reproduction script, TQQQ/IGV pair-cell evidence with immutable QQQ controls, prescribed sensitivities, option-proxy status, falsifications, and complete artifact tree.
- **Compression-breakout deliverable:** `compression_breakout_v1`, including its separate feature contract, pure signal, canonical plug-in, package-local offline reproduction script, TQQQ/IGV pair-cell evidence, prescribed sensitivities, option-proxy status, falsifications, and complete artifact tree.

The Group C packet owner authors both families but freezes both specifications before viewing outcome P&L for either family. The Group A packet owner independently reviews both returned packages and signs each `pair_cell_review.json`. The reviewer may report a defect but cannot tune or directly repair a reviewed economic rule after seeing P&L; the Group C owner versions any outcome-changing correction and repeats affected runs. If the designated reviewer is unavailable, obtain another non-author reviewer outside Group C.

The packet owner may not modify the authoritative registry, install broker credentials, run private market-data downloads, enable paper mode, review their own packages, or approve their own promotion. Stop before work if the checkout or `uv.lock` differs from the pinned values above.

## 3. Exact universe and controls

| Use | Symbols | Rule |
|---|---|---|
| Owned research cells | TQQQ, IGV | Complete data/signal/stress/falsification output for both; run option proxy only when the global blinded feasibility manifest selects the symbol. |
| Relative-strength promotion-eligible compatibility | QQQ, TQQQ, SMH, SOXL, IGV | Freeze one implementation and the full benchmark map. Central integration runs it unchanged on every compatible feasible target. |
| Relative-strength diagnostic only | SPY benchmarked to QQQ | Symmetry check only; never promotion-eligible for this version. |
| Compression-breakout compatibility | SPY, QQQ, TQQQ, SMH, SOXL, IGV | Freeze one implementation. Central integration runs it unchanged on every compatible feasible symbol. |
| Required technology benchmark | QQQ | Benchmark map: TQQQ→QQQ and IGV→QQQ. Missing, stale, or mismatched QQQ data invalidates the target decision. |
| Broad-market reporting control | SPY | Report beta/correlation and market-date overlap; SPY is not an additional relative-strength tuning variable for these cells. |
| Leverage stress | TQQQ | Compare at fixed fee-inclusive maximum loss; audit splits and daily-reset/path effects; never use raw share returns as equivalent risk. |
| Statistical null | All viewed candidates on common dates | Use the synchronized centered five-session moving-block maximum-statistic procedure; no per-trade sign permutation. |

The full relative-strength benchmark map, frozen before outcome P&L, is:

| Target | Benchmark |
|---|---|
| QQQ | SPY |
| TQQQ | QQQ |
| SMH | QQQ |
| SOXL | SMH |
| IGV | QQQ |
| SPY diagnostic | QQQ |

This packet returns two separate strategy packages and their TQQQ/IGV `pair_cell_metrics.json` files; relative strength also binds immutable QQQ control rows. Those files are diagnostic evidence only: the packet owner may not select TQQQ versus IGV, declare a champion/fallback, or claim the pair is the complete `CandidateSpecV1`. After all six strategies freeze, the central quant/release owner expands both unchanged to their compatible feasible universe and writes `central_full_universe_replay.json`. That later replay alone applies cross-symbol arbitration and the family-wide selection test. QQQ inputs are centrally published immutable control data and cannot be tuned or backfilled.

Before any alpha outcome is viewed, the data steward must sign `research/shared/selection/option_proxy_feasibility_manifest.json`, ranking all six symbols from blinded entitlement, completeness, timestamp, standard-contract, simultaneous-leg, and corporate-action fields. The global `selected_symbols` list has at most three symbols. TQQQ or IGV absent from it still receives full underlying research, but its option artifacts are empty schema-valid tables plus `option_proxy_not_selected.json` with exact status `NOT_SELECTED_BY_FEASIBILITY`. QQQ is a required relative-strength benchmark input, not automatically an option-proxy selection. Group C cannot swap, rerank, or fill a slot after seeing results.

### 3.1 Frozen cross-symbol arbitration

For each decision time, compute every compatible target row with the same pure family function, then:

1. remove rows failing data, feature, benchmark, session, family, cluster, cooldown, or existing-exposure gates;
2. rank remaining rows by `entry_score - 1.00`, where the central entry threshold is exactly `1.00`;
3. break an exact tie by `SPY, QQQ, TQQQ, SMH, SOXL, IGV` order;
4. map score `[1.00, 1.25)` to `LOW`, `[1.25, 1.75)` to `MEDIUM`, and `>= 1.75` to `HIGH`;
5. send only the winner as a semantic request; if later option/quote/risk checks fail, record `NO_TRADE` with no fall-through;
6. allow at most one new exposure-increasing intent per decision time and one nonterminal position/order;
7. treat QQQ/TQQQ/IGV as one technology cluster and SMH/SOXL as one semiconductor cluster;
8. retain every eligible, rejected, selected, and suppressed row with a reason code.

The pair-cell reproduction script records both per-symbol rows and does not arbitrate a winner. The later central adapter must use `packages/strategy_sdk/arbitration.py` at source-file SHA-256 `864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5`. Option P&L, spread width, current quote, and future coverage can never be alpha tie-breaks.

## 4. Alpaca free-tier data rules

The data steward collects and hashes all inputs. Group C consumes immutable artifacts only.

Do not begin outcome-bearing work until `research/shared/entitlement_probe.json` confirms the approved free-tier endpoints, IEX/indicative feed behavior, requested dates, pagination, and access result. An unavailable or mismatched probe fails the data gate; it does not cause the researcher to request credentials or try another source.

- Underlying history: Alpaca stock bars with explicit `feed=iex`; never SIP, delayed SIP relabeled as IEX, or another vendor.
- Fetch raw-adjusted bars for point-in-time spot/strike matching and split-adjusted bars for continuous return features. Never pair a split-adjusted spot with a raw option strike.
- Historical option evidence: Alpaca option bars/trades only, labeled as non-executable proxies. Record `requested_feed=N/A_ENDPOINT_HAS_NO_FEED_PARAM` where the endpoint has no feed parameter.
- Current option readiness: Alpaca `feed=indicative` latest quotes/chains/snapshots only. Never call them OPRA, NBBO, or executable history.
- Consume all pagination tokens; persist endpoint/tool version, scrubbed query, requested/returned coverage, rate-limit/errors, row counts, source timestamps, adjustment type, schema hash, and raw/normalized hashes.
- No Yahoo, Polygon, Databento, Cboe/OPRA download, FRED, external news, hand-copied chain, or researcher-specific data patch.
- Missing or invalid source data produces a failed gate or `NO_TRADE`, not forward-fill, another feed, another benchmark, another symbol, or an invented price.

Every normalized row carries `event_time`, `available_time`, `ingested_at`, endpoint/tool, explicit feed or sentinel, source page token, and raw response hash. Features require both `event_time <= decision_time` and `available_time <= decision_time`.

Target and benchmark bars must come from the same normalized feed/version and common completed timestamp. A target row without its exact benchmark pair is invalid. TQQQ requires a corporate-action record, raw/adjusted discontinuity audit, and worked raw-spot-to-option-strike joins around all splits.

## 5. Common clock and execution proxy

- Aggregate one-minute IEX bars into ET half-open 15-minute intervals. Open is first, high is maximum, low is minimum, close is last, volume is sum, and interval VWAP is volume-weighted minute VWAP.
- A missing minute, missing VWAP, or zero cumulative volume invalidates the decision interval.
- Label an interval by its end and set availability to `interval_end + 1 second`.
- Entry evaluations occur at 10:30:01, then every 30 minutes through 14:30:01 ET. Compression breakout starts only at 11:00:01.
- The underlying execution proxy is the open of the first one-minute interval beginning on the next whole minute after the decision/exit time.
- Position age starts at confirmed proxy/runtime fill, never at signal time.
- No overnight positions, overlapping labels, or early-close sessions.
- Central time exit is 60 minutes from confirmed fill, capped at 15:45 ET. Diagnostics at 45 and 90 minutes cannot replace the central result.
- Evaluate open-position management after every completed 15-minute interval plus one second. For trend exits, bullish closes on `close <= session_vwap` and bearish on `close >= session_vwap`.
- Strategy-level premium profit targets and price stops are disabled. Safety/reconciliation exits are recorded separately and never tuned as alpha exits.
- On the competition final Thursday, the target policy allows no new entry after 13:30 ET, begins flatten by 15:15, and requires broker-confirmed flat by 15:30. Research replays the rule through the pinned policy semantics; durable broker-confirmed flatten evidence remains release-owned and blocks paper use, not credential-free research.
- Plug-ins are entry-only. The central position manager owns exit orders and final flatten; the named policy decisions and reduce-only construction exist at the pinned commit, while durable broker/fill/restart/confirmed-flat proof remains a release-owned paper gate.

## 6. Benchmark-residual relative strength

At each common decision time `t`, use the exact intraday-continuation 60-minute return clock for target and mapped benchmark. At 10:30, the 60-minute base is the 09:30 session open; at later decisions it is the completed close exactly 60 minutes earlier. Never cross an overnight boundary:

```text
r_target_t = log(target_close_t / target_price_t_minus_60m)
r_benchmark_t = log(benchmark_close_t / benchmark_price_t_minus_60m)

beta_t = cov(prior_60_valid_same_time_target_returns,
             prior_60_valid_same_time_benchmark_returns)
         / max(var(prior_60_valid_same_time_benchmark_returns), 1e-8)

prior_residual_i = r_target_i - beta_t * r_benchmark_i
residual_z = (r_target_t - beta_t * r_benchmark_t
              - mean(prior_60_residuals))
             / max(sample_std(prior_60_residuals), 1e-6)
```

The beta at `t` is computed only from the 60 valid paired prior sessions at the same decision time. The same frozen-at-`t` beta is applied to those prior pairs to form the residual mean and standard deviation. Never include the current pair in the lookback.

Central signal:

- bullish when `residual_z >= 1.25` and target close is above its completed session IEX VWAP;
- bearish when `residual_z <= -1.25` and target close is below its completed session IEX VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = abs(residual_z) / 1.25`. Missing/stale benchmark or fewer than 60 valid same-time pairs produces `BENCHMARK_MISSING`/`NO_TRADE`; do not substitute SPY or a neighboring time. Target exit policy is `TREND_VWAP_OR_60M_V1`.

Parameter budget:

- central residual threshold `1.25`; diagnostic `1.00` and `1.50` one at a time;
- beta lookback fixed at 60 valid same-time session pairs;
- variance floors fixed at `1e-8` for benchmark variance and `1e-6` for residual standard deviation;
- benchmark map fixed exactly as above;
- central time exit 60 minutes; diagnostic 45 and 90 minutes;
- no symbol-specific beta window, shrinkage/model variant, additional factor, volatility normalization, sector substitution, news, IV, Greeks, or regime overlay.

## 7. Intraday compression followed by expansion

Compression breakout starts at 11:00:01 ET. At decision time `t`, exclude the just-completed decision interval from the compression box. The box spans the four completed 15-minute intervals `[t-75m, t-15m)`:

```text
box_high = max(high in the four box intervals)
box_low = min(low in the four box intervals)
box_range_log = max(log(box_high / box_low), 1e-6)

median_box_range = median(prior_20_valid_sessions_same_time_box_range_log)
compression_ratio = box_range_log / median_box_range

up_break_fraction = log(close_t / box_high) / box_range_log
down_break_fraction = log(box_low / close_t) / box_range_log
volume_ratio_t = completed_interval_volume_t
                 / median_prior_20_sessions_same_time_interval_volume
```

Central signal:

- bullish when `compression_ratio <= 0.65`, `up_break_fraction >= 0.10`, `volume_ratio_t >= 1.25`, and close is above completed session VWAP;
- bearish when `compression_ratio <= 0.65`, `down_break_fraction >= 0.10`, `volume_ratio_t >= 1.25`, and close is below completed session VWAP;
- otherwise `NO_TRADE`.

Set `entry_score = min(0.65 / max(compression_ratio, 1e-6), active_break_fraction / 0.10, volume_ratio_t / 1.25)`. Permit only the first compression-breakout entry per symbol/session. Target exit policy is `TREND_VWAP_OR_60M_V1`.

Parameter budget:

- central compression ratio `0.65`; diagnostic `0.50` and `0.80` one at a time;
- central break fraction `0.10`; diagnostic `0.05` and `0.15` one at a time;
- volume ratio fixed at `1.25` with no strategy-specific search;
- fixed four-interval box and 20-session same-time baselines;
- central time exit 60 minutes; diagnostic 45 and 90 minutes;
- no alternate box, symbol-specific threshold, continuation/opening-range overlay, regime filter, news, IV, Greeks, or extra feature search.

## 8. Candidate and feature contracts

Create these complete candidate identities before viewing outcome P&L:

- `relative_strength_residual__qqq_tqqq_smh_soxl_igv__o2_v1`, with ordered eligible set `[QQQ, TQQQ, SMH, SOXL, IGV]`;
- `compression_breakout__all_feasible__o2_v1`, with ordered eligible set `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]`.

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

The Group C feature contract includes versioned, namespaced definitions for at least:

- `<SYMBOL>__r60_v1` and `<BENCHMARK>__r60_v1`;
- `<SYMBOL>__benchmark_symbol_h5_v1` as a validated config/identity binding, not a numeric feature;
- `<SYMBOL>__beta_same_time_60_v1`;
- `<SYMBOL>__residual_mean_same_time_60_v1`, `residual_std_same_time_60_v1`, and `residual_z_same_time_60_v1`;
- `<SYMBOL>__session_iex_vwap_v1` and completed close;
- `<SYMBOL>__compression_box_high_4x15m_v1`, `compression_box_low_4x15m_v1`, and `box_range_log_v1`;
- `<SYMBOL>__median_box_range_same_time_20_v1` and `compression_ratio_same_time_20_v1`;
- `<SYMBOL>__up_break_fraction_compression_v1` and `down_break_fraction_compression_v1`;
- `<SYMBOL>__volume_ratio_same_time_20_v1`;
- paired-benchmark, split/adjustment, session/early-close, and source-quality flags.

Each entry states type, unit, exact formula, lookback, source/feed, event/availability rule, maximum age, missing behavior, allowed quality flags, and worked-example hash. Missing, stale, nonfinite, schema-mismatched, unpaired-benchmark, or adjustment-ambiguous features produce `NO_TRADE`.

## 9. Exact package and integration handoff

The packet owner returns one canonical package per family, for two packages total. Substitute the corresponding plug-in ID in each tree; do not use the flat fixture layout currently present elsewhere in the repository:

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

The separate research evidence tree is `research/candidates/<candidate_id>/` and contains `strategy_card.md`, `hypothesis.yaml`, `feature_contract.yaml`, `central_config.json`, `sensitivities.yaml`, `reason_codes.yaml`, `state_schema.json`, `data_refs.json`, `artifact_schema.json`, `runs/<run_id>/`, `integration/`, and `promotion_card.md`. Every run contains `run_manifest.json`, `pair_cell_metrics.json`, `signals.parquet`, `selected_contracts.parquet`, `proxy_leg_observations.parquet`, `trades.parquet`, `daily_returns.parquet`, `fold_metrics.parquet`, `metrics.json`, `cost_stress.json`, benchmark or compression audits as applicable, `limitations.md`, and plots. The reviewer adds `pair_cell_review.json`; central owners later add `central_full_universe_replay.json` outside the researcher's run.

### 9.1 Frozen integration cards

| Field | Relative-strength package | Compression-breakout package |
|---|---|---|
| `plugin_id` / version | `relative_strength_residual` / `1.0.0` | `compression_breakout` / `1.0.0` |
| entry point | `relative_strength_residual_v1.plugin:Plugin` | `compression_breakout_v1.plugin:Plugin` |
| hypothesis ID | `RELATIVE_STRENGTH_RESIDUAL` | `COMPRESSION_BREAKOUT` |
| owner / reviewer | assigned Group C owner / independent Group A reviewer | assigned Group C owner / independent Group A reviewer |
| pair-cell evidence | ordered targets `[TQQQ, IGV]`, immutable `QQQ` control | ordered `[TQQQ, IGV]` |
| later compatibility | ordered `[QQQ, TQQQ, SMH, SOXL, IGV]` | ordered `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]` |
| position policy | `TREND_VWAP_OR_60M_V1` | `TREND_VWAP_OR_60M_V1` |
| allowed entry tuples | bullish call-debit and bearish put-debit; `INTRADAY_15_60M`, `TINY`, max TTL `300` | same |
| data requirements | `feature-vector/v1`; hash `CANDIDATE_DEFINED_AND_HASHED_BEFORE_OUTCOME_RUN`; maximum age `60`; logical positions `false` | same |

Both manifests use `api_version: strategy-plugin/v1`, `decision_schema_version: strategy-evaluation/v1`, `deterministic: true`, and `network_access: false`. Required feature keys are ordered by `SPY, QQQ, TQQQ, SMH, SOXL, IGV`, then lexicographically within symbol. Relative strength requires target/benchmark `r60`, frozen benchmark binding, beta, residual mean/std/z, completed close, session VWAP, and paired-quality keys from Section 8. Compression breakout requires compression box high/low/range, same-time median, compression ratio, up/down break, volume ratio, completed close, and session VWAP. The packet owner freezes and hashes both complete candidate-specific contracts before viewing outcome P&L for either one; the release owner validates the key lists and hashes before integration review.

`central_config.json` is a canonical rendering of flat `StrategyConfigV1.values`. Exact relative-strength keys/values are `residual_z_threshold="1.25"`, `beta_lookback_sessions=60`, `benchmark_variance_floor="0.00000001"`, `residual_std_floor="0.000001"`, `benchmark_map_version="RELATIVE_STRENGTH_BENCHMARK_MAP_V1"`, `decision_start_et="10:30:01"`, `decision_end_et="14:30:01"`, `decision_step_minutes=30`, `max_entries_per_symbol_session=1`, `time_exit_minutes=60`, `risk_tier="TINY"`, and `intent_ttl_seconds=300`. Exact compression-breakout keys/values are `compression_ratio_max="0.65"`, `break_fraction_threshold="0.10"`, `volume_ratio_threshold="1.25"`, `box_interval_count=4`, `same_time_lookback_sessions=20`, `range_floor="0.000001"`, `decision_start_et="11:00:01"`, `decision_end_et="14:30:01"`, `decision_step_minutes=30`, `max_entries_per_symbol_session=1`, `time_exit_minutes=60`, `risk_tier="TINY"`, and `intent_ttl_seconds=300`. Decimal thresholds are strings in JSON and become `Decimal` values in `StrategyConfigV1`.

### 9.2 Output, reason, and state rules

The pure `signal.py` function and `Plugin.evaluate()` use identical logic. A bullish entry emits `CALL_DEBIT_SPREAD_V1`; bearish emits `PUT_DEBIT_SPREAD_V1`; horizon is `INTRADAY_15_60M`, risk tier is `TINY`, expiry is exactly `context.as_of + 300 seconds`, and the sole evidence reference is the input `FEATURE_VECTOR`. Score buckets are `[1.00,1.25)=LOW`, `[1.25,1.75)=MEDIUM`, and `>=1.75=HIGH`. The plug-in emits `packages.strategy_sdk.UNBOUND_PLUGIN_CONTENT_HASH`; the host owns source-hash binding.

Common `NO_TRADE` codes are exactly `DATA_MISSING`, `DATA_STALE`, `DATA_QUALITY_REJECTED`, `FEATURE_SCHEMA_MISMATCH`, `OUTSIDE_DECISION_WINDOW`, `EARLY_CLOSE_SESSION`, `DAILY_ENTRY_ALREADY_USED`, `NO_SIGNAL`, `DIRECTION_AMBIGUOUS`, `UNDERLYING_NOT_ALLOWED`, `TEMPLATE_NOT_ALLOWED`, and `TUPLE_NOT_ALLOWED`. Relative strength adds `BENCHMARK_MISSING`, `BENCHMARK_BINDING_MISMATCH`, `RELATIVE_STRENGTH_GATE_NOT_MET`, `RELATIVE_STRENGTH_BULLISH`, and `RELATIVE_STRENGTH_BEARISH`; compression breakout adds `COMPRESSION_BREAKOUT_GATE_NOT_MET`, `COMPRESSION_BREAKOUT_BULLISH`, and `COMPRESSION_BREAKOUT_BEARISH`. `reason_codes.yaml` declares every code and the implementation emits no undeclared code.

`state_schema.json` freezes `strategy-state/v1`, initial sequence `0`, and payload `{}`. Every evaluation sets sequence to prior plus one and `as_of=context.as_of`. `NO_TRADE` preserves payload; entry may set only `last_entry_session_<SYMBOL>=YYYY-MM-DD`. No `PositionDirectiveV1`, clock/random/global state, I/O, raw bars, broker object, option symbol, strike, expiration, quantity, price, account, or order field is permitted.

### 9.3 Truthful reproduction command

No central historical backtester is claimed. Each of the two packages must implement an offline `src/<plugin_id>_v1/reproduce.py` module accepting exactly `--data-manifest PATH --feasibility-manifest PATH --output PATH`; it refuses nonempty output, validates commit/lock/data/config hashes, runs package tests, and emits deterministically ordered evidence. Optional POSIX/PowerShell wrappers must call that same Python module. The package README contains:

```text
uv run python -m <plugin_id>_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

Baseline verification uses the platform-neutral commands `uv sync --frozen`, `uv run python -m pytest`, and `uv run ruff check .`. A researcher records their operating system and CPU architecture in the run manifest but is not blocked by either. A researcher may not call verification a backtest. JSON uses sorted keys and decimal strings; JSONL sorts by `(candidate_id, symbol_order, decision_time, variant_id, record_id)`; Parquet uses the fixed `artifact_schema.json` column order, UTC timestamps, symbol order `SPY, QQQ, TQQQ, SMH, SOXL, IGV`, and stable row-group size `65536`.

## 10. Golden fixtures and conformance cases

Minimum relative-strength fixtures for TQQQ and IGV, plus one QQQ control fixture:

- bullish and bearish central entries;
- equality at residual z `+1.25` and `-1.25`;
- residual threshold met with target VWAP misalignment;
- exactly 60 valid prior pairs versus 59, which refuses;
- current target row present but current benchmark row missing/stale/mismatched;
- current pair accidentally included in beta lookback, with expected hash mismatch/refusal;
- near-zero benchmark variance and residual-standard-deviation floors;
- wrong benchmark-map binding, unsupported target, early-close, and outside-window refusal.

Minimum compression-breakout fixtures for TQQQ and IGV:

- bullish and bearish central entries;
- equality at compression `0.65`, break `0.10`, and volume `1.25`;
- qualifying compression without break, break without volume, and VWAP misalignment;
- four complete box intervals versus one missing interval;
- exactly 20 prior same-time boxes versus 19, which refuses;
- exclusion of the just-completed decision interval from the box;
- first entry accepted then repeated daily entry refused;
- stale/quality-flagged, early-close, and before-11:00 refusal.

Common conformance fixtures for each plug-in cover tampered context/config/package hashes, wrong plug-in ID/version or metadata, disallowed underlying/template/horizon/risk tier, excessive TTL, prior-state sequence/hash mismatch, hidden exact-order fields, nondeterministic repeated evaluation, and attempted filesystem/network/environment/clock/random access. Expected behavior is a stable refusal or deployment-equivalent containment; missing host enforcement remains a failed platform gate.

For at least 20 frozen timestamps per candidate, `integration/backtest_runtime_parity.json` records feature/context/config hashes, expected direction/score, exact semantic output or refusal reason, next state, and evaluation hash. Run every context twice through the isolated runner and require byte-identical canonical output. An open platform gate is recorded honestly as failed/not implemented; the group cannot waive it.

The non-author reproduction section in `pair_cell_review.json` records reviewer, operating-system/CPU report, pinned commit/lock hash, the literal `uv run python -m <plugin_id>_v1.reproduce` command, immutable target/benchmark/feasibility refs, candidate hash, expected/actual artifact hashes, metric differences, one reproduced negative fixture, deviations, and timestamp. Record the central `host_interface_baseline=PASSED_AT_cb03a76` separately from `candidate_host_conformance=NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED`; the package module is never mislabeled as a central backtester.

## 11. Backtest and artifact requirements

Use one shared engine, fold calendar, selector, cost policy, trial ledger, and synchronized daily index. Required run artifacts include:

- `run_manifest.json` with code, data, feature, config, benchmark-map, selector, allocator, position-policy, catalog, and cost hashes;
- `signals.parquet`, including every eligible, selected, suppressed, and refused target/benchmark row;
- `selected_contracts.parquet` and point-in-time evidence hashes;
- `proxy_leg_observations.parquet` and `trades.parquet`;
- complete-market-date `daily_returns.parquet`, including zero-return dates;
- `fold_metrics.parquet`, `metrics.json`, `cost_stress.json`, `portfolio_replay.json`, `limitations.md`, and plots;
- `benchmark_pair_audit.json`, `leverage_adjustment_audit.json`, `family_overlap.json`, and complete trial-ledger entries;
- integration proposal, golden contexts/evaluations, parity/conformance/catalog reports, checklist, and non-author promotion card.

Frozen periods are discovery/warm-up `2017-01-03`–`2023-12-29`, option calibration `2024-02-01`–`2024-12-31`, OOS folds `2025Q1=2025-01-02..03-31`, `Q2=04-01..06-30`, `Q3=07-01..09-30`, `Q4=10-01..12-31`, and final accept/reject `2026-01-02`–`2026-08-27`. Times are bounded by the regular-session ET calendar; half days are invalid. Clip only through a centrally reviewed pre-outcome `coverage_exception.json` applied identically to all families.

Use next-observation execution, one-session boundary embargo/purge, a complete daily market-date index, and synchronized five-session circular moving-block bootstrap with `PCG64` seed `20260829`, exactly 10,000 replications numbered `00000`–`09999`, and identical sampled date blocks for all candidates. Never pick a TQQQ/IGV winner, alternate benchmark, or threshold after outcomes.

## 12. Metrics, O2 policy, and stresses

Metric authority uses normalized equity `E_0=100000` and `r_d=E_d/E_(d-1)-1`, with valid inactive dates set to zero, `ddof=1`, 252-session annualization, and zero risk-free rate. Sharpe is `sqrt(252)*mean(r)/sample_std(r)`; Sortino uses `sqrt(mean(min(r,0)^2))`; drawdown is `E/running_max(E)-1`; Calmar is annualized geometric return divided by positive maximum-drawdown magnitude; 95% expected shortfall is the negative mean at/below the empirical 5th percentile; hit rate counts strictly positive net trades only; top-trade/day concentration divides the largest positive contribution by all positive contribution. Undefined denominators are labeled, never coerced to zero. JSON stores decimal strings and sorted keys.

Report at minimum:

- bar, paired-benchmark, contract-existence, simultaneous-leg, no-fill, and prospective quote coverage;
- signed underlying/residual forward return, active days/trades, hit rate, excursions, date-clustered interval, and quarter/direction/volatility slices;
- gross/net option-proxy P&L, normalized $100,000 account return, drawdown, expected shortfall, worst trade/day, top-trade/day concentration, turnover, and exposure time;
- Sharpe, Sortino, Calmar, deflated/selection-adjusted Sharpe, raw and family-wise adjusted one-sided p-values on complete historical daily returns only;
- beta distributions/stability, residual versus raw momentum, QQQ/SPY beta/correlation, TQQQ/IGV same-date dependence, compression-breakout overlap with continuation/opening-range signals, and incremental portfolio contribution;
- fixed maximum-loss versus misleading raw-share-return comparisons.

Common O2 expression:

- debit vertical only for promotion;
- 7–14 DTE central and 15–21 DTE diagnostic; within a bucket choose minimum positive calendar DTE, then expiration ISO date and OCC symbol lexicographically;
- choose the nearest standard long strike to raw spot, with exact-distance tie toward OTM;
- for a call choose the smallest listed same-expiry standard short strike at or above `long_strike × 1.01`; for a put choose the largest at or below `long_strike × 0.99`; no qualifying strike means infeasible;
- require simultaneous observations, positive debit strictly below spread width, and standard multiplier/deliverable validity;
- integer quantity under fee-inclusive `min($500, 0.50% × equity)` maximum loss;
- no contract reranking after observations/quotes are joined.

For one spread, fee-inclusive maximum loss is `gross_entry_debit_per_share × 100 + opening_fees + reserved_exit_fees`; quantity is the largest nonnegative integer fitting the risk budget. If one spread does not fit, return infeasible/`NO_TRADE`.

Also publish the required O1 single-long-option diagnostic using the identical expiry/long-leg ranking: one contract for instrument reporting and a separately fixed-risk account view. O1 is never promotion-eligible and cannot replace O2 or influence champion ordering; if a human uses it to select, it enters the selectable trial family and invalidates the existing freeze.

Base historical option proxy uses the first common one-minute interval within five minutes and:

```text
buffer = max($0.05, 10% × bar_open, 25% × (bar_high - bar_low))
buy_proxy = bar_open + buffer
sell_proxy = max(0, bar_open - buffer)
```

Central fee assumption is $0.10 per contract, per leg, per side. Publish $0.00 and $0.25 diagnostics and a severe run using `high + $0.05` for buys, `max(0, low - $0.05)` for sells, doubled fees, and zero exit value for missing debit-structure exits. These are proxy assumptions, never fill claims.

## 13. Required falsification

Relative strength must publish raw target momentum beside residual momentum, beta distributions by fold/time, and fixed-risk leveraged/unleveraged comparisons. Reject/demote if the edge is only unnormalized leverage, one unstable beta period, current-pair leakage, a benchmark substitution, or a few dates. Repeat with the prescribed residual threshold neighbors only; alternate factor models are new candidates, not rescue diagnostics.

Compression breakout must publish exact timestamp overlap with intraday-continuation and opening-range-breakout signals, unique-versus-overlapping trade attribution, and incremental common-portfolio evidence. Remove volume/VWAP confirmation one at a time as falsifications. Reject/demote if the signal duplicates those strategies without incremental contribution, uses the just-completed bar inside the compression box, is dominated by one leverage/software episode, or is erased by next-observation/base option costs.

For both families:

- compare TQQQ with controls at identical fee-inclusive maximum-loss budgets, not equal share counts;
- demonstrate raw/adjusted/strike continuity across all TQQQ corporate actions;
- report TQQQ/IGV/QQQ dependence honestly and never count correlated echoes as independent successes;
- reject/demote when fewer than 75 OOS underlying trades over 40 active sessions, fewer than four populated quarters, fewer than 60% positive folds, one positive trade/date above 25% of positive contribution, or central sign instability;
- option support additionally requires at least 50 scored OOS trades over 30 dates, four quarters with at least five trades, missing-exit penalty no more than 10%, positive base net result, nonnegative severe/2× result, OOS drawdown no more than 4%, green prospective indicative-quote gate, and independent reproduction.

Family-wise adjusted evidence that does not pass is labeled `suggestive`, shadow, or demo-only, never statistically supported alpha.

## 14. Acceptance and integration gates

| Gate | Required evidence | Failure result |
|---|---|---|
| `C0_HANDOFF` | Baseline, native lock, immutable target/benchmark refs, Group C owner, both candidate IDs, and external reviewer recorded | Do not start outcome runs |
| `C1_DATA` | TQQQ/IGV/QQQ feasibility cards, at least 99% expected 15-minute bars, exact pair coverage, zero duplicates/OHLC failures, complete TQQQ split audit | Remove affected symbol or stop |
| `C2_SPEC_FREEZE` | Candidate cards, benchmark map, features, central/sensitivity configs, exits, costs, trial entries, and hashes frozen before P&L | New candidate/version required |
| `C3_SIGNAL` | Common-engine runs for both strategies, next-observation behavior, diagnostics, minimum sample/fold/concentration gates | `REJECTED` or `RESEARCH_COMPLETE` only |
| `C4_OPTION_PROXY` | PIT existence, simultaneous-leg coverage, O2 base/severe output, no reranking, Monday quote gate | No option-expression support |
| `C5_PLUGIN` | Packages, golden/boundary fixtures, deterministic output, semantic parity, forbidden-field/I/O tests | Not `INTEGRATION_READY` |
| `C6_PLATFORM_PARITY` | Platform-owner evidence for `G-R1`–`G-R6`, especially feature/catalog/exit/runner parity | Record failed/not implemented; do not claim closure |
| `C7_REPRODUCTION` | Non-author reproduces manifests, hashes, metrics, benchmark/leverage audits, a negative fixture, and parity on another available supported platform; record OS/CPU and investigate any hash difference | Not done |
| `C8_SELECTION` | Central quant includes all viewed trials, applies 2025 family-wide test, and freezes champion/fallback before 2026 | No promotion |

Passing Group C gates never authorizes paper trading. The release/risk/execution owners must separately close every paper-host, account, control, preflight, reservation, outbox/inbox, reconciliation, close/flatten, credential, and deployment gate.

## 15. Definition of done

Group C is done when both independently versioned packages are complete and reproducible, every prescribed pair-cell central/diagnostic result is published, benchmark and cluster dependence are explicit, feasibility exclusions and open integration gates remain visible, and the independent Group A reviewer signs both packages. Each card has exactly one truthful terminal state: `REJECTED`, `RESEARCH_COMPLETE`, `INTEGRATION_READY`, `PAPER_SHADOW`, `PAPER_DEMO_ONLY`, or `PAPER_CANDIDATE`. The packet owner may not declare `PAPER_ENABLED` or produce the central full-universe replay.

Normative references: [`../plans/STRATEGY_RESEARCH_PLAN.md`](../plans/STRATEGY_RESEARCH_PLAN.md), [`../architecture/STRATEGY_API.md`](../architecture/STRATEGY_API.md), and [`../architecture/RESEARCH_INTERFACE_FREEZE.md`](../architecture/RESEARCH_INTERFACE_FREEZE.md).
