"""Canonical serialization used for immutable payload hashes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="json", exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("decimal values must be finite")
        return format(value, "f")
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Return the single JSON representation permitted in hash-bearing records."""
    return json.dumps(
        _canonical_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: Any) -> str:
    """Return a tagged SHA-256 digest of canonical JSON."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_without(value: BaseModel | dict[str, Any], *fields: str) -> str:
    """Hash a payload after omitting its self-referential derived fields."""
    if isinstance(value, BaseModel):
        body = value.model_dump(mode="json", exclude_none=False)
    else:
        body = dict(value)
    for field in fields:
        body.pop(field, None)
    return canonical_hash(body)
