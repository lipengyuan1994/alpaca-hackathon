# Research team handoff

Status: **ready to distribute for credential-free research**

Pinned implementation: `cb03a7684fb67c6f0888333f6c3c2145e8645be9`

Pinned `uv.lock` SHA-256: `b846dc0b4d52be240cbb131e8267a5bf5ed4659b21570573b9ea1d48dcc865cf`

This page is the front door for three research owners. Each owner receives one packet, researches both strategy families in that packet, and returns two separately versioned integration-shaped packages plus reproducible evidence. Researchers do not need Alpaca credentials, a broker account, deployment access, or permission to trade.

## 1. Assignments

| Packet recipient | Owned families | Required pair cells | Independent review |
|---|---|---|---|
| Group A research owner | `intraday_continuation_v1`; `vwap_reversion_v1` | SPY, QQQ | Group B research owner reviews both packages |
| Group B research owner | `opening_range_breakout_v1`; `gap_continuation_v1` | SMH, SOXL | Group C research owner reviews both packages |
| Group C research owner | `relative_strength_residual_v1`; `compression_breakout_v1` | TQQQ, IGV, with immutable QQQ controls for relative strength | Group A research owner reviews both packages |

Send each research owner this page plus exactly one packet:

- [Group A — SPY and QQQ](GROUP_A_BROAD_TECH_PLAN.md)
- [Group B — SMH and SOXL](GROUP_B_SEMICONDUCTOR_PLAN.md)
- [Group C — TQQQ and IGV](GROUP_C_LEVERAGED_SOFTWARE_PLAN.md)

Every packet owner starts with [trading foundation](trading_foundation.md) and [quant trading basics](quant_trading_basic.md), then follows the normative [strategy research protocol](../plans/STRATEGY_RESEARCH_PLAN.md), [strategy API](../architecture/STRATEGY_API.md), and [published research interface freeze](../architecture/RESEARCH_INTERFACE_FREEZE.md). If a packet conflicts with those documents, stop and ask the release owner.

## 2. What the platform/data owner must supply first

Researchers start outcome-bearing work only after receiving:

1. a checkout or source archive at the pinned implementation commit;
2. the exact pinned `uv.lock`;
3. a centrally collected immutable Alpaca data manifest covering the required underlying/control symbols;
4. `research/shared/entitlement_probe.json` showing the approved free-tier endpoints and feed behavior;
5. a signed, blinded `research/shared/selection/option_proxy_feasibility_manifest.json`; and
6. the artifact schema plus hashes for every supplied raw and normalized input.

The data steward collects once for the team. Researchers must not build private downloaders, request competition credentials, switch vendors/feeds, hand-fill missing data, or search for a more favorable sample. A missing prerequisite is a visible failed gate, not permission to improvise.

The data steward uses the repository's [read-only Alpaca collector](ALPACA_DATA_COLLECTOR.md) from an approved credential runtime. It writes raw provider pages, normalized Parquet, a canonical `data_manifest.json`, and an unattested entitlement probe to a new empty directory. The separate review/attestation step remains mandatory before researchers consume the manifest.

```text
research-data-collect \
  --spec configs/research_data_collection_v1.yaml \
  --output /absolute/path/to/empty-collection-directory
```

Historical option observations are supplied only through a frozen, ordered request manifest; current option readiness uses a separate sorted contract-symbol request and explicit `feed=indicative`. Neither request accepts credentials or enables trading.

## 3. Required workflow for each packet owner

1. Verify Python 3.12, the source commit, and lock hash; record operating system and CPU architecture in the run manifest. Windows, Linux, and non-ARM macOS are supported for offline research.
2. For each assigned family, and before viewing its outcome P&L, separately freeze `strategy_card.md`, `hypothesis.yaml`, `feature_contract.yaml`, central config, sensitivities, reason codes, state schema, compatible symbol set, option-expression policy, exit-policy ID, cost policy, and falsification conditions.
3. Compute and record a distinct candidate-specific feature-contract hash for each family. Do not reuse the host fixture feature hash unless the complete formulas and keys truly match.
4. Implement each economic rule once in its own pure `signal.py`; its offline adapter and `Plugin.evaluate()` call that same function.
5. Run the prescribed pair cells, folds, null, costs, sensitivities, and falsifications for both families. Preserve all attempted trials, including failures.
6. Build two canonical plug-in packages, each with golden contexts/evaluations, negative/boundary tests, and one deterministic cross-platform Python reproduction entry point. POSIX and PowerShell wrappers may call that same entry point.
7. Ask the designated owner of another packet to reproduce both packages' hashes, metrics, and at least one negative fixture per package. The reviewer reports defects but does not tune a reviewed rule after seeing outcomes.
8. Return both packages, each with one truthful state: `REJECTED`, `RESEARCH_COMPLETE`, or a request for central integration review. Do not declare `PAPER_ENABLED`.

Pair-cell work does not choose a symbol winner. After all six families are frozen, the central quant/release owner alone runs each unchanged family over its compatible universe and applies the common arbitration, cluster/concurrency constraints, exact option selector/sizer, multiple-testing controls, and central exit policy.

## 4. Exact return package

Each packet owner returns the two trees below once per assigned family.

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
│   ├── reason_codes.py
│   └── reproduce.py
├── scripts/reproduce.sh                 # optional POSIX wrapper
├── scripts/reproduce.ps1                # optional PowerShell wrapper
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

research/candidates/<candidate_id>/
├── strategy_card.md
├── hypothesis.yaml
├── feature_contract.yaml
├── central_config.json
├── sensitivities.yaml
├── reason_codes.yaml
├── state_schema.json
├── data_refs.json
├── artifact_schema.json
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
│   └── limitations.md
├── integration/
│   ├── registry_candidate.yaml
│   ├── golden_contexts/
│   ├── golden_evaluations/
│   ├── backtest_runtime_parity.json
│   ├── conformance_report.json
│   ├── catalog_parity.json
│   └── integration_checklist.md
└── promotion_card.md
```

An unselected option-proxy symbol still returns schema-valid empty option tables plus `option_proxy_not_selected.json` with status `NOT_SELECTED_BY_FEASIBILITY`.

## 5. Reproducibility contract

Each package supplies a platform-neutral offline command:

```text
uv run python -m <plugin_id>_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

It must refuse a nonempty output directory, validate the pinned commit/lock/data/config hashes, run package tests, make no network or credential call, and produce deterministic authoritative artifacts. Running it twice from the same inputs must yield identical authoritative hashes. A shell or PowerShell wrapper must invoke this same Python module; it cannot contain different research logic.

The following host-interface baseline is run only by the central Mac/ARM64 owner:

```zsh
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen pytest -q tests/security/test_strategy_authorization.py tests/contract/test_feature_contract.py tests/contract/test_strategy_arbitration.py
```

Passing that command means only `host_interface_baseline=PASSED_AT_cb03a76`. Candidate host conformance remains `NOT_RUN_UNTIL_REGISTRY_PROPOSAL_REVIEWED` until the release owner tests the submitted package through the isolated host.

## 6. What “good” looks like

A deliverable is reviewable when:

- all timestamps are point-in-time safe and every input is hash-addressed;
- the central and every prescribed sensitivity are published, not cherry-picked;
- inactive market dates are retained as zero-return dates in portfolio metrics;
- net percentage return, annualized Sharpe where sample size permits, maximum drawdown, downside risk, turnover, exposure, and concentration are reported with explicit sample counts;
- underlying signal evidence is separated from option-proxy evidence and from broker/paper telemetry;
- fee, spread/tick, missing-exit, quote-coverage, leverage, and regime stresses are visible;
- every `NO_TRADE`, rejection, suppressed row, and failed falsification is preserved;
- `signal.py` and `Plugin.evaluate()` agree on golden rows and boundary cases;
- no package contains exact order authority, hidden I/O, self-promotion, or credentials; and
- a non-author reviewer reproduces the claimed hashes and records any deviation.

Headline P&L or Sharpe alone is not acceptance. A candidate with weak coverage, unstable parameters, concentrated profit, unrealistic option marks, or failed reproduction is rejected even if its backtest is profitable.

## 7. Stop conditions

Stop and report the exact failed gate if any of these occur:

- source commit or lock hash mismatch;
- missing/unsigned data or feasibility manifest;
- feed/entitlement mismatch, incomplete pagination, timestamp ambiguity, or invalid corporate-action join;
- outcome P&L was viewed before the hypothesis/config/feature/selection identity was frozen;
- a requested feature is missing, stale, future, nonfinite, or schema-mismatched;
- the package needs network, credentials, raw broker objects, or a new vendor;
- deterministic rerun hashes differ;
- the reviewer cannot reproduce the result; or
- the only way to pass is to tune an unregistered parameter, switch symbols, or discard an unfavorable trial.

Returning `NO_TRADE`, `REJECTED`, or `INSUFFICIENT_EVIDENCE` is a successful research outcome when the data do not support safe promotion.
