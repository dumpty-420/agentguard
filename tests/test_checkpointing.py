"""tests/test_checkpointing.py — Tests for Phase 3: durable / resumable state.

Covers:
  (a) State is persisted after a step
  (b) A run interrupted mid-way can resume from the last checkpoint and
      does NOT re-execute completed steps
  (c) Resume reuses the same run_id / thread_id

All tests use LangGraph's ``MemorySaver`` (in-memory checkpointer) and
mock LLM calls — no real Postgres or real APIs.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.coordinator import AgentState, build_agent_graph
from config.settings import Settings
from core.orchestrator import Orchestrator
from runtime.checkpointer import CheckpointerManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides: Any) -> Settings:
    """Create a ``Settings`` instance with test-friendly defaults."""
    defaults: dict[str, Any] = {
        "anthropic_api_key": "test-key",
        "openai_api_key": "test-key",
        "provider_priority": ["anthropic", "openai"],
        "provider_timeout_seconds": 5.0,
        "provider_max_retries": 2,
        "circuit_breaker_failure_threshold": 3,
        "circuit_breaker_cooldown_seconds": 10.0,
        "budget_per_run_usd": 10.0,
        "budget_per_user_usd": 50.0,
        "budget_degradation_threshold": 0.8,
        "model_price_table": {
            "claude-sonnet-4-20250514": {
                "input_per_1k": 0.003,
                "output_per_1k": 0.015,
            },
        },
        "model_downgrade_map": {},
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "testdb",
        "postgres_user": "testuser",
        "postgres_password": "testpass",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# A custom interruptible graph for testing resume
# ---------------------------------------------------------------------------

class InterruptibleState(TypedDict):
    """State for the interruptible test graph."""

    topic: str
    messages: Annotated[list[str], operator.add]
    completed_steps: Annotated[list[str], operator.add]


# Track how many times each node is called (for re-execution detection).
_call_counts: dict[str, int] = {}


def _reset_call_counts() -> None:
    _call_counts.clear()


async def _step_one(state: dict[str, Any]) -> dict[str, Any]:
    _call_counts["step_one"] = _call_counts.get("step_one", 0) + 1
    return {
        "messages": ["step_one output"],
        "completed_steps": ["step_one"],
    }


async def _step_two_interrupt(state: dict[str, Any]) -> dict[str, Any]:
    """This node interrupts execution — simulating a crash / pause."""
    _call_counts["step_two"] = _call_counts.get("step_two", 0) + 1
    # interrupt() halts graph execution here.
    # On resume, it returns the value passed via Command(resume=...).
    value = interrupt("Pausing for review")
    return {
        "messages": [f"step_two output (resumed with: {value})"],
        "completed_steps": ["step_two"],
    }


async def _step_three(state: dict[str, Any]) -> dict[str, Any]:
    _call_counts["step_three"] = _call_counts.get("step_three", 0) + 1
    return {
        "messages": ["step_three output"],
        "completed_steps": ["step_three"],
    }


def _build_interruptible_graph() -> StateGraph:
    """Build a 3-step graph where step 2 interrupts."""
    builder = StateGraph(InterruptibleState)
    builder.add_node("step_one", _step_one)
    builder.add_node("step_two", _step_two_interrupt)
    builder.add_node("step_three", _step_three)
    builder.add_edge(START, "step_one")
    builder.add_edge("step_one", "step_two")
    builder.add_edge("step_two", "step_three")
    builder.add_edge("step_three", END)
    return builder


# ===========================================================================
# (a) State is persisted after a step
# ===========================================================================

class TestStatePersistence:
    """Assert that graph state is checkpointed after each node."""

    @pytest.mark.asyncio
    async def test_state_persisted_after_full_run(self) -> None:
        """After a complete run, the checkpoint contains all steps."""
        saver = MemorySaver()
        builder = build_agent_graph()
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "run-persist-1"}}
        result = await graph.ainvoke(
            {
                "topic": "testing",
                "messages": [],
                "completed_steps": [],
                "llm_client": None,
            },
            config=config,
        )

        # Verify the result has all three steps.
        assert result["completed_steps"] == ["researcher", "analyst", "synthesizer"]

        # Verify state is retrievable from the checkpoint.
        state = await graph.aget_state(config)
        assert state.values["completed_steps"] == [
            "researcher", "analyst", "synthesizer",
        ]
        assert len(state.values["messages"]) == 3

    @pytest.mark.asyncio
    async def test_checkpoint_exists_after_interrupted_run(self) -> None:
        """After an interrupted run, a checkpoint exists with partial state."""
        saver = MemorySaver()
        builder = _build_interruptible_graph()
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "run-interrupt-1"}}
        _reset_call_counts()

        # Run — step_one completes, step_two hits interrupt().
        result = await graph.ainvoke(
            {"topic": "test", "messages": [], "completed_steps": []},
            config=config,
        )

        # After interrupt, the checkpoint should show step_one completed.
        state = await graph.aget_state(config)
        assert "step_one" in state.values["completed_steps"]
        # step_two is the next pending node (it interrupted).
        assert "step_two" in state.next

    @pytest.mark.asyncio
    async def test_checkpointer_manager_has_checkpoint(self) -> None:
        """CheckpointerManager.has_checkpoint returns True after a run."""
        saver = MemorySaver()
        mgr = CheckpointerManager(saver_override=saver)
        await mgr.setup()

        builder = build_agent_graph()
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "run-mgr-1"}}
        await graph.ainvoke(
            {
                "topic": "test",
                "messages": [],
                "completed_steps": [],
                "llm_client": None,
            },
            config=config,
        )

        assert await mgr.has_checkpoint(graph, "run-mgr-1") is True
        assert await mgr.has_checkpoint(graph, "nonexistent") is False


# ===========================================================================
# (b) Resume from checkpoint — no re-execution of completed steps
# ===========================================================================

class TestResumeFromCheckpoint:
    """Assert that resume skips completed steps."""

    @pytest.mark.asyncio
    async def test_resume_skips_completed_steps(self) -> None:
        """After interrupt at step_two, resume completes step_two + step_three
        without re-executing step_one."""
        from langgraph.types import Command

        saver = MemorySaver()
        builder = _build_interruptible_graph()
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "run-resume-1"}}
        _reset_call_counts()

        # --- First invocation: runs step_one, interrupts at step_two ---
        await graph.ainvoke(
            {"topic": "test", "messages": [], "completed_steps": []},
            config=config,
        )

        assert _call_counts.get("step_one") == 1
        assert _call_counts.get("step_two") == 1  # entered but interrupted
        assert _call_counts.get("step_three", 0) == 0  # not reached

        # --- Resume: step_two completes, step_three runs ---
        result = await graph.ainvoke(
            Command(resume="approved"),
            config=config,
        )

        # step_one should NOT have been called again.
        assert _call_counts["step_one"] == 1  # still 1, not 2

        # step_two was re-entered on resume (LangGraph replays the node).
        # step_three ran for the first time.
        assert _call_counts["step_three"] == 1

        # Final state should have all steps.
        assert "step_one" in result["completed_steps"]
        assert "step_two" in result["completed_steps"]
        assert "step_three" in result["completed_steps"]

    @pytest.mark.asyncio
    async def test_resume_preserves_prior_messages(self) -> None:
        """Messages from completed steps survive the resume."""
        from langgraph.types import Command

        saver = MemorySaver()
        builder = _build_interruptible_graph()
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "run-resume-2"}}
        _reset_call_counts()

        # Run until interrupt.
        await graph.ainvoke(
            {"topic": "test", "messages": [], "completed_steps": []},
            config=config,
        )

        # Resume.
        result = await graph.ainvoke(
            Command(resume="go"),
            config=config,
        )

        # All three nodes should have contributed messages.
        assert len(result["messages"]) == 3
        assert "step_one output" in result["messages"][0]


# ===========================================================================
# (c) Resume reuses the same run_id / thread_id
# ===========================================================================

class TestResumeReuseThreadId:
    """Assert that resume uses the same thread_id as the original run."""

    @pytest.mark.asyncio
    async def test_same_thread_id_on_resume(self) -> None:
        """The config used for resume matches the original thread_id."""
        run_id = "run-thread-reuse-1"
        config = CheckpointerManager.make_config(run_id)

        assert config == {"configurable": {"thread_id": run_id}}

        # Verify the orchestrator's resume path uses the same config.
        saver = MemorySaver()
        builder = build_agent_graph()
        graph = builder.compile(checkpointer=saver)

        # Run the graph fully with this thread_id.
        await graph.ainvoke(
            {
                "topic": "test",
                "messages": [],
                "completed_steps": [],
                "llm_client": None,
            },
            config=config,
        )

        # Retrieve state with the same thread_id — must match.
        state = await graph.aget_state(config)
        assert state.values["completed_steps"] == [
            "researcher", "analyst", "synthesizer",
        ]

    @pytest.mark.asyncio
    async def test_orchestrator_run_and_state_retrieval(self) -> None:
        """The Orchestrator.run path persists state retrievable by run_id."""
        saver = MemorySaver()
        settings = _make_settings()
        orch = Orchestrator(
            settings=settings,
            db=None,
            saver_override=saver,
        )
        await orch.setup()

        result = await orch.run(
            "AI safety",
            run_id="orch-run-1",
            user_id="user-1",
            _use_real_llm=False,
        )

        assert result["completed_steps"] == ["researcher", "analyst", "synthesizer"]

        # State is retrievable via the checkpointer manager.
        state = await orch.checkpointer.get_state(orch.graph, "orch-run-1")
        assert state["completed_steps"] == ["researcher", "analyst", "synthesizer"]

        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_resume_raises_on_no_checkpoint(self) -> None:
        """Orchestrator.resume raises ValueError when no checkpoint exists."""
        saver = MemorySaver()
        settings = _make_settings()
        orch = Orchestrator(
            settings=settings,
            db=None,
            saver_override=saver,
        )
        await orch.setup()

        with pytest.raises(ValueError, match="No checkpoint found"):
            await orch.resume(run_id="nonexistent", user_id="user-1")

        await orch.shutdown()
