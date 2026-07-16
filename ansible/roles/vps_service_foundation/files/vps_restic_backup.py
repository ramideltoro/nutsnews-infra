#!/usr/bin/env python3
"""Run and verify encrypted NutsNews VPS restic backups."""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|authorization|credential|api[_-]?key)=\S+"),
    re.compile(r"(?i)\b(password|passwd|token|secret|authorization|credential|api[_-]?key)\b\s*[:]\s*[^,\s]+"),
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]+"),
]

STATUS_FILE = Path(os.environ.get("NUTSNEWS_BACKUP_STATUS_FILE", "/opt/nutsnews/portal-assets/data/backup-status.json"))
STATE_DIR = Path(os.environ.get("NUTSNEWS_BACKUP_STATE_DIR", "/opt/nutsnews/ops/backups"))
LOG_FILE = Path(os.environ.get("NUTSNEWS_BACKUP_LOG_FILE", "/opt/nutsnews/logs/backups/restic-backup.log"))
PATHS_FILE = Path(os.environ.get("NUTSNEWS_BACKUP_PATHS_FILE", "/etc/nutsnews/vps-backup-paths.txt"))
EXCLUDES_FILE = Path(os.environ.get("NUTSNEWS_BACKUP_EXCLUDES_FILE", "/etc/nutsnews/vps-backup-excludes.txt"))
LOCK_FILE = STATE_DIR / "restic-backup.lock"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def env_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def redact(text: str) -> str:
    value = text
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value


def safe_lines(text: str, limit: int = 40) -> list[str]:
    lines = [redact(line.rstrip()) for line in text.splitlines()]
    return lines[-limit:]


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


def append_log(title: str, result: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"[{utc_now()}] {title}",
        f"returncode={result.get('returncode', 'unknown')} duration_seconds={result.get('duration_seconds', 'unknown')}",
    ]
    output = "\n".join(safe_lines(result.get("stdout", "") + result.get("stderr", ""), limit=160))
    if output:
        lines.append(output)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")
    LOG_FILE.chmod(0o640)


def read_list(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def expand_backup_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    missing: list[str] = []

    for item in paths:
        matches = sorted(glob.glob(item)) if any(char in item for char in "*?[") else []
        if matches:
            selected.extend(matches)
            continue
        if Path(item).exists():
            selected.append(item)
        else:
            missing.append(item)

    deduped = sorted(dict.fromkeys(selected))
    return deduped, missing


def current_backup_path_status() -> tuple[list[str], dict[str, Any]]:
    raw_paths = read_list(PATHS_FILE)
    selected_paths, missing_paths = expand_backup_paths(raw_paths)
    return selected_paths, {
        "backup_path_count": len(selected_paths),
        "protected_path_count": len(selected_paths),
        "missing_path_count": len(missing_paths),
        "backup_paths_redacted": True,
        "backup_paths_source": "Root-only Ansible-managed path list.",
        "exclude_source": "Root-only Ansible-managed exclude list.",
    }


def restic_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GOMAXPROCS", "1")
    env.setdefault("RCLONE_TRANSFERS", "2")
    env.setdefault("RCLONE_CHECKERS", "4")
    return env


def run(argv: list[str], *, env: dict[str, str], timeout: int | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"{argv[0]} not found",
            "returncode": 127,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "command timed out",
            "returncode": 124,
            "duration_seconds": round(time.monotonic() - started, 3),
        }

    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def configured() -> tuple[bool, list[str]]:
    missing = []
    repository = env_text("RESTIC_REPOSITORY")
    password_file = Path(env_text("RESTIC_PASSWORD_FILE"))
    rclone_config = Path(env_text("RCLONE_CONFIG"))

    if not repository:
        missing.append("RESTIC_REPOSITORY")
    if not str(repository).startswith("rclone:"):
        missing.append("RESTIC_REPOSITORY must use the restic rclone backend")
    if not password_file.is_file() or password_file.stat().st_size <= 0:
        missing.append("RESTIC_PASSWORD_FILE")
    if not rclone_config.is_file() or rclone_config.stat().st_size <= 0:
        missing.append("RCLONE_CONFIG")

    return not missing, missing


def base_status() -> dict[str, Any]:
    enabled = env_bool("NUTSNEWS_BACKUP_ENABLED")
    is_configured, missing = configured() if enabled else (False, [])
    repository = env_text("RESTIC_REPOSITORY", "rclone:nutsnews-onedrive:nutsnews-backups/vps")
    stale_hours = env_int("NUTSNEWS_BACKUP_STALE_AFTER_HOURS", 30)
    verify_stale_hours = env_int("NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS", 192)
    _, path_status = current_backup_path_status()

    status = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "enabled": enabled,
        "configured": is_configured,
        "missing_configuration": missing,
        "repository": repository,
        "rclone_remote": env_text("NUTSNEWS_BACKUP_RCLONE_REMOTE", "nutsnews-onedrive"),
        "repository_path": "nutsnews-backups/vps",
        "transport": "rclone OneDrive remote dedicated to NutsNews backups",
        "encryption": "restic",
        "encrypted_before_transport": True,
        "status_file": str(STATUS_FILE),
        "stale_after_hours": stale_hours,
        "stale_after_seconds": stale_hours * 3600,
        "verify_stale_after_hours": verify_stale_hours,
        "verify_stale_after_seconds": verify_stale_hours * 3600,
        "retention": {
            "keep_daily": env_int("NUTSNEWS_BACKUP_KEEP_DAILY", 14),
            "keep_weekly": env_int("NUTSNEWS_BACKUP_KEEP_WEEKLY", 8),
            "keep_monthly": env_int("NUTSNEWS_BACKUP_KEEP_MONTHLY", 12),
            "keep_yearly": env_int("NUTSNEWS_BACKUP_KEEP_YEARLY", 2),
            "prune_after_backup": True,
        },
        "services": {
            "backup_service": env_text("NUTSNEWS_BACKUP_SERVICE", "nutsnews-restic-backup.service"),
            "backup_timer": env_text("NUTSNEWS_BACKUP_TIMER", "nutsnews-restic-backup.timer"),
            "verify_service": env_text("NUTSNEWS_BACKUP_VERIFY_SERVICE", "nutsnews-restic-verify.service"),
            "verify_timer": env_text("NUTSNEWS_BACKUP_VERIFY_TIMER", "nutsnews-restic-verify.timer"),
        },
        "security_model": "restic encrypts snapshots locally before rclone transports ciphertext to OneDrive.",
        "raw_onedrive_backups": False,
    }
    status.update(path_status)
    return status


def load_status() -> dict[str, Any]:
    previous = read_json(STATUS_FILE, {})
    if not isinstance(previous, dict):
        previous = {}
    return {**previous, **base_status()}


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if "." in raw:
        prefix, suffix = raw.split(".", 1)
        fraction = suffix
        tz = ""
        for marker in ("+", "-"):
            if marker in suffix:
                fraction, tz = suffix.split(marker, 1)
                tz = marker + tz
                break
        if len(fraction) > 6:
            raw = f"{prefix}.{fraction[:6]}{tz}"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: Any) -> int | None:
    parsed = parse_timestamp(value)
    if not parsed:
        return None
    return max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)


def deadline_at(value: Any, seconds: int) -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return "unknown"
    return (parsed + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def snapshot_id_candidates(snapshot: Any) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    candidates = []
    for key in ("id", "short_id"):
        value = str(snapshot.get(key, "")).strip()
        if value:
            candidates.append(value)
    return list(dict.fromkeys(candidates))


def snapshot_id_matches(checked_id: Any, snapshot: Any) -> bool:
    checked = str(checked_id or "").strip()
    if not checked:
        return False
    for candidate in snapshot_id_candidates(snapshot):
        if checked == candidate:
            return True
        if len(checked) >= 8 and candidate.startswith(checked):
            return True
        if len(candidate) >= 8 and checked.startswith(candidate):
            return True
    return False


def snapshot_time_matches(checked_time: Any, latest_time: Any) -> bool:
    checked = str(checked_time or "").strip()
    latest = str(latest_time or "").strip()
    if not checked or not latest:
        return False
    if checked == latest:
        return True
    checked_parsed = parse_timestamp(checked)
    latest_parsed = parse_timestamp(latest)
    if not checked_parsed or not latest_parsed:
        return False
    return int(checked_parsed.timestamp()) == int(latest_parsed.timestamp())


def checked_snapshot_is_older(last_check: dict[str, Any], latest_snapshot: dict[str, Any]) -> bool:
    checked_time = parse_timestamp(last_check.get("latest_snapshot_time"))
    latest_time = parse_timestamp(latest_snapshot.get("time"))
    if not checked_time or not latest_time:
        return False
    return checked_time < latest_time


def verification_status(data: dict[str, Any]) -> dict[str, Any]:
    latest_snapshot = data.get("latest_snapshot")
    latest = latest_snapshot if isinstance(latest_snapshot, dict) else {}
    last_check_value = data.get("last_check")
    last_check = last_check_value if isinstance(last_check_value, dict) else {}
    try:
        threshold_seconds = int(data.get("verify_stale_after_seconds", 0))
    except (TypeError, ValueError):
        threshold_seconds = 0
    if threshold_seconds <= 0:
        threshold_seconds = env_int("NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS", 192) * 3600
    finished_at = last_check.get("finished_at")
    finished_at_age = age_seconds(finished_at)
    latest_snapshot_age = age_seconds(latest.get("time")) if latest else None

    check_status = str(last_check.get("status", "never")).lower()
    latest_id = latest.get("short_id") or latest.get("id", "")
    checked_latest = bool(latest) and (
        snapshot_id_matches(last_check.get("latest_snapshot_id"), latest)
        or snapshot_time_matches(last_check.get("latest_snapshot_time"), latest.get("time"))
    )
    deadline_basis = finished_at if check_status == "success" else latest.get("time")
    overdue = (
        isinstance(finished_at_age, int) and finished_at_age > threshold_seconds
        if check_status == "success"
        else isinstance(latest_snapshot_age, int) and latest_snapshot_age > threshold_seconds
    )

    result = {
        "status": "unknown",
        "policy_status": "unknown",
        "latest_snapshot_verified": False,
        "checked_latest_snapshot": checked_latest,
        "checked_snapshot_is_older": checked_snapshot_is_older(last_check, latest) if latest else False,
        "stale": False,
        "overdue": False,
        "pending": False,
        "age_seconds": finished_at_age,
        "stale_after_seconds": threshold_seconds,
        "stale_after_hours": round(threshold_seconds / 3600, 2),
        "latest_snapshot_id": latest_id,
        "latest_snapshot_time": latest.get("time", ""),
        "checked_snapshot_id": last_check.get("latest_snapshot_id", ""),
        "checked_snapshot_time": last_check.get("latest_snapshot_time", ""),
        "last_checked_at": finished_at or "never",
        "deadline_at": deadline_at(deadline_basis, threshold_seconds),
        "deadline_basis": "last_successful_verification" if check_status == "success" else "latest_snapshot",
        "detail": "Backup verification status is unknown.",
    }

    if not data.get("enabled"):
        result.update({"status": "disabled", "policy_status": "disabled", "detail": "Backups are disabled."})
    elif not data.get("configured"):
        result.update(
            {
                "status": "misconfigured",
                "policy_status": "misconfigured",
                "detail": "Backups are enabled but restic/rclone configuration is incomplete.",
            }
        )
    elif check_status == "running":
        result.update(
            {"status": "running", "policy_status": "running", "detail": "Backup verification is currently running."}
        )
    elif check_status == "failed":
        result.update(
            {
                "status": "failed",
                "policy_status": "failed",
                "detail": last_check.get("error") or "The latest backup verification failed.",
            }
        )
    elif not latest:
        result.update(
            {
                "status": "latest_unverified",
                "policy_status": "pending",
                "pending": True,
                "detail": "No latest restic snapshot is available to verify yet.",
            }
        )
    elif overdue:
        result.update(
            {
                "status": "stale" if checked_latest else "latest_unverified",
                "policy_status": "overdue",
                "stale": True,
                "overdue": True,
                "detail": (
                    "Backup verification is overdue and the newest snapshot is still awaiting verification."
                    if not checked_latest
                    else "The latest snapshot was verified, but the verification is overdue."
                ),
            }
        )
    elif check_status != "success" or not checked_latest:
        result.update(
            {
                "status": "latest_unverified",
                "policy_status": "pending",
                "pending": True,
                "detail": "The newest daily snapshot is awaiting the scheduled weekly verification within policy.",
            }
        )
    else:
        result.update(
            {
                "status": "success",
                "policy_status": "current",
                "latest_snapshot_verified": True,
                "detail": "The latest restic snapshot has a recent successful verification.",
            }
        )

    return result


def sanitize_public_status(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("backup_paths", "missing_paths", "backup_paths_file", "exclude_file"):
        data.pop(key, None)
    latest_snapshot = data.get("latest_snapshot")
    if isinstance(latest_snapshot, dict):
        paths = latest_snapshot.pop("paths", None)
        if paths is not None and "path_count" not in latest_snapshot:
            latest_snapshot["path_count"] = len(paths) if isinstance(paths, list) else 0
    data["latest_snapshot_verification"] = verification_status(data)
    data["verification_status"] = data["latest_snapshot_verification"]["status"]
    data["latest_snapshot_verified"] = data["latest_snapshot_verification"]["latest_snapshot_verified"]
    return data


def save_status(data: dict[str, Any]) -> None:
    data = sanitize_public_status(data)
    write_json(STATUS_FILE, data, mode=0o644)


def error_text(result: dict[str, Any], limit: int = 10) -> str:
    return "\n".join(safe_lines(result.get("stderr", "") or result.get("stdout", ""), limit=limit))


def set_active_error(status: dict[str, Any], message: str, source: str) -> dict[str, Any]:
    now = utc_now()
    status["last_error"] = redact(str(message))
    status["last_error_at"] = now
    status["last_error_source"] = source
    return status


def resolve_active_error(status: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    message = str(status.get("last_error") or "").strip()
    if not message:
        status["last_error"] = ""
        status.pop("last_error_at", None)
        status.pop("last_error_source", None)
        return status

    resolved = status.get("resolved_errors")
    if not isinstance(resolved, list):
        resolved = []
    resolved.append(
        {
            "error": message,
            "occurred_at": status.get("last_error_at") or "unknown",
            "source": status.get("last_error_source") or "unknown",
            "resolved_at": utc_now(),
            "resolved_by": resolved_by,
        }
    )
    status["resolved_errors"] = resolved[-20:]
    status["last_error"] = ""
    status.pop("last_error_at", None)
    status.pop("last_error_source", None)
    return status


def repo_missing(result: dict[str, Any]) -> bool:
    output = (result.get("stdout", "") + result.get("stderr", "")).lower()
    markers = (
        "is there a repository",
        "config file does not exist",
        "repository does not exist",
    )
    wrong_password_markers = ("wrong password", "ciphertext verification failed", "unable to decrypt")
    return any(marker in output for marker in markers) and not any(marker in output for marker in wrong_password_markers)


def ensure_repository(env: dict[str, str], status: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    snapshots = run(["restic", "snapshots", "--json"], env=env, timeout=120)
    if snapshots["ok"]:
        status["repository_initialized"] = True
        return True, status

    if not repo_missing(snapshots):
        append_log("restic snapshots failed", snapshots)
        status["repository_initialized"] = False
        status = set_active_error(status, error_text(snapshots, limit=8), "restic snapshots")
        return False, status

    init = run(["restic", "init"], env=env, timeout=300)
    append_log("restic init", init)
    status["last_repository_init_at"] = utc_now()
    status["repository_initialized"] = init["ok"]
    if not init["ok"]:
        status = set_active_error(status, error_text(init, limit=8), "restic init")
    return init["ok"], status


def parse_backup_summary(output: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("message_type") == "summary":
            summary = item
    return summary


def parse_snapshots(output: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def latest_snapshot(env: dict[str, str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = run(["restic", "snapshots", "--json"], env=env, timeout=180)
    if not result["ok"]:
        return None, result

    snapshots = parse_snapshots(result["stdout"])
    if not snapshots:
        return None, result

    latest = sorted(snapshots, key=lambda item: item.get("time", ""))[-1]
    return latest, result


def snapshot_age_seconds(snapshot: dict[str, Any] | None) -> int | None:
    if not snapshot:
        return None
    raw_time = str(snapshot.get("time", ""))
    if not raw_time:
        return None
    try:
        if raw_time.endswith("Z"):
            raw_time = raw_time[:-1] + "+00:00"
        snapshot_time = datetime.fromisoformat(raw_time)
        if snapshot_time.tzinfo is None:
            snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(int((datetime.now(timezone.utc) - snapshot_time).total_seconds()), 0)


def latest_snapshot_status(env: dict[str, str], status: dict[str, Any]) -> dict[str, Any]:
    snapshot, result = latest_snapshot(env)
    if snapshot:
        status["latest_snapshot"] = {
            "id": snapshot.get("id", ""),
            "short_id": snapshot.get("short_id", ""),
            "time": snapshot.get("time", ""),
            "hostname": snapshot.get("hostname", ""),
            "path_count": len(snapshot.get("paths", [])) if isinstance(snapshot.get("paths"), list) else 0,
        }
        status["latest_snapshot_age_seconds"] = snapshot_age_seconds(snapshot)
        age = status.get("latest_snapshot_age_seconds")
        stale_after = status.get("stale_after_seconds", 0)
        status["latest_status"] = "fresh" if isinstance(age, int) and age <= stale_after else "stale"
    elif result["ok"]:
        status["latest_snapshot"] = None
        status["latest_snapshot_age_seconds"] = None
        status["latest_status"] = "none"
    else:
        status["latest_snapshot"] = None
        status["latest_snapshot_age_seconds"] = None
        status["latest_status"] = "unknown"
    return status


def disabled_status(mode: str) -> int:
    status = load_status()
    status["status"] = "disabled"
    status = resolve_active_error(status, f"{mode} disabled")
    if mode == "backup":
        status["last_backup"] = {"status": "disabled", "finished_at": utc_now()}
    else:
        status["last_check"] = {"status": "disabled", "finished_at": utc_now()}
    save_status(status)
    return 0


def unconfigured_status(mode: str) -> int:
    status = load_status()
    status["status"] = "misconfigured"
    status = set_active_error(
        status,
        "Missing required backup configuration: " + ", ".join(status["missing_configuration"]),
        f"{mode} configuration",
    )
    if mode == "backup":
        status["last_backup"] = {"status": "failed", "finished_at": utc_now(), "error": status["last_error"]}
    else:
        status["last_check"] = {"status": "failed", "finished_at": utc_now(), "error": status["last_error"]}
    save_status(status)
    return 1


def handle_backup() -> int:
    status = load_status()
    if not status["enabled"]:
        return disabled_status("backup")
    if not status["configured"]:
        return unconfigured_status("backup")
    selected_paths, path_status = current_backup_path_status()
    status.update(path_status)
    if not selected_paths:
        message = "No configured backup paths currently exist on the VPS."
        status = set_active_error(status, message, "backup path selection")
        status["last_backup"] = {
            "status": "failed",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "error": message,
        }
        save_status(status)
        return 1

    env = restic_env()
    status["status"] = "running"
    status["last_backup"] = {"status": "running", "started_at": utc_now()}
    save_status(status)

    ok, status = ensure_repository(env, status)
    if not ok:
        status["status"] = "failed"
        status["last_backup"].update({"status": "failed", "finished_at": utc_now(), "error": status.get("last_error", "")})
        save_status(status)
        return 1

    runtime_paths = STATE_DIR / "last-backup-paths.txt"
    runtime_paths.write_text("\n".join(selected_paths) + "\n", encoding="utf-8")
    runtime_paths.chmod(0o600)

    backup_args = [
        "restic",
        "backup",
        "--json",
        "--one-file-system",
        "--files-from",
        str(runtime_paths),
    ]
    if EXCLUDES_FILE.exists():
        backup_args.extend(["--exclude-file", str(EXCLUDES_FILE)])

    backup = run(backup_args, env=env)
    append_log("restic backup", backup)
    summary = parse_backup_summary(backup["stdout"] + backup["stderr"])
    finished_at = utc_now()

    status["last_backup"] = {
        "status": "success" if backup["ok"] else "failed",
        "started_at": status["last_backup"].get("started_at"),
        "finished_at": finished_at,
        "duration_seconds": backup["duration_seconds"],
        "snapshot_id": summary.get("snapshot_id", ""),
        "files_new": summary.get("files_new", 0),
        "files_changed": summary.get("files_changed", 0),
        "files_unmodified": summary.get("files_unmodified", 0),
        "dirs_new": summary.get("dirs_new", 0),
        "data_added_bytes": summary.get("data_added", 0),
        "total_bytes_processed": summary.get("total_bytes_processed", 0),
        "error": "" if backup["ok"] else error_text(backup, limit=10),
    }
    status = latest_snapshot_status(env, status)

    if not backup["ok"]:
        status["status"] = "failed"
        status = set_active_error(status, status["last_backup"]["error"], "restic backup")
        save_status(status)
        return 1

    retention = status["retention"]
    prune_args = [
        "restic",
        "forget",
        "--json",
        "--prune",
        "--keep-daily",
        str(retention["keep_daily"]),
        "--keep-weekly",
        str(retention["keep_weekly"]),
        "--keep-monthly",
        str(retention["keep_monthly"]),
        "--keep-yearly",
        str(retention["keep_yearly"]),
    ]
    prune = run(prune_args, env=env)
    append_log("restic forget --prune", prune)
    status["last_prune"] = {
        "status": "success" if prune["ok"] else "failed",
        "started_at": finished_at,
        "finished_at": utc_now(),
        "duration_seconds": prune["duration_seconds"],
        "error": "" if prune["ok"] else error_text(prune, limit=10),
    }
    status["status"] = "success" if prune["ok"] else "degraded"
    if prune["ok"]:
        status = resolve_active_error(status, "successful backup and prune")
    else:
        status = set_active_error(status, status["last_prune"]["error"], "restic forget --prune")
    save_status(status)
    return 0 if prune["ok"] else 1


def count_ls_entries(output: str) -> int:
    count = 0
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("struct_type") in {"node", "snapshot"} or item.get("name"):
            count += 1
    return count


def handle_verify_latest() -> int:
    status = load_status()
    if not status["enabled"]:
        return disabled_status("verify")
    if not status["configured"]:
        return unconfigured_status("verify")

    env = restic_env()
    status["status"] = "verifying"
    status["last_check"] = {"status": "running", "started_at": utc_now()}
    save_status(status)

    ok, status = ensure_repository(env, status)
    if not ok:
        status["status"] = "failed"
        status["last_check"].update({"status": "failed", "finished_at": utc_now(), "error": status.get("last_error", "")})
        save_status(status)
        return 1

    status = latest_snapshot_status(env, status)
    if not status.get("latest_snapshot"):
        message = "No restic snapshots are available to verify."
        status["status"] = "failed"
        status["last_check"].update(
            {"status": "failed", "finished_at": utc_now(), "error": message}
        )
        status = set_active_error(status, message, "latest snapshot lookup")
        save_status(status)
        return 1

    listing = run(["restic", "ls", "--json", "latest"], env=env, timeout=600)
    append_log("restic ls latest", listing)
    if not listing["ok"]:
        status["status"] = "failed"
        status["last_check"].update(
            {
                "status": "failed",
                "finished_at": utc_now(),
                "duration_seconds": listing["duration_seconds"],
                "error": error_text(listing, limit=10),
            }
        )
        status = set_active_error(status, status["last_check"]["error"], "restic ls latest")
        save_status(status)
        return 1

    subset = env_text("NUTSNEWS_BACKUP_CHECK_READ_DATA_SUBSET", "5%")
    check = run(["restic", "check", f"--read-data-subset={subset}"], env=env)
    append_log("restic check", check)
    status["last_check"] = {
        "status": "success" if check["ok"] else "failed",
        "started_at": status["last_check"].get("started_at"),
        "finished_at": utc_now(),
        "duration_seconds": check["duration_seconds"],
        "latest_snapshot_id": status["latest_snapshot"].get("short_id") or status["latest_snapshot"].get("id", ""),
        "latest_snapshot_time": status["latest_snapshot"].get("time", ""),
        "listed_entries": count_ls_entries(listing["stdout"]),
        "read_data_subset": subset,
        "error": "" if check["ok"] else error_text(check, limit=10),
    }
    status["status"] = "success" if check["ok"] else "failed"
    if check["ok"]:
        status = resolve_active_error(status, "successful latest snapshot verification")
    else:
        status = set_active_error(status, status["last_check"]["error"], "restic check")
    save_status(status)
    return 0 if check["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["backup", "verify-latest"], required=True)
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            status = load_status()
            status["status"] = "busy"
            status = set_active_error(status, "Another restic backup operation is already running.", "backup lock")
            save_status(status)
            return 1

        if args.mode == "backup":
            return handle_backup()
        return handle_verify_latest()


if __name__ == "__main__":
    raise SystemExit(main())
