# Normalized VWAP reversion V1

This deterministic Group A package is entry-only. It requests only a semantic
defined-risk debit-spread template and cannot retrieve data, choose contracts,
strikes, expiration, size, price, or submit an order.

The canonical package API entry point is
`strategy_plugins.vwap_reversion_v1.plugin:Plugin`. The central registry binds
the source hash and retains all deployment authority.

```text
uv run python -m strategy_plugins.vwap_reversion_v1.reproduce \
  --data-manifest /absolute/path/to/data_manifest.json \
  --feasibility-manifest /absolute/path/to/option_proxy_feasibility_manifest.json \
  --output /absolute/path/to/empty-output-directory
```

The command works only with reviewed immutable inputs and makes no network or
credential call.
