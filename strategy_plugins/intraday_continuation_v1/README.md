# Normalized intraday continuation V1

This is a deterministic, entry-only Group A research package. It emits only a
semantic request for a defined-risk debit spread: bullish `CALL_DEBIT_SPREAD_V1`
or bearish `PUT_DEBIT_SPREAD_V1`. It cannot fetch data, select an OCC contract,
strike, expiry, size, price, or submit an order.

The canonical package API entry point is
`strategy_plugins.intraday_continuation_v1.plugin:Plugin`. The central registry
must separately pin its source hash and may keep it only `research_only`.

The portable evidence command is:

```text
uv run python -m strategy_plugins.intraday_continuation_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

It has no network or credential path. It accepts a hash-valid `COLLECTED`
manifest plus a bound `READY_FOR_REPLAY` feasibility manifest; a separate
review signature is optional provenance, not a reproduction gate. It does not
call the Alpaca collector.
