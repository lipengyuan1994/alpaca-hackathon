from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import packages.strategy_runner.runner as runner_module
from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.contracts.models import (
    DataRequirementsV1,
    IntentTupleV1,
    OperatingModeV1,
    PositionDirectiveV1,
    PositionPolicyIdV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)
from packages.decision_core.registry import (
    RegistryError,
    StrategyRegistry,
    default_registry,
    load_registry,
)
from packages.strategy_runner import (
    PluginAuthorization,
    PluginAuthorizationError,
    PluginIsolationError,
    PluginResponse,
    run_plugin,
    validate_plugin_response,
)

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "configs" / "strategy_registry.yaml"


def _authorization(plugin_id: str = "regime_momentum") -> PluginAuthorization:
    registry = default_registry()
    entry = registry.entry(plugin_id, "1.0.0")
    return PluginAuthorization(
        registry_hash=registry.registry_hash,
        entrypoint=entry.entrypoint,
        content_hash=entry.content_hash,
        expected_metadata=entry.expected_metadata,
        expected_data_requirements=entry.data_requirements,
        config_hash=entry.config_hash,
        allowed_underlyings=entry.allowed_underlyings,
        allowed_intent_tuples=entry.allowed_intent_tuples,
    )


@pytest.fixture(scope="module")
def authorized_case():
    *_, context, config, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, _ = pair
    authorization = _authorization()
    response = PluginResponse(
        metadata=authorization.expected_metadata,
        data_requirements=authorization.expected_data_requirements,
        evaluation=evaluation,
    )
    return context, config, authorization, response


def _rebuild_evaluation(
    evaluation: StrategyEvaluationV1,
    **updates: object,
) -> StrategyEvaluationV1:
    payload = evaluation.model_dump(mode="json", exclude={"evaluation_hash"})
    payload.update(updates)
    return StrategyEvaluationV1.model_validate(payload)


def test_registry_loads_one_schema_validated_authority_and_external_source_hash() -> None:
    registry = default_registry()
    entry = registry.entry("regime_momentum", "1.0.0")
    assert entry.content_hash != "sha256:" + "0" * 64

    *_, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, _ = pair
    assert evaluation.plugin_content_hash == entry.content_hash


@pytest.mark.parametrize(
    "yaml_body,reason",
    [
        ("version: strategy-registry/v1\nentries: [\n", "REGISTRY_YAML_INVALID"),
        (
            "version: strategy-registry/v1\nentries:\n  - plugin_id: incomplete\n",
            "REGISTRY_SCHEMA_INVALID",
        ),
    ],
)
def test_registry_rejects_yaml_or_schema_failure(
    tmp_path: Path,
    yaml_body: str,
    reason: str,
) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text(yaml_body, encoding="utf-8")
    with pytest.raises(RegistryError, match=reason):
        load_registry(path, repository_root=ROOT)


def test_registry_rejects_pinned_source_hash_tampering(tmp_path: Path) -> None:
    registry = default_registry()
    pinned = registry.entry("always_no_trade", "1.0.0").content_hash
    body = REGISTRY_PATH.read_text(encoding="utf-8").replace(
        pinned,
        "sha256:" + "f" * 64,
        1,
    )
    path = tmp_path / "registry.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(RegistryError, match="REGISTRY_CONTENT_HASH_MISMATCH"):
        load_registry(path, repository_root=ROOT)


def test_registry_requires_a_concrete_central_position_policy(tmp_path: Path) -> None:
    body = REGISTRY_PATH.read_text(encoding="utf-8").replace(
        "position_policy_ref: TREND_VWAP_OR_60M_V1",
        "position_policy_ref: unknown-policy",
        1,
    )
    path = tmp_path / "registry.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(RegistryError, match="REGISTRY_SCHEMA_INVALID"):
        load_registry(path, repository_root=ROOT)

    entry = default_registry().entry("regime_momentum", "1.0.0")
    assert entry.position_policy_ref is PositionPolicyIdV1.TREND_VWAP_OR_60M_V1


def test_registry_hash_covers_every_authority_field() -> None:
    registry = default_registry()
    first = registry.entry("always_no_trade", "1.0.0")
    second = registry.entry("regime_momentum", "1.0.0")
    mutations = (
        replace(first, plugin_id="different_plugin"),
        replace(first, plugin_version="1.0.1"),
        replace(first, entrypoint="strategy_plugins.other_v1.plugin:Plugin"),
        replace(first, content_hash="sha256:" + "9" * 64),
        replace(first, lifecycle="research_only"),
        replace(first, authority="different-authority"),
        replace(first, owner="different-owner"),
        replace(first, reviewer="different-reviewer"),
        replace(first, economic_hypothesis_id="different-hypothesis"),
        replace(first, allowed_underlyings=("SPY", "QQQ")),
        replace(
            first,
            allowed_intent_tuples=(
                IntentTupleV1(
                    template_id="CALL_DEBIT_SPREAD_V1",
                    horizon_bucket="INTRADAY_15_60M",
                    risk_tier="STANDARD",
                    max_intent_ttl_seconds=300,
                ),
            ),
        ),
        replace(first, config_hash="sha256:" + "a" * 64),
        replace(first, promotion_evidence_hash="sha256:" + "b" * 64),
        replace(first, position_policy_ref=PositionPolicyIdV1.REVERSION_VWAP_TOUCH_OR_60M_V1),
        replace(
            first,
            data_requirements=DataRequirementsV1(
                underlyings=("SPY",),
                feature_contract_hash=first.data_requirements.feature_contract_hash,
                required_feature_keys=(),
                maximum_observation_age_seconds=120,
            ),
        ),
    )
    for changed in mutations:
        assert StrategyRegistry((changed, second)).registry_hash != registry.registry_hash


def test_registry_rejects_config_and_lifecycle_before_evaluation() -> None:
    registry = default_registry()
    with pytest.raises(RegistryError, match="REGISTRY_CONFIG_HASH_MISMATCH"):
        registry.authorize(
            "regime_momentum",
            "1.0.0",
            config_hash="sha256:" + "f" * 64,
            mode=OperatingModeV1.PAPER_DEMO_ARMED,
        )
    entry = registry.entry("regime_momentum", "1.0.0")
    with pytest.raises(RegistryError, match="REGISTRY_LIFECYCLE_MODE_REJECTED"):
        registry.authorize(
            entry.plugin_id,
            entry.plugin_version,
            config_hash=entry.config_hash,
            mode=OperatingModeV1.PAPER_ARMED,
        )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("evaluation_id", "different-evaluation", "PLUGIN_EVALUATION_ID_MISMATCH"),
        ("context_hash", "sha256:" + "a" * 64, "PLUGIN_CONTEXT_HASH_MISMATCH"),
        ("config_hash", "sha256:" + "b" * 64, "PLUGIN_CONFIG_HASH_MISMATCH"),
        ("plugin_content_hash", "sha256:" + "c" * 64, "PLUGIN_CONTENT_HASH_MISMATCH"),
    ],
)
def test_host_rejects_tampered_evaluation_bindings(
    authorized_case,
    field: str,
    value: str,
    reason: str,
) -> None:
    context, config, authorization, response = authorized_case
    evaluation = _rebuild_evaluation(response.evaluation, **{field: value})
    tampered = response.model_copy(update={"evaluation": evaluation})
    with pytest.raises(PluginAuthorizationError, match=reason):
        validate_plugin_response(
            tampered,
            authorization=authorization,
            context=context,
            config=config,
        )


def test_host_rejects_tampered_plugin_identity(authorized_case) -> None:
    context, config, authorization, response = authorized_case
    next_state = response.evaluation.next_state.model_copy(
        update={"plugin_id": "always_no_trade", "state_hash": None}
    )
    evaluation = _rebuild_evaluation(
        response.evaluation,
        plugin_id="always_no_trade",
        next_state=next_state,
    )
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_EVALUATION_IDENTITY_MISMATCH"):
        validate_plugin_response(
            response.model_copy(update={"evaluation": evaluation}),
            authorization=authorization,
            context=context,
            config=config,
        )


@pytest.mark.parametrize(
    ("state_update", "reason"),
    [
        ({"sequence": 9, "state_hash": None}, "PLUGIN_NEXT_STATE_SEQUENCE_MISMATCH"),
        (
            {"as_of": FIXTURE_TIME + timedelta(seconds=1), "state_hash": None},
            "PLUGIN_NEXT_STATE_TIME_MISMATCH",
        ),
    ],
)
def test_host_rejects_tampered_next_state(
    authorized_case,
    state_update: dict[str, object],
    reason: str,
) -> None:
    context, config, authorization, response = authorized_case
    next_state = response.evaluation.next_state.model_copy(update=state_update)
    evaluation = _rebuild_evaluation(response.evaluation, next_state=next_state)
    with pytest.raises(PluginAuthorizationError, match=reason):
        validate_plugin_response(
            response.model_copy(update={"evaluation": evaluation}),
            authorization=authorization,
            context=context,
            config=config,
        )


def test_host_rejects_unauthorized_underlying_and_tuple(authorized_case) -> None:
    context, config, authorization, response = authorized_case
    unauthorized_underlying = response.evaluation.decision.model_copy(
        update={"underlying": "SMH"}
    )
    evaluation = _rebuild_evaluation(
        response.evaluation,
        decision=unauthorized_underlying,
    )
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_UNDERLYING_NOT_AUTHORIZED"):
        validate_plugin_response(
            response.model_copy(update={"evaluation": evaluation}),
            authorization=authorization,
            context=context,
            config=config,
        )

    unauthorized_tuple = response.evaluation.decision.model_copy(
        update={"template_id": "PUT_DEBIT_SPREAD_V1"}
    )
    evaluation = _rebuild_evaluation(response.evaluation, decision=unauthorized_tuple)
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_INTENT_TUPLE_NOT_AUTHORIZED"):
        validate_plugin_response(
            response.model_copy(update={"evaluation": evaluation}),
            authorization=authorization,
            context=context,
            config=config,
        )


def test_host_rejects_metadata_and_data_requirement_tampering(authorized_case) -> None:
    context, config, authorization, response = authorized_case
    metadata = StrategyMetadataV1(
        plugin_id="regime_momentum",
        plugin_version="1.0.0",
        owner="unregistered-owner",
        economic_hypothesis_id="H1_NORMALIZED_INTRADAY_CONTINUATION",
    )
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_METADATA_MISMATCH"):
        validate_plugin_response(
            response.model_copy(update={"metadata": metadata}),
            authorization=authorization,
            context=context,
            config=config,
        )

    requirements = DataRequirementsV1(
        underlyings=("SPY",),
        feature_contract_hash=authorization.expected_data_requirements.feature_contract_hash,
        required_feature_keys=(),
        maximum_observation_age_seconds=60,
    )
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_DATA_REQUIREMENTS_MISMATCH"):
        validate_plugin_response(
            response.model_copy(update={"data_requirements": requirements}),
            authorization=authorization,
            context=context,
            config=config,
        )


def test_host_rejects_context_authority_tampering_before_plugin_use(authorized_case) -> None:
    context, config, authorization, _ = authorized_case
    unauthorized = IntentTupleV1(
        template_id="PUT_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="TINY",
        max_intent_ttl_seconds=300,
    )
    payload = context.model_dump(mode="json", exclude={"context_hash"})
    payload["allowed_intent_tuples"] = [unauthorized.model_dump(mode="json")]
    altered_context = StrategyContextV1.model_validate(payload)
    with pytest.raises(PluginAuthorizationError, match="PLUGIN_CONTEXT_AUTHORITY_MISMATCH"):
        run_plugin(
            authorization=authorization,
            context=altered_context,
            config=config,
        )


def test_entry_only_freeze_rejects_every_position_directive(authorized_case) -> None:
    context, config, authorization, response = authorized_case
    requirements = authorization.expected_data_requirements.model_copy(
        update={"needs_logical_positions": True}
    )
    position_authorization = authorization.model_copy(
        update={"expected_data_requirements": requirements}
    )
    directive = PositionDirectiveV1(
        strategy_position_id="position-1",
        action="HOLD",
        urgency="NORMAL",
        reason_codes=("PLUGIN_ATTEMPTED_POSITION_OWNERSHIP",),
        directive_expires_at=context.as_of + timedelta(seconds=30),
    )
    evaluation = _rebuild_evaluation(response.evaluation, decision=directive)
    position_response = response.model_copy(
        update={"data_requirements": requirements, "evaluation": evaluation}
    )

    with pytest.raises(
        PluginAuthorizationError,
        match="PLUGIN_POSITION_DIRECTIVE_FORBIDDEN_IN_ENTRY_ONLY_V1",
    ):
        validate_plugin_response(
            position_response,
            authorization=position_authorization,
            context=context,
            config=config,
        )


def test_runner_rehashes_source_before_spawning(authorized_case, monkeypatch) -> None:
    context, config, authorization, _ = authorized_case
    monkeypatch.setattr(
        runner_module,
        "calculate_plugin_content_hash",
        lambda *_args, **_kwargs: "sha256:" + "0" * 64,
    )

    def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("source mismatch must be rejected before subprocess spawn")

    monkeypatch.setattr(runner_module.subprocess, "run", unexpected_spawn)
    with pytest.raises(
        PluginAuthorizationError,
        match="PLUGIN_SOURCE_CHANGED_AFTER_AUTHORIZATION",
    ):
        run_plugin(authorization=authorization, context=context, config=config)


def test_process_limit_setup_does_not_swallow_failures(monkeypatch) -> None:
    import resource

    def fail_setrlimit(*_args, **_kwargs) -> None:
        raise OSError("setrlimit denied")

    monkeypatch.setattr(resource, "setrlimit", fail_setrlimit)
    with pytest.raises(OSError, match="setrlimit denied"):
        runner_module._limit_process(runner_module.RunnerLimits())


def test_runner_surfaces_resource_setup_failure_stably(authorized_case, monkeypatch) -> None:
    context, config, authorization, _ = authorized_case

    def fail_spawn(*_args, **_kwargs):
        raise runner_module.subprocess.SubprocessError("Exception occurred in preexec_fn")

    monkeypatch.setattr(runner_module.subprocess, "run", fail_spawn)
    with pytest.raises(PluginIsolationError, match="PLUGIN_RESOURCE_LIMIT_SETUP_FAILED"):
        run_plugin(authorization=authorization, context=context, config=config)
