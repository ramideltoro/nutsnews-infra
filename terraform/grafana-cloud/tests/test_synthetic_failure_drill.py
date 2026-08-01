#!/usr/bin/env python3
"""Unit tests for the fail-safe Synthetic Monitoring failure drill."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import stat
import tempfile
import unittest
import urllib.parse
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "exercise_synthetic_failure_drill.py"
SPEC = importlib.util.spec_from_file_location("exercise_synthetic_failure_drill", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to import exercise_synthetic_failure_drill.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

WATCHDOG_NONCE = "ab" * 32
PARENT_RUN_ID = 1234567890
PARENT_RUN_ATTEMPT = 2
PARENT_HEAD_SHA = "c" * 40
WATCHDOG_RUN_ID = 2234567890
WATCHDOG_RUN_ATTEMPT = 1


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


def remote_check(job: str = "canonical_readiness") -> dict:
    target_path = (
        "/"
        if job == "canonical_homepage"
        else "/api/articles"
        if job == "canonical_articles_api"
        else "/readyz"
    )
    return {
        "alertSensitivity": "none",
        "basicMetricsOnly": True,
        "channels": {"k6": None},
        "created": 1.0,
        "description": f"Synthetic check {job}",
        "disableReason": None,
        "enabled": True,
        "folderUid": "nutsnews-observability",
        "frequency": 300000,
        "id": 741,
        "job": job,
        "labels": [
            {"name": "service_namespace", "value": "nutsnews"},
            {"name": "deployment_environment", "value": "production"},
            {"name": "check", "value": job},
            {"name": "owner", "value": "nutsnews-observability"},
            {"name": "service", "value": "synthetic-monitoring"},
        ],
        "modified": 2.0,
        "offset": 0,
        "probes": [11, 22],
        "settings": {
            "http": {
                "method": 0,
                "headers": [],
                "body": "",
                "basicAuth": None,
                "bearerToken": "",
                "proxyURL": None,
                "proxyConnectHeaders": [],
                "oauth2Config": None,
                "cacheBustingQueryParamName": None,
                "secretManagerEnabled": False,
                "tlsConfig": None,
                "failIfNotSSL": True,
                "validStatusCodes": [200],
                "failIfBodyMatchesRegexp": ["deploymentTarget.*unknown"],
                "failIfBodyNotMatchesRegexp": [
                    "ready.*true",
                    "deploymentTarget.*production-vps|vercel-production",
                ],
                "failIfHeaderMatchesRegexp": [],
                "failIfHeaderNotMatchesRegexp": [
                    {"allowMissing": False, "header": "Cache-Control", "regexp": "no-store"}
                ],
            }
        },
        "target": f"https://www.nutsnews.com{target_path}",
        "tenantId": 1234,
        "timeout": 5000,
    }


class FakeSyntheticClient:
    def __init__(self, check: dict | None = None) -> None:
        self.remote = copy.deepcopy(check or remote_check())
        self.updates: list[dict] = []
        self.before_update = None
        self.fail_first_update = False

    def list_checks(self):
        return [{"id": self.remote["id"], "job": self.remote["job"]}]

    def get_check(self, check_id: int):
        if check_id != self.remote["id"]:
            raise AssertionError("wrong check")
        return copy.deepcopy(self.remote)

    def update_check(self, check_id: int, payload: dict):
        if self.before_update is not None:
            self.before_update()
        self.updates.append(copy.deepcopy(payload))
        if self.fail_first_update and len(self.updates) == 1:
            raise MODULE.DrillError("bounded test failure")
        self.remote = {
            **copy.deepcopy(payload),
            "created": self.remote["created"],
            "modified": self.remote["modified"] + 1,
            "disableReason": None,
        }
        return copy.deepcopy(self.remote)


class FakeGrafanaClient:
    pass


class ProbeSampleClient:
    def __init__(self, samples: list[dict]) -> None:
        self.samples = samples

    def probe_samples(self, datasource_uid: str, since_epoch: float) -> list[dict]:
        if datasource_uid != "grafanacloud-prom":
            raise AssertionError("wrong datasource")
        if since_epoch != 1_000:
            raise AssertionError("wrong freshness cutoff")
        return copy.deepcopy(self.samples)


class AlertInstanceClient:
    def __init__(self, instances: list[dict]) -> None:
        self.instances = instances

    def active_alert_instances(self) -> list[dict]:
        return copy.deepcopy(self.instances)


def run_execute_with_watchdog(
    client: FakeSyntheticClient,
    grafana_client: object,
    datasource_uid: str,
    snapshot: Path,
    variant: str,
    timeout_seconds: int,
) -> dict:
    watchdog_snapshot = snapshot.with_name("watchdog-private-restore.json")
    handshake = snapshot.with_name("watchdog-armed.json")
    armed = MODULE.run_watchdog_arm(
        client,
        watchdog_snapshot,
        variant,
        WATCHDOG_NONCE,
        PARENT_RUN_ID,
        PARENT_RUN_ATTEMPT,
        PARENT_HEAD_SHA,
        WATCHDOG_RUN_ID,
        WATCHDOG_RUN_ATTEMPT,
    )
    handshake.write_text(json.dumps(armed), encoding="utf-8")
    return MODULE.run_execute(
        client,
        grafana_client,
        datasource_uid,
        snapshot,
        variant,
        timeout_seconds,
        handshake,
        WATCHDOG_NONCE,
        PARENT_RUN_ID,
        PARENT_RUN_ATTEMPT,
        PARENT_HEAD_SHA,
        WATCHDOG_RUN_ID,
        WATCHDOG_RUN_ATTEMPT,
    )


class SyntheticFailureDrillTests(unittest.TestCase):
    def tearDown(self) -> None:
        MODULE.select_job("canonical_readiness")

    def test_bearer_origins_are_pinned_to_their_api_roles(self) -> None:
        for value, expected in (
            (
                "https://synthetic-monitoring-api.grafana.net",
                "https://synthetic-monitoring-api.grafana.net",
            ),
            (
                "https://synthetic-monitoring-api.grafana.net/",
                "https://synthetic-monitoring-api.grafana.net",
            ),
            (
                "https://synthetic-monitoring-api.us.grafana.net:443/",
                "https://synthetic-monitoring-api.us.grafana.net",
            ),
            (
                "https://synthetic-monitoring-api.us.east.grafana.net/",
                "https://synthetic-monitoring-api.us.east.grafana.net",
            ),
            (
                "https://synthetic-monitoring-api-eu-west-0.grafana.net/",
                "https://synthetic-monitoring-api-eu-west-0.grafana.net",
            ),
        ):
            with self.subTest(value=value):
                self.assertEqual(MODULE.validate_sm_api_url(value), expected)
        for value in (
            "http://synthetic-monitoring-api.grafana.net",
            "https://grafana.net",
            "https://nutsnews.grafana.net",
            "https://another-tenant.grafana.net",
            "https://synthetic-monitoring-apiattacker.grafana.net",
            "https://synthetic-monitoring-api-.grafana.net",
            "https://synthetic-monitoring-api--us.grafana.net",
            "https://synthetic-monitoring-api.grafana.net.evil.invalid",
            "https://synthetic-monitoring-api.grafana.net/api/v1",
            "https://synthetic-monitoring-api.grafana.net?secret=x",
            "https://user:secret@synthetic-monitoring-api.grafana.net",
            "https://synthetic-monitoring-api.grafana.net:444",
            "https://synthetic-monitoring-api.grafana.net:",
            "https://bad_.grafana.net",
            " https://synthetic-monitoring-api.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(MODULE.DrillError):
                MODULE.SyntheticClient(value, "sensitive-token")

        for value in (
            "https://nutsnews.grafana.net",
            "https://nutsnews.grafana.net/",
            "https://nutsnews.grafana.net:443/",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    MODULE.validate_grafana_api_url(value),
                    "https://nutsnews.grafana.net",
                )
        for value in (
            "https://another-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
            "https://synthetic-monitoring-api.us.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(MODULE.DrillError):
                MODULE.GrafanaClient(value, "sensitive-token")

    def test_redirects_never_move_bearer_tokens(self) -> None:
        sm_transport = RedirectingHTTPSHandler()
        sm_client = MODULE.SyntheticClient(
            "https://synthetic-monitoring-api.grafana.net", "sensitive-sm-token"
        )
        sm_client.opener = urllib.request.build_opener(
            MODULE.NoRedirectHandler(), sm_transport
        )
        with self.assertRaisesRegex(MODULE.DrillError, "HTTP 302"):
            sm_client.list_checks()

        self.assertEqual(len(sm_transport.requests), 1)
        self.assertEqual(
            sm_transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-sm-token",
        )
        self.assertEqual(
            sm_transport.requests[0].full_url,
            "https://synthetic-monitoring-api.grafana.net/api/v1/check",
        )

        grafana_transport = RedirectingHTTPSHandler()
        grafana_client = MODULE.GrafanaClient(
            "https://nutsnews.grafana.net", "sensitive-grafana-token"
        )
        grafana_client.opener = urllib.request.build_opener(
            MODULE.NoRedirectHandler(), grafana_transport
        )
        with self.assertRaisesRegex(MODULE.DrillError, "HTTP 302"):
            grafana_client.active_alert_instances()

        self.assertEqual(len(grafana_transport.requests), 1)
        self.assertEqual(
            grafana_transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-grafana-token",
        )
        self.assertEqual(
            grafana_transport.requests[0].full_url,
            "https://nutsnews.grafana.net/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false",
        )

    def test_dry_run_resolves_exact_check_without_update_and_is_sanitized(self) -> None:
        client = FakeSyntheticClient()
        report = MODULE.run_dry_run(client, "header")
        self.assertEqual(client.updates, [])
        self.assertEqual(report["result"], "dry-run")
        encoded = json.dumps(report)
        self.assertNotIn("nutsnews.com", encoded)
        self.assertNotIn("no-store", encoded)
        self.assertNotIn(MODULE.HEADER_MISMATCH["regexp"], encoded)

    def test_each_variant_changes_exactly_one_assertion_family(self) -> None:
        base = MODULE.update_payload(remote_check())
        for variant, field in MODULE.MUTATION_FIELDS.items():
            with self.subTest(variant=variant):
                changed = MODULE.mutated_payload(base, variant)
                expected = copy.deepcopy(base)
                expected["settings"]["http"][field] = changed["settings"]["http"][field]
                self.assertEqual(changed, expected)
                self.assertNotEqual(changed["settings"]["http"][field], base["settings"]["http"].get(field))

    def test_all_five_approved_checks_support_each_controlled_mismatch(self) -> None:
        for job in MODULE.JOBS:
            for variant in MODULE.VARIANTS:
                with self.subTest(job=job, variant=variant):
                    MODULE.select_job(job)
                    client = FakeSyntheticClient(remote_check(job))
                    report = MODULE.run_dry_run(client, variant)
                    self.assertEqual(report["target"], job)
                    self.assertEqual(report["result"], "dry-run")
                    self.assertEqual(client.updates, [])

    def test_execute_saves_private_complete_snapshot_before_post_and_restores(self) -> None:
        client = FakeSyntheticClient()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "restore.json"

            def assert_saved() -> None:
                self.assertTrue(snapshot.is_file())
                self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
                saved = json.loads(snapshot.read_text(encoding="utf-8"))
                self.assertIn("remote_check", saved)
                self.assertIn("restore_payload", saved)

            client.before_update = assert_saved
            with mock.patch.object(
                MODULE,
                "wait_for_probe_state",
                return_value={
                    "status": "pass",
                    "probe_count": 2,
                    "probes": ["probe-a", "probe-b"],
                },
            ), mock.patch.object(
                MODULE,
                "wait_for_alert_state",
                return_value={"status": "pass", "alert_uid": MODULE.ALERT_UID},
            ):
                report = run_execute_with_watchdog(
                    client, FakeGrafanaClient(), "grafanacloud-prom", snapshot, "body", 30
                )
        self.assertEqual(report["result"], "pass")
        self.assertTrue(report["restored"])
        self.assertTrue(report["alert_firing_observed"])
        self.assertTrue(report["alert_recovery_observed"])
        self.assertEqual(len(client.updates), 2)
        self.assertEqual(client.updates[-1], MODULE.update_payload(remote_check()))

    def test_uncertain_update_failure_still_attempts_exact_restoration(self) -> None:
        client = FakeSyntheticClient()
        client.fail_first_update = True
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE,
            "wait_for_probe_state",
            return_value={
                "status": "pass",
                "probe_count": 2,
                "probes": ["probe-a", "probe-b"],
            },
        ), mock.patch.object(
            MODULE,
            "wait_for_alert_state",
            return_value={"status": "pass", "alert_uid": MODULE.ALERT_UID},
        ):
            report = run_execute_with_watchdog(
                client,
                FakeGrafanaClient(),
                "grafanacloud-prom",
                Path(directory) / "restore.json",
                "status",
                30,
            )
        self.assertEqual(report["result"], "fail")
        self.assertTrue(report["restoration_attempted"])
        self.assertTrue(report["restored"])
        self.assertFalse(report["restore_write_performed"])
        self.assertEqual(len(client.updates), 1)

    def test_observation_timeout_restores_in_finally(self) -> None:
        client = FakeSyntheticClient()
        states = iter(
            [
                {
                    "status": "pass",
                    "probe_count": 2,
                    "probes": ["probe-a", "probe-b"],
                },
                {"status": "fail", "probe_count": 1, "probes": ["probe-a"]},
                {
                    "status": "pass",
                    "probe_count": 2,
                    "probes": ["probe-a", "probe-b"],
                },
            ]
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE, "wait_for_probe_state", side_effect=lambda *args, **kwargs: next(states)
        ), mock.patch.object(
            MODULE,
            "wait_for_alert_state",
            return_value={"status": "pass", "alert_uid": MODULE.ALERT_UID},
        ):
            report = run_execute_with_watchdog(
                client,
                FakeGrafanaClient(),
                "grafanacloud-prom",
                Path(directory) / "restore.json",
                "header",
                30,
            )
        self.assertEqual(report["result"], "fail")
        self.assertTrue(report["restored"])
        self.assertTrue(report["recovery_observed"])

    def test_api_drift_or_auth_state_fails_before_any_update(self) -> None:
        for mutation in ("unknown", "auth"):
            with self.subTest(mutation=mutation):
                check = remote_check()
                if mutation == "unknown":
                    check["newRemoteField"] = "drift"
                else:
                    check["settings"]["http"]["bearerToken"] = "secret"
                client = FakeSyntheticClient(check)
                with self.assertRaises(MODULE.DrillError):
                    MODULE.run_dry_run(client, "body")
                self.assertEqual(client.updates, [])

    def test_remote_target_query_or_nondefault_port_fails_before_any_update(self) -> None:
        for target in (
            "https://www.nutsnews.com:8443/readyz",
            "https://www.nutsnews.com/readyz?cached=true",
        ):
            with self.subTest(target=target):
                check = remote_check()
                check["target"] = target
                client = FakeSyntheticClient(check)
                with self.assertRaises(MODULE.DrillError):
                    MODULE.run_dry_run(client, "status")
                self.assertEqual(client.updates, [])

    def test_remote_payload_comparison_accepts_set_reordering_only(self) -> None:
        expected = MODULE.update_payload(remote_check())
        reordered = remote_check()
        reordered["settings"]["http"]["failIfBodyNotMatchesRegexp"].reverse()
        client = FakeSyntheticClient(reordered)
        MODULE.verify_remote_payload(client, expected["id"], expected)

        client.remote["settings"]["http"]["failIfBodyNotMatchesRegexp"].append(
            client.remote["settings"]["http"]["failIfBodyNotMatchesRegexp"][0]
        )
        with self.assertRaises(MODULE.DrillError):
            MODULE.verify_remote_payload(client, expected["id"], expected)

    def test_restore_mode_uses_exact_saved_payload(self) -> None:
        client = FakeSyntheticClient()
        original = remote_check()
        base = MODULE.update_payload(original)
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "restore.json"
            MODULE.private_snapshot_write(snapshot, original, base)
            client.remote["settings"]["http"]["validStatusCodes"] = [599]
            with mock.patch.object(
                MODULE,
                "wait_for_probe_state",
                return_value={
                    "status": "pass",
                    "probe_count": 2,
                    "probes": ["probe-a", "probe-b"],
                },
            ), mock.patch.object(
                MODULE,
                "wait_for_alert_state",
                return_value={"status": "pass", "alert_uid": MODULE.ALERT_UID},
            ):
                report = MODULE.restore_saved(
                    client,
                    FakeGrafanaClient(),
                    "grafanacloud-prom",
                    snapshot,
                    30,
                    "status",
                )
        self.assertEqual(report["result"], "pass")
        self.assertEqual(client.updates[-1], base)

    def test_watchdog_arms_with_private_snapshot_and_sanitized_evidence(self) -> None:
        client = FakeSyntheticClient()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "watchdog-private.json"
            report = MODULE.run_watchdog_arm(
                client,
                snapshot,
                "header",
                WATCHDOG_NONCE,
                PARENT_RUN_ID,
                PARENT_RUN_ATTEMPT,
                PARENT_HEAD_SHA,
                WATCHDOG_RUN_ID,
                WATCHDOG_RUN_ATTEMPT,
            )
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            saved = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertIn("remote_check", saved)
            self.assertIn("restore_payload", saved)
        self.assertEqual(client.updates, [])
        self.assertEqual(set(report), MODULE.WATCHDOG_ARM_FIELDS)
        self.assertEqual(report["result"], "armed")
        self.assertFalse(report["private_snapshot_uploaded"])
        encoded = json.dumps(report)
        self.assertNotIn("nutsnews.com", encoded)
        self.assertNotIn("no-store", encoded)
        self.assertNotIn(MODULE.HEADER_MISMATCH["regexp"], encoded)

    def test_watchdog_handshake_rejects_tampering_and_staleness(self) -> None:
        client = FakeSyntheticClient()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = MODULE.run_watchdog_arm(
                client,
                root / "private.json",
                "body",
                WATCHDOG_NONCE,
                PARENT_RUN_ID,
                PARENT_RUN_ATTEMPT,
                PARENT_HEAD_SHA,
                WATCHDOG_RUN_ID,
                WATCHDOG_RUN_ATTEMPT,
            )
            for name, mutate in (
                ("target", lambda item: item.__setitem__("target", "canonical_homepage")),
                ("nonce", lambda item: item.__setitem__("watchdog_nonce_sha256", "0" * 64)),
                ("extra", lambda item: item.__setitem__("remote_target", "https://example.invalid")),
            ):
                with self.subTest(name=name):
                    changed = copy.deepcopy(report)
                    mutate(changed)
                    path = root / f"{name}.json"
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(MODULE.DrillError):
                        MODULE.validate_watchdog_handshake(
                            path,
                            WATCHDOG_NONCE,
                            "body",
                            PARENT_RUN_ID,
                            PARENT_RUN_ATTEMPT,
                            PARENT_HEAD_SHA,
                            WATCHDOG_RUN_ID,
                            WATCHDOG_RUN_ATTEMPT,
                        )

            stale = copy.deepcopy(report)
            stale["armed_at"] = "2020-01-01T00:00:00Z"
            stale_path = root / "stale.json"
            stale_path.write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaises(MODULE.DrillError):
                MODULE.validate_watchdog_handshake(
                    stale_path,
                    WATCHDOG_NONCE,
                    "body",
                    PARENT_RUN_ID,
                    PARENT_RUN_ATTEMPT,
                    PARENT_HEAD_SHA,
                    WATCHDOG_RUN_ID,
                    WATCHDOG_RUN_ATTEMPT,
                    now=MODULE.dt.datetime(2020, 1, 1, 1, tzinfo=MODULE.dt.timezone.utc),
                )

    def test_execute_refuses_if_remote_no_longer_matches_watchdog_snapshot(self) -> None:
        client = FakeSyntheticClient()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = MODULE.run_watchdog_arm(
                client,
                root / "watchdog-private.json",
                "status",
                WATCHDOG_NONCE,
                PARENT_RUN_ID,
                PARENT_RUN_ATTEMPT,
                PARENT_HEAD_SHA,
                WATCHDOG_RUN_ID,
                WATCHDOG_RUN_ATTEMPT,
            )
            handshake = root / "armed.json"
            handshake.write_text(json.dumps(report), encoding="utf-8")
            client.remote["description"] = "changed outside the drill"
            with self.assertRaises(MODULE.DrillError):
                MODULE.run_execute(
                    client,
                    FakeGrafanaClient(),
                    "grafanacloud-prom",
                    root / "parent-private.json",
                    "status",
                    30,
                    handshake,
                    WATCHDOG_NONCE,
                    PARENT_RUN_ID,
                    PARENT_RUN_ATTEMPT,
                    PARENT_HEAD_SHA,
                    WATCHDOG_RUN_ID,
                    WATCHDOG_RUN_ATTEMPT,
                )
        self.assertEqual(client.updates, [])

    def test_watchdog_restore_refuses_to_overwrite_unrelated_remote_change(self) -> None:
        client = FakeSyntheticClient()
        base = MODULE.update_payload(remote_check())
        client.remote["description"] = "new desired configuration"
        with self.assertRaises(MODULE.DrillError):
            MODULE.restore_exact_if_owned(client, base["id"], base, "header")
        self.assertEqual(client.updates, [])

    def test_official_update_operation_is_post(self) -> None:
        calls = []

        class CaptureClient(MODULE.SyntheticClient):
            def request(self, method, path, body=None):
                calls.append((method, path, body))
                return {}

        CaptureClient("https://synthetic-monitoring-api.grafana.net", "token").update_check(
            741, {"id": 741}
        )
        self.assertEqual(calls[0][:2], ("POST", "/api/v1/check/741"))

    def test_probe_state_requires_exact_current_config_and_two_samples(self) -> None:
        current = [
            {
                "probe": "probe-a",
                "config_version": "current-7",
                "timestamp": 1_010.0,
                "value": 1.0,
            },
            {
                "probe": "probe-b",
                "config_version": "current-7",
                "timestamp": 1_011.0,
                "value": 1.0,
            },
        ]
        result = MODULE.wait_for_probe_state(
            ProbeSampleClient(current),
            "grafanacloud-prom",
            1,
            1_000,
            0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["config_version_count"], 1)

    def test_probe_state_rejects_mixed_old_and_current_versions(self) -> None:
        mixed = [
            {
                "probe": "probe-a",
                "config_version": "old-6",
                "timestamp": 990.0,
                "value": 1.0,
            },
            {
                "probe": "probe-b",
                "config_version": "old-6",
                "timestamp": 991.0,
                "value": 1.0,
            },
            {
                "probe": "probe-a",
                "config_version": "current-7",
                "timestamp": 1_010.0,
                "value": 0.0,
            },
            {
                "probe": "probe-b",
                "config_version": "current-7",
                "timestamp": 1_011.0,
                "value": 0.0,
            },
        ]
        result = MODULE.wait_for_probe_state(
            ProbeSampleClient(mixed),
            "grafanacloud-prom",
            1,
            1_000,
            0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual(result["config_version_count"], 2)

    def test_probe_query_filters_on_source_sample_timestamp(self) -> None:
        paths: list[str] = []

        class CaptureGrafanaClient(MODULE.GrafanaClient):
            def request(self, method, path, body=None):
                paths.append(path)
                return {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "probe": "probe-a",
                                    "config_version": "current-7",
                                },
                                "value": [1_010, "1"],
                            }
                        ]
                    },
                }

        samples = CaptureGrafanaClient("https://nutsnews.grafana.net", "token").probe_samples(
            "grafanacloud-prom", 1_000
        )
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(paths[0]).query)["query"][0]
        self.assertIn("timestamp(probe_success", query)
        self.assertIn(">= 995", query)
        self.assertEqual(samples[0]["config_version"], "current-7")

    def test_unrelated_job_alert_neither_satisfies_firing_nor_blocks_recovery(self) -> None:
        client = AlertInstanceClient(
            [
                {
                    "alert_uid": MODULE.ALERT_UID,
                    "job": "canonical_homepage",
                    "probe": "probe-a",
                },
                {
                    "alert_uid": MODULE.ALERT_UID,
                    "job": "canonical_homepage",
                    "probe": "probe-b",
                },
            ]
        )
        firing = MODULE.wait_for_alert_state(
            client,
            True,
            0,
            expected_probes={"probe-a", "probe-b"},
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        recovered = MODULE.wait_for_alert_state(
            client,
            False,
            0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertEqual(firing["status"], "fail")
        self.assertEqual(recovered["status"], "pass")

    def test_firing_requires_selected_job_and_both_expected_probe_labels(self) -> None:
        unrelated = {
            "alert_uid": MODULE.ALERT_UID,
            "job": "canonical_homepage",
            "probe": "probe-c",
        }
        selected = [
            {
                "alert_uid": MODULE.ALERT_UID,
                "job": MODULE.JOB,
                "probe": "probe-a",
            },
            {
                "alert_uid": MODULE.ALERT_UID,
                "job": MODULE.JOB,
                "probe": "probe-b",
            },
        ]
        result = MODULE.wait_for_alert_state(
            AlertInstanceClient([unrelated, *selected]),
            True,
            0,
            expected_probes={"probe-a", "probe-b"},
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        incomplete = MODULE.wait_for_alert_state(
            AlertInstanceClient([unrelated, selected[0]]),
            True,
            0,
            expected_probes={"probe-a", "probe-b"},
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["probe_count"], 2)
        self.assertEqual(incomplete["status"], "fail")

    def test_selected_job_alert_blocks_recovery_even_with_unrelated_instances(self) -> None:
        client = AlertInstanceClient(
            [
                {
                    "alert_uid": MODULE.ALERT_UID,
                    "job": MODULE.JOB,
                    "probe": "probe-a",
                },
                {
                    "alert_uid": MODULE.ALERT_UID,
                    "job": "canonical_homepage",
                    "probe": "probe-b",
                },
            ]
        )
        result = MODULE.wait_for_alert_state(
            client,
            False,
            0,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
