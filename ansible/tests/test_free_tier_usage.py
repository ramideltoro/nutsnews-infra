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

from ops_free_tier_usage import FreeTierCollector  # noqa: E402


NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


class FakeHttpClient:
    def __init__(self, payload: object | Exception) -> None:
        self.payload = payload
        self.headers: list[dict[str, str]] = []

    def get_json(self, url, headers=None, params=None, timeout=8):  # noqa: ANN001, ANN201
        self.headers.append(headers or {})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def post_json(self, url, body, headers=None, timeout=8):  # noqa: ANN001, ANN201
        self.headers.append(headers or {})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def quota_config(live: dict | None = None) -> list[dict]:
    provider = {
        "key": "demo",
        "platform": "Demo",
        "quota_source": "https://example.invalid/docs",
        "quota_last_verified": "2026-07-07",
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
