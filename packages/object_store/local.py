"""Local fixture implementation of the immutable object-store port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.contracts.canonical import canonical_hash, canonical_json


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_json(self, payload: Any) -> str:
        encoded = canonical_json(payload)
        digest = canonical_hash(payload)
        path = self.root / f"{digest.removeprefix('sha256:')}.json"
        if path.exists() and path.read_text(encoding="utf-8") != encoded:
            raise ValueError("OBJECT_STORE_HASH_COLLISION")
        if not path.exists():
            path.write_text(encoded, encoding="utf-8")
        return digest

    def get_json(self, digest: str) -> str:
        path = self.root / f"{digest.removeprefix('sha256:')}.json"
        encoded = path.read_text(encoding="utf-8")
        if canonical_hash(__import__("json").loads(encoded)) != digest:
            raise ValueError("OBJECT_STORE_INTEGRITY_FAILURE")
        return encoded
