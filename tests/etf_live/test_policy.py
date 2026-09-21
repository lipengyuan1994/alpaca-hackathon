from __future__ import annotations

from decimal import Decimal

from packages.etf_live.policy import (
    FeatureSnapshot,
    L11Policy,
    PendingOrder,
    PolicyContext,
    PolicyState,
)


def _features(*, broad_close: float = 110.0, sector_close: float = 110.0) -> dict[str, FeatureSnapshot]:
    return {
        "QQQ": FeatureSnapshot(broad_close, 100.0, 0.20, r63=0.10),
        "SOXX": FeatureSnapshot(sector_close, 100.0, 0.20, r63=0.15, ratio=1.10, ratio_sma20=1.00),
    }


def _context(
    execution: str,
    *,
    cutoff: str | None = None,
    features: dict[str, FeatureSnapshot] | None = None,
    positions: dict[str, Decimal] | None = None,
    state: PolicyState | None = None,
    activation_review: bool = False,
    first_session_of_week: bool | None = None,
) -> PolicyContext:
    return PolicyContext(
        execution_session=execution,
        information_cutoff=cutoff or execution,
        features=features or _features(),
        positions=positions or {},
        prior_close_equity=Decimal("1000"),
        prior_close={"TQQQ": Decimal("100"), "SOXL": Decimal("100")},
        state=state or PolicyState(),
        activation_review=activation_review,
        first_session_of_week=first_session_of_week,
    )


def test_weekly_boundary_targets_are_emitted_once_then_held() -> None:
    policy = L11Policy()
    monday = policy.decide(_context("2024-06-03", cutoff="2024-05-31"))
    assert monday.review is True
    assert [(item.symbol, item.side, item.target_weight) for item in monday.intents] == [
        ("TQQQ", "buy", Decimal("0.495")),
        ("SOXL", "buy", Decimal("0.495")),
    ]

    # The state records that this week's review already happened.  A Tuesday
    # call must not retry either purchase if Monday was missed/unfilled.
    tuesday = policy.decide(
        _context("2024-06-04", cutoff="2024-06-03", state=monday.next_state)
    )
    assert tuesday.review is False
    assert tuesday.intents == ()

    next_monday = policy.decide(
        _context("2024-06-10", cutoff="2024-06-07", state=tuesday.next_state)
    )
    assert next_monday.review is True
    assert {item.symbol for item in next_monday.intents} == {"TQQQ", "SOXL"}


def test_daily_proxy_exit_precedes_weekly_targets_and_suppresses_reentry() -> None:
    policy = L11Policy()
    features = _features(broad_close=90.0)
    held = {"TQQQ": Decimal("4.950000"), "SOXL": Decimal("4.950000")}
    decision = policy.decide(
        _context("2024-06-03", cutoff="2024-05-31", features=features, positions=held)
    )
    assert decision.intents
    assert decision.intents[0].symbol == "TQQQ"
    assert decision.intents[0].side == "sell"
    assert all(not (item.symbol == "TQQQ" and item.side == "buy") for item in decision.intents)
    # SOXL remains eligible and may be rebalanced after the higher-priority
    # TQQQ exit; the exited sleeve itself cannot be re-entered.
    assert all(item.symbol != "TQQQ" or item.side == "sell" for item in decision.intents)


def test_equality_retains_state_and_does_not_pass_strict_eligibility() -> None:
    policy = L11Policy()
    equal = _features(broad_close=100.0, sector_close=100.0)
    decision = policy.decide(
        _context("2024-06-03", cutoff="2024-05-31", features=equal)
    )
    assert decision.intents == ()
    assert decision.review is True


def test_no_same_session_reentry_survives_a_repeated_decision() -> None:
    policy = L11Policy()
    first = policy.decide(
        _context(
            "2024-06-03",
            cutoff="2024-05-31",
            features=_features(broad_close=90.0),
            positions={"TQQQ": Decimal("4.950000")},
        )
    )
    assert ("TQQQ", "sell") in {(item.symbol, item.side) for item in first.intents}
    assert all(item.symbol != "TQQQ" or item.side != "buy" for item in first.intents)
    repeated = policy.decide(
        _context(
            "2024-06-03",
            cutoff="2024-05-31",
            features=_features(),
            positions={"TQQQ": Decimal("4.950000")},
            state=first.next_state,
        )
    )
    assert all(item.symbol != "TQQQ" or item.side != "buy" for item in repeated.intents)


def test_initial_activation_review_is_consumed_and_not_repeated() -> None:
    policy = L11Policy()
    activation = policy.decide(
        _context(
            "2024-06-05",
            cutoff="2024-06-04",
            activation_review=True,
            first_session_of_week=False,
        )
    )
    assert activation.review is True
    assert activation.activation_review is True
    assert activation.next_state.activation_review_consumed is True

    restart = policy.decide(
        _context(
            "2024-06-06",
            cutoff="2024-06-05",
            activation_review=True,
            first_session_of_week=False,
            state=activation.next_state,
        )
    )
    assert restart.review is False
    assert restart.activation_review is False
    assert restart.intents == ()


def test_future_feature_rows_cannot_change_a_prior_decision() -> None:
    policy = L11Policy()
    base = _features()
    with_future = {
        **base,
        "QQQ": {
            "rows": [
                {"date": "2024-05-31", "close": 110.0, "sma200": 100.0, "r126": 0.20, "r63": 0.10},
                # This row is after the cutoff and would make broad eligibility
                # false if a future value were accidentally read.
                {"date": "2024-06-03", "close": 1.0, "sma200": 100.0, "r126": -0.90, "r63": -0.90},
            ]
        },
    }
    left = policy.decide(_context("2024-06-03", cutoff="2024-05-31", features=base))
    right = policy.decide(_context("2024-06-03", cutoff="2024-05-31", features=with_future))
    assert left.intents == right.intents
    assert left.decision_id == right.decision_id


def test_current_or_missing_cutoff_blocks_without_consuming_activation() -> None:
    policy = L11Policy()
    current = policy.decide(_context("2024-06-03", cutoff="2024-06-03", activation_review=True))
    missing = policy.decide(
        PolicyContext(execution_session="2024-06-03", information_cutoff=None, activation_review=True)
    )
    assert current.status == "BLOCKED"
    assert missing.status == "BLOCKED"
    assert current.intents == missing.intents == ()
    assert current.next_state.activation_review_consumed is False
    assert missing.next_state.activation_review_consumed is False


def test_active_pending_buy_blocks_duplicate_weekly_submission() -> None:
    policy = L11Policy()
    decision = policy.decide(
        PolicyContext(
            execution_session="2024-06-03",
            information_cutoff="2024-05-31",
            features=_features(),
            pending_orders=(PendingOrder("TQQQ", "buy", Decimal("4")),),
            prior_close_equity=Decimal("1000"),
            prior_close={"TQQQ": Decimal("100"), "SOXL": Decimal("100")},
        )
    )
    assert [item.symbol for item in decision.intents] == ["SOXL"]
