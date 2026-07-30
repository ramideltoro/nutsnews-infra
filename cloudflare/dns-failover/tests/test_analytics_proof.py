from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify_failover_analytics.py"
)
SPEC = importlib.util.spec_from_file_location("verify_failover_analytics", SCRIPT)
assert SPEC and SPEC.loader
analytics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analytics)


class FailoverAnalyticsProofTests(unittest.TestCase):
    def test_deployed_binding_summary_is_value_free(self):
        summary = analytics.summarize_bindings(
            {
                "success": True,
                "result": {
                    "bindings": [
                        {"name": "ADMIN_TOKEN", "type": "secret_text", "text": "hidden"},
                        {
                            "name": "DNS_FAILOVER",
                            "type": "durable_object_namespace",
                            "namespace_id": "hidden",
                        },
                        {
                            "name": "FAILOVER_ANALYTICS",
                            "type": "analytics_engine",
                            "dataset": "nutsnews_dns_failover_v1",
                        },
                    ]
                },
            }
        )

        self.assertTrue(summary["failover_analytics"]["present"])
        self.assertTrue(summary["dns_failover"]["present"])
        self.assertNotIn("hidden", str(summary))
        self.assertNotIn("namespace_id", str(summary))

    def test_graphql_summary_requires_positive_events(self):
        empty = analytics.summarize_graphql(
            {"data": {"viewer": {"accounts": [{"workersAnalyticsEngineAdaptiveGroups": []}]}}}
        )
        self.assertTrue(empty["query_succeeded"])
        self.assertFalse(empty["positive_event_count"])

        populated = analytics.summarize_graphql(
            {
                "data": {
                    "viewer": {
                        "accounts": [
                            {
                                "workersAnalyticsEngineAdaptiveGroups": [
                                    {
                                        "count": 3,
                                        "dimensions": {
                                            "dataset": "nutsnews_dns_failover_v1",
                                            "datetimeMinute": "2026-07-30T20:10:00Z",
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        )
        self.assertTrue(populated["query_succeeded"])
        self.assertTrue(populated["positive_event_count"])
        self.assertEqual(populated["sampled_event_count"], 3)

    def test_schedule_summary_requires_minute_watchdog(self):
        pending = analytics.summarize_schedules({"success": True, "result": []})
        self.assertTrue(pending["query_succeeded"])
        self.assertFalse(pending["minute_watchdog_present"])

        propagated = analytics.summarize_schedules(
            {"success": True, "result": [{"cron": "* * * * *"}]}
        )
        self.assertTrue(propagated["minute_watchdog_present"])

    def test_graphql_errors_fail_closed_without_copying_messages(self):
        summary = analytics.summarize_graphql(
            {"errors": [{"message": "sensitive upstream detail"}], "data": None}
        )
        self.assertFalse(summary["query_succeeded"])
        self.assertEqual(summary["error_count"], 1)
        self.assertNotIn("sensitive upstream detail", str(summary))


if __name__ == "__main__":
    unittest.main()
