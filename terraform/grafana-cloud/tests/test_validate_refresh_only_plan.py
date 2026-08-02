#!/usr/bin/env python3
"""Tests for resource-only refresh plan drift validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_refresh_only_plan.py"
SPEC = importlib.util.spec_from_file_location("validate_refresh_only_plan", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RefreshOnlyPlanTests(unittest.TestCase):
    def test_output_only_change_is_not_resource_drift(self) -> None:
        payload = {
            "format_version": "1.2",
            "errored": False,
            "resource_drift": [],
            "output_changes": {"new_output": {"actions": ["create"]}},
        }
        self.assertEqual(MODULE.resource_drift_findings(payload), [])

    def test_resource_drift_is_reported(self) -> None:
        payload = {
            "format_version": "1.2",
            "errored": False,
            "resource_drift": [
                {
                    "address": "grafana_dashboard.example",
                    "change": {"actions": ["update"]},
                }
            ],
        }
        self.assertEqual(
            MODULE.resource_drift_findings(payload),
            [("grafana_dashboard.example", ("update",))],
        )

    def test_empty_notification_timing_normalization_is_ignored(self) -> None:
        before = {
            "policy": [
                {
                    "contact_point": "NutsNews operations email",
                    "policy": [{"matcher": ["severity", "=", "critical"]}],
                }
            ]
        }
        after = {
            "policy": [
                {
                    "active_timings": [],
                    "mute_timings": [],
                    "contact_point": "NutsNews operations email",
                    "policy": [
                        {
                            "active_timings": [],
                            "mute_timings": [],
                            "matcher": ["severity", "=", "critical"],
                        }
                    ],
                }
            ]
        }
        payload = {
            "format_version": "1.2",
            "errored": False,
            "resource_drift": [
                {
                    "address": MODULE.NOTIFICATION_POLICY_ADDRESS,
                    "change": {
                        "actions": ["update"],
                        "before": before,
                        "after": after,
                    },
                }
            ],
        }
        self.assertEqual(MODULE.resource_drift_findings(payload), [])

    def test_notification_policy_real_changes_are_reported(self) -> None:
        for after in (
            {"contact_point": "changed", "active_timings": [], "mute_timings": []},
            {"contact_point": "expected", "active_timings": ["business-hours"]},
        ):
            payload = {
                "format_version": "1.2",
                "errored": False,
                "resource_drift": [
                    {
                        "address": MODULE.NOTIFICATION_POLICY_ADDRESS,
                        "change": {
                            "actions": ["update"],
                            "before": {"contact_point": "expected"},
                            "after": after,
                        },
                    }
                ],
            }
            with self.subTest(after=after):
                self.assertEqual(
                    MODULE.resource_drift_findings(payload),
                    [(MODULE.NOTIFICATION_POLICY_ADDRESS, ("update",))],
                )

    def test_malformed_or_errored_plan_fails_closed(self) -> None:
        for payload in (
            [],
            {"format_version": "2.0", "errored": False},
            {"format_version": "1.2", "errored": True},
            {"format_version": "1.2", "errored": False, "resource_drift": {}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(MODULE.RefreshPlanError):
                    MODULE.resource_drift_findings(payload)


if __name__ == "__main__":
    unittest.main()
