from __future__ import annotations

import json

import pytest

from packages.contracts.canonical import canonical_hash, canonical_json
from packages.object_store.s3 import S3ObjectStore


class _Body:
    def __init__(self, value: str) -> None:
        self.value = value

    def read(self) -> bytes:
        return self.value.encode("utf-8")


class _NoSuchKey(Exception):
    pass


class _Exceptions:
    NoSuchKey = _NoSuchKey


class FakeS3:
    exceptions = _Exceptions

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], str] = {}
        self.put_calls: list[dict[str, object]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _Body]:
        value = self.objects.get((Bucket, Key))
        if value is None:
            raise _NoSuchKey()
        return {"Body": _Body(value)}

    def put_object(self, **kwargs: object) -> None:
        self.put_calls.append(kwargs)
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = body.decode("utf-8")


def test_put_json_uses_content_hash_as_key() -> None:
    client = FakeS3()
    store = S3ObjectStore("artifacts", client=client, prefix="evidence")
    payload = {"run_id": "run-1", "value": 42}

    digest = store.put_json(payload)

    assert digest == canonical_hash(payload)
    assert list(client.objects) == [
        ("artifacts", f"evidence/{digest.removeprefix('sha256:')}.json")
    ]
    assert client.put_calls[0]["ContentType"] == "application/json"


def test_put_json_is_idempotent_for_same_content() -> None:
    client = FakeS3()
    store = S3ObjectStore("artifacts", client=client)
    payload = {"a": 1, "b": [2, 3]}

    digest = store.put_json(payload)
    second_digest = store.put_json(json.loads(canonical_json(payload)))

    assert second_digest == digest
    assert len(client.put_calls) == 1


def test_put_json_rejects_hash_collision() -> None:
    client = FakeS3()
    store = S3ObjectStore("artifacts", client=client)
    payload = {"a": 1}
    digest = canonical_hash(payload)
    key = f"{digest.removeprefix('sha256:')}.json"
    client.objects[("artifacts", key)] = '{"different":true}'

    with pytest.raises(ValueError, match="OBJECT_STORE_HASH_COLLISION"):
        store.put_json(payload)


def test_get_json_verifies_integrity() -> None:
    client = FakeS3()
    store = S3ObjectStore("artifacts", client=client)
    payload = {"run_id": "run-1"}
    digest = store.put_json(payload)

    assert store.get_json(digest) == canonical_json(payload)


def test_get_json_rejects_tampered_object() -> None:
    client = FakeS3()
    store = S3ObjectStore("artifacts", client=client)
    payload = {"run_id": "run-1"}
    digest = store.put_json(payload)
    key = f"{digest.removeprefix('sha256:')}.json"
    client.objects[("artifacts", key)] = '{"run_id":"tampered"}'

    with pytest.raises(ValueError, match="OBJECT_STORE_INTEGRITY_FAILURE"):
        store.get_json(digest)
