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
from datetime import datetime, timezone
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
    raw_paths = read_list(PATHS_FILE)
    selected_paths, missing_paths = expand_backup_paths(raw_paths)

    return {
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
        "backup_paths_file": str(PATHS_FILE),
        "exclude_file": str(EXCLUDES_FILE),
        "backup_paths": selected_paths,
        "missing_paths": missing_paths,
        "stale_after_hours": stale_hours,
        "stale_after_seconds": stale_hours * 3600,
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
        },
        "security_model": "restic encrypts snapshots locally before rclone transports ciphertext to OneDrive.",
        "raw_onedrive_backups": False,
    }


def load_status() -> dict[str, Any]:
    previous = read_json(STATUS_FILE, {})
    if not isinstance(previous, dict):
        previous = {}
    return {**previous, **base_status()}


def save_status(data: dict[str, Any]) -> None:
    write_json(STATUS_FILE, data, mode=0o644)


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
        status["last_error"] = "\n".join(safe_lines(snapshots["stderr"] or snapshots["stdout"], limit=8))
        return False, status

    init = run(["restic", "init"], env=env, timeout=300)
    append_log("restic init", init)
    status["last_repository_init_at"] = utc_now()
    status["repository_initialized"] = init["ok"]
    if not init["ok"]:
        status["last_error"] = "\n".join(safe_lines(init["stderr"] or init["stdout"], limit=8))
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
            "paths": snapshot.get("paths", []),
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
    status["last_error"] = ""
    if mode == "backup":
        status["last_backup"] = {"status": "disabled", "finished_at": utc_now()}
    else:
        status["last_check"] = {"status": "disabled", "finished_at": utc_now()}
    save_status(status)
    return 0


def unconfigured_status(mode: str) -> int:
    status = load_status()
    status["status"] = "misconfigured"
    status["last_error"] = "Missing required backup configuration: " + ", ".join(status["missing_configuration"])
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
    if not status["backup_paths"]:
        status["last_backup"] = {
            "status": "failed",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "error": "No configured backup paths currently exist on the VPS.",
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
    runtime_paths.write_text("\n".join(status["backup_paths"]) + "\n", encoding="utf-8")
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
        "error": "" if backup["ok"] else "\n".join(safe_lines(backup["stderr"] or backup["stdout"], limit=10)),
    }
    status = latest_snapshot_status(env, status)

    if not backup["ok"]:
        status["status"] = "failed"
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
        "error": "" if prune["ok"] else "\n".join(safe_lines(prune["stderr"] or prune["stdout"], limit=10)),
    }
    status["status"] = "success" if prune["ok"] else "degraded"
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
        status["status"] = "failed"
        status["last_check"].update(
            {"status": "failed", "finished_at": utc_now(), "error": "No restic snapshots are available to verify."}
        )
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
                "error": "\n".join(safe_lines(listing["stderr"] or listing["stdout"], limit=10)),
            }
        )
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
        "error": "" if check["ok"] else "\n".join(safe_lines(check["stderr"] or check["stdout"], limit=10)),
    }
    status["status"] = "success" if check["ok"] else "failed"
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
            status["last_error"] = "Another restic backup operation is already running."
            save_status(status)
            return 1

        if args.mode == "backup":
            return handle_backup()
        return handle_verify_latest()


if __name__ == "__main__":
    raise SystemExit(main())
