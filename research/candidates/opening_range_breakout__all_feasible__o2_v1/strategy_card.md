# Strategy card — opening_range_breakout__all_feasible__o2_v1

Frozen at B2_SPEC_FREEZE before any outcome P&L was viewed. Hashes bind the
canonical LF-normalized file bytes as committed on branch
`research/group-b-orb-gap`.

| Frozen field | Value |
|---|---|
| signal_family_id | `OPENING_RANGE_BREAKOUT` |
| ordered_eligible_symbol_set | `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]` |
| pair_cell_symbols | ordered `[SMH, SOXL]` |
| feature_schema_hash | `sha256:0ef3d29fd8680508b0c02cacda2aef31f495f0960373848dc46837ff4a259654` (feature_contract.yaml) |
| central_config_hash | `sha256:607b30f351d8ca5203d75293a45018bb5a3cc381409c253530d8bdea38f6e081` (central_config.json) |
| O2_expression_and_template_catalog_hash | `sha256:74906ee706cef3a52b77cb84e2f7b80c66bbc6b0e63ad3982be9e0ef0e02076e` (loaded template catalog, RESEARCH_INTERFACE_FREEZE section 2) |
| allocator_hash | `sha256:864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5` (packages/strategy_sdk/arbitration.py) |
| position_policy_hash | `sha256:c77bfe135c4cb13eb530e9d390f2c6ceab1e8aaced762489b849ee0d82bd69b7` (packages/position_manager/manager.py) |
| base_cost_policy_hash | `sha256:d4711c84edd637b933fc9574e7b5332dce4d91539944dfd4412f084aa61c11c8` (costs.yaml) |
| hypothesis_hash | `sha256:79302bbecd9f775ddfa691a982451819261339f7175f91ef5c56a22e6b42525a` (hypothesis.yaml) |
| sensitivities_hash | `sha256:4439fa95496edb1e8992362838c1767a5a975802ca7f37e0fbd4c6ef6f21829b` (sensitivities.yaml) |
| reason_codes_hash | `sha256:4e2ea70681c1918ec7543865726f6deabd2575b5c76a5fd26da8844bf8b9c856` (reason_codes.yaml) |
| state_schema_hash | `sha256:1bb2bf5fb452e3a30ef71c436a0ddc6ff9ae46afb9ed32714b1c248d7c85cf8d` (state_schema.json) |

## Central decision rule

Entry evaluations at 10:30:01 ET then every 30 minutes through 14:30:01 ET,
on completed 15-minute IEX intervals. Bullish when
`up_break_fraction >= 0.10` and `volume_ratio >= 1.25` and completed close is
above session VWAP; bearish mirror below. Score is
`min(active_break_fraction/0.10, volume_ratio/1.25)`. First entry only per
symbol/session. Exit policy `TREND_VWAP_OR_60M_V1` (central-owned); the
plug-in is entry-only.

## Package identity

`plugin_id` `opening_range_breakout` / `1.0.0`; entry point
`opening_range_breakout_v1.plugin:Plugin`; position policy
`TREND_VWAP_OR_60M_V1`; allowed entry tuples bullish call-debit and bearish
put-debit, horizon `INTRADAY_15_60M`, risk tier `TINY`, max TTL `300`.
