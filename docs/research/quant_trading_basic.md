# Quant trading basics: data, signals, and reproducible research

This is the practical companion to [trading foundation](trading_foundation.md). It teaches the smallest useful workflow for an assigned research packet:

```text
immutable bars -> validate/normalize -> no-look-ahead features -> pure signal
-> conservative underlying proxy -> metrics + artifacts -> plug-in package
```

It is not a broker tutorial. Do not put account IDs, API keys, secret values, `.env` files, or credential-loading code in a package, notebook, issue, commit, or research artifact.

## 1. Platform-neutral researcher setup

Researchers may use Windows, macOS, or Linux on either x64 or ARM64. The native-ARM64 rule applies to the Mac-hosted **core platform/runtime**, not to offline research. A supported non-ARM machine is not a failed research gate.

Install Python 3.12 and `uv` using its [official installation guide](https://docs.astral.sh/uv/getting-started/installation/), then, from the repository root, run the same commands in PowerShell, Terminal, or a Linux shell:

```text
uv sync --frozen
uv run python -m pytest -q
uv run ruff check .
```

Do not copy the maintainer's `/opt/homebrew/bin/uv` path or their ARM-only cache path. `uv` must be on your own `PATH`. Record the following in your run manifest for reproducibility, but do not treat an architecture difference as a rejection:

```text
uv --version
uv run python --version
uv run python -c "import platform; print(platform.system(), platform.machine())"
git rev-parse HEAD
```

The central platform owner runs final host-interface and paper-safety conformance on the supported Mac runtime. A researcher is responsible for deterministic offline evidence from the pinned commit, lock file, configuration, and shared data hashes.

## 2. What data you may use

| Need | Researcher action | Non-negotiable rule |
|---|---|---|
| Historical ETF bars | Load immutable normalized files named in a hash-valid `data_manifest.json`, either shared or collected through this repository's helper. | Never use a private downloader, switch vendor/feed, or patch missing rows. |
| Historical option proxy | Use a hash-bound option-observation artifact and feasibility manifest. | A missing observation is `NO_PROXY_FILL`, never zero or a forward-filled price. |
| Current quote readiness | Read hash-bound indicative quote artifacts when supplied. | Do not call indicative quotes OPRA, NBBO, or executable history. |
| Direct Alpaca retrieval | Follow [the individual historical-data guide](ALPACA_HISTORICAL_DATA_GUIDE.md) and use `research-data-collect`. | Use only a separate development credential in the GET-only helper; it contains no order path. |

Alpaca's historical stock-bar endpoint is paginated; a result page may contain only one requested symbol, so a collector must continue until `next_page_token` is absent. The current primary API docs also state the requested `feed` explicitly and document access/rate-limit errors. See [stock bars](https://docs.alpaca.markets/us/reference/stockbars). Historical option bars are separately paginated and are a proxy surface in this project, not proof of a tradable historical quote. See [option bars](https://docs.alpaca.markets/us/reference/optionbars).

## 3. Read-only Alpaca bars client

The production helper is `packages.research_data.client.ReadOnlyAlpacaClient` plus `packages.research_data.collector.ResearchDataCollector`, invoked by `research-data-collect`; use that helper rather than copying the educational example below. It keeps the useful properties—UTC timestamps, explicit feed, pagination, normalized columns, and failure on malformed data—while deliberately omitting all trading/order methods. The [individual guide](ALPACA_HISTORICAL_DATA_GUIDE.md) gives the supported command and secret-file setup.

The `headers` mapping is supplied only by an approved data-steward runtime; this document neither creates nor displays credential values. Packet researchers normally skip this class and read the immutable files supplied to them.

```python
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadOnlyAlpacaBars:
    """Read historical stock bars; no trading, account, or credential code."""

    headers: Mapping[str, str]
    base_url: str = "https://data.alpaca.markets"
    feed: str = "iex"
    timeout_seconds: int = 30
    page_limit: int = 10_000

    def get_bars(
        self,
        symbols: list[str],
        *,
        start: str,
        end: str,
        timeframe: str = "1Min",
        adjustment: str = "raw",
    ) -> pd.DataFrame:
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if not normalized_symbols:
            raise ValueError("at least one symbol is required")

        params = {
            "symbols": ",".join(normalized_symbols),
            "start": start,
            "end": end,
            "timeframe": timeframe,
            "adjustment": adjustment,
            "feed": self.feed,
            "limit": str(self.page_limit),
            "sort": "asc",
        }
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            page = dict(params)
            if page_token:
                page["page_token"] = page_token
            payload = self._get_json("/v2/stocks/bars", page)
            bars_by_symbol = payload.get("bars")
            if not isinstance(bars_by_symbol, dict):
                raise MarketDataError("response has no bars object")
            for symbol, raw_bars in bars_by_symbol.items():
                if not isinstance(raw_bars, list):
                    raise MarketDataError("bar list is malformed")
                rows.extend(_normalize_bar(str(symbol).upper(), raw) for raw in raw_bars)
            page_token = payload.get("next_page_token")
            if not page_token:
                break

        frame = pd.DataFrame(
            rows,
            columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"],
        )
        return validate_bars(frame)

    def _get_json(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=dict(self.headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise MarketDataError(f"market-data request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise MarketDataError("response must be a JSON object")
        return payload


def _normalize_bar(symbol: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MarketDataError("bar must be an object")
    try:
        return {
            "symbol": symbol,
            "timestamp": pd.Timestamp(raw["t"], tz="UTC"),
            "open": float(raw["o"]),
            "high": float(raw["h"]),
            "low": float(raw["l"]),
            "close": float(raw["c"]),
            "volume": float(raw["v"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("bar is missing a required field") from exc


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
    if frame.empty or not required.issubset(frame.columns):
        raise MarketDataError("bars are empty or have the wrong schema")
    ordered = frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True).copy()
    if ordered.duplicated(["symbol", "timestamp"]).any():
        raise MarketDataError("duplicate symbol/timestamp bars")
    if ordered[["open", "high", "low", "close"]].isna().any().any():
        raise MarketDataError("missing OHLC value")
    if (ordered["low"] > ordered[["open", "close"]].min(axis=1)).any():
        raise MarketDataError("low violates OHLC invariant")
    if (ordered["high"] < ordered[["open", "close"]].max(axis=1)).any():
        raise MarketDataError("high violates OHLC invariant")
    if (ordered["volume"] < 0).any():
        raise MarketDataError("negative volume")
    return ordered
```

Important: the project data contract additionally requires `event_time`, `available_time`, `ingested_at`, feed/endpoint, page provenance, and raw-response hash. Add those columns in the central data collection layer; do not silently infer them in a strategy.

## 4. No-look-ahead features

Features may use a completed bar at time `t` only after the prescribed availability time. The function below is intentionally simple: it calculates same-session VWAP, four-bar momentum, and a prior four-bar high. `prior_high_4` uses `shift(1)`, so it does not peek at the current bar when defining a breakout level.

```python
from zoneinfo import ZoneInfo


def add_intraday_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Return one-symbol bars with features known when each bar has completed."""
    frame = validate_bars(bars).copy()
    if frame["symbol"].nunique() != 1:
        raise ValueError("call once per symbol")

    et = ZoneInfo("America/New_York")
    frame["session"] = frame["timestamp"].dt.tz_convert(et).dt.date
    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    weighted_price = typical_price * frame["volume"]
    cumulative_value = weighted_price.groupby(frame["session"]).cumsum()
    cumulative_volume = frame["volume"].groupby(frame["session"]).cumsum()
    frame["session_vwap"] = cumulative_value / cumulative_volume.replace(0.0, float("nan"))
    frame["return_4"] = frame["close"].pct_change(4)
    frame["prior_high_4"] = frame["high"].shift(1).rolling(4, min_periods=4).max()
    return frame


def continuation_signal(latest: pd.Series, *, threshold: float = 0.01) -> tuple[str, str]:
    """A teaching example, not a new candidate or approved production rule."""
    needed = ("return_4", "close", "session_vwap")
    if latest.loc[list(needed)].isna().any():
        return "NO_TRADE", "INSUFFICIENT_HISTORY"
    if latest["return_4"] >= threshold and latest["close"] > latest["session_vwap"]:
        return "BUY", "MOMENTUM_AND_VWAP_CONFIRMED"
    if latest["return_4"] <= -threshold and latest["close"] < latest["session_vwap"]:
        return "SELL", "MOMENTUM_AND_VWAP_CONFIRMED"
    return "NO_TRADE", "THRESHOLD_NOT_MET"
```

Do not replace the packet's frozen formulas with this example. It is only a pattern for writing a pure function: inputs in, deterministic output and reason code out, no network, no clock, no broker, and no mutable global state.

## 5. A conservative underlying-only proxy

The following is an **underlying diagnostic proxy**. It is not an option backtest and must never be labelled as option P&L. It applies yesterday's signal to the following period return and charges a simple cost when exposure changes.

```python
import math


def simulate_underlying_proxy(close: pd.Series, position: pd.Series, *, cost_bps: float) -> pd.DataFrame:
    """Use only lagged position; positive is long, negative is short, zero is flat."""
    close = close.astype(float).sort_index()
    position = position.reindex(close.index).fillna(0.0).astype(float)
    returns = close.pct_change().fillna(0.0)
    lagged_position = position.shift(1).fillna(0.0)
    turnover = position.diff().abs().fillna(position.abs())
    net_returns = lagged_position * returns - turnover * cost_bps / 10_000.0
    equity = (1.0 + net_returns).cumprod()
    return pd.DataFrame(
        {"return": returns, "position": position, "turnover": turnover,
         "net_return": net_returns, "equity": equity}
    )


def portfolio_metrics(equity: pd.Series, *, periods_per_year: int) -> dict[str, float]:
    """Adapted from the working live-trading metrics module."""
    values = equity.astype(float).sort_index()
    if values.empty or (values <= 0).any():
        raise ValueError("equity must be non-empty and positive")
    returns = values.pct_change().dropna()
    volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = 0.0 if volatility == 0.0 else math.sqrt(periods_per_year) * float(returns.mean()) / volatility
    drawdown = values / values.cummax() - 1.0
    return {
        "cumulative_return": float(values.iloc[-1] / values.iloc[0] - 1.0),
        "sharpe": float(sharpe),
        "max_drawdown": float(drawdown.min()),
        "observations": float(len(returns)),
    }
```

Choose `periods_per_year` honestly. For daily returns it is conventionally about 252; for intraday returns, state the number of eligible periods per full session multiplied by the number of full sessions. Annualized Sharpe from a short, correlated intraday sample is a descriptive statistic, not proof of performance.

## 6. Research checks before reading a headline metric

1. **Time correctness:** all features have `available_time <= decision_time`; execution happens after the decision.
2. **Data correctness:** no duplicate bars; OHLC invariants and expected-session coverage pass; raw/split-adjusted use is explicit.
3. **Economic correctness:** costs, latency, and exit policy are frozen; a stock proxy is never presented as option P&L.
4. **Robustness:** run only the prespecified sensitivity cells and falsification/null tests; retain the losers.
5. **Reproducibility:** a fresh output directory produces the same authoritative hashes from the same input manifests.
6. **Integration correctness:** the package implements the [strategy API](../architecture/STRATEGY_API.md) and calls the same pure signal function used offline.

## 7. Portable package handoff

For a Windows-compatible handoff, make the canonical reproduction entry point a Python module:

```text
uv run python -m <plugin_id>_v1.reproduce \
  --data-manifest <path-to-data_manifest.json> \
  --feasibility-manifest <path-to-option_proxy_feasibility_manifest.json> \
  --output <empty-output-directory>
```

On PowerShell, use the same command with PowerShell line continuations or one line. A package may also include `scripts/reproduce.sh` and `scripts/reproduce.ps1` wrappers, but those wrappers must invoke the same Python module and cannot have different logic. The command must make no network, credential, account, order, or MCP-trading call.

Your submitted package should contain the frozen strategy card/config/features, `signal.py`, golden tests, negative tests, run manifest, metrics, cost stress, data hashes, and explicit state (`REJECTED`, `RESEARCH_COMPLETE`, or central integration review requested). It never sets `PAPER_ENABLED`.
