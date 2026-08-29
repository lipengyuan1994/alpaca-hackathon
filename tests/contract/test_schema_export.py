from __future__ import annotations

import inspect
from pathlib import Path

from packages.contracts import models
from packages.contracts.models import StrictModel, TimestampedModel
from packages.contracts.schema_export import MODELS, export, schema_filename

ROOT = Path(__file__).parents[2]
COMMITTED_SCHEMAS = ROOT / "schemas" / "v1"


def test_export_list_covers_every_concrete_public_v1_model() -> None:
    public_models = {
        value
        for name, value in vars(models).items()
        if name.endswith("V1")
        and inspect.isclass(value)
        and issubclass(value, StrictModel)
        and value not in {StrictModel, TimestampedModel}
    }
    assert set(MODELS) == public_models
    assert len(MODELS) == len(set(MODELS))


def test_committed_schemas_exactly_match_deterministic_export(tmp_path: Path) -> None:
    generated = tmp_path / "schemas"
    export(generated)

    expected_names = {schema_filename(model) for model in MODELS}
    assert {path.name for path in generated.glob("*.json")} == expected_names
    assert {path.name for path in COMMITTED_SCHEMAS.glob("*.json")} == expected_names
    for name in sorted(expected_names):
        assert (COMMITTED_SCHEMAS / name).read_bytes() == (generated / name).read_bytes()
