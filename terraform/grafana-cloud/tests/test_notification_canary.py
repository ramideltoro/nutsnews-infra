#!/usr/bin/env python3
"""Unit tests for the Grafana notification canary payload and state matcher."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import unittest
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exercise_notification_canary.py"
SPEC = importlib.util.spec_from_file_location("exercise_notification_canary", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to import exercise_notification_canary.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ATTEST_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "attest_notification_canary.py"
)
ATTEST_SPEC = importlib.util.spec_from_file_location(
    "attest_notification_canary", ATTEST_SCRIPT
)
if ATTEST_SPEC is None or ATTEST_SPEC.loader is None:
    raise RuntimeError("Unable to import attest_notification_canary.py")
ATTEST_MODULE = importlib.util.module_from_spec(ATTEST_SPEC)
ATTEST_SPEC.loader.exec_module(ATTEST_MODULE)
ATTEST_SOURCE = ATTEST_SCRIPT.read_text(encoding="utf-8")
WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github/workflows/grafana-notification-canary.yml"
).read_text(encoding="utf-8")


class RedirectingHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[urllib.request.Request] = []

    def https_open(self, request: urllib.request.Request):
        self.requests.append(request)
        headers = Message()
        headers["Location"] = "https://attacker.invalid/steal"
        response = urllib.response.addinfourl(
            io.BytesIO(b""), headers, request.full_url, 302
        )
        response.msg = "Found"
        return response


class NotificationCanaryTests(unittest.TestCase):
    def test_grafana_origin_is_exact_query_free_https_grafana_cloud(self) -> None:
        for value in (
            "https://kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net/",
            "https://kindcantaloupe2036.grafana.net:443/",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    MODULE.validate_api_url(value),
                    "https://kindcantaloupe2036.grafana.net",
                )
        for value in (
            "",
            "http://kindcantaloupe2036.grafana.net",
            "https://grafana.net",
            "https://another-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
            "https://synthetic-monitoring-api.us.grafana.net",
            "https://kindcantaloupe2036.grafana.net.evil.invalid",
            "https://kindcantaloupe2036.grafana.net/api",
            "https://kindcantaloupe2036.grafana.net/?token=secret",
            "https://kindcantaloupe2036.grafana.net/#fragment",
            "https://user:secret@kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net:444",
            "https://kindcantaloupe2036.grafana.net:",
            "https://bad_.grafana.net",
            " https://kindcantaloupe2036.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.AlertmanagerClient(value, "sensitive-token")

    def test_redirect_is_rejected_before_a_second_authenticated_request(self) -> None:
        transport = RedirectingHTTPSHandler()
        client = MODULE.AlertmanagerClient(
            "https://kindcantaloupe2036.grafana.net", "sensitive-token"
        )
        client.opener = urllib.request.build_opener(
            MODULE.NoRedirectHandler(), transport
        )

        with self.assertRaisesRegex(RuntimeError, "failed with 302"):
            client.request("GET", "/api/alertmanager/grafana/api/v2/alerts")

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-token",
        )
        self.assertEqual(
            transport.requests[0].full_url,
            "https://kindcantaloupe2036.grafana.net/api/alertmanager/grafana/api/v2/alerts",
        )

    def test_payload_routes_to_email_and_has_unique_searchable_name(self) -> None:
        start = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
        alert = MODULE.build_alert("run-123", start, start + dt.timedelta(minutes=15))
        self.assertEqual(alert["labels"]["alertname"], "NutsNewsNotificationCanary-run-123")
        self.assertEqual(alert["labels"]["route"], "operations-email")
        self.assertEqual(alert["labels"]["severity"], "critical")
        self.assertEqual(set(alert["labels"]), MODULE.REQUIRED_LABELS)
        self.assertIn("run-123", alert["annotations"]["summary"])

    def test_resolution_reuses_exact_labels_and_start_time(self) -> None:
        start = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
        firing = MODULE.build_alert("run-123", start, start + dt.timedelta(minutes=15))
        resolved = MODULE.build_alert("run-123", start, start + dt.timedelta(minutes=1))
        self.assertEqual(firing["labels"], resolved["labels"])
        self.assertEqual(firing["startsAt"], resolved["startsAt"])
        self.assertNotEqual(firing["endsAt"], resolved["endsAt"])

    def test_active_matcher_ignores_other_alerts(self) -> None:
        response = [
            {"labels": {"alertname": "OtherAlert"}},
            {"labels": {"alertname": "NutsNewsNotificationCanary-run-123"}},
        ]
        matches = MODULE.matching_active_alerts(
            response, "NutsNewsNotificationCanary-run-123"
        )
        self.assertEqual(len(matches), 1)

    def test_canary_id_rejects_empty_or_oversized_values(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.safe_canary_id(" ")
        with self.assertRaises(ValueError):
            MODULE.safe_canary_id("x" * 81)

    def human_attestation(self, **overrides):
        values = {
            "canary_id": "github-123",
            "api_transition_evidence_sha256": "sha256:" + "a" * 64,
            "receipt_evidence_sha256": "sha256:" + "b" * 64,
            "human_confirmation": (
                "human-attest-grafana-notification-canary:github-123:"
                "firing-and-resolved-received"
            ),
            "github_actor": "ramideltoro",
            "github_run_id": 456,
            "github_run_attempt": 2,
            "github_repository": "ramideltoro/nutsnews-infra",
            "github_ref": "refs/heads/main",
            "github_event_name": "workflow_dispatch",
            "attested_at": dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        }
        values.update(overrides)
        return ATTEST_MODULE.build_attestation(**values)

    def test_receipt_human_attestation_is_bound_without_refiring(self) -> None:
        timestamp = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
        attestation = self.human_attestation(attested_at=timestamp)
        self.assertEqual(attestation["canary_id"], "github-123")
        self.assertEqual(attestation["original_run_id"], 123)
        self.assertEqual(attestation["phase"], "receipt_human_attested")
        self.assertEqual(attestation["receipt_status"], "human_attested")
        self.assertTrue(attestation["human_confirmation_recorded"])
        self.assertEqual(attestation["attested_by"], "ramideltoro")
        self.assertEqual(attestation["attestation_run_id"], 456)
        self.assertEqual(attestation["attestation_run_attempt"], 2)
        self.assertTrue(attestation["firing_receipt_human_attested"])
        self.assertTrue(attestation["resolved_receipt_human_attested"])
        self.assertFalse(attestation["evidence_store_allowlisted"])
        self.assertFalse(attestation["evidence_store_fetched"])
        self.assertFalse(attestation["independent_verification_performed"])
        self.assertFalse(attestation["refired"])
        self.assertEqual(attestation["attested_at"], "2026-08-01T00:00:00Z")
        self.assertRegex(attestation["attestation_binding_sha256"], r"^sha256:[a-f0-9]{64}$")
        encoded = json.dumps(attestation)
        self.assertNotIn("https://", encoded)
        self.assertNotIn("receipt_status\": \"verified", encoded)

    def test_attestation_binding_changes_with_actor_run_or_attempt(self) -> None:
        base = self.human_attestation()
        for overrides in (
            {"github_actor": "another-human"},
            {"github_run_id": 457},
            {"github_run_attempt": 3},
        ):
            with self.subTest(overrides=overrides):
                changed = self.human_attestation(**overrides)
                self.assertNotEqual(
                    changed["attestation_binding_sha256"],
                    base["attestation_binding_sha256"],
                )

    def test_arbitrary_https_links_cannot_become_verified_receipt_evidence(self) -> None:
        for field, value in (
            (
                "api_transition_evidence_sha256",
                "https://evidence.example.invalid/api/github-123",
            ),
            (
                "receipt_evidence_sha256",
                "https://evidence.example.invalid/private/share-token-123",
            ),
            ("receipt_evidence_sha256", "sha256:" + "B" * 64),
            ("receipt_evidence_sha256", "b" * 64),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.human_attestation(**{field: value})

    def test_receipt_attestation_rejects_invalid_or_ambiguous_evidence(self) -> None:
        digest = "sha256:" + "a" * 64
        invalid_cases = (
            {"receipt_evidence_sha256": digest},
            {"canary_id": "github-other"},
            {"canary_id": "github 123"},
            {"human_confirmation": "yes"},
            {"github_actor": "github-actions[bot]"},
            {"github_run_id": 123},
            {"github_run_id": 0},
            {"github_run_attempt": 0},
            {"github_repository": "attacker/fork"},
            {"github_ref": "refs/heads/feature"},
            {"github_event_name": "schedule"},
            {"attested_at": dt.datetime(2026, 8, 1)},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.human_attestation(**overrides)

    def test_human_attestation_workflow_uses_only_opaque_digest_inputs(self) -> None:
        attestation_job = WORKFLOW.split("  attest-receipt:", maxsplit=1)[1]
        for token in (
            "api_transition_evidence_sha256",
            "receipt_evidence_sha256",
            "human_attestation",
            "github.triggering_actor",
            '--github-run-id "$GITHUB_RUN_ID"',
            '--github-run-attempt "$GITHUB_RUN_ATTEMPT"',
            "receipt human-attested",
            "not independent delivery verification",
        ):
            self.assertIn(token, attestation_job)
        for forbidden in (
            "api_transition_evidence_reference",
            "receipt_evidence_reference",
            "original_run_url",
            "receipt verified",
            "query-free https reference",
        ):
            self.assertNotIn(forbidden, attestation_job.lower())

    def test_human_attestation_has_no_evidence_fetch_or_verified_status(self) -> None:
        self.assertNotIn("urllib", ATTEST_SOURCE)
        self.assertNotIn("urlopen", ATTEST_SOURCE)
        self.assertNotIn('"receipt_status": "verified"', ATTEST_SOURCE)
        for token in (
            '"receipt_status": "human_attested"',
            '"evidence_store_allowlisted": False',
            '"evidence_store_fetched": False',
            '"independent_verification_performed": False',
            '"attestation_run_id": attestation_run_id',
            '"attestation_run_attempt": attestation_run_attempt',
        ):
            self.assertIn(token, ATTEST_SOURCE)


if __name__ == "__main__":
    unittest.main()
