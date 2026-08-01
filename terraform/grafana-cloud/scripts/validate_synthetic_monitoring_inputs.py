#!/usr/bin/env python3
"""Validate protected Synthetic Monitoring inputs without disclosing their values."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MIN_FREQUENCY_MS = 10_000
MAX_FREQUENCY_MS = 3_600_000
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 60_000
DEFAULT_FREQUENCY_MS = 1_800_000
DEFAULT_TIMEOUT_MS = 5_000
DEFAULT_FREE_API_EXECUTIONS_MONTHLY = 100_000
FREE_TIER_GUARDRAIL_RATIO = 0.90
MILLISECONDS_PER_30_DAY_MONTH = 2_592_000_000


class QuotaGuardrailError(ValueError):
    """A value-free quota report is available even though validation failed."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _json(raw: str, expected: type, variable_name: str) -> Any:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{variable_name} must be valid JSON when set.") from exc
    if not isinstance(value, expected):
        expected_name = "array" if expected is list else "object"
        raise ValueError(f"{variable_name} must be a JSON {expected_name}.")
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_inputs(
    probes_raw: str,
    checks_raw: str,
    *,
    token_present: bool,
    synthetic_monitoring_url: str,
    free_api_executions_monthly: int = DEFAULT_FREE_API_EXECUTIONS_MONTHLY,
) -> dict[str, Any]:
    probes = _json(probes_raw.strip() or "[]", list, "NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON")
    checks = _json(checks_raw.strip() or "{}", dict, "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON")

    if not all(_integer(probe) and probe > 0 for probe in probes):
        raise ValueError("Every synthetic probe ID must be a positive integer.")
    if len(set(probes)) != len(probes):
        raise ValueError("Synthetic probe IDs must be unique.")

    enabled_frequencies: list[int] = []
    disabled_check_count = 0
    for check in checks.values():
        if not isinstance(check, dict):
            raise ValueError("Every synthetic HTTP check must be a JSON object.")
        target = check.get("target")
        if not isinstance(target, str) or not target.startswith("https://"):
            raise ValueError("Every synthetic HTTP check target must start with https://.")
        enabled = check.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Every synthetic HTTP check enabled flag must be boolean.")
        frequency_ms = check.get("frequency_ms", DEFAULT_FREQUENCY_MS)
        if not _integer(frequency_ms) or not MIN_FREQUENCY_MS <= frequency_ms <= MAX_FREQUENCY_MS:
            raise ValueError("Every synthetic HTTP check frequency must be between 10 seconds and 60 minutes.")
        timeout_ms = check.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        if not _integer(timeout_ms) or not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
            raise ValueError("Every synthetic HTTP check timeout must be between 1 and 60 seconds.")
        if enabled:
            enabled_frequencies.append(frequency_ms)
        else:
            disabled_check_count += 1

    if probes and enabled_frequencies and not token_present:
        raise ValueError(
            "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN is required "
            "when synthetic probes and enabled HTTP checks are configured."
        )
    endpoint_configured = bool(synthetic_monitoring_url.strip())
    if probes and enabled_frequencies:
        if not endpoint_configured:
            raise ValueError(
                "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL is required "
                "when synthetic probes and enabled HTTP checks are configured."
            )
        parsed_endpoint = urlparse(synthetic_monitoring_url.strip())
        try:
            port = parsed_endpoint.port
        except ValueError as exc:
            raise ValueError(
                "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL must be a bounded HTTPS Grafana endpoint."
            ) from exc
        if (
            parsed_endpoint.scheme != "https"
            or not parsed_endpoint.hostname
            or not parsed_endpoint.hostname.endswith(".grafana.net")
            or parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or port not in (None, 443)
            or parsed_endpoint.path not in ("", "/")
            or parsed_endpoint.params
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError(
                "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL must be a bounded HTTPS Grafana endpoint."
            )

    projected_executions = round(
        sum(
            len(probes) * (MILLISECONDS_PER_30_DAY_MONTH / frequency_ms)
            for frequency_ms in enabled_frequencies
        )
    )
    guardrail = round(free_api_executions_monthly * FREE_TIER_GUARDRAIL_RATIO)
    report = {
        "schema_version": 1,
        "status": "fail" if projected_executions > guardrail else "pass",
        "value_free": True,
        "probe_count": len(probes),
        "enabled_check_count": len(enabled_frequencies),
        "disabled_check_count": disabled_check_count,
        "minimum_frequency_seconds": min(enabled_frequencies) // 1000 if enabled_frequencies else None,
        "maximum_frequency_seconds": max(enabled_frequencies) // 1000 if enabled_frequencies else None,
        "projected_monthly_api_executions": projected_executions,
        "monthly_api_execution_guardrail": guardrail,
        "synthetic_monitoring_endpoint_configured": endpoint_configured,
    }
    if projected_executions > guardrail:
        report["error_code"] = "synthetic_api_execution_guardrail_exceeded"
        raise QuotaGuardrailError(
            "Configured Synthetic Monitoring checks exceed 90% of the current free API execution assumption.",
            report,
        )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_inputs(
            os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON", ""),
            os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON", ""),
            token_present=bool(
                os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN", "").strip()
            ),
            synthetic_monitoring_url=os.environ.get(
                "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL", ""
            ),
        )
    except QuotaGuardrailError as exc:
        text = json.dumps(exc.report, indent=2, sort_keys=True)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
