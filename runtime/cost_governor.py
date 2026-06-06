"""runtime/cost_governor.py — Budget enforcement with graceful degradation.

================================================================================
DEGRADATION-VS-HALT DECISION FLOW
================================================================================

The cost governor intercepts every LLM call at two points:

  ┌─────────────────────────────────────────────────────────────────────┐
  │ PRE-CHECK  (before each LLM call — called by LLMClient.complete)  │
  │                                                                     │
  │  1. Read current spend for the run  (in-memory + DB seed)          │
  │  2. Read current spend for the user (in-memory + DB seed)          │
  │                                                                     │
  │  3. HARD HALT — if EITHER:                                         │
  │       run_spend  >= run_ceiling     →  raise BudgetExceededError   │
  │       user_spend >= user_ceiling    →  raise BudgetExceededError   │
  │                                                                     │
  │  4. GRACEFUL DEGRADATION — if EITHER:                              │
  │       run_spend  >= run_ceiling  * degradation_threshold           │
  │       user_spend >= user_ceiling * degradation_threshold           │
  │     → look up the requested model in ``model_downgrade_map``       │
  │     → if a cheaper model exists, swap silently and log             │
  │     → if no downgrade is mapped, keep the original model           │
  │                                                                     │
  │  5. PASS — spend is within comfortable range; use original model   │
  │                                                                     │
  │  Returns: effective model name (str)                               │
  └─────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────┐
  │ POST-CALL  (after each LLM call — called by LLMClient.complete)   │
  │                                                                     │
  │  6. Compute cost_usd = (prompt_tokens * input_price / 1000)        │
  │                       + (completion_tokens * output_price / 1000)   │
  │  7. Update in-memory run and user spend accumulators               │
  │  8. Persist a CostLedgerEntry row via core.database.Database       │
  └─────────────────────────────────────────────────────────────────────┘

Why degrade before halting?
  An abrupt stop mid-run wastes all work already done. By downgrading to
  a cheaper model (e.g. Haiku instead of Sonnet), the run can finish at
  lower quality rather than dying. The hard halt is the last resort when
  even the cheapest model can't keep costs within the ceiling.
================================================================================
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from config.settings import Settings, settings as default_settings
from core.schemas import CostLedgerEntry, TokenUsage

if TYPE_CHECKING:
    from core.database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exception
# ---------------------------------------------------------------------------

class BudgetExceededError(Exception):
    """Raised when a run or user has exhausted their budget ceiling.

    Attributes:
        run_id: The run that triggered the halt.
        user_id: The user who owns the run.
        spend: Current accumulated spend (USD).
        ceiling: The budget ceiling that was breached.
        scope: ``"run"`` or ``"user"`` — which ceiling was hit.
    """

    def __init__(
        self,
        *,
        run_id: str,
        user_id: str,
        spend: Decimal,
        ceiling: Decimal,
        scope: str,
    ) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.spend = spend
        self.ceiling = ceiling
        self.scope = scope
        super().__init__(
            f"Budget exceeded ({scope}): spend ${spend:.6f} >= ceiling ${ceiling:.6f} "
            f"[run_id={run_id}, user_id={user_id}]"
        )


# ---------------------------------------------------------------------------
# Cost governor
# ---------------------------------------------------------------------------

class CostGovernor:
    """Per-run budget enforcement with graceful degradation.

    One ``CostGovernor`` instance is created per agent run.  It holds
    in-memory spend accumulators that are seeded from the database on the
    first ``pre_check`` call, then kept up-to-date with each
    ``record_usage`` call.

    Args:
        run_id: Unique identifier for this agent run.
        user_id: Identifier for the user who initiated the run.
        settings: Application settings (budget ceilings, price table, etc.).
        db: The async database interface for ledger persistence.
    """

    def __init__(
        self,
        *,
        run_id: str,
        user_id: str,
        settings: Settings | None = None,
        db: Database | None = None,
    ) -> None:
        self._run_id = run_id
        self._user_id = user_id
        self._settings = settings or default_settings
        self._db = db

        # In-memory spend accumulators (seeded from DB on first pre_check).
        self._run_spend: Decimal = Decimal("0")
        self._user_spend: Decimal = Decimal("0")
        self._seeded: bool = False

    # -- properties ---------------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def run_spend(self) -> Decimal:
        """Current accumulated spend for this run."""
        return self._run_spend

    @property
    def user_spend(self) -> Decimal:
        """Current accumulated spend for this user."""
        return self._user_spend

    # -- public interface ---------------------------------------------------

    async def pre_check(self, model: str) -> str:
        """Check budget and return the effective model to use.

        May downgrade *model* to a cheaper alternative when approaching
        the budget ceiling.

        Args:
            model: The model the caller intends to use.

        Returns:
            The effective model name (same as *model* or a cheaper one).

        Raises:
            BudgetExceededError: If the budget is fully exhausted.
        """
        # Seed from DB on the first call within this run.
        await self._seed_from_db()

        run_ceiling = Decimal(str(self._settings.budget_per_run_usd))
        user_ceiling = Decimal(str(self._settings.budget_per_user_usd))

        # --- HARD HALT -------------------------------------------------------
        if self._run_spend >= run_ceiling:
            raise BudgetExceededError(
                run_id=self._run_id,
                user_id=self._user_id,
                spend=self._run_spend,
                ceiling=run_ceiling,
                scope="run",
            )
        if self._user_spend >= user_ceiling:
            raise BudgetExceededError(
                run_id=self._run_id,
                user_id=self._user_id,
                spend=self._user_spend,
                ceiling=user_ceiling,
                scope="user",
            )

        # --- GRACEFUL DEGRADATION --------------------------------------------
        threshold = Decimal(str(self._settings.budget_degradation_threshold))
        run_degradation_line = run_ceiling * threshold
        user_degradation_line = user_ceiling * threshold

        if (
            self._run_spend >= run_degradation_line
            or self._user_spend >= user_degradation_line
        ):
            effective_model = self._maybe_downgrade(model)
            if effective_model != model:
                logger.warning(
                    "Cost governor: degrading model %s → %s "
                    "[run_spend=$%.6f, user_spend=$%.6f]",
                    model,
                    effective_model,
                    self._run_spend,
                    self._user_spend,
                )
            return effective_model

        # --- PASS — within comfortable range ---------------------------------
        return model

    async def record_usage(
        self,
        *,
        provider: str,
        model: str,
        usage: TokenUsage,
    ) -> Decimal:
        """Compute cost, update accumulators, and persist to the ledger.

        Args:
            provider: The provider that handled the call (e.g. ``"anthropic"``).
            model: The model that was actually used.
            usage: Token counts from the provider response.

        Returns:
            The computed cost (USD) for this call.
        """
        cost = self.compute_cost(model, usage)

        # Update in-memory accumulators.
        self._run_spend += cost
        self._user_spend += cost

        # Persist to DB.
        entry = CostLedgerEntry(
            run_id=self._run_id,
            user_id=self._user_id,
            provider=provider,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=cost,
        )
        if self._db is not None:
            try:
                await self._db.insert_ledger_entry(entry)
            except Exception:
                logger.exception(
                    "Failed to persist ledger entry for run=%s", self._run_id,
                )

        logger.info(
            "Cost recorded: provider=%s model=%s tokens=%d+%d cost=$%.8f "
            "run_total=$%.6f user_total=$%.6f",
            provider,
            model,
            usage.prompt_tokens,
            usage.completion_tokens,
            cost,
            self._run_spend,
            self._user_spend,
        )
        return cost

    # -- cost computation ---------------------------------------------------

    def compute_cost(self, model: str, usage: TokenUsage) -> Decimal:
        """Pure function: compute cost in USD from token usage and price table.

        If the model is not in the price table, returns ``Decimal('0')``
        and logs a warning (fail-open: don't crash on unknown models).

        Args:
            model: The model name to look up pricing for.
            usage: Token counts.

        Returns:
            Cost in USD as a ``Decimal``.
        """
        pricing = self._settings.model_price_table.get(model)
        if pricing is None:
            logger.warning(
                "No pricing found for model '%s'; recording $0 cost.", model,
            )
            return Decimal("0")

        input_cost = Decimal(str(pricing["input_per_1k"])) * usage.prompt_tokens / 1000
        output_cost = Decimal(str(pricing["output_per_1k"])) * usage.completion_tokens / 1000
        return input_cost + output_cost

    # -- internal helpers ---------------------------------------------------

    def _maybe_downgrade(self, model: str) -> str:
        """Return the cheaper substitute for *model*, or *model* itself."""
        return self._settings.model_downgrade_map.get(model, model)

    async def _seed_from_db(self) -> None:
        """Load historical spend from the database on first use."""
        if self._seeded:
            return
        self._seeded = True

        if self._db is None:
            return

        try:
            self._run_spend = await self._db.get_run_spend(self._run_id)
            self._user_spend = await self._db.get_user_spend(self._user_id)
            logger.info(
                "Seeded spend from DB: run=%s ($%.6f), user=%s ($%.6f)",
                self._run_id,
                self._run_spend,
                self._user_id,
                self._user_spend,
            )
        except Exception:
            logger.exception("Failed to seed spend from DB; starting from $0.")
