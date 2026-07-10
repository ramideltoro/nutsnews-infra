#!/usr/bin/env python3
"""Write low-cardinality NutsNews observability metrics for Alloy textfile scraping."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FILE = Path(os.environ.get("NUTSNEWS_PORTAL_STATUS_FILE", "/opt/nutsnews/portal-assets/data/status.json"))
OUTPUT_FILE = Path(
    os.environ.get("NUTSNEWS_OBSERVABILITY_TEXTFILE", "/var/lib/nutsnews/alloy/textfile/nutsnews.prom")
)


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
        return []
    now = time.time()
    return [
        sample(f"{prefix}_timestamp_seconds", parsed),
        sample(f"{prefix}_age_seconds", max(now - parsed, 0)),
    ]


def collect() -> list[str]:
    status = read_json(STATUS_FILE)
    backups = nested(status, "backups", default={}) or {}
    reporting = nested(status, "email_reporting", default={}) or {}
    app = nested(status, "app", default={}) or {}
    docker = nested(status, "docker", default={}) or {}
    resources = nested(status, "resources", default={}) or {}

    lines = [
        "# HELP nutsnews_ops_portal_status_available Whether the Ops Portal status JSON could be read.",
        "# TYPE nutsnews_ops_portal_status_available gauge",
        sample("nutsnews_ops_portal_status_available", 1 if status else 0),
    ]

    lines.extend(timestamp_samples("nutsnews_ops_portal_status_generated", status.get("generated_at")))

    alert_counts: dict[str, int] = {}
    for alert in nested(status, "alerts", "items", default=[]) or []:
        if isinstance(alert, dict):
            level = label_value(alert.get("level", "unknown"))
            alert_counts[level] = alert_counts.get(level, 0) + 1
    for level in ("ok", "warning", "critical", "unknown"):
        lines.append(sample("nutsnews_alerts_total", alert_counts.get(level, 0), {"level": level}))

    lines.extend(
        [
            sample("nutsnews_backup_enabled", bool_value(backups.get("enabled"))),
            sample("nutsnews_backup_configured", bool_value(backups.get("configured"))),
            sample("nutsnews_backup_last_success", success_value(nested(backups, "last_backup", "status"))),
            sample("nutsnews_backup_last_prune_success", success_value(nested(backups, "last_prune", "status"))),
            sample("nutsnews_backup_last_verify_success", success_value(nested(backups, "last_check", "status"))),
            sample("nutsnews_backup_latest_snapshot_age_seconds", number(backups.get("latest_snapshot_age_seconds"), -1)),
            sample("nutsnews_backup_stale_after_seconds", number(backups.get("stale_after_seconds"), 108000)),
            sample("nutsnews_backup_missing_configuration_total", len(backups.get("missing_configuration", []) or [])),
            sample("nutsnews_backup_missing_paths_total", len(backups.get("missing_paths", []) or [])),
            sample("nutsnews_backup_timer_active", bool_value(backups.get("timer_active"))),
        ]
    )

    lines.extend(timestamp_samples("nutsnews_backup_status_updated", backups.get("updated_at")))
    lines.extend(timestamp_samples("nutsnews_backup_last_backup_finished", nested(backups, "last_backup", "finished_at")))
    lines.extend(timestamp_samples("nutsnews_backup_last_verify_finished", nested(backups, "last_check", "finished_at")))

    lines.extend(
        [
            sample("nutsnews_email_reporting_enabled", bool_value(reporting.get("enabled"))),
            sample("nutsnews_email_reporting_configured", bool_value(reporting.get("configured"))),
            sample("nutsnews_email_reporting_pending_alerts", number(reporting.get("pending_alerts"))),
            sample("nutsnews_email_reporting_suppressed_alerts", number(reporting.get("suppressed_alerts"))),
            sample("nutsnews_email_reporting_recipients", number(reporting.get("recipients_count"))),
        ]
    )
    lines.extend(timestamp_samples("nutsnews_email_reporting_status_updated", reporting.get("updated_at")))
    lines.extend(timestamp_samples("nutsnews_email_reporting_last_report_success", reporting.get("last_report_success_at")))

    deploy_status = nested(app, "deploy_status", default={}) or {}
    routing = nested(app, "routing", default={}) or {}
    lines.extend(
        [
            sample("nutsnews_app_enabled", bool_value(app.get("enabled"))),
            sample("nutsnews_app_staged_route_enabled", bool_value(app.get("staged_route_enabled"))),
            sample("nutsnews_app_public_route_enabled", bool_value(app.get("public_route_enabled"))),
            sample("nutsnews_app_container_running", bool_value(deploy_status.get("container_state") == "running")),
            sample("nutsnews_app_container_healthy", bool_value(deploy_status.get("container_health") in {"healthy", "none"})),
            sample("nutsnews_app_route_ready", bool_value(routing.get("health_status") == "ready")),
        ]
    )

    for service in status.get("services", []) or []:
        if not isinstance(service, dict):
            continue
        name = label_value(service.get("name"))
        lines.append(sample("nutsnews_systemd_service_active", bool_value(service.get("active") == "active"), {"unit": name}))
        lines.append(sample("nutsnews_systemd_service_enabled", bool_value(service.get("enabled") == "enabled"), {"unit": name}))

    for container in docker.get("containers", []) or []:
        if not isinstance(container, dict):
            continue
        labels = {
            "container": label_value(container.get("name")),
            "compose_project": label_value(container.get("compose_project")),
        }
        lines.append(sample("nutsnews_docker_container_running", bool_value(container.get("state") == "running"), labels))
        lines.append(sample("nutsnews_docker_container_healthy", bool_value(container.get("health") in {"healthy", "none"}), labels))
        lines.append(sample("nutsnews_docker_container_restart_count", number(container.get("restart_count")), labels))

    lines.extend(
        [
            sample("nutsnews_resource_cpu_percent", number(resources.get("cpu_percent"))),
            sample("nutsnews_resource_memory_used_percent", number(nested(resources, "memory", "used_percent"))),
            sample("nutsnews_resource_swap_available", bool_value(nested(resources, "swap", "available"))),
            sample("nutsnews_resource_swap_used_percent", number(nested(resources, "swap", "used_percent"), -1)),
            sample("nutsnews_kernel_oom_recent_total", number(nested(resources, "oom_evidence", "count"), -1)),
            sample("nutsnews_resource_root_disk_used_percent", number(nested(resources, "disk", "used_percent"))),
            sample("nutsnews_resource_root_inode_used_percent", number(nested(resources, "disk", "inode_used_percent"))),
            sample("nutsnews_resource_nutsnews_disk_used_percent", number(nested(resources, "nutsnews_disk", "used_percent"))),
            sample("nutsnews_security_failed_logins_recent", number(nested(status, "security", "failed_logins", "recent_failed_login_lines"))),
            sample("nutsnews_security_failed_logins_invalid_user", number(nested(status, "security", "failed_logins", "invalid_user_lines"))),
        ]
    )

    return lines


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(collect()) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(OUTPUT_FILE.parent), delete=False) as handle:
        handle.write(data)
        tmp_name = handle.name
    Path(tmp_name).replace(OUTPUT_FILE)
    OUTPUT_FILE.chmod(0o644)


if __name__ == "__main__":
    main()
