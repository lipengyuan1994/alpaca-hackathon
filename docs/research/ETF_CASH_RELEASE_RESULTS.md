# ETF cash research release results

This is the deterministic research release produced by
`packages/etf_cash_research`. It is not a live-trading authorization and the
Gemini news veto is not included in these returns.

The collected SIP input covered all five symbols from 2022-09-01 through
2026-09-18: 1,015 regular sessions per symbol and 5,075 normalized bars. The
collection manifest hash was
`sha256:612ae2a50742277afb72ae18e1717e254d4e8aa906e81caacbb602eaa793602c`.
The frozen protocol hash was
`sha256:f6ccda151678f052b0fb26eb2f395a51e42788d6eb1462d14802d0155ff7b8be`.

Validation ranked the primary candidates and froze this order:

1. `S01__QQQM_SMH__primary` — dual-momentum rotation, QQQM + SMH
2. `S10__QQQM_SMH__primary` — fixed trend/pullback ensemble, QQQM + SMH
3. `S08__QQQM_SMH__primary` — QQQM core with semiconductor breakout sleeve, QQQM + SMH

The frozen shortlist hash is
`sha256:22729bf0f72f8e3988e7160f27547efaae6f4978452822540c1e901658a2f751`.
All three passed positive base/stress validation, positive base/stress holdout,
and the 35% drawdown gates. The first frozen candidate, S01 QQQM + SMH, is the
nominee. Its final-selection envelope is
`sha256:06edc614830bd3b220925a475fc6a46194629dd3f93c87229b76147e74988624`.

| Phase | Candidate | Base return | Stress return | Base max drawdown | Ending equity (base) |
|---|---|---:|---:|---:|---:|
| Development | S01 QQQM + SMH | 3.88% | 3.52% | 7.43% | $1,038.77 |
| Development | S10 QQQM + SMH | 32.81% | 31.63% | 18.70% | $1,328.13 |
| Development | S08 QQQM + SMH | 26.54% | 25.97% | 11.98% | $1,265.40 |
| Validation | S01 QQQM + SMH | 24.76% | 24.16% | 9.64% | $1,247.57 |
| Validation | S10 QQQM + SMH | 21.14% | 19.99% | 7.94% | $1,211.35 |
| Validation | S08 QQQM + SMH | 16.73% | 16.21% | 7.63% | $1,167.26 |
| Holdout | S01 QQQM + SMH | 68.28% | 68.18% | 24.46% | $1,682.84 |
| Holdout | S10 QQQM + SMH | 54.41% | 53.39% | 19.02% | $1,544.12 |
| Holdout | S08 QQQM + SMH | 18.05% | 17.11% | 10.77% | $1,180.54 |
| Continuous | S01 QQQM + SMH | 138.85% | 137.21% | 24.49% | $2,388.55 |
| Continuous | S10 QQQM + SMH | 164.79% | 158.59% | 19.02% | $2,647.87 |
| Continuous | S08 QQQM + SMH | 77.28% | 74.55% | 11.98% | $1,772.79 |

For context, validation base returns were QQQ 23.64%, SPY 17.01%, QQQM
23.71%, SOXX 19.13%, SMH 32.14%, static QQQM + SOXX 21.42%, and static QQQM +
SMH 27.93%. Holdout base returns were QQQ 21.01%, SPY 15.92%, QQQM 21.00%,
SOXX 98.80%, SMH 80.54%, static QQQM + SOXX 59.90%, and static QQQM + SMH
50.77%. These are comparisons, not requirements to beat passive exposure.

The release directories contain the complete artifacts, including all 20
primary candidates, benchmarks, signals, orders, fills, cash ledger, daily
equity, trades, metrics, 2,000 paired moving-block bootstrap intervals, and
20-candidate one-session delay stress files:

The permanent verified copy is on T9:

- Dataset: `/Volumes/T9/TradingResearch/datasets/alpaca/us-etf-daily/612ae2a50742277a/data_manifest.json`
- Complete results, including frozen and final selection files: `/Volumes/T9/TradingResearch/studies/etf-cash-v1/2026-09-20-complete-release/`
- Catalog: `/Volumes/T9/TradingResearch/catalog.json`

Use `etf-cash-research verify-data` before reuse. The original temporary
directories below remain available but are not the permanent archive.
An offline `reproduce --phase continuous` run from the T9 manifest matched the
original 60-row, 76-column continuous leaderboard, including every numeric
P&L and drawdown field (maximum difference under `1e-8`).

- `/tmp/etf-cash-development-finalrelease.yfsSLj`
- `/tmp/etf-cash-validation-finalrelease.6rbmsK`
- `/tmp/etf-cash-holdout-finalrelease.8Acofz`
- `/tmp/etf-cash-continuous-finalrelease.hYfXXc`
- `/tmp/etf-cash-report-finalrelease.QAhTWB/report.html`
- `/tmp/etf-cash-report-finalrelease.QAhTWB/report.md`

The hash-locked shortlist and final disposition are also checked into
[`ETF_CASH_SELECTION.json`](ETF_CASH_SELECTION.json) and
[`ETF_CASH_FINAL_SELECTION.json`](ETF_CASH_FINAL_SELECTION.json).

The existing v13.5 paper wheel was not changed, and no account, order, or live
execution endpoint is reachable from this research package.
