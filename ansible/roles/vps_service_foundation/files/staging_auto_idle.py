#!/usr/bin/env python3
"""Idle expired NutsNews staging deployments without touching production."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


APP_MARKER_FILE = Path(os.environ.get("NUTSNEWS_APP_APPLY_MARKER_FILE", "/opt/nutsnews/ops/last-app-apply.json"))
STAGING_MARKER_FILE = Path(
    os.environ.get("NUTSNEWS_STAGING_APPLY_MARKER_FILE", "/opt/nutsnews/ops/apps/staging/last-apply.json")
)
STATUS_FILE = Path(
    os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_STATUS_FILE", "/opt/nutsnews/portal-assets/data/staging-idle-status.json")
)
LOG_FILE = Path(os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_LOG_FILE", "/opt/nutsnews/logs/staging-auto-idle/idle.jsonl"))
LOCK_DIR = Path(os.environ.get("NUTSNEWS_STAGING_MUTATION_LOCK", "/var/lock/nutsnews-staging-deploy.lock"))
ENABLED = os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
GRACE_SECONDS = int(os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_GRACE_SECONDS", "3600"))
REMOVE_CACHE_VOLUME = os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_REMOVE_CACHE_VOLUME", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
STAGING_PROJECT_NAME = os.environ.get("NUTSNEWS_STAGING_PROJECT_NAME", "nutsnews-staging")
STAGING_APP_DIR = Path(os.environ.get("NUTSNEWS_STAGING_APP_DIR", "/opt/nutsnews/apps/nutsnews-staging"))
STAGING_COMPOSE_FILE = Path(os.environ.get("NUTSNEWS_STAGING_COMPOSE_FILE", str(STAGING_APP_DIR / "compose.yml")))
STAGING_ENV_FILE = Path(os.environ.get("NUTSNEWS_STAGING_ENV_FILE", "/etc/nutsnews/nutsnews-staging-app.env"))
STAGING_CACHE_VOLUME = os.environ.get("NUTSNEWS_STAGING_CACHE_VOLUME", "nutsnews-app-staging-cache")
STAGING_ACCESS_PROJECT = os.environ.get("NUTSNEWS_STAGING_ACCESS_PROJECT", "nutsnews-staging-access")
STAGING_ACCESS_DIR = Path(os.environ.get("NUTSNEWS_STAGING_ACCESS_DIR", "/opt/nutsnews/staging-access"))
STAGING_ACCESS_COMPOSE_FILE = Path(
    os.environ.get("NUTSNEWS_STAGING_ACCESS_COMPOSE_FILE", str(STAGING_ACCESS_DIR / "compose.yml"))
)
STAGING_ACCESS_ENV_FILE = Path(
    os.environ.get("NUTSNEWS_STAGING_ACCESS_ENV_FILE", "/etc/nutsnews/nutsnews-staging-access.env")
)
STAGING_CONTAINERS = ("nutsnews-app-staging", "nutsnews-staging-access-verifier")


def utc_now() -> datetime:
    override = os.environ.get("NUTSNEWS_STAGING_AUTO_IDLE_NOW", "").strip()
    if override:
        parsed = parse_timestamp(override)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
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
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_log(payload: dict[str, Any]) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def command(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return {
        "args": args[:4],
        "returncode": result.returncode,
        "stdout": result.stdout[-400:],
        "stderr": result.stderr[-400:],
        "ok": result.returncode == 0,
    }


def container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def compose_down(project: str, project_dir: Path, env_file: Path, compose_file: Path) -> dict[str, Any]:
    if not compose_file.exists():
        return {"ok": True, "skipped": True, "reason": "compose_file_missing", "project": project}
    if not env_file.exists():
        return {"ok": False, "skipped": True, "reason": "env_file_missing", "project": project}
    result = command(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "--project-directory",
            str(project_dir),
            "--env-file",
            str(env_file),
            "-f",
            str(compose_file),
            "down",
            "--remove-orphans",
            "--timeout",
            "20",
        ]
    )
    result["project"] = project
    return result


def acquire_lock() -> bool:
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        return False
    return True


def release_lock(acquired: bool) -> None:
    if acquired:
        try:
            LOCK_DIR.rmdir()
        except OSError:
            pass


def base_status(now: datetime, app_marker: dict[str, Any], staging_marker: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "checked_at": iso(now),
        "enabled": ENABLED,
        "status": "unknown",
        "action": "none",
        "reason": "",
        "qualification_expires_at": str(app_marker.get("qualification_expires_at", "")).strip(),
        "grace_seconds": GRACE_SECONDS,
        "idle_after": "",
        "staging_deployment_id": str(app_marker.get("staging_deployment_id", "")).strip(),
        "staging_marker_deployment_id": str(
            staging_marker.get("deployment_id") or staging_marker.get("staging_deployment_id") or ""
        ).strip(),
        "production_touched": False,
        "managed_projects": [STAGING_PROJECT_NAME, STAGING_ACCESS_PROJECT],
        "managed_containers": list(STAGING_CONTAINERS),
        "cache_volume": STAGING_CACHE_VOLUME,
        "remove_cache_volume": REMOVE_CACHE_VOLUME,
    }


def idle_staging(status: dict[str, Any]) -> dict[str, Any]:
    running_before = {name: container_running(name) for name in STAGING_CONTAINERS}
    status["running_before"] = running_before
    if not any(running_before.values()):
        status.update({"status": "idled", "action": "already_idled", "reason": "no_staging_containers_running"})
        return status

    acquired = acquire_lock()
    if not acquired:
        status.update({"status": "blocked", "action": "skipped", "reason": "staging_mutation_lock_held"})
        return status

    try:
        actions = [
            compose_down(STAGING_ACCESS_PROJECT, STAGING_ACCESS_DIR, STAGING_ACCESS_ENV_FILE, STAGING_ACCESS_COMPOSE_FILE),
            compose_down(STAGING_PROJECT_NAME, STAGING_APP_DIR, STAGING_ENV_FILE, STAGING_COMPOSE_FILE),
        ]
        if REMOVE_CACHE_VOLUME:
            volume = command(["docker", "volume", "rm", STAGING_CACHE_VOLUME])
            volume["project"] = "staging_cache_volume"
            if volume["returncode"] != 0 and "No such volume" in str(volume.get("stderr", "")):
                volume["ok"] = True
                volume["skipped"] = True
                volume["reason"] = "volume_missing"
            actions.append(volume)
        status["actions"] = actions
        if not all(item.get("ok") for item in actions):
            status.update({"status": "error", "action": "failed", "reason": "staging_idle_command_failed"})
            return status
        status["running_after"] = {name: container_running(name) for name in STAGING_CONTAINERS}
        status.update({"status": "idled", "action": "idled", "reason": "qualification_expired"})
        return status
    finally:
        release_lock(acquired)


def evaluate() -> dict[str, Any]:
    now = utc_now()
    app_marker = read_json(APP_MARKER_FILE)
    staging_marker = read_json(STAGING_MARKER_FILE)
    status = base_status(now, app_marker, staging_marker)

    if not ENABLED:
        status.update({"status": "disabled", "reason": "auto_idle_disabled"})
        return status

    expires_at = parse_timestamp(status["qualification_expires_at"])
    if not status["qualification_expires_at"]:
        status.update({"status": "not_configured", "reason": "qualification_expiry_missing"})
        return status
    if not expires_at:
        status.update({"status": "error", "reason": "qualification_expiry_invalid"})
        return status
    idle_after = expires_at + timedelta(seconds=GRACE_SECONDS)
    status["idle_after"] = iso(idle_after)

    if not status["staging_deployment_id"]:
        status.update({"status": "not_configured", "reason": "staging_deployment_id_missing"})
        return status
    if (
        status["staging_marker_deployment_id"]
        and status["staging_marker_deployment_id"] != status["staging_deployment_id"]
    ):
        status.update({"status": "superseded", "reason": "staging_marker_deployment_id_mismatch"})
        return status
    if now < expires_at:
        status.update({"status": "current", "reason": "qualification_current"})
        return status
    if now < idle_after:
        status.update({"status": "expired_waiting_grace", "reason": "within_grace_period"})
        return status
    return idle_staging(status)


def main() -> int:
    status = evaluate()
    write_json(STATUS_FILE, status)
    append_log(status)
    if status.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
