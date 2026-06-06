# AgentGuard

AgentGuard is an **agent runtime / control plane** for running autonomous LLM agents safely in production. This is a **platform-engineering project, not a prompting project**. The emphasis is on reliability and resource management, not on agent intelligence.

The three core capabilities, in build order:
1. **Provider failover** — transparent failover across LLM providers with retry + circuit breaking
2. **Cost governance** — per-run and per-user budget enforcement with graceful degradation, logged to a Postgres cost ledger
3. **Durable / resumable state** — LangGraph checkpointing to Postgres so interrupted runs resume from the last good step

## Architecture principle (non-negotiable)

The `runtime/` layer **WRAPS** the agent graph. Agents must NOT know about budgets, failover, or checkpoints — the runtime enforces all of that around them. Keep infrastructure concerns out of agent business logic. If you ever find yourself adding retry, budget, or checkpoint logic inside an agent, that logic belongs in `runtime/` instead.

## Stack

- **Language:** Python (type hints everywhere)
- **Orchestration:** LangGraph (with Postgres checkpointing)
- **API:** FastAPI
- **Database:** Postgres (durable checkpoints + auditable cost ledger + run history)
- **LLM providers:** Anthropic (primary), OpenAI (fallback) — multi-provider client
- **Tracing / observability:** LangSmith
- **Containerization:** Docker (app + postgres via docker-compose)

## Conventions

- Pydantic for ALL settings and schemas
- Type hints on every function signature
- Each module gets a corresponding test in `tests/`
- Secrets via environment variables ONLY — never hardcoded. Always provide a `.env.example` with placeholders, and ensure `.env` is in `.gitignore` before the first commit.
- Build ONE phase at a time. Do NOT scaffold files for unbuilt phases. No empty stubs for cost governance or checkpointing until those phases are reached.
- Mock external SDK calls in tests — never hit real APIs in the test suite.

## Target repo structure (built incrementally)

```
agentguard/
├── .github/workflows/deploy.yaml   # CI/CD → GCP Cloud Run (Workload Identity Federation, no SA keys)
├── agents/
│   ├── coordinator.py              # LangGraph supervisor
│   ├── worker_agents.py            # researcher / analyst / synthesizer
│   └── llm_client.py               # unified multi-provider client (Phase 1)
├── runtime/                        # THE PLATFORM LAYER — the differentiator
│   ├── failover.py                 # circuit breaker + retry + provider routing (Phase 1)
│   ├── cost_governor.py            # budget enforcement + degradation (Phase 2)
│   ├── checkpointer.py             # durable state, Postgres-backed (Phase 3)
│   └── telemetry.py                # LangSmith tracing
├── api/
│   └── routes.py                   # FastAPI routes (uses core/orchestrator)
├── config/
│   ├── logger.py
│   └── settings.py                 # Pydantic settings
├── core/
│   ├── orchestrator.py             # wires runtime/ layers around the agent graph
│   ├── database.py                 # Postgres interface layer
│   └── schemas.py                  # Pydantic models (incl. CostLedgerEntry, RunCheckpoint, ProviderHealth)
├── data/
│   └── schema.sql                  # DB schema (cost_ledger, run_checkpoints tables)
├── tests/
│   ├── test_failover.py
│   ├── test_cost_governor.py
│   └── test_checkpointing.py
├── .env.example
├── docker-compose.yml              # app + postgres
├── Dockerfile
├── main.py                         # FastAPI entry point
└── requirements.txt
```

## Design decisions to defend in interviews

- **Postgres over Redis for checkpoints:** chosen for durability and queryable run history — supports an auditable cost ledger ("show me why run X cost $4") over raw speed.
- **runtime/ wraps the graph:** infrastructure concerns (failover, budget, checkpoints) are separated from agent business logic by design.
- **Graceful degradation over hard-kill:** when a run approaches its budget, downgrade to a cheaper model before halting.
