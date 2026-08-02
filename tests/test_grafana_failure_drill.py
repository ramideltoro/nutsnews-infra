#!/usr/bin/env python3
"""Tests for the source-controlled Grafana failure-drill contract and evidence."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/grafana_failure_drill.py"
SPEC = importlib.util.spec_from_file_location("grafana_failure_drill", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load Grafana failure drill module.")
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


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

EXPECTED_DRILLS = {
    "alloy-stopped",
    "textfile-stale",
    "worker-unavailable",
    "rabbitmq-zero-consumer",
    "rabbitmq-growing-dlq",
    "postgres-relay-lag",
    "backend-readiness-failed",
    "synthetic-mismatch",
}


def phase(path: Path, status: str, **extra: object) -> Path:
    path.write_text(json.dumps({"status": status, **extra}) + "\n", encoding="utf-8")
    return path


class GrafanaFailureDrillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = DRILL.load_contract(DRILL.DEFAULT_CONTRACT)

    def test_contract_has_exact_eight_dry_run_first_drills(self) -> None:
        self.assertEqual({item["id"] for item in self.contract["drills"]}, EXPECTED_DRILLS)
        self.assertEqual(self.contract["default_mode"], "dry-run")
        self.assertEqual(self.contract["artifact_retention_days"], 90)
        synthetic = DRILL.select_drill(self.contract, "synthetic-mismatch", "body")
        self.assertEqual(synthetic["target"], "canonical_readiness")
        self.assertEqual(
            set(synthetic["targets"]),
            {
                "canonical_articles_api",
                "canonical_homepage",
                "canonical_readiness",
                "vercel_secondary_readiness",
                "vps_readiness",
            },
        )
        self.assertEqual(synthetic["variants"], ["status", "body", "header"])

    def test_grafana_origin_is_pinned_to_exact_query_free_ui_role(self) -> None:
        for value in (
            "https://nutsnews.grafana.net",
            "https://nutsnews.grafana.net/",
            "https://nutsnews.grafana.net:443",
            "https://nutsnews.grafana.net:443/",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    DRILL.validate_api_url(value),
                    "https://nutsnews.grafana.net",
                )
        for value in (
            "",
            "http://nutsnews.grafana.net",
            "https://grafana.net",
            "https://other-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
            "https://nutsnews.grafana.net.evil.invalid",
            "https://nutsnews.grafana.net/api",
            "https://nutsnews.grafana.net?",
            "https://nutsnews.grafana.net/?",
            "https://nutsnews.grafana.net/?token=secret",
            "https://nutsnews.grafana.net#",
            "https://nutsnews.grafana.net/#",
            "https://nutsnews.grafana.net/#fragment",
            "https://user:secret@nutsnews.grafana.net",
            "https://nutsnews.grafana.net:444",
            "https://nutsnews.grafana.net:",
            "https://bad_.grafana.net",
            " https://nutsnews.grafana.net",
            "\x00https://nutsnews.grafana.net",
            "https://nutsnews.\ngrafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                DRILL.GrafanaClient(value, "sensitive-token")

    def test_malformed_nfkc_netloc_never_echoes_protected_input(self) -> None:
        protected_fragment = "do-not-reflect-this-value"
        malformed = f"https://nutsnews.grafana.net\uff0f{protected_fragment}"

        with self.assertRaises(SystemExit) as raised:
            DRILL.validate_api_url(malformed, "NUTSNEWS_GRAFANA_CLOUD_URL")

        message = str(raised.exception)
        self.assertEqual(
            message,
            "NUTSNEWS_GRAFANA_CLOUD_URL must be a query-free HTTPS Grafana Cloud API origin",
        )
        self.assertNotIn(protected_fragment, message)
        self.assertNotIn(malformed, message)

    def test_redirect_is_rejected_before_a_second_authenticated_request(self) -> None:
        transport = RedirectingHTTPSHandler()
        client = DRILL.GrafanaClient(
            "https://nutsnews.grafana.net", "sensitive-token"
        )
        self.assertTrue(
            any(
                isinstance(handler, DRILL.NoRedirectHandler)
                for handler in client.opener.handlers
            )
        )
        client.opener = urllib.request.build_opener(
            DRILL.NoRedirectHandler(), transport
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            client.request("/api/alertmanager/grafana/api/v2/alerts")

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-token",
        )
        self.assertEqual(
            transport.requests[0].full_url,
            "https://nutsnews.grafana.net/api/alertmanager/grafana/api/v2/alerts",
        )

    def test_each_synthetic_target_gets_an_exact_confirmation(self) -> None:
        base = DRILL.select_drill(self.contract, "synthetic-mismatch", "status")
        for target in base["targets"]:
            with self.subTest(target=target):
                item = DRILL.select_synthetic_target(base, target)
                confirmation = f"execute-grafana-failure-drill:{target}:synthetic-mismatch"
                DRILL.validate_execute(item, target, confirmation)
                self.assertEqual(item["target"], target)

    def test_plan_is_value_free_and_non_mutating(self) -> None:
        item = DRILL.select_drill(self.contract, "alloy-stopped", "not-applicable")
        report = DRILL.planned_report(item, "not-applicable", "123-1")
        self.assertEqual(report["mode"], "dry-run")
        self.assertFalse(report["mutation_performed"])
        self.assertEqual(report["result"], "dry-run")
        self.assertEqual(report["observed_alert_uids"], ["nn-alloy-readiness"])
        self.assertNotIn("token", json.dumps(report).lower())

    def test_execute_requires_exact_target_and_drill_confirmation(self) -> None:
        item = DRILL.select_drill(self.contract, "worker-unavailable", "not-applicable")
        DRILL.validate_execute(
            item,
            "backend.nutsnews.com",
            "execute-grafana-failure-drill:backend.nutsnews.com:worker-unavailable",
        )
        with self.assertRaises(SystemExit):
            DRILL.validate_execute(item, "vps.nutsnews.com", "wrong")

    def test_alert_observer_matches_only_bounded_rule_uid_labels(self) -> None:
        response = [
            {"labels": {"__alert_rule_uid__": "nn-textfile-stale"}},
            {"labels": {"grafana_rule_uid": "nn-alloy-readiness", "secret": "ignored"}},
            {"labels": {"uid": "bad uid with spaces"}},
        ]
        client = mock.Mock()
        client.request.return_value = response
        self.assertEqual(
            DRILL.active_rule_uids(client),
            {"nn-alloy-readiness", "nn-textfile-stale"},
        )

    def test_finalize_requires_recovery_and_drops_unapproved_detail(self) -> None:
        item = DRILL.select_drill(self.contract, "textfile-stale", "not-applicable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "precheck": phase(root / "precheck.json", "pass", detail="do-not-copy"),
                "injection": phase(root / "injection.json", "pass"),
                "observation": phase(
                    root / "observation.json",
                    "pass",
                    observed_alert_uids=["nn-observability-collector-stale"],
                ),
                "recovery": phase(root / "recovery.json", "pass"),
                "postcheck": phase(root / "postcheck.json", "pass"),
            }
            report = DRILL.finalized_report(item, "not-applicable", "123-1", paths)
            phase(root / "recovery.json", "fail")
            failed = DRILL.finalized_report(item, "not-applicable", "123-1", paths)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["observed_alert_uids"], ["nn-observability-collector-stale"])
        self.assertNotIn("do-not-copy", json.dumps(report))
        self.assertEqual(failed["result"], "fail")

    def test_final_evidence_reuses_exact_initialized_start_time(self) -> None:
        item = DRILL.select_drill(self.contract, "alloy-stopped", "not-applicable")
        with tempfile.TemporaryDirectory() as directory:
            initialization = Path(directory) / "initialized.json"
            value = DRILL.planned_report(item, "not-applicable", "123-1")
            value.update({"mode": "execute", "result": "initialized"})
            value["started_at"] = "2026-07-31T12:34:56Z"
            initialization.write_text(json.dumps(value) + "\n", encoding="utf-8")
            started_at = DRILL.initialization_started_at(initialization, item, "123-1")
        self.assertEqual(started_at, "2026-07-31T12:34:56Z")


if __name__ == "__main__":
    unittest.main()
