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
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str) -> Any:
        self.requests.append((method, path))
        if self.error:
            raise self.error
        return self.response


class RaisingOpener:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self, request: urllib.request.Request, timeout: int):
        raise self.error


class ReturningOpener:
    def __init__(self, response: Any) -> None:
        self.response = response

    def open(self, request: urllib.request.Request, timeout: int) -> Any:
        return self.response


class TrackingBody(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_called = False

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        self.read_called = True
        return super().read(*args, **kwargs)


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
    def test_rabbitmq_queue_selectors_are_valid_promql_string_literals(self) -> None:
        for selector in (
            MODULE.WORKER_QUEUE_SELECTOR,
            MODULE.WORKER_MAIN_QUEUE_SELECTOR,
        ):
            self.assertNotIn(r"\.", selector)
            self.assertIn("[.]", selector)

        for name in (
            "backend_rabbitmq_queue_acked",
            "backend_rabbitmq_queue_delivered",
            "backend_rabbitmq_queue_redelivered",
        ):
            query = MODULE.PROMETHEUS_QUERIES[name][0]
            self.assertIn("or on (queue) (0 * rabbitmq_detailed_queue_messages", query)

    def test_synthetic_execution_guardrail_is_exactly_ninety_thousand(self) -> None:
        errors: list[str] = []
        MODULE.validate_synthetic_execution_guardrail(90_000, errors)
        self.assertFalse(errors)

        MODULE.validate_synthetic_execution_guardrail(95_000, errors)
        self.assertEqual(
            errors,
            [
                "Terraform synthetic execution guardrail must remain exactly 90,000 "
                "monthly API executions"
            ],
        )

    def test_grafana_origins_are_pinned_to_their_api_roles(self) -> None:
        expected = {
            "https://kindcantaloupe2036.grafana.net": "https://kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net/": "https://kindcantaloupe2036.grafana.net",
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
            "http://kindcantaloupe2036.grafana.net",
            "https://grafana.net",
            "https://kindcantaloupe2036.grafana.net.evil.invalid",
            "https://kindcantaloupe2036.grafana.net/api",
            "https://kindcantaloupe2036.grafana.net?token=secret",
            "https://user:secret@kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net:444",
            "https://kindcantaloupe2036.grafana.net:",
            "https://bad_.grafana.net",
            " https://kindcantaloupe2036.grafana.net",
            "https://other-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.GrafanaClient(value, "sensitive-token")
        for value in (
            "https://kindcantaloupe2036.grafana.net",
            "https://other-tenant.grafana.net",
            "https://synthetic-monitoring-apiattacker.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.SyntheticMonitoringClient(value, "sensitive-token")

    def test_redirect_is_rejected_before_a_second_authenticated_request(self) -> None:
        transport = RedirectingHTTPSHandler()
        client = MODULE.GrafanaClient(
            "https://kindcantaloupe2036.grafana.net", "sensitive-token"
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
            "https://kindcantaloupe2036.grafana.net/api/health",
        )

    def test_grafana_transport_errors_are_status_and_path_only(self) -> None:
        sentinel = "https://protected-synthetic-target.invalid/readyz?key=secret"
        credential = "post-apply-api-credential-sentinel"
        client = MODULE.GrafanaClient(
            "https://kindcantaloupe2036.grafana.net", credential
        )
        headers = Message()
        response_body = TrackingBody(
            f"attacker body {sentinel} {credential}".encode()
        )
        http_error = urllib.error.HTTPError(
            "https://kindcantaloupe2036.grafana.net/api/health",
            502,
            f"attacker reason {sentinel} {credential}",
            headers,
            response_body,
        )
        client.opener = RaisingOpener(http_error)
        with self.assertRaises(RuntimeError) as raised:
            client.request("GET", f"/api/health?upstream={sentinel}")
        self.assertEqual(
            str(raised.exception),
            "Grafana API GET /api/health failed with HTTP 502",
        )
        self.assertNotIn("protected-synthetic-target", str(raised.exception))
        self.assertNotIn(credential, str(raised.exception))
        self.assertFalse(response_body.read_called)
        self.assertTrue(raised.exception.__suppress_context__)

        client.opener = RaisingOpener(
            urllib.error.URLError(
                f"attacker transport reason {sentinel} {credential}"
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            client.request("GET", f"/api/health?upstream={sentinel}")
        self.assertEqual(
            str(raised.exception),
            "Grafana API GET /api/health failed before an HTTP response",
        )
        self.assertNotIn("protected-synthetic-target", str(raised.exception))
        self.assertNotIn(credential, str(raised.exception))

    def test_urlsplit_nfkc_failure_never_echoes_untrusted_netloc(self) -> None:
        sentinel = "protected-synthetic-target.invalid"
        malformed = f"https://kindcantaloupe2036.grafana.net\uff0f{sentinel}"
        with self.assertRaises(ValueError) as raised:
            MODULE.validate_grafana_cloud_url(malformed, "GRAFANA_URL")
        self.assertEqual(
            str(raised.exception),
            "GRAFANA_URL must be a query-free HTTPS kindcantaloupe2036.grafana.net Grafana UI API origin",
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
            'job="integrations/unix"',
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

    def test_quiet_log_sources_use_bounded_one_day_evidence(self) -> None:
        self.assertEqual(
            MODULE.LOKI_QUERY_HOURS_OVERRIDES,
            {
                "backend_postgresql_logs": 24,
                "vps_caddy_logs": 24,
                "vps_web_logs": 24,
            },
        )
        self.assertIn(
            'source=~"journal|postgresql"',
            MODULE.LOKI_QUERIES["backend_postgresql_logs"],
        )

    def test_whole_report_removes_target_and_credential_sentinels(self) -> None:
        sentinel = "https://protected-synthetic-target.invalid/readyz"
        untrusted_url = f"{sentinel}?key=secret"
        target_hostname = "protected-synthetic-target.invalid"
        unexpected_live_instance = "unexpected-live-instance.private.invalid"
        credential = "post-apply-service-account-token-sentinel"
        synthetic_credential = "post-apply-synthetic-token-sentinel"
        desired_checks = {
            "canonical_homepage": {
                "target": "https://canonical-target.invalid/"
            },
            "canonical_readiness": {
                "target": "https://canonical-target.invalid/readyz"
            },
            "canonical_articles_api": {
                "target": "https://canonical-target.invalid/api/articles"
            },
            "vps_readiness": {"target": sentinel},
            "vercel_secondary_readiness": {
                "target": "https://secondary-target.invalid/readyz"
            },
        }
        desired_checks_raw = json.dumps(desired_checks, sort_keys=True)
        validation_result = {
            "query": "probe_success",
            "status": "success",
            "result_count": 1,
            "series_labels": [
                {
                    "instance": sentinel,
                    "target": unexpected_live_instance,
                    "probe": "public-probe-a",
                    "attacker_label": sentinel,
                }
            ],
            "sample_values": [1.0],
        }
        raw_report = {
            "status": "fail",
            "prometheus_queries": {"backend_api_up": validation_result},
            "errors": [
                f"upstream returned {untrusted_url}",
                f"provider diagnostic mentioned {target_hostname}",
                f"provider diagnostic echoed Bearer {credential}",
                f"provider diagnostic echoed Bearer {synthetic_credential}",
                f"provider diagnostic echoed protected JSON {desired_checks_raw}",
                f"unexpected live target label {unexpected_live_instance}",
            ],
            untrusted_url: {"detail": sentinel},
        }

        sensitive_values = MODULE.protected_report_values(
            credential,
            synthetic_credential,
            desired_checks_raw,
            desired_checks,
        )
        serialized = MODULE.serialize_report_for_output(raw_report, sensitive_values)
        safe_report = json.loads(serialized)

        self.assertEqual(
            validation_result["series_labels"][0]["instance"], sentinel
        )
        self.assertFalse(
            MODULE.report_contains_sensitive_value(serialized, sensitive_values)
        )
        self.assertNotIn("protected-synthetic-target.invalid", serialized)
        self.assertNotIn("key=secret", serialized)
        self.assertNotIn(credential, serialized)
        self.assertNotIn(synthetic_credential, serialized)
        self.assertNotIn("canonical-target.invalid", serialized)
        self.assertNotIn("secondary-target.invalid", serialized)
        self.assertNotIn(desired_checks_raw, serialized)
        self.assertNotIn(unexpected_live_instance, serialized)
        query_report = safe_report["prometheus_queries"]["results"][
            "backend_api_up"
        ]
        self.assertEqual(
            query_report["label_structure"],
            {
                "series_count": 1,
                "invalid_series_count": 0,
                "allowlisted_label_keys": ["probe"],
            },
        )
        self.assertEqual(query_report["finite_sample_count"], 1)
        self.assertEqual(query_report["zero_sample_count"], 0)
        self.assertEqual(query_report["one_sample_count"], 1)
        self.assertEqual(query_report["other_finite_sample_count"], 0)
        self.assertEqual(query_report["distinct_probe_label_count"], 1)
        self.assertEqual(query_report["distinct_config_version_count"], 0)
        self.assertEqual(
            safe_report["errors"],
            {
                "error_count": 6,
                "invalid_error_count": 0,
                "category_counts": {"other": 2, "synthetic_monitoring": 4},
            },
        )
        self.assertEqual(safe_report["unexpected_top_level_field_count"], 1)

    def test_complete_artifact_never_contains_an_http_error_body(self) -> None:
        body_sentinel = "provider-http-error-body-private-sentinel"
        response_body = TrackingBody(body_sentinel.encode())
        client = MODULE.GrafanaClient(
            "https://kindcantaloupe2036.grafana.net", "protected-api-token"
        )
        client.opener = RaisingOpener(
            urllib.error.HTTPError(
                "https://kindcantaloupe2036.grafana.net/api/folders/private",
                503,
                f"provider reason {body_sentinel}",
                Message(),
                response_body,
            )
        )
        errors: list[str] = []
        MODULE.safe_check(
            "folder inventory",
            lambda: client.request("GET", "/api/folders/private"),
            errors,
            {},
        )

        artifact = MODULE.serialize_report_for_output(
            {"status": "fail", "errors": errors},
            ("protected-api-token",),
        )

        self.assertFalse(response_body.read_called)
        self.assertNotIn(body_sentinel, artifact)
        self.assertNotIn("provider reason", artifact)
        self.assertIn(
            "Grafana API GET /api/folders/private failed with HTTP 503",
            errors[0],
        )
        self.assertEqual(json.loads(artifact)["errors"]["error_count"], 1)

    def test_invalid_api_response_body_is_reduced_to_a_fixed_error(self) -> None:
        body_sentinel = "invalid-json-provider-body-private-sentinel"
        client = MODULE.GrafanaClient(
            "https://kindcantaloupe2036.grafana.net", "protected-api-token"
        )
        response = urllib.response.addinfourl(
            io.BytesIO(f"not-json {body_sentinel}".encode()),
            Message(),
            "https://kindcantaloupe2036.grafana.net/api/health",
            200,
        )
        client.opener = ReturningOpener(response)
        errors: list[str] = []
        MODULE.safe_check(
            "Grafana health",
            lambda: client.request("GET", "/api/health"),
            errors,
            {},
        )

        artifact = MODULE.serialize_report_for_output(
            {"status": "fail", "errors": errors},
            ("protected-api-token",),
        )

        self.assertNotIn(body_sentinel, artifact)
        self.assertIn("Grafana API GET /api/health returned invalid JSON", errors[0])
        self.assertEqual(json.loads(artifact)["errors"]["error_count"], 1)

    def test_datasource_uids_are_removed_from_every_proxy_error_path(self) -> None:
        datasource_uids = (
            "private-prometheus-datasource-uid",
            "private-loki-datasource-uid",
            "private-usage-datasource-uid",
        )
        errors: list[str] = []
        for index, datasource_uid in enumerate(datasource_uids):
            path = f"/api/datasources/proxy/uid/{datasource_uid}/api/v1/query?query=up"
            client = MODULE.GrafanaClient(
                "https://kindcantaloupe2036.grafana.net", "protected-api-token"
            )
            if index == 0:
                client.opener = RaisingOpener(
                    urllib.error.HTTPError(
                        f"https://kindcantaloupe2036.grafana.net{path}",
                        502,
                        "private provider reason",
                        Message(),
                        io.BytesIO(b"private provider body"),
                    )
                )
            elif index == 1:
                client.opener = RaisingOpener(
                    urllib.error.URLError("private transport detail")
                )
            else:
                client.opener = ReturningOpener(
                    urllib.response.addinfourl(
                        io.BytesIO(b"not-json"),
                        Message(),
                        f"https://kindcantaloupe2036.grafana.net{path}",
                        200,
                    )
                )
            with self.assertRaises(RuntimeError) as raised:
                client.request("GET", path)
            rendered_error = str(raised.exception)
            self.assertNotIn(datasource_uid, rendered_error)
            self.assertIn("[redacted-datasource-uid]", rendered_error)
            errors.append(rendered_error)

        protected_values = MODULE.protected_report_values(
            "protected-api-token",
            "protected-synthetic-token",
            "{}",
            {},
            *datasource_uids,
        )
        artifact = MODULE.serialize_report_for_output(
            {"status": "fail", "errors": errors}, protected_values
        )
        for datasource_uid in datasource_uids:
            self.assertNotIn(datasource_uid, artifact)
        self.assertEqual(json.loads(artifact)["errors"]["error_count"], 3)

    def test_successful_slo_json_is_projected_without_raw_provider_fields(self) -> None:
        sentinel = "private-provider-slo-value\nwith-newline"
        payload = {
            "uuid": sentinel,
            "name": sentinel,
            "description": sentinel,
            "objectives": [{"value": sentinel, "window": sentinel}],
            "readOnly": {
                "status": {"type": "updated", "message": sentinel},
                "provider_extension": sentinel,
            },
            "provider_extension": {sentinel: sentinel},
        }
        client = MODULE.GrafanaClient(
            "https://kindcantaloupe2036.grafana.net", "protected-api-token"
        )
        client.opener = ReturningOpener(
            urllib.response.addinfourl(
                io.BytesIO(json.dumps(payload).encode()),
                Message(),
                "https://kindcantaloupe2036.grafana.net/api/plugins/grafana-slo-app/resources/v1/slo",
                200,
            )
        )
        remote = client.request(
            "GET", "/api/plugins/grafana-slo-app/resources/v1/slo/private"
        )
        self.assertEqual(remote["name"], sentinel)

        artifact = MODULE.serialize_report_for_output(
            {
                "status": "fail",
                "grafana_slos": {
                    "public_availability": {
                        **remote,
                        "recording_rule_count": 10,
                        "alert_rule_count": 2,
                        "recorded_sample_state": "required-finite-samples",
                        "recorded_samples": {
                            "grafana_slo_objective": {
                                "query": sentinel,
                                "status": sentinel,
                                "result_count": 1,
                                "series_labels": [
                                    {
                                        "grafana_slo_uuid": sentinel,
                                        "service": sentinel,
                                    }
                                ],
                                "sample_values": [0.995],
                            }
                        },
                    }
                },
            }
        )
        safe_report = json.loads(artifact)

        for representation in (
            sentinel,
            repr(sentinel),
            json.dumps(sentinel),
            sentinel.encode("unicode_escape").decode("ascii"),
        ):
            self.assertNotIn(representation, artifact)
        slo = safe_report["grafana_slos"]["slos"]["public_availability"]
        self.assertEqual(slo["recording_rule_count"], 10)
        self.assertEqual(slo["alert_rule_count"], 2)
        for raw_field in ("uuid", "name", "description", "objectives", "readOnly"):
            self.assertNotIn(raw_field, slo)

    def test_label_values_worker_inventory_and_error_reprs_never_reach_artifact(self) -> None:
        label_keys = (
            "job",
            "queue",
            "version",
            "revision",
            "service",
            "environment",
            "deployment_environment",
            "adapter",
        )
        sentinels = {
            key: f"private-{key}-value\nsecond-line" for key in label_keys
        }
        errors = []
        for value in sentinels.values():
            errors.extend((value, repr(value), json.dumps(value)))
        report = {
            "status": "fail",
            "prometheus_queries": {
                "backend_api_up": {
                    "status": sentinels["job"],
                    "result_count": 1,
                    "series_labels": [sentinels],
                    "sample_values": [1.0],
                }
            },
            "worker_rollout": {
                "phase": sentinels["environment"],
                "host_expected_active": float("nan"),
                "host_deployment_mode": sentinels["deployment_environment"],
                "delivery_service_count": 7,
                "readiness_ok_services": [sentinels["service"]],
                "deployment_identity_services": [sentinels["adapter"]],
                "build_identity_services": [sentinels["version"]],
                "host_verified_deployed_identity_services": [sentinels["revision"]],
            },
            "synthetic_monitoring_inventory": {
                "enabled_api_check_count": 1,
                "enabled_browser_check_count": 0,
                "monthly_api_execution_estimate": 100,
                "monthly_api_execution_ceiling": 90_000,
                "execution_estimate_complete": True,
                "checks": [
                    {
                        "job": sentinels["job"],
                        "check_id": sentinels["queue"],
                        "enabled": True,
                        "terraform_managed": False,
                    }
                ],
            },
            "errors": errors,
        }
        artifact = MODULE.serialize_report_for_output(report)
        safe_report = json.loads(artifact)

        for value in sentinels.values():
            for representation in (
                value,
                repr(value),
                json.dumps(value),
                value.encode("unicode_escape").decode("ascii"),
            ):
                self.assertNotIn(representation, artifact)
        self.assertEqual(safe_report["errors"]["error_count"], len(errors))
        self.assertEqual(
            safe_report["worker_rollout"]["ownership_state"], "invalid"
        )
        self.assertEqual(
            safe_report["synthetic_monitoring_inventory"]["inventory_check_count"],
            1,
        )

    def test_serialized_report_is_strict_json_with_nonfinite_inputs(self) -> None:
        artifact = MODULE.serialize_report_for_output(
            {
                "status": "fail",
                "terraform_state": {
                    "synthetic_execution_estimate": float("nan"),
                    "synthetic_execution_guardrail": float("inf"),
                    "synthetic_execution_major_threshold": float("-inf"),
                },
                "worker_rollout": {
                    "phase": "production-runtime-v1-required",
                    "host_expected_active": float("nan"),
                },
                "prometheus_queries": {
                    "backend_api_up": {
                        "status": "success",
                        "result_count": 3,
                        "sample_values": [float("nan"), float("inf"), float("-inf")],
                        "series_labels": [],
                    }
                },
            }
        )

        self.assertNotIn("NaN", artifact)
        self.assertNotIn("Infinity", artifact)
        parsed = json.loads(
            artifact,
            parse_constant=lambda value: self.fail(
                f"non-standard JSON constant survived: {value}"
            ),
        )
        self.assertIsNone(
            parsed["terraform_state"]["synthetic_execution_estimate"]
        )
        self.assertEqual(
            parsed["prometheus_queries"]["results"]["backend_api_up"][
                "finite_sample_count"
            ],
            0,
        )
        self.assertEqual(
            parsed["prometheus_queries"]["results"]["backend_api_up"][
                "zero_sample_count"
            ],
            0,
        )

    def test_every_provider_bearing_top_level_section_uses_bounded_projection(self) -> None:
        sentinel = "private-provider-top-level-sentinel"
        source_owned_uid = sorted(MODULE._source_owned_external_rule_uids())[0]
        safe_fingerprint = "a" * 64
        artifact = MODULE.serialize_report_for_output(
            {
                "status": sentinel,
                "folders": {sentinel: sentinel},
                "contact_points": [
                    {
                        "name": sentinel,
                        "email_integration_count": 1,
                        "recipient_configuration_present": True,
                        "resolved_notifications_enabled": True,
                        "provider_extension": sentinel,
                    }
                ],
                "notification_policy": {
                    "receiver": sentinel,
                    "group_by": [sentinel],
                    "timings": [sentinel],
                    "routes": [{"receiver": sentinel, "severity": sentinel}],
                },
                "external_rule_inventory": {
                    "definition_fingerprint_baseline_status": sentinel,
                    "rules": [
                        {
                            "uid": sentinel,
                            "title": sentinel,
                            "group": sentinel,
                            "health": sentinel,
                            "definition_fingerprint_sha256": sentinel,
                        },
                        {
                            "uid": source_owned_uid,
                            "title": sentinel,
                            "health": "ok",
                            "definition_fingerprint_sha256": safe_fingerprint,
                        },
                    ],
                },
                "terraform_state": {
                    "synthetic_execution_estimate": sentinel,
                    "provider_extension": sentinel,
                },
                "loki_queries": {
                    "backend_host_logs": {
                        "query": sentinel,
                        "status": sentinel,
                        "result_count": 1,
                        "line_count": 1,
                        "stream_labels": [{"service": sentinel}],
                        "provider_extension": sentinel,
                    }
                },
                sentinel: {sentinel: sentinel},
            }
        )
        safe_report = json.loads(artifact)

        self.assertNotIn(sentinel, artifact)
        self.assertEqual(safe_report["status"], "fail")
        self.assertEqual(safe_report["unexpected_top_level_field_count"], 1)
        self.assertEqual(safe_report["folders"]["observed_count"], 1)
        self.assertFalse(
            safe_report["notification_policy"]["contract_matches"]
        )
        self.assertEqual(
            safe_report["external_rule_inventory"]["health_status_counts"][
                "unknown"
            ],
            1,
        )
        self.assertEqual(
            safe_report["external_rule_inventory"][
                "observed_definition_fingerprints_sha256"
            ],
            {source_owned_uid: safe_fingerprint},
        )
        self.assertIsNone(
            safe_report["terraform_state"]["synthetic_execution_estimate"]
        )

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
            "provider_extension": {sentinel: sentinel},
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
        self.assertNotIn("provider_extension", safe_report)
        self.assertEqual(safe_report["unexpected_top_level_field_count"], 1)
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

    def test_loki_series_returns_authoritative_indexed_labels(self) -> None:
        labels = {
            label: f"value-{label}" for label in MODULE.LOKI_INDEXED_LABELS
        }
        labels["service_name"] = labels["service"]
        client = FakeClient({"status": "success", "data": [labels]})
        result = MODULE.loki_series(client, "loki", '{service="web"}', 1)
        self.assertEqual(result["indexed_series_labels"], [labels])
        self.assertIn("match%5B%5D", client.requests[0][1])

    def test_loki_label_contract_rejects_extra_and_missing_indexed_labels(self) -> None:
        exact = {label: f"value-{label}" for label in MODULE.LOKI_INDEXED_LABELS}
        exact["service_name"] = exact["service"]
        errors: list[str] = []
        MODULE.validate_loki_indexed_labels("exact", [exact], errors)
        self.assertFalse(errors)

        invalid = dict(exact)
        invalid.pop("severity")
        invalid["service_name"] = "wrong-service"
        invalid["correlation_id"] = "must-be-structured-metadata"
        MODULE.validate_loki_indexed_labels("invalid", [invalid], errors)
        self.assertTrue(any("unapproved indexed labels" in error for error in errors))
        self.assertTrue(any("missing normalized indexed labels" in error for error in errors))
        self.assertTrue(any("service_name alias" in error for error in errors))

        summary = MODULE._query_result_summary(
            {
                "status": "success",
                "indexed_series_status": "success",
                "indexed_series_labels": [invalid],
            }
        )
        self.assertEqual(summary["indexed_series_status"], "success")
        self.assertEqual(
            summary["indexed_series_missing_normalized_label_count"], 1
        )
        self.assertEqual(summary["indexed_series_unexpected_label_count"], 1)
        self.assertEqual(
            summary["indexed_series_service_alias_mismatch_count"], 1
        )

    def test_generated_slo_burn_windows_match_canonical_families(self) -> None:
        critical = {
            "query": " + ".join(
                f"grafana_slo_sli_{window}" for window in ("5m", "30m", "1h", "6h")
            )
        }
        warning = {
            "query": " + ".join(
                f"grafana_slo_sli_{window}" for window in ("2h", "6h", "1d", "3d")
            )
        }
        self.assertEqual(
            MODULE.generated_slo_burn_windows(critical), {"5m", "30m", "1h", "6h"}
        )
        self.assertEqual(
            MODULE.generated_slo_burn_windows(warning), {"2h", "6h", "1d", "3d"}
        )

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
        lifecycle_message = "private-provider-lifecycle-detail"
        with self.assertRaisesRegex(RuntimeError, "entered error lifecycle state") as raised:
            MODULE.wait_for_remote_slo(
                LifecycleClient([("error", lifecycle_message)]),
                "slo-public-1",
                timeout_seconds=0,
                sleep=lambda _: None,
                monotonic=lambda: 0,
            )
        self.assertNotIn(lifecycle_message, str(raised.exception))

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
        summary = MODULE.synthetic_contract_error_summary(errors)
        self.assertFalse(summary["valid"])
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["category_counts"], {"assertion_shape": 1})

    def test_synthetic_inventory_artifact_retains_only_bounded_contract_failures(self) -> None:
        sentinel = "private-assertion-or-target-value"
        report = {
            "synthetic_monitoring_inventory": {
                "enabled_api_check_count": 1,
                "enabled_browser_check_count": 0,
                "monthly_api_execution_estimate": 17_280,
                "monthly_api_execution_ceiling": 90_000,
                "execution_estimate_complete": True,
                "checks": [
                    {
                        "job": "canonical_readiness",
                        "check_id": sentinel,
                        "enabled": True,
                        "terraform_managed": True,
                        "target": sentinel,
                        "contract_validation": {
                            "valid": False,
                            "error_count": 2,
                            "category_counts": {
                                "assertion_shape": 1,
                                "redirects": 1,
                                sentinel: 99,
                            },
                        },
                    }
                ],
            }
        }
        serialized = MODULE.serialize_report_for_output(report)
        summary = json.loads(serialized)["synthetic_monitoring_inventory"]
        self.assertNotIn(sentinel, serialized)
        self.assertEqual(
            summary["managed_contracts"]["canonical_readiness"],
            {
                "valid": False,
                "error_count": 2,
                "category_counts": {"assertion_shape": 1, "redirects": 1},
                "unexpected_category_count": 1,
            },
        )

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
            "integrationUpgradeStatus": "not_available_from_live_api",
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
        catalog["integrationUpgradeStatus"] = "completed_supported_integration_upgrade"
        legacy_errors: list[str] = []
        MODULE.validate_external_rule_inventory(
            catalog,
            provisioned,
            ruler,
            {"alert-1": {"health": "ok", "last_error": ""}},
            legacy_errors,
        )
        self.assertTrue(
            any("vendor alert bundle" in error for error in legacy_errors)
        )
        catalog["integrationUpgradeStatus"] = "not_available_from_live_api"
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
