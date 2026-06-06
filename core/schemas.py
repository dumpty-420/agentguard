"""core/schemas.py — Pydantic models shared across AgentGuard.

Phase 2 adds the cost-governance models. New phases append to this file
rather than creating separate schema files.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Phase 2: Cost Governance
# ---------------------------------------------------------------------------

class TokenUsage(BaseModel):
    """Token counts returned by an LLM provider for a single call."""

    prompt_tokens: int = Field(ge=0, description="Number of input / prompt tokens.")
    completion_tokens: int = Field(ge=0, description="Number of output / completion tokens.")

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (prompt + completion)."""
        return self.prompt_tokens + self.completion_tokens


class CostLedgerEntry(BaseModel):
    """A single row in the ``cost_ledger`` table.

    Created by the cost governor after every metered LLM call and
    persisted via ``core.database.Database.insert_ledger_entry``.
    """

    run_id: str = Field(description="Unique identifier for the agent run.")
    user_id: str = Field(description="Identifier for the user who initiated the run.")
    provider: str = Field(description="LLM provider name (e.g. 'anthropic').")
    model: str = Field(description="Model name used for this call.")
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(
        ge=0,
        decimal_places=8,
        description="Computed cost in USD for this call.",
    )
