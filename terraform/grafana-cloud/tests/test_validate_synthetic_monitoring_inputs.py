#!/usr/bin/env python3
"""Focused tests for value-free Synthetic Monitoring input validation."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_synthetic_monitoring_inputs.py"
SPEC = importlib.util.spec_from_file_location("validate_synthetic_monitoring_inputs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def readiness_assertions(identity: str) -> dict[str, object]:
    identity_pattern = f"({identity})" if "|" in identity else identity
    return {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            f"deploymentTarget.*{identity_pattern}",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {"allow_missing": False, "header": "Cache-Control", "regexp": "no-store"}
        ],
    }


def production_http_check(target: str) -> dict[str, object]:
    """Return the complete protected JSON shape used by production workflows."""
    return {
        "target": target,
        "enabled": True,
        "frequency_ms": 300_000,
        "timeout_ms": 5_000,
        "valid_status_codes": [200],
        "fail_if_body_matches_regexp": [],
        "fail_if_body_not_matches_regexp": [],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [],
    }


def valid_checks() -> dict[str, dict[str, object]]:
    checks = {
        "canonical_homepage": production_http_check("https://news.example.com/"),
        "canonical_readiness": production_http_check(
            "https://news.example.com/readyz"
        ),
        "canonical_articles_api": production_http_check(
            "https://news.example.com/api/articles"
        ),
        "vps_readiness": production_http_check("https://vps.example.com/readyz"),
        "vercel_secondary_readiness": production_http_check(
            "https://secondary.example.com/readyz"
        ),
    }
    checks["canonical_homepage"].update(
        {
            "fail_if_body_matches_regexp": ["maintenance"],
            "fail_if_body_not_matches_regexp": ["NutsNews"],
        }
    )
    checks["canonical_readiness"].update(
        readiness_assertions("production-vps|vercel-production")
    )
    checks["canonical_articles_api"].update(
        {
            "fail_if_body_not_matches_regexp": ["articles"],
            "fail_if_header_not_matches_regexp": [
                {
                    "allow_missing": False,
                    "header": "Cache-Control",
                    "regexp": "public|max-age|s-maxage",
                }
            ],
        }
    )
    checks["vps_readiness"].update(readiness_assertions("production-vps"))
    checks["vercel_secondary_readiness"].update(
        readiness_assertions("vercel-production")
    )
    return checks


class SyntheticMonitoringInputsTest(unittest.TestCase):
    def validate(
        self,
        checks: dict[str, dict[str, object]] | None = None,
        *,
        probes: list[int] | None = None,
        token_present: bool = True,
        grafana_url: str = "https://nutsnews.grafana.net",
        synthetic_monitoring_url: str = "https://synthetic-monitoring-api.grafana.net",
        major_forecast_acknowledged: bool = True,
        free_api_executions_monthly: int = 100_000,
    ) -> dict[str, object]:
        return MODULE.validate_inputs(
            json.dumps([101, 202] if probes is None else probes),
            json.dumps(valid_checks() if checks is None else checks),
            token_present=token_present,
            grafana_url=grafana_url,
            synthetic_monitoring_url=synthetic_monitoring_url,
            major_forecast_acknowledged=major_forecast_acknowledged,
            free_api_executions_monthly=free_api_executions_monthly,
        )

    def test_accepts_explicit_production_shape_with_reviewed_acknowledgment(self) -> None:
        checks = valid_checks()
        expected_fields = {
            "target",
            "enabled",
            "frequency_ms",
            "timeout_ms",
            "valid_status_codes",
            "fail_if_body_matches_regexp",
            "fail_if_body_not_matches_regexp",
            "fail_if_header_matches_regexp",
            "fail_if_header_not_matches_regexp",
        }
        self.assertTrue(all(set(check) == expected_fields for check in checks.values()))
        self.assertTrue(all(check["enabled"] is True for check in checks.values()))
        self.assertTrue(
            all(check["frequency_ms"] == 300_000 for check in checks.values())
        )

        report = self.validate(checks)
        serialized = json.dumps(report)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["value_free"])
        self.assertEqual(report["probe_count"], 2)
        self.assertEqual(report["enabled_check_count"], 5)
        self.assertEqual(report["projected_monthly_api_executions"], 86_400)
        self.assertEqual(report["monthly_api_execution_major_threshold"], 85_000)
        self.assertEqual(report["monthly_api_execution_hard_ceiling"], 90_000)
        self.assertEqual(report["monthly_api_execution_guardrail"], 90_000)
        self.assertTrue(report["synthetic_monitoring_endpoint_configured"])
        self.assertNotIn("canonical_homepage", serialized)
        self.assertNotIn("news.example.com", serialized)
        self.assertNotIn("101", serialized)

    def test_optional_transport_fields_resolve_to_the_approved_production_contract(self) -> None:
        checks = valid_checks()
        for check in checks.values():
            for field in ("enabled", "frequency_ms", "timeout_ms", "valid_status_codes"):
                del check[field]

        report = self.validate(checks)

        self.assertEqual(report["enabled_check_count"], 5)
        self.assertEqual(report["minimum_frequency_seconds"], 300)
        self.assertEqual(report["maximum_frequency_seconds"], 300)
        self.assertEqual(report["projected_monthly_api_executions"], 86_400)

    def test_requires_exactly_two_unique_positive_probe_ids(self) -> None:
        for probes in ([101], [101, 101], [101, -2], [101, True], [101, 202, 303]):
            with self.subTest(probes=probes), self.assertRaisesRegex(ValueError, "exactly two"):
                self.validate(probes=probes)

    def test_requires_exact_five_check_inventory(self) -> None:
        checks = valid_checks()
        del checks["canonical_homepage"]
        with self.assertRaisesRegex(ValueError, "exactly the five"):
            self.validate(checks)

    def test_requires_access_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "ACCESS_TOKEN is required"):
            self.validate(token_present=False)

    def test_requires_five_minute_enabled_http_200_checks(self) -> None:
        mutations = (
            ("frequency_ms", 600_000, "five minutes"),
            ("enabled", False, "must be enabled"),
            ("valid_status_codes", [204], "only HTTP 200"),
        )
        for key, value, message in mutations:
            checks = valid_checks()
            checks["canonical_homepage"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                self.validate(checks)

    def test_rejects_timeout_outside_grafana_bounds(self) -> None:
        for timeout_ms in (999, 60_001, True):
            checks = valid_checks()
            checks["canonical_homepage"]["timeout_ms"] = timeout_ms
            with self.subTest(timeout_ms=timeout_ms), self.assertRaisesRegex(
                ValueError, "between 1 and 60 seconds"
            ):
                self.validate(checks)

    def test_rejects_credentials_query_fragment_port_and_wrong_route(self) -> None:
        targets = (
            "https://user:secret@news.example.com/",
            "https://news.example.com/?refresh=true",
            "https://news.example.com/#payload",
            "https://news.example.com:8443/",
            "https://news.example.com/refresh",
            "https://news example.com/",
            "https://news%2eexample.com/",
            "https://news\\example.com/",
            "https://localhost/",
        )
        for target in targets:
            checks = valid_checks()
            checks["canonical_homepage"]["target"] = target
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "public HTTPS route"):
                self.validate(checks)

    def test_explicit_443_and_implicit_port_hosts_have_identical_roles(self) -> None:
        checks = valid_checks()
        checks["canonical_homepage"]["target"] = "https://news.example.com:443/"
        report = self.validate(checks)
        self.assertEqual(report["status"], "pass")

    def test_requires_distinct_canonical_vps_and_secondary_hosts(self) -> None:
        checks = valid_checks()
        checks["vps_readiness"]["target"] = "https://news.example.com/readyz"
        with self.assertRaisesRegex(ValueError, "distinct direct-VPS"):
            self.validate(checks)

    def test_requires_readiness_body_identity_and_no_store_assertions(self) -> None:
        for field in (
            "fail_if_body_matches_regexp",
            "fail_if_body_not_matches_regexp",
            "fail_if_header_not_matches_regexp",
        ):
            checks = valid_checks()
            checks["vps_readiness"][field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(checks)

    def test_requires_homepage_and_articles_content_contracts(self) -> None:
        for check_name, field in (
            ("canonical_homepage", "fail_if_body_matches_regexp"),
            ("canonical_articles_api", "fail_if_header_not_matches_regexp"),
        ):
            checks = valid_checks()
            checks[check_name][field] = []
            with self.subTest(check_name=check_name), self.assertRaises(ValueError):
                self.validate(checks)

    def test_rejects_ineffective_or_merely_token_containing_assertions(self) -> None:
        mutations = (
            (
                "canonical_readiness",
                "fail_if_body_not_matches_regexp",
                ["ready|true|deploymentTarget"],
            ),
            (
                "canonical_homepage",
                "fail_if_body_not_matches_regexp",
                ["(?!)NutsNews"],
            ),
            (
                "vps_readiness",
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
        for check_name, field, value in mutations:
            checks = valid_checks()
            checks[check_name][field] = value
            with self.subTest(check_name=check_name, field=field), self.assertRaisesRegex(
                ValueError, "exactly match the approved behavioral contract"
            ):
                self.validate(checks)

    def test_rejects_non_grafana_cloud_origins_without_echoing_them(self) -> None:
        for argument in ("grafana_url", "synthetic_monitoring_url"):
            kwargs = {argument: "https://token@attacker.invalid/path?secret=value"}
            with self.subTest(argument=argument), self.assertRaisesRegex(
                ValueError, "exact query-free HTTPS"
            ) as raised:
                self.validate(**kwargs)
            self.assertNotIn("attacker.invalid", str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_origins_are_pinned_to_their_service_roles(self) -> None:
        accepted_sm = (
            "https://synthetic-monitoring-api.grafana.net",
            "https://synthetic-monitoring-api.us.grafana.net:443/",
            "https://synthetic-monitoring-api-us-east-0.grafana.net",
        )
        for origin in accepted_sm:
            with self.subTest(origin=origin):
                self.assertEqual(self.validate(synthetic_monitoring_url=origin)["status"], "pass")

        rejected = (
            {"grafana_url": "https://other-tenant.grafana.net"},
            {"grafana_url": "https://synthetic-monitoring-api.grafana.net"},
            {"synthetic_monitoring_url": "https://nutsnews.grafana.net"},
            {"synthetic_monitoring_url": "https://other-tenant.grafana.net"},
            {
                "synthetic_monitoring_url": (
                    "https://synthetic-monitoring-apiattacker.grafana.net"
                )
            },
        )
        for kwargs in rejected:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(
                ValueError, "exact query-free HTTPS"
            ):
                self.validate(**kwargs)

    def test_malformed_nfkc_netloc_never_leaks_raw_values(self) -> None:
        sentinel = "protected-sentinel"
        malformed = f"https://{sentinel}\uff20attacker.invalid/"
        with self.assertRaisesRegex(ValueError, "exact query-free HTTPS") as origin_error:
            self.validate(grafana_url=malformed)
        self.assertNotIn(sentinel, str(origin_error.exception))

        checks = valid_checks()
        checks["canonical_homepage"]["target"] = malformed
        with self.assertRaisesRegex(ValueError, "public HTTPS route") as target_error:
            self.validate(checks)
        self.assertNotIn(sentinel, str(target_error.exception))

    def test_requires_acknowledgment_for_standing_major_forecast(self) -> None:
        with self.assertRaisesRegex(
            MODULE.QuotaGuardrailError, ">=85% major forecast"
        ) as raised:
            self.validate(major_forecast_acknowledged=False)
        report = raised.exception.report
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            report["error_code"],
            "synthetic_api_execution_major_acknowledgment_required",
        )
        self.assertEqual(report["projected_monthly_api_executions"], 86_400)
        self.assertEqual(report["monthly_api_execution_major_threshold"], 85_000)
        self.assertEqual(report["monthly_api_execution_hard_ceiling"], 90_000)
        self.assertFalse(report["major_forecast_acknowledged"])
        self.assertNotIn("canonical_homepage", json.dumps(report))
        self.assertNotIn("news.example.com", json.dumps(report))
        self.assertNotIn("101", json.dumps(report))

    def test_fails_at_or_above_ninety_percent_hard_ceiling(self) -> None:
        with self.assertRaisesRegex(
            MODULE.QuotaGuardrailError, "reach or exceed the effective 90%"
        ) as raised:
            self.validate(free_api_executions_monthly=95_000)
        report = raised.exception.report
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            report["error_code"], "synthetic_api_execution_guardrail_exceeded"
        )
        self.assertEqual(report["projected_monthly_api_executions"], 86_400)
        self.assertEqual(report["monthly_api_execution_hard_ceiling"], 90_000)
        self.assertEqual(report["monthly_api_execution_guardrail"], 85_500)

    def test_absolute_hard_ceiling_never_rises_with_a_larger_allowance(self) -> None:
        report = self.validate(free_api_executions_monthly=200_000)

        self.assertEqual(report["monthly_api_execution_hard_ceiling"], 90_000)
        self.assertEqual(report["monthly_api_execution_guardrail"], 90_000)
        self.assertLess(
            report["projected_monthly_api_executions"],
            report["monthly_api_execution_hard_ceiling"],
        )

    def test_rejects_non_standard_json_numeric_constants(self) -> None:
        checks_raw = json.dumps(valid_checks()).replace("5000", "NaN", 1)
        with self.assertRaisesRegex(ValueError, "must be valid JSON"):
            MODULE.validate_inputs(
                "[101,202]",
                checks_raw,
                token_present=True,
                grafana_url="https://nutsnews.grafana.net",
                synthetic_monitoring_url=(
                    "https://synthetic-monitoring-api.grafana.net"
                ),
                major_forecast_acknowledged=True,
            )

    def test_boolean_parser_fails_closed(self) -> None:
        self.assertTrue(MODULE._boolean("true", "ACK"))
        self.assertFalse(MODULE._boolean("", "ACK"))
        with self.assertRaisesRegex(ValueError, "true or false"):
            MODULE._boolean("yes", "ACK")

    def test_public_validator_requires_boolean_acknowledgment(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            self.validate(major_forecast_acknowledged="false")  # type: ignore[arg-type]

    def test_cli_writes_value_free_success_and_failure_evidence(self) -> None:
        base_env = {
            "NUTSNEWS_GRAFANA_CLOUD_URL": "https://nutsnews.grafana.net",
            "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL": (
                "https://synthetic-monitoring-api.grafana.net"
            ),
            "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN": "protected-token",
            "NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON": "[101,202]",
            "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON": json.dumps(valid_checks()),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with mock.patch.dict(
                os.environ,
                {
                    **base_env,
                    "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED": "true",
                },
                clear=True,
            ), mock.patch.object(sys, "argv", [str(SCRIPT), "--output", str(output)]):
                self.assertEqual(MODULE.main(), 0)
            success = output.read_text(encoding="utf-8")
            self.assertIn('"status": "pass"', success)
            self.assertNotIn("protected-token", success)
            self.assertNotIn("news.example.com", success)

            with mock.patch.dict(
                os.environ,
                {
                    **base_env,
                    "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED": "false",
                },
                clear=True,
            ), mock.patch.object(sys, "argv", [str(SCRIPT), "--output", str(output)]):
                self.assertEqual(MODULE.main(), 1)
            failure = output.read_text(encoding="utf-8")
            self.assertIn('"status": "fail"', failure)
            self.assertIn(
                '"error_code": "synthetic_api_execution_major_acknowledgment_required"',
                failure,
            )
            self.assertIn('"projected_monthly_api_executions": 86400', failure)
            self.assertIn('"monthly_api_execution_major_threshold": 85000', failure)
            self.assertIn('"monthly_api_execution_hard_ceiling": 90000', failure)
            self.assertNotIn("protected-token", failure)
            self.assertNotIn("news.example.com", failure)
            self.assertNotIn("canonical_homepage", failure)
            self.assertNotIn("101", failure)


if __name__ == "__main__":
    unittest.main()
