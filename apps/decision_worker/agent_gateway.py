"""Exact internal-only client for schema-constrained advisory endpoints."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from packages.contracts.agent_input import AgentRequestV1
from packages.contracts.economic_input import EconomicAssessmentRequestV1
from packages.contracts.models import AgentThesisV1, EconomicAssessmentV1


class InternalAdvisoryError(ValueError):
    """Fail-closed error from the decision-to-agent internal boundary."""


class InternalAdvisoryClient:
    """Talk only to the Compose-private ``agent`` service, never a model provider."""

    def __init__(self, base_url: str = "http://agent:8081") -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "agent"
            or parsed.port != 8081
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise InternalAdvisoryError("INTERNAL_AGENT_ORIGIN_NOT_EXACT")
        self._base_url = "http://agent:8081"

    def create_thesis(self, request: AgentRequestV1) -> AgentThesisV1:
        return AgentThesisV1.model_validate(self._post("/internal/v1/theses", request))

    def create_economic_assessment(
        self,
        request: EconomicAssessmentRequestV1,
    ) -> EconomicAssessmentV1:
        return EconomicAssessmentV1.model_validate(
            self._post("/internal/v1/economic-assessments", request)
        )

    def _post(self, path: str, payload: Any) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload.model_dump(mode="json"), separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 -- exact internal origin above
                body = response.read(131_073)
                if len(body) > 131_072:
                    raise InternalAdvisoryError("INTERNAL_AGENT_RESPONSE_TOO_LARGE")
        except (HTTPError, URLError, OSError) as exc:
            raise InternalAdvisoryError("INTERNAL_AGENT_UNAVAILABLE") from exc
        try:
            decoded = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise InternalAdvisoryError("INTERNAL_AGENT_RESPONSE_INVALID") from exc
        if not isinstance(decoded, dict):
            raise InternalAdvisoryError("INTERNAL_AGENT_RESPONSE_INVALID")
        return decoded
