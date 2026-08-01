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


def check_set(count: int, *, frequency_ms: int = 300_000) -> str:
    return json.dumps(
        {
            f"fixture-{index}": {
                "target": f"https://example-{index}.invalid/health",
                "frequency_ms": frequency_ms,
                "timeout_ms": 5_000,
                "enabled": True,
            }
            for index in range(count)
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
        with self.assertRaisesRegex(MODULE.QuotaGuardrailError, "exceed 90%") as raised:
            MODULE.validate_inputs("[1]", checks(frequency_ms=10_000), token_present=True)
        report = raised.exception.report
        self.assertEqual(report["status"], "fail")
        self.assertTrue(report["value_free"])
        self.assertEqual(report["error_code"], "synthetic_api_execution_guardrail_exceeded")
        self.assertGreater(
            report["projected_monthly_api_executions"],
            report["monthly_api_execution_guardrail"],
        )
        self.assertNotIn("fixture", json.dumps(report))
        self.assertNotIn("example.invalid", json.dumps(report))

    def test_current_value_free_shape_stays_below_guardrail(self) -> None:
        report = MODULE.validate_inputs("[1, 2]", check_set(5), token_present=True)
        self.assertEqual(report["projected_monthly_api_executions"], 86_400)
        self.assertEqual(report["monthly_api_execution_guardrail"], 90_000)
        self.assertEqual(report["status"], "pass")
        self.assertNotIn("fixture", json.dumps(report))
        self.assertNotIn("example-", json.dumps(report))

    def test_one_more_current_shape_check_fails_closed(self) -> None:
        with self.assertRaises(MODULE.QuotaGuardrailError) as raised:
            MODULE.validate_inputs("[1, 2]", check_set(6), token_present=True)
        self.assertEqual(raised.exception.report["projected_monthly_api_executions"], 103_680)
        self.assertEqual(raised.exception.report["monthly_api_execution_guardrail"], 90_000)

    def test_rejects_non_https_target_without_echoing_it(self) -> None:
        bad = checks().replace("https://", "http://")
        with self.assertRaisesRegex(ValueError, "must start with https") as raised:
            MODULE.validate_inputs("[1]", bad, token_present=True)
        self.assertNotIn("example.invalid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
