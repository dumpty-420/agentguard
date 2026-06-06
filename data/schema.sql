-- data/schema.sql — AgentGuard database schema.
--
-- Applied automatically on first postgres container start via
-- docker-entrypoint-initdb.d mount in docker-compose.yml.

-- =========================================================================
-- Phase 2: Cost Ledger
-- =========================================================================

CREATE TABLE IF NOT EXISTS cost_ledger (
    id                  BIGSERIAL       PRIMARY KEY,
    run_id              TEXT            NOT NULL,
    user_id             TEXT            NOT NULL,
    provider            TEXT            NOT NULL,
    model               TEXT            NOT NULL,
    prompt_tokens       INTEGER         NOT NULL,
    completion_tokens   INTEGER         NOT NULL,
    cost_usd            NUMERIC(12, 8) NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Indexes for queryable run / user history.
CREATE INDEX IF NOT EXISTS ix_cost_ledger_run_id  ON cost_ledger (run_id);
CREATE INDEX IF NOT EXISTS ix_cost_ledger_user_id ON cost_ledger (user_id);

-- =========================================================================
-- Phase 3: LangGraph Checkpointing  (AsyncPostgresSaver — managed tables)
-- =========================================================================
--
-- The checkpoint tables are NOT hand-rolled.  They are created and managed
-- by LangGraph's ``AsyncPostgresSaver.setup()`` at application startup.
--
-- For reference, ``setup()`` creates three tables:
--
--   checkpoints         — serialized graph state keyed by (thread_id,
--                         checkpoint_ns, checkpoint_id).  Contains the
--                         full state snapshot, metadata, and a pointer to
--                         the parent checkpoint.
--
--   checkpoint_blobs    — large binary blobs (msgpack-serialized channel
--                         values) referenced by checkpoints.  Separated
--                         to keep the main table lean.
--
--   checkpoint_writes   — pending / incomplete writes used for crash
--                         recovery.  Rows here represent writes that
--                         were started but not yet committed to a full
--                         checkpoint.
--
-- These tables share the same Postgres database as cost_ledger.
-- Do NOT manually modify them — use LangGraph's checkpointer API.
