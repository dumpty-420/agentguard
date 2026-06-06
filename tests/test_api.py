"""tests/test_api.py — Tests for the FastAPI API endpoints."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

import main
from main import app


@pytest.fixture(autouse=True)
def mock_lifecycle():
    """Mock Database and Orchestrator startup/shutdown to avoid real connections in tests."""
    with patch("core.database.Database.connect", new_callable=AsyncMock), \
         patch("core.database.Database.close", new_callable=AsyncMock), \
         patch("core.orchestrator.Orchestrator.setup", new_callable=AsyncMock), \
         patch("core.orchestrator.Orchestrator.shutdown", new_callable=AsyncMock):
        yield


@pytest.fixture
def mock_orchestrator():
    """Mock the orchestrator global."""
    orchestrator = MagicMock()
    orchestrator.run = AsyncMock()
    orchestrator.resume = AsyncMock()
    orchestrator.checkpointer = MagicMock()
    orchestrator.checkpointer.has_checkpoint = AsyncMock()
    orchestrator.checkpointer.get_state = AsyncMock()
    orchestrator.checkpointer.get_next_steps = AsyncMock()
    orchestrator.graph = MagicMock()

    with patch("main._orchestrator", orchestrator):
        yield orchestrator


@pytest.fixture
def client():
    """FastAPI TestClient."""
    with TestClient(app) as test_client:
        yield test_client


def test_health_check(client) -> None:
    """GET /health returns 200 and version."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_start_run_success(client, mock_orchestrator) -> None:
    """POST /run successfully starts a run."""
    mock_orchestrator.run.return_value = {
        "completed_steps": ["step_one"],
        "messages": ["result_msg"],
    }

    payload = {
        "topic": "test topic",
        "user_id": "user-123",
        "run_id": "run-abc",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-abc",
        "completed_steps": ["step_one"],
        "messages": ["result_msg"],
    }
    mock_orchestrator.run.assert_awaited_once_with(
        "test topic",
        run_id="run-abc",
        user_id="user-123",
    )


def test_start_run_runtime_error(client, mock_orchestrator) -> None:
    """POST /run raises 502 Bad Gateway on RuntimeError (e.g. BudgetExceeded or Failover failure)."""
    mock_orchestrator.run.side_effect = RuntimeError("Budget exceeded")

    payload = {
        "topic": "test topic",
        "user_id": "user-123",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 502
    assert "Budget exceeded" in response.json()["detail"]


def test_start_run_unexpected_error(client, mock_orchestrator) -> None:
    """POST /run raises 500 Internal Server Error on unexpected exceptions."""
    mock_orchestrator.run.side_effect = ValueError("Boom")

    payload = {
        "topic": "test topic",
        "user_id": "user-123",
    }
    response = client.post("/run", json=payload)
    assert response.status_code == 500
    assert "Boom" in response.json()["detail"]


def test_resume_run_success(client, mock_orchestrator) -> None:
    """POST /resume successfully resumes a run."""
    mock_orchestrator.resume.return_value = {
        "completed_steps": ["step_one", "step_two"],
        "messages": ["resumed_msg"],
    }

    payload = {
        "run_id": "run-abc",
        "user_id": "user-123",
    }
    response = client.post("/resume", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-abc",
        "completed_steps": ["step_one", "step_two"],
        "messages": ["resumed_msg"],
    }
    mock_orchestrator.resume.assert_awaited_once_with(
        run_id="run-abc",
        user_id="user-123",
    )


def test_resume_run_not_found(client, mock_orchestrator) -> None:
    """POST /resume raises 404 Not Found if run doesn't exist."""
    mock_orchestrator.resume.side_effect = ValueError("No checkpoint found")

    payload = {
        "run_id": "run-nonexistent",
        "user_id": "user-123",
    }
    response = client.post("/resume", json=payload)
    assert response.status_code == 404
    assert "No checkpoint found" in response.json()["detail"]


def test_get_run_state_no_checkpoint(client, mock_orchestrator) -> None:
    """GET /run/{run_id} returns empty lists if no checkpoint exists."""
    mock_orchestrator.checkpointer.has_checkpoint.return_value = False

    response = client.get("/run/run-abc")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-abc",
        "has_checkpoint": False,
        "completed_steps": [],
        "next_steps": [],
    }


def test_get_run_state_with_checkpoint(client, mock_orchestrator) -> None:
    """GET /run/{run_id} returns state and next steps if checkpoint exists."""
    mock_orchestrator.checkpointer.has_checkpoint.return_value = True
    mock_orchestrator.checkpointer.get_state.return_value = {
        "completed_steps": ["step_one"],
    }
    mock_orchestrator.checkpointer.get_next_steps.return_value = ["step_two"]

    response = client.get("/run/run-abc")
    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-abc",
        "has_checkpoint": True,
        "completed_steps": ["step_one"],
        "next_steps": ["step_two"],
    }
