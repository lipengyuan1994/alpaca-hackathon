# Test fixtures

## Semantics

The JSON files here are **frozen delivered-feature snapshots**, not raw bars.
The plug-in is a consumer of the frozen feature contract
(`research/candidates/opening_range_breakout__all_feasible__o2_v1/feature_contract.yaml`);
it never recomputes derived features, so the fixtures freeze the eight numeric
delivered features per symbol exactly as the producer would deliver them.

Snapshot invariants:

- `up_break_fraction_or30_v1 + down_break_fraction_or30_v1 = -1` exactly
  whenever the width equals `log(or_high/or_low)` (algebraic identity of the
  two log expressions), so bullish/bearish blocks are internally consistent.
- Log relationships (`width`, `close` versus the opening range) are stored to
  8 decimal places; recomputation reproduces the stored values to within
  `5e-5` absolute.
- A zero observed range is represented by the frozen `1e-6` log-width floor
  and must refuse entry (plan section 6: boundary fixture, not a parameter).

## Files

- `feature_vector_smh_bullish.json` — full 48-key universe, keys in the
  contract `key_order` (symbols `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]`, then
  lexicographic within symbol); only SMH carries a qualifying bullish block
  (score `1.12` → `LOW` bucket).
- `feature_vector_soxl_bearish.json` — same shape; only SOXL carries a
  qualifying bearish block (score `1.25` → `MEDIUM` bucket, the exact bucket
  boundary).

Per-case signal goldens (entries, refusals, boundaries, degenerate ranges,
nonfinite and missing features) live in `tests/golden/signal_cases.json`.

The candidate's `worked_examples` remain `TO_BE_HASHED_WITH_GOLDEN_FIXTURES_BEFORE_OUTCOME_RUN`
per the frozen feature contract; nothing here authorizes an outcome claim.
