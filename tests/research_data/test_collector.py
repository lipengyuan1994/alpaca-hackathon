from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.research_data.client import HttpResponse, ReadOnlyAlpacaClient
from packages.research_data.collector import CollectionSpec, ResearchDataCollector


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(200, {"x-ratelimit-remaining": "199"}, json.dumps(self.responses.pop(0)).encode())


def _spec() -> CollectionSpec:
    return CollectionSpec("fixture", ("SPY",), "2025-01-02T14:30:00Z", "2025-01-02T21:00:00Z", "2025-01-02T14:30:00Z", "2025-01-02T21:00:00Z")


def _bar(timestamp: str) -> dict[str, object]:
    return {"t": timestamp, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 10, "n": 2, "vw": 100.2}


def test_collector_writes_hashed_manifest_and_provenance(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("collection_id: fixture\nsymbols: [SPY]\nstock_history: {start: '2025-01-02T14:30:00Z', end: '2025-01-02T21:00:00Z'}\noption_history: {start: '2025-01-02T14:30:00Z', end: '2025-01-02T21:00:00Z'}\n", encoding="utf-8")
    responses: list[dict[str, object]] = [
        {"bars": {"SPY": [_bar("2025-01-02T14:30:00Z")]}},
        {"bars": {"SPY": [_bar("2025-01-02T14:30:00Z")]}},
        {"calendar": []},
        {"option_contracts": []},
        {"option_contracts": []},
    ]
    collector = ResearchDataCollector(
        ReadOnlyAlpacaClient(headers={}, transport=FakeTransport(responses)),
        now=lambda: datetime(2025, 1, 3, tzinfo=UTC),
    )
    manifest = collector.collect(spec=_spec(), spec_path=spec_path, output=tmp_path / "out")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["status"] == "COLLECTED_UNATTESTED"
    assert payload["manifest_hash"].startswith("sha256:")
    assert (manifest.parent / "entitlement_probe.json").is_file()
    assert payload["entitlement_probe"]["probe_hash"].startswith("sha256:")
    stock = next(item for item in payload["datasets"] if item["dataset_id"] == "stock_bars_raw")
    assert stock["artifact"]["rows"] == 1
    assert (manifest.parent / stock["raw_pages"][0]["path"]).is_file()


def test_collector_refuses_nonempty_output_and_bad_ohlc(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "present").write_text("x", encoding="utf-8")
    collector = ResearchDataCollector(ReadOnlyAlpacaClient(headers={}, transport=FakeTransport([])))
    with pytest.raises(ValueError, match="RESEARCH_OUTPUT_DIRECTORY_NOT_EMPTY"):
        collector.collect(spec=_spec(), spec_path=tmp_path / "missing.yaml", output=output)


def test_collector_uses_indicative_only_for_current_option_quotes(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("x", encoding="utf-8")
    quote_symbols = tmp_path / "quotes.json"
    quote_symbols.write_text('{"symbols":["SPY250117C00600000"]}', encoding="utf-8")
    transport = FakeTransport(
        [
            {"bars": {"SPY": [_bar("2025-01-02T14:30:00Z")]}},
            {"bars": {"SPY": [_bar("2025-01-02T14:30:00Z")]}},
            [],
            {"option_contracts": []},
            {"option_contracts": []},
            {"quotes": {"SPY250117C00600000": {"t": "2025-01-02T14:30:00Z", "bp": 1, "ap": 1.1}}},
        ]
    )
    collector = ResearchDataCollector(
        ReadOnlyAlpacaClient(headers={}, transport=transport), now=lambda: datetime(2025, 1, 3, tzinfo=UTC)
    )
    manifest = collector.collect(
        spec=_spec(), spec_path=spec_path, output=tmp_path / "out", quote_symbols_path=quote_symbols
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert any(item["dataset_id"] == "option_quotes_indicative" for item in payload["datasets"])
    assert "feed=indicative" in transport.urls[-1]
