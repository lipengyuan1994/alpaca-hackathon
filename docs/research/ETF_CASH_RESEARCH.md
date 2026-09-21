# QQQM + semiconductor ETF cash research

This package implements the frozen ten-strategy study in `packages/etf_cash_research`.
It is deterministic research only. It does not import an account, order, or
execution client, and it does not alter the existing QQQ V13.5 paper wheel.

## Permanent T9 library

The research library is `/Volumes/T9/TradingResearch`. Its `catalog.json`
lists immutable dataset snapshots and archived studies. `datasets/alpaca/us-etf-daily/<snapshot-id>/`
contains the original manifest, raw responses, normalized Parquet files, and
an archive inventory; `studies/etf-cash-v1/<run-id>/` contains the phase ledgers,
selection evidence, and report. The library commands require the actual T9
volume UUID, so a disconnected or replaced drive fails closed. T9 is ExFAT;
the publisher stages on the same volume, verifies hashes, then renames the
snapshot and updates the catalog. If interrupted, inspect `.staging/` before
retrying. The original `/tmp` files are retained.

```zsh
/opt/homebrew/bin/uv run --frozen etf-cash-research list-data
/opt/homebrew/bin/uv run --frozen etf-cash-research verify-data
/opt/homebrew/bin/uv run --frozen etf-cash-research update-data \
  --library /Volumes/T9/TradingResearch \
  --dataset alpaca/us-etf-daily --through YYYY-MM-DD
```

`update-data` uses the most recent snapshot, recollects five overlapping
exchange sessions and new sessions, and compares the overlapping prices and
corporate actions. A provider revision or missing bar leaves the old catalog
unchanged and writes a report under `.staging/`. A successful update creates a
new snapshot; the older snapshot and its study binding remain unchanged.
Use the specific manifest path from `list-data` with `backtest --data-manifest`
or `reproduce --data-manifest`. Updates only fetch read-only market data; they
do not rerun or change a frozen strategy selection.

The native ARM64 workflow is:

```zsh
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" \
  /opt/homebrew/bin/uv sync --frozen

UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" \
  /opt/homebrew/bin/uv run etf-cash-research collect \
  --spec configs/etf_cash_collection_v1.yaml \
  --output /absolute/path/to/new-empty-collection

UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" \
  /opt/homebrew/bin/uv run etf-cash-research backtest \
  --data-manifest /absolute/path/to/new-empty-collection/data_manifest.json \
  --phase development \
  --output /absolute/path/to/new-empty-development-run
```

Repeat the backtest for `validation`. Freeze its shortlist with
`freeze-selection`, then run `holdout` only after the selection file exists.
`report` writes `report.html`, a sibling `report.md`, and inline SVGs for the
required growth, profit, drawdown, heatmap, return/drawdown, cost, and
SOXX-versus-SMH views. `reproduce` reads only a hash-checked manifest.

The collector probes SIP first and falls back to IEX only when SIP is
unavailable, recording that fact in the manifest. It collects raw and split
daily bars, the exchange calendar, and corporate actions. A missing corporate
action surface is a failed collection, not a reason to claim total-return
evidence.

The simulator uses previous-close information, next-session opening-price
proxies, adverse costs, fractional quantities, T+2/T+1 settled-cash rules,
dividend receivables, split quantities, and explicit pending sale proceeds.
Metrics are calculated from daily marked-to-market equity. The Gemini news
contract lives in `news_gate.py`; this release does not replay historical news
through an LLM or treat an LLM result as a backtest result.

Each primary candidate also receives a one-session execution-delay stress run
under `delay_1_session/` and in `delay_stress.csv`. Delaying an order keeps its
signal timestamp fixed and moves only the simulated fill session.
