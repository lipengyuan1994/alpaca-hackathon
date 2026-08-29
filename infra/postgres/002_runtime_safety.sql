-- Durable risk capacity, execution leases, broker evidence, and control authority.
-- Apply after 001_initial.sql. All runtime mutations occur in private roles only.

ALTER TABLE events_v1 ALTER COLUMN event_id TYPE text USING event_id::text;

ALTER TABLE outbox_v1
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_until timestamptz,
    ADD COLUMN IF NOT EXISTS processed_at timestamptz,
    ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error text,
    ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100,
    ADD COLUMN IF NOT EXISTS submission_state text NOT NULL DEFAULT 'READY'
        CHECK (submission_state IN ('READY', 'RECONCILE_ONLY'));

CREATE TABLE IF NOT EXISTS order_risk_state_v1 (
    account_id text PRIMARY KEY,
    version bigint NOT NULL CHECK (version >= 0),
    content_hash text NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS risk_reservations_v1 (
    reservation_id text PRIMARY KEY,
    account_id text NOT NULL,
    plan_hash text NOT NULL UNIQUE,
    maximum_loss numeric(20, 6) NOT NULL CHECK (maximum_loss > 0),
    remaining_quantity integer NOT NULL CHECK (remaining_quantity > 0),
    status text NOT NULL CHECK (status IN ('APPROVED', 'ACCEPTED', 'PARTIAL', 'UNKNOWN')),
    expires_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS one_nonterminal_reservation_per_account_v1
    ON risk_reservations_v1 (account_id)
    WHERE status IN ('APPROVED', 'ACCEPTED', 'PARTIAL', 'UNKNOWN');

CREATE TABLE IF NOT EXISTS execution_leases_v1 (
    account_id text PRIMARY KEY,
    worker_id text NOT NULL,
    lease_until timestamptz NOT NULL,
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0)
);

CREATE TABLE IF NOT EXISTS broker_events_v1 (
    content_hash text PRIMARY KEY,
    client_order_id text NOT NULL,
    broker_order_id text,
    status text NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_positions_v1 (
    strategy_position_id text PRIMARY KEY,
    account_id text NOT NULL,
    entry_client_order_id text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('OPEN', 'CLOSING')),
    content_hash text NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS active_managed_positions_by_account_v1
    ON managed_positions_v1 (account_id, status, strategy_position_id);

CREATE TABLE IF NOT EXISTS control_state_v1 (
    account_id text PRIMARY KEY,
    version bigint NOT NULL CHECK (version >= 0),
    mode text NOT NULL CHECK (
        mode IN ('DISARMED', 'REPLAY', 'SHADOW', 'PAPER_DEMO_ARMED', 'PAPER_ARMED', 'FLATTENING', 'HALTED')
    ),
    release_hash text NOT NULL,
    config_hash text NOT NULL,
    account_allowlist_hash text NOT NULL,
    reconciliation_hash text NOT NULL,
    reconciled_at timestamptz NOT NULL,
    content_hash text NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS control_nonces_v1 (
    nonce uuid PRIMARY KEY,
    command_hash text NOT NULL UNIQUE,
    operator_id text NOT NULL,
    used_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pending_execution_outbox_v1
    ON outbox_v1 (priority, next_attempt_at, created_at)
    WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS decision_jobs_v1 (
    job_id text PRIMARY KEY,
    job_hash text NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    result_status text,
    last_error text
);

CREATE INDEX IF NOT EXISTS pending_decision_jobs_v1
    ON decision_jobs_v1 (next_attempt_at, created_at, job_id)
    WHERE processed_at IS NULL;

-- Roles are deployment-owned. These blocks are idempotent when provisioned by an
-- administrator and deliberately grant no access to PUBLIC.
REVOKE ALL ON events_v1, outbox_v1, inbox_v1, order_uniqueness_v1,
    order_risk_state_v1, risk_reservations_v1, execution_leases_v1,
    broker_events_v1, managed_positions_v1, decision_jobs_v1,
    control_state_v1, control_nonces_v1 FROM PUBLIC;
