# Repaired ETF research v3

Deterministic research backtest; historical periods reused; LLM overlay not performance-tested; no live authorization.

## Verified release — September 20, 2026

Open [the release summary](../../output/etf_cash_research_v3/20260920_repaired_63_tracefix/index.html), [full performance report](../../output/etf_cash_research_v3/20260920_repaired_63_tracefix/report.html), or [complete qualification and benchmark audit](../../output/etf_cash_research_v3/20260920_repaired_63_tracefix/qualification_details.html).

The release passed 141 research tests on native ARM64 Python 3.12. All 2,428 candidate/benchmark accounts and 30 shadow accounts reconcile independently. Offline reproduction, with networking disabled, matched all 39,334 compared artifacts. Independent qualification reconstruction reproduced all 63 decisions and the three shortlists. See `release_validation.json`, `offline_reproduction.json`, and `migration_evidence.md` in the release directory.

The research leaders are A01/QQQM+SMH, L11/TQQQ+SOXL, and L11/SPXL+SOXL. They are selected by the frozen evaluation-window ranking, not by continuous return alone. All failed candidates remain in the report. Original v1/v2 evidence and selections are preserved. The first v3 attempt is retained as diagnostic history: three order logs had nondeterministic simultaneous cancellation row ordering. The fresh `tracefix` release corrected that ordering without changing any outcome-bearing artifact.

HTML references and all 315 SVG charts passed structural checks. The browser URL policy blocked visual preview; no alternative browser access was used.

The inventory-verified permanent archive is `/Volumes/T9/TradingResearch/studies/etf-cash-v3/20260920_repaired_63_tracefix/`. Its catalog entry references dataset snapshot `36733396b8a35f06`. Archive publication is complete; no scheduler was added.

This round registers 63 primary candidates: 20 original S candidates, 11 A candidates, and 32 leveraged L candidates. It uses a new explicit-intent cash engine. Old v1/v2 artifacts and selections remain historical records. Strategy development workers use `gpt-5.6-luna` with `max` reasoning effort.

## Frozen inputs

- Protocol: `configs/etf_cash_research_v3.yaml`.
- Exact manifest: `/Volumes/T9/TradingResearch/datasets/alpaca/us-etf-daily/36733396b8a35f06/data_manifest.json`.
- SIP/raw daily bars for QQQM, SMH, SOXX, QQQ, SPY, TQQQ, SOXL, SPXL. QQQM begins at fund inception; other instruments cover January 2019 onward.
- Corporate-action resolution: `configs/etf_cash_action_resolutions_v3.json`. The provider duplicated QQQ's September 2022 distribution with two payable dates. The issuer confirms October 31. Both raw records remain archived; the resolution explicitly selects the verified record.
- T9 must be mounted with UUID `8D84339B-38CA-350F-A7C6-A3DDE650156E`. Never replace a missing mount with an internal-disk directory.

## Execution semantics

Signals use previous-session causal features. Forward split-consistent feature units do not rewrite earlier observations when a future split occurs. Raw prices drive executions and equity. Quantities are fixed using prior-close equity and prices; the current open affects fills and affordability only. Dividend entitlement precedes ex-date purchases. Payable dates and the provider's explicit settlement dates control cash availability. No borrowing, shorting, options, account endpoints, or order-submission endpoints are used.

Independent accounts start with $1,000. Fixed-share positions hold quantities between entry/exit events. Scheduled strategies rebalance on execution-session review boundaries. Holding ages and cooldowns follow actual fills. Delayed orders retain quantities and original signal timestamps. Virtual components share physical holdings and one cash ledger; internal transfers conserve ownership and incur no market costs.

## Run and reproduce

Use the verified native ARM64 `.venv/bin/python3` (3.12) and `/opt/homebrew/bin/uv`. The audit records interpreter, manager, and compiled dependency architecture. Commands reject stale acceptance or changed input hashes.

```sh
.venv/bin/python3 -m packages.etf_cash_research.cli audit-engine \
  --protocol configs/etf_cash_research_v3.yaml \
  --output output/etf_cash_research_v3/acceptance-new

.venv/bin/python3 -m packages.etf_cash_research.cli run-study \
  --protocol configs/etf_cash_research_v3.yaml \
  --data-manifest /Volumes/T9/TradingResearch/datasets/alpaca/us-etf-daily/36733396b8a35f06/data_manifest.json \
  --acceptance output/etf_cash_research_v3/acceptance-new/engine_acceptance.json \
  --output output/etf_cash_research_v3/study-new

.venv/bin/python3 -m packages.etf_cash_research.cli rank-study \
  --study output/etf_cash_research_v3/study-new

.venv/bin/python3 -m packages.etf_cash_research.cli report-study \
  --study output/etf_cash_research_v3/study-new \
  --output output/etf_cash_research_v3/study-new/report.html

.venv/bin/python3 -m packages.etf_cash_research.cli reproduce-study \
  --study output/etf_cash_research_v3/study-new \
  --output output/etf_cash_research_v3/reproduction-new

.venv/bin/python3 -m packages.etf_cash_research.cli archive-study \
  --study output/etf_cash_research_v3/study-new
```

Use `--resume` only for the same frozen binding. Completed runs are verified before reuse. An interrupted, unsealed run requires inspection, never silent acceptance. `reproduce-study` disables network in the parent and calculation workers, reads saved inputs, and compares deterministic artifacts. Source files are bundled under `source/`; source hashes are bound to acceptance.

## Outputs and interpretation

Each run is stored under `runs/<candidate>/<window>/<cost>/`, including equity, signals, orders, fills, cash/component ledgers, trades, physical FIFO profit attribution, and independent reconstruction evidence. A09 additionally stores its unthrottled shadow account. The top-level leaderboard and selection include failures as well as shortlists.

The continuous account runs September 19, 2023 through September 18, 2026. Its P&L is an actual simulated dollar balance change. The six-window evaluation index chains independent flat-start accounts; it is a normalized analysis index, not continuously traded wealth. The 2022 and leveraged 2020 stress accounts remain separate. No parameter optimization or untouched-holdout claim is made.

Qualification requires positive continuous and pooled base/stress returns, at least four positive base windows, all specified drawdown gates (35% unleveraged, 50% leveraged), positive delayed pooled returns, and complete accounting/data evidence. Selection never forces a winner. Bootstrap intervals are descriptive, paired, and do not remove strategy-selection bias.

The archive workflow is explicit. There is no automatic capture or scheduler.
