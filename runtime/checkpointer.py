"""runtime/checkpointer.py — Durable state management via LangGraph checkpointing.

Wraps LangGraph's ``AsyncPostgresSaver`` so agent code never touches
checkpointing internals.  The ``CheckpointerManager`` builds the saver,
provides LangGraph config dicts keyed by ``run_id`` (= ``thread_id``),
and exposes helpers to inspect / resume checkpoint state.

For testing, any LangGraph-compatible ``BaseCheckpointSaver`` (e.g.
``MemorySaver``) can be injected instead of the Postgres saver.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver

from config.settings import Settings, settings as default_settings

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


class CheckpointerManager:
    """Manages LangGraph state persistence to Postgres.

    One ``CheckpointerManager`` is typically created per application
    lifetime.  It holds the checkpointer saver and provides helpers
    that the orchestrator uses to compile graphs and build configs.

    Args:
        settings: Application settings (Postgres connection params).
        saver_override: Optional pre-built saver for testing (e.g.
            ``MemorySaver``).  When provided, Postgres is bypassed.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        saver_override: BaseCheckpointSaver | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._saver: BaseCheckpointSaver | None = saver_override
        self._owns_saver = saver_override is None
        self._context_manager: Any = None

    # -- DSN ----------------------------------------------------------------

    @property
    def dsn(self) -> str:
        """Build a Postgres DSN (psycopg v3 format) from settings.

        Note: ``AsyncPostgresSaver`` uses ``psycopg`` (v3), which accepts
        the standard ``postgresql://`` URI format.
        """
        import urllib.parse
        s = self._settings
        user = urllib.parse.quote_plus(s.postgres_user)
        password = urllib.parse.quote_plus(s.postgres_password)
        return (
            f"postgresql://{user}:{password}"
            f"@{s.postgres_host}:{s.postgres_port}/{s.postgres_db}"
        )

    # -- lifecycle ----------------------------------------------------------

    async def setup(self) -> BaseCheckpointSaver:
        """Initialize the checkpointer saver.

        If no ``saver_override`` was provided at construction, this creates
        an ``AsyncPostgresSaver`` from the Postgres DSN and calls
        ``.setup()`` to ensure the checkpoint tables exist.

        Returns:
            The ready-to-use saver instance.
        """
        if self._saver is not None:
            return self._saver

        # Lazy import to avoid hard dependency when testing with MemorySaver.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        self._context_manager = AsyncPostgresSaver.from_conn_string(self.dsn)
        saver = await self._context_manager.__aenter__()
        await saver.setup()
        self._saver = saver
        logger.info("AsyncPostgresSaver initialized and tables ensured.")
        return saver

    @property
    def saver(self) -> BaseCheckpointSaver:
        """Return the current saver.  Raises if ``setup()`` has not been called."""
        if self._saver is None:
            raise RuntimeError(
                "CheckpointerManager.setup() must be called before accessing the saver."
            )
        return self._saver

    async def close(self) -> None:
        """Close the saver's connection pool (Postgres savers only)."""
        if self._context_manager is not None:
            await self._context_manager.__aexit__(None, None, None)
            self._context_manager = None
            self._saver = None
            logger.info("Checkpointer saver closed.")
        elif self._saver is not None and self._owns_saver:
            # AsyncPostgresSaver has an async close/aclose method.
            close_fn = getattr(self._saver, "close", None) or getattr(
                self._saver, "aclose", None
            )
            if close_fn is not None:
                await close_fn()
            self._saver = None
            logger.info("Checkpointer saver closed.")

    # -- config helpers -----------------------------------------------------

    @staticmethod
    def make_config(run_id: str) -> dict[str, Any]:
        """Build the LangGraph ``config`` dict for a given run.

        LangGraph uses ``thread_id`` to identify a conversation / run.
        We map ``run_id`` directly to ``thread_id``.

        Args:
            run_id: The unique identifier for this agent run.

        Returns:
            A config dict suitable for ``graph.ainvoke(..., config=...)``.
        """
        return {"configurable": {"thread_id": run_id}}

    # -- state introspection ------------------------------------------------

    async def has_checkpoint(
        self,
        graph: CompiledStateGraph,
        run_id: str,
    ) -> bool:
        """Check whether a checkpoint exists for *run_id*.

        Args:
            graph: A compiled LangGraph graph (with checkpointer attached).
            run_id: The run to look up.

        Returns:
            ``True`` if at least one checkpoint exists.
        """
        config = self.make_config(run_id)
        state = await graph.aget_state(config)
        # A state with no values (empty dict) and no 'next' means no checkpoint.
        return bool(state.values)

    async def get_state(
        self,
        graph: CompiledStateGraph,
        run_id: str,
    ) -> dict[str, Any]:
        """Retrieve the latest checkpoint state for *run_id*.

        Args:
            graph: A compiled LangGraph graph (with checkpointer attached).
            run_id: The run to look up.

        Returns:
            The state values dict, or an empty dict if no checkpoint exists.
        """
        config = self.make_config(run_id)
        state = await graph.aget_state(config)
        return dict(state.values) if state.values else {}

    async def get_next_steps(
        self,
        graph: CompiledStateGraph,
        run_id: str,
    ) -> tuple[str, ...]:
        """Return the next node(s) scheduled for execution for *run_id*.

        This is useful for introspecting where a run was interrupted.

        Returns:
            A tuple of node names, or an empty tuple if the run is complete.
        """
        config = self.make_config(run_id)
        state = await graph.aget_state(config)
        return state.next if state.next else ()
