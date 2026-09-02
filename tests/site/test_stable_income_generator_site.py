from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import pytest

from packages.contracts.canonical import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
HTML_PATH = DOCS / "index.html"
BENCHMARK_PATH = DOCS / "assets" / "data" / "v13-5-benchmark.json"
LIVE_PATH = DOCS / "assets" / "data" / "live-paper-snapshot.json"


class _References(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.references.append(value)


def test_public_site_references_existing_local_assets() -> None:
    parser = _References()
    parser.feed(HTML_PATH.read_text(encoding="utf-8"))
    missing: list[str] = []
    for reference in parser.references:
        parsed = urlparse(reference)
        if parsed.scheme or parsed.netloc or reference.startswith("#"):
            continue
        target = (DOCS / parsed.path).resolve()
        if not target.is_relative_to(DOCS.resolve()) or not target.is_file():
            missing.append(reference)
    assert not missing


def test_pages_workflow_publishes_only_the_curated_site() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )
    assert "path: _site" in workflow
    assert "path: docs" not in workflow
    assert "cp docs/index.html docs/.nojekyll _site/" in workflow


def test_public_copy_matches_approved_paper_snapshot() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    snapshot = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
    assert snapshot["source"] == "broker_reported_paper"
    assert snapshot["account_id"] in html
    assert "$100,079.73" in html
    assert "+$79.73" in html
    assert "Gemini 3.6 Flash" in html
    forbidden = ("paper-api.alpaca.markets", "paper_alpaca_api_key", "api_secret")
    assert all(value not in html for value in forbidden)


def test_benchmark_artifact_is_hash_bound_and_copy_is_exact() -> None:
    artifact = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    assert artifact["artifact_hash"] == canonical_hash(
        {key: value for key, value in artifact.items() if key != "artifact_hash"}
    )
    assert artifact["status"] == "RESEARCH_ONLY_EXPLORATORY_IN_SAMPLE"
    assert artifact["period"]["strategy_sessions"] == 644
    assert artifact["metrics"]["v13_5"]["total_return"] == pytest.approx(0.302858)
    assert artifact["metrics"]["spy"]["total_return"] == pytest.approx(0.5662912589)
    assert artifact["metrics"]["qqq"]["total_return"] == pytest.approx(0.6670914755)
    assert artifact["regime_summary"]["downtrend"]["v13_5"][
        "conditional_compounded_return"
    ] < 0


def test_chart_and_generated_banner_are_valid_assets() -> None:
    chart = DOCS / "assets" / "v13-5-performance.svg"
    banner = DOCS / "assets" / "stable-income-generator-banner.png"
    root = ElementTree.parse(chart).getroot()
    assert root.tag.endswith("svg")
    assert banner.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert banner.stat().st_size > 1_000_000
