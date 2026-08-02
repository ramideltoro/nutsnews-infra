#!/usr/bin/env python3
"""Validate protected Synthetic Monitoring inputs without disclosing their values."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any


EXPECTED_CHECKS = frozenset(
    {
        "canonical_articles_api",
        "canonical_homepage",
        "canonical_readiness",
        "vercel_secondary_readiness",
        "vps_readiness",
    }
)
READINESS_CHECKS = frozenset(
    {"canonical_readiness", "vercel_secondary_readiness", "vps_readiness"}
)
EXPECTED_FREQUENCY_MS = 300_000
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 60_000
DEFAULT_TIMEOUT_MS = 5_000
DEFAULT_FREE_API_EXECUTIONS_MONTHLY = 100_000
FREE_TIER_MAJOR_RATIO = 0.85
FREE_TIER_HARD_CEILING_RATIO = 0.90
SYNTHETIC_API_EXECUTION_HARD_CEILING_MONTHLY = 90_000
MILLISECONDS_PER_30_DAY_MONTH = 2_592_000_000
GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"
SYNTHETIC_MONITORING_HOSTNAME = re.compile(
    r"synthetic-monitoring-api(?:[.-][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.grafana\.net",
    re.ASCII,
)
PUBLIC_TARGET_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.ASCII,
)


class QuotaGuardrailError(ValueError):
    """A value-free quota report is available even though validation failed."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _json(raw: str, expected: type, variable_name: str) -> Any:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{variable_name} must be valid JSON when set.") from exc
    if not isinstance(value, expected):
        expected_name = "array" if expected is list else "object"
        raise ValueError(f"{variable_name} must be a JSON {expected_name}.")
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _boolean(raw: str, variable_name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false" or not normalized:
        return False
    raise ValueError(f"{variable_name} must be true or false.")


APPROVED_ASSERTION_CONTRACT: dict[str, dict[str, list[Any]]] = {
    "canonical_homepage": {
        "fail_if_body_matches_regexp": ["maintenance"],
        "fail_if_body_not_matches_regexp": ["NutsNews"],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [],
    },
    "canonical_articles_api": {
        "fail_if_body_matches_regexp": [],
        "fail_if_body_not_matches_regexp": ["articles"],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": False,
                "header": "Cache-Control",
                "regexp": "public|max-age|s-maxage",
            }
        ],
    },
    "canonical_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*(production-vps|vercel-production)",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": False,
                "header": "Cache-Control",
                "regexp": "no-store",
            }
        ],
    },
    "vps_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*production-vps",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": False,
                "header": "Cache-Control",
                "regexp": "no-store",
            }
        ],
    },
    "vercel_secondary_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*vercel-production",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": False,
                "header": "Cache-Control",
                "regexp": "no-store",
            }
        ],
    },
}


def _validate_role_origin(
    variable_name: str,
    value: str,
    *,
    hostname_is_allowed: Any,
    role: str,
) -> str:
    """Return a normalized, role-pinned Grafana origin without echoing input."""

    message = (
        f"{variable_name} must be an exact query-free HTTPS {role} origin using "
        "implicit or explicit port 443."
    )
    if not value or value != value.strip():
        raise ValueError(message)
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not hostname_is_allowed(hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower() not in {hostname, f"{hostname}:443"}
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(message)
    return f"https://{hostname}"


def validate_grafana_ui_origin(variable_name: str, value: str) -> str:
    return _validate_role_origin(
        variable_name,
        value,
        hostname_is_allowed=lambda hostname: hostname == GRAFANA_UI_HOSTNAME,
        role="kindcantaloupe2036.grafana.net Grafana UI",
    )


def validate_synthetic_monitoring_origin(variable_name: str, value: str) -> str:
    return _validate_role_origin(
        variable_name,
        value,
        hostname_is_allowed=lambda hostname: (
            SYNTHETIC_MONITORING_HOSTNAME.fullmatch(hostname) is not None
        ),
        role="synthetic-monitoring-api*.grafana.net service",
    )


def _assertion_lists(check: dict[str, Any], check_name: str) -> dict[str, list[Any]]:
    fields = (
        "fail_if_body_matches_regexp",
        "fail_if_body_not_matches_regexp",
        "fail_if_header_matches_regexp",
        "fail_if_header_not_matches_regexp",
    )
    assertions: dict[str, list[Any]] = {}
    for field in fields:
        value = check.get(field, [])
        if not isinstance(value, list):
            raise ValueError(f"Synthetic check {check_name} assertion fields must be JSON arrays.")
        assertions[field] = value

    for field in ("fail_if_body_matches_regexp", "fail_if_body_not_matches_regexp"):
        if not all(isinstance(pattern, str) and pattern for pattern in assertions[field]):
            raise ValueError(f"Synthetic check {check_name} body assertions must be nonempty strings.")
    for field in ("fail_if_header_matches_regexp", "fail_if_header_not_matches_regexp"):
        for assertion in assertions[field]:
            if (
                not isinstance(assertion, dict)
                or not isinstance(assertion.get("header"), str)
                or not assertion["header"]
                or not isinstance(assertion.get("regexp"), str)
                or not assertion["regexp"]
                or not isinstance(assertion.get("allow_missing", False), bool)
            ):
                raise ValueError(
                    f"Synthetic check {check_name} header assertions must contain a header, "
                    "regexp, and optional boolean allow_missing."
                )
    if sum(len(value) for value in assertions.values()) < 1:
        raise ValueError(
            f"Synthetic check {check_name} must include a body or header assertion."
        )
    return assertions


def _header_assertion_matches(
    assertions: list[Any], *, header: str, regexp: str
) -> bool:
    return any(
        isinstance(assertion, dict)
        and assertion.get("header", "").lower() == header
        and assertion.get("allow_missing", False) is False
        and re.search(regexp, assertion.get("regexp", "").lower()) is not None
        for assertion in assertions
    )


def _validate_assertion_contract(
    check_name: str, assertions: dict[str, list[Any]]
) -> None:
    normalized = {
        "fail_if_body_matches_regexp": assertions["fail_if_body_matches_regexp"],
        "fail_if_body_not_matches_regexp": assertions[
            "fail_if_body_not_matches_regexp"
        ],
        "fail_if_header_matches_regexp": [
            {
                "allow_missing": assertion.get("allow_missing", False),
                "header": assertion["header"],
                "regexp": assertion["regexp"],
            }
            for assertion in assertions["fail_if_header_matches_regexp"]
        ],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": assertion.get("allow_missing", False),
                "header": assertion["header"],
                "regexp": assertion["regexp"],
            }
            for assertion in assertions["fail_if_header_not_matches_regexp"]
        ],
    }
    if normalized != APPROVED_ASSERTION_CONTRACT[check_name]:
        raise ValueError(
            f"Synthetic check {check_name} assertions must exactly match the approved "
            "behavioral contract."
        )


def _validate_target(check_name: str, target: Any) -> str:
    if not isinstance(target, str) or not target or target != target.strip():
        raise ValueError(
            f"Synthetic check {check_name} must use its query-free public HTTPS route."
        )
    try:
        parsed = urllib.parse.urlsplit(target)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"Synthetic check {check_name} must use its query-free public HTTPS route."
        ) from exc
    expected_path = (
        "/"
        if check_name == "canonical_homepage"
        else "/api/articles"
        if check_name == "canonical_articles_api"
        else "/readyz"
    )
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or PUBLIC_TARGET_HOSTNAME.fullmatch(parsed.hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower() not in {parsed.hostname, f"{parsed.hostname}:443"}
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != expected_path.rstrip("/")
    ):
        raise ValueError(
            f"Synthetic check {check_name} must use its query-free public HTTPS route."
        )
    forbidden = {"refresh", "controller", "ingest", "trigger", "publish"}
    segments = {segment.lower() for segment in parsed.path.split("/") if segment}
    if segments & forbidden:
        raise ValueError(f"Synthetic check {check_name} targets a side-effecting route.")
    return parsed.hostname


def validate_inputs(
    probes_raw: str,
    checks_raw: str,
    *,
    token_present: bool,
    grafana_url: str,
    synthetic_monitoring_url: str,
    major_forecast_acknowledged: bool,
    free_api_executions_monthly: int = DEFAULT_FREE_API_EXECUTIONS_MONTHLY,
) -> dict[str, Any]:
    validate_grafana_ui_origin("NUTSNEWS_GRAFANA_CLOUD_URL", grafana_url)
    validate_synthetic_monitoring_origin(
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL", synthetic_monitoring_url
    )
    probes = _json(probes_raw.strip() or "[]", list, "NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON")
    checks = _json(checks_raw.strip() or "{}", dict, "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON")

    if (
        len(probes) != 2
        or not all(_integer(probe) and probe > 0 for probe in probes)
        or len(set(probes)) != 2
    ):
        raise ValueError("Synthetic Monitoring requires exactly two unique positive public probe IDs.")
    if set(checks) != EXPECTED_CHECKS:
        raise ValueError("Synthetic Monitoring must configure exactly the five approved read-only checks.")
    if not token_present:
        raise ValueError("NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN is required.")
    if not isinstance(major_forecast_acknowledged, bool):
        raise ValueError("The synthetic major forecast acknowledgment must be boolean.")
    if not _integer(free_api_executions_monthly) or free_api_executions_monthly <= 0:
        raise ValueError("The free Synthetic Monitoring API execution assumption must be positive.")

    target_hosts: dict[str, str] = {}
    for check_name, check in checks.items():
        if not isinstance(check, dict):
            raise ValueError(f"Synthetic check {check_name} must be an enabled JSON object.")
        if check.get("enabled", True) is not True:
            raise ValueError(f"Synthetic check {check_name} must be enabled.")
        frequency_ms = check.get("frequency_ms", EXPECTED_FREQUENCY_MS)
        if not _integer(frequency_ms) or frequency_ms != EXPECTED_FREQUENCY_MS:
            raise ValueError(f"Synthetic check {check_name} must run every five minutes.")
        timeout_ms = check.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        if not _integer(timeout_ms) or not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
            raise ValueError(
                f"Synthetic check {check_name} timeout must be between 1 and 60 seconds."
            )
        if check.get("valid_status_codes", [200]) != [200]:
            raise ValueError(f"Synthetic check {check_name} must require only HTTP 200.")
        target_hosts[check_name] = _validate_target(check_name, check.get("target"))
        _validate_assertion_contract(check_name, _assertion_lists(check, check_name))

    canonical_hosts = {
        target_hosts["canonical_homepage"],
        target_hosts["canonical_readiness"],
        target_hosts["canonical_articles_api"],
    }
    if (
        len(canonical_hosts) != 1
        or target_hosts["vps_readiness"] in canonical_hosts
        or target_hosts["vercel_secondary_readiness"] in canonical_hosts
        or target_hosts["vps_readiness"] == target_hosts["vercel_secondary_readiness"]
    ):
        raise ValueError(
            "Synthetic targets must use one canonical host plus distinct direct-VPS and Vercel-secondary hosts."
        )
    projected_executions = round(
        len(checks)
        * len(probes)
        * (MILLISECONDS_PER_30_DAY_MONTH / EXPECTED_FREQUENCY_MS)
    )
    allowance_guardrail = round(
        free_api_executions_monthly * FREE_TIER_HARD_CEILING_RATIO
    )
    effective_guardrail = min(
        SYNTHETIC_API_EXECUTION_HARD_CEILING_MONTHLY,
        allowance_guardrail,
    )
    major_threshold = round(free_api_executions_monthly * FREE_TIER_MAJOR_RATIO)
    report = {
        "schema_version": 2,
        "status": "pass",
        "value_free": True,
        "probe_count": len(probes),
        "enabled_check_count": len(checks),
        "disabled_check_count": 0,
        "minimum_frequency_seconds": EXPECTED_FREQUENCY_MS // 1000,
        "maximum_frequency_seconds": EXPECTED_FREQUENCY_MS // 1000,
        "projected_monthly_api_executions": projected_executions,
        "monthly_api_execution_guardrail": effective_guardrail,
        "monthly_api_execution_major_threshold": major_threshold,
        "monthly_api_execution_hard_ceiling": SYNTHETIC_API_EXECUTION_HARD_CEILING_MONTHLY,
        "major_forecast_acknowledged": major_forecast_acknowledged,
        "synthetic_monitoring_endpoint_configured": True,
    }
    if projected_executions >= effective_guardrail:
        report["status"] = "fail"
        report["error_code"] = "synthetic_api_execution_guardrail_exceeded"
        raise QuotaGuardrailError(
            "Configured Synthetic Monitoring checks reach or exceed the effective 90% allowance "
            "guardrail or the absolute 90,000-execution ceiling.",
            report,
        )
    if projected_executions >= major_threshold and not major_forecast_acknowledged:
        report["status"] = "fail"
        report["error_code"] = "synthetic_api_execution_major_acknowledgment_required"
        raise QuotaGuardrailError(
            "The five-check, two-probe, five-minute Synthetic Monitoring topology enters the "
            ">=85% major forecast band and requires the protected reviewed acknowledgment.",
            report,
        )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    try:
        report = validate_inputs(
            os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON", ""),
            os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON", ""),
            token_present=bool(
                os.environ.get("NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN", "").strip()
            ),
            grafana_url=os.environ.get("NUTSNEWS_GRAFANA_CLOUD_URL", ""),
            synthetic_monitoring_url=os.environ.get(
                "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL", ""
            ),
            major_forecast_acknowledged=_boolean(
                os.environ.get(
                    "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED", "false"
                ),
                "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED",
            ),
        )
    except QuotaGuardrailError as exc:
        text = json.dumps(exc.report, indent=2, sort_keys=True, allow_nan=False)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        _write_report(
            args.output,
            {
                "schema_version": 2,
                "status": "fail",
                "value_free": True,
                "error": str(exc),
            },
        )
        print(str(exc), file=sys.stderr)
        return 1

    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    print(text)
    _write_report(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
