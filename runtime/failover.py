"""runtime/failover.py — Provider failover with retry, circuit breaking, and routing.

================================================================================
CIRCUIT BREAKER STATE MACHINE
================================================================================

Each provider has an independent circuit breaker with three states:

    ┌────────┐  N consecutive   ┌────────┐  cooldown   ┌───────────┐
    │ CLOSED │ ──  failures  ──▶│  OPEN  │ ── expires ─▶│ HALF-OPEN │
    └────────┘                  └────────┘              └───────────┘
         ▲                           ▲                       │
         │                           │                       │
         │   success                 │  failure              │
         └───────────────────────────┴───────────────────────┘

CLOSED   — The provider is considered healthy. Requests flow through normally.
           Every failure increments a consecutive-failure counter; a success
           resets it to zero. When the counter reaches the configured threshold,
           the breaker trips to OPEN.

OPEN     — The provider is considered unhealthy and ALL requests are
           immediately skipped (fail-fast). After a cooldown window elapses
           the breaker moves to HALF-OPEN automatically.

HALF-OPEN — One probe request is allowed through to test recovery.
            • If it succeeds → the breaker resets to CLOSED.
            • If it fails   → the breaker returns to OPEN and the cooldown
              window restarts.

The failover router walks the provider priority list and skips any provider
whose breaker is OPEN. If a provider call fails after exhausting retries,
the router advances to the next healthy provider in the list.
================================================================================
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from config.settings import Settings, settings as default_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit breaker states
# ---------------------------------------------------------------------------

class CircuitState(str, enum.Enum):
    """Possible states for a provider circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


# ---------------------------------------------------------------------------
# Per-provider circuit breaker
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreaker:
    """Tracks health state for a single LLM provider.

    Attributes:
        provider: The provider name (e.g. ``"anthropic"``).
        failure_threshold: Consecutive failures required to trip.
        cooldown_seconds: Seconds the breaker stays OPEN before half-opening.
    """

    provider: str
    failure_threshold: int
    cooldown_seconds: float

    # Internal mutable state ------------------------------------------------
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    consecutive_failures: int = field(default=0, init=False)
    last_failure_time: float | None = field(default=None, init=False)
    _probe_in_flight: bool = field(default=False, init=False)

    # -- public interface ---------------------------------------------------

    def record_success(self) -> None:
        """Record a successful call — resets the breaker to CLOSED."""
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time = None
        self._probe_in_flight = False
        logger.info("Circuit breaker [%s] → CLOSED (success)", self.provider)

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker to OPEN."""
        self.consecutive_failures += 1
        self.last_failure_time = time.monotonic()
        self._probe_in_flight = False

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker [%s] → OPEN after %d consecutive failures",
                self.provider,
                self.consecutive_failures,
            )

    def allow_request(self) -> bool:
        """Return ``True`` if a request is allowed through the breaker.

        • CLOSED     → always allowed
        • HALF-OPEN  → allowed (single probe)
        • OPEN       → allowed only once the cooldown has elapsed,
                        at which point the state transitions to HALF-OPEN.
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.HALF_OPEN:
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

        # OPEN — check if cooldown expired
        assert self.last_failure_time is not None
        elapsed = time.monotonic() - self.last_failure_time
        if elapsed >= self.cooldown_seconds:
            self.state = CircuitState.HALF_OPEN
            self._probe_in_flight = True
            logger.info(
                "Circuit breaker [%s] → HALF-OPEN (cooldown elapsed after %.1fs)",
                self.provider,
                elapsed,
            )
            return True

        return False


# ---------------------------------------------------------------------------
# Provider call function type
# ---------------------------------------------------------------------------

# A provider call is an async callable that takes a list of messages and
# returns the model response.  The failover router is agnostic to the
# payload shape — the ``LLMClient`` layer prepares provider-specific args.
ProviderCallFn = Callable[..., Coroutine[Any, Any, Any]]


# ---------------------------------------------------------------------------
# Failover router
# ---------------------------------------------------------------------------

class FailoverRouter:
    """Routes LLM requests across providers with retry, circuit breaking,
    and automatic failover.

    Args:
        settings: Application settings (provider list, retry config, etc.).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._breakers: dict[str, CircuitBreaker] = {
            provider: CircuitBreaker(
                provider=provider,
                failure_threshold=self._settings.circuit_breaker_failure_threshold,
                cooldown_seconds=self._settings.circuit_breaker_cooldown_seconds,
            )
            for provider in self._settings.provider_priority
        }
        # Registry of async call functions per provider, set via register().
        self._provider_fns: dict[str, ProviderCallFn] = {}

    # -- registration -------------------------------------------------------

    def register(self, provider: str, fn: ProviderCallFn) -> None:
        """Register the async callable for a provider.

        Args:
            provider: Must match a name in ``provider_priority``.
            fn: An async function ``(messages, **kwargs) -> response``.
        """
        if provider not in self._breakers:
            raise ValueError(
                f"Provider '{provider}' is not in the priority list "
                f"{self._settings.provider_priority}"
            )
        self._provider_fns[provider] = fn

    # -- health introspection -----------------------------------------------

    def get_health(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of every provider's circuit-breaker state.

        Returns:
            A dict keyed by provider name with fields:
            ``state``, ``consecutive_failures``, ``last_failure_time``.
        """
        return {
            name: {
                "state": cb.state.value,
                "consecutive_failures": cb.consecutive_failures,
                "last_failure_time": cb.last_failure_time,
            }
            for name, cb in self._breakers.items()
        }

    def get_breaker(self, provider: str) -> CircuitBreaker:
        """Return the ``CircuitBreaker`` for *provider* (mostly for testing)."""
        return self._breakers[provider]

    # -- core routing -------------------------------------------------------

    async def call(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        """Route a request through the priority list with retry + failover.

        Walks the provider list in priority order. For each healthy provider:
        1. Retries up to ``max_retries`` times with exponential backoff.
        2. On exhaustion, records a failure on the breaker and moves to the
           next provider.

        Args:
            messages: The chat messages to send (provider-agnostic format).
            **kwargs: Extra keyword arguments forwarded to the provider fn.

        Returns:
            The response from whichever provider succeeded.

        Raises:
            RuntimeError: If all providers are exhausted.
        """
        errors: list[tuple[str, Exception]] = []

        for provider in self._settings.provider_priority:
            breaker = self._breakers[provider]

            if not breaker.allow_request():
                logger.debug(
                    "Skipping provider [%s] — circuit is %s",
                    provider,
                    breaker.state.value,
                )
                continue

            if provider not in self._provider_fns:
                logger.warning(
                    "No call function registered for provider [%s]; skipping.",
                    provider,
                )
                continue

            fn = self._provider_fns[provider]

            try:
                result = await self._attempt_with_retries(
                    provider=provider,
                    fn=fn,
                    breaker=breaker,
                    messages=messages,
                    **kwargs,
                )
                return result
            except Exception as exc:
                errors.append((provider, exc))
                logger.error(
                    "Provider [%s] exhausted retries: %s", provider, exc,
                )
                continue

        # Every provider failed or was skipped.
        raise RuntimeError(
            f"All providers exhausted. Errors: {errors}"
        )

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return whether *exc* is worth retrying.

        Client errors (HTTP 4xx) are not retryable, with the exception
        of 429 Too Many Requests which indicates rate-limiting.
        """
        status_code: int | None = getattr(exc, "status_code", None)
        if status_code is None:
            response = getattr(exc, "response", None)
            if response is not None:
                status_code = getattr(response, "status_code", None)
        if status_code is not None and 400 <= status_code < 500 and status_code != 429:
            return False
        return True

    async def _attempt_with_retries(
        self,
        *,
        provider: str,
        fn: ProviderCallFn,
        breaker: CircuitBreaker,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """Retry a provider call with exponential backoff.

        On success, records a success on the circuit breaker.
        On failure (after all retries), records a failure.

        Raises the last exception if all retries are exhausted.
        """
        max_retries = self._settings.provider_max_retries
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):  # attempt 0 is the first try
            try:
                result = await asyncio.wait_for(
                    fn(messages, **kwargs),
                    timeout=self._settings.provider_timeout_seconds,
                )
                breaker.record_success()
                return result
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Provider [%s] attempt %d/%d failed: %s",
                    provider,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if not self._is_retryable(exc):
                    logger.info(
                        "Provider [%s]: non-retryable error; "
                        "skipping remaining retries",
                        provider,
                    )
                    break
                if attempt < max_retries:
                    backoff = 2 ** attempt + random.uniform(0, 1)  # jitter
                    await asyncio.sleep(backoff)

        # All retries exhausted — record a failure on the breaker.
        breaker.record_failure()
        assert last_exc is not None
        raise last_exc
