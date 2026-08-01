#!/usr/bin/env python3
"""Write low-cardinality NutsNews observability metrics for Alloy textfile scraping."""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FILE = Path(os.environ.get("NUTSNEWS_PORTAL_STATUS_FILE", "/opt/nutsnews/portal-assets/data/status.json"))
OUTPUT_FILE = Path(
    os.environ.get("NUTSNEWS_OBSERVABILITY_TEXTFILE", "/var/lib/nutsnews/alloy/textfile/nutsnews.prom")
)
ALLOY_READY_URL = os.environ.get("NUTSNEWS_ALLOY_READY_URL", "http://127.0.0.1:12345/-/ready").strip()
CADDY_TLS_HOST = os.environ.get("NUTSNEWS_CADDY_TLS_HOST", "vps.nutsnews.com").strip()
try:
    PROBE_TIMEOUT_SECONDS = max(float(os.environ.get("NUTSNEWS_OBSERVABILITY_PROBE_TIMEOUT_SECONDS", "5")), 1.0)
except ValueError:
    PROBE_TIMEOUT_SECONDS = 5.0

DEFAULT_DOCKER_STATS_TARGETS = [
    {"container": "nutsnews-caddy", "service": "caddy", "expected_active": True},
    {"container": "nutsnews-app", "service": "web", "expected_active": False},
]
CANONICAL_PRODUCTION_READINESS_URL = "https://www.nutsnews.com/readyz"
PRODUCTION_READINESS_URL = os.environ.get(
    "NUTSNEWS_PRODUCTION_READINESS_URL", CANONICAL_PRODUCTION_READINESS_URL
).strip()
DEPLOYED_INFRA_COMMIT_FILE = Path(
    os.environ.get("NUTSNEWS_DEPLOYED_INFRA_COMMIT_FILE", "/opt/nutsnews/ops/deployed-infra-commit")
)
PRODUCTION_READINESS_MAX_BYTES = 16 * 1024
PRODUCTION_WEB_TARGETS = {"production-vps", "vercel-production"}
PRODUCTION_DATABASE_PROVIDERS = {"supabase_primary", "backend_postgres_primary"}
SAFE_RUNTIME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BYTE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def json_env(name: str, default: Any) -> Any:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


DOCKER_STATS_TARGETS = json_env("NUTSNEWS_DOCKER_STATS_TARGETS", DEFAULT_DOCKER_STATS_TARGETS)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip() or value in {"never", "unknown"}:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def bool_value(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "on", "success", "ok", "healthy", "running", "active"} else 0
    return 0


def success_value(value: Any) -> int:
    return 1 if str(value or "").strip().lower() in {"success", "succeeded", "ok", "fresh"} else 0


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def percent(value: Any, default: float = -1.0) -> float:
    return number(str(value or "").strip().removesuffix("%"), default)


def bytes_value(value: Any, default: float = -1.0) -> float:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)\s*", str(value or ""), re.IGNORECASE)
    if not match:
        return default
    return float(match.group(1)) * BYTE_UNITS.get(match.group(2).lower(), 1)


def pair_values(value: Any) -> tuple[float, float]:
    parts = str(value or "").split("/", maxsplit=1)
    if len(parts) != 2:
        return -1.0, -1.0
    return bytes_value(parts[0]), bytes_value(parts[1])


def label_value(value: Any) -> str:
    raw = str(value or "unknown")
    cleaned = []
    for char in raw:
        if char.isalnum() or char in "._:-":
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned)[:80] or "unknown"


def sample(name: str, value: float, labels: dict[str, str] | None = None) -> str:
    if not labels:
        return f"{name} {value:g}"
    rendered = ",".join(f'{key}="{label_value(item)}"' for key, item in sorted(labels.items()))
    return f"{name}{{{rendered}}} {value:g}"


def timestamp_samples(prefix: str, value: Any) -> list[str]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return [
            sample(f"{prefix}_available", 0),
            sample(f"{prefix}_timestamp_seconds", -1),
            sample(f"{prefix}_age_seconds", -1),
        ]
    now = time.time()
    return [
        sample(f"{prefix}_available", 1),
        sample(f"{prefix}_timestamp_seconds", parsed),
        sample(f"{prefix}_age_seconds", max(now - parsed, 0)),
    ]


def command(argv: list[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def valid_docker_targets() -> list[dict[str, Any]]:
    if not isinstance(DOCKER_STATS_TARGETS, list):
        return []
    targets: list[dict[str, Any]] = []
    seen_services: set[str] = set()
    for item in DOCKER_STATS_TARGETS:
        if not isinstance(item, dict):
            continue
        container = str(item.get("container") or "").strip()
        service = str(item.get("service") or "").strip()
        if not SAFE_RUNTIME_NAME_RE.fullmatch(container) or not SAFE_RUNTIME_NAME_RE.fullmatch(service):
            continue
        if service in seen_services:
            continue
        seen_services.add(service)
        targets.append(
            {
                "container": container,
                "service": service,
                "expected_active": bool_value(item.get("expected_active")),
            }
        )
    return targets[:16]


def collect_docker_stats(status: dict[str, Any]) -> list[str]:
    docker = nested(status, "docker", default={}) or {}
    containers = docker.get("containers", []) if isinstance(docker, dict) else []
    docker_state_available = isinstance(docker, dict) and bool_value(docker.get("available")) == 1
    known = {
        str(item.get("name") or ""): item
        for item in containers
        if isinstance(item, dict) and SAFE_RUNTIME_NAME_RE.fullmatch(str(item.get("name") or ""))
    }
    lines = [
        "# HELP nutsnews_docker_stats_available Whether bounded docker stats were collected for the service.",
        "# TYPE nutsnews_docker_stats_available gauge",
    ]
    for target in valid_docker_targets():
        container = target["container"]
        labels = {"service": target["service"]}
        expected_active = target["expected_active"]
        state = known.get(container, {})
        state_available = docker_state_available and bool(state)
        lines.extend(
            [
                sample("nutsnews_docker_container_expected_active", expected_active, labels),
                sample("nutsnews_docker_container_state_available", 1 if state_available else 0, labels),
            ]
        )
        if state_available:
            running = state.get("state") == "running"
            lines.extend(
                [
                    sample("nutsnews_docker_container_running", bool_value(running), labels),
                    sample(
                        "nutsnews_docker_container_healthy",
                        bool_value(running and state.get("health") in {"healthy", "none"}),
                        labels,
                    ),
                    sample("nutsnews_docker_container_restart_count", number(state.get("restart_count"), -1), labels),
                ]
            )
        try:
            result = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", container])
            row = json.loads(result.stdout.splitlines()[-1]) if result.returncode == 0 and result.stdout.strip() else {}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            row = {}
        available = isinstance(row, dict) and bool(row)
        memory_used, memory_limit = pair_values(row.get("MemUsage") if available else None)
        network_receive, network_transmit = pair_values(row.get("NetIO") if available else None)
        block_read, block_write = pair_values(row.get("BlockIO") if available else None)
        lines.extend(
            [
                sample("nutsnews_docker_stats_available", 1 if available else 0, labels),
                sample("nutsnews_docker_container_cpu_percent", percent(row.get("CPUPerc") if available else None), labels),
                sample("nutsnews_docker_container_memory_used_bytes", memory_used, labels),
                sample("nutsnews_docker_container_memory_limit_bytes", memory_limit, labels),
                sample("nutsnews_docker_container_memory_used_percent", percent(row.get("MemPerc") if available else None), labels),
                sample("nutsnews_docker_container_network_receive_bytes", network_receive, labels),
                sample("nutsnews_docker_container_network_transmit_bytes", network_transmit, labels),
                sample("nutsnews_docker_container_block_read_bytes", block_read, labels),
                sample("nutsnews_docker_container_block_write_bytes", block_write, labels),
                sample("nutsnews_docker_container_pids", number(row.get("PIDs") if available else None, -1), labels),
            ]
        )
    return lines


def collect_alloy_readiness() -> list[str]:
    ready = 0
    probe_success = 0
    if ALLOY_READY_URL:
        try:
            request = urllib.request.Request(ALLOY_READY_URL, method="GET")
            with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
                probe_success = 1
                ready = 1 if response.status == 200 else 0
        except (OSError, urllib.error.URLError, ValueError):
            pass
    return [
        sample("nutsnews_alloy_readiness_probe_success", probe_success),
        sample("nutsnews_alloy_ready", ready),
    ]


def collect_tls_expiry() -> list[str]:
    labels = {"service": "caddy"}
    available = 0
    expiry = -1.0
    if CADDY_TLS_HOST and SAFE_RUNTIME_NAME_RE.fullmatch(CADDY_TLS_HOST):
        try:
            context = ssl.create_default_context()
            with socket.create_connection((CADDY_TLS_HOST, 443), timeout=PROBE_TIMEOUT_SECONDS) as connection:
                with context.wrap_socket(connection, server_hostname=CADDY_TLS_HOST) as tls_connection:
                    certificate = tls_connection.getpeercert()
            expiry = float(ssl.cert_time_to_seconds(str(certificate["notAfter"])))
            available = 1
        except (OSError, ssl.SSLError, KeyError, ValueError):
            pass
    lines = [sample("nutsnews_caddy_tls_certificate_probe_success", available, labels)]
    if available:
        lines.extend(
            [
                sample("nutsnews_caddy_tls_certificate_expiry_timestamp_seconds", expiry, labels),
                sample("nutsnews_caddy_tls_certificate_expiry_seconds", expiry - time.time(), labels),
            ]
        )
    return lines


def production_ownership_unavailable_samples() -> list[str]:
    return [
        sample("nutsnews_production_ownership_available", 0),
        sample("nutsnews_production_ownership_last_success_timestamp_seconds", -1),
    ]


def production_ownership_samples() -> list[str]:
    """Observe routed production identity; never infer it from desired Ansible defaults."""
    parsed = urllib.parse.urlsplit(PRODUCTION_READINESS_URL)
    if (
        PRODUCTION_READINESS_URL != CANONICAL_PRODUCTION_READINESS_URL
        or parsed.scheme != "https"
        or parsed.hostname != "www.nutsnews.com"
        or parsed.port not in (None, 443)
        or parsed.path != "/readyz"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return production_ownership_unavailable_samples()

    try:
        infra_revision = DEPLOYED_INFRA_COMMIT_FILE.read_text(encoding="utf-8").strip()
        if not COMMIT_RE.fullmatch(infra_revision):
            return production_ownership_unavailable_samples()

        request = urllib.request.Request(
            PRODUCTION_READINESS_URL,
            headers={"Accept": "application/json", "User-Agent": "nutsnews-observability-textfile/1"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            if response.status != 200 or response.geturl() != CANONICAL_PRODUCTION_READINESS_URL:
                return production_ownership_unavailable_samples()
            raw_body = response.read(PRODUCTION_READINESS_MAX_BYTES + 1)
            if len(raw_body) > PRODUCTION_READINESS_MAX_BYTES:
                return production_ownership_unavailable_samples()
            cache_control = str(response.headers.get("Cache-Control") or "")
            if "no-store" not in {directive.strip().lower() for directive in cache_control.split(",")}:
                return production_ownership_unavailable_samples()
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                return production_ownership_unavailable_samples()

            web_target = payload.get("deploymentTarget")
            database_provider = payload.get("databaseProviderMode")
            web_revision = payload.get("sourceCommit")
            if (
                payload.get("ready") is not True
                or payload.get("service") != "nutsnews-web"
                or web_target not in PRODUCTION_WEB_TARGETS
                or database_provider not in PRODUCTION_DATABASE_PROVIDERS
                or not isinstance(web_revision, str)
                or not COMMIT_RE.fullmatch(web_revision)
                or response.headers.get("X-NutsNews-Deployment-Target") != web_target
                or response.headers.get("X-NutsNews-Database-Provider-Mode") != database_provider
                or response.headers.get("X-NutsNews-Source-Commit") != web_revision
            ):
                return production_ownership_unavailable_samples()
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
        ValueError,
    ):
        return production_ownership_unavailable_samples()

    return [
        sample(
            "nutsnews_production_ownership_info",
            1,
            {
                "web_target": web_target,
                "database_provider": database_provider,
                "web_revision": web_revision,
                "infra_revision": infra_revision,
            },
        ),
        sample("nutsnews_production_ownership_available", 1),
        sample("nutsnews_production_ownership_last_success_timestamp_seconds", time.time()),
    ]


def mapping_section(status: dict[str, Any], *keys: str) -> tuple[dict[str, Any], bool]:
    value = nested(status, *keys, default={})
    available = isinstance(value, dict) and bool(value)
    return (value if isinstance(value, dict) else {}), available


def collect() -> list[str]:
    status = read_json(STATUS_FILE)
    backups, backups_available = mapping_section(status, "backups")
    reporting, reporting_available = mapping_section(status, "email_reporting")
    synthetic_audit, synthetic_audit_available = mapping_section(
        status, "synthetic_inventory_audit"
    )
    app, app_available = mapping_section(status, "app")
    resources, resources_available = mapping_section(status, "resources")
    security, security_available = mapping_section(status, "security")
    alerts = nested(status, "alerts", "items", default=[])
    alerts_available = isinstance(alerts, list)
    services = status.get("services", [])
    services_available = isinstance(services, list)

    lines = [
        "# HELP nutsnews_ops_portal_status_available Whether the Ops Portal status JSON could be read.",
        "# TYPE nutsnews_ops_portal_status_available gauge",
        sample("nutsnews_ops_portal_status_available", 1 if status else 0),
        sample("nutsnews_alert_status_available", 1 if alerts_available and status else 0),
        sample("nutsnews_backup_status_available", 1 if backups_available else 0),
        sample("nutsnews_email_reporting_status_available", 1 if reporting_available else 0),
        sample(
            "nutsnews_synthetic_inventory_audit_status_available",
            1
            if synthetic_audit_available and bool_value(synthetic_audit.get("available"))
            else 0,
        ),
        sample("nutsnews_app_status_available", 1 if app_available else 0),
        sample("nutsnews_resource_status_available", 1 if resources_available else 0),
        sample("nutsnews_security_status_available", 1 if security_available else 0),
        sample("nutsnews_systemd_service_status_available", 1 if services_available and status else 0),
    ]

    lines.extend(timestamp_samples("nutsnews_ops_portal_status_generated", status.get("generated_at")))

    alert_counts: dict[str, int] = {}
    if alerts_available and status:
        for alert in alerts:
            if isinstance(alert, dict):
                level = label_value(alert.get("level", "unknown"))
                alert_counts[level] = alert_counts.get(level, 0) + 1
        for level in ("ok", "warning", "critical", "unknown"):
            lines.append(sample("nutsnews_alerts_total", alert_counts.get(level, 0), {"level": level}))

    if backups_available:
        lines.extend(
            [
                sample("nutsnews_backup_enabled", bool_value(backups.get("enabled"))),
                sample("nutsnews_backup_configured", bool_value(backups.get("configured"))),
                sample("nutsnews_backup_last_success", success_value(nested(backups, "last_backup", "status"))),
                sample("nutsnews_backup_last_prune_success", success_value(nested(backups, "last_prune", "status"))),
                sample("nutsnews_backup_last_verify_success", success_value(nested(backups, "last_check", "status"))),
                sample(
                    "nutsnews_backup_latest_snapshot_age_seconds",
                    number(backups.get("latest_snapshot_age_seconds"), -1),
                ),
                sample("nutsnews_backup_stale_after_seconds", number(backups.get("stale_after_seconds"), 108000)),
                sample(
                    "nutsnews_backup_missing_configuration_total",
                    len(backups.get("missing_configuration", []) or []),
                ),
                sample("nutsnews_backup_missing_paths_total", len(backups.get("missing_paths", []) or [])),
                sample("nutsnews_backup_timer_active", bool_value(backups.get("timer_active"))),
            ]
        )
        lines.extend(timestamp_samples("nutsnews_backup_status_updated", backups.get("updated_at")))
        lines.extend(
            timestamp_samples("nutsnews_backup_last_backup_finished", nested(backups, "last_backup", "finished_at"))
        )
        lines.extend(
            timestamp_samples("nutsnews_backup_last_verify_finished", nested(backups, "last_check", "finished_at"))
        )

    if reporting_available:
        report_conclusion = label_value(reporting.get("last_report_conclusion", "unknown"))
        if report_conclusion not in {
            "success",
            "critical",
            "delivery_failed",
            "disabled",
            "misconfigured",
            "dry_run",
        }:
            report_conclusion = "unknown"
        lines.extend(
            [
                sample("nutsnews_email_reporting_enabled", bool_value(reporting.get("enabled"))),
                sample("nutsnews_email_reporting_configured", bool_value(reporting.get("configured"))),
                sample("nutsnews_email_reporting_pending_alerts", number(reporting.get("pending_alerts"))),
                sample("nutsnews_email_reporting_suppressed_alerts", number(reporting.get("suppressed_alerts"))),
                sample("nutsnews_email_reporting_recipients", number(reporting.get("recipients_count"))),
                sample("nutsnews_email_reporting_last_report_exit_code", number(reporting.get("last_report_exit_code"), -1)),
            ]
        )
        for outcome in (
            "success",
            "critical",
            "delivery_failed",
            "disabled",
            "misconfigured",
            "dry_run",
            "unknown",
        ):
            lines.append(
                sample(
                    "nutsnews_email_reporting_last_report_conclusion",
                    1 if report_conclusion == outcome else 0,
                    {"outcome": outcome},
                )
            )
        lines.extend(timestamp_samples("nutsnews_email_reporting_status_updated", reporting.get("updated_at")))
        lines.extend(timestamp_samples("nutsnews_email_reporting_last_report_run", reporting.get("last_report_run_at")))
        lines.extend(
            timestamp_samples("nutsnews_email_reporting_last_report_success", reporting.get("last_report_success_at"))
        )
        lines.extend(
            timestamp_samples(
                "nutsnews_email_reporting_last_report_delivery_success",
                reporting.get("last_report_delivery_success_at"),
            )
        )

    if synthetic_audit_available:
        audit_conclusion = label_value(
            synthetic_audit.get("latest_conclusion", "unknown")
        )
        audit_outcomes = (
            "success",
            "failure",
            "cancelled",
            "timed_out",
            "action_required",
            "neutral",
            "skipped",
            "stale",
            "unknown",
        )
        if audit_conclusion not in audit_outcomes:
            audit_conclusion = "unknown"
        for outcome in audit_outcomes:
            lines.append(
                sample(
                    "nutsnews_synthetic_inventory_audit_conclusion",
                    1 if audit_conclusion == outcome else 0,
                    {"outcome": outcome},
                )
            )
        lines.append(
            sample(
                "nutsnews_synthetic_inventory_audit_expected_interval_seconds",
                number(synthetic_audit.get("expected_interval_seconds"), 86400),
            )
        )
        lines.extend(
            timestamp_samples(
                "nutsnews_synthetic_inventory_audit_last_run",
                synthetic_audit.get("last_run_at"),
            )
        )
        lines.extend(
            timestamp_samples(
                "nutsnews_synthetic_inventory_audit_last_success",
                synthetic_audit.get("last_success_at"),
            )
        )

    if app_available:
        deploy_status, _ = mapping_section(app, "deploy_status")
        routes, _ = mapping_section(app, "routes")
        staged_route, _ = mapping_section(routes, "staged")
        public_route, _ = mapping_section(routes, "public")
        lines.extend(
            [
                sample("nutsnews_app_enabled", bool_value(app.get("enabled"))),
                sample("nutsnews_app_staged_route_enabled", bool_value(app.get("staged_route_enabled"))),
                sample("nutsnews_app_public_route_enabled", bool_value(app.get("public_route_enabled"))),
                sample("nutsnews_app_container_running", bool_value(deploy_status.get("container_state") == "running")),
                sample(
                    "nutsnews_app_container_healthy",
                    bool_value(deploy_status.get("container_health") == "healthy"),
                ),
                sample(
                    "nutsnews_app_staged_route_healthy",
                    bool_value(nested(staged_route, "health", "ok", default=False)),
                ),
                sample(
                    "nutsnews_app_public_route_healthy",
                    bool_value(nested(public_route, "health", "ok", default=False)),
                ),
            ]
        )

    if services_available and status:
        for service in services:
            if not isinstance(service, dict):
                continue
            name = label_value(service.get("name"))
            lines.append(
                sample("nutsnews_systemd_service_active", bool_value(service.get("active") == "active"), {"unit": name})
            )
            lines.append(
                sample(
                    "nutsnews_systemd_service_enabled",
                    bool_value(service.get("enabled") == "enabled"),
                    {"unit": name},
                )
            )

    if resources_available:
        lines.extend(
            [
                sample("nutsnews_resource_cpu_percent", number(resources.get("cpu_percent"))),
                sample("nutsnews_resource_memory_used_percent", number(nested(resources, "memory", "used_percent"))),
                sample("nutsnews_resource_swap_available", bool_value(nested(resources, "swap", "available"))),
                sample("nutsnews_resource_swap_used_percent", number(nested(resources, "swap", "used_percent"), -1)),
                sample("nutsnews_kernel_oom_recent_total", number(nested(resources, "oom_evidence", "count"), -1)),
                sample("nutsnews_resource_root_disk_used_percent", number(nested(resources, "disk", "used_percent"))),
                sample(
                    "nutsnews_resource_root_inode_used_percent",
                    number(nested(resources, "disk", "inode_used_percent")),
                ),
                sample(
                    "nutsnews_resource_nutsnews_disk_used_percent",
                    number(nested(resources, "nutsnews_disk", "used_percent")),
                ),
            ]
        )

    if security_available:
        lines.extend(
            [
                sample(
                    "nutsnews_security_failed_logins_recent",
                    number(nested(security, "failed_logins", "recent_failed_login_lines")),
                ),
                sample(
                    "nutsnews_security_failed_logins_invalid_user",
                    number(nested(security, "failed_logins", "invalid_user_lines")),
                ),
            ]
        )

    lines.extend(collect_docker_stats(status))
    lines.extend(collect_alloy_readiness())
    lines.extend(collect_tls_expiry())
    lines.extend(production_ownership_samples())
    generated_at = time.time()
    lines.extend(
        [
            sample("nutsnews_observability_textfile_collector_success", 1),
            sample("nutsnews_observability_textfile_last_success_timestamp_seconds", generated_at),
        ]
    )

    return lines


def collection_failure_samples() -> list[str]:
    lines = [
        "# HELP nutsnews_observability_textfile_collector_success Whether the latest collection completed.",
        "# TYPE nutsnews_observability_textfile_collector_success gauge",
        sample("nutsnews_observability_textfile_collector_success", 0),
        sample("nutsnews_observability_textfile_last_success_timestamp_seconds", -1),
        sample("nutsnews_ops_portal_status_available", 0),
        sample("nutsnews_alert_status_available", 0),
        sample("nutsnews_backup_status_available", 0),
        sample("nutsnews_email_reporting_status_available", 0),
        sample("nutsnews_synthetic_inventory_audit_status_available", 0),
        sample("nutsnews_app_status_available", 0),
        sample("nutsnews_resource_status_available", 0),
        sample("nutsnews_security_status_available", 0),
        sample("nutsnews_systemd_service_status_available", 0),
        sample("nutsnews_alloy_readiness_probe_success", 0),
        sample("nutsnews_alloy_ready", 0),
        sample("nutsnews_caddy_tls_certificate_probe_success", 0, {"service": "caddy"}),
    ]
    lines.extend(timestamp_samples("nutsnews_ops_portal_status_generated", None))
    lines.extend(timestamp_samples("nutsnews_synthetic_inventory_audit_last_run", None))
    lines.extend(timestamp_samples("nutsnews_synthetic_inventory_audit_last_success", None))
    for target in valid_docker_targets():
        labels = {"service": target["service"]}
        lines.extend(
            [
                sample("nutsnews_docker_container_expected_active", target["expected_active"], labels),
                sample("nutsnews_docker_container_state_available", 0, labels),
                sample("nutsnews_docker_stats_available", 0, labels),
            ]
        )
    lines.extend(production_ownership_unavailable_samples())
    return lines


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = collect()
    except Exception as exc:  # The next scrape must see explicit failure, never stale success.
        print(f"observability collection failed: {exc.__class__.__name__}", file=sys.stderr)
        lines = collection_failure_samples()
    data = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(OUTPUT_FILE.parent), delete=False) as handle:
        handle.write(data)
        tmp_name = handle.name
    Path(tmp_name).replace(OUTPUT_FILE)
    OUTPUT_FILE.chmod(0o644)


if __name__ == "__main__":
    main()
