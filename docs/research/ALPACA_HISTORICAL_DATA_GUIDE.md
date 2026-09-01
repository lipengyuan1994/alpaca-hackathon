# Historical Alpaca data for individual research

Use this guide when you need to collect your own Alpaca market-data evidence and run a research-only backtest. The repository helper is GET-only: it cannot call an account, position, order, clock, submit, cancel, or reconciliation endpoint. It does not grant paper-trading authority.

This replaces the former central-steward/signature prerequisite. A researcher may collect a separate immutable dataset, provided they use the frozen repository helper and record the resulting hashes. Independent review remains required for an integration or promotion claim, not to start or complete a research-only backtest.

## 1. Prepare a read-only collection runtime

Use a development Alpaca paper account or other approved non-competition credential. Alpaca market-data keys are not assumed to be technically read-scoped, so never use a competition or live-money credential for research collection.

Install the pinned project dependencies from the repository root. On this Mac, use the native ARM64 runtime:

```zsh
/opt/homebrew/bin/uv sync --frozen
/opt/homebrew/bin/uv run research-data-collect --help
```

The helper reads its key pair only from a YAML file outside the repository. Create the file at a private location of your choice, with these two keys (use your own values; do not commit, paste, or log them):

```yaml
paper_alpaca_api_key: your-development-key
paper_alpaca_api_secret: your-development-secret
```

Point `REGIMESWITCH_SECRETS_DIR` to the directory that contains `alpaca/alpaca_api_key.yaml`. For example, if the file is `/secure/research-secrets/alpaca/alpaca_api_key.yaml`:

```zsh
export REGIMESWITCH_SECRETS_DIR=/secure/research-secrets
chmod 700 "$REGIMESWITCH_SECRETS_DIR" "$REGIMESWITCH_SECRETS_DIR/alpaca"
chmod 600 "$REGIMESWITCH_SECRETS_DIR/alpaca/alpaca_api_key.yaml"
```

The variable contains a path, never a secret value. The helper deliberately rejects key values supplied through command-line flags or environment variables.

## 2. Collect immutable underlying history

Use the source-controlled specification for the common six-symbol study. Choose an absent or empty output directory outside the repository; raw provider pages and Parquet data are intentionally not committed.

```zsh
export RESEARCH_COLLECTION=/absolute/path/to/new-empty-collection
/opt/homebrew/bin/uv run research-data-collect \
  --spec configs/research_data_collection_v1.yaml \
  --output "$RESEARCH_COLLECTION"
```

`packages.research_data.collector.ResearchDataCollector` uses `packages.research_data.client.ReadOnlyAlpacaClient` to paginate GET-only requests for one-minute stock bars with explicit `feed=iex` and both `raw`/`split` adjustments, XNYS calendar metadata, and option-contract metadata. It preserves every raw page.

The command writes `data_manifest.json`, raw JSON pages, normalized Parquet, and `entitlement_probe.json`. A successful base manifest has `status: COLLECTED`; validate its canonical hash and every referenced artifact before using it. `ALPACA_READ_ONLY_CREDENTIALS_UNAVAILABLE`, an entitlement error, an incomplete page sequence, or a nonempty output directory is a safe stop—do not substitute a vendor, feed, or manually repaired rows.

For an interrupted long collection, run the resumable stages in the same empty-or-recognized directory, then finalize:

```zsh
/opt/homebrew/bin/uv run research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output "$RESEARCH_COLLECTION" --stage stock_raw
/opt/homebrew/bin/uv run research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output "$RESEARCH_COLLECTION" --stage stock_split
/opt/homebrew/bin/uv run research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output "$RESEARCH_COLLECTION" --stage calendar
/opt/homebrew/bin/uv run research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output "$RESEARCH_COLLECTION" --stage contracts
/opt/homebrew/bin/uv run research-data-finalize --spec configs/research_data_collection_v1.yaml --output "$RESEARCH_COLLECTION"
```

## 3. Create deterministic feasibility and option inputs

Generate the blinded, hash-bound feasibility file from the completed base manifest. It has `status: READY_FOR_REPLAY` immediately; a signature is optional provenance and is not a backtest gate.

```zsh
/opt/homebrew/bin/uv run research-data-feasibility \
  --data-manifest "$RESEARCH_COLLECTION/data_manifest.json" \
  --output "$RESEARCH_COLLECTION/option_proxy_feasibility_manifest.json"
```

For the existing Group A debit-spread study, generate the frozen request file before fetching option prices, then collect only those historical option bars and trades:

```zsh
export RESEARCH_REQUESTS=/absolute/path/to/group-a-option-requests.json
export RESEARCH_OPTIONS=/absolute/path/to/new-empty-option-observations
/opt/homebrew/bin/uv run research-data-group-a-option-requests \
  --data-manifest "$RESEARCH_COLLECTION/data_manifest.json" \
  --output "$RESEARCH_REQUESTS"
/opt/homebrew/bin/uv run research-data-collect-options \
  --spec configs/research_data_collection_v1.yaml \
  --base-data-manifest "$RESEARCH_COLLECTION/data_manifest.json" \
  --option-observation-requests "$RESEARCH_REQUESTS" \
  --output "$RESEARCH_OPTIONS"
```

Historical option bars and trades are non-executable proxies. The helper records `N/A_ENDPOINT_HAS_NO_FEED_PARAM` for endpoints without a feed parameter; it does not invent `feed=indicative`. Do not describe historical proxy P&L as fills, NBBO, or paper-trading performance.

## 4. Run the research backtest

Run the deterministic Group A option-proxy helper from the frozen manifests. It makes no network call and refuses a mixed, changed, or hash-mismatched input.

```zsh
export RESEARCH_BACKTEST=/absolute/path/to/new-empty-backtest-output
/opt/homebrew/bin/uv run research-data-group-a-proxy-backtest \
  --base-data-manifest "$RESEARCH_COLLECTION/data_manifest.json" \
  --option-manifest "$RESEARCH_OPTIONS/option_observation_manifest.json" \
  --request-manifest "$RESEARCH_REQUESTS" \
  --output "$RESEARCH_BACKTEST"
```

The output contains `metrics.json`, normalized trades and daily returns, a cumulative-P&L SVG, and a hash-bound report. Its status is research-only and non-executable. Preserve the input manifest hashes, command, source revision, lock hash, and output report hash in the candidate run manifest. A researcher can iterate on a new predeclared candidate or sensitivity, but must retain all attempted trials and never overwrite a previous output directory.

For a plug-in input-validation/reproduction run, use the corresponding package command with the same data and feasibility manifests. It validates lineage but is not itself a backtest:

```zsh
/opt/homebrew/bin/uv run python -m strategy_plugins.intraday_continuation_v1.reproduce \
  --data-manifest "$RESEARCH_COLLECTION/data_manifest.json" \
  --feasibility-manifest "$RESEARCH_COLLECTION/option_proxy_feasibility_manifest.json" \
  --output /absolute/path/to/new-empty-reproduction-output
```

## 5. Share evidence, not secrets or authority

Share `data_manifest.json`, `entitlement_probe.json`, feasibility/request/option manifests, artifact hashes, and the final research report. Transfer large raw/Parquet data through an approved private channel; do not commit it. Do not share the YAML key file, a credential value, an account ID, or any execution configuration.

Independent reproduction validates a research claim; central risk, execution, and release gates are still required before any paper-enabled state. No action in this guide places an order.
