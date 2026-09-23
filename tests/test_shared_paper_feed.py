from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts.publish_shared_paper_feed import canonical_hash, publish, validate_bundle
from scripts.verify_shared_paper_page import verify_deployed_page


def fixture_bundle() -> dict[str, object]:
    captured = "2026-09-22T21:00:00Z"
    metric = {
        "status": "available",
        "amount": "10",
        "return_value": "0.01",
        "attribution": "fixture attribution",
        "label": "fixture metric",
    }
    snapshot: dict[str, object] = {
        "schema_version": "signalquarry-public-performance/v1",
        "deployment_alias": "v13-5-paper",
        "strategy_version": "v13.5",
        "evidence_mode": "paper",
        "account_history_epoch": "fixture-epoch",
        "broker_observed_at": captured,
        "captured_at": captured,
        "account": {"equity": "1010", "cash": "500", "buying_power": "500", "status": "ACTIVE"},
        "pnl": {
            "broker_reference": metric,
            "day": metric,
            "net_dollar_pnl": metric,
            "time_weighted_return": metric,
        },
        "equity_history": [{"timestamp": captured, "equity": "1010", "drawdown": "0"}],
        "drawdown_sampling": "daily_close",
        "positions": [],
        "recent_fills": [],
        "external_cash_flows": [],
        "availability": {
            "equity_history": "available",
            "recent_fills": "available",
            "positions": "available",
            "external_cash_flows": "available",
            "realized_pnl": "unavailable",
            "source_feed": "fixture",
        },
        "limitations": ["fixture"],
    }
    snapshot["snapshot_hash"] = canonical_hash(snapshot)
    compat: dict[str, object] = {
        "schema_version": "stable-income-generator-live-paper/v3",
        "source": "broker_reported_paper",
        "generated_at": captured,
        "signalquarry_snapshot_hash": snapshot["snapshot_hash"],
        "refresh_contract": {
            "scheduled_interval_seconds": 1800,
            "browser_poll_seconds": 60,
            "stale_after_seconds": 5400,
            "publishing_window": {"timezone": "America/New_York", "weekdays": ["MON", "TUE", "WED", "THU", "FRI"], "start": "09:00", "final_run": "17:00"},
            "delivery": "fixture",
        },
        "account": {
            "deployment_alias": "v13-5-paper",
            "status": "ACTIVE",
            "equity": 1010.0,
            "cash": 500.0,
            "buying_power": 500.0,
            "starting_baseline": 1000.0,
            "total_pnl": 10.0,
            "total_return": 0.01,
            "day_start_equity": 1000.0,
            "day_pnl": 10.0,
            "day_return": 0.01,
        },
        "strategy": {"strategy_id": "v13.5", "underlying": "QQQ"},
        "recent_filled_system_orders": [],
        "portfolio_history": {"status": "available", "period": "1A", "timeframe": "1D", "points": []},
        "publication_scope": {
            "paper_only": True,
            "account_id_publication_approved": False,
            "excluded": ["account identifiers", "credentials", "broker order IDs", "client-order IDs"],
            "order_filter": "fixture",
        },
    }
    compat["artifact_hash"] = canonical_hash(compat)
    return {"snapshot": snapshot, "compatibility": compat}


class SharedPaperFeedTests(unittest.TestCase):
    def test_valid_current_bundle_is_accepted_with_one_identity(self) -> None:
        bundle = fixture_bundle()
        snapshot, compat = validate_bundle(
            bundle,
            mode="refresh",
            now=datetime(2026, 9, 22, 21, 0, 30, tzinfo=UTC),
        )
        self.assertEqual(compat["signalquarry_snapshot_hash"], snapshot["snapshot_hash"])

    def test_extra_account_identifier_field_is_rejected(self) -> None:
        bundle = fixture_bundle()
        compat = bundle["compatibility"]
        compat["account"]["account_id"] = "private"
        compat["artifact_hash"] = canonical_hash({key: value for key, value in compat.items() if key != "artifact_hash"})
        with self.assertRaisesRegex(ValueError, "COMPAT_ACCOUNT_FIELDS_INVALID"):
            validate_bundle(bundle, mode="site-only")

    def test_unpaired_compatibility_snapshot_is_rejected(self) -> None:
        bundle = fixture_bundle()
        bundle["compatibility"]["signalquarry_snapshot_hash"] = "sha256:" + "0" * 64
        compat = bundle["compatibility"]
        compat["artifact_hash"] = canonical_hash({key: value for key, value in compat.items() if key != "artifact_hash"})
        with self.assertRaisesRegex(ValueError, "HASH_IDENTITY_MISMATCH"):
            validate_bundle(bundle, mode="site-only")

    def test_refresh_rejects_stale_capture_but_site_only_accepts_saved_snapshot(self) -> None:
        bundle = fixture_bundle()
        with self.assertRaisesRegex(ValueError, "STALE_OR_FUTURE"):
            validate_bundle(bundle, mode="refresh", now=datetime(2026, 9, 22, 23, 0, tzinfo=UTC))
        validate_bundle(bundle, mode="site-only", now=datetime(2026, 9, 22, 23, 0, tzinfo=UTC))

    def test_publish_writes_only_the_allowlisted_compatibility_view(self) -> None:
        bundle = fixture_bundle()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "bundle.json"
            incoming.write_text(json.dumps(bundle), encoding="utf-8")
            digest = publish(
                incoming,
                public_root=root / "docs",
                mode="refresh",
                max_age_seconds=5400,
                now=datetime(2026, 9, 22, 21, 0, 30, tzinfo=UTC),
            )
            public_json = (root / "docs/assets/data/live-paper-snapshot.json").read_text(encoding="utf-8")
            public_js = (root / "docs/assets/data/live-paper-snapshot.js").read_text(encoding="utf-8")
            self.assertIn(digest, public_json)
            self.assertNotIn('"account_id":', public_json)
            self.assertNotIn("private", public_js)

    def test_deployed_page_verifier_accepts_matching_public_files(self) -> None:
        bundle = fixture_bundle()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "bundle.json"
            public_root = root / "docs"
            incoming.write_text(json.dumps(bundle), encoding="utf-8")
            publish(incoming, public_root=public_root, mode="site-only", max_age_seconds=5400)

            def fetch(url: str) -> bytes:
                return (public_root / urlsplit(url).path.removeprefix("/repo/")).read_bytes()

            result = verify_deployed_page(
                public_root,
                "https://pages.example/repo/",
                attempts=1,
                fetch=fetch,
                sleep=lambda _seconds: None,
            )
            self.assertEqual(result["snapshot_hash"], bundle["snapshot"]["snapshot_hash"])
            self.assertEqual(result["captured_at"], bundle["compatibility"]["generated_at"])

    def test_deployed_page_verifier_rejects_a_different_valid_capture(self) -> None:
        bundle = fixture_bundle()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "bundle.json"
            public_root = root / "docs"
            incoming.write_text(json.dumps(bundle), encoding="utf-8")
            publish(incoming, public_root=public_root, mode="site-only", max_age_seconds=5400)
            remote = json.loads((public_root / "assets/data/live-paper-snapshot.json").read_text())
            remote["generated_at"] = "2026-09-22T21:01:00Z"
            remote["artifact_hash"] = canonical_hash(
                {key: value for key, value in remote.items() if key != "artifact_hash"}
            )
            tampered = json.dumps(remote).encode()

            def fetch(url: str) -> bytes:
                path = urlsplit(url).path.removeprefix("/repo/")
                if path.endswith(".json"):
                    return tampered
                return (public_root / path).read_bytes()

            with self.assertRaisesRegex(SystemExit, "O_DEPLOYED_FEED_IDENTITY_MISMATCH"):
                verify_deployed_page(
                    public_root,
                    "https://pages.example/repo/",
                    attempts=1,
                    fetch=fetch,
                    sleep=lambda _seconds: None,
                )


if __name__ == "__main__":
    unittest.main()
