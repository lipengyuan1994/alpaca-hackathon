"""Small, injectable HTTP boundary for read-only Alpaca collection."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class ResearchHttpError(RuntimeError):
    """A provider response cannot safely be used as research input."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str]) -> HttpResponse: ...


@dataclass(frozen=True)
class UrllibTransport:
    timeout_seconds: int = 30

    def get(self, url: str, *, headers: Mapping[str, str]) -> HttpResponse:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return HttpResponse(
                    status_code=int(response.status),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:  # pragma: no cover - provider-dependent
            return HttpResponse(
                status_code=int(exc.code),
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=exc.read(),
            )
        except Exception as exc:  # pragma: no cover - depends on provider/network
            raise ResearchHttpError(f"READ_ONLY_ALPACA_REQUEST_FAILED: {exc}") from exc


@dataclass(frozen=True)
class FetchedPage:
    endpoint: str
    request_params: dict[str, str]
    page_token: str | None
    response_headers: dict[str, str]
    payload: dict[str, Any] | list[Any]
    raw_bytes: bytes
    raw_hash: str


@dataclass(frozen=True)
class ReadOnlyAlpacaClient:
    """Only GET requests, with every provider page retained for provenance."""

    headers: Mapping[str, str]
    transport: HttpTransport = UrllibTransport()
    max_rate_limit_retries: int = 3

    def _get(self, url: str) -> HttpResponse:
        """Retry only the exact same GET after an explicit provider throttle."""
        for attempt in range(self.max_rate_limit_retries + 1):
            response = self.transport.get(url, headers=self.headers)
            if response.status_code != 429:
                return response
            if attempt == self.max_rate_limit_retries:
                return response
            retry_after = response.headers.get("retry-after", "60")
            try:
                seconds = min(60, max(1, int(float(retry_after))))
            except ValueError:
                seconds = 60
            time.sleep(seconds)
        raise AssertionError("unreachable")

    def get_paginated(
        self,
        *,
        base_url: str,
        endpoint: str,
        params: Mapping[str, str],
    ) -> tuple[FetchedPage, ...]:
        pages: list[FetchedPage] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            request_params = {str(key): str(value) for key, value in params.items()}
            if page_token is not None:
                if page_token in seen_tokens:
                    raise ResearchHttpError("ALPACA_PAGINATION_TOKEN_REPEATED")
                seen_tokens.add(page_token)
                request_params["page_token"] = page_token
            query = urllib.parse.urlencode(request_params)
            url = f"{base_url.rstrip('/')}{endpoint}?{query}" if query else f"{base_url.rstrip('/')}{endpoint}"
            response = self._get(url)
            if response.status_code != 200:
                raise ResearchHttpError(f"ALPACA_HTTP_{response.status_code}")
            decoded = self._decode_object(response.body)
            pages.append(
                FetchedPage(
                    endpoint=endpoint,
                    request_params=request_params,
                    page_token=page_token,
                    response_headers=dict(response.headers),
                    payload=decoded,
                    raw_bytes=response.body,
                    raw_hash="sha256:" + hashlib.sha256(response.body).hexdigest(),
                )
            )
            next_token = decoded.get("next_page_token")
            if next_token is None or next_token == "":
                return tuple(pages)
            if not isinstance(next_token, str):
                raise ResearchHttpError("ALPACA_NEXT_PAGE_TOKEN_INVALID")
            page_token = next_token

    def get_one(
        self,
        *,
        base_url: str,
        endpoint: str,
        params: Mapping[str, str],
    ) -> FetchedPage:
        request_params = {str(key): str(value) for key, value in params.items()}
        query = urllib.parse.urlencode(request_params)
        url = f"{base_url.rstrip('/')}{endpoint}?{query}" if query else f"{base_url.rstrip('/')}{endpoint}"
        response = self._get(url)
        if response.status_code != 200:
            raise ResearchHttpError(f"ALPACA_HTTP_{response.status_code}")
        payload = self._decode_response(response.body)
        return FetchedPage(
            endpoint=endpoint,
            request_params=request_params,
            page_token=None,
            response_headers=dict(response.headers),
            payload=payload,
            raw_bytes=response.body,
            raw_hash="sha256:" + hashlib.sha256(response.body).hexdigest(),
        )

    @staticmethod
    def _decode_object(body: bytes) -> dict[str, Any]:
        decoded = ReadOnlyAlpacaClient._decode_response(body)
        if not isinstance(decoded, dict):
            raise ResearchHttpError("ALPACA_RESPONSE_NOT_OBJECT")
        return decoded

    @staticmethod
    def _decode_response(body: bytes) -> dict[str, Any] | list[Any]:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchHttpError("ALPACA_RESPONSE_NOT_JSON") from exc
        if not isinstance(decoded, (dict, list)):
            raise ResearchHttpError("ALPACA_RESPONSE_NOT_OBJECT_OR_ARRAY")
        return decoded
