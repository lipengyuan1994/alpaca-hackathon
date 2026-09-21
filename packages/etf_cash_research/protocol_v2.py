"""Versioned protocol and typed study configuration for the extended ETF study.

The original ``protocol.py`` remains immutable for the v1 QQQM/SOXX/SMH
release.  This module owns the v2 multi-universe study defaults and hashes all
outcome-affecting settings before a run can start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

import yaml

from packages.contracts.canonical import canonical_hash


@dataclass(frozen=True)
class UniverseSpec:
    pair_id: str
    tradable_symbols: tuple[str, ...]
    signal_symbols: tuple[str, ...] = ()
    broad_proxy: str | None = None
    semiconductor_proxy: str | None = None
    track_id: str = "a"

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.tradable_symbols, *self.signal_symbols)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "track_id": self.track_id,
            "tradable_symbols": list(self.tradable_symbols),
            "signal_symbols": list(self.signal_symbols),
            "broad_proxy": self.broad_proxy,
            "semiconductor_proxy": self.semiconductor_proxy,
        }


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    strategy_id: str
    universe: UniverseSpec
    variant: str = "primary"
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strategy_id": self.strategy_id,
            "variant": self.variant,
            "parameters": dict(self.parameters),
            "universe": self.universe.as_dict(),
        }


@dataclass(frozen=True)
class CostScenarioV2:
    name: str
    basis_points_per_side: float
    sell_fee: float = 0.01

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "basis_points_per_side": self.basis_points_per_side, "sell_fee": self.sell_fee}


@dataclass(frozen=True)
class StudyProtocolV2:
    schema_version: str = "etf-cash-research-protocol/v2"
    initial_cash: float = 1000.0
    target_investment: float = 0.99
    minimum_order_notional: float = 5.0
    quantity_decimals: int = 6
    warmup_start: date = date(2022, 9, 1)
    primary_start: date = date(2023, 9, 19)
    primary_end: date = date(2026, 9, 18)
    settlement_change: date = date(2024, 5, 28)
    max_drawdown_track_a: float = 0.35
    max_drawdown_track_b: float = 0.50
    bootstrap_samples: int = 2000
    bootstrap_block_length: int = 20
    bootstrap_seed: int = 135
    costs_track_a: tuple[CostScenarioV2, ...] = field(default_factory=lambda: (
        CostScenarioV2("base", 5.0),
        CostScenarioV2("stress", 15.0),
        CostScenarioV2("severe", 30.0),
    ))
    costs_track_b: tuple[CostScenarioV2, ...] = field(default_factory=lambda: (
        CostScenarioV2("base", 10.0),
        CostScenarioV2("stress", 25.0),
        CostScenarioV2("severe", 50.0),
    ))
    delay_stress_sessions: int = 1
    stress_periods: tuple[tuple[str, date, date], ...] = (("stress_2020", date(2020, 1, 2), date(2020, 12, 31)), ("stress_2022", date(2022, 1, 3), date(2022, 12, 30)))
    evaluation_windows: tuple[tuple[str, date, date], ...] = (
        ("W1", date(2023, 9, 19), date(2024, 3, 18)),
        ("W2", date(2024, 3, 19), date(2024, 9, 18)),
        ("W3", date(2024, 9, 19), date(2025, 3, 18)),
        ("W4", date(2025, 3, 19), date(2025, 9, 18)),
        ("W5", date(2025, 9, 19), date(2026, 3, 18)),
        ("W6", date(2026, 3, 19), date(2026, 9, 18)),
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "initial_cash": self.initial_cash,
            "target_investment": self.target_investment,
            "minimum_order_notional": self.minimum_order_notional,
            "quantity_decimals": self.quantity_decimals,
            "dates": {
                "warmup_start": self.warmup_start.isoformat(),
                "primary_start": self.primary_start.isoformat(),
                "primary_end": self.primary_end.isoformat(),
            },
            "settlement_change": self.settlement_change.isoformat(),
            "max_drawdown": {"track_a": self.max_drawdown_track_a, "track_b": self.max_drawdown_track_b},
            "bootstrap": {"samples": self.bootstrap_samples, "block_length": self.bootstrap_block_length, "seed": self.bootstrap_seed},
            "costs": {
                "track_a": [item.as_dict() for item in self.costs_track_a],
                "track_b": [item.as_dict() for item in self.costs_track_b],
            },
            "delay_stress_sessions": self.delay_stress_sessions,
            "stress_periods": [{"id": key, "start": start.isoformat(), "end": end.isoformat()} for key, start, end in self.stress_periods],
            "evaluation_windows": [{"id": key, "start": start.isoformat(), "end": end.isoformat()} for key, start, end in self.evaluation_windows],
            "execution": {
                "signal_clock": "09:20 America/New_York",
                "information_cutoff": "previous_completed_session_close",
                "fill": "next_regular_session_open_with_adverse_cost",
                "mark": "regular_session_close",
                "reductions_before_increases": True,
                "unsettled_sale_proceeds_available": False,
                "cash_interest": 0.0,
            },
            "limitations": ["historical_periods_reused", "deterministic_backtest_only", "llm_overlay_not_performance_tested", "no_live_authority"],
        }

    @property
    def protocol_hash(self) -> str:
        return canonical_hash(self.as_dict())

    @classmethod
    def from_yaml(cls, path: str | Any) -> "StudyProtocolV2":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("ETF_V2_PROTOCOL_INVALID")
        dates = raw.get("dates", {})
        maximum = raw.get("max_drawdown", {})
        bootstrap = raw.get("bootstrap", {})
        costs = raw.get("costs", {})
        def cost_items(value: Any, fallback: tuple[CostScenarioV2, ...]) -> tuple[CostScenarioV2, ...]:
            if not isinstance(value, list) or not value:
                return fallback
            return tuple(CostScenarioV2(str(item["name"]), float(item["basis_points_per_side"]), float(item.get("sell_fee", 0.01))) for item in value)
        windows = raw.get("evaluation_windows")
        parsed_windows = tuple((str(item["id"]), date.fromisoformat(str(item["start"])), date.fromisoformat(str(item["end"]))) for item in windows) if isinstance(windows, list) else cls().evaluation_windows
        stress = raw.get("stress_periods")
        parsed_stress = tuple((str(item["id"]), date.fromisoformat(str(item["start"])), date.fromisoformat(str(item["end"]))) for item in stress) if isinstance(stress, list) else cls().stress_periods
        return cls(
            schema_version=str(raw.get("schema_version", cls.schema_version)),
            initial_cash=float(raw.get("initial_cash", 1000.0)),
            target_investment=float(raw.get("target_investment", 0.99)),
            minimum_order_notional=float(raw.get("minimum_order_notional", 5.0)),
            quantity_decimals=int(raw.get("quantity_decimals", 6)),
            warmup_start=date.fromisoformat(str(dates.get("warmup_start", "2022-09-01"))),
            primary_start=date.fromisoformat(str(dates.get("primary_start", "2023-09-19"))),
            primary_end=date.fromisoformat(str(dates.get("primary_end", "2026-09-18"))),
            settlement_change=date.fromisoformat(str(raw.get("settlement_change", "2024-05-28"))),
            max_drawdown_track_a=float(maximum.get("track_a", 0.35)),
            max_drawdown_track_b=float(maximum.get("track_b", 0.50)),
            bootstrap_samples=int(bootstrap.get("samples", 2000)),
            bootstrap_block_length=int(bootstrap.get("block_length", 20)),
            bootstrap_seed=int(bootstrap.get("seed", 135)),
            costs_track_a=cost_items(costs.get("track_a"), cls().costs_track_a),
            costs_track_b=cost_items(costs.get("track_b"), cls().costs_track_b),
            delay_stress_sessions=int(raw.get("delay_stress_sessions", 1)),
            stress_periods=parsed_stress,
            evaluation_windows=parsed_windows,
        )


TRACK_A_UNIVERSE = UniverseSpec("QQQM_SMH", ("QQQM", "SMH"), track_id="a")
TRACK_B1_UNIVERSE = UniverseSpec("TQQQ_SOXL", ("TQQQ", "SOXL"), ("QQQ", "SOXX"), "QQQ", "SOXX", "b")
TRACK_B2_UNIVERSE = UniverseSpec("SPXL_SOXL", ("SPXL", "SOXL"), ("SPY", "SOXX"), "SPY", "SOXX", "b")
DEFAULT_STUDY_PROTOCOL = StudyProtocolV2()


def candidate_specs(track: str = "all", *, include_sensitivities: bool = False) -> list[CandidateSpec]:
    """Return deterministic candidate definitions without consulting outcomes."""
    track = track.lower()
    if track not in {"a", "b", "all"}:
        raise ValueError("ETF_TRACK_INVALID")
    output: list[CandidateSpec] = []
    variants_a = {
        "A01": ("buffer_0.02", "buffer_0.04"), "A02": ("sma_40", "sma_60"),
        "A03": ("vol_42", "vol_84"), "A04": ("buffer_0.005", "buffer_0.015"),
        "A05": ("atr_2.5", "atr_3.5"), "A06": ("confirm_2", "confirm_4"),
        "A07": ("corr_0.80", "corr_0.90"), "A08": ("er_0.20", "er_0.40"),
        "A09": ("throttle_0.8", "throttle_1.2"), "A10": ("trend_share_0.70", "trend_share_0.90"),
    }
    variants_b = {
        "L01": ("long_105", "long_147"), "L02": ("vol_target_30", "vol_target_50"),
        "L03": ("ema_80", "ema_120"), "L04": ("vol_target_30", "vol_target_50"),
        "L05": ("atr_2.5", "atr_3.5"), "L06": ("rsi_5", "rsi_15"),
        "L07": ("contraction_15", "contraction_25"), "L08": ("vol_target_40", "vol_target_60"),
        "L09": ("cooldown_5", "cooldown_15"), "L10": ("trend_share_60", "trend_share_80"),
    }
    if track in {"a", "all"}:
        for strategy_id in (f"A{index:02d}" for index in range(1, 11)):
            output.append(CandidateSpec(f"{strategy_id}__QQQM_SMH__primary", strategy_id, TRACK_A_UNIVERSE))
            if include_sensitivities:
                output.extend(CandidateSpec(f"{strategy_id}__QQQM_SMH__{variant}", strategy_id, TRACK_A_UNIVERSE, variant) for variant in variants_a[strategy_id])
    if track in {"b", "all"}:
        for strategy_id in (f"L{index:02d}" for index in range(1, 11)):
            for universe in (TRACK_B1_UNIVERSE, TRACK_B2_UNIVERSE):
                output.append(CandidateSpec(f"{strategy_id}__{universe.pair_id}__primary", strategy_id, universe))
                if include_sensitivities:
                    output.extend(CandidateSpec(f"{strategy_id}__{universe.pair_id}__{variant}", strategy_id, universe, variant) for variant in variants_b[strategy_id])
    return output


def protocol_envelope(protocol: StudyProtocolV2 = DEFAULT_STUDY_PROTOCOL) -> dict[str, Any]:
    payload = {"protocol": protocol.as_dict(), "protocol_hash": protocol.protocol_hash, "candidate_registry": [item.as_dict() for item in candidate_specs()]}
    payload["envelope_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "envelope_hash"})
    return payload
