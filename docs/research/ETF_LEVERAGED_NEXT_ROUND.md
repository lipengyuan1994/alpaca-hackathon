# Further leveraged ETF exploration — proposed round, 2026-09-20

Implementation update: these frozen hypotheses were implemented and tested in the
[repaired v3 release](ETF_CASH_V3_RESEARCH.md). The proposal below is preserved as
the original pre-results specification.

Status: hypotheses specified before new candidate returns are inspected; not
implemented or backtested. No live or paper authorization. Retain all previous
results as historical artifacts. New results require the corrected execution
engine, not post-processing of old fills.

## Objective and prerequisites

Explore high-return strategies that can adapt when semiconductors lose leadership
but broad large-cap equities remain strong. Test six hypotheses on both existing
pairs: TQQQ/SOXL and SPXL/SOXL (12 new primary candidates). Keep $1,000 independent
cash accounts, no account borrowing, 99% maximum investment and the previously
agreed 50% leveraged drawdown ceiling. No requirement to force a winner.

First repair and fixture-test all blockers in ETF_CASH_CORRECTION_AUDIT.md:
prior-close quantities, execution-session review schedules, actual-fill state,
frozen delayed quantities, and complete shadow/component accounting. Independently
reconstruct accounts. Do not collect new candidate returns before acceptance.

Verify native ARM64 manager, Python and compiled dependencies. Discover and verify
the mounted T9 archive using list-data/verify-data; freeze an exact manifest and
its coverage before simulations. Never silently substitute an unverified snapshot.

## Common definitions

B = TQQQ or SPXL; S = SOXL. U = QQQ for TQQQ and SPY for SPXL; V = SOXX.
V is a sector proxy, not a perfect replication of SOXL's changing benchmark.
Signals use split-consistent completed closes; actual fund raw prices and actions
drive all execution/P&L. Use existing v2 indicator definitions and cash accounting.

An eligible proxy has C>SMA200 and R126>0. Missing indicators block purchases.
Equality retains state. Review on the first exchange session of each ISO week,
including the first simulation session. Daily exits precede new purchases;
same-session re-entry following a full exit is prohibited. Purchases execute at
the next open from prior-close quantities; proceeds must settle before reuse.

All targets below are fractions of total equity. Review-date targets rebalance;
between reviews only specified reductions occur. A 50% ETF allocation is roughly
150% underlying exposure, not a precise portfolio beta. Cash is intentional.

## L11 — Broad-first semiconductor leadership

Default to B when U is eligible. Permit S only if V is eligible,
R63(V)>R63(U), and V/U>SMA20(V/U). At weekly review, target 49.5% S and
49.5% B when both qualify; 99% B when only the broad allocation qualifies;
49.5% S and cash when only the semiconductor allocation qualifies; otherwise cash.
Exit a held fund daily when its proxy falls below SMA200. Weekly review alone
restores or redirects allocation. Hypothesis: avoid persistent sector overweight
when leadership shifts to platform companies.

## L12 — Fast/slow agreement rotation

At weekly review, among eligible proxies choose the winner of
0.5*R21+0.5*R63; ties favor U. Require that proxy's R21>0; otherwise cash.
Target its fund at 0.99*min(1,0.40/sigma60(actual fund)); nonpositive/invalid
volatility blocks entry. Exit daily below proxy SMA100. Hypothesis: react to
leadership changes faster than six-month momentum, accepting extra turnover.

## L13 — Trend-persistence exposure

At weekly review, calculate each proxy's fraction of the last 20 closes above
its contemporaneous SMA100. Eligible proxies receive nominal weights of 60% B
and 40% S, multiplied by that fraction and 0.99. Ineligible weights stay cash;
do not redistribute. Exit daily below proxy SMA200. Hypothesis: sustained trends
justify more leveraged exposure than repeatedly interrupted ones.

## L14 — Downside-volatility allocation

At weekly review, eligible proxies receive nominal 60% B/40% S; others zero.
Calculate the nominal-weight portfolio's last 60 daily actual ETF returns.
Downside volatility = sqrt(252*mean(min(return,0)^2)); scale weights by
0.99*min(1,0.25/downside_volatility), using multiplier one at zero volatility.
Missing observations block increases. Daily proxy SMA200 exits. Hypothesis:
permit upside volatility while reducing exposure after downside instability.

## L15 — Trend-quality rotation

Weekly selection uses highest 0.5*R63+0.5*R126 among eligible proxies; ties U.
For the selected proxy define ER63=abs(C-C[-63])/sum(abs(close changes),63).
Target selected fund at 0.99*min(1,ER63/0.30); zero denominator means cash.
Daily exit below proxy SMA200. Hypothesis: leveraged exposure benefits from
directional persistence and suffers in noisy sideways paths.

## L16 — Broad-first allocation with fast risk reduction

Use L11 weekly targets as the base. Cap each allocation at 50% of its base target
when its proxy C<EMA20 or R5<=-0.06; set it to zero below SMA200. These risk
reductions apply daily and may not increase shares. Restoration is allowed only
on a weekly review after five consecutive completed signal sessions above EMA20
and with R5>-0.06. While reduced, retain the minimum previously imposed target
until restoration; do not create daily upward rebalances. Hypothesis: reduce
leverage more quickly than it is restored without assuming threshold-price fills.

## Comparison and decision rules

- Retain 2023-09-19–2026-09-18 and the same six independent windows as exploratory
  reused history; separately simulate the continuous account. No untouched-holdout
  claim. Run 2022 and 2020 stress periods when verified data covers them.
- Reuse v2 leveraged costs (10/25/50 bps plus $0.01 per external sell), settlement,
  fractional rounding and delayed-quantity tests. Keep the 50% drawdown gate and
  all prior positive-return/window-consistency gates; incomplete evidence blocks
  full qualification. No parameter sensitivities or optimization in this round.
- Rerun all old candidates retained for comparison on the same repaired engine
  and exact data snapshot. Preserve original rankings as history. Keep Track A's
  35% ceiling separate from leveraged candidates' 50% ceiling.
- Compare SPY, QQQ, relevant leveraged buy-and-hold and static pair benchmarks,
  cash, S01, S10, A03, all old L01–L10 candidates, and the new L11–L16 candidates.
  Show return, dollar P&L, drawdown, worst window, recovery time, costs, exposure,
  daily beta, and severe/delay behavior together; highest return alone is not
  evidence of added strategy value.
- Predefine conditional diagnostics: prior-close R126(U)>0 and R126(V)<R126(U)
  for broad leadership; reverse relative inequality for sector leadership;
  both negative for joint weakness. These labels summarize regime behavior and
  do not turn the fragmented subsets into independently tradable accounts.
- Also show deterministic hypothetical paths (broad up/sector flat, broad up/
  sector down, sector resurgence, alternating choppy returns, sudden gaps). Label
  these stress fixtures, not historical returns or fund-performance forecasts.
- Publish all losers, failures and strategy trials. Chained paired bootstrap
  intervals remain descriptive and do not remove selection bias from testing
  many candidates on reused history. Prospective shadow validation remains a
  separate future phase; Gemini is not included in measured performance.

Priority: L11 for the platform-leadership hypothesis, L12 for faster adaptation,
then L13–L16. Existing L08/SPXL+SOXL and L02/TQQQ+SOXL remain leveraged research
references, not approved deployments.
