#!/usr/bin/env python3
"""Unit tests for redacted Grafana Cloud post-apply verification helpers."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_post_apply.py"
SPEC = importlib.util.spec_from_file_location("verify_post_apply", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to import verify_post_apply.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def request(self, method: str, path: str) -> Any:
        if self.error:
            raise self.error
        return self.response


class RaisingOpener:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self, request: urllib.request.Request, timeout: int):
        raise self.error


def remote_synthetic_check(job: str, check_id: int, probes: list[int] | None = None) -> dict:
    body_matches: list[str] = []
    body_not_matches: list[str] = []
    headers: list[dict] = []
    target_path = "/readyz"
    if job == "canonical_homepage":
        target_path = "/"
        body_matches = ["maintenance"]
        body_not_matches = ["NutsNews"]
    elif job == "canonical_articles_api":
        target_path = "/api/articles"
        body_not_matches = ["articles"]
        headers = [
            {
                "allowMissing": False,
                "header": "Cache-Control",
                "regexp": "public|max-age|s-maxage",
            }
        ]
    else:
        identity = {
            "vps_readiness": "production-vps",
            "vercel_secondary_readiness": "vercel-production",
            "canonical_readiness": "production-vps|vercel-production",
        }[job]
        identity_pattern = f"({identity})" if "|" in identity else identity
        body_matches = ["deploymentTarget.*unknown"]
        body_not_matches = ["ready.*true", f"deploymentTarget.*{identity_pattern}"]
        headers = [
            {
                "allowMissing": False,
                "header": "Cache-Control",
                "regexp": "no-store",
            }
        ]
    return {
        "id": check_id,
        "job": job,
        "target": f"https://private-target.invalid{target_path}",
        "enabled": True,
        "frequency": 300_000,
        "timeout": 5_000,
        "probes": probes or [11, 22],
        "basicMetricsOnly": True,
        "alertSensitivity": "none",
        "labels": [
            {"name": "service_namespace", "value": "nutsnews"},
            {"name": "deployment_environment", "value": "production"},
            {"name": "check", "value": job},
            {"name": "owner", "value": "nutsnews-observability"},
            {"name": "service", "value": "synthetic-monitoring"},
        ],
        "settings": {
            "http": {
                "method": 0,
                "failIfNotSSL": True,
                "noFollowRedirects": True,
                "validStatusCodes": [200],
                "failIfBodyMatchesRegexp": body_matches,
                "failIfBodyNotMatchesRegexp": body_not_matches,
                "failIfHeaderNotMatchesRegexp": headers,
            }
        },
    }


class FakeSyntheticInventoryClient:
    def __init__(self, checks: list[dict]) -> None:
        self.checks = {check["id"]: check for check in checks}

    def request(self, method: str, path: str) -> Any:
        if method != "GET":
            raise AssertionError("inventory must be read-only")
        if path == "/api/v1/check":
            return [{"id": check_id, "job": check["job"]} for check_id, check in self.checks.items()]
        check_id = int(path.rsplit("/", 1)[1])
        return self.checks[check_id]


def protected_desired_checks(checks: list[dict]) -> dict[str, dict]:
    desired: dict[str, dict] = {}
    for check in checks:
        if check["job"] not in MODULE.EXPECTED_SYNTHETIC_CHECKS:
            continue
        http = check["settings"]["http"]
        desired[check["job"]] = {
            "target": check["target"],
            "enabled": True,
            "frequency_ms": check["frequency"],
            "timeout_ms": check["timeout"],
            "valid_status_codes": list(http["validStatusCodes"]),
            "fail_if_body_matches_regexp": list(http["failIfBodyMatchesRegexp"]),
            "fail_if_body_not_matches_regexp": list(http["failIfBodyNotMatchesRegexp"]),
            "fail_if_header_matches_regexp": [],
            "fail_if_header_not_matches_regexp": [
                {
                    "allow_missing": item["allowMissing"],
                    "header": item["header"],
                    "regexp": item["regexp"],
                }
                for item in http["failIfHeaderNotMatchesRegexp"]
            ],
        }
    return desired


def remote_slo(
    key: str = "public_availability",
    *,
    uuid: str = "slo-public-1",
    alerting_enabled: bool | None = None,
) -> dict:
    spec = MODULE.EXPECTED_SLO_SPECS[key]
    enabled = (
        bool(spec["alerting_enabled"])
        if alerting_enabled is None
        else alerting_enabled
    )
    item = {
        "uuid": uuid,
        "name": spec["name"],
        "description": spec["description"],
        "objectives": [{"value": spec["objective"], "window": "30d"}],
        "query": {"type": "freeform", "freeform": {"query": spec["query"]}},
        "destinationDatasource": {"uid": "grafanacloud-prom", "type": "prometheus"},
        "folder": {"uid": MODULE.GRAFANA_SLO_FOLDER_UID},
        "labels": [
            {"key": "deployment_environment", "value": "production"},
            {"key": "owner", "value": "nutsnews-observability"},
            {"key": "service", "value": spec["service"]},
        ],
        "readOnly": {
            "provenance": "terraform",
            "status": {"type": "created", "message": ""},
        },
        "alerting": None,
    }
    if enabled:
        item["alerting"] = {
            "labels": [
                {"key": "deployment_environment", "value": "production"},
                {"key": "owner", "value": "nutsnews-observability"},
                {"key": "route", "value": "operations-email"},
                {"key": "service", "value": spec["service"]},
            ],
            "annotations": [
                {
                    "key": "summary",
                    "value": f"{spec['name']} error budget burn requires operator attention.",
                },
                {"key": "dashboard_url", "value": spec["dashboard_url"]},
                {
                    "key": "runbook_url",
                    "value": MODULE.GRAFANA_OBSERVABILITY_RUNBOOK_URL,
                },
            ],
            "fastBurn": {
                "labels": [{"key": "severity", "value": "critical"}]
            },
            "slowBurn": {
                "labels": [{"key": "severity", "value": "warning"}]
            },
        }
    return item


class VerifyPostApplyTests(unittest.TestCase):
    def test_grafana_origins_are_pinned_to_their_api_roles(self) -> None:
        expected = {
            "https://nutsnews.grafana.net": "https://nutsnews.grafana.net",
            "https://nutsnews.grafana.net/": "https://nutsnews.grafana.net",
        }
        for value, canonical in expected.items():
            with self.subTest(value=value):
                self.assertEqual(
                    MODULE.validate_grafana_cloud_url(value, "GRAFANA_URL"),
                    canonical,
                )
        for value, canonical in {
            "https://synthetic-monitoring-api.grafana.net": (
                "https://synthetic-monitoring-api.grafana.net"
            ),
            "https://synthetic-monitoring-api.us.grafana.net:443/": (
                "https://synthetic-monitoring-api.us.grafana.net"
            ),
            "https://synthetic-monitoring-api-us-east-0.grafana.net": (
                "https://synthetic-monitoring-api-us-east-0.grafana.net"
            ),
        }.items():
            with self.subTest(value=value):
                self.assertEqual(MODULE.validate_synthetic_monitoring_url(value), canonical)
        for value in (
            "",
            "http://nutsnews.grafana.net",
            "https://grafana.net",
            "https://nutsnews.grafana.net.evil.invalid",
            "https://nutsnews.grafana.net/api",
            "https://nutsnews.grafana.net?token=secret",
            "https://user:secret@nutsnews.grafana.net",
            "https://nutsnews.grafana.net:444",
            "https://nutsnews.grafana.net:",
            "https://bad_.grafana.net",
            " https://nutsnews.grafana.net",
            "https://other-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.GrafanaClient(value, "sensitive-token")
        for value in (
            "https://nutsnews.grafana.net",
            "https://other-tenant.grafana.net",
            "https://synthetic-monitoring-apiattacker.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.SyntheticMonitoringClient(value, "sensitive-token")

    def test_redirect_is_rejected_before_a_second_authenticated_request(self) -> None:
        transport = RedirectingHTTPSHandler()
        client = MODULE.GrafanaClient(
            "https://nutsnews.grafana.net", "sensitive-token"
        )
        client.opener = urllib.request.build_opener(
            MODULE.NoRedirectHandler(), transport
        )

        with self.assertRaisesRegex(RuntimeError, "failed with HTTP 302"):
            client.request("GET", "/api/health")

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-token",
        )
        self.assertEqual(
            transport.requests[0].full_url,
            "https://nutsnews.grafana.net/api/health",
        )

    def test_grafana_transport_errors_are_status_and_path_only(self) -> None:
        sentinel = "https://protected-synthetic-target.invalid/readyz?key=secret"
        client = MODULE.GrafanaClient(
            "https://nutsnews.grafana.net", "sensitive-token"
        )
        headers = Message()
        http_error = urllib.error.HTTPError(
            "https://nutsnews.grafana.net/api/health",
            502,
            f"attacker reason {sentinel}",
            headers,
            io.BytesIO(f"attacker body {sentinel}".encode()),
        )
        client.opener = RaisingOpener(http_error)
        with self.assertRaises(RuntimeError) as raised:
            client.request("GET", f"/api/health?upstream={sentinel}")
        self.assertEqual(
            str(raised.exception),
            "Grafana API GET /api/health failed with HTTP 502",
        )
        self.assertNotIn("protected-synthetic-target", str(raised.exception))

        client.opener = RaisingOpener(
            urllib.error.URLError(f"attacker transport reason {sentinel}")
        )
        with self.assertRaises(RuntimeError) as raised:
            client.request("GET", f"/api/health?upstream={sentinel}")
        self.assertEqual(
            str(raised.exception),
            "Grafana API GET /api/health failed before an HTTP response",
        )
        self.assertNotIn("protected-synthetic-target", str(raised.exception))

    def test_urlsplit_nfkc_failure_never_echoes_untrusted_netloc(self) -> None:
        sentinel = "protected-synthetic-target.invalid"
        malformed = f"https://nutsnews.grafana.net\uff0f{sentinel}"
        with self.assertRaises(ValueError) as raised:
            MODULE.validate_grafana_cloud_url(malformed, "GRAFANA_URL")
        self.assertEqual(
            str(raised.exception),
            "GRAFANA_URL must be a query-free HTTPS nutsnews.grafana.net Grafana UI API origin",
        )
        self.assertNotIn(sentinel, str(raised.exception))

        checks = [
            remote_synthetic_check(job, index + 100)
            for index, job in enumerate(sorted(MODULE.EXPECTED_SYNTHETIC_CHECKS))
        ]
        checks[0]["target"] = malformed
        with self.assertRaises(ValueError) as raised:
            MODULE.parse_desired_synthetic_checks(json.dumps({
                check["job"]: protected_desired_checks([check])[check["job"]]
                for check in checks
            }))
        self.assertNotIn(sentinel, str(raised.exception))

    def test_production_ownership_queries_are_exact_and_freshness_gated(self) -> None:
        vps_query = MODULE.PROMETHEUS_QUERIES["vps_production_ownership"][0]
        for token in (
            'job="integrations/node_exporter"',
            'instance="vps.nutsnews.com"',
            'service_namespace="nutsnews"',
            'service="host-exporter"',
            'host="vps.nutsnews.com"',
            'nutsnews_production_ownership_available',
            'nutsnews_production_ownership_last_success_timestamp_seconds',
            '< 300',
        ):
            self.assertIn(token, vps_query)

        for name in (
            "backend_worker_uplift_ownership_available",
            "backend_worker_uplift_expected_active",
            "backend_worker_uplift_deployment_info",
        ):
            query = MODULE.PROMETHEUS_QUERIES[name][0]
            for token in (
                'job="nutsnews-backend-host"',
                'instance="backend.nutsnews.com"',
                'service_namespace="nutsnews"',
                'service="host"',
                'environment="production"',
                'deployment_environment="production"',
                'host="backend.nutsnews.com"',
                'nutsnews_backend_metric_scrape_timestamp_seconds',
                '< 600',
            ):
                self.assertIn(token, query)

    def test_prometheus_query_reports_result_count(self) -> None:
        client = FakeClient(
            {
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"service": "scheduler"}, "value": [1, "1"]}
                    ]
                },
            }
        )
        result = MODULE.prometheus_query(client, "prom", "up")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["series_labels"], [{"service": "scheduler"}])
        self.assertEqual(result["sample_values"], [1.0])
        self.assertEqual(result["non_finite_sample_count"], 0)
        self.assertEqual(result["invalid_sample_count"], 0)

    def test_whole_report_removes_label_values_and_url_sentinels(self) -> None:
        sentinel = "https://protected-synthetic-target.invalid/readyz?key=secret"
        validation_result = {
            "query": "probe_success",
            "status": "success",
            "result_count": 1,
            "series_labels": [
                {
                    "instance": sentinel,
                    "probe": "public-probe-a",
                    "attacker_label": sentinel,
                }
            ],
            "sample_values": [1.0],
        }
        raw_report = {
            "status": "fail",
            "prometheus_queries": {"synthetic": validation_result},
            "errors": [f"upstream returned {sentinel}"],
            sentinel: {"detail": sentinel},
        }

        safe_report = MODULE.sanitize_report_for_output(raw_report)
        serialized = json.dumps(safe_report, sort_keys=True)

        self.assertEqual(
            validation_result["series_labels"][0]["instance"], sentinel
        )
        self.assertNotIn("protected-synthetic-target.invalid", serialized)
        self.assertNotIn("key=secret", serialized)
        query_report = safe_report["prometheus_queries"]["synthetic"]
        self.assertNotIn("series_labels", query_report)
        self.assertEqual(
            query_report["series_labels_summary"],
            {
                "series_count": 1,
                "invalid_series_count": 0,
                "allowlisted_label_keys": ["probe"],
            },
        )
        self.assertEqual(query_report["sample_values"], [1.0])
        self.assertEqual(safe_report["errors"], ["upstream returned [redacted-url]"])

    def test_alert_evidence_retains_only_structural_status_summaries(self) -> None:
        sentinel = "opaque-provider-alert-sentinel"
        raw_report = {
            "alert_rule_health": {
                f"private-rule-{sentinel}": {
                    "health": "error",
                    "last_error": sentinel,
                    "state": "Error",
                    "labels": {sentinel: sentinel},
                },
                "healthy-rule": {
                    "health": "ok",
                    "lastError": "",
                    "state": "Normal",
                },
            },
            "datasource_generated_alerts": [
                {
                    "alertname": "DatasourceError",
                    "labels": {"alertname": "DatasourceError", sentinel: sentinel},
                },
                {
                    "alertname": [sentinel],
                    "labels": {"alertname": sentinel, "detail": sentinel},
                },
            ],
            "rules": [
                {
                    "labels": {"owner": sentinel},
                    "annotations": {"summary": sentinel},
                    "lastError": sentinel,
                }
            ],
        }

        safe_report = MODULE.sanitize_report_for_output(raw_report)
        serialized = json.dumps(safe_report, sort_keys=True)

        self.assertNotIn(sentinel, serialized)
        self.assertEqual(
            safe_report["alert_rule_health"],
            {
                "rule_count": 2,
                "invalid_rule_count": 0,
                "rules_with_error_detail_count": 1,
                "health_status_counts": {"error": 1, "ok": 1},
                "state_status_counts": {"error": 1, "normal": 1},
            },
        )
        self.assertEqual(
            safe_report["datasource_generated_alerts"],
            {
                "active_alert_count": 2,
                "invalid_alert_count": 1,
                "alert_type_counts": {
                    "DatasourceError": 1,
                    "DatasourceNoData": 0,
                    "other": 1,
                },
            },
        )
        self.assertEqual(
            safe_report["rules"],
            [
                {
                    "label_structure": {
                        "entry_count": 1,
                        "container_type": "mapping",
                    },
                    "annotation_structure": {
                        "entry_count": 1,
                        "container_type": "mapping",
                    },
                }
            ],
        )
        self.assertEqual(
            raw_report["alert_rule_health"][f"private-rule-{sentinel}"][
                "last_error"
            ],
            sentinel,
        )

    def test_prometheus_query_uses_latest_matrix_sample(self) -> None:
        client = FakeClient(
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"service": "publication"},
                            "values": [[1, "0"], [2, "1"]],
                        }
                    ]
                },
            }
        )
        result = MODULE.prometheus_query(client, "prom", "up")
        self.assertEqual(result["sample_values"], [1.0])
        self.assertEqual(result["result_count"], 1)

    def test_prometheus_query_rejects_non_finite_and_invalid_samples(self) -> None:
        client = FakeClient(
            {
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"kind": "nan"}, "value": [1, "NaN"]},
                        {"metric": {"kind": "infinity"}, "value": [1, "+Inf"]},
                        {"metric": {"kind": "bad"}, "value": [1, "not-a-number"]},
                        {"metric": {"kind": "missing"}},
                        {"metric": {"kind": "finite"}, "value": [1, "6.5"]},
                    ]
                },
            }
        )
        result = MODULE.prometheus_query(client, "prom", "up")
        self.assertEqual(result["result_count"], 5)
        self.assertEqual(result["sample_values"], [6.5])
        self.assertEqual(result["non_finite_sample_count"], 2)
        self.assertEqual(result["invalid_sample_count"], 2)

    def test_prometheus_no_data_is_explicit(self) -> None:
        client = FakeClient({"status": "success", "data": {"result": []}})
        result = MODULE.prometheus_query(client, "prom", "up")
        self.assertEqual(result["result_count"], 0)

    def test_api_error_is_retained_for_artifact(self) -> None:
        errors: list[str] = []
        result = MODULE.safe_check(
            "usage query",
            lambda: (_ for _ in ()).throw(RuntimeError("HTTP 503")),
            errors,
            {"status": "error"},
        )
        self.assertEqual(result, {"status": "error"})
        self.assertEqual(errors, ["usage query: HTTP 503"])

    def test_contact_point_requires_email_and_resolved_delivery(self) -> None:
        response = [
            {
                "name": MODULE.CONTACT_POINT_NAME,
                "grafana_managed_receiver_configs": [
                    {
                        "type": "email",
                        "disableResolveMessage": False,
                        "settings": {"addresses": "ops@example.invalid"},
                    }
                ],
            }
        ]
        errors: list[str] = []
        result = MODULE.summarize_contact_points(response, errors)
        self.assertFalse(errors)
        self.assertTrue(result[0]["resolved_notifications_enabled"])
        self.assertTrue(result[0]["recipient_configuration_present"])

    def test_direct_provisioning_contact_point_shape_is_supported(self) -> None:
        response = [
            {
                "name": MODULE.CONTACT_POINT_NAME,
                "type": "email",
                "disableResolveMessage": False,
                "settings": {"addresses": "ops@example.invalid"},
            }
        ]
        errors: list[str] = []
        result = MODULE.summarize_contact_points(response, errors)
        self.assertFalse(errors)
        self.assertEqual(result[0]["email_integration_count"], 1)

    def test_policy_timing_contract(self) -> None:
        policy = {
            "receiver": MODULE.CONTACT_POINT_NAME,
            "group_by": ["alertname", "service", "deployment_environment"],
            "group_wait": "5m",
            "group_interval": "15m",
            "repeat_interval": "6h",
            "routes": [
                {
                    "receiver": MODULE.CONTACT_POINT_NAME,
                    "object_matchers": [["severity", "=~", "critical|major"]],
                    "group_by": ["alertname", "service", "deployment_environment"],
                    "group_wait": "30s",
                    "group_interval": "5m",
                    "repeat_interval": "1h",
                },
                {
                    "receiver": MODULE.CONTACT_POINT_NAME,
                    "object_matchers": [["severity", "=~", "warning|minor|low"]],
                    "group_by": ["alertname", "service", "deployment_environment"],
                    "group_wait": "5m",
                    "group_interval": "15m",
                    "repeat_interval": "6h",
                },
            ],
        }
        errors: list[str] = []
        result = MODULE.verify_notification_policy(policy, errors)
        self.assertFalse(errors)
        self.assertEqual(len(result["routes"]), 2)

    def test_policy_structural_drift_is_rejected(self) -> None:
        base = {
            "receiver": MODULE.CONTACT_POINT_NAME,
            "group_by": ["alertname", "service", "deployment_environment"],
            "group_wait": "5m",
            "group_interval": "15m",
            "repeat_interval": "6h",
            "routes": [
                {
                    "receiver": MODULE.CONTACT_POINT_NAME,
                    "object_matchers": [["severity", "=~", "critical|major"]],
                    "group_by": ["alertname", "service", "deployment_environment"],
                    "group_wait": "30s",
                    "group_interval": "5m",
                    "repeat_interval": "1h",
                },
                {
                    "receiver": MODULE.CONTACT_POINT_NAME,
                    "object_matchers": [["severity", "=~", "warning|minor|low"]],
                    "group_by": ["alertname", "service", "deployment_environment"],
                    "group_wait": "5m",
                    "group_interval": "15m",
                    "repeat_interval": "6h",
                },
            ],
        }
        mutations = {
            "wrong matcher label": lambda policy: policy["routes"][0]["object_matchers"][0].__setitem__(0, "service"),
            "wrong matcher operator": lambda policy: policy["routes"][0]["object_matchers"][0].__setitem__(1, "="),
            "wrong child receiver": lambda policy: policy["routes"][0].__setitem__("receiver", "empty"),
            "wrong child group_by": lambda policy: policy["routes"][0].__setitem__("group_by", ["alertname"]),
            "wrong root timing": lambda policy: policy.__setitem__("group_wait", "30s"),
            "duplicate route": lambda policy: policy["routes"].append(dict(policy["routes"][0])),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                policy = json.loads(json.dumps(base))
                mutate(policy)
                errors: list[str] = []
                MODULE.verify_notification_policy(policy, errors)
                self.assertTrue(errors)

    def test_ruler_health_extracts_uid_and_error(self) -> None:
        response = {
            "data": {
                "groups": [
                    {
                        "rules": [
                            {
                                "grafana_alert": {"uid": "rule-1"},
                                "health": "error",
                                "lastError": "datasource unavailable",
                                "state": "Error",
                            }
                        ]
                    }
                ]
            }
        }
        health = MODULE.ruler_health(response)
        self.assertEqual(health["rule-1"]["health"], "error")
        self.assertEqual(health["rule-1"]["last_error"], "datasource unavailable")

    def test_loki_query_range_returns_stream_labels_and_line_count(self) -> None:
        labels = {label: f"value-{label}" for label in MODULE.LOKI_INDEXED_LABELS}
        client = FakeClient(
            {
                "status": "success",
                "data": {
                    "result": [
                        {"stream": labels, "values": [["1", "first"], ["2", "second"]]}
                    ]
                },
            }
        )
        result = MODULE.loki_query_range(client, "loki", "{service=\"web\"}", 1)
        self.assertEqual(result["line_count"], 2)
        self.assertEqual(result["stream_labels"], [labels])

    def test_loki_label_contract_rejects_extra_and_missing_indexed_labels(self) -> None:
        exact = {label: f"value-{label}" for label in MODULE.LOKI_INDEXED_LABELS}
        errors: list[str] = []
        MODULE.validate_loki_stream_labels("exact", [exact], errors)
        self.assertFalse(errors)

        invalid = dict(exact)
        invalid.pop("severity")
        invalid["correlation_id"] = "must-be-structured-metadata"
        MODULE.validate_loki_stream_labels("invalid", [invalid], errors)
        self.assertTrue(any("unapproved indexed labels" in error for error in errors))
        self.assertTrue(any("missing normalized indexed labels" in error for error in errors))

    def test_disabled_sync_relay_does_not_require_a_recent_log_line(self) -> None:
        self.assertFalse(
            MODULE.loki_log_is_required("backend_sync_relay_logs", "not_configured")
        )
        self.assertTrue(MODULE.loki_log_is_required("backend_sync_relay_logs", "pass"))
        self.assertTrue(MODULE.loki_log_is_required("backend_api_logs", "not_configured"))

    def test_list_items_descends_common_api_envelopes(self) -> None:
        payload = {"data": {"results": [{"uuid": "slo-1"}, "ignored"]}}
        self.assertEqual(MODULE.list_items(payload), [{"uuid": "slo-1"}])

    def test_ruler_rules_flattens_recording_and_alert_rules(self) -> None:
        response = {
            "data": {
                "groups": [
                    {
                        "name": "slo-generated",
                        "rules": [
                            {"name": "sli", "type": "recording", "health": "ok"},
                            {"name": "fastburn", "type": "alerting", "health": "ok"},
                        ],
                    }
                ]
            }
        }
        rules = MODULE.ruler_rules(response)
        self.assertEqual([rule["type"] for rule in rules], ["recording", "alerting"])
        self.assertTrue(all(rule["group"] == "slo-generated" for rule in rules))

    def test_remote_slo_contract_requires_exact_query_datasource_labels_and_provenance(self) -> None:
        item = remote_slo()
        errors: list[str] = []
        MODULE.verify_remote_slo_contract(
            "public_availability",
            item,
            MODULE.EXPECTED_SLO_SPECS["public_availability"],
            "grafanacloud-prom",
            True,
            errors,
        )
        self.assertFalse(errors)

        drifted = json.loads(json.dumps(item))
        drifted["query"]["freeform"]["query"] = "vector(1)"
        drifted["destinationDatasource"]["uid"] = "wrong-prom"
        drifted["labels"].append(
            {"key": "owner", "value": "duplicate-must-fail"}
        )
        drifted["readOnly"]["provenance"] = "api"
        drift_errors: list[str] = []
        MODULE.verify_remote_slo_contract(
            "public_availability",
            drifted,
            MODULE.EXPECTED_SLO_SPECS["public_availability"],
            "grafanacloud-prom",
            True,
            drift_errors,
        )
        self.assertTrue(any("exact freeform query mismatch" in error for error in drift_errors))
        self.assertTrue(any("destination datasource mismatch" in error for error in drift_errors))
        self.assertTrue(any("duplicate" in error for error in drift_errors))
        self.assertTrue(any("not Terraform-owned" in error for error in drift_errors))

    def test_shadow_worker_slo_requires_alerting_to_be_absent(self) -> None:
        item = remote_slo("worker_terminal_success", alerting_enabled=False)
        errors: list[str] = []
        MODULE.verify_remote_slo_contract(
            "worker_terminal_success",
            item,
            MODULE.EXPECTED_SLO_SPECS["worker_terminal_success"],
            "grafanacloud-prom",
            False,
            errors,
        )
        self.assertFalse(errors)
        item["alerting"] = {}
        MODULE.verify_remote_slo_contract(
            "worker_terminal_success",
            item,
            MODULE.EXPECTED_SLO_SPECS["worker_terminal_success"],
            "grafanacloud-prom",
            False,
            errors,
        )
        self.assertTrue(any("unexpectedly has API alerting" in error for error in errors))

    def test_wait_for_remote_slo_retries_transitional_state_and_rejects_error(self) -> None:
        class LifecycleClient:
            def __init__(self, states: list[tuple[str, str]]) -> None:
                self.states = iter(states)

            def request(self, method: str, path: str) -> dict:
                self.assertions = (method, path)
                status, message = next(self.states)
                item = remote_slo()
                item["readOnly"]["status"] = {"type": status, "message": message}
                return item

        times = iter([0.0, 0.0, 1.0])
        settled = MODULE.wait_for_remote_slo(
            LifecycleClient([("updating", ""), ("updated", "")]),
            "slo-public-1",
            timeout_seconds=30,
            sleep=lambda _: None,
            monotonic=lambda: next(times),
        )
        self.assertEqual(settled["readOnly"]["status"]["type"], "updated")
        with self.assertRaisesRegex(RuntimeError, "entered error lifecycle state"):
            MODULE.wait_for_remote_slo(
                LifecycleClient([("error", "generated rule failed")]),
                "slo-public-1",
                timeout_seconds=0,
                sleep=lambda _: None,
                monotonic=lambda: 0,
            )

    def test_recorded_slo_samples_query_only_exact_sli_and_objective_metrics(self) -> None:
        calls: list[str] = []

        class RecordedMetricClient:
            def request(self, method: str, path: str) -> dict:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["query"][0]
                calls.append(query)
                metric = query.split("{", 1)[0]
                value = "0.995" if metric == "grafana_slo_objective" else "0.999"
                return {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "__name__": metric,
                                    "grafana_slo_uuid": "slo-public-1",
                                },
                                "value": [1, value],
                            }
                        ]
                    },
                }

        errors: list[str] = []
        result = MODULE.verify_recorded_slo_samples(
            RecordedMetricClient(),
            "grafanacloud-prom",
            "public_availability",
            "slo-public-1",
            0.995,
            True,
            errors,
        )
        self.assertFalse(errors)
        self.assertEqual(set(result), set(MODULE.GRAFANA_SLO_RECORDED_METRICS))
        self.assertEqual(
            calls,
            [
                f'{metric}{{grafana_slo_uuid="slo-public-1"}}'
                for metric in MODULE.GRAFANA_SLO_RECORDED_METRICS
            ],
        )
        self.assertTrue(all("__name__=~" not in query for query in calls))

    def test_enabled_slo_recorded_samples_poll_until_rules_evaluate(self) -> None:
        calls = 0

        class DelayedRecordedMetricClient:
            def request(self, method: str, path: str) -> dict:
                nonlocal calls
                calls += 1
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["query"][0]
                metric = query.split("{", 1)[0]
                if calls <= len(MODULE.GRAFANA_SLO_RECORDED_METRICS):
                    result = []
                else:
                    value = "0.995" if metric == "grafana_slo_objective" else "0.999"
                    result = [
                        {
                            "metric": {
                                "__name__": metric,
                                "grafana_slo_uuid": "slo-public-1",
                            },
                            "value": [1, value],
                        }
                    ]
                return {"status": "success", "data": {"result": result}}

        errors: list[str] = []
        MODULE.verify_recorded_slo_samples(
            DelayedRecordedMetricClient(),
            "grafanacloud-prom",
            "public_availability",
            "slo-public-1",
            0.995,
            True,
            errors,
            timeout_seconds=30,
            sleep=lambda _: None,
            monotonic=lambda: 0,
        )
        self.assertFalse(errors)
        self.assertEqual(calls, 2 * len(MODULE.GRAFANA_SLO_RECORDED_METRICS))

    def test_shadow_worker_slo_accepts_coherent_no_terminal_event_samples(self) -> None:
        class EmptyRecordedMetricClient:
            def request(self, method: str, path: str) -> dict:
                return {"status": "success", "data": {"result": []}}

        errors: list[str] = []
        result = MODULE.verify_recorded_slo_samples(
            EmptyRecordedMetricClient(),
            "grafanacloud-prom",
            "worker_terminal_success",
            "slo-worker-1",
            0.99,
            True,
            errors,
            require_samples=False,
        )
        self.assertFalse(errors)
        self.assertTrue(
            all(item["result_count"] == 0 for item in result.values())
        )

    def test_shadow_rollout_requires_all_runtime_identities(self) -> None:
        errors: list[str] = []
        phase = MODULE.validate_worker_runtime_identity_rollout(
            0,
            {"scheduler"},
            {"scheduler", "fetcher"},
            errors,
        )
        self.assertEqual(phase, "shadow-runtime-identity-visible")
        self.assertTrue(any("deployment-info for all eight" in error for error in errors))
        self.assertTrue(any("build identity for all eight" in error for error in errors))

        complete_errors: list[str] = []
        MODULE.validate_worker_runtime_identity_rollout(
            0,
            set(MODULE.WORKER_SERVICES),
            set(MODULE.WORKER_SERVICES),
            complete_errors,
        )
        self.assertFalse(complete_errors)

    def test_production_rollout_requires_runtime_identity_for_all_workers(self) -> None:
        errors: list[str] = []
        phase = MODULE.validate_worker_runtime_identity_rollout(
            1,
            {"scheduler"},
            {"scheduler", "fetcher"},
            errors,
        )
        self.assertEqual(phase, "production-runtime-v1-required")
        self.assertTrue(any("deployment-info for all eight" in error for error in errors))
        self.assertTrue(any("build identity for all eight" in error for error in errors))

        complete_errors: list[str] = []
        MODULE.validate_worker_runtime_identity_rollout(
            1,
            set(MODULE.WORKER_SERVICES),
            set(MODULE.WORKER_SERVICES),
            complete_errors,
        )
        self.assertFalse(complete_errors)

    def test_deployed_worker_identity_requires_all_immutable_image_refs(self) -> None:
        labels = [
            {
                "worker_service": service,
                "service_version": "1.0.0",
                "revision": f"revision-{service}",
                "image_digest": "sha256:" + "a" * 64,
            }
            for service in sorted(MODULE.WORKER_SERVICES)
        ]
        errors: list[str] = []
        identities = MODULE.validate_deployed_worker_identities(labels, errors)
        self.assertFalse(errors)
        self.assertEqual(set(identities), MODULE.WORKER_SERVICES)

    def test_deployed_worker_identity_rejects_missing_and_mutable_images(self) -> None:
        errors: list[str] = []
        MODULE.validate_deployed_worker_identities(
            [
                {
                    "worker_service": "scheduler",
                    "service_version": "unknown",
                    "revision": "unknown",
                    "image_digest": "ghcr.io/ramideltoro/nutsnews-worker:latest",
                }
            ],
            errors,
        )
        self.assertTrue(any("cover all eight" in error for error in errors))
        self.assertTrue(any("service version" in error for error in errors))
        self.assertTrue(any("immutable revision" in error for error in errors))
        self.assertTrue(any("immutable sha256 image digest" in error for error in errors))

    def test_remote_synthetic_inventory_is_exact_bounded_and_target_free(self) -> None:
        checks = [
            remote_synthetic_check(job, index + 100)
            for index, job in enumerate(sorted(MODULE.EXPECTED_SYNTHETIC_CHECKS))
        ]
        managed_ids = {check["job"]: str(check["id"]) for check in checks}
        desired_checks = protected_desired_checks(checks)
        readiness = next(check for check in checks if check["job"] == "canonical_readiness")
        readiness["settings"]["http"]["failIfBodyNotMatchesRegexp"].reverse()
        selected_probes = {
            "probe-a": {"id": 11, "public": True},
            "probe-b": {"id": 22, "public": True},
        }
        errors: list[str] = []
        inventory = MODULE.remote_synthetic_inventory(
            FakeSyntheticInventoryClient(checks),
            managed_ids,
            selected_probes,
            desired_checks,
            errors,
        )
        self.assertFalse(errors)
        self.assertEqual(inventory["enabled_api_check_count"], 5)
        self.assertEqual(inventory["enabled_browser_check_count"], 0)
        self.assertEqual(inventory["monthly_api_execution_estimate"], 86_400)
        self.assertNotIn("private-target.invalid", json.dumps(inventory))
        self.assertNotIn("target", json.dumps(inventory))

    def test_remote_synthetic_inventory_rejects_probe_and_browser_drift(self) -> None:
        checks = [
            remote_synthetic_check(job, index + 100)
            for index, job in enumerate(sorted(MODULE.EXPECTED_SYNTHETIC_CHECKS))
        ]
        checks[0]["probes"] = [11, 99]
        browser = remote_synthetic_check("canonical_homepage", 999)
        browser["job"] = "unmanaged_browser"
        browser["settings"] = {"browser": {}}
        checks.append(browser)
        managed_ids = {
            check["job"]: str(check["id"])
            for check in checks
            if check["job"] in MODULE.EXPECTED_SYNTHETIC_CHECKS
        }
        errors: list[str] = []
        MODULE.remote_synthetic_inventory(
            FakeSyntheticInventoryClient(checks),
            managed_ids,
            {
                "probe-a": {"id": 11, "public": True},
                "probe-b": {"id": 22, "public": True},
            },
            protected_desired_checks(checks),
            errors,
        )
        self.assertTrue(any("protected public selection" in error for error in errors))
        self.assertTrue(any("no browser checks" in error for error in errors))

    def test_remote_synthetic_contract_rejects_assertion_membership_drift(self) -> None:
        check = remote_synthetic_check("canonical_readiness", 101)
        desired = json.loads(
            json.dumps(protected_desired_checks([check])["canonical_readiness"])
        )
        check["settings"]["http"]["failIfBodyNotMatchesRegexp"].append("unexpected")
        errors: list[str] = []
        MODULE.validate_remote_synthetic_contract(check, {11, 22}, desired, errors)
        self.assertTrue(any("assertion families differ" in error for error in errors))

    def test_remote_synthetic_contract_requires_redirect_rejection(self) -> None:
        check = remote_synthetic_check("canonical_readiness", 101)
        desired = protected_desired_checks([check])["canonical_readiness"]
        del check["settings"]["http"]["noFollowRedirects"]
        errors: list[str] = []
        MODULE.validate_remote_synthetic_contract(check, {11, 22}, desired, errors)
        self.assertTrue(any("must reject redirects" in error for error in errors))

    def test_postapply_rejects_ineffective_desired_assertions(self) -> None:
        mutations = (
            ("fail_if_body_not_matches_regexp", ["ready|true|deploymentTarget"]),
            ("fail_if_body_not_matches_regexp", ["(?!)NutsNews"]),
            (
                "fail_if_header_not_matches_regexp",
                [
                    {
                        "allow_missing": False,
                        "header": "Cache-Control",
                        "regexp": "no-store(?!)",
                    }
                ],
            ),
        )
        jobs = ("canonical_readiness", "canonical_homepage", "vps_readiness")
        for job, (field, value) in zip(jobs, mutations):
            check = remote_synthetic_check(job, 101)
            desired = protected_desired_checks([check])[job]
            desired[field] = value
            errors: list[str] = []
            MODULE.validate_remote_synthetic_contract(check, {11, 22}, desired, errors)
            self.assertTrue(
                any("approved behavioral contract" in error for error in errors),
                errors,
            )

    def test_remote_synthetic_contract_independently_rejects_query_and_nondefault_port(self) -> None:
        for target in (
            "https://private-target.invalid:8443/readyz",
            "https://private-target.invalid/readyz?cached=true",
            "https://localhost/readyz",
            "https://private%2etarget.invalid/readyz",
            "https://private-target.invalid\\readyz",
            "https://user:secret@private-target.invalid/readyz",
        ):
            with self.subTest(target=target):
                check = remote_synthetic_check("canonical_readiness", 101)
                check["target"] = target
                desired = protected_desired_checks([check])["canonical_readiness"]
                errors: list[str] = []
                MODULE.validate_remote_synthetic_contract(
                    check, {11, 22}, desired, errors
                )
                self.assertTrue(
                    any("approved read-only HTTPS route" in error for error in errors)
                )

    def test_current_synthetic_probe_contract_rejects_mixed_config_versions(self) -> None:
        result = {
            "result_count": 2,
            "series_labels": [
                {"probe": "probe-a", "config_version": "old"},
                {"probe": "probe-b", "config_version": "current"},
            ],
            "sample_values": [1.0, 1.0],
            "sample_timestamps": [1_010.0, 1_011.0],
        }
        self.assertFalse(MODULE.synthetic_probe_result_is_current(result, 1_000))

    def test_external_inventory_validates_kind_context_health_and_upgrade_state(self) -> None:
        catalog = {
            "folderUid": "integration-folder",
            "owner": "vendor",
            "source": "integration",
            "managedByLabel": {"key": "__converted_prometheus_rule__", "value": "true"},
            "contextPolicy": {
                "requiredAlertLabels": [
                    "severity",
                    "owner",
                    "route",
                    "service",
                    "deployment_environment",
                    "__converted_prometheus_rule__",
                ],
                "requiredAlertLabelValues": {
                    "owner": "nutsnews-observability",
                    "route": "operations-email",
                    "service": "vps-host",
                    "deployment_environment": "production",
                    "__converted_prometheus_rule__": "true",
                },
                "severityNormalization": {
                    "info": "low",
                    "warning": "warning",
                    "critical": "critical",
                },
                "requiredAlertAnnotations": [
                    "summary",
                    "description",
                    "dashboard_url",
                    "runbook_url",
                ],
                "requiredAlertAnnotationValues": {
                    "dashboard_url": "/d/nutsnews-vps-overview"
                },
                "normalizationStatus": "blocked_pending_owned_replacements_or_supported_vendor_relabel",
            },
            "definitionFingerprintPolicy": {
                "algorithm": "sha256",
                "requiredDisposition": "retain",
                "baselineStatus": "pending_authenticated_rollout",
            },
            "legacyObservedRuleCount": 3,
            "expectedRetainedRuleCount": 2,
            "expectedPostUpgradeRuleCount": 2,
            "expectedPostUpgradeKindCounts": {"alert": 1, "recording": 1},
            "rules": [
                {
                    "uid": "alert-1",
                    "group": "alerts",
                    "title": "NodeAlert",
                    "kind": "alert",
                    "severity": "warning",
                    "disposition": "retain",
                },
                {
                    "uid": "record-1",
                    "group": "records",
                    "title": "node:record",
                    "kind": "recording",
                    "disposition": "retain",
                },
                {
                    "uid": "obsolete-1",
                    "group": "asserts-node.rules",
                    "title": "asserts:resource:total",
                    "kind": "recording",
                    "disposition": "remove_via_integration_upgrade",
                },
            ],
        }
        marker = {"__converted_prometheus_rule__": "true"}
        normalized_context = {
            **marker,
            "owner": "nutsnews-observability",
            "route": "operations-email",
            "service": "vps-host",
            "deployment_environment": "production",
        }
        provisioned = {
            "alert-1": {
                "uid": "alert-1",
                "folderUID": "integration-folder",
                "ruleGroup": "alerts",
                "title": "NodeAlert",
                "labels": {**normalized_context, "severity": "warning"},
                "annotations": {
                    "summary": "summary",
                    "description": "description",
                    "dashboard_url": "/d/nutsnews-vps-overview",
                    "runbook_url": "https://example.invalid/runbook",
                },
                "data": [{"model": {"expr": "up == 0"}}],
                "noDataState": "NoData",
                "execErrState": "Error",
            },
            "record-1": {
                "uid": "record-1",
                "folderUID": "integration-folder",
                "ruleGroup": "records",
                "title": "node:record",
                "labels": marker,
                "record": {"metric": "node:record"},
                "data": [{"model": {"expr": "sum(up)"}}],
            },
        }
        ruler = {
            "alert-1": {"uid": "alert-1", "type": "alerting", "query": "up == 0", "health": "ok"},
            "record-1": {"uid": "record-1", "type": "recording", "query": "sum(up)", "health": "ok"},
        }
        bootstrap_errors: list[str] = []
        bootstrap_inventory = MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {"alert-1": {"health": "ok", "last_error": ""}},
            bootstrap_errors,
        )
        self.assertTrue(any("pending authenticated operator review" in error for error in bootstrap_errors))
        self.assertTrue(any("explicit rollout blocker" in error for error in bootstrap_errors))
        observed_fingerprints = {
            item["uid"]: item["definition_fingerprint_sha256"]
            for item in bootstrap_inventory
            if item["disposition"] == "retain"
        }
        for expected in catalog["rules"]:
            if expected["disposition"] == "retain":
                expected["definitionFingerprintSha256"] = observed_fingerprints[
                    expected["uid"]
                ]
        catalog["definitionFingerprintPolicy"]["baselineStatus"] = "approved"
        catalog["contextPolicy"]["normalizationStatus"] = "approved"

        errors: list[str] = []
        inventory = MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {"alert-1": {"health": "ok", "last_error": ""}},
            errors,
        )
        self.assertFalse(errors)
        self.assertEqual(len(inventory), 3)
        self.assertEqual(
            next(item for item in inventory if item["uid"] == "obsolete-1")["state"],
            "removed-by-supported-integration-upgrade",
        )
        fingerprints = [
            item["definition_fingerprint_sha256"]
            for item in inventory
            if item["disposition"] == "retain"
        ]
        self.assertTrue(all(len(value) == 64 for value in fingerprints))
        self.assertTrue(
            all(
                item["definition_fingerprint_status"]
                == "matched-approved-baseline"
                for item in inventory
                if item["disposition"] == "retain"
            )
        )

        provisioned["obsolete-1"] = {
            "uid": "obsolete-1",
            "folderUID": "integration-folder",
            "ruleGroup": "asserts-node.rules",
            "title": "asserts:resource:total",
            "labels": marker,
            "record": {"metric": "asserts:resource:total"},
            "data": [{"model": {"expr": "sum(up)"}}],
        }
        ruler["obsolete-1"] = {
            "uid": "obsolete-1",
            "type": "recording",
            "query": "sum(up)",
            "health": "ok",
        }
        legacy_errors: list[str] = []
        MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {"alert-1": {"health": "ok", "last_error": ""}},
            legacy_errors,
        )
        self.assertTrue(
            any("supported Grafana Linux integration upgrade" in error for error in legacy_errors)
        )
        del provisioned["obsolete-1"]
        del ruler["obsolete-1"]

        ruler["alert-1"]["query"] = "up > 0"
        drift_errors: list[str] = []
        MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {"alert-1": {"health": "ok", "last_error": ""}},
            drift_errors,
        )
        self.assertTrue(any("definition fingerprint drifted" in error for error in drift_errors))

        sentinel = "opaque-provider-rule-sentinel"
        provisioned["alert-1"]["labels"]["owner"] = f"label-{sentinel}"
        provisioned["alert-1"]["annotations"]["dashboard_url"] = (
            f"annotation-{sentinel}"
        )
        ruler["record-1"]["health"] = "error"
        ruler["record-1"]["lastError"] = f"recording-health-{sentinel}"
        private_errors: list[str] = []
        MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {
                "alert-1": {
                    "health": "error",
                    "last_error": f"alert-health-{sentinel}",
                }
            },
            private_errors,
        )
        serialized_errors = json.dumps(private_errors)
        self.assertNotIn(sentinel, serialized_errors)
        self.assertTrue(
            any("integration alert alert-1 is unhealthy" in error for error in private_errors)
        )
        self.assertTrue(
            any(
                "integration recording rule record-1 is unhealthy" in error
                for error in private_errors
            )
        )
        self.assertTrue(
            any("normalized label 'owner' drifted" in error for error in private_errors)
        )
        self.assertTrue(
            any(
                "normalized annotation 'dashboard_url' drifted" in error
                for error in private_errors
            )
        )


if __name__ == "__main__":
    unittest.main()
