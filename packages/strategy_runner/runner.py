"""Host-side resource-limited subprocess runner with output authorization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from packages.contracts.canonical import canonical_json
from packages.contracts.models import (
    EntryTemplateRequestV1,
    PositionDirectiveV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
)
from packages.plugin_integrity import PluginIntegrityError, calculate_plugin_content_hash

from .models import PluginAuthorization, PluginRequest, PluginResponse


class PluginIsolationError(RuntimeError):
    """The runner refused or could not safely execute a plug-in."""


class PluginAuthorizationError(PluginIsolationError):
    """A plug-in response did not match its host-owned authorization."""


@dataclass(frozen=True)
class RunnerLimits:
    wall_time_seconds: float = 2.0
    cpu_seconds: int = 1
    max_output_bytes: int = 128_000
    memory_bytes: int = 256 * 1024 * 1024


def _limit_process(limits: RunnerLimits) -> None:
    """Apply mandatory POSIX limits; any unavailable limit aborts child setup."""
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - guarded by the host POSIX check
        raise RuntimeError("PLUGIN_RESOURCE_LIMITS_UNAVAILABLE") from exc

    required_limits = [
        "RLIMIT_CPU",
        "RLIMIT_FSIZE",
        "RLIMIT_NOFILE",
        "RLIMIT_CORE",
    ]
    if sys.platform.startswith("linux"):
        required_limits.append("RLIMIT_AS")
    elif sys.platform != "darwin":
        raise RuntimeError("PLUGIN_RESOURCE_LIMITS_UNAVAILABLE")
    if any(not hasattr(resource, name) for name in required_limits):
        raise RuntimeError("PLUGIN_RESOURCE_LIMITS_UNAVAILABLE")
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_output_bytes, limits.max_output_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))


def _tuple_key(item: Any) -> tuple[str, str, str, int]:
    return (
        item.template_id,
        item.horizon_bucket,
        item.risk_tier,
        item.max_intent_ttl_seconds,
    )


def validate_plugin_response(
    response: PluginResponse,
    *,
    authorization: PluginAuthorization,
    context: StrategyContextV1,
    config: StrategyConfigV1,
) -> StrategyEvaluationV1:
    """Bind every response field to the host-owned request and registry authority."""
    if response.metadata != authorization.expected_metadata:
        raise PluginAuthorizationError("PLUGIN_METADATA_MISMATCH")
    if response.data_requirements != authorization.expected_data_requirements:
        raise PluginAuthorizationError("PLUGIN_DATA_REQUIREMENTS_MISMATCH")
    if config.config_hash != authorization.config_hash or context.config_hash != config.config_hash:
        raise PluginAuthorizationError("PLUGIN_CONFIG_BINDING_MISMATCH")
    if tuple(map(_tuple_key, context.allowed_intent_tuples)) != tuple(
        map(_tuple_key, authorization.allowed_intent_tuples)
    ):
        raise PluginAuthorizationError("PLUGIN_CONTEXT_AUTHORITY_MISMATCH")
    if (
        context.prior_state.plugin_id != authorization.expected_metadata.plugin_id
        or context.prior_state.plugin_version != authorization.expected_metadata.plugin_version
    ):
        raise PluginAuthorizationError("PLUGIN_PRIOR_STATE_IDENTITY_MISMATCH")

    evaluation = response.evaluation
    if evaluation.evaluation_id != context.evaluation_id:
        raise PluginAuthorizationError("PLUGIN_EVALUATION_ID_MISMATCH")
    if (
        evaluation.plugin_id != authorization.expected_metadata.plugin_id
        or evaluation.plugin_version != authorization.expected_metadata.plugin_version
    ):
        raise PluginAuthorizationError("PLUGIN_EVALUATION_IDENTITY_MISMATCH")
    if evaluation.plugin_content_hash != authorization.content_hash:
        raise PluginAuthorizationError("PLUGIN_CONTENT_HASH_MISMATCH")
    if evaluation.context_hash != context.context_hash:
        raise PluginAuthorizationError("PLUGIN_CONTEXT_HASH_MISMATCH")
    if evaluation.config_hash != config.config_hash:
        raise PluginAuthorizationError("PLUGIN_CONFIG_HASH_MISMATCH")
    if (
        evaluation.next_state.plugin_id != authorization.expected_metadata.plugin_id
        or evaluation.next_state.plugin_version != authorization.expected_metadata.plugin_version
    ):
        raise PluginAuthorizationError("PLUGIN_NEXT_STATE_IDENTITY_MISMATCH")
    if evaluation.next_state.sequence != context.prior_state.sequence + 1:
        raise PluginAuthorizationError("PLUGIN_NEXT_STATE_SEQUENCE_MISMATCH")
    if evaluation.next_state.as_of.astimezone(UTC) != context.as_of.astimezone(UTC):
        raise PluginAuthorizationError("PLUGIN_NEXT_STATE_TIME_MISMATCH")

    decision = evaluation.decision
    if isinstance(decision, EntryTemplateRequestV1):
        if decision.underlying not in authorization.allowed_underlyings:
            raise PluginAuthorizationError("PLUGIN_UNDERLYING_NOT_AUTHORIZED")
        matches = [
            item
            for item in authorization.allowed_intent_tuples
            if item.template_id == decision.template_id
            and item.horizon_bucket == decision.horizon_bucket
            and item.risk_tier == decision.risk_tier
        ]
        if len(matches) != 1:
            raise PluginAuthorizationError("PLUGIN_INTENT_TUPLE_NOT_AUTHORIZED")
        ttl_seconds = (decision.intent_expires_at - context.as_of).total_seconds()
        if ttl_seconds <= 0 or ttl_seconds > matches[0].max_intent_ttl_seconds:
            raise PluginAuthorizationError("PLUGIN_INTENT_TTL_NOT_AUTHORIZED")
    elif isinstance(decision, PositionDirectiveV1):
        raise PluginAuthorizationError("PLUGIN_POSITION_DIRECTIVE_FORBIDDEN_IN_ENTRY_ONLY_V1")
    return evaluation


def run_plugin(
    *,
    authorization: PluginAuthorization,
    context: StrategyContextV1,
    config: StrategyConfigV1,
    limits: RunnerLimits = RunnerLimits(),
) -> StrategyEvaluationV1:
    """Evaluate one preauthorized plug-in through canonical JSON stdin/stdout."""
    if os.name != "posix":
        raise PluginIsolationError("PLUGIN_RESOURCE_LIMITS_UNAVAILABLE")
    if config.config_hash != authorization.config_hash or context.config_hash != config.config_hash:
        raise PluginAuthorizationError("PLUGIN_CONFIG_BINDING_MISMATCH")
    if (
        context.prior_state.plugin_id != authorization.expected_metadata.plugin_id
        or context.prior_state.plugin_version != authorization.expected_metadata.plugin_version
    ):
        raise PluginAuthorizationError("PLUGIN_PRIOR_STATE_IDENTITY_MISMATCH")
    authorized_keys = tuple(map(_tuple_key, authorization.allowed_intent_tuples))
    if tuple(map(_tuple_key, context.allowed_intent_tuples)) != authorized_keys:
        raise PluginAuthorizationError("PLUGIN_CONTEXT_AUTHORITY_MISMATCH")
    requirements = authorization.expected_data_requirements
    if context.feature_contract_hash != requirements.feature_contract_hash:
        raise PluginAuthorizationError("PLUGIN_FEATURE_CONTRACT_MISMATCH")
    if tuple(context.universe_features) != requirements.required_feature_keys:
        raise PluginAuthorizationError("PLUGIN_FEATURE_AUTHORITY_MISMATCH")
    if any(not value.is_finite() for value in context.universe_features.values()):
        raise PluginAuthorizationError("PLUGIN_FEATURE_NONFINITE")
    feature_age = (context.as_of - context.feature_available_time).total_seconds()
    if feature_age < 0 or feature_age > requirements.maximum_observation_age_seconds:
        raise PluginAuthorizationError("PLUGIN_FEATURE_STALE_OR_FUTURE")

    request = PluginRequest(
        authorization=authorization,
        context=context,
        config=config,
    )
    source_root = Path(__file__).resolve().parents[2]
    child = source_root / "packages" / "strategy_runner" / "child.py"
    with tempfile.TemporaryDirectory(prefix="strategy-runner-") as workdir:
        try:
            current_content_hash = calculate_plugin_content_hash(
                authorization.entrypoint,
                repository_root=source_root,
            )
        except PluginIntegrityError as exc:
            raise PluginAuthorizationError(
                "PLUGIN_SOURCE_CHANGED_AFTER_AUTHORIZATION"
            ) from exc
        if current_content_hash != authorization.content_hash:
            raise PluginAuthorizationError("PLUGIN_SOURCE_CHANGED_AFTER_AUTHORIZATION")
        try:
            completed = subprocess.run(
                [sys.executable, str(child)],
                input=canonical_json(request).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env={},
                close_fds=True,
                timeout=limits.wall_time_seconds,
                check=False,
                preexec_fn=lambda: _limit_process(limits),
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginIsolationError("PLUGIN_TIMEOUT") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise PluginIsolationError("PLUGIN_RESOURCE_LIMIT_SETUP_FAILED") from exc
    if len(completed.stdout) > limits.max_output_bytes or len(completed.stderr) > limits.max_output_bytes:
        raise PluginIsolationError("PLUGIN_OUTPUT_LIMIT_EXCEEDED")
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()[:4_000]
        raise PluginIsolationError(
            f"PLUGIN_ISOLATION_FAILURE: {diagnostic or 'runner exited non-zero'}"
        )
    try:
        parsed = json.loads(completed.stdout)
        response = PluginResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise PluginIsolationError("PLUGIN_INVALID_OUTPUT") from exc
    return validate_plugin_response(
        response,
        authorization=authorization,
        context=context,
        config=config,
    )
