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

Run from the repository root with Python 3.12 and the pinned lock. The approved data-steward runtime injects `ALPACA_MARKET_DATA_KEY_ID` and `ALPACA_MARKET_DATA_SECRET_KEY`; do not put them in a shell history, file, or command line.

```text
uv sync --frozen
research-data-collect \
  --spec configs/research_data_collection_v1.yaml \
  --output /absolute/path/to/new-empty-collection-directory
```

The output directory must be absent or empty. A nonempty directory is rejected so a previous evidence set cannot be overwritten or mixed with a new provider response.

`configs/research_data_collection_v1.yaml` freezes the shared six-symbol universe, historical windows, IEX feed, raw/split collection, one-minute timeframe, and page limit. Any change to it changes the manifest-bound specification hash and requires a new collection/review cycle.

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

Current quote readiness is separate from historical proxy research. A sorted, unique symbol file has this form:

```json
{"symbols": ["SPY260831C00420000"]}
```

Pass it with `--quote-symbols PATH`. The collector requests only `/v1beta1/options/quotes/latest` with `feed=indicative`; results are operational-readiness evidence, never historical OPRA/NBBO fills or alpha features.

## Immutable output and review

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

The collector emits `status=COLLECTED_UNATTESTED`. That is deliberately insufficient for outcome-bearing research. The data steward and independent reviewer must validate coverage, pagination, session/OHLC quality, corporate-action treatment, entitlement behavior, and manifest hashes; then sign the immutable data/feasibility artifacts. The separate blinded `option_proxy_feasibility_manifest.json` is a review/selection output, not something this collector self-authorizes.

## Failure behavior

The collector fails closed on missing read-only credentials, HTTP/auth/rate-limit errors, malformed JSON, repeated pagination tokens, invalid event times, duplicate bars, OHLC/volume violations, crossed option quotes, malformed option request files, or nonempty output paths. It writes a narrow `collection_failure.json` when a provider request fails; it does not retry through another vendor, feed, symbol, or credential source.

An empty historical option-bar/trade table is valid evidence of no observation coverage for that request. It is not a zero-price fill, and it must remain visible to feasibility and backtest reporting.
