-- Runtime source of truth for deployment.  Fixture replay uses MemoryLedger.
-- This migration intentionally grants no public mutation authority.

CREATE TABLE IF NOT EXISTS events_v1 (
    event_id uuid PRIMARY KEY,
    aggregate_id text NOT NULL,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    event_type text NOT NULL,
    run_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    content_hash text NOT NULL UNIQUE,
    UNIQUE (aggregate_id, aggregate_version)
);

CREATE TABLE IF NOT EXISTS outbox_v1 (
    message_id text PRIMARY KEY,
    command_hash text NOT NULL UNIQUE,
    topic text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz
);

CREATE TABLE IF NOT EXISTS inbox_v1 (
    message_id text PRIMARY KEY,
    consumed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_uniqueness_v1 (
    account_id text NOT NULL,
    client_order_id text NOT NULL,
    intent_id text NOT NULL,
    plan_hash text NOT NULL,
    PRIMARY KEY (account_id, client_order_id),
    UNIQUE (intent_id, plan_hash)
);

CREATE TABLE IF NOT EXISTS public_decision_tape_v1 (
    run_id text NOT NULL,
    sequence bigint NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    sanitized_payload jsonb NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

-- Deployment provisioner must create separate API and operator identities.
-- The API receives SELECT only on public_decision_tape_v1.  It receives no
-- grants for events_v1, outbox_v1, inbox_v1, order_uniqueness_v1, or control.
