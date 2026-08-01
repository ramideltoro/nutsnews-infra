#!/usr/bin/env python3
"""Focused tests for value-free Synthetic Monitoring input validation."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_synthetic_monitoring_inputs.py"
SPEC = importlib.util.spec_from_file_location("validate_synthetic_monitoring_inputs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def checks(*, frequency_ms: int = 300_000, enabled: bool = True) -> str:
    return json.dumps(
        {
            "fixture": {
                "target": "https://example.invalid/health",
                "frequency_ms": frequency_ms,
                "timeout_ms": 5_000,
                "enabled": enabled,
            }
        }
    )


class SyntheticMonitoringInputsTest(unittest.TestCase):
    def test_accepts_frequency_below_legacy_fifteen_minute_floor(self) -> None:
        report = MODULE.validate_inputs("[1]", checks(), token_present=True)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["value_free"])
        self.assertEqual(report["minimum_frequency_seconds"], 300)
        self.assertNotIn("fixture", json.dumps(report))
        self.assertNotIn("example.invalid", json.dumps(report))

    def test_rejects_frequency_below_grafana_minimum(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 10 seconds and 60 minutes"):
            MODULE.validate_inputs("[1]", checks(frequency_ms=9_999), token_present=True)

    def test_rejects_frequency_above_grafana_maximum(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 10 seconds and 60 minutes"):
            MODULE.validate_inputs("[1]", checks(frequency_ms=3_600_001), token_present=True)

    def test_requires_token_for_enabled_checks(self) -> None:
        with self.assertRaisesRegex(ValueError, "ACCESS_TOKEN is required"):
            MODULE.validate_inputs("[1]", checks(), token_present=False)

    def test_disabled_checks_do_not_require_token_or_consume_quota(self) -> None:
        report = MODULE.validate_inputs("[1]", checks(enabled=False), token_present=False)
        self.assertEqual(report["enabled_check_count"], 0)
        self.assertEqual(report["disabled_check_count"], 1)
        self.assertEqual(report["projected_monthly_api_executions"], 0)

    def test_rejects_projected_executions_above_guardrail(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceed 70%"):
            MODULE.validate_inputs("[1]", checks(frequency_ms=10_000), token_present=True)

    def test_rejects_non_https_target_without_echoing_it(self) -> None:
        bad = checks().replace("https://", "http://")
        with self.assertRaisesRegex(ValueError, "must start with https") as raised:
            MODULE.validate_inputs("[1]", bad, token_present=True)
        self.assertNotIn("example.invalid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
