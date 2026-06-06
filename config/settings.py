"""AgentGuard configuration — Pydantic settings loaded from environment variables.

All tunables for the Phase 1 provider-failover layer and Phase 2 cost
governance live here. Secrets (API keys, DB password) are read from env
vars only — never hardcoded.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for AgentGuard.

    Values are loaded from environment variables (case-insensitive).
    A ``.env`` file in the project root is supported via pydantic-settings.
    """

    # --- LLM Provider API Keys ---
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key. Required when Anthropic is in the priority list.",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key. Required when OpenAI is in the priority list.",
    )

    # --- Provider Failover ---
    provider_priority: list[str] = Field(
        default=["anthropic", "openai"],
        description=(
            "Ordered list of provider names. The runtime tries each in order "
            "and fails over to the next healthy provider."
        ),
    )
    provider_models: dict[str, str] = Field(
        default={
            "anthropic": "claude-sonnet-4-5",
            "openai": "gpt-4o",
        },
        description="Per-provider model mapping.",
    )
    provider_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout in seconds for an individual provider call.",
    )
    provider_max_retries: int = Field(
        default=3,
        ge=0,
        description="Max retry attempts per provider before moving to the next one.",
    )

    # --- Circuit Breaker ---
    circuit_breaker_failure_threshold: int = Field(
        default=5,
        ge=1,
        description=(
            "Number of consecutive failures that trips the circuit breaker "
            "for a provider (closed → open)."
        ),
    )
    circuit_breaker_cooldown_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "Seconds a tripped provider stays in the OPEN state before "
            "transitioning to HALF-OPEN for a recovery probe."
        ),
    )

    # ===================================================================
    # Phase 2: Cost Governance
    # ===================================================================

    # --- Budget ceilings ---
    budget_per_run_usd: float = Field(
        default=5.0,
        gt=0,
        description="Maximum spend (USD) allowed for a single agent run.",
    )
    budget_per_user_usd: float = Field(
        default=50.0,
        gt=0,
        description="Maximum cumulative spend (USD) allowed per user.",
    )
    budget_degradation_threshold: float = Field(
        default=0.8,
        gt=0,
        le=1.0,
        description=(
            "Fraction of a budget ceiling at which the cost governor "
            "downgrades to a cheaper model instead of hard-killing."
        ),
    )

    # --- Per-model pricing (USD per 1 K tokens) ---
    model_price_table: dict[str, dict[str, float]] = Field(
        default={
            "claude-sonnet-4-5": {
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
        description=(
            "Per-model price table. Each entry maps a model name to "
            "{'input_per_1k': <float>, 'output_per_1k': <float>}."
        ),
    )

    # --- Model tier / downgrade map ---
    model_downgrade_map: dict[str, str] = Field(
        default={
            "claude-sonnet-4-5": "claude-3-5-haiku-20241022",
            "gpt-4o": "gpt-4o-mini",
        },
        description=(
            "Maps an expensive model to a cheaper substitute used when the "
            "cost governor triggers graceful degradation."
        ),
    )

    # --- Postgres ---
    postgres_host: str = Field(
        default="localhost",
        description="Postgres hostname.",
    )
    postgres_port: int = Field(
        default=5435,
        description="Postgres port.",
    )
    postgres_db: str = Field(
        default="agentguard",
        description="Postgres database name.",
    )
    postgres_user: str = Field(
        default="agentguard",
        description="Postgres user.",
    )
    postgres_password: str = Field(
        default="",
        description="Postgres password.",
    )

    @model_validator(mode="after")
    def strip_whitespace_from_strings(self) -> Settings:
        for field_name, value in self.__dict__.items():
            if isinstance(value, str):
                setattr(self, field_name, value.strip())
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        # 'model_' is a Pydantic-reserved prefix by default, but our domain
        # genuinely uses it (model_price_table, model_downgrade_map).
        "protected_namespaces": ("settings_",),
    }


# Module-level singleton — import `settings` elsewhere.
settings = Settings()
