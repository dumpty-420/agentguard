"""core/database.py — Async Postgres interface layer using asyncpg.

Provides a connection-pooled interface for the cost ledger (Phase 2).
All SQL lives in this module — callers work with Pydantic models only.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from config.settings import Settings, settings as default_settings
from core.schemas import CostLedgerEntry

logger = logging.getLogger(__name__)


class Database:
    """Async Postgres interface backed by an ``asyncpg`` connection pool.

    Usage::

        db = Database()
        await db.connect()
        try:
            await db.insert_ledger_entry(entry)
        finally:
            await db.close()

    Args:
        settings: Application settings (Postgres connection params).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._pool: asyncpg.Pool | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def dsn(self) -> str:
        """Build a Postgres DSN from settings."""
        import urllib.parse
        s = self._settings
        user = urllib.parse.quote_plus(s.postgres_user)
        password = urllib.parse.quote_plus(s.postgres_password)
        return (
            f"postgresql://{user}:{password}"
            f"@{s.postgres_host}:{s.postgres_port}/{s.postgres_db}"
        )

    async def connect(self) -> None:
        """Create the asyncpg connection pool."""
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(dsn=self.dsn, min_size=2, max_size=10)
        logger.info("Database pool created: %s", self._settings.postgres_db)

    async def close(self) -> None:
        """Gracefully close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed.")

    # -- cost ledger ---------------------------------------------------------

    async def insert_ledger_entry(self, entry: CostLedgerEntry) -> None:
        """Insert a single cost-ledger row.

        Args:
            entry: Validated ``CostLedgerEntry`` to persist.
        """
        assert self._pool is not None, "Database.connect() must be called first."
        await self._pool.execute(
            """
            INSERT INTO cost_ledger
                (run_id, user_id, provider, model,
                 prompt_tokens, completion_tokens, cost_usd)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            entry.run_id,
            entry.user_id,
            entry.provider,
            entry.model,
            entry.prompt_tokens,
            entry.completion_tokens,
            entry.cost_usd,
        )

    async def get_run_spend(self, run_id: str) -> Decimal:
        """Return total spend (USD) for *run_id*.

        Returns ``Decimal('0')`` if no rows exist for the run.
        """
        assert self._pool is not None, "Database.connect() must be called first."
        row: Any = await self._pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE run_id = $1",
            run_id,
        )
        return Decimal(str(row))

    async def get_user_spend(self, user_id: str) -> Decimal:
        """Return total spend (USD) for *user_id*.

        Returns ``Decimal('0')`` if no rows exist for the user.
        """
        assert self._pool is not None, "Database.connect() must be called first."
        row: Any = await self._pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE user_id = $1",
            user_id,
        )
        return Decimal(str(row))
