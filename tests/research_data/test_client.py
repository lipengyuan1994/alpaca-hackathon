from __future__ import annotations

import json

import pytest

from packages.research_data.client import HttpResponse, ReadOnlyAlpacaClient, ResearchHttpError


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        return self.responses.pop(0)


def _response(payload: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(status, {"x-ratelimit-remaining": "199"}, json.dumps(payload).encode())


def test_client_consumes_all_pages_and_retains_raw_hashes() -> None:
    transport = FakeTransport([_response({"bars": {}, "next_page_token": "next"}), _response({"bars": {}})])
    pages = ReadOnlyAlpacaClient(headers={"x": "y"}, transport=transport).get_paginated(
        base_url="https://data.example", endpoint="/bars", params={"symbols": "SPY"}
    )
    assert len(pages) == 2
    assert "page_token=next" in transport.urls[1]
    assert pages[0].raw_hash.startswith("sha256:")


def test_client_rejects_non_success_or_repeated_token() -> None:
    failed = ReadOnlyAlpacaClient(headers={}, transport=FakeTransport([_response({}, 403)]))
    with pytest.raises(ResearchHttpError, match="ALPACA_HTTP_403"):
        failed.get_paginated(base_url="https://data.example", endpoint="/bars", params={})

    loop = ReadOnlyAlpacaClient(headers={}, transport=FakeTransport([_response({"next_page_token": "loop"}), _response({"next_page_token": "loop"})]))
    with pytest.raises(ResearchHttpError, match="ALPACA_PAGINATION_TOKEN_REPEATED"):
        loop.get_paginated(base_url="https://data.example", endpoint="/bars", params={})
