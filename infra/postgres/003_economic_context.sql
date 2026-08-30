-- One immutable pre-market Alpaca market/news context per New York trading day,
-- plus the durable audit projection requested for every generated signal.
-- These tables live in the same private PostgreSQL service as the outbox.

CREATE TABLE IF NOT EXISTS daily_economic_context_v1 (
    trading_date date PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('COLLECTING', 'READY', 'FAILED')),
    collection_config_hash text NOT NULL,
    collection_started_at timestamptz NOT NULL,
    context_hash text UNIQUE,
    collected_at timestamptz,
    expires_at timestamptz,
    payload jsonb,
    failure_reason text,
    failed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (status = 'READY' AND context_hash IS NOT NULL AND collected_at IS NOT NULL
            AND expires_at IS NOT NULL AND payload IS NOT NULL AND failure_reason IS NULL)
        OR (status = 'COLLECTING' AND context_hash IS NULL AND payload IS NULL)
        OR (status = 'FAILED' AND failure_reason IS NOT NULL AND failed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS signal_decision_audit_v1 (
    record_id text PRIMARY KEY,
    run_id text NOT NULL,
    trading_date date NOT NULL,
    recorded_at timestamptz NOT NULL,
    strategy_evaluation_hash text NOT NULL,
    agent_thesis_hash text,
    trade_intent_hash text,
    economic_context_hash text,
    economic_assessment_hash text,
    decision_status text NOT NULL CHECK (
        decision_status IN ('NO_TRADE', 'RISK_REJECTED', 'APPROVED_AND_ENQUEUED')
    ),
    placement_state text NOT NULL CHECK (
        placement_state IN (
            'NOT_PLACED', 'ENQUEUED', 'ACCEPTED', 'PARTIAL', 'FILLED',
            'REJECTED', 'CANCELLED', 'EXPIRED', 'UNKNOWN'
        )
    ),
    order_placed boolean NOT NULL,
    reason_code text NOT NULL,
    plan_hash text,
    client_order_id text,
    signal_payload jsonb NOT NULL,
    supplemental jsonb NOT NULL,
    payload jsonb NOT NULL,
    content_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (decision_status <> 'NO_TRADE' OR (
            placement_state = 'NOT_PLACED' AND plan_hash IS NULL AND client_order_id IS NULL
        ))
    ),
    CHECK (
        (placement_state IN ('ACCEPTED', 'PARTIAL', 'FILLED', 'CANCELLED', 'EXPIRED'))
            = order_placed
    ),
    CHECK (placement_state = 'NOT_PLACED' OR client_order_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS signal_decision_audit_by_day_v1
    ON signal_decision_audit_v1 (trading_date, recorded_at DESC, record_id);

CREATE UNIQUE INDEX IF NOT EXISTS signal_decision_audit_client_order_v1
    ON signal_decision_audit_v1 (client_order_id)
    WHERE client_order_id IS NOT NULL;

REVOKE ALL ON daily_economic_context_v1, signal_decision_audit_v1 FROM PUBLIC;
