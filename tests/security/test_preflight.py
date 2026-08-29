from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.decision_worker.main import (
    COMPETITION_ENTRY_CUTOFF,
    COMPETITION_FLAT_DEADLINE,
    COMPETITION_FLATTEN_AT,
    FIXTURE_TIME,
    run_approved_fixture,
)
from apps.execution_worker.main import process_approved_command
from packages.contracts.canonical import hash_without
from packages.contracts.models import (
    ControlStateV1,
    ExecutionDeploymentV1,
    PositionLegV1,
    PositionSnapshotV1,
)
from packages.execution_core import FakeBroker
from packages.risk_kernel import evaluate_risk


def _deployment(decision, *, paper_base_url: str = "https://paper-api.alpaca.markets") -> ExecutionDeploymentV1:
    risk_input = decision.risk_input
    return ExecutionDeploymentV1(
        expected_account_id=decision.account.account_id,
        paper_base_url=paper_base_url,
        risk_policy_hash=risk_input.risk_policy.policy_hash,
        template_catalog_hash=risk_input.template_catalog_hash,
        strategy_registry_hash=risk_input.strategy_registry_hash,
        strategy_config_hash=risk_input.strategy_config_hash,
        strategy_content_hash=risk_input.strategy_content_hash,
        account_allowlist_hash=risk_input.account_allowlist_hash,
        release_hash=risk_input.release_hash,
        entry_cutoff_at=COMPETITION_ENTRY_CUTOFF,
        flatten_at=COMPETITION_FLATTEN_AT,
        flat_deadline_at=COMPETITION_FLAT_DEADLINE,
    )


@pytest.mark.parametrize(
    "paper_hostname",
    [
        "https://unsafe.example.test",
        "https://paper-api.evil.test",
        "https://paper-api.alpaca.markets.evil.test",
        "https://paper-api.alpaca.markets@evil.test",
        "https://user@paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets:8443",
        "https://paper-api.alpaca.markets/orders",
        "https://paper-api.alpaca.markets?next=evil",
        "https://paper-api.alpaca.markets#evil",
        "http://paper-api.alpaca.markets",
    ],
)
def test_preflight_rejects_non_exact_paper_origin_without_broker_call(
    paper_hostname: str,
) -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision, paper_base_url=paper_hostname),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_NON_PAPER_HOST" in outcome.preflight.reason_codes
    assert outcome.broker_event is None
    assert broker.submit_count == 0


@pytest.mark.parametrize(
    "paper_hostname",
    ["https://paper-api.alpaca.markets", "https://paper-api.alpaca.markets/", "https://paper-api.alpaca.markets:443"],
)
def test_preflight_accepts_only_exact_paper_origin_variants(paper_hostname: str) -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()

    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision, paper_base_url=paper_hostname),
    )

    assert outcome.preflight.allowed
    assert broker.submit_count == 1


def test_preflight_rejects_sibling_authorization_swap_without_broker_call() -> None:
    decision = run_approved_fixture()
    altered_input = decision.risk_input.model_copy(update={"release_hash": "sha256:" + "f" * 64})
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        altered_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_RISK_INPUT_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_underreported_maximum_loss_fails_risk_and_execution_without_broker_call() -> None:
    """Regression: a $400 debit cannot be represented or authorized as $1 risk."""
    decision = run_approved_fixture()
    tampered_plan = decision.command.plan.model_copy(
        update={"limit_debit": Decimal("2.00"), "maximum_loss": Decimal("1.00")}
    )
    tampered_risk_input = decision.risk_input.model_copy(update={"plan": tampered_plan})

    risk_decision = evaluate_risk(
        tampered_risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )

    assert not risk_decision.approved
    assert "RISK_MAXIMUM_LOSS_MISMATCH" in risk_decision.reason_codes
    assert "RISK_PER_TRADE_LIMIT" in risk_decision.reason_codes
    assert risk_decision.maximum_loss == Decimal("400.00")

    tampered_command = decision.command.model_copy(update={"plan": tampered_plan})
    broker = FakeBroker()
    outcome = process_approved_command(
        tampered_command,
        tampered_risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )

    assert not outcome.preflight.allowed
    assert "PREFLIGHT_MAXIMUM_LOSS_MISMATCH" in outcome.preflight.reason_codes
    assert "PREFLIGHT_APPROVAL_LOSS_MISMATCH" in outcome.preflight.reason_codes
    assert "PREFLIGHT_PER_TRADE_LIMIT" in outcome.preflight.reason_codes
    assert outcome.broker_event is None
    assert broker.submit_count == 0


def test_risk_and_preflight_reject_stale_option_quotes_even_with_fresh_underlying() -> None:
    decision = run_approved_fixture()
    stale_contracts = tuple(
        contract.model_copy(
            update={
                "quote": contract.quote.model_copy(
                    update={"event_time": FIXTURE_TIME.replace(minute=13)}
                )
            }
        )
        for contract in decision.market.option_contracts
    )
    stale_market = decision.market.model_copy(update={"option_contracts": stale_contracts})

    risk_decision = evaluate_risk(
        decision.risk_input,
        stale_market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )
    assert not risk_decision.approved
    assert "RISK_STALE_OPTION_QUOTE" in risk_decision.reason_codes

    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        stale_market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_STALE_OPTION_QUOTE" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_current_halt_invalidates_previously_approved_entry() -> None:
    decision = run_approved_fixture()
    halted = ControlStateV1(
        account_id=decision.control_state.account_id,
        version=decision.control_state.version + 1,
        mode="HALTED",
        release_hash=decision.control_state.release_hash,
        config_hash=decision.control_state.config_hash,
        account_allowlist_hash=decision.control_state.account_allowlist_hash,
        reconciliation_hash=decision.control_state.reconciliation_hash,
        reconciled_at=FIXTURE_TIME,
    )
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        halted,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_CURRENT_CONTROL_MODE_MISMATCH" in outcome.preflight.reason_codes
    assert "PREFLIGHT_CONTROL_STATE_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_preflight_recomputes_policy_hash_instead_of_trusting_copied_identifier() -> None:
    decision = run_approved_fixture()
    forged_policy = decision.risk_input.risk_policy.model_copy(
        update={
            "max_per_trade_loss": Decimal("1000"),
            "max_daily_loss": Decimal("1000"),
            "max_total_reserved_loss": Decimal("1000"),
        }
    )
    forged_risk = decision.risk_input.model_copy(
        update={"risk_policy": forged_policy, "risk_input_hash": None}
    )
    forged_risk = forged_risk.model_copy(
        update={"risk_input_hash": hash_without(forged_risk, "risk_input_hash")}
    )
    forged_approval = decision.command.approval.model_copy(
        update={"risk_input_hash": forged_risk.risk_input_hash, "decision_hash": None}
    )
    forged_approval = forged_approval.model_copy(
        update={"decision_hash": hash_without(forged_approval, "decision_hash")}
    )
    forged_command = decision.command.model_copy(
        update={
            "risk_input_hash": forged_risk.risk_input_hash,
            "approval": forged_approval,
            "command_hash": None,
        }
    )
    forged_command = forged_command.model_copy(
        update={"command_hash": hash_without(forged_command, "command_hash")}
    )
    broker = FakeBroker()

    outcome = process_approved_command(
        forged_command,
        forged_risk,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )

    assert not outcome.preflight.allowed
    assert "PREFLIGHT_RISK_POLICY_HASH_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_preflight_requires_credential_zone_runtime_state_refresh() -> None:
    class MismatchedBroker(FakeBroker):
        def runtime_state_violations(self, **kwargs):
            return ("PREFLIGHT_BROKER_ACCOUNT_SNAPSHOT_MISMATCH",)

    decision = run_approved_fixture()
    broker = MismatchedBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )

    assert not outcome.preflight.allowed
    assert "PREFLIGHT_BROKER_ACCOUNT_SNAPSHOT_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_deployed_registry_hash_swap_is_rejected() -> None:
    decision = run_approved_fixture()
    deployment = _deployment(decision).model_copy(
        update={"strategy_registry_hash": "sha256:" + "f" * 64}
    )
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=deployment,
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_DEPLOYED_STRATEGY_REGISTRY_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_daily_loss_capacity_is_conservative() -> None:
    decision = run_approved_fixture()
    losing_account = decision.account.model_copy(
        update={"day_start_equity": Decimal("100000"), "equity": Decimal("99600")}
    )
    risk_decision = evaluate_risk(
        decision.risk_input,
        decision.market,
        losing_account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )
    assert not risk_decision.approved
    assert "RISK_DAILY_LOSS_LIMIT" in risk_decision.reason_codes


def test_closed_market_clock_rejects_entry() -> None:
    decision = run_approved_fixture()
    closed_market = decision.market.model_copy(
        update={"clock": decision.market.clock.model_copy(update={"is_open": False})}
    )
    risk_decision = evaluate_risk(
        decision.risk_input,
        closed_market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )
    assert not risk_decision.approved
    assert "RISK_MARKET_CLOSED" in risk_decision.reason_codes


def test_competition_cutoff_rejects_new_entry_in_risk_and_preflight() -> None:
    decision = run_approved_fixture()
    risk_decision = evaluate_risk(
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=COMPETITION_ENTRY_CUTOFF,
    )
    assert not risk_decision.approved
    assert "RISK_ENTRY_CUTOFF_REACHED" in risk_decision.reason_codes

    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=COMPETITION_ENTRY_CUTOFF,
        deployment=_deployment(decision),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_ENTRY_CUTOFF_REACHED" in outcome.preflight.reason_codes
    assert broker.submit_count == 0


def test_risk_rejects_hold_window_crossing_flatten_or_market_close() -> None:
    decision = run_approved_fixture()
    near_flatten = evaluate_risk(
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=COMPETITION_FLATTEN_AT - timedelta(minutes=30),
    )
    assert not near_flatten.approved
    assert "RISK_HOLD_WINDOW_CROSSES_FLATTEN" in near_flatten.reason_codes

    closing_market = decision.market.model_copy(
        update={
            "clock": decision.market.clock.model_copy(
                update={"next_close": FIXTURE_TIME + timedelta(minutes=30)}
            )
        }
    )
    near_close = evaluate_risk(
        decision.risk_input,
        closing_market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )
    assert not near_close.approved
    assert "RISK_HOLD_WINDOW_CROSSES_MARKET_CLOSE" in near_close.reason_codes


def test_existing_position_blocks_a_second_entry_in_risk_and_preflight() -> None:
    decision = run_approved_fixture()
    existing = PositionSnapshotV1(
        snapshot_id="positions-existing",
        account_id=decision.account.account_id,
        version=decision.positions.version,
        as_of=FIXTURE_TIME,
        legs=(PositionLegV1(symbol=decision.command.plan.legs[0].symbol, quantity=1),),
    )
    risk_decision = evaluate_risk(
        decision.risk_input,
        decision.market,
        decision.account,
        existing,
        decision.order_risk,
        decision.control_state,
        now=FIXTURE_TIME,
    )
    assert not risk_decision.approved
    assert "RISK_EXISTING_POSITION" in risk_decision.reason_codes

    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        existing,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_EXISTING_POSITION" in outcome.preflight.reason_codes
    assert broker.submit_count == 0
