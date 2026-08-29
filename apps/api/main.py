"""Read-only FastAPI surface for judge replay.  It has no mutation routes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from packages.ledger import MemoryLedger


def create_app(ledger: MemoryLedger | None = None) -> FastAPI:
    # Deployment injects a SELECT-only read-model adapter.  The empty memory
    # store is solely for liveness/startup; it never starts a decision worker.
    store = ledger or MemoryLedger()
    app = FastAPI(title="RegimeSwitch public replay API", version="v1")

    @app.get("/v1/status")
    def status() -> dict[str, str]:
        return {"service": "api", "mode": "REPLAY", "authority": "read-only"}

    @app.get("/v1/runs")
    def runs() -> list[dict[str, str]]:
        return [{"run_id": run_id} for run_id in sorted({event.run_id for event in store.events})]

    @app.get("/v1/decisions/{decision_id}")
    def decision(decision_id: str) -> dict[str, Any]:
        tape = store.decision_tape(decision_id)
        if not tape:
            raise HTTPException(status_code=404, detail="decision tape not found")
        return {"decision_id": decision_id, "events": tape}

    @app.get("/v1/replay/{run_id}")
    def replay(run_id: str) -> dict[str, Any]:
        tape = store.decision_tape(run_id)
        if not tape:
            raise HTTPException(status_code=404, detail="replay not found")
        return {"run_id": run_id, "events": tape}

    @app.get("/v1/pnl")
    def pnl() -> dict[str, str]:
        return {
            "account_reported_paper_pnl": "UNAVAILABLE_IN_REPLAY",
            "conservative_shadow_pnl": "UNAVAILABLE_IN_REPLAY",
            "official_contest_score": "NOT_ORGANIZER_DEFINED",
        }

    @app.get("/v1/events")
    def events() -> StreamingResponse:
        def stream() -> Iterator[str]:
            for event in store.events:
                yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()


def run() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
