#!/usr/bin/env python3
"""Verify O's deployed paper page serves the staged SignalQuarry snapshot."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .publish_shared_paper_feed import canonical_hash
except ImportError:  # Direct script execution from the Pages workflow.
    from publish_shared_paper_feed import canonical_hash

JSON_PATH = "assets/data/live-paper-snapshot.json"
SCRIPT_PATH = "assets/data/live-paper-snapshot.js"


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    with urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("O_DEPLOYED_FEED_HTTP_STATUS_INVALID")
        return response.read()


def _read_local(public_root: Path) -> tuple[dict[str, Any], bytes]:
    json_body = (public_root / JSON_PATH).read_bytes()
    snapshot = json.loads(json_body)
    if not isinstance(snapshot, dict):
        raise ValueError("O_LOCAL_FEED_EXPECTED_OBJECT")
    artifact_hash = snapshot.pop("artifact_hash", None)
    if artifact_hash != canonical_hash(snapshot):
        raise ValueError("O_LOCAL_FEED_HASH_INVALID")
    return snapshot, (public_root / SCRIPT_PATH).read_bytes()


def verify_deployed_page(
    public_root: Path,
    base_url: str,
    *,
    attempts: int = 12,
    wait_seconds: float = 5.0,
    fetch: Any = _fetch,
    sleep: Any = time.sleep,
) -> dict[str, str]:
    """Retry Pages propagation and verify JSON identity plus browser fallback."""
    if not base_url.startswith("https://") or attempts < 1:
        raise ValueError("O_DEPLOYED_SITE_URL_INVALID")
    expected, expected_script = _read_local(public_root)
    expected_hash = expected.get("signalquarry_snapshot_hash")
    expected_time = expected.get("generated_at")
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            query = urlencode({"verify": attempt})
            remote_json = json.loads(fetch(f"{base_url.rstrip('/')}/{JSON_PATH}?{query}"))
            remote_script = fetch(f"{base_url.rstrip('/')}/{SCRIPT_PATH}?{query}")
            if not isinstance(remote_json, dict):
                raise ValueError("O_DEPLOYED_FEED_EXPECTED_OBJECT")
            remote_hash = remote_json.pop("artifact_hash", None)
            if remote_hash != canonical_hash(remote_json):
                raise ValueError("O_DEPLOYED_FEED_HASH_INVALID")
            if (
                remote_json.get("signalquarry_snapshot_hash") != expected_hash
                or remote_json.get("generated_at") != expected_time
                or remote_json != expected
                or remote_script != expected_script
            ):
                raise ValueError("O_DEPLOYED_FEED_IDENTITY_MISMATCH")
            return {
                "snapshot_hash": str(expected_hash),
                "captured_at": str(expected_time),
                "artifact_hash": str(remote_hash),
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleep(wait_seconds)

    reason = str(last_error) if last_error and str(last_error).isupper() else "O_DEPLOYED_FEED_NOT_PROPAGATED"
    raise SystemExit(reason) from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=Path("docs"))
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    result = verify_deployed_page(args.public_root, args.base_url)
    for key, value in result.items():
        print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
