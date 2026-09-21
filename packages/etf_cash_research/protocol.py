"""Frozen protocol and candidate catalogue for the ETF cash study."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

import yaml

from packages.contracts.canonical import canonical_hash

SYMBOLS = ("QQQM", "SOXX", "SMH", "QQQ", "SPY")
PAIR_SYMBOLS = ("QQQM", "S")
STRATEGY_IDS = tuple(f"S{index:02d}" for index in range(1, 11))


@dataclass(frozen=True)
class CostScenario:
    name: str
    basis_points_per_side: float
    sell_fee: float = 0.01


@dataclass(frozen=True)
class ResearchProtocol:
    """All outcome-affecting defaults, serialized canonically."""

    schema_version: str = "etf-cash-research-protocol/v1"
    initial_cash: float = 1000.0
    target_investment: float = 0.99
    minimum_order_notional: float = 5.0
    quantity_decimals: int = 6
    warmup_start: date = date(2022, 9, 1)
    development_start: date = date(2023, 9, 19)
    development_end: date = date(2024, 9, 18)
    validation_start: date = date(2024, 9, 19)
    validation_end: date = date(2025, 9, 18)
    holdout_start: date = date(2025, 9, 19)
    holdout_end: date = date(2026, 9, 18)
    settlement_change: date = date(2024, 5, 28)
    bootstrap_samples: int = 2000
    bootstrap_block_length: int = 20
    bootstrap_seed: int = 135
    max_drawdown: float = 0.35
    costs: tuple[CostScenario, ...] = field(
        default_factory=lambda: (
            CostScenario("base", 5.0),
            CostScenario("stress", 15.0),
            CostScenario("severe", 30.0),
        )
    )
    symbols: tuple[str, ...] = SYMBOLS

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "initial_cash": self.initial_cash,
            "target_investment": self.target_investment,
            "minimum_order_notional": self.minimum_order_notional,
            "quantity_decimals": self.quantity_decimals,
            "dates": {
                "warmup_start": self.warmup_start.isoformat(),
                "development_start": self.development_start.isoformat(),
                "development_end": self.development_end.isoformat(),
                "validation_start": self.validation_start.isoformat(),
                "validation_end": self.validation_end.isoformat(),
                "holdout_start": self.holdout_start.isoformat(),
                "holdout_end": self.holdout_end.isoformat(),
            },
            "settlement_change": self.settlement_change.isoformat(),
            "bootstrap": {
                "samples": self.bootstrap_samples,
                "block_length": self.bootstrap_block_length,
                "seed": self.bootstrap_seed,
            },
            "max_drawdown": self.max_drawdown,
            "costs": [
                {"name": item.name, "basis_points_per_side": item.basis_points_per_side, "sell_fee": item.sell_fee}
                for item in self.costs
            ],
            "symbols": list(self.symbols),
            "execution": {
                "signal_clock": "09:20 America/New_York",
                "signal_information_cutoff": "previous_completed_session_close",
                "fill": "next_regular_session_open_with_adverse_cost",
                "delay_stress_sessions": 1,
                "mark": "regular_session_close",
                "reductions_before_increases": True,
                "unsettled_sale_proceeds_available": False,
                "cash_interest": 0.0,
            },
            "limitations": [
                "deterministic_backtest_only",
                "llm_overlay_not_performance_tested",
                "no_live_authority",
                "opening_price_is_execution_proxy",
            ],
        }

    @property
    def protocol_hash(self) -> str:
        return canonical_hash(self.as_dict())

    @classmethod
    def from_yaml(cls, path: str | Any) -> "ResearchProtocol":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("ETF_PROTOCOL_INVALID")
        dates = raw.get("dates", {})
        bootstrap = raw.get("bootstrap", {})
        costs = tuple(
            CostScenario(str(item["name"]), float(item["basis_points_per_side"]), float(item.get("sell_fee", 0.01)))
            for item in raw.get("costs", [])
        )
        if not costs:
            costs = cls().costs
        return cls(
            schema_version=str(raw.get("schema_version", cls.schema_version)),
            initial_cash=float(raw.get("initial_cash", 1000.0)),
            target_investment=float(raw.get("target_investment", 0.99)),
            minimum_order_notional=float(raw.get("minimum_order_notional", 5.0)),
            quantity_decimals=int(raw.get("quantity_decimals", 6)),
            warmup_start=date.fromisoformat(str(dates.get("warmup_start", "2022-09-01"))),
            development_start=date.fromisoformat(str(dates.get("development_start", "2023-09-19"))),
            development_end=date.fromisoformat(str(dates.get("development_end", "2024-09-18"))),
            validation_start=date.fromisoformat(str(dates.get("validation_start", "2024-09-19"))),
            validation_end=date.fromisoformat(str(dates.get("validation_end", "2025-09-18"))),
            holdout_start=date.fromisoformat(str(dates.get("holdout_start", "2025-09-19"))),
            holdout_end=date.fromisoformat(str(dates.get("holdout_end", "2026-09-18"))),
            settlement_change=date.fromisoformat(str(raw.get("settlement_change", "2024-05-28"))),
            bootstrap_samples=int(bootstrap.get("samples", 2000)),
            bootstrap_block_length=int(bootstrap.get("block_length", 20)),
            bootstrap_seed=int(bootstrap.get("seed", 135)),
            max_drawdown=float(raw.get("max_drawdown", 0.35)),
            costs=costs,
            symbols=tuple(str(item).upper() for item in raw.get("symbols", SYMBOLS)),
        )


def candidate_registry() -> dict[str, Any]:
    sensitivity = {
        "S01": ["momentum_105", "momentum_147"],
        "S02": ["ema_80", "ema_120"],
        "S03": ["breakout_40", "breakout_70"],
        "S04": ["rsi_5", "rsi_15"],
        "S05": ["band_1.75", "band_2.25"],
        "S06": ["ratio_sma_15", "ratio_sma_25"],
        "S07": ["vol_target_20", "vol_target_30"],
        "S08": ["breakout_15", "breakout_25"],
        "S09": ["contraction_15", "contraction_25"],
        "S10": ["trend_share_60", "trend_share_80"],
    }
    names = {
        "S01": "dual_momentum_rotation",
        "S02": "two_sleeve_moving_average_trend",
        "S03": "donchian_breakout",
        "S04": "pullback_in_uptrend",
        "S05": "bollinger_band_recovery",
        "S06": "semiconductor_leadership_switch",
        "S07": "volatility_controlled_growth",
        "S08": "qqqm_core_semiconductor_breakout",
        "S09": "volatility_contraction_breakout",
        "S10": "trend_pullback_ensemble",
    }
    rows = []
    for strategy_id in STRATEGY_IDS:
        for semiconductor in ("SOXX", "SMH"):
            rows.append(
                {
                    "candidate_id": f"{strategy_id}__QQQM_{semiconductor}",
                    "strategy_id": strategy_id,
                    "name": names[strategy_id],
                    "semiconductor": semiconductor,
                    "primary": True,
                }
            )
    return {
        "schema_version": "etf-cash-candidate-registry/v1",
        "candidate_count": len(rows),
        "candidates": rows,
        "sensitivity": sensitivity,
        "selection": {
            "validation_positive_base_and_stress": True,
            "validation_max_drawdown": 0.35,
            "ranking": ["validation_base_net_return", "lower_drawdown", "lower_turnover", "candidate_id"],
            "holdout_shortlist_size": 3,
        },
    }


DEFAULT_PROTOCOL = ResearchProtocol()
