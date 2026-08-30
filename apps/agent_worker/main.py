"""Internal HTTP surface for schema-constrained advisory theses."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from packages.agent.advisory import AdvisoryModelClient
from packages.contracts.agent_input import AgentRequestV1
from packages.contracts.models import AgentThesisV1


def create_app(client: AdvisoryModelClient) -> FastAPI:
    app = FastAPI(title="RegimeSwitch advisory worker", version="v1", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/v1/theses", response_model=AgentThesisV1)
    def create_thesis(request: AgentRequestV1) -> AgentThesisV1:
        return client.create_thesis(request)

    return app


def main() -> None:
    catalog_path = Path(
        os.environ.get("AGENT_MODEL_CONFIG_PATH", "/app/configs/advisory_models.yaml")
    )
    client = AdvisoryModelClient.from_environment(
        environ=dict(os.environ),
        catalog_path=catalog_path,
    )
    try:
        uvicorn.run(
            create_app(client),
            host="0.0.0.0",
            port=int(os.environ.get("AGENT_PORT", "8081")),
            log_level="warning",
            access_log=False,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
