#!/usr/bin/env python3
"""Unit coverage for read-only free-tier usage collection."""

from __future__ import annotations

import json
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


class GitHubHttpClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.urls.append(url)
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


def collect(env: dict[str, str], http_client: FakeHttpClient | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        merged = {
            "NUTSNEWS_FREE_TIER_CACHE_FILE": str(Path(tmpdir) / "cache.json"),
            "NUTSNEWS_FREE_TIER_QUOTAS_JSON": json.dumps(quota_config()),
            **env,
        }
        return FreeTierCollector(env=merged, http_client=http_client, now=NOW).collect()


class FreeTierUsageTests(unittest.TestCase):
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

    def test_github_actions_missing_token_is_actionable(self) -> None:
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
            data = FreeTierCollector(env=env, now=NOW).collect()
        provider = data["providers"][0]
        self.assertEqual(provider["status"], "not configured")
        self.assertIn("DEMO_GITHUB_TOKEN", provider["source_detail"])

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
