# Standardized gap continuation V1 (Group B)

Deterministic, entry-only Group B research package for the frozen candidate
`gap_continuation__all_feasible__o2_v1`. It emits only a semantic request for
a defined-risk debit spread: bullish `CALL_DEBIT_SPREAD_V1` or bearish
`PUT_DEBIT_SPREAD_V1`. It cannot fetch data, select an OCC contract, strike,
expiry, size, price, or submit an order. Per the frozen architecture, an LLM
thesis/counter-thesis may explain or veto downstream but can never rewrite
this signal, choose contracts, or alter risk.

## Identity

| Field | Value |
|---|---|
| plugin_id / version | `gap_continuation` / `1.0.0` |
| entry point | `gap_continuation_v1.plugin:Plugin` |
| hypothesis | `STANDARDIZED_GAP_CONTINUATION` |
| feature contract | `sha256:5d3521f1d77ec642ba80c5a8b03eb6d7a0b521b2ea246f1e44828fe5116c8160` |
| pair cell | ordered `[SMH, SOXL]` (later compatibility `SPY, QQQ, TQQQ, SMH, SOXL, IGV`) |
| position policy | `TREND_VWAP_OR_60M_V1` (central-owned; this plug-in is entry-only) |
| allowed tuples | `INTRADAY_15_60M` / `TINY` / max TTL `300` |
| lifecycle | `research_only` |

## Layout and registry note

This package follows the src-layout tree prescribed by Group B plan section 9
("do not use the flat fixture layout currently present elsewhere in the
repository"). Truthful consequence: the central registry's current entrypoint
pattern (`^strategy_plugins\.…`) and its `strategy_plugins`-rooted source
hashing cannot yet express the plan-prescribed top-level entry point
`gap_continuation_v1.plugin:Plugin`. Central registration therefore requires
an explicit registry-schema decision by the release owner at the
B6_PLATFORM_PARITY gate; until then this package stays `research_only` and
cannot self-register or self-assert its content hash. The plug-in emits
`packages.strategy_sdk.UNBOUND_PLUGIN_CONTENT_HASH`; the host owns source-hash
binding.

## Reproduction command (section 9.3)

Run from the repository root:

```text
uv run python -m gap_continuation_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

With `src/` not installed, prefix `PYTHONPATH=strategy_plugins/gap_continuation_v1/src`
(or use `scripts/reproduce.sh` / `scripts/reproduce.ps1`, which do this). The
module refuses a nonempty output directory, validates the frozen
candidate/config hashes, runs the package tests, and writes deterministically
ordered evidence. It is a preflight, not a backtester, and has no network or
credential path.

## Baseline verification

```text
uv sync --frozen
uv run python -m pytest strategy_plugins/gap_continuation_v1/tests
uv run ruff check strategy_plugins/gap_continuation_v1
```

## Decision summary

Entry at the single weekday instant 10:30:01 ET on the completed first-hour
15-minute IEX interval: bullish when `gap_z_60 >= 1.00` and
`continuation_ratio >= 0.25` and close is strictly above session VWAP;
bearish mirror (`gap_z_60 <= -1.00`, close strictly below VWAP). The gap is
the adjusted log gap `ln(open_0930/prior_regular_close)` standardized over 60
sessions. Score is `min(active_z/1.00, continuation_ratio/0.25)`; buckets
`[1.00,1.25)=LOW`, `[1.25,1.75)=MEDIUM`, `>=1.75=HIGH`. First entry only per
symbol/session; degenerate gaps (sigma or |log gap| pinned at the frozen
`1e-6` floor) refuse. Corporate-action continuity must be clear and the
adjustment basis affirmed for every symbol, else
`CORPORATE_ACTION_AMBIGUOUS`; early-close sessions refuse first. Quality
flags must equal the frozen clean set `FULL_XNYS_SESSION, IEX_COMPLETE,
NO_DUPLICATE_BARS, OHLC_VALID, PRIOR_SESSION_FULL,
CORPORATE_ACTION_CONTINUITY_CLEAR`. The universe is the 84-key contract
(six symbols × fourteen features); the signal consumes only the six
decision features.
