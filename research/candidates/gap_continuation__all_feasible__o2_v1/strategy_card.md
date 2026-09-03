# Strategy card — gap_continuation__all_feasible__o2_v1

Frozen at B2_SPEC_FREEZE before any outcome P&L was viewed. Hashes bind the
canonical LF-normalized file bytes as committed on branch
`research/group-b-orb-gap`.

| Frozen field | Value |
|---|---|
| signal_family_id | `STANDARDIZED_GAP_CONTINUATION` |
| ordered_eligible_symbol_set | `[SPY, QQQ, TQQQ, SMH, SOXL, IGV]` |
| pair_cell_symbols | ordered `[SMH, SOXL]` |
| feature_schema_hash | `sha256:5d3521f1d77ec642ba80c5a8b03eb6d7a0b521b2ea246f1e44828fe5116c8160` (feature_contract.yaml) |
| central_config_hash | `sha256:6dacfe17abb96b3ecb4c93f93b292dc137da125fbef9770e17cc111c7cc56c4b` (central_config.json) |
| O2_expression_and_template_catalog_hash | `sha256:74906ee706cef3a52b77cb84e2f7b80c66bbc6b0e63ad3982be9e0ef0e02076e` (loaded template catalog, RESEARCH_INTERFACE_FREEZE section 2) |
| allocator_hash | `sha256:864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5` (packages/strategy_sdk/arbitration.py) |
| position_policy_hash | `sha256:c77bfe135c4cb13eb530e9d390f2c6ceab1e8aaced762489b849ee0d82bd69b7` (packages/position_manager/manager.py) |
| base_cost_policy_hash | `sha256:d4711c84edd637b933fc9574e7b5332dce4d91539944dfd4412f084aa61c11c8` (costs.yaml) |
| hypothesis_hash | `sha256:f1d6d94bed3b43a58dfe89e0ceaa17907430ef21aa599efbd7574aa461eb57c8` (hypothesis.yaml) |
| sensitivities_hash | `sha256:668531f1d5a024ffbad2296d98731a0814f631d3aa0480ebf1c47bc4e3dc6c5b` (sensitivities.yaml) |
| reason_codes_hash | `sha256:5fd03b6b1df6120603b089be77579d3a782315ca77e84ae90d019b540cbdef12` (reason_codes.yaml) |
| state_schema_hash | `sha256:d2b28d3618ddce937dd68fd00b315e208a6e2480e4e9de5ae5ee342e43dcdb03` (state_schema.json) |

## Central decision rule

Exactly one entry decision per symbol/session at 10:30:01 ET on the
completed 10:30 15-minute IEX interval. Bullish when `gap_z_60 >= 1.00`,
`continuation_ratio >= 0.25`, and the completed close is above session VWAP;
bearish when `gap_z_60 <= -1.00`, `continuation_ratio >= 0.25`, and the
completed close is below session VWAP. Score is
`min(abs(gap_z_60)/1.00, continuation_ratio/0.25)`. The corporate-action
calendar is a validity control: a split or distribution with uncertain
raw-price continuity invalidates the session (`CORPORATE_ACTION_AMBIGUOUS`).
Exit policy `TREND_VWAP_OR_60M_V1` (central-owned); the plug-in is entry-only.

## Package identity

`plugin_id` `gap_continuation` / `1.0.0`; entry point
`gap_continuation_v1.plugin:Plugin`; position policy
`TREND_VWAP_OR_60M_V1`; allowed entry tuples bullish call-debit and bearish
put-debit, horizon `INTRADAY_15_60M`, risk tier `TINY`, max TTL `300`.
