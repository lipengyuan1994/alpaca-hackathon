"""Parity, boundary, refusal, and determinism tests for Group A packages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.contracts.models import (
    FeedIdentityV1,
    IntentTupleV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyStateV1,
)
from strategy_plugins.intraday_continuation_v1.plugin import (
    ALLOWED_UNDERLYINGS as CONTINUATION_SYMBOLS,
)
from strategy_plugins.intraday_continuation_v1.plugin import (
    FEATURE_CONTRACT_HASH as CONTINUATION_HASH,
)
from strategy_plugins.intraday_continuation_v1.plugin import Plugin as ContinuationPlugin
from strategy_plugins.intraday_continuation_v1.signal import evaluate_signal as continuation_signal
from strategy_plugins.vwap_reversion_v1.plugin import ALLOWED_UNDERLYINGS as REVERSION_SYMBOLS
from strategy_plugins.vwap_reversion_v1.plugin import FEATURE_CONTRACT_HASH as REVERSION_HASH
from strategy_plugins.vwap_reversion_v1.plugin import Plugin as ReversionPlugin
from strategy_plugins.vwap_reversion_v1.signal import evaluate_signal as reversion_signal

AS_OF = datetime(2026, 8, 28, 14, 30, 1, tzinfo=UTC)  # Friday 10:30:01 ET
HASH = "sha256:" + "a" * 64


def _config(**values: Decimal | str | int | bool) -> StrategyConfigV1:
    return StrategyConfigV1(values=values)


def _context(
    *,
    plugin_id: str,
    symbols: tuple[str, ...],
    feature_hash: str,
    values: dict[str, Decimal],
    quality_flags: tuple[str, ...] = (),
    as_of: datetime = AS_OF,
    feature_available_time: datetime | None = None,
    payload: dict[str, str] | None = None,
) -> StrategyContextV1:
    config = _config()
    return StrategyContextV1(
        evaluation_id=f"{plugin_id}-evaluation",
        as_of=as_of,
        market_snapshot_id="fixture-market",
        market_snapshot_hash=HASH,
        feature_vector_id="fixture-features",
        feature_vector_hash=HASH,
        feature_contract_hash=feature_hash,
        feature_available_time=feature_available_time or as_of - timedelta(seconds=1),
        feed_identity=FeedIdentityV1(entitlement="fixture-only"),
        quality_flags=quality_flags,
        universe_features=values,
        allowed_intent_tuples=(
            IntentTupleV1(
                template_id="CALL_DEBIT_SPREAD_V1",
                horizon_bucket="INTRADAY_15_60M",
                risk_tier="TINY",
                max_intent_ttl_seconds=300,
            ),
            IntentTupleV1(
                template_id="PUT_DEBIT_SPREAD_V1",
                horizon_bucket="INTRADAY_15_60M",
                risk_tier="TINY",
                max_intent_ttl_seconds=300,
            ),
        ),
        prior_state=StrategyStateV1(
            plugin_id=plugin_id,
            plugin_version="1.0.0",
            as_of=as_of - timedelta(seconds=1),
            sequence=0,
            payload=payload or {},
        ),
        config_hash=config.config_hash,
    )


def _continuation_values(*, spy_momentum: Decimal = Decimal("0")) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for symbol in CONTINUATION_SYMBOLS:
        values[f"{symbol}__close_completed_15m_v1"] = Decimal("100")
        values[f"{symbol}__momentum_z_60m_same_time_v1"] = Decimal("0")
        values[f"{symbol}__session_iex_vwap_v1"] = Decimal("100")
    values["SPY__momentum_z_60m_same_time_v1"] = spy_momentum
    values["SPY__close_completed_15m_v1"] = Decimal("101") if spy_momentum >= 0 else Decimal("99")
    return values


def _reversion_values(*, spy_deviation: Decimal = Decimal("0"), spy_momentum: Decimal = Decimal("0")) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for symbol in REVERSION_SYMBOLS:
        values[f"{symbol}__deviation_z_same_time_v1"] = Decimal("0")
        values[f"{symbol}__momentum_z_60m_same_time_v1"] = Decimal("0")
    values["SPY__deviation_z_same_time_v1"] = spy_deviation
    values["SPY__momentum_z_60m_same_time_v1"] = spy_momentum
    return values


@pytest.mark.parametrize(
    ("momentum", "close", "expected"),
    [
        (Decimal("1.00"), Decimal("101"), "BUY"),
        (Decimal("-1.00"), Decimal("99"), "SELL"),
        (Decimal("1.00"), Decimal("100"), "NO_TRADE"),
    ],
)
def test_continuation_signal_boundaries(momentum: Decimal, close: Decimal, expected: str) -> None:
    result = continuation_signal(
        underlying="SPY",
        features={
            "close_completed_15m_v1": close,
            "momentum_z_60m_same_time_v1": momentum,
            "session_iex_vwap_v1": Decimal("100"),
        },
    )
    assert result.action == expected


@pytest.mark.parametrize(
    ("deviation", "momentum", "expected"),
    [
        (Decimal("-1.50"), Decimal("0.49"), "BUY"),
        (Decimal("1.50"), Decimal("-0.49"), "SELL"),
        (Decimal("-1.50"), Decimal("0.50"), "NO_TRADE"),
    ],
)
def test_reversion_signal_boundaries(deviation: Decimal, momentum: Decimal, expected: str) -> None:
    result = reversion_signal(
        underlying="SPY",
        features={
            "deviation_z_same_time_v1": deviation,
            "momentum_z_60m_same_time_v1": momentum,
        },
    )
    assert result.action == expected


@pytest.mark.parametrize(
    ("plugin", "context", "config", "reason"),
    [
        (
            ContinuationPlugin(),
            lambda: _context(
                plugin_id="intraday_continuation",
                symbols=CONTINUATION_SYMBOLS,
                feature_hash=CONTINUATION_HASH,
                values=_continuation_values(spy_momentum=Decimal("1.2")),
                quality_flags=("EARLY_CLOSE_SESSION",),
            ),
            _config(momentum_threshold="1.00"),
            "EARLY_CLOSE_SESSION",
        ),
        (
            ReversionPlugin(),
            lambda: _context(
                plugin_id="vwap_reversion",
                symbols=REVERSION_SYMBOLS,
                feature_hash=REVERSION_HASH,
                values=_reversion_values(spy_deviation=Decimal("-2")),
                feature_available_time=AS_OF - timedelta(seconds=61),
            ),
            _config(deviation_threshold="1.50", momentum_neutral_abs_max="0.50"),
            "DATA_STALE",
        ),
    ],
)
def test_plugins_fail_closed(
    plugin: object,
    context: object,
    config: StrategyConfigV1,
    reason: str,
) -> None:
    evaluation = plugin.evaluate(context(), config)  # type: ignore[union-attr,operator]
    assert evaluation.decision.kind == "NO_TRADE"
    assert evaluation.decision.primary_reason_code == reason


def test_continuation_runtime_parity_and_determinism() -> None:
    context = _context(
        plugin_id="intraday_continuation",
        symbols=CONTINUATION_SYMBOLS,
        feature_hash=CONTINUATION_HASH,
        values=_continuation_values(spy_momentum=Decimal("1.25")),
    )
    config = _config(momentum_threshold="1.00")
    pure = continuation_signal(
        underlying="SPY",
        features={
            "close_completed_15m_v1": Decimal("101"),
            "momentum_z_60m_same_time_v1": Decimal("1.25"),
            "session_iex_vwap_v1": Decimal("100"),
        },
    )
    first = ContinuationPlugin().evaluate(context, config)
    second = ContinuationPlugin().evaluate(context, config)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.decision.kind == "ENTRY_TEMPLATE_REQUEST"
    assert first.decision.underlying == pure.underlying
    assert first.decision.entry_reason_codes == pure.reason_codes
    assert first.decision.template_id == "CALL_DEBIT_SPREAD_V1"
    assert first.decision.signal_strength_bucket == "MEDIUM"


def test_reversion_runtime_parity_and_daily_entry_refusal() -> None:
    values = _reversion_values(spy_deviation=Decimal("-1.50"), spy_momentum=Decimal("0.49"))
    context = _context(
        plugin_id="vwap_reversion",
        symbols=REVERSION_SYMBOLS,
        feature_hash=REVERSION_HASH,
        values=values,
    )
    config = _config(deviation_threshold="1.50", momentum_neutral_abs_max="0.50")
    pure = reversion_signal(
        underlying="SPY",
        features={
            "deviation_z_same_time_v1": Decimal("-1.50"),
            "momentum_z_60m_same_time_v1": Decimal("0.49"),
        },
    )
    evaluation = ReversionPlugin().evaluate(context, config)
    assert evaluation.decision.kind == "ENTRY_TEMPLATE_REQUEST"
    assert evaluation.decision.underlying == pure.underlying
    repeated = _context(
        plugin_id="vwap_reversion",
        symbols=REVERSION_SYMBOLS,
        feature_hash=REVERSION_HASH,
        values=values,
        payload={"last_entry_session_SPY": "2026-08-28"},
    )
    refusal = ReversionPlugin().evaluate(repeated, config)
    assert refusal.decision.kind == "NO_TRADE"
    assert refusal.decision.primary_reason_code == "DAILY_ENTRY_ALREADY_USED"
