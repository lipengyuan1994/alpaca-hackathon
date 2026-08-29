from __future__ import annotations

from pathlib import Path

import pytest

from apps.decision_worker.main import run_approved_fixture
from packages.order_planner import CatalogError, load_template_catalog, template_catalog_hash


def test_runtime_loads_the_committed_catalog_and_uses_nearest_spot_policy() -> None:
    catalog = load_template_catalog()
    decision = run_approved_fixture()
    plan = decision.command.plan
    assert template_catalog_hash == catalog.content_hash
    assert plan.legs[0].strike == 600
    assert plan.legs[1].strike == 605
    assert plan.quantity == 2
    assert plan.maximum_loss == 250
    assert decision.risk_input.template_catalog_hash == catalog.content_hash


def test_catalog_hash_covers_selector_authority(tmp_path: Path) -> None:
    source = Path("configs/template_catalog.yaml").read_text(encoding="utf-8")
    changed = tmp_path / "changed.yaml"
    changed.write_text(source.replace("target_short_offset_fraction: 0.01", "target_short_offset_fraction: 0.02", 1))
    assert load_template_catalog(changed).content_hash != template_catalog_hash


def test_catalog_schema_fails_closed_on_unknown_field(tmp_path: Path) -> None:
    source = Path("configs/template_catalog.yaml").read_text(encoding="utf-8")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(source + "unknown_authority: true\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="TEMPLATE_CATALOG_INVALID"):
        load_template_catalog(invalid)
