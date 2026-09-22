from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from packages.etf_live.config import load_config
from packages.etf_live.policy import PendingOrder, PolicyContext, PolicyState, T08Policy
from packages.etf_live.runtime import LiveRuntime
from packages.etf_strategy_core.tecl_enhancement import (
    T08EnhancementState,
    evaluate_t08_enhancement,
)


def _context(
    *,
    close: str = "110",
    sma200: str = "100",
    asymmetry: str | None = "1.0",
    vol_ratio: str | None = "1.0",
    positions: dict[str, Decimal] | None = None,
    prior_equity: str = "1000",
    prior_price: str = "100",
    cutoff: str = "2026-09-18",
    execution: str = "2026-09-21",
    state: PolicyState | None = None,
    pending: tuple[PendingOrder, ...] = (),
) -> PolicyContext:
    feature = {
        "close": Decimal(close),
        "sma200": Decimal(sma200),
        "asymmetry": None if asymmetry is None else Decimal(asymmetry),
        "vol_ratio": None if vol_ratio is None else Decimal(vol_ratio),
        "asof": cutoff,
    }
    return PolicyContext(
        execution_session=execution,
        information_cutoff=cutoff,
        features={"XLK": feature},
        positions=positions or {},
        pending_orders=pending,
        prior_close_equity=Decimal(prior_equity),
        prior_close={"TECL": Decimal(prior_price)},
        state=state or PolicyState(),
    )


def test_t08_full_target_is_daily_and_trades_only_tecl() -> None:
    decision = T08Policy().decide(_context())
    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert (intent.symbol, intent.side, intent.target_weight) == ("TECL", "buy", Decimal("0.99"))
    assert intent.quantity == Decimal("9.900000")
    assert decision.decision_id.startswith("t08d-")


def test_t08_brake_target_is_half() -> None:
    decision = T08Policy().decide(_context(asymmetry="1.5001", vol_ratio="1.2501"))
    assert decision.intents[0].target_weight == Decimal("0.495")


def test_t08_threshold_equality_does_not_brake() -> None:
    decision = T08Policy().decide(_context(asymmetry="1.5", vol_ratio="1.25"))
    assert decision.intents[0].target_weight == Decimal("0.99")


def test_t08_below_trend_exits_even_without_risk_features() -> None:
    decision = T08Policy().decide(_context(close="99", asymmetry=None, vol_ratio=None, positions={"TECL": Decimal("9.9")}))
    assert [(item.symbol, item.side, item.reason) for item in decision.intents] == [("TECL", "sell", "T08_XLK_SMA200_EXIT")]


def test_t08_missing_risk_features_blocks_increase() -> None:
    decision = T08Policy().decide(_context(asymmetry=None, vol_ratio=None))
    assert decision.intents == ()
    assert decision.status == "READY"


def test_t08_equal_trend_retains_actual_state() -> None:
    decision = T08Policy().decide(_context(close="100", positions={"TECL": Decimal("9.9")}))
    assert decision.intents == ()


def test_t08_unchanged_target_holds_shares_through_market_weight_drift() -> None:
    state = PolicyState(allocation_target_weight=Decimal("0.99"))
    decision = T08Policy().decide(_context(
        positions={"TECL": Decimal("8.5")},
        prior_equity="1000",
        prior_price="100",
        state=state,
    ))
    assert decision.intents == ()
    assert decision.next_state.allocation_target_weight == Decimal("0.99")


def test_t08_allocation_change_creates_one_transition_not_daily_rebalance() -> None:
    state = PolicyState(allocation_target_weight=Decimal("0.495"))
    context = _context(positions={"TECL": Decimal("5")}, prior_equity="1000", prior_price="100", state=state)
    decision = T08Policy().decide(context)
    assert len(decision.intents) == 1
    assert decision.intents[0].reason == "T08_ALLOCATION_INCREASE"
    assert decision.next_state.allocation_target_weight == Decimal("0.495")


def test_t08_active_buy_blocks_duplicate() -> None:
    decision = T08Policy().decide(_context(pending=(PendingOrder("TECL", "buy", Decimal("1")),)))
    assert decision.intents == ()


def test_t08_rejects_non_tecl_universe() -> None:
    try:
        T08Policy(symbols=("TQQQ",))
    except ValueError as exc:
        assert "T08_SYMBOLS_INVALID" in str(exc)
    else:
        raise AssertionError("expected T08 universe rejection")


def test_t08_feature_builder_has_causal_risk_fields() -> None:
    rows = [{"t": f"{date(2026, 1, 1) + timedelta(days=index):%Y-%m-%d}T00:00:00Z", "c": str(100 + index)} for index in range(75)]
    features = LiveRuntime._t08_feature_rows(rows)
    assert features[-1]["sma200"] is None
    assert features[-1]["asymmetry"] is not None
    assert features[-1]["vol_ratio"] is not None


def test_t08_runtime_selects_tecl_policy_from_versioned_config(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    runtime = LiveRuntime(config=config, broker=object())
    assert isinstance(runtime.policy, T08Policy)
    assert runtime.config.symbols == ("TECL",)
    assert runtime.config.signal_symbols == ("XLK",)


def test_t08_feature_distribution_does_not_create_false_crash() -> None:
    rows = [
        {"t": "2026-01-01T00:00:00Z", "c": "100"},
        {"t": "2026-01-02T00:00:00Z", "c": "90"},
    ]
    features = LiveRuntime._t08_feature_rows(rows, [{"ex_date": "2026-01-02", "type": "cash_dividend", "amount": "10"}])
    assert features[-1]["close"] == Decimal("100")


def test_t08_research_core_and_live_adapter_match_target_states() -> None:
    policy = T08Policy()
    for close, asymmetry, ratio, expected in (
        ("110", "1", "1", Decimal("0.99")),
        ("110", "1.6", "1.3", Decimal("0.495")),
        ("90", None, None, Decimal("0")),
    ):
        live_context = _context(close=close, asymmetry=asymmetry, vol_ratio=ratio, positions={"TECL": Decimal("1")})
        live = policy.decide(live_context)
        core_features = {
            "XLK": {
                "date": date(2026, 9, 18),
                "close": close,
                "sma200": "100",
                "d20": "0.016" if asymmetry == "1.6" else "0.01",
                "u20": "0.01",
                "sigma20": "0.26" if ratio == "1.3" else "0.2",
                "sigma60": "0.2",
            },
            "TECL": {"date": date(2026, 9, 18), "sigma20": "0.5"},
            "IEF": {"date": date(2026, 9, 18), "r21": "0"},
            "HYG": {"date": date(2026, 9, 18), "ratio_lqd_r21": "0"},
        }
        core = evaluate_t08_enhancement(
            execution_session=date(2026, 9, 21),
            information_cutoff=date(2026, 9, 18),
            features=core_features,
            state=T08EnhancementState(core_target=Decimal("0.99"), core_initialized=True),
            held_quantity="1",
        )
        assert core.target_weight == expected
        if expected == 0:
            assert live.intents and live.intents[0].side == "sell"
        else:
            assert live.intents[0].target_weight == expected
