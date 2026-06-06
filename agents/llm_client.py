"""agents/llm_client.py — Unified multi-provider LLM client.

The caller uses ``LLMClient.complete(messages)`` and is completely unaware
of which provider answers. Provider selection, retry, and failover are
handled by ``runtime.failover.FailoverRouter``.

Phase 2 adds optional cost-governance: when a ``CostGovernor`` is
attached, every call is budget-checked, potentially model-downgraded,
and usage-logged — transparently to the agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anthropic
import openai

from config.settings import Settings, settings as default_settings
from core.schemas import TokenUsage
from runtime.cost_governor import CostGovernor
from runtime.failover import FailoverRouter

logger = logging.getLogger(__name__)

# Default models per provider — easily overridable via kwargs.
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o",
}


# ---------------------------------------------------------------------------
# Internal result type — never exposed to agents.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ProviderResult:
    """Bundles the response text with token usage for cost metering.

    This type is internal to ``llm_client.py``.  The failover router is
    ``Any``-typed so it passes this through unchanged.  ``complete()``
    unpacks ``.text`` for the caller and ``.usage`` for the cost governor.
    """

    text: str
    usage: TokenUsage
    provider: str
    model: str


class LLMClient:
    """A unified LLM client that routes calls through the failover layer.

    Usage::

        client = LLMClient()
        response_text = await client.complete([
            {"role": "user", "content": "Hello!"}
        ])

    Args:
        settings: Optional ``Settings`` override (useful for testing).
        router: Optional ``FailoverRouter`` override (useful for testing).
        cost_governor: Optional ``CostGovernor`` for budget enforcement.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        router: FailoverRouter | None = None,
        cost_governor: CostGovernor | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._router = router or FailoverRouter(settings=self._settings)
        self._cost_governor = cost_governor

        # Register provider call functions for each configured provider.
        _PROVIDER_FACTORIES: dict[str, Any] = {
            "anthropic": self._call_anthropic,
            "openai": self._call_openai,
        }
        for provider in self._settings.provider_priority:
            if provider in _PROVIDER_FACTORIES:
                self._router.register(provider, _PROVIDER_FACTORIES[provider])
            else:
                logger.warning("Unknown provider '%s' — skipping registration.", provider)

    # -- public API ---------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Send *messages* to the best available provider and return the response text.

        The caller never needs to know which provider handled the request.
        When a ``CostGovernor`` is attached, the call is budget-checked and
        usage-logged transparently.

        Args:
            messages: A list of ``{"role": "...", "content": "..."}`` dicts.
            **kwargs: Forwarded to the underlying provider SDK call
                      (e.g. ``model``, ``max_tokens``).

        Returns:
            The assistant's response text.

        Raises:
            RuntimeError: If every provider is exhausted.
            BudgetExceededError: If the budget is fully exhausted
                (only when a ``CostGovernor`` is attached).
        """
        # --- Phase 2: pre-check budget and maybe downgrade model ---
        if self._cost_governor is not None:
            first_provider = self._settings.provider_priority[0]
            current_model = kwargs.get("model", self._settings.provider_models.get(
                first_provider, _DEFAULT_MODELS.get(first_provider, "")
            ))
            effective_model = await self._cost_governor.pre_check(current_model)
            kwargs["model"] = effective_model

        # --- Core call through the failover router ---
        result: _ProviderResult = await self._router.call(messages, **kwargs)

        # --- Phase 2: record usage ---
        if self._cost_governor is not None:
            await self._cost_governor.record_usage(
                provider=result.provider,
                model=result.model,
                usage=result.usage,
            )

        return result.text

    @property
    def router(self) -> FailoverRouter:
        """Expose the router for health introspection."""
        return self._router

    @property
    def cost_governor(self) -> CostGovernor | None:
        """Expose the cost governor for spend introspection."""
        return self._cost_governor

    # -- helper to resolve models per provider ------------------------------

    def _resolve_model(self, provider: str, **kwargs: Any) -> str:
        """Resolve the model to use for a provider.

        Looks up self._settings.provider_models[provider]. If the passed model name
        is in the values of model_downgrade_map, it indicates that a budget downgrade
        has been triggered, so we use the provider's mapped downgraded model instead.
        """
        passed_model = kwargs.get("model")
        base_model = self._settings.provider_models.get(
            provider, _DEFAULT_MODELS.get(provider, "")
        )
        downgraded_model = self._settings.model_downgrade_map.get(base_model, base_model)

        if passed_model is not None:
            if passed_model in (base_model, downgraded_model):
                return passed_model
            if passed_model in self._settings.model_downgrade_map.values():
                return downgraded_model

        return base_model

    # -- private provider wrappers ------------------------------------------

    async def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> _ProviderResult:
        """Call the Anthropic Messages API."""
        model = self._resolve_model("anthropic", **kwargs)
        kwargs_clean = kwargs.copy()
        kwargs_clean.pop("model", None)
        max_tokens = kwargs_clean.pop("max_tokens", 1024)

        client = anthropic.AsyncAnthropic(
            api_key=self._settings.anthropic_api_key,
        )
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            **kwargs_clean,
        )
        # Extract the text from the first content block.
        text = response.content[0].text
        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )
        return _ProviderResult(text=text, usage=usage, provider="anthropic", model=model)

    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> _ProviderResult:
        """Call the OpenAI Chat Completions API."""
        model = self._resolve_model("openai", **kwargs)
        kwargs_clean = kwargs.copy()
        kwargs_clean.pop("model", None)

        client = openai.AsyncOpenAI(
            api_key=self._settings.openai_api_key,
        )
        response = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            **kwargs_clean,
        )
        text = response.choices[0].message.content or ""
        resp_usage = response.usage
        usage = TokenUsage(
            prompt_tokens=resp_usage.prompt_tokens if resp_usage else 0,
            completion_tokens=resp_usage.completion_tokens if resp_usage else 0,
        )
        return _ProviderResult(text=text, usage=usage, provider="openai", model=model)
