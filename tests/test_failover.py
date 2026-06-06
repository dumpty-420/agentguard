"""tests/test_failover.py — Tests for the provider failover layer.

Covers:
  (a) Retry with exponential backoff
  (b) Circuit breaker trips to OPEN after the failure threshold
  (c) Failover to the next healthy provider
  (d) Circuit breaker transitions to HALF-OPEN after cooldown
  (e) HALF-OPEN single-probe gating
  (f) Non-retryable errors skip retries

All provider SDK calls are mocked — no real API calls are made.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from config.settings import Settings
from runtime.failover import CircuitBreaker, CircuitState, FailoverRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides: Any) -> Settings:
    """Create a ``Settings`` instance with test-friendly defaults."""
    defaults = {
        "anthropic_api_key": "test-key-anthropic",
        "openai_api_key": "test-key-openai",
        "provider_priority": ["anthropic", "openai"],
        "provider_timeout_seconds": 5.0,
        "provider_max_retries": 2,
        "circuit_breaker_failure_threshold": 3,
        "circuit_breaker_cooldown_seconds": 10.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


MESSAGES = [{"role": "user", "content": "Hello"}]


# ===========================================================================
# (a) Retry with exponential backoff
# ===========================================================================

class TestRetryWithBackoff:
    """Assert that a failing provider is retried with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retries_correct_number_of_times(self) -> None:
        """The provider fn should be called 1 + max_retries times."""
        settings = _make_settings(provider_max_retries=2)
        router = FailoverRouter(settings=settings)

        failing_fn = AsyncMock(side_effect=RuntimeError("boom"))
        success_fn = AsyncMock(return_value="ok from openai")

        router.register("anthropic", failing_fn)
        router.register("openai", success_fn)

        result = await router.call(MESSAGES)

        assert result == "ok from openai"
        # 1 initial + 2 retries = 3 calls
        assert failing_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_delays_are_exponential(self) -> None:
        """asyncio.sleep should be called with 2^attempt seconds."""
        settings = _make_settings(provider_max_retries=3)
        router = FailoverRouter(settings=settings)

        failing_fn = AsyncMock(side_effect=RuntimeError("boom"))
        success_fn = AsyncMock(return_value="fallback")

        router.register("anthropic", failing_fn)
        router.register("openai", success_fn)

        with patch("runtime.failover.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("runtime.failover.random.uniform", return_value=0):
            await router.call(MESSAGES)

        # Retries 0, 1, 2 → backoff 1, 2, 4
        expected_delays = [1, 2, 4]
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == expected_delays

    @pytest.mark.asyncio
    async def test_success_on_retry_does_not_failover(self) -> None:
        """If a provider succeeds on a retry, the next provider is never called."""
        settings = _make_settings(provider_max_retries=2)
        router = FailoverRouter(settings=settings)

        # Fail once, then succeed.
        anthropic_fn = AsyncMock(
            side_effect=[RuntimeError("transient"), "hello from anthropic"]
        )
        openai_fn = AsyncMock(return_value="should not be called")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        with patch("runtime.failover.asyncio.sleep", new_callable=AsyncMock):
            result = await router.call(MESSAGES)

        assert result == "hello from anthropic"
        assert anthropic_fn.call_count == 2
        openai_fn.assert_not_called()


# ===========================================================================
# (b) Circuit breaker opens after the failure threshold
# ===========================================================================

class TestCircuitBreakerOpens:
    """Assert that the circuit trips to OPEN after N consecutive failures."""

    def test_breaker_starts_closed(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=3, cooldown_seconds=10)
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_breaker_opens_at_threshold(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=3, cooldown_seconds=10)

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_success_resets_failure_count(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=3, cooldown_seconds=10)

        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # reset

        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_circuit_opens_after_exhausting_retries(self) -> None:
        """End-to-end: exhausting retries records a failure on the breaker."""
        settings = _make_settings(
            provider_max_retries=0,  # No retries → 1 attempt
            circuit_breaker_failure_threshold=2,
        )
        router = FailoverRouter(settings=settings)

        failing_fn = AsyncMock(side_effect=RuntimeError("down"))
        fallback_fn = AsyncMock(return_value="ok")

        router.register("anthropic", failing_fn)
        router.register("openai", fallback_fn)

        # First call — one failure recorded, circuit still CLOSED.
        await router.call(MESSAGES)
        breaker = router.get_breaker("anthropic")
        assert breaker.state == CircuitState.CLOSED
        assert breaker.consecutive_failures == 1

        # Second call — second failure, circuit trips to OPEN.
        await router.call(MESSAGES)
        assert breaker.state == CircuitState.OPEN


# ===========================================================================
# (c) Failover to the next healthy provider
# ===========================================================================

class TestFailover:
    """Assert that requests fail over to the next provider."""

    @pytest.mark.asyncio
    async def test_failover_on_provider_error(self) -> None:
        settings = _make_settings(provider_max_retries=0)
        router = FailoverRouter(settings=settings)

        anthropic_fn = AsyncMock(side_effect=RuntimeError("anthropic down"))
        openai_fn = AsyncMock(return_value="openai response")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        result = await router.call(MESSAGES)

        assert result == "openai response"
        anthropic_fn.assert_called_once()
        openai_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_open_circuit_provider(self) -> None:
        """A provider with an OPEN circuit is skipped entirely."""
        settings = _make_settings(provider_max_retries=0)
        router = FailoverRouter(settings=settings)

        anthropic_fn = AsyncMock(return_value="should not be called")
        openai_fn = AsyncMock(return_value="openai response")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        # Manually trip anthropic's breaker.
        breaker = router.get_breaker("anthropic")
        for _ in range(settings.circuit_breaker_failure_threshold):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        result = await router.call(MESSAGES)

        assert result == "openai response"
        anthropic_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_providers_exhausted_raises(self) -> None:
        """RuntimeError is raised when every provider fails."""
        settings = _make_settings(provider_max_retries=0)
        router = FailoverRouter(settings=settings)

        router.register("anthropic", AsyncMock(side_effect=RuntimeError("a")))
        router.register("openai", AsyncMock(side_effect=RuntimeError("b")))

        with pytest.raises(RuntimeError, match="All providers exhausted"):
            await router.call(MESSAGES)


# ===========================================================================
# (d) Circuit breaker half-opens after cooldown
# ===========================================================================

class TestCircuitBreakerHalfOpen:
    """Assert the OPEN → HALF-OPEN transition and recovery behaviour."""

    def test_half_open_after_cooldown(self) -> None:
        """After the cooldown elapses, allow_request() transitions to HALF-OPEN."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        # Trip the breaker.
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate time passing beyond the cooldown.
        assert cb.last_failure_time is not None
        cb.last_failure_time -= 6  # shift 6 seconds into the past

        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_resets_to_closed(self) -> None:
        """A successful probe in HALF-OPEN resets to CLOSED."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        cb.record_failure()
        cb.record_failure()
        cb.last_failure_time -= 6  # expire cooldown
        cb.allow_request()  # → HALF-OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    def test_half_open_failure_returns_to_open(self) -> None:
        """A failed probe in HALF-OPEN returns to OPEN."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        cb.record_failure()
        cb.record_failure()
        cb.last_failure_time -= 6
        cb.allow_request()  # → HALF-OPEN

        cb.record_failure()  # probe fails
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_half_open_probe_succeeds_end_to_end(self) -> None:
        """After cooldown, the router probes the recovered provider."""
        settings = _make_settings(
            provider_max_retries=0,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_cooldown_seconds=1.0,
        )
        router = FailoverRouter(settings=settings)

        call_count = 0

        async def anthropic_fn(messages: list, **kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call fails")
            return "anthropic recovered"

        openai_fn = AsyncMock(return_value="openai fallback")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        # 1st call: anthropic fails → failover to openai.
        result = await router.call(MESSAGES)
        assert result == "openai fallback"
        assert router.get_breaker("anthropic").state == CircuitState.OPEN

        # Simulate cooldown elapsed.
        router.get_breaker("anthropic").last_failure_time -= 2

        # 2nd call: anthropic should be probed (HALF-OPEN) and succeed.
        result = await router.call(MESSAGES)
        assert result == "anthropic recovered"
        assert router.get_breaker("anthropic").state == CircuitState.CLOSED


# ===========================================================================
# Health introspection
# ===========================================================================

class TestHealthIntrospection:
    """Assert the get_health() snapshot is accurate."""

    def test_health_snapshot(self) -> None:
        settings = _make_settings()
        router = FailoverRouter(settings=settings)

        health = router.get_health()

        assert set(health.keys()) == {"anthropic", "openai"}
        for provider_health in health.values():
            assert provider_health["state"] == "closed"
            assert provider_health["consecutive_failures"] == 0
            assert provider_health["last_failure_time"] is None


# ===========================================================================
# (e) HALF-OPEN single-probe gating
# ===========================================================================

class TestHalfOpenSingleProbe:
    """Assert that HALF_OPEN allows only a single in-flight probe request."""

    def test_second_concurrent_request_is_blocked(self) -> None:
        """Only one probe is allowed through while _probe_in_flight is set."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        # Trip the breaker and expire the cooldown.
        cb.record_failure()
        cb.record_failure()
        assert cb.last_failure_time is not None
        cb.last_failure_time -= 6

        # First request transitions to HALF_OPEN and is allowed.
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Second concurrent request is blocked.
        assert cb.allow_request() is False

    def test_probe_flag_cleared_on_success(self) -> None:
        """After a successful probe, new requests flow through (CLOSED)."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        cb.record_failure()
        cb.record_failure()
        cb.last_failure_time -= 6
        cb.allow_request()  # → HALF_OPEN, probe in flight

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._probe_in_flight is False
        assert cb.allow_request() is True

    def test_probe_flag_cleared_on_failure(self) -> None:
        """After a failed probe, the flag is cleared (breaker → OPEN)."""
        cb = CircuitBreaker(provider="test", failure_threshold=2, cooldown_seconds=5)

        cb.record_failure()
        cb.record_failure()
        cb.last_failure_time -= 6
        cb.allow_request()  # → HALF_OPEN, probe in flight

        cb.record_failure()  # probe fails
        assert cb.state == CircuitState.OPEN
        assert cb._probe_in_flight is False

    @pytest.mark.asyncio
    async def test_concurrent_half_open_only_one_probe_e2e(self) -> None:
        """When two requests race during HALF_OPEN, only one probes the provider."""
        settings = _make_settings(
            provider_max_retries=0,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_cooldown_seconds=1.0,
        )
        router = FailoverRouter(settings=settings)

        probe_started = asyncio.Event()
        probe_release = asyncio.Event()
        probe_count = 0

        async def slow_anthropic(messages: list, **kwargs: Any) -> str:
            nonlocal probe_count
            probe_count += 1
            probe_started.set()
            await probe_release.wait()
            return "anthropic recovered"

        openai_fn = AsyncMock(return_value="openai fallback")

        router.register("anthropic", slow_anthropic)
        router.register("openai", openai_fn)

        # Trip the breaker and expire cooldown.
        breaker = router.get_breaker("anthropic")
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        breaker.last_failure_time -= 2

        # Launch two concurrent calls.
        task1 = asyncio.create_task(router.call(MESSAGES))
        await probe_started.wait()  # task1 has entered slow_anthropic
        task2 = asyncio.create_task(router.call(MESSAGES))
        await asyncio.sleep(0)  # let task2 run past allow_request()

        # Release the blocking probe.
        probe_release.set()

        result1 = await task1
        result2 = await task2

        # task1 probed anthropic; task2 fell through to openai.
        assert result1 == "anthropic recovered"
        assert result2 == "openai fallback"
        assert probe_count == 1


# ===========================================================================
# (f) Non-retryable errors skip retries
# ===========================================================================

class _HttpError(Exception):
    """Minimal exception carrying a status_code, used by retry tests."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class TestNonRetryableErrors:
    """Assert that non-retryable errors (4xx except 429) skip retries."""

    @pytest.mark.asyncio
    async def test_400_does_not_retry(self) -> None:
        """A 400 Bad Request should fail immediately without retries."""
        settings = _make_settings(provider_max_retries=3)
        router = FailoverRouter(settings=settings)

        anthropic_fn = AsyncMock(side_effect=_HttpError(400))
        openai_fn = AsyncMock(return_value="openai ok")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        with patch("runtime.failover.asyncio.sleep", new_callable=AsyncMock):
            result = await router.call(MESSAGES)

        assert result == "openai ok"
        # Only 1 call — no retries for non-retryable 400.
        assert anthropic_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_429_is_still_retried(self) -> None:
        """429 Too Many Requests IS retryable and should be retried."""
        settings = _make_settings(provider_max_retries=2)
        router = FailoverRouter(settings=settings)

        anthropic_fn = AsyncMock(side_effect=_HttpError(429))
        openai_fn = AsyncMock(return_value="openai ok")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        with patch("runtime.failover.asyncio.sleep", new_callable=AsyncMock), \
             patch("runtime.failover.random.uniform", return_value=0):
            result = await router.call(MESSAGES)

        assert result == "openai ok"
        # 1 initial + 2 retries = 3 calls.
        assert anthropic_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_500_is_retried(self) -> None:
        """Server errors (5xx) should still be retried normally."""
        settings = _make_settings(provider_max_retries=2)
        router = FailoverRouter(settings=settings)

        anthropic_fn = AsyncMock(side_effect=_HttpError(500))
        openai_fn = AsyncMock(return_value="openai ok")

        router.register("anthropic", anthropic_fn)
        router.register("openai", openai_fn)

        with patch("runtime.failover.asyncio.sleep", new_callable=AsyncMock), \
             patch("runtime.failover.random.uniform", return_value=0):
            result = await router.call(MESSAGES)

        assert result == "openai ok"
        assert anthropic_fn.call_count == 3

    def test_is_retryable_helper_directly(self) -> None:
        """Unit-test the _is_retryable static method for various status codes."""
        assert FailoverRouter._is_retryable(_HttpError(400)) is False
        assert FailoverRouter._is_retryable(_HttpError(401)) is False
        assert FailoverRouter._is_retryable(_HttpError(403)) is False
        assert FailoverRouter._is_retryable(_HttpError(404)) is False
        assert FailoverRouter._is_retryable(_HttpError(422)) is False
        assert FailoverRouter._is_retryable(_HttpError(429)) is True   # rate-limited
        assert FailoverRouter._is_retryable(_HttpError(500)) is True
        assert FailoverRouter._is_retryable(_HttpError(503)) is True
        assert FailoverRouter._is_retryable(RuntimeError("generic")) is True
