"""tests/test_cost_governor.py — Tests for the Phase 2 cost-governance layer.

Covers:
  (a) Spend is tracked and summed correctly per run and per user
  (b) Crossing the degradation threshold downgrades the model
  (c) Exhausting the budget raises BudgetExceededError
  (d) A ledger entry is written per call

All database and provider calls are mocked — no real Postgres or real APIs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings
from core.schemas import CostLedgerEntry, TokenUsage
from runtime.cost_governor import BudgetExceededError, CostGovernor


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
        # Phase 2 — tight ceilings for easy testing.
        "budget_per_run_usd": 1.0,
        "budget_per_user_usd": 5.0,
        "budget_degradation_threshold": 0.8,
        "model_price_table": {
            "claude-sonnet-4-20250514": {
                "input_per_1k": 0.003,
                "output_per_1k": 0.015,
            },
            "claude-3-5-haiku-20241022": {
                "input_per_1k": 0.00025,
                "output_per_1k": 0.00125,
            },
            "gpt-4o": {
                "input_per_1k": 0.0025,
                "output_per_1k": 0.01,
            },
            "gpt-4o-mini": {
                "input_per_1k": 0.00015,
                "output_per_1k": 0.0006,
            },
        },
        "model_downgrade_map": {
            "claude-sonnet-4-20250514": "claude-3-5-haiku-20241022",
            "gpt-4o": "gpt-4o-mini",
        },
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "testdb",
        "postgres_user": "testuser",
        "postgres_password": "testpass",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_governor(
    *,
    run_id: str = "run-1",
    user_id: str = "user-1",
    db: Any = None,
    **settings_overrides: Any,
) -> CostGovernor:
    """Create a ``CostGovernor`` with a mock DB and test settings."""
    settings = _make_settings(**settings_overrides)
    if db is None:
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()
    return CostGovernor(
        run_id=run_id,
        user_id=user_id,
        settings=settings,
        db=db,
    )


USAGE_SMALL = TokenUsage(prompt_tokens=100, completion_tokens=50)
USAGE_LARGE = TokenUsage(prompt_tokens=10_000, completion_tokens=5_000)


# ===========================================================================
# (a) Spend tracking — accumulated correctly per run and per user
# ===========================================================================

class TestSpendTracking:
    """Assert that record_usage accumulates spend correctly."""

    @pytest.mark.asyncio
    async def test_spend_accumulates_per_run(self) -> None:
        """Multiple record_usage calls sum up for the run."""
        gov = _make_governor()

        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=USAGE_SMALL,
        )
        first_spend = gov.run_spend

        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=USAGE_SMALL,
        )
        assert gov.run_spend == first_spend * 2
        assert gov.run_spend > Decimal("0")

    @pytest.mark.asyncio
    async def test_spend_accumulates_per_user(self) -> None:
        """User spend tracks alongside run spend."""
        gov = _make_governor()

        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=USAGE_SMALL,
        )

        assert gov.user_spend == gov.run_spend
        assert gov.user_spend > Decimal("0")

    @pytest.mark.asyncio
    async def test_compute_cost_with_known_model(self) -> None:
        """Cost is computed correctly from the price table."""
        gov = _make_governor()
        # claude-sonnet-4-20250514: input $0.003/1K, output $0.015/1K
        # 1000 prompt tokens + 500 completion tokens
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500)
        cost = gov.compute_cost("claude-sonnet-4-20250514", usage)
        # (1000 * 0.003 / 1000) + (500 * 0.015 / 1000) = 0.003 + 0.0075 = 0.0105
        assert cost == Decimal("0.003") + Decimal("0.0075")

    @pytest.mark.asyncio
    async def test_compute_cost_with_unknown_model(self) -> None:
        """An unknown model returns $0 cost (fail-open)."""
        gov = _make_governor()
        cost = gov.compute_cost("unknown-model-v9", USAGE_SMALL)
        assert cost == Decimal("0")

    @pytest.mark.asyncio
    async def test_record_usage_returns_cost(self) -> None:
        """record_usage returns the computed cost for the call."""
        gov = _make_governor()
        cost = await gov.record_usage(
            provider="openai",
            model="gpt-4o",
            usage=TokenUsage(prompt_tokens=1000, completion_tokens=1000),
        )
        # gpt-4o: (1000 * 0.0025/1000) + (1000 * 0.01/1000) = 0.0025 + 0.01
        expected = Decimal("0.0025") + Decimal("0.01")
        assert cost == expected

    @pytest.mark.asyncio
    async def test_seeded_from_db_on_first_pre_check(self) -> None:
        """The governor seeds in-memory spend from the DB on the first pre_check."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0.50"))
        db.get_user_spend = AsyncMock(return_value=Decimal("2.00"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        # Before pre_check, spend is zero.
        assert gov.run_spend == Decimal("0")

        await gov.pre_check("claude-sonnet-4-20250514")

        # After pre_check, spend is seeded from DB.
        assert gov.run_spend == Decimal("0.50")
        assert gov.user_spend == Decimal("2.00")
        db.get_run_spend.assert_awaited_once_with("run-1")
        db.get_user_spend.assert_awaited_once_with("user-1")

    @pytest.mark.asyncio
    async def test_db_seed_happens_only_once(self) -> None:
        """Subsequent pre_check calls do NOT re-query the DB."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))

        gov = _make_governor(db=db)

        await gov.pre_check("gpt-4o")
        await gov.pre_check("gpt-4o")

        # Only called once despite two pre_check invocations.
        assert db.get_run_spend.await_count == 1
        assert db.get_user_spend.await_count == 1


# ===========================================================================
# (b) Crossing the degradation threshold downgrades the model
# ===========================================================================

class TestGracefulDegradation:
    """Assert that the cost governor downgrades to a cheaper model."""

    @pytest.mark.asyncio
    async def test_degrades_when_run_spend_crosses_threshold(self) -> None:
        """When run spend >= 80% of run ceiling, the model is downgraded."""
        # Run ceiling = $1.00, threshold = 0.8 → degrade at $0.80.
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0.85"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)
        effective = await gov.pre_check("claude-sonnet-4-20250514")

        assert effective == "claude-3-5-haiku-20241022"

    @pytest.mark.asyncio
    async def test_degrades_when_user_spend_crosses_threshold(self) -> None:
        """When user spend >= 80% of user ceiling, the model is downgraded."""
        # User ceiling = $5.00, threshold = 0.8 → degrade at $4.00.
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("4.50"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)
        effective = await gov.pre_check("gpt-4o")

        assert effective == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_degradation_below_threshold(self) -> None:
        """When spend is below the threshold, the original model is returned."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0.10"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0.50"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)
        effective = await gov.pre_check("claude-sonnet-4-20250514")

        assert effective == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_model_without_downgrade_keeps_original(self) -> None:
        """If no downgrade is mapped for a model, it stays unchanged even past threshold."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0.85"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)
        # gpt-4o-mini is already the cheapest — no mapping in downgrade_map.
        effective = await gov.pre_check("gpt-4o-mini")

        assert effective == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_degradation_at_exact_threshold(self) -> None:
        """Degradation triggers at exactly 80% of the ceiling."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0.80"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)
        effective = await gov.pre_check("claude-sonnet-4-20250514")

        assert effective == "claude-3-5-haiku-20241022"


# ===========================================================================
# (c) Exhausting the budget raises BudgetExceededError
# ===========================================================================

class TestBudgetExhausted:
    """Assert that BudgetExceededError is raised at the ceiling."""

    @pytest.mark.asyncio
    async def test_hard_halt_on_run_ceiling(self) -> None:
        """pre_check raises BudgetExceededError when run spend >= run ceiling."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("1.00"))
        db.get_user_spend = AsyncMock(return_value=Decimal("1.00"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        with pytest.raises(BudgetExceededError) as exc_info:
            await gov.pre_check("claude-sonnet-4-20250514")

        err = exc_info.value
        assert err.scope == "run"
        assert err.spend == Decimal("1.00")
        assert err.ceiling == Decimal("1.0")
        assert err.run_id == "run-1"
        assert err.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_hard_halt_on_user_ceiling(self) -> None:
        """pre_check raises BudgetExceededError when user spend >= user ceiling."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("5.00"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        with pytest.raises(BudgetExceededError) as exc_info:
            await gov.pre_check("gpt-4o")

        assert exc_info.value.scope == "user"

    @pytest.mark.asyncio
    async def test_hard_halt_over_ceiling(self) -> None:
        """pre_check raises even when spend exceeds ceiling (not just equals)."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("2.00"))  # way over $1.00
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        with pytest.raises(BudgetExceededError):
            await gov.pre_check("gpt-4o")

    @pytest.mark.asyncio
    async def test_hard_halt_after_accumulation(self) -> None:
        """Budget is exhausted after several record_usage calls push spend over the ceiling."""
        gov = _make_governor(budget_per_run_usd=0.10)

        # Seed the governor (first pre_check queries the mock DB → $0).
        effective = await gov.pre_check("claude-sonnet-4-20250514")
        assert effective == "claude-sonnet-4-20250514"

        # Record usage that puts us near the ceiling.
        # claude-sonnet-4-20250514: (5000*0.003/1000)+(2000*0.015/1000) = $0.045
        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=TokenUsage(prompt_tokens=5000, completion_tokens=2000),
        )
        assert gov.run_spend == Decimal("0.045")

        # Second usage pushes total to $0.090 → still below $0.10 ceiling
        # but above 80% degradation threshold ($0.08).
        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=TokenUsage(prompt_tokens=5000, completion_tokens=2000),
        )
        assert gov.run_spend == Decimal("0.090")

        # Third usage pushes total to $0.135 → over $0.10 ceiling.
        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=TokenUsage(prompt_tokens=5000, completion_tokens=2000),
        )
        assert gov.run_spend == Decimal("0.135")

        with pytest.raises(BudgetExceededError) as exc_info:
            await gov.pre_check("claude-sonnet-4-20250514")

        assert exc_info.value.scope == "run"

    @pytest.mark.asyncio
    async def test_error_message_includes_details(self) -> None:
        """BudgetExceededError string contains run_id, user_id, spend, ceiling."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("1.50"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        with pytest.raises(BudgetExceededError, match="run_id=run-1"):
            await gov.pre_check("gpt-4o")


# ===========================================================================
# (d) A ledger entry is written per call
# ===========================================================================

class TestLedgerPersistence:
    """Assert that record_usage persists a CostLedgerEntry to the DB."""

    @pytest.mark.asyncio
    async def test_ledger_entry_written(self) -> None:
        """record_usage calls db.insert_ledger_entry with correct data."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        usage = TokenUsage(prompt_tokens=500, completion_tokens=200)
        await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=usage,
        )

        db.insert_ledger_entry.assert_awaited_once()
        entry: CostLedgerEntry = db.insert_ledger_entry.call_args[0][0]

        assert entry.run_id == "run-1"
        assert entry.user_id == "user-1"
        assert entry.provider == "anthropic"
        assert entry.model == "claude-sonnet-4-20250514"
        assert entry.prompt_tokens == 500
        assert entry.completion_tokens == 200
        assert entry.cost_usd > Decimal("0")

    @pytest.mark.asyncio
    async def test_ledger_entry_cost_matches_compute(self) -> None:
        """The cost in the ledger entry matches compute_cost."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500)
        expected_cost = gov.compute_cost("gpt-4o", usage)

        await gov.record_usage(
            provider="openai",
            model="gpt-4o",
            usage=usage,
        )

        entry: CostLedgerEntry = db.insert_ledger_entry.call_args[0][0]
        assert entry.cost_usd == expected_cost

    @pytest.mark.asyncio
    async def test_multiple_calls_write_multiple_entries(self) -> None:
        """Each record_usage call writes exactly one ledger entry."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock()

        gov = _make_governor(db=db)

        for _ in range(3):
            await gov.record_usage(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                usage=USAGE_SMALL,
            )

        assert db.insert_ledger_entry.await_count == 3

    @pytest.mark.asyncio
    async def test_db_failure_does_not_crash(self) -> None:
        """If the DB insert fails, record_usage logs but does not raise."""
        db = AsyncMock()
        db.get_run_spend = AsyncMock(return_value=Decimal("0"))
        db.get_user_spend = AsyncMock(return_value=Decimal("0"))
        db.insert_ledger_entry = AsyncMock(side_effect=ConnectionError("DB down"))

        gov = _make_governor(db=db)

        # Should not raise — the governor logs the error and continues.
        cost = await gov.record_usage(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            usage=USAGE_SMALL,
        )
        assert cost > Decimal("0")
        # In-memory accumulators are still updated.
        assert gov.run_spend == cost

    @pytest.mark.asyncio
    async def test_no_db_is_fine(self) -> None:
        """A governor with db=None works — it just doesn't persist."""
        gov = CostGovernor(
            run_id="run-x",
            user_id="user-x",
            settings=_make_settings(),
            db=None,
        )

        effective = await gov.pre_check("gpt-4o")
        assert effective == "gpt-4o"

        cost = await gov.record_usage(
            provider="openai",
            model="gpt-4o",
            usage=USAGE_SMALL,
        )
        assert cost > Decimal("0")
        assert gov.run_spend == cost
