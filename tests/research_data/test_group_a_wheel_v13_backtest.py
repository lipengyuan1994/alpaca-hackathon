import pytest

from packages.research_data.group_a_wheel_v12_backtest import WheelReplayError
from packages.research_data.group_a_wheel_v13_variants_backtest import expand_variant_records


def _manifest(*, trend_up: bool) -> dict:
    contract_map = {
        "put_1pct": "P1",
        "call_1pct": "C1",
        "put_2pct": "P2",
        "call_2pct": "C2",
        "put_3pct": "P3",
        "call_3pct": "C3",
    }
    return {
        "variants": [
            {"variant_id": "v13.1", "put_otm_pct": 2, "call_otm_pct": 2, "take_profit_fraction": 0.15, "regime": "fixed"},
            {"variant_id": "v13.2", "put_otm_pct": 1, "call_otm_pct": 1, "take_profit_fraction": 0.15, "regime": "fixed"},
            {"variant_id": "v13.3", "put_otm_pct": 3, "call_otm_pct": 3, "take_profit_fraction": 0.15, "regime": "fixed"},
            {"variant_id": "v13.4", "put_otm_pct": 2, "call_otm_pct": 2, "take_profit_fraction": 0.25, "regime": "fixed"},
            {"variant_id": "v13.5", "put_otm_pct": None, "call_otm_pct": None, "take_profit_fraction": 0.15, "regime": "prior_50_session_trend"},
        ],
        "selection_records": [{
            "underlying": "QQQ",
            "decision_time": "2025-01-06T15:00:01Z",
            "expiry_time": "2025-01-17T21:05:00Z",
            "prior_50_session_trend_up": trend_up,
            "contract_map": contract_map,
        }],
        "requests": [{"request_id": "000001", "symbols": sorted(contract_map.values()), "start": "2025-01-06T15:00:01Z", "end": "2025-01-17T21:05:00Z"}],
    }


def test_expands_exactly_five_predeclared_variants() -> None:
    records = expand_variant_records(_manifest(trend_up=True))

    assert [record["variant_id"] for record in records] == ["v13.1", "v13.2", "v13.3", "v13.4", "v13.5"]
    assert records[0]["put_symbol"] == "P2"
    assert records[0]["call_symbol"] == "C2"
    assert records[3]["take_profit_fraction"] == 0.25


def test_trend_adaptive_variant_uses_wider_call_in_uptrend() -> None:
    up = expand_variant_records(_manifest(trend_up=True))[-1]
    down = expand_variant_records(_manifest(trend_up=False))[-1]

    assert (up["put_symbol"], up["call_symbol"]) == ("P1", "C3")
    assert (down["put_symbol"], down["call_symbol"]) == ("P3", "C1")


def test_missing_frozen_trend_yields_no_adaptive_trade() -> None:
    manifest = _manifest(trend_up=True)
    manifest["selection_records"][0]["prior_50_session_trend_up"] = None

    records = expand_variant_records(manifest)

    assert [record["variant_id"] for record in records] == ["v13.1", "v13.2", "v13.3", "v13.4"]


def test_variant_expansion_is_deterministic() -> None:
    manifest = _manifest(trend_up=False)

    assert expand_variant_records(manifest) == expand_variant_records(manifest)


def test_malformed_manifest_fails_closed() -> None:
    with pytest.raises(WheelReplayError, match="V13_REQUEST_MANIFEST_INVALID"):
        expand_variant_records({"variants": [], "selection_records": None, "requests": None})
