# Opening-range breakout V1 (Group B)

Deterministic, entry-only Group B research package for the frozen candidate
`opening_range_breakout__all_feasible__o2_v1`. It emits only a semantic
request for a defined-risk debit spread: bullish `CALL_DEBIT_SPREAD_V1` or
bearish `PUT_DEBIT_SPREAD_V1`. It cannot fetch data, select an OCC contract,
strike, expiry, size, price, or submit an order. Per the frozen architecture,
an LLM thesis/counter-thesis may explain or veto downstream but can never
rewrite this signal, choose contracts, or alter risk.

## Identity

| Field | Value |
|---|---|
| plugin_id / version | `opening_range_breakout` / `1.0.0` |
| entry point | `opening_range_breakout_v1.plugin:Plugin` |
| hypothesis | `OPENING_RANGE_BREAKOUT` |
| feature contract | `sha256:0ef3d29fd8680508b0c02cacda2aef31f495f0960373848dc46837ff4a259654` |
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
`opening_range_breakout_v1.plugin:Plugin`. Central registration therefore
requires an explicit registry-schema decision by the release owner at the
B6_PLATFORM_PARITY gate; until then this package stays `research_only` and
cannot self-register or self-assert its content hash. The plug-in emits
`packages.strategy_sdk.UNBOUND_PLUGIN_CONTENT_HASH`; the host owns source-hash
binding.

## Reproduction command (section 9.3)

Run from the repository root:

```text
uv run python -m opening_range_breakout_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

With `src/` not installed, prefix `PYTHONPATH=strategy_plugins/opening_range_breakout_v1/src`
(or use `scripts/reproduce.sh` / `scripts/reproduce.ps1`, which do this). The
module refuses a nonempty output directory, validates the frozen
candidate/config hashes, runs the package tests, and writes deterministically
ordered evidence. It is a preflight, not a backtester, and has no network or
credential path.

## Baseline verification

```text
uv sync --frozen
uv run python -m pytest strategy_plugins/opening_range_breakout_v1/tests
uv run ruff check strategy_plugins/opening_range_breakout_v1
```

## Decision summary

Entries at 10:30:01 ET then every 30 minutes through 14:30:01 ET on completed
15-minute IEX intervals: bullish when `up_break_fraction >= 0.10` and
`volume_ratio >= 1.25` and close is strictly above session VWAP; bearish
mirror below. Score is `min(active_break/0.10, volume_ratio/1.25)`; buckets
`[1.00,1.25)=LOW`, `[1.25,1.75)=MEDIUM`, `>=1.75=HIGH`. First entry only per
symbol/session; degenerate opening ranges (nonpositive, inverted, or
width-pinned-at-floor) refuse. Quality flags must equal the frozen clean set
`FULL_XNYS_SESSION, IEX_COMPLETE, NO_DUPLICATE_BARS, OHLC_VALID,
CORPORATE_ACTION_CONTINUITY_CLEAR`; `EARLY_CLOSE_SESSION` refuses first.
