# Research interface freeze

Status: **PUBLISHED FOR CREDENTIAL-FREE RESEARCH — not paper-trading authorization**

Published: 2026-08-29

Implementation commit: `cb03a7684fb67c6f0888333f6c3c2145e8645be9`

Dependency lock: `uv.lock` SHA-256 `b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf`

This release is sufficient for three packet owners to preregister all six assigned hypotheses, implement pure signal packages, run offline pair-cell research from centrally supplied immutable data, and return reproducible evidence. It does **not** certify the judged-account runtime, authorize broker credentials, or permit a candidate to self-promote.

## 1. What is frozen

Researchers build against these semantics until the release owner publishes a versioned replacement:

- `StrategyPluginV1.data_requirements(config)` declares the complete feature contract.
- `StrategyPluginV1.evaluate(context, config)` returns one deterministic semantic entry request or `NO_TRADE`.
- Plug-ins receive precomputed, hash-bound features; they do not receive raw bars, a clock, filesystem, network, database, broker, account, MCP object, or order client.
- Plug-ins may request only an authorized underlying plus `(template, horizon, risk tier, TTL)`. They never choose option symbols, strikes, expirations, legs, quantity, price, order type, time in force, account, or broker action.
- The host owns source-hash verification, registry authorization, contract selection, sizing, risk approval, order planning, execution, position management, and lifecycle promotion.
- `NO_TRADE`, `REJECTED`, `NOT_SELECTED_BY_FEASIBILITY`, and `INSUFFICIENT_EVIDENCE` are valid results.

The exact Python contract is in [`STRATEGY_API.md`](STRATEGY_API.md). The research protocol is in [`../plans/STRATEGY_RESEARCH_PLAN.md`](../plans/STRATEGY_RESEARCH_PLAN.md).

## 2. Published release values

| Interface | Published value | Meaning for a researcher |
|---|---|---|
| Source snapshot | `cb03a7684fb67c6f0888333f6c3c2145e8645be9` | Stop if the supplied checkout differs. |
| Dependency lock | `sha256:b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf` | Stop if `uv.lock` differs. |
| Registry schema | `strategy-registry/v1` | Candidate registry files are proposals only and start `research_only`. |
| Loaded registry hash | `sha256:fdbe412038def1df8b3c1e552cbbfa42c300d4e73e5ac74cd92b4db233893a04` | Proves the current fixture registry authority surface; it does not register any research candidate. |
| Template catalog schema | `template-catalog/v1` | Research uses call/put debit spreads and the frozen O2 policy below. |
| Loaded catalog hash | `sha256:74906ee706cef3a52b77cb84e2f7b80c66bbc6b0e63ad3982be9e0ef0e02076e` | Bind this catalog in every run manifest. |
| Host fixture feature contract | `sha256:3e3cfa8a1047dda69b0c829d0b1153f9258f4356c23ffb1def6a8601ff3445bc` | Fixture evidence only. Do not reuse it for a research strategy unless the keys and formulas are exactly identical. |
| Arbitration helper | `packages/strategy_sdk/arbitration.py` | Central compatible-symbol replay uses this pure ranking/tie-break implementation. |
| Arbitration source-file SHA-256 | `864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5` | Record this with later full-universe replay evidence. |
| Position-policy implementation | `packages/position_manager/manager.py` | Central research replay may target the two policy IDs below; strategy plug-ins remain entry-only. |
| Position-manager source-file SHA-256 | `c77bfe135c4cb13eb530e9d390f2c6ceab1e8aaced762489b849ee0d82bd69b7` | Research parity reference, not proof of durable broker flattening. |
| Contract schemas | `schemas/v1/*.json` | Generated contract snapshots; drift is checked by `tests/contract/test_schema_export.py`. |
| Host interface tests | Section 8 command | Baseline host authorization/feature/arbitration evidence at the pinned commit. |

Each strategy package must define and freeze a candidate-specific `feature_contract.yaml` and its canonical hash **before viewing outcome P&L**. Until then, the integration card records `CANDIDATE_DEFINED_AND_HASHED_BEFORE_OUTCOME_RUN`; this is a required candidate deliverable, not a missing platform release value.

Each owner also freezes their exact `reason_codes.yaml`. There is intentionally no single global strategy reason-code hash; host and registry refusal codes remain platform-owned.

## 3. Research data and evidence boundary

- The data steward, not individual researchers, collects the shared Alpaca data once and publishes immutable manifests and hashes.
- Underlying history is explicitly Alpaca stock bars with `feed=iex`.
- Current options readiness uses the free indicative feed only where the endpoint accepts a feed argument.
- Historical option bars/trades are labeled non-executable proxies. They are not reconstructed OPRA/NBBO quotes or fill evidence.
- Every normalized observation carries event time, available time, ingestion time, endpoint/tool identity, feed or explicit no-feed sentinel, pagination provenance, and raw artifact hash.
- Missing, stale, future, nonfinite, schema-mismatched, or quality-rejected input produces a failed gate or `NO_TRADE`; it does not trigger another vendor, feed, credential, or hand-filled patch.

Before alpha outcomes are opened, the data steward publishes `research/shared/selection/option_proxy_feasibility_manifest.json`. It selects at most three symbols using blinded entitlement and coverage fields. Every unselected symbol still receives underlying-signal research and an explicit `NOT_SELECTED_BY_FEASIBILITY` option artifact.

## 4. Frozen portfolio and option-expression semantics

Pair-cell runs retain both owned symbol rows and do not select a local winner. After all six family packages are frozen, the central quant/release owner alone runs the compatible-symbol and full-universe replay through `packages/strategy_sdk/arbitration.py`:

1. remove rows failing data, feature, session, family, cluster, cooldown, or exposure gates;
2. rank by normalized opportunity score;
3. break exact ties by `SPY, QQQ, TQQQ, SMH, SOXL, IGV`;
4. emit at most one semantic entry request; and
5. record every eligible, rejected, selected, and suppressed row with a reason code.

The promotion-eligible option expression is O2 debit vertical:

- `CALL_DEBIT_SPREAD_V1` for bullish and `PUT_DEBIT_SPREAD_V1` for bearish;
- 7–14 calendar DTE central bucket; 15–21 DTE diagnostic only;
- long strike nearest raw spot with an exact-distance tie toward OTM;
- same-expiry short target approximately 1% farther OTM, rounded outward to the next listed standard strike;
- atomic option-only vertical with positive debit strictly below spread width; and
- integer quantity sized to fee-inclusive maximum loss under the centrally registered `TINY` budget.

The current catalog is published and loaded. Exact research/runtime parity for every OTM tie, outward-strike, fee, quantity, maximum-loss, refusal, and historical selection case remains an integration deliverable; researchers must not claim that pair-cell option proxies prove runtime execution parity.

## 5. Clock, state, and exit semantics

- Decisions use completed 15-minute IEX intervals with availability at `interval_end + 1 second`.
- The underlying execution proxy uses the next eligible one-minute observation.
- A candidate maintains deterministic, schema-limited state and increments sequence exactly once per evaluation.
- V1 permits no overnight strategy position and at most one nonterminal position/order in the portfolio replay.
- Trend strategies bind `TREND_VWAP_OR_60M_V1`; VWAP reversion binds `REVERSION_VWAP_TOUCH_OR_60M_V1`.
- The final-Thursday research rule allows no new entry after 13:30 ET, begins flatten by 15:15, and requires flat by 15:30.

The central position manager implements the named policy decisions and reduce-only plan construction at the pinned commit. Durable Postgres fill/position lifecycle, broker-confirmed-flat deadlines, restart/concurrency behavior, and operator authority remain release-owned paper-safety work. Researchers replay the frozen economic exit rules; they do not implement broker exits or claim paper readiness.

## 6. Canonical researcher handoff

Each packet owner returns one importable package per assigned strategy family, for two packages total:

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
├── scripts/reproduce.sh
├── tests/
│   ├── fixtures/
│   ├── golden/
│   ├── test_contract.py
│   ├── test_thresholds.py
│   ├── test_no_trade.py
│   ├── test_determinism.py
│   ├── test_boundary.py
│   └── test_parity.py
└── evidence/promotion.json
```

The separate `research/candidates/<candidate_id>/` evidence tree contains the preregistration, feature/config/reason/state contracts, immutable data references, deterministic run artifacts, golden contexts/evaluations, conformance/parity reports, reviewer reproduction, limitations, and a non-authorizing promotion card.

There is no claimed shared historical backtester in this release. Each package must ship an offline executable `scripts/reproduce.sh` that accepts exactly `--data-manifest`, `--feasibility-manifest`, and `--output`; refuses a nonempty output directory; validates all pinned hashes; runs package tests; makes no network or credential call; and emits deterministic evidence. Central full-universe replay is a later release-owner step.

## 7. Gate status at publication

| Gate | Research status | What still blocks integration or paper use |
|---|---|---|
| `G-R1_REGISTRY_AUTHORITY` | **CLOSED for host research interface** | Research packages are not registered. Candidate source/config/feature/evidence hashes and non-author review must be proposed, independently checked, and centrally merged. |
| `G-R2_CATALOG_PARITY` | **PARTIAL; research may start** | Catalog loading/hash authority is present. Candidate-specific OTM tie/outward-strike, fees, quantity, maximum-loss, and refusal parity must still be proven. |
| `G-R3_OUTPUT_BINDING` | **CLOSED for host baseline** | Every candidate must pass the same golden/negative cases after its registry proposal is reviewed. |
| `G-R4_FEATURE_CONTRACT` | **CLOSED for host shape; candidate-owned formulas open** | The fixture proves namespaced, hash-bound input enforcement. Each family must publish exact formulas, lookbacks, availability rules, worked examples, key ordering, and its own hash. |
| `G-R5_EXIT_OWNERSHIP` | **CLOSED for credential-free research semantics; paper proof open** | Named central policies and reduce-only construction exist. Durable fill/position persistence, live broker deadlines, restart/concurrency, and broker-confirmed flat are not proven. |
| `G-R6_RUNNER_ISOLATION` | **PARTIAL; offline package work may start** | Pre-import authorization/source rehash and fail-closed limits have tests, but deployment-equivalent image build, OS-level egress/filesystem containment, and malicious-package evidence remain release work. |

Partial gates do not block credential-free hypothesis work, pure plug-in implementation, or offline pair-cell research. They do block `INTEGRATION_READY`, `PAPER_CANDIDATE`, `PAPER_ENABLED`, and judged-account access until their candidate- and runtime-specific evidence passes.

## 8. Exact verification commands

Run from repository root on native Apple Silicon:

```zsh
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen python -c 'import platform; assert platform.machine() == "arm64", platform.machine()'
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen pytest -q tests/security/test_strategy_authorization.py tests/contract/test_feature_contract.py tests/contract/test_strategy_arbitration.py
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen pytest -q
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen ruff check .
```

The first three targeted test files are the published host-interface baseline. They are not a candidate backtest. A candidate records `host_interface_baseline=PASSED_AT_cb03a76` only when this exact command passes, and records `candidate_host_conformance=NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED` until the release owner runs its golden cases through the host.

## 9. Paper-safety non-claims

This research freeze does not prove or authorize:

- a complete real-Postgres fill, reservation, outbox, position, and flatten lifecycle under concurrency and restart;
- blocking a fresh entry solely from all forms of existing reconciled exposure;
- authenticated arm/halt operator authority derived from broker truth;
- execution-side close-debit/slippage authorization;
- production image startup, OS egress allowlisting, secret isolation, or deployment-equivalent runner containment;
- partial-fill, unknown-submit, orphan-leg, assignment, exercise, expiration, or broker-confirmed final-flatten handling; or
- judged-account credentials, paper arming, or any live-trading mode.

Until the release owner closes those controls with dated evidence, the permissible work is credential-free deterministic research and explicitly labeled fixture/development-account demonstration. A profitable result cannot waive a safety gate.

## 10. Team handoff and change control

Distribute [`../research/README.md`](../research/README.md) plus the assigned packet:

- Group A: [`../research/GROUP_A_BROAD_TECH_PLAN.md`](../research/GROUP_A_BROAD_TECH_PLAN.md)
- Group B: [`../research/GROUP_B_SEMICONDUCTOR_PLAN.md`](../research/GROUP_B_SEMICONDUCTOR_PLAN.md)
- Group C: [`../research/GROUP_C_LEVERAGED_SOFTWARE_PLAN.md`](../research/GROUP_C_LEVERAGED_SOFTWARE_PLAN.md)

Any change to a feature formula/name, compatible symbol scope, central threshold, state rule, option selector, cost assumption, allocation/tie-break, or position policy creates a new version/hash before additional outcome P&L is viewed. The release owner and one consuming owner approve interface changes; risk/execution changes also require the risk and execution reviewers.
