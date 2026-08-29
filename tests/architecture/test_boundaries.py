from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_decision_role_has_no_execution_adapter_dependency() -> None:
    forbidden = ("execution_core", "alpaca_execution_mcp", "FakeBroker")
    sources = [
        ROOT / "apps/decision_worker/main.py",
        *sorted((ROOT / "packages/decision_core").glob("*.py")),
    ]
    imports = "\n".join(
        line
        for path in sources
        for line in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*(from|import)\s+", line)
    )
    assert not any(item in imports for item in forbidden)


def test_strategy_plugins_do_not_import_platform_or_broker_packages() -> None:
    forbidden = ("apps.", "execution_core", "risk_kernel", "order_planner", "alpaca")
    for path in (ROOT / "strategy_plugins").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(item in source for item in forbidden), path


def test_public_api_declares_get_only_business_routes() -> None:
    from apps.api.main import create_app

    app = create_app()
    business_routes = [route for route in app.routes if getattr(route, "path", "").startswith("/v1/")]
    assert business_routes
    assert all(route.methods == {"GET"} for route in business_routes)


def test_public_api_does_not_import_workers_or_execution_adapters() -> None:
    source = (ROOT / "apps/api/main.py").read_text(encoding="utf-8")
    assert "decision_worker" not in source
    assert "execution_worker" not in source
    assert "alpaca_execution_mcp" not in source
