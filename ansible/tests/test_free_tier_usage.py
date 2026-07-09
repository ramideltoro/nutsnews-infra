#!/usr/bin/env python3
"""Unit coverage for read-only free-tier usage collection."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ansible/roles/vps_service_foundation/files"))

from ops_free_tier_usage import ApiRequestError, FreeTierCollector  # noqa: E402


NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


class FakeHttpClient:
    def __init__(self, payload: object | Exception) -> None:
        self.payload = payload
        self.headers: list[dict[str, str]] = []
        self.params: list[dict[str, str]] = []
        self.urls: list[str] = []

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        self.params.append(params or {})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def post_json(self, url, body, headers=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class TextHttpClient:
    def __init__(self, payload: str | Exception) -> None:
        self.payload = payload
        self.headers: list[dict[str, str]] = []
        self.params: list[dict[str, str]] = []
        self.urls: list[str] = []

    def get_text(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        self.params.append(params or {})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        raise AssertionError("Vercel billing charges collector must read text JSONL.")

    def post_json(self, url, body, headers=None, timeout=8):  # noqa: ANN001, ANN201
        raise AssertionError("Vercel billing charges collector must use GET.")


class GitHubHttpClient:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        if url.endswith("/actions/cache/usage"):
            return {"active_caches_size_in_bytes": 1073741824}
        if url.endswith("/actions/artifacts"):
            return {"artifacts": [{"size_in_bytes": 1048576}, {"size_in_bytes": 2097152}]}
        if url == "https://api.github.com/rate_limit":
            return {"resources": {"core": {"used": 42}}}
        raise ApiRequestError(status_code=404, body=b'{"message":"not found"}', content_type="application/json")


class CloudflareHttpClient:
    def __init__(self, payload: object | Exception | None = None) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.bodies: list[dict] = []

    def post_json(self, url, body, headers=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        self.bodies.append(body)
        if isinstance(self.payload, Exception):
            raise self.payload
        if self.payload is not None:
            return self.payload
        return {
            "data": {
                "viewer": {
                    "accounts": [
                        {
                            "workersInvocationsAdaptive": [
                                {"sum": {"requests": 7}},
                                {"sum": {"requests": 5}},
                            ]
                        }
                    ]
                }
            },
            "errors": None,
        }

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        raise AssertionError("Cloudflare GraphQL collector must use POST.")


class GrafanaUsageHttpClient:
    def __init__(self, payloads: dict[str, object | Exception]) -> None:
        self.payloads = payloads
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.params: list[dict[str, str]] = []

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
        self.headers.append(headers or {})
        self.params.append(params or {})
        query = (params or {}).get("query", "")
        payload = self.payloads.get(query)
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        return payload

    def post_json(self, url, body, headers=None, timeout=8):  # noqa: ANN001, ANN201
        raise AssertionError("Grafana Cloud usage collector must use GET.")


def quota_config(live: dict | None = None) -> list[dict]:
    provider = {
        "key": "demo",
        "platform": "Demo",
        "quota_source": "https://example.invalid/docs",
        "quota_last_verified": "2026-07-08",
        "metrics": [
            {
                "key": "requests",
                "label": "Requests",
                "unit": "requests/month",
                "period": "monthly",
                "limit": 100,
            }
        ],
    }
    if live:
        provider["live"] = live
    return [provider]


def github_quota_config(live: dict) -> list[dict]:
    return [
        {
            "key": "github_actions",
            "platform": "GitHub Actions",
            "metrics": [
                {"key": "hosted_runner_minutes", "label": "Hosted Runner Minutes", "unit": "minutes/month", "limit": 2000},
                {"key": "artifact_storage_mb", "label": "Artifacts", "unit": "MB/month", "limit": 500},
                {"key": "cache_storage_gb", "label": "Actions Cache", "unit": "GB/repository", "limit": 10},
                {"key": "rest_api_requests", "label": "REST API Requests", "unit": "requests/hour", "limit": 5000},
            ],
            "live": live,
        }
    ]


def cloudflare_quota_config(live: dict) -> list[dict]:
    return [
        {
            "key": "cloudflare",
            "platform": "Cloudflare",
            "metrics": [
                {"key": "workers_requests", "label": "Workers Requests", "unit": "requests/day", "limit": 100000},
                {"key": "pages_builds", "label": "Pages Builds", "unit": "builds/month", "limit": 500},
            ],
            "live": live,
        }
    ]


def grafana_quota_config(live: dict) -> list[dict]:
    return [
        {
            "key": "grafana_cloud",
            "platform": "Grafana Cloud",
            "metrics": [
                {"key": "metrics_active_series", "label": "Metrics Active Series", "unit": "active series/month", "limit": 10000},
                {"key": "logs_ingested_gb", "label": "Logs Ingested", "unit": "GB/month", "limit": 50},
            ],
            "live": live,
        }
    ]


def vercel_quota_config(live: dict) -> list[dict]:
    return [
        {
            "key": "vercel",
            "platform": "Vercel",
            "display_unmeasured_status": True,
            "metrics": [
                {"key": "fast_data_transfer_gb", "label": "Fast Data Transfer", "unit": "GB/month", "limit": 100},
                {"key": "function_invocations", "label": "Function Invocations", "unit": "invocations/month", "limit": 1000000},
                {"key": "active_cpu_hours", "label": "Active CPU", "unit": "CPU-hours/month", "limit": 4},
                {"key": "provisioned_memory_gb_hours", "label": "Provisioned Memory", "unit": "GB-hours/month", "limit": 360},
            ],
            "live": live,
        }
    ]


def collect(env: dict[str, str], http_client: FakeHttpClient | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        merged = {
            "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
            "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config()),
            **env,
        }
        return FreeTierCollector(env=merged, http_client=http_client, now=NOW).collect()


class FreeTierUsageTests(unittest.TestCase):
    def test_runtime_loads_free_tier_env_file_when_process_env_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "free-tier-usage.env"
            cache_file = Path(tmpdir) / "cache.json"
            env_file.write_text(
                "\n".join(
                    [
                        f"NUTSNEWS_FREE_TIER_QUOTAS_JSON={shlex.quote(json.dumps(quota_config()))}",
                        f"NUTSNEWS_FREE_TIER_USAGE_JSON={shlex.quote(json.dumps({'demo': {'requests': 40}}))}",
                        f"NUTSNEWS_FREE_TIER_CACHE_FILE={shlex.quote(str(cache_file))}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            saved = {
                key: os.environ.get(key)
                for key in (
                    "NUTSNEWS_FREE_TIER_ENV_FILE",
                    "NUTSNEWS_FREE_TIER_QUOTAS_JSON",
                    "NUTSNEWS_FREE_TIER_USAGE_JSON",
                    "NUTSNEWS_FREE_TIER_CACHE_FILE",
                )
            }
            try:
                for key in saved:
                    os.environ.pop(key, None)
                os.environ["NUTSNEWS_FREE_TIER_ENV_FILE"] = str(env_file)
                data = FreeTierCollector(now=NOW).collect()
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        provider = data["providers"][0]
        self.assertEqual(provider["key"], "demo")
        self.assertEqual(provider["status"], "cached")
        self.assertEqual(provider["metrics"][0]["usage"], 40)

    def test_normal_usage_below_quota(self) -> None:
        data = collect({"NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps({"demo": {"requests": 25}})})
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "cached")
        self.assertEqual(provider["health"], "healthy")
        self.assertEqual(provider["risk_status"], "safe")
        self.assertEqual(provider["percent_used"], 25.0)
        self.assertEqual(provider["remaining"], "75 requests/month")

    def test_warning_threshold(self) -> None:
        data = collect({"NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps({"demo": {"requests": 75}})})
        self.assertEqual(data["providers"][0]["health"], "warning")
        self.assertEqual(data["providers"][0]["risk_status"], "warning")

    def test_critical_threshold(self) -> None:
        data = collect({"NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps({"demo": {"requests": 90}})})
        provider = data["providers"][0]
        self.assertEqual(provider["health"], "critical")
        self.assertEqual(provider["risk_status"], "critical")
        self.assertEqual(provider["remaining"], "10 requests/month")

    def test_over_limit_exceeded_quota(self) -> None:
        data = collect({"NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps({"demo": {"requests": 120}})})
        provider = data["providers"][0]
        self.assertEqual(provider["health"], "over_limit")
        self.assertEqual(provider["risk_status"], "over_limit")
        self.assertEqual(provider["remaining"], "-20 requests/month")
        self.assertEqual(provider["percent_remaining"], 0.0)

    def test_missing_provider_token(self) -> None:
        live = {
            "type": "json_api",
            "url_env": "DEMO_USAGE_URL",
            "token_env": "DEMO_TOKEN",
            "metric_paths": {"requests": "usage.requests"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config(live=live)),
                "DEMO_USAGE_URL": "https://example.invalid/usage",
            }
            data = FreeTierCollector(env=env, now=NOW).collect()
        self.assertEqual(data["providers"][0]["status"], "not configured")
        self.assertEqual(data["providers"][0]["risk_status"], "not_configured")
        self.assertEqual(data["providers"][0]["current_usage"], "unknown")
        self.assertIn("DEMO_TOKEN", data["providers"][0]["source_detail"])
        self.assertEqual(data["providers"][0]["metrics"][0]["measurement_status"], "missing credential")

    def test_malformed_provider_response(self) -> None:
        live = {
            "type": "json_api",
            "url_env": "DEMO_USAGE_URL",
            "token_env": "DEMO_TOKEN",
            "metric_paths": {"requests": "usage.requests"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config(live=live)),
                "DEMO_USAGE_URL": "https://example.invalid/usage",
                "DEMO_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=FakeHttpClient({"unexpected": {}}), now=NOW).collect()
        self.assertEqual(data["providers"][0]["status"], "unavailable")
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_http_error_detail_is_sanitized(self) -> None:
        live = {
            "type": "json_api",
            "url_env": "DEMO_USAGE_URL",
            "token_env": "DEMO_TOKEN",
            "metric_paths": {"requests": "usage.requests"},
        }
        error = ApiRequestError(
            status_code=400,
            content_type="application/json",
            body=b'{"error":{"code":"bad_request","message":"missing date window"}}',
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config(live=live)),
                "DEMO_USAGE_URL": "https://example.invalid/usage",
                "DEMO_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=FakeHttpClient(error), now=NOW).collect()
        detail = data["providers"][0]["source_detail"]
        self.assertIn("HTTP 400", detail)
        self.assertIn("missing date window", detail)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_len_metric_path_counts_provider_list(self) -> None:
        live = {
            "type": "json_api",
            "url_env": "DEMO_USAGE_URL",
            "token_env": "DEMO_TOKEN",
            "metric_paths": {"requests": "data.__len__"},
        }
        payload = {"data": [{}, {}, {}]}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config(live=live)),
                "DEMO_USAGE_URL": "https://example.invalid/usage",
                "DEMO_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=FakeHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "live")
        self.assertEqual(provider["metrics"][0]["usage"], 3)
        self.assertEqual(provider["metrics"][0]["measurement_status"], "measured")

    def test_metric_metadata_and_unavailable_states_are_sanitized(self) -> None:
        provider = quota_config(
            live={
                "type": "json_api",
                "url_env": "DEMO_USAGE_URL",
                "token_env": "DEMO_TOKEN",
                "metric_paths": {"requests": "usage.requests", "bytes": "usage.bytes"},
            }
        )[0]
        provider["metrics"].append(
            {
                "key": "bytes",
                "label": "Bytes",
                "unit": "bytes/month",
                "period": "monthly",
                "limit": 1000,
                "quota_source": "https://example.invalid/bytes",
            }
        )
        provider["metrics"].append(
            {
                "key": "deployments",
                "label": "Deployments",
                "unit": "deployments/day",
                "period": "daily",
                "limit": 100,
                "usage_source": "unsupported",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps([provider]),
                "DEMO_USAGE_URL": "https://example.invalid/usage",
                "DEMO_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(
                env=env,
                http_client=FakeHttpClient({"usage": {"requests": 25}}),
                now=NOW,
            ).collect()
        provider_result = data["providers"][0]
        metrics = {metric["key"]: metric for metric in provider_result["metrics"]}
        self.assertEqual(metrics["requests"]["measurement_status"], "measured")
        self.assertEqual(metrics["bytes"]["measurement_status"], "unavailable")
        self.assertEqual(metrics["deployments"]["measurement_status"], "unsupported")
        self.assertEqual(metrics["bytes"]["quota_source"], "https://example.invalid/bytes")
        self.assertEqual(metrics["requests"]["quota_last_verified"], "2026-07-08")
        self.assertEqual(provider_result["metric_status_counts"]["measured"], 1)
        self.assertEqual(provider_result["metric_status_counts"]["unavailable"], 1)
        self.assertEqual(provider_result["metric_status_counts"]["unsupported"], 1)
        self.assertEqual(data["summary"]["total_metrics"], 3)
        self.assertEqual(data["summary"]["measured_metrics"], 1)
        self.assertEqual(data["summary"]["unavailable_metrics"], 1)
        self.assertEqual(data["summary"]["unsupported_metrics"], 1)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_grafana_cloud_usage_datasource_queries_prometheus(self) -> None:
        live = {
            "type": "grafana_cloud_usage",
            "url_env": "GRAFANA_URL",
            "token_env": "GRAFANA_TOKEN",
            "usage_datasource_uid_env": "GRAFANA_USAGE_UID",
            "queries": {
                "metrics_active_series": "max(grafanacloud_instance_metrics_usage)",
                "logs_ingested_gb": "max(grafanacloud_logs_instance_usage)",
            },
        }
        http_client = GrafanaUsageHttpClient(
            {
                "max(grafanacloud_instance_metrics_usage)": {
                    "status": "success",
                    "data": {"resultType": "vector", "result": [{"value": [1783454400, "321"]}]},
                },
                "max(grafanacloud_logs_instance_usage)": {
                    "status": "success",
                    "data": {"resultType": "vector", "result": [{"value": [1783454400, "1.5"]}]},
                },
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(grafana_quota_config(live)),
                "GRAFANA_URL": "https://example.grafana.net",
                "GRAFANA_TOKEN": "sentinel-redaction-value",
                "GRAFANA_USAGE_UID": "grafanacloud-usage",
            }
            data = FreeTierCollector(env=env, http_client=http_client, now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "live")
        self.assertEqual(provider["metrics"][0]["usage"], 321)
        self.assertEqual(provider["metrics"][1]["usage"], 1.5)
        self.assertEqual(http_client.urls[0], "https://example.grafana.net/api/datasources/proxy/uid/grafanacloud-usage/api/v1/query")
        self.assertEqual(http_client.params[0]["query"], "max(grafanacloud_instance_metrics_usage)")
        self.assertTrue(http_client.headers[0]["Authorization"].startswith("Bearer "))
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_grafana_cloud_usage_reports_missing_env(self) -> None:
        live = {
            "type": "grafana_cloud_usage",
            "url_env": "GRAFANA_URL",
            "token_env": "GRAFANA_TOKEN",
            "usage_datasource_uid_env": "GRAFANA_USAGE_UID",
            "queries": {"metrics_active_series": "max(grafanacloud_instance_metrics_usage)"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(grafana_quota_config(live)),
            }
            data = FreeTierCollector(env=env, http_client=GrafanaUsageHttpClient({}), now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "not configured")
        self.assertIn("GRAFANA_URL", provider["source_detail"])

    def test_vercel_billing_charges_jsonl_usage(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "query_params": {
                "from": "__current_month_start_iso__",
                "to": "__now_iso__",
            },
            "focus_mappings": {
                "fast_data_transfer_gb": {
                    "contains_all": ["fast", "data", "transfer"],
                    "unit_contains_any": ["gb"],
                },
                "function_invocations": {
                    "contains_all": ["function", "invocation"],
                    "unit_contains_any": ["invocation"],
                },
                "active_cpu_hours": {
                    "contains_all": ["active", "cpu"],
                    "unit_contains_any": ["hour"],
                },
                "provisioned_memory_gb_hours": {
                    "contains_all": ["provisioned", "memory"],
                    "unit_contains_any": ["gb"],
                },
            },
        }
        payload = "\n".join(
            [
                json.dumps({"ServiceName": "Fast Data Transfer", "ConsumedQuantity": "12.5", "ConsumedUnit": "GB"}),
                json.dumps({"ServiceName": "Function Invocations", "ConsumedQuantity": "2000", "ConsumedUnit": "invocations"}),
                json.dumps({"ServiceName": "Active CPU", "ConsumedQuantity": "1.25", "ConsumedUnit": "hours"}),
                json.dumps({"ServiceName": "Provisioned Memory", "ConsumedQuantity": "18", "ConsumedUnit": "GB-Hours"}),
                json.dumps({"ServiceName": "Fast Data Transfer", "ConsumedQuantity": "2.5", "ConsumedUnit": "GB"}),
            ]
        )
        client = TextHttpClient(payload)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=client, now=NOW).collect()
        provider = data["providers"][0]
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "live")
        self.assertEqual(usage_by_key["fast_data_transfer_gb"], 15)
        self.assertEqual(usage_by_key["function_invocations"], 2000)
        self.assertEqual(usage_by_key["active_cpu_hours"], 1.25)
        self.assertEqual(usage_by_key["provisioned_memory_gb_hours"], 18)
        self.assertEqual(client.urls, ["https://api.vercel.com/v1/billing/charges?teamId=team_123"])
        self.assertEqual(client.params[0]["from"], "2026-07-01T00:00:00Z")
        self.assertEqual(client.params[0]["to"], "2026-07-07T12:00:00Z")
        self.assertIn("Authorization", client.headers[0])
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_vercel_billing_charges_real_zero_is_measured(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {
                "function_invocations": {
                    "contains_all": ["function", "invocation"],
                    "unit_contains_any": ["invocation"],
                },
            },
        }
        payload = json.dumps(
            {
                "ServiceName": "Function Invocations",
                "ConsumedQuantity": "0",
                "ConsumedUnit": "invocations",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        metrics = {metric["key"]: metric for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "live")
        self.assertEqual(metrics["function_invocations"]["usage"], 0)
        self.assertEqual(metrics["function_invocations"]["measurement_status"], "measured")
        self.assertEqual(metrics["function_invocations"]["usage_display"], "0 invocations/month")
        self.assertEqual(metrics["fast_data_transfer_gb"]["usage_display"], "unavailable")

    def test_vercel_unsupported_metrics_render_explicit_status(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {
                "function_invocations": {
                    "contains_all": ["function", "invocation"],
                    "unit_contains_any": ["invocation"],
                },
            },
        }
        config = vercel_quota_config(live)
        config[0]["metrics"].append(
            {
                "key": "concurrent_deployments",
                "label": "Concurrent Deployments",
                "unit": "deployments",
                "period": "current",
                "limit": 1,
                "usage_source": "unsupported",
            }
        )
        payload = json.dumps(
            {
                "ServiceName": "Function Invocations",
                "ConsumedQuantity": "10",
                "ConsumedUnit": "invocations",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(config),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        metrics = {metric["key"]: metric for metric in data["providers"][0]["metrics"]}
        self.assertEqual(metrics["concurrent_deployments"]["measurement_status"], "unsupported")
        self.assertEqual(metrics["concurrent_deployments"]["usage_display"], "unsupported")
        self.assertEqual(metrics["concurrent_deployments"]["remaining_display"], "unsupported")
        self.assertEqual(metrics["concurrent_deployments"]["percent_used_display"], "unsupported")
        self.assertEqual(metrics["concurrent_deployments"]["reset_at"], "unsupported")

    def test_vercel_billing_charges_null_quantity_is_unavailable_not_zero(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {
                "function_invocations": {
                    "contains_all": ["function", "invocation"],
                    "unit_contains_any": ["invocation"],
                },
            },
        }
        payload = json.dumps(
            {
                "ServiceName": "Function Invocations",
                "ConsumedQuantity": None,
                "ConsumedUnit": "invocations",
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        metrics = {metric["key"]: metric for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "unavailable")
        self.assertIsNone(metrics["function_invocations"]["usage"])
        self.assertEqual(metrics["function_invocations"]["usage_display"], "unavailable")
        self.assertEqual(metrics["function_invocations"]["remaining_display"], "unavailable")
        self.assertEqual(metrics["function_invocations"]["percent_used_display"], "unavailable")
        self.assertEqual(metrics["function_invocations"]["measurement_status"], "unavailable")
        self.assertIn("did not include configured quota metric records", metrics["function_invocations"]["measurement_detail"])

    def test_vercel_billing_charges_matches_focus_alias_fields(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {
                "function_invocations": {
                    "contains_all": ["function", "invocation"],
                    "unit_contains_any": ["invocation"],
                },
                "active_cpu_hours": {
                    "contains_all": ["active", "cpu"],
                    "unit_contains_any": ["hour"],
                },
            },
        }
        payload = "\n".join(
            [
                json.dumps(
                    {
                        "ServiceName": "Managed Infrastructure",
                        "ChargeDescription": "Function Invocations",
                        "UsageQuantity": "1200",
                        "PricingUnit": "invocations",
                    }
                ),
                json.dumps(
                    {
                        "ServiceName": "Managed Infrastructure",
                        "Tags": {"VercelProduct": "Active CPU"},
                        "BilledQuantity": "0.5",
                        "PricingUnit": "CPU-hours",
                    }
                ),
            ]
        )
        client = TextHttpClient(payload)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=client, now=NOW).collect()
        provider = data["providers"][0]
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "live")
        self.assertEqual(usage_by_key["function_invocations"], 1200)
        self.assertEqual(usage_by_key["active_cpu_hours"], 0.5)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_vercel_snapshot_usage_wrapper_populates_after_billing_api_error(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {"fast_data_transfer_gb": {"contains_all": ["fast", "data", "transfer"]}},
        }
        payload = json.dumps({"error": {"code": "costs_not_found", "message": "costs_not_found"}})
        snapshot = {
            "providers": {
                "vercel": {
                    "last_checked_at": "2026-07-07T11:30:00+00:00",
                    "usage": {
                        "fast_data_transfer_gb": {"current": 3.75},
                        "function_invocations": {"used": 1200},
                        "active_cpu_hours": {"value": 0.25},
                        "provisioned_memory_gb_hours": 12,
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps(snapshot),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "cached")
        self.assertEqual(provider["source_detail"], "Usage loaded from configured snapshot.")
        self.assertEqual(provider["last_checked_at"], "2026-07-07T11:30:00+00:00")
        self.assertEqual(usage_by_key["fast_data_transfer_gb"], 3.75)
        self.assertEqual(usage_by_key["function_invocations"], 1200)
        self.assertEqual(usage_by_key["active_cpu_hours"], 0.25)
        self.assertEqual(usage_by_key["provisioned_memory_gb_hours"], 12)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_vercel_live_only_ignores_placeholder_snapshot_zeroes_after_api_error(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "allow_usage_fallback": False,
            "focus_mappings": {"fast_data_transfer_gb": {"contains_all": ["fast", "data", "transfer"]}},
        }
        payload = json.dumps({"error": {"code": "costs_not_found", "message": "costs_not_found"}})
        snapshot = {
            "providers": {
                "vercel": {
                    "last_checked_at": "2026-07-07T11:30:00+00:00",
                    "usage": {
                        "fast_data_transfer_gb": 0,
                        "function_invocations": 0,
                        "active_cpu_hours": 0,
                        "provisioned_memory_gb_hours": 0,
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps(snapshot),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        metrics = {metric["key"]: metric for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "unavailable")
        self.assertIn("costs_not_found", provider["source_detail"])
        self.assertIn("fallback is disabled", provider["source_detail"])
        self.assertIsNone(metrics["fast_data_transfer_gb"]["usage"])
        self.assertEqual(metrics["fast_data_transfer_gb"]["usage_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["remaining_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["percent_used_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["measurement_status"], "unavailable")
        self.assertIn("costs_not_found", metrics["fast_data_transfer_gb"]["measurement_detail"])
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_vercel_http_error_keeps_provider_unavailable_not_zero(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "allow_usage_fallback": False,
            "focus_mappings": {
                "fast_data_transfer_gb": {
                    "contains_all": ["fast", "data", "transfer"],
                    "unit_contains_any": ["gb"],
                },
            },
        }
        snapshot = {
            "providers": {
                "vercel": {
                    "usage": {
                        "fast_data_transfer_gb": 0,
                        "function_invocations": 0,
                        "active_cpu_hours": 0,
                        "provisioned_memory_gb_hours": 0,
                    },
                }
            }
        }
        error = ApiRequestError(
            status_code=404,
            content_type="application/json; charset=utf-8",
            body=b'{"error":{"code":"costs_not_found","message":"Costs not found"}}',
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps(snapshot),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(error), now=NOW).collect()
        provider = data["providers"][0]
        metrics = {metric["key"]: metric for metric in provider["metrics"]}
        self.assertEqual(provider["key"], "vercel")
        self.assertEqual(provider["status"], "unavailable")
        self.assertIn("HTTP 404", provider["source_detail"])
        self.assertIn("Costs not found", provider["source_detail"])
        self.assertIn("verify the protected teamId or slug", provider["source_detail"])
        self.assertIsNone(metrics["fast_data_transfer_gb"]["usage"])
        self.assertEqual(metrics["fast_data_transfer_gb"]["usage_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["remaining_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["percent_used_display"], "unavailable")
        self.assertEqual(metrics["fast_data_transfer_gb"]["measurement_status"], "unavailable")
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_metric_reset_placeholders_are_rendered(self) -> None:
        provider = quota_config()[0]
        provider["metrics"][0]["reset_at"] = "__next_month_start_iso__"
        daily_metric = dict(provider["metrics"][0])
        daily_metric["key"] = "daily_requests"
        daily_metric["label"] = "Daily Requests"
        daily_metric["reset_at"] = "__next_day_start_iso__"
        provider["metrics"].append(daily_metric)
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps([provider]),
                "NUTSNEWS_FREE_TIER_USAGE_JSON": json.dumps({"demo": {"requests": 25, "daily_requests": 1}}),
            }
            data = FreeTierCollector(env=env, now=NOW).collect()
        resets = {metric["key"]: metric["reset_at"] for metric in data["providers"][0]["metrics"]}
        self.assertEqual(resets["requests"], "2026-08-01T00:00:00Z")
        self.assertEqual(resets["daily_requests"], "2026-07-08T00:00:00Z")

    def test_vercel_billing_charges_missing_env_is_actionable(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(""), now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "not configured")
        self.assertIn("VERCEL_TOKEN", provider["source_detail"])

    def test_vercel_billing_charges_provider_message_is_sanitized(self) -> None:
        live = {
            "type": "vercel_billing_charges",
            "url_env": "VERCEL_URL",
            "token_env": "VERCEL_TOKEN",
            "focus_mappings": {"fast_data_transfer_gb": {"contains_all": ["fast", "data", "transfer"]}},
        }
        payload = json.dumps({"error": {"code": "costs_not_found", "message": "costs_not_found"}})
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(vercel_quota_config(live)),
                "VERCEL_URL": "https://api.vercel.com/v1/billing/charges?teamId=team_123",
                "VERCEL_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=TextHttpClient(payload), now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "unavailable")
        self.assertIn("costs_not_found", provider["source_detail"])
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("team_123", json.dumps(data))

    def test_sentry_base_url_accepts_api_root(self) -> None:
        live = {
            "type": "sentry_stats_v2",
            "token_env": "DEMO_TOKEN",
            "org_env": "DEMO_ORG",
            "base_url_env": "DEMO_BASE_URL",
        }
        provider = quota_config(live=live)[0]
        provider["metrics"][0]["live_category"] = "error"
        client = FakeHttpClient({"groups": [{"totals": {"sum(quantity)": 2}}]})
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps([provider]),
                "DEMO_TOKEN": "sentinel-redaction-value",
                "DEMO_ORG": "demo-org",
                "DEMO_BASE_URL": "https://sentry.io/api/0",
            }
            data = FreeTierCollector(env=env, http_client=client, now=NOW).collect()
        self.assertEqual(data["providers"][0]["status"], "live")
        self.assertIn("/api/0/organizations/demo-org/stats_v2/", client.urls[0])
        self.assertNotIn("/api/0/api/0/", client.urls[0])

    def test_github_actions_public_usage_without_token(self) -> None:
        live = {
            "type": "github_actions",
            "url_env": "DEMO_GITHUB_URL",
            "token_env": "DEMO_GITHUB_TOKEN",
            "url": "https://api.github.com/repos/example/repo",
        }
        client = GitHubHttpClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(github_quota_config(live)),
            }
            data = FreeTierCollector(env=env, http_client=client, now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "live")
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(usage_by_key["artifact_storage_mb"], 3)
        self.assertEqual(usage_by_key["cache_storage_gb"], 1)
        self.assertIsNone(usage_by_key["rest_api_requests"])
        self.assertNotIn("https://api.github.com/rate_limit", client.urls)
        self.assertNotIn("Authorization", client.headers[0])
        self.assertIn("without a token", provider["source_detail"])

    def test_github_actions_missing_token_remains_actionable_when_public_usage_fails(self) -> None:
        live = {
            "type": "github_actions",
            "url_env": "DEMO_GITHUB_URL",
            "token_env": "DEMO_GITHUB_TOKEN",
            "url": "https://api.github.com/repos/example/repo",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(github_quota_config(live)),
            }
            data = FreeTierCollector(
                env=env,
                http_client=FakeHttpClient(ApiRequestError(status_code=404, body=b'{"message":"not found"}')),
                now=NOW,
            ).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "not configured")
        self.assertIn("DEMO_GITHUB_TOKEN", provider["source_detail"])
        self.assertIn("HTTP 404", provider["source_detail"])

    def test_github_actions_live_usage(self) -> None:
        live = {
            "type": "github_actions",
            "url_env": "DEMO_GITHUB_URL",
            "token_env": "DEMO_GITHUB_TOKEN",
            "url": "https://api.github.com/repos/example/repo",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(github_quota_config(live)),
                "DEMO_GITHUB_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, http_client=GitHubHttpClient(), now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "live")
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(usage_by_key["artifact_storage_mb"], 3)
        self.assertEqual(usage_by_key["cache_storage_gb"], 1)
        self.assertEqual(usage_by_key["rest_api_requests"], 42)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_cloudflare_graphql_missing_account_id_is_actionable(self) -> None:
        live = {
            "type": "cloudflare_graphql",
            "url_env": "DEMO_CLOUDFLARE_URL",
            "token_env": "DEMO_CLOUDFLARE_TOKEN",
            "account_id_env": "DEMO_CLOUDFLARE_ACCOUNT_ID",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(cloudflare_quota_config(live)),
                "DEMO_CLOUDFLARE_URL": "https://api.cloudflare.com/client/v4/graphql",
                "DEMO_CLOUDFLARE_TOKEN": "sentinel-redaction-value",
            }
            data = FreeTierCollector(env=env, now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "not configured")
        self.assertIn("DEMO_CLOUDFLARE_ACCOUNT_ID", provider["source_detail"])
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))

    def test_cloudflare_graphql_live_usage(self) -> None:
        live = {
            "type": "cloudflare_graphql",
            "url_env": "DEMO_CLOUDFLARE_URL",
            "token_env": "DEMO_CLOUDFLARE_TOKEN",
            "account_id_env": "DEMO_CLOUDFLARE_ACCOUNT_ID",
        }
        client = CloudflareHttpClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(cloudflare_quota_config(live)),
                "DEMO_CLOUDFLARE_URL": "https://api.cloudflare.com/client/v4/graphql",
                "DEMO_CLOUDFLARE_TOKEN": "sentinel-redaction-value",
                "DEMO_CLOUDFLARE_ACCOUNT_ID": "sentinel-account-id",
            }
            data = FreeTierCollector(env=env, http_client=client, now=NOW).collect()
        provider = data["providers"][0]
        usage_by_key = {metric["key"]: metric["usage"] for metric in provider["metrics"]}
        self.assertEqual(provider["status"], "live")
        self.assertEqual(usage_by_key["workers_requests"], 12)
        self.assertIsNone(usage_by_key["pages_builds"])
        self.assertEqual(client.urls, ["https://api.cloudflare.com/client/v4/graphql"])
        self.assertEqual(client.bodies[0]["variables"]["accountTag"], "sentinel-account-id")
        self.assertEqual(client.bodies[0]["variables"]["datetimeStart"], "2026-07-07T00:00:00Z")
        self.assertEqual(client.bodies[0]["variables"]["datetimeEnd"], "2026-07-07T12:00:00Z")
        self.assertIn("Authorization", client.headers[0])
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("sentinel-account-id", json.dumps(data))

    def test_cloudflare_graphql_errors_are_sanitized(self) -> None:
        live = {
            "type": "cloudflare_graphql",
            "url_env": "DEMO_CLOUDFLARE_URL",
            "token_env": "DEMO_CLOUDFLARE_TOKEN",
            "account_id_env": "DEMO_CLOUDFLARE_ACCOUNT_ID",
        }
        payload = {"data": None, "errors": [{"message": "permission denied"}]}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(cloudflare_quota_config(live)),
                "DEMO_CLOUDFLARE_URL": "https://api.cloudflare.com/client/v4/graphql",
                "DEMO_CLOUDFLARE_TOKEN": "sentinel-redaction-value",
                "DEMO_CLOUDFLARE_ACCOUNT_ID": "sentinel-account-id",
            }
            data = FreeTierCollector(
                env=env,
                http_client=CloudflareHttpClient(payload),
                now=NOW,
            ).collect()
        detail = data["providers"][0]["source_detail"]
        self.assertEqual(data["providers"][0]["status"], "unavailable")
        self.assertIn("permission denied", detail)
        self.assertNotIn("sentinel-redaction-value", json.dumps(data))
        self.assertNotIn("sentinel-account-id", json.dumps(data))

    def test_stale_cached_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir) / "cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "providers": {
                            "demo": {
                                "last_checked_at": "2026-07-07T10:00:00+00:00",
                                "metrics": {"requests": 10},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "NUTSNEWS_FREE_TIER_CACHE_FILE": str(cache),
                "NUTSNEWS_FREE_TIER_CACHE_TTL_SECONDS": "60",
                "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config()),
            }
            data = FreeTierCollector(env=env, now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "cached")
        self.assertTrue(provider["stale"])
        self.assertIn("stale", provider["source_detail"])


if __name__ == "__main__":
    unittest.main()
