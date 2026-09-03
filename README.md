# RegimeSwitch — paper-only options-agent system

This is the executable modular-monolith skeleton defined in the
[system architecture](docs/architecture/SYSTEM_ARCHITECTURE.md). It has no
live-trading mode, endpoint, or live credential path.

The public judge-facing product story is the
[Stable Income Generator website](https://lipengyuan1994.github.io/alpaca-hackathon/),
with separated backtest, deterministic-system, and 30-minute broker-reported
paper evidence. Its dedicated
[architecture page](https://lipengyuan1994.github.io/alpaca-hackathon/architecture/ARCHITECTURE_DESIGN.html)
shows the physical, logical, data-flow, PostgreSQL, and container boundaries.

The following are **core-platform macOS/ARM64** fixture paths. They run without network or broker credentials:

```zsh
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv sync --frozen
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run python -m pytest
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run paper-decision-worker
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run paper-decision-worker --approved
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run pytest -q tests/e2e/test_fixture_full_flow.py
```

The normal fixture produces a visible, deterministic `NO_TRADE` decision tape.
The `--approved` fixture ends at the transactional outbox; it does not contact
Alpaca. The execution-side test consumes that immutable command with the fake
broker, independently validates all preflight bindings, and exposes the
accepted/fill lifecycle in the credential-free public replay tape.

Offline strategy researchers on Windows, Linux, or non-ARM macOS should use
the platform-neutral setup and reproduction guidance in
[docs/research/quant_trading_basic.md](docs/research/quant_trading_basic.md),
not the Mac-specific executable path above.

`apps/api` exposes credential-free read-only replay endpoints. `apps/operator_cli`
only validates one-shot control command payloads; it has no broker or public
API integration. Role-specific Dockerfiles are in [infra](infra).

The paper runtime uses file-mounted Compose secrets.  Provision the external
secret directory and role boundaries described in
[the Compose secret runbook](docs/deployment/COMPOSE_SECRETS.md); do not put
secret values in `.env`, source, tests, documentation, or the public dashboard.
For the durable local database that backs the paper-only execution ledger, use
the [local Docker PostgreSQL runbook](docs/deployment/LOCAL_POSTGRES.md).
The bounded QQQ V13.5 paper-wheel canary has a separate
[preflight, arming, scheduling, reconciliation, and halt runbook](docs/deployment/PAPER_WHEEL_V13_5.md).
It is Alpaca-paper-only and does not create a live-trading path.
The one-capture-per-day economic support/veto gate, its PostgreSQL audit table,
and its external morning scheduler trigger are described in the
[daily economic-context runbook](docs/deployment/ECONOMIC_CONTEXT.md).

Start with the [documentation index](docs/index.md). The competition strategy,
risk posture, ownership model, and delivery gates remain in
[HACKATHON_PLAN.md](HACKATHON_PLAN.md). The strategy boundary and delivery
sequence are in [STRATEGY_API.md](docs/architecture/STRATEGY_API.md) and
[SKELETON_IMPLEMENTATION_PLAN.md](docs/plans/SKELETON_IMPLEMENTATION_PLAN.md).
