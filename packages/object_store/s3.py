"""S3-compatible implementation of the immutable object-store port."""

from __future__ import annotations

import json
from typing import Any

from packages.contracts.canonical import canonical_hash, canonical_json


class S3ObjectStore:
    """Content-addressed JSON storage backed by an S3-compatible bucket."""

    def __init__(
        self,
        bucket: str,
        *,
        client: Any | None = None,
        prefix: str = "",
    ) -> None:
        if not bucket:
            raise ValueError("S3_BUCKET_REQUIRED")

        self.bucket = bucket
        self.prefix = prefix.strip("/")

        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client = client

    def _key(self, digest: str) -> str:
        filename = f"{digest.removeprefix('sha256:')}.json"
        return f"{self.prefix}/{filename}" if self.prefix else filename

    def put_json(self, payload: Any) -> str:
        encoded = canonical_json(payload)
        digest = canonical_hash(payload)
        key = self._key(digest)

        try:
            existing = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            existing = None

        if existing is not None:
            existing_encoded = existing["Body"].read().decode("utf-8")
            if existing_encoded != encoded:
                raise ValueError("OBJECT_STORE_HASH_COLLISION")
            return digest

        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=encoded.encode("utf-8"),
            ContentType="application/json",
        )
        return digest

    def get_json(self, digest: str) -> str:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(digest))
        encoded = response["Body"].read().decode("utf-8")

        if canonical_hash(json.loads(encoded)) != digest:
            raise ValueError("OBJECT_STORE_INTEGRITY_FAILURE")
        return encoded
