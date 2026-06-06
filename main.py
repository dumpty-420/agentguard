"""main.py — FastAPI entry point for AgentGuard.

Exposes the agent runtime as an HTTP API.  All three runtime layers
(failover, cost governance, checkpointing) are composed inside the
``Orchestrator`` which is initialized at startup.

Run locally::

    uvicorn main:app --reload --port 8000

Or via Docker::

    docker compose up app
"""

from __future__ import annotations

import uuid
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config.settings import settings
from core.database import Database
from core.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state (initialized in lifespan)
# ---------------------------------------------------------------------------

_orchestrator: Orchestrator | None = None
_db: Database | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start up and tear down the orchestrator + database pool."""
    global _orchestrator, _db

    # --- Startup ---
    _db = Database(settings)
    await _db.connect()
    logger.info("Database pool connected.")

    _orchestrator = Orchestrator(settings=settings, db=_db)
    await _orchestrator.setup()
    logger.info("Orchestrator initialized — all runtime layers active.")

    yield

    # --- Shutdown ---
    if _orchestrator is not None:
        await _orchestrator.shutdown()
    if _db is not None:
        await _db.close()
    logger.info("AgentGuard shut down gracefully.")


app = FastAPI(
    title="AgentGuard",
    description=(
        "Agent runtime / control plane — provider failover, cost governance, "
        "and durable checkpointing for autonomous LLM agents."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    """Request body for starting a new agent run."""

    topic: str = Field(
        ...,
        description="The research topic for the agent to process.",
        min_length=1,
        max_length=1000,
    )
    user_id: str = Field(
        ...,
        description="Identifier for the user initiating the run.",
        min_length=1,
    )
    run_id: str | None = Field(
        default=None,
        description="Optional run ID.  Auto-generated if omitted.",
    )


class ResumeRequest(BaseModel):
    """Request body for resuming an interrupted run."""

    run_id: str = Field(..., description="The run_id to resume.", min_length=1)
    user_id: str = Field(..., description="User who owns the run.", min_length=1)


class RunResponse(BaseModel):
    """Response body for run / resume endpoints."""

    run_id: str
    completed_steps: list[str]
    messages: list[str]


class HealthResponse(BaseModel):
    """Response body for health check."""

    status: str
    version: str


class RunStateResponse(BaseModel):
    """Response body for run state introspection."""

    run_id: str
    has_checkpoint: bool
    completed_steps: list[str]
    next_steps: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Liveness / readiness probe."""
    return HealthResponse(status="ok", version="0.1.0")


@app.post("/run", response_model=RunResponse)
async def start_run(request: RunRequest) -> RunResponse:
    """Start a new agent run with all three runtime layers.

    The topic is processed through a researcher → analyst → synthesizer
    pipeline.  State is checkpointed to Postgres after each step.
    """
    assert _orchestrator is not None, "Orchestrator not initialized."

    run_id = request.run_id or f"run-{uuid.uuid4().hex[:12]}"

    try:
        result = await _orchestrator.run(
            request.topic,
            run_id=run_id,
            user_id=request.user_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in run=%s", run_id)
        raise HTTPException(status_code=500, detail=str(exc))

    return RunResponse(
        run_id=run_id,
        completed_steps=result.get("completed_steps", []),
        messages=result.get("messages", []),
    )


@app.post("/resume", response_model=RunResponse)
async def resume_run(request: ResumeRequest) -> RunResponse:
    """Resume an interrupted run from its last checkpoint.

    LangGraph rehydrates the state from Postgres and continues from
    the next pending node — already-completed steps are skipped.
    """
    assert _orchestrator is not None, "Orchestrator not initialized."

    try:
        result = await _orchestrator.resume(
            run_id=request.run_id,
            user_id=request.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error in resume=%s", request.run_id)
        raise HTTPException(status_code=500, detail=str(exc))

    return RunResponse(
        run_id=request.run_id,
        completed_steps=result.get("completed_steps", []),
        messages=result.get("messages", []),
    )


@app.get("/run/{run_id}", response_model=RunStateResponse)
async def get_run_state(run_id: str) -> RunStateResponse:
    """Inspect the checkpoint state of a run.

    Returns the completed steps and what would run next if resumed.
    """
    assert _orchestrator is not None, "Orchestrator not initialized."

    has_ckpt = await _orchestrator.checkpointer.has_checkpoint(
        _orchestrator.graph, run_id,
    )

    if not has_ckpt:
        return RunStateResponse(
            run_id=run_id,
            has_checkpoint=False,
            completed_steps=[],
            next_steps=[],
        )

    state = await _orchestrator.checkpointer.get_state(
        _orchestrator.graph, run_id,
    )
    next_steps = await _orchestrator.checkpointer.get_next_steps(
        _orchestrator.graph, run_id,
    )

    return RunStateResponse(
        run_id=run_id,
        has_checkpoint=True,
        completed_steps=state.get("completed_steps", []),
        next_steps=list(next_steps),
    )


# ---------------------------------------------------------------------------
# CLI entry (for pyproject.toml [project.scripts])
# ---------------------------------------------------------------------------

def cli_entry() -> None:
    """Run the server via ``agentguard`` CLI command."""
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    cli_entry()
