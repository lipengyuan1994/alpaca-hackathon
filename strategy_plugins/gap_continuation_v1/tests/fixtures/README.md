# Test fixtures

## Semantics

The JSON files here are **frozen delivered-feature snapshots**, not raw bars.
The plug-in is a consumer of the frozen feature contract
(`research/candidates/gap_continuation__all_feasible__o2_v1/feature_contract.yaml`);
it never recomputes derived features, so the fixtures freeze the fourteen
numeric delivered features per symbol exactly as the producer would deliver
them.

Snapshot invariants (natural-log identities, hand-computed Decimals):

- `gap_log_adjusted_v1 = ln(open_0930_adjusted_v1 / prior_regular_close_adjusted_v1)`
  exactly, so a bullish block has a positive log gap and a bearish block a
  negative one (e.g. `ln(190/180) = 0.05406722`, `ln(38/40) = -0.05129329`).
- `first_hour_return_v1 = ln(close_completed_15m_v1 / open_0930_adjusted_v1)` and
  `continuation_ratio_v1 = first_hour_return_v1 / gap_log_adjusted_v1`, so the
  continuation ratio is a pure intraday-vs-overnight share with the sign of the
  gap (0.28 bullish, 0.3125 bearish).
- `gap_z_60_v1 = gap_log_adjusted_v1 / sigma_gap_60_v1` (1.12, −1.25, 1.75 on
  the reference presets); log relationships are stored to 8 decimal places and
  recomputation reproduces the stored values to within `5e-5` absolute.
- A degenerate gap is represented by sigma or |log gap| pinned at the frozen
  `1e-6` floor and must refuse entry (plan section 6: boundary fixture, not a
  parameter).

## Files

- `feature_vector_smh_bullish.json` — full 84-key universe, keys in the
  contract `key_order` (symbols `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]`, then
  lexicographic within symbol); only SMH carries a qualifying bullish block
  (score `1.12` → `LOW` bucket).
- `feature_vector_soxl_bearish.json` — same shape; only SOXL carries a
  qualifying bearish block (score `1.25` → `MEDIUM` bucket, the exact bucket
  boundary).

Per-case signal goldens (entries, refusals, boundaries, degenerate gaps,
nonfinite and missing features) live in `tests/golden/signal_cases.json`.

The candidate's `worked_examples` remain `TO_BE_HASHED_WITH_GOLDEN_FIXTURES_BEFORE_OUTCOME_RUN`
per the frozen feature contract; nothing here authorizes an outcome claim.
