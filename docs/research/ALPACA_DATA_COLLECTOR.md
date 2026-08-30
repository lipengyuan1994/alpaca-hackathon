# Read-only Alpaca research data collector

Status: **data-steward utility; collection output is not research approval or paper-trading authority**

The `research-data-collect` command collects the team-wide Alpaca evidence needed for offline strategy research. It is run once by the approved data steward, then packet owners use only the frozen artifacts. It never belongs in a plug-in package or a strategy-reproduction command.

## Scope and safety boundary

The collector permits only HTTP `GET` requests for:

- one-minute stock bars for `SPY`, `QQQ`, `TQQQ`, `SMH`, `SOXL`, and `IGV`, with explicit `feed=iex` and both raw and split adjustments;
- XNYS calendar metadata;
- active and inactive option-contract metadata with deliverables;
- historical one-minute option bars and trades named in a frozen observation-request file; and
- latest option quotes named in a separate frozen contract-symbol file, with explicit `feed=indicative`.

It does not instantiate the paper execution adapter or call any account, position, order, clock, submit, cancel, or reconciliation endpoint. It does not accept API keys in arguments, specification files, request files, logs, raw artifacts, or manifests.

The collector is intentionally distinct from `live-trading-2026`: it reuses its read-only pagination, canonical-hash, and atomic-write patterns without importing its broker-facing code.

## Data-steward invocation

Run from the repository root with Python 3.12 and the pinned lock. The collector
uses the fixed Compose file-secret bundle at
`$REGIMESWITCH_SECRETS_DIR/alpaca/alpaca_api_key.yaml`, defaulting to
`/Users/lipengyuan/.config/great_secrets/alpaca/alpaca_api_key.yaml`. It reads
only the fixed `paper_alpaca_api_key` and `paper_alpaca_api_secret` YAML keys
to construct its GET-only market-data client. `REGIMESWITCH_SECRETS_DIR` is a
non-secret location setting; do not put keys, secret values, or a `.env` file
in the repository, shell history, command line, or collector output.

```text
uv sync --frozen
research-data-collect \
  --spec configs/research_data_collection_v1.yaml \
  --output /absolute/path/to/new-empty-collection-directory
```

The output directory must be absent or empty. A nonempty directory is rejected so a previous evidence set cannot be overwritten or mixed with a new provider response.

Store all retrieved provider artifacts beneath `data/alpaca/collections/<collection-id>/`:

```text
data/alpaca/collections/<collection-id>/
  underlying/                 # raw/split bars, calendar, contracts, base manifest
  option_observations/<name>/ # one hash-bound staged option collection per request manifest
  interrupted/                # preserved, explicitly ineligible failed attempts
```

This directory is intentionally ignored by Git. Source-controlled research
packages retain only artifact hashes, manifests, commands, and documentation.

`configs/research_data_collection_v1.yaml` freezes the shared six-symbol universe, historical windows, IEX feed, raw/split collection, one-minute timeframe, and page limit. Any change to it changes the manifest-bound specification hash and requires a new collection cycle.

For long provider requests, run resumable base stages. Each stage is written
once and hash-recorded in `collection_staging.json`; finalization is rejected
until every required stage has succeeded:

```text
research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output /absolute/path/to/new-collection --stage stock_raw
research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output /absolute/path/to/new-collection --stage stock_split
research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output /absolute/path/to/new-collection --stage calendar
research-data-collect-stage --spec configs/research_data_collection_v1.yaml --output /absolute/path/to/new-collection --stage contracts
research-data-finalize --spec configs/research_data_collection_v1.yaml --output /absolute/path/to/new-collection
```

## Option request inputs

Historical option data is intentionally demand-driven: it accepts only an ordered request manifest generated from frozen, pre-outcome candidate/selector inputs. This prevents a collector operator from informally changing contracts after results are visible.

```json
{
  "requests": [
    {
      "request_id": "000001",
      "symbols": ["SPY240201C00490000"],
      "start": "2024-02-01T14:30:00Z",
      "end": "2024-02-01T21:00:00Z"
    }
  ]
}
```

Pass it with `--option-observation-requests PATH`. Request IDs must be lexicographically ordered; each request contains 1–100 distinct OCC symbols. The collector retrieves both `/v1beta1/options/bars` and `/v1beta1/options/trades` without a feed query parameter and records `N/A_ENDPOINT_HAS_NO_FEED_PARAM` in the normalized lineage.

For a long historical study, collect the expensive option observations in a new,
separate immutable directory after the underlying data manifest and frozen
request file exist. This avoids re-downloading or mutating the underlying
dataset:

```text
research-data-collect-options \
  --spec configs/research_data_collection_v1.yaml \
  --base-data-manifest /absolute/path/to/underlying/data_manifest.json \
  --option-observation-requests /absolute/path/to/frozen_option_requests.json \
  --output /absolute/path/to/new-empty-option-observation-directory
```

Its `option_observation_manifest.json` hash-binds the exact base data manifest,
collection specification, request file, raw pages, normalized option artifacts,
and entitlement probe. The command remains GET-only and cannot modify the base
dataset.

Current quote readiness is separate from historical proxy research. A sorted, unique symbol file has this form:

```json
{"symbols": ["SPY260831C00420000"]}
```

Pass it with `--quote-symbols PATH`. The collector requests only `/v1beta1/options/quotes/latest` with `feed=indicative`; results are operational-readiness evidence, never historical OPRA/NBBO fills or alpha features.

## Immutable output

Each successful collection creates:

```text
<output>/
  raw/<dataset-id>/<page>.json
  normalized/stock_bars_raw.parquet
  normalized/stock_bars_split.parquet
  normalized/calendar.json
  normalized/option_contracts.parquet
  normalized/option_bars_<request-id>.parquet
  normalized/option_trades_<request-id>.parquet
  normalized/option_quotes_indicative.parquet
  entitlement_probe.json
  data_manifest.json
```

Only requested option-observation and quote files are present. Every raw page and normalized artifact has a SHA-256 reference in `data_manifest.json`. Normalized market rows retain event time, availability time, ingestion time, endpoint, feed/sentinel, page provenance, and raw-response hash. JSON is canonical and Parquet is written atomically with the fixed writer settings.

The collector emits `status=COLLECTED` only after every required base dataset
has an immutable hash-bound record. The separate blinded
`option_proxy_feasibility_manifest.json` is deterministic selection evidence;
its `READY_FOR_REPLAY` status is sufficient for offline research. Independent
review may be added later as extra provenance, but it is not a replay gate.
Completed legacy manifests marked `COLLECTED_UNATTESTED` remain replayable when
their canonical hash and every referenced artifact hash validate; that marker
was a former review requirement, not a data-completeness state.

## Blinded feasibility draft

After collection, the data steward can generate an unsigned, deterministic
feasibility draft without reading signal returns or option P&L:

```text
research-data-feasibility \
  --data-manifest /absolute/path/to/collection/data_manifest.json \
  --output /absolute/path/to/option_proxy_feasibility_draft.json
```

It verifies the collector manifest and the split-adjusted IEX artifact hash,
then ranks only data-quality-eligible symbols using the frozen symbol order.
The resulting manifest has `status=READY_FOR_REPLAY` and may be consumed by a
strategy reproduction command immediately. No signal return, option P&L, or
strategy-specific preference may enter that feasibility process. A later
option-observation manifest must bind to the same base data-manifest hash or
it is rejected.

The current provider-scope exception is
[`alpaca_free_iex_history_floor_v1.json`](../../research/shared/coverage_exceptions/alpaca_free_iex_history_floor_v1.json): it changes the common underlying
warm-up floor to 2020-07-27 because that is the observed free-tier IEX history
floor. It does not authorize a new feed, vendor, threshold, or outcome-driven
tuning.

## Failure behavior

The collector fails closed on missing read-only credentials, HTTP/auth/rate-limit errors, malformed JSON, repeated pagination tokens, invalid event times, duplicate bars, OHLC/volume violations, crossed option quotes, malformed option request files, or nonempty output paths. It writes a narrow `collection_failure.json` when a provider request fails; it does not retry through another vendor, feed, symbol, or credential source.

An empty historical option-bar/trade table is valid evidence of no observation coverage for that request. It is not a zero-price fill, and it must remain visible to feasibility and backtest reporting.
