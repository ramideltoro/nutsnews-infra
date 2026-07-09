#!/usr/bin/env python3
"""Send opt-in VPS alerts and health reports from the portal status feed."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


STATUS_FILE = Path(os.environ.get("NUTSNEWS_PORTAL_STATUS_FILE", "/opt/nutsnews/portal-assets/data/status.json"))
REPORTING_STATUS_FILE = Path(
    os.environ.get("NUTSNEWS_REPORTING_STATUS_FILE", "/opt/nutsnews/portal-assets/data/reporting-status.json")
)
ALERT_STATE_FILE = Path(os.environ.get("NUTSNEWS_ALERT_STATE_FILE", "/opt/nutsnews/ops/email-alert-state.json"))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: dict[str, Any], mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_file.replace(path)
    path.chmod(mode)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def email_config() -> dict[str, Any]:
    username = os.environ.get("NUTSNEWS_SMTP_USERNAME", "").strip()
    password = os.environ.get("NUTSNEWS_SMTP_PASSWORD", "")
    return {
        "enabled": env_bool("NUTSNEWS_EMAIL_ENABLED"),
        "host": os.environ.get("NUTSNEWS_SMTP_HOST", "").strip(),
        "port": env_int("NUTSNEWS_SMTP_PORT", 587),
        "username": username,
        "password": password,
        "starttls": env_bool("NUTSNEWS_SMTP_STARTTLS", True),
        "sender": os.environ.get("NUTSNEWS_EMAIL_FROM", "").strip(),
        "recipients": env_list("NUTSNEWS_EMAIL_TO"),
        "cooldown_seconds": env_int("NUTSNEWS_ALERT_COOLDOWN_SECONDS", 21600),
        "subject_prefix": os.environ.get("NUTSNEWS_REPORT_SUBJECT_PREFIX", "NutsNews VPS").strip()
        or "NutsNews VPS",
        "auth_complete": not username or bool(password),
    }


def config_status(config: dict[str, Any]) -> tuple[bool, str]:
    if not config["enabled"]:
        return False, "disabled"
    missing = []
    for field in ("host", "sender"):
        if not config[field]:
            missing.append(field)
    if not config["recipients"]:
        missing.append("recipients")
    if not config["auth_complete"]:
        missing.append("smtp_password")
    if missing:
        return False, "misconfigured: missing " + ", ".join(missing)
    return True, "configured"


def public_status_update(
    *,
    config: dict[str, Any],
    configured: bool,
    status: str,
    mode: str,
    dry_run: bool,
    pending_alerts: int = 0,
    suppressed_alerts: int = 0,
    error: str = "",
    sent: bool = False,
) -> dict[str, Any]:
    lock_file = REPORTING_STATUS_FILE.with_name(f"{REPORTING_STATUS_FILE.name}.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_json(REPORTING_STATUS_FILE, {})
        if not isinstance(previous, dict):
            previous = {}

        data = {
            "schema_version": 1,
            "updated_at": utc_now(),
            "enabled": config["enabled"],
            "configured": configured,
            "status": status,
            "mode": mode,
            "dry_run": dry_run,
            "cooldown_seconds": config["cooldown_seconds"],
            "pending_alerts": pending_alerts,
            "suppressed_alerts": suppressed_alerts,
            "recipients_count": len(config["recipients"]),
            "smtp_host_configured": bool(config["host"]),
            "email_config_source": "Root-only environment file managed by Ansible from protected GitHub Environment secrets.",
            "last_error": error,
            "last_alert_check_at": previous.get("last_alert_check_at", "unknown"),
            "last_alert_sent_at": previous.get("last_alert_sent_at", "never"),
            "last_report_run_at": previous.get("last_report_run_at", "never"),
            "last_report_success_at": previous.get("last_report_success_at", "never"),
            "last_report_sent_at": previous.get("last_report_sent_at", "never"),
            "last_dry_run_at": previous.get("last_dry_run_at", "never"),
        }

        if mode == "alert":
            data["last_alert_check_at"] = data["updated_at"]
            if sent and not dry_run:
                data["last_alert_sent_at"] = data["updated_at"]
        if mode == "report" and sent and not dry_run:
            data["last_report_run_at"] = data["updated_at"]
            data["last_report_success_at"] = data["updated_at"]
            data["last_report_sent_at"] = data["updated_at"]
        elif mode == "report":
            data["last_report_run_at"] = data["updated_at"]
        if dry_run:
            data["last_dry_run_at"] = data["updated_at"]

        write_json(REPORTING_STATUS_FILE, data, mode=0o644)
        fcntl.flock(lock, fcntl.LOCK_UN)
        return data


def relevant_alerts(status: dict[str, Any]) -> list[dict[str, str]]:
    alerts = status.get("alerts", {}).get("items", [])
    if not isinstance(alerts, list):
        return []
    selected = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        level = str(alert.get("level", "")).lower()
        message = str(alert.get("message", "")).strip()
        if level in {"warning", "critical"} and message:
            selected.append({"level": level, "message": message})
    return selected


def alert_fingerprint(alert: dict[str, str]) -> str:
    raw = f"{alert['level']}\0{alert['message']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = ", ".join(config["recipients"])
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(config["host"], config["port"], timeout=20) as smtp:
        if config["starttls"]:
            smtp.starttls()
        if config["username"]:
            smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def bytes_label(value: Any) -> str:
    number = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return f"{number:.1f} TiB"


def process_lines(processes: list[dict[str, Any]]) -> list[str]:
    lines = []
    for process in processes[:5]:
        name = process.get("name") or process.get("command") or "unknown"
        lines.append(
            "- "
            f"{name} pid={process.get('pid', 'unknown')} "
            f"user={process.get('user', 'unknown')} "
            f"cpu={process.get('cpu_percent', 0)}% "
            f"mem={bytes_label(process.get('memory_bytes', 0))}"
        )
    return lines or ["- No process data available."]


def backup_lines(backups: dict[str, Any]) -> list[str]:
    latest = backups.get("latest_snapshot") or {}
    last_backup = backups.get("last_backup") or {}
    last_prune = backups.get("last_prune") or {}
    last_check = backups.get("last_check") or {}
    verification = backups.get("latest_snapshot_verification") or {}
    if not isinstance(verification, dict):
        verification = {}

    return [
        f"- Enabled/configured: {backups.get('enabled', False)} / {backups.get('configured', False)}",
        f"- Latest status: {backups.get('latest_status', 'unknown')}",
        f"- Latest snapshot: {latest.get('short_id') or latest.get('id') or 'none'} at {latest.get('time', 'never')}",
        f"- Latest verification: {verification.get('status', backups.get('verification_status', 'unknown'))} ({verification.get('detail', 'no detail')})",
        f"- Last backup: {last_backup.get('status', 'unknown')} at {last_backup.get('finished_at', 'never')}",
        f"- Last prune: {last_prune.get('status', 'unknown')} at {last_prune.get('finished_at', 'never')}",
        f"- Last verify: {last_check.get('status', 'unknown')} at {last_check.get('finished_at', 'never')}",
        f"- Next verify: {backups.get('verify_next_run_at', 'unknown')}",
    ]


def free_tier_lines(free_tier: dict[str, Any]) -> list[str]:
    providers = free_tier.get("providers", [])
    if not isinstance(providers, list) or not providers:
        return ["- No free-tier usage providers are configured."]

    lines = []
    summary = free_tier.get("summary", {}) if isinstance(free_tier.get("summary"), dict) else {}
    if summary:
        lines.append(
            "- "
            f"Safe={summary.get('safe', 0)} "
            f"warning={summary.get('warning', 0)} "
            f"critical={summary.get('critical', 0)} "
            f"over_limit={summary.get('over_limit', 0)} "
            f"unknown={summary.get('unknown_or_not_configured', 0)}"
        )

    for provider in providers[:12]:
        if not isinstance(provider, dict):
            continue
        lines.append(
            "- "
            f"{provider.get('platform', provider.get('key', 'unknown'))}: "
            f"{provider.get('risk_label') or provider.get('risk_status') or provider.get('health', 'unknown')}, "
            f"{provider.get('remaining', 'unknown')} remaining, "
            f"{provider.get('percent_used_display', 'unknown')} used, "
            f"source={provider.get('source_status') or provider.get('status', 'unknown')}, "
            f"checked={provider.get('last_checked_at', 'unknown')}"
        )
    if len(providers) > 12:
        lines.append(f"- {len(providers) - 12} additional provider(s) omitted from email.")
    return lines


def swap_summary_line(swap: dict[str, Any]) -> str:
    status = str(swap.get("status") or "unavailable")
    if status == "enabled":
        return (
            "- Swap: "
            f"{swap.get('used_percent', 'unknown')}% used "
            f"({bytes_label(swap.get('used_bytes'))} of {bytes_label(swap.get('total_bytes'))}), "
            f"state={swap.get('usage_state', 'unknown')}"
        )
    return f"- Swap: {status} ({swap.get('detail', 'no detail')})"


def oom_summary_line(oom_evidence: dict[str, Any]) -> str:
    status = str(oom_evidence.get("status") or "unavailable")
    count = oom_evidence.get("count")
    count_label = "unknown" if count is None else str(count)
    return f"- Kernel OOM evidence: {status}, {count_label} match(es) in {oom_evidence.get('window', 'unknown')}"


def health_report_body(status: dict[str, Any]) -> str:
    host = status.get("host", {})
    resources = status.get("resources", {})
    memory = resources.get("memory", {})
    swap = resources.get("swap", {})
    disk = resources.get("disk", {})
    oom_evidence = resources.get("oom_evidence", {})
    alerts = relevant_alerts(status)
    processes = status.get("processes", {})
    disk_usage = status.get("disk_usage", {})
    backups = status.get("backups", {})
    free_tier = status.get("free_tier_usage", {})

    lines = [
        "NutsNews VPS health report",
        "",
        f"Generated: {status.get('generated_at', 'unknown')}",
        f"Host: {host.get('fqdn') or host.get('hostname') or 'unknown'}",
        f"OS: {host.get('os', 'unknown')}",
        f"Uptime seconds: {host.get('uptime_seconds', 'unknown')}",
        "",
        "Resource summary",
        f"- CPU sample: {resources.get('cpu_percent', 'unknown')}%",
        f"- Load average: {resources.get('load_average', {})}",
        f"- Memory: {memory.get('used_percent', 0)}% used ({bytes_label(memory.get('used_bytes', 0))})",
        swap_summary_line(swap),
        f"- Root disk: {disk.get('used_percent', 0)}% used ({bytes_label(disk.get('used_bytes', 0))})",
        oom_summary_line(oom_evidence),
        "",
        "Current warnings and critical alerts",
    ]
    lines.extend([f"- {alert['level']}: {alert['message']}" for alert in alerts] or ["- None."])
    lines.extend(
        [
            "",
            "Top memory processes",
            *process_lines(processes.get("top_memory", [])),
            "",
            "Top CPU processes",
            *process_lines(processes.get("top_cpu", [])),
            "",
            "Largest cached folder scan entries",
        ]
    )
    for folder in disk_usage.get("top_folders", [])[:5]:
        lines.append(f"- {folder.get('path', 'unknown')}: {bytes_label(folder.get('size_bytes', 0))}")
    if not disk_usage.get("top_folders"):
        lines.append("- No cached folder scan data available.")
    lines.extend(
        [
            "",
            "VPS backup summary",
            *backup_lines(backups),
            "",
            "Free-tier usage summary",
            *free_tier_lines(free_tier),
            "",
            "Reminder: this report is read-only. Fixes still go through PR, CI, merge, and protected apply.",
        ]
    )
    return "\n".join(lines) + "\n"


def alert_body(status: dict[str, Any], alerts: list[dict[str, str]]) -> str:
    host = status.get("host", {})
    lines = [
        "NutsNews VPS alert",
        "",
        f"Generated: {status.get('generated_at', 'unknown')}",
        f"Host: {host.get('fqdn') or host.get('hostname') or 'unknown'}",
        "",
        "Active warnings and critical alerts",
    ]
    lines.extend(f"- {alert['level']}: {alert['message']}" for alert in alerts)
    lines.extend(
        [
            "",
            "The duplicate-alert cooldown suppresses repeats. The portal shows current status at the local tunnel endpoint.",
        ]
    )
    return "\n".join(lines) + "\n"


def handle_alert(config: dict[str, Any], configured: bool, dry_run: bool) -> int:
    status = read_json(STATUS_FILE, {})
    alerts = relevant_alerts(status)
    if not configured:
        public_status_update(
            config=config,
            configured=False,
            status="disabled" if not config["enabled"] else "misconfigured",
            mode="alert",
            dry_run=dry_run,
            pending_alerts=len(alerts),
        )
        return 0

    state = read_json(ALERT_STATE_FILE, {"alerts": {}})
    if not isinstance(state, dict):
        state = {"alerts": {}}
    sent_alerts = state.setdefault("alerts", {})
    now = int(time.time())
    sendable = []
    suppressed = 0
    for alert in alerts:
        fingerprint = alert_fingerprint(alert)
        last_sent = int(sent_alerts.get(fingerprint, {}).get("last_sent_epoch", 0))
        if now - last_sent >= config["cooldown_seconds"]:
            sendable.append((fingerprint, alert))
        else:
            suppressed += 1

    if not alerts:
        public_status_update(
            config=config,
            configured=True,
            status="ok",
            mode="alert",
            dry_run=dry_run,
            pending_alerts=0,
        )
        return 0

    if not sendable:
        public_status_update(
            config=config,
            configured=True,
            status="suppressed by cooldown",
            mode="alert",
            dry_run=dry_run,
            pending_alerts=len(alerts),
            suppressed_alerts=suppressed,
        )
        return 0

    try:
        if not dry_run:
            subject = f"{config['subject_prefix']}: {len(sendable)} VPS alert(s)"
            send_email(config, subject, alert_body(status, [alert for _, alert in sendable]))
            for fingerprint, alert in sendable:
                sent_alerts[fingerprint] = {
                    "last_sent_epoch": now,
                    "last_sent_at": utc_now(),
                    "level": alert["level"],
                    "message": alert["message"],
                }
            write_json(ALERT_STATE_FILE, state, mode=0o600)
        public_status_update(
            config=config,
            configured=True,
            status="dry run" if dry_run else "sent",
            mode="alert",
            dry_run=dry_run,
            pending_alerts=len(alerts),
            suppressed_alerts=suppressed,
            sent=not dry_run,
        )
    except Exception as error:  # noqa: BLE001 - surface email transport errors to the status feed
        public_status_update(
            config=config,
            configured=True,
            status="send failed",
            mode="alert",
            dry_run=dry_run,
            pending_alerts=len(alerts),
            suppressed_alerts=suppressed,
            error=str(error),
        )
        return 1
    return 0


def handle_report(config: dict[str, Any], configured: bool, dry_run: bool) -> int:
    status = read_json(STATUS_FILE, {})
    if not configured:
        public_status_update(
            config=config,
            configured=False,
            status="disabled" if not config["enabled"] else "misconfigured",
            mode="report",
            dry_run=dry_run,
        )
        return 0

    try:
        if not dry_run:
            subject = f"{config['subject_prefix']}: daily VPS health report"
            send_email(config, subject, health_report_body(status))
        public_status_update(
            config=config,
            configured=True,
            status="dry run" if dry_run else "sent",
            mode="report",
            dry_run=dry_run,
            sent=not dry_run,
        )
    except Exception as error:  # noqa: BLE001 - surface email transport errors to the status feed
        public_status_update(
            config=config,
            configured=True,
            status="send failed",
            mode="report",
            dry_run=dry_run,
            error=str(error),
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["alert", "report"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = email_config()
    configured, status = config_status(config)
    if not configured and config["enabled"]:
        public_status_update(
            config=config,
            configured=False,
            status=status,
            mode=args.mode,
            dry_run=args.dry_run,
        )
        return 0

    if args.mode == "alert":
        return handle_alert(config, configured, args.dry_run)
    return handle_report(config, configured, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
