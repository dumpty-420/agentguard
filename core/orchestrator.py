"""core/orchestrator.py — Wires the three runtime layers around the agent graph.

================================================================================
REQUEST FLOW THROUGH ALL THREE RUNTIME LAYERS
================================================================================

A fresh run (``Orchestrator.run``):

  API / Caller
    │
    ▼
  Orchestrator.run(topic, run_id, user_id)
    │
    ├── 1. CostGovernor(run_id, user_id, db)          ← Phase 2
    │       Per-run + per-user budget enforcement.
    │       Pre-checks budget, may downgrade model.
    │       Post-call records usage to cost_ledger.
    │
    ├── 2. LLMClient(router, cost_governor)            ← Phase 1 + 2
    │       Failover across providers with retry +
    │       circuit breaking.  Cost governor hooks
    │       transparently into every call.
    │
    ├── 3. Agent graph compiled WITH checkpointer      ← Phase 3
    │       │  State persisted to Postgres after each
    │       │  node completes.  Keyed by run_id →
    │       │  LangGraph thread_id.
    │       │
    │       ├── researcher node  → LLMClient.complete()
    │       ├── analyst node     → LLMClient.complete()
    │       └── synthesizer node → LLMClient.complete()
    │
    └── 4. graph.ainvoke(input, config={"thread_id": run_id})
            LangGraph runs the full graph, checkpointing
            state after each step.

A resumed run (``Orchestrator.resume``):

  API / Caller
    │
    ▼
  Orchestrator.resume(run_id, user_id)
    │
    ├── Same CostGovernor + LLMClient setup
    │
    └── graph.ainvoke(None, config={"thread_id": run_id})
            LangGraph rehydrates from the last checkpoint,
            skips already-completed steps, and continues
            from the next pending node.

Key invariant:
  Agents (coordinator, workers) NEVER see failover, budgets, or
  checkpoints.  All three runtime concerns are composed here.
================================================================================
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agents.coordinator import build_agent_graph
from agents.worker_agents import make_researcher_node, make_analyst_node, make_synthesizer_node
from agents.llm_client import LLMClient
from config.settings import Settings, settings as default_settings
from core.database import Database
from runtime.checkpointer import CheckpointerManager
from runtime.cost_governor import CostGovernor
from runtime.failover import FailoverRouter

logger = logging.getLogger(__name__)


class Orchestrator:
    """Composes failover, cost governance, and checkpointing around the agent graph.

    Usage::

        orch = Orchestrator()
        await orch.setup()
        try:
            result = await orch.run("AI safety", run_id="run-1", user_id="u-1")
        finally:
            await orch.shutdown()

    For testing, inject a ``saver_override`` (e.g. ``MemorySaver``) and
    set ``db=None`` to skip Postgres.

    Args:
        settings: Application settings.
        db: Database interface for cost ledger (``None`` skips DB).
        saver_override: Pre-built LangGraph checkpointer saver for testing.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        db: Database | None = None,
        saver_override: BaseCheckpointSaver | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._db = db
        self._checkpointer_mgr = CheckpointerManager(
            settings=self._settings,
            saver_override=saver_override,
        )
        self._graph: CompiledStateGraph | None = None

    # -- lifecycle ----------------------------------------------------------

    async def setup(self) -> None:
        """Initialize the checkpointer and compile the graph.

        Must be called before ``run`` or ``resume``.
        """
        saver = await self._checkpointer_mgr.setup()
        builder = build_agent_graph()
        self._graph = builder.compile(checkpointer=saver)
        logger.info("Orchestrator ready: graph compiled with checkpointer.")

    async def shutdown(self) -> None:
        """Release checkpointer resources."""
        await self._checkpointer_mgr.close()
        logger.info("Orchestrator shut down.")

    # -- public API ---------------------------------------------------------

    async def run(
        self,
        topic: str,
        *,
        run_id: str,
        user_id: str,
        llm_client: LLMClient | None = None,
        _use_real_llm: bool = True,
    ) -> dict[str, Any]:
        """Execute a full agent run with all three runtime layers.

        Args:
            topic: The research topic to process.
            run_id: Unique identifier for this run (maps to LangGraph thread_id).
            user_id: User who initiated the run.
            llm_client: Optional pre-built LLMClient. If not provided and
                *_use_real_llm* is True, one is created with failover +
                cost governor.
            _use_real_llm: Set to ``False`` in tests to skip real LLM
                calls.  Workers will produce deterministic output.

        Returns:
            The final graph state dict.
        """
        assert self._graph is not None, "Call setup() first."

        if llm_client is None and _use_real_llm:
            llm_client = self._build_llm_client(run_id, user_id)

        # Compile the graph with nodes closed over this run's client
        saver = self._checkpointer_mgr.saver
        builder = build_agent_graph(
            researcher=make_researcher_node(llm_client),
            analyst=make_analyst_node(llm_client),
            synthesizer=make_synthesizer_node(llm_client),
        )
        self._graph = builder.compile(checkpointer=saver)

        config = CheckpointerManager.make_config(run_id)
        initial_state = {
            "topic": topic,
            "messages": [],
            "completed_steps": [],
        }

        logger.info("Starting run=%s user=%s topic='%s'", run_id, user_id, topic)
        result = await self._graph.ainvoke(initial_state, config=config)
        logger.info(
            "Run completed: run=%s steps=%s",
            run_id,
            result.get("completed_steps", []),
        )
        return dict(result)

    async def resume(
        self,
        *,
        run_id: str,
        user_id: str,
        llm_client: LLMClient | None = None,
    ) -> dict[str, Any]:
        """Resume an interrupted run from its last checkpoint.

        LangGraph rehydrates the state from Postgres and continues
        from the next pending node — already-completed steps are skipped.

        Args:
            run_id: The run to resume (must have a prior checkpoint).
            user_id: User who owns the run.
            llm_client: Optional pre-built LLMClient.

        Returns:
            The final graph state dict.

        Raises:
            ValueError: If no checkpoint exists for *run_id*.
        """
        assert self._graph is not None, "Call setup() first."

        has_ckpt = await self._checkpointer_mgr.has_checkpoint(self._graph, run_id)
        if not has_ckpt:
            raise ValueError(f"No checkpoint found for run_id={run_id!r}")

        if llm_client is None:
            llm_client = self._build_llm_client(run_id, user_id)

        # Compile the graph with nodes closed over this run's client
        saver = self._checkpointer_mgr.saver
        builder = build_agent_graph(
            researcher=make_researcher_node(llm_client),
            analyst=make_analyst_node(llm_client),
            synthesizer=make_synthesizer_node(llm_client),
        )
        self._graph = builder.compile(checkpointer=saver)

        config = CheckpointerManager.make_config(run_id)

        logger.info("Resuming run=%s user=%s", run_id, user_id)
        result = await self._graph.ainvoke(None, config=config)
        logger.info(
            "Resume completed: run=%s steps=%s",
            run_id,
            result.get("completed_steps", []),
        )
        return dict(result)

    # -- introspection ------------------------------------------------------

    @property
    def graph(self) -> CompiledStateGraph:
        """The compiled graph (for introspection / testing)."""
        assert self._graph is not None, "Call setup() first."
        return self._graph

    @property
    def checkpointer(self) -> CheckpointerManager:
        """The checkpointer manager (for introspection / testing)."""
        return self._checkpointer_mgr

    # -- internal -----------------------------------------------------------

    def _build_llm_client(self, run_id: str, user_id: str) -> LLMClient:
        """Create an LLMClient wired with failover + cost governor."""
        governor = CostGovernor(
            run_id=run_id,
            user_id=user_id,
            settings=self._settings,
            db=self._db,
        )
        router = FailoverRouter(settings=self._settings)
        return LLMClient(
            settings=self._settings,
            router=router,
            cost_governor=governor,
        )
