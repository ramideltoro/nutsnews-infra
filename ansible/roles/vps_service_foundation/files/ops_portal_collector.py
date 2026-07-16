#!/usr/bin/env python3
"""Collect read-only VPS status for the NutsNews Operations Portal."""

from __future__ import annotations

import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ops_free_tier_usage import collect_free_tier_usage, summarize_providers
except ImportError:
    collect_free_tier_usage = None
    summarize_providers = None


PRIVATE_KEY_LINE_PATTERN = ".*PRIVATE" + r"\s+" + "KEY.*"

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|authorization|credential|api[_-]?key)=\S+"),
    re.compile(
        r"(?i)\b(password|passwd|token|secret|authorization|credential|api[_-]?key)\b"
        r"\s*[:]\s*[^,\s]+"
    ),
    re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)" + PRIVATE_KEY_LINE_PATTERN),
]

ROOT_DIR = Path(os.environ.get("NUTSNEWS_ROOT_DIR", "/opt/nutsnews"))
OUTPUT_FILE = Path(os.environ.get("NUTSNEWS_PORTAL_OUTPUT", "/opt/nutsnews/portal-assets/data/status.json"))
BACKUPS_DIR = Path(os.environ.get("NUTSNEWS_BACKUPS_DIR", "/opt/nutsnews/backups"))
DEPLOYED_COMMIT_FILE = Path(
    os.environ.get("NUTSNEWS_DEPLOYED_COMMIT_FILE", "/opt/nutsnews/ops/deployed-infra-commit")
)
APPLY_STATUS_FILE = Path(os.environ.get("NUTSNEWS_APPLY_STATUS_FILE", "/opt/nutsnews/ops/last-apply.json"))
APP_APPLY_MARKER_FILE = Path(os.environ.get("NUTSNEWS_APP_APPLY_MARKER_FILE", "/opt/nutsnews/ops/last-app-apply.json"))
REPORTING_STATUS_FILE = Path(
    os.environ.get("NUTSNEWS_REPORTING_STATUS_FILE", "/opt/nutsnews/portal-assets/data/reporting-status.json")
)
BACKUP_STATUS_FILE = Path(
    os.environ.get("NUTSNEWS_BACKUP_STATUS_FILE", "/opt/nutsnews/portal-assets/data/backup-status.json")
)
APP_ENABLED = os.environ.get("NUTSNEWS_APP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_STAGED_ROUTE_ENABLED = os.environ.get("NUTSNEWS_APP_STAGED_ROUTE_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_PUBLIC_ROUTE_ENABLED = os.environ.get("NUTSNEWS_APP_PUBLIC_ROUTE_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_CONTAINER_NAME = os.environ.get("NUTSNEWS_APP_CONTAINER_NAME", "nutsnews-app").strip()
APP_CONTAINER_PORT = int(os.environ.get("NUTSNEWS_APP_CONTAINER_PORT", "3000") or 0)
APP_IMAGE = os.environ.get("NUTSNEWS_APP_IMAGE", "").strip()
APP_IMAGE_REPO = os.environ.get("NUTSNEWS_APP_IMAGE_REPO", "").strip()
APP_IMAGE_DIGEST = os.environ.get("NUTSNEWS_APP_IMAGE_DIGEST", "").strip()
APP_SOURCE_COMMIT = os.environ.get("NUTSNEWS_APP_SOURCE_COMMIT", "").strip()
APP_BUILD_ID = os.environ.get("NUTSNEWS_APP_BUILD_ID", "").strip()
APP_DEPLOYMENT_TARGET = os.environ.get("NUTSNEWS_APP_DEPLOYMENT_TARGET", "production-vps").strip()
APP_LAST_KNOWN_GOOD_DIGEST = os.environ.get("NUTSNEWS_APP_LAST_KNOWN_GOOD_DIGEST", "").strip()
APP_HEALTH_PATH = os.environ.get("NUTSNEWS_APP_HEALTH_PATH", "/healthz").strip() or "/healthz"
APP_ROUTE_PATH = os.environ.get("NUTSNEWS_APP_ROUTE_PATH", "/app-stage").strip() or "/app-stage"
APP_PUBLIC_DOMAIN = os.environ.get("NUTSNEWS_APP_PUBLIC_DOMAIN", "vps.nutsnews.com").strip() or "vps.nutsnews.com"
APP_ENV_FILE = Path(os.environ.get("NUTSNEWS_APP_ENV_FILE", "/etc/nutsnews/nutsnews-app.env")).resolve()
APP_SECRET_ENV_KEYS = [item.strip() for item in os.environ.get("NUTSNEWS_APP_SECRET_ENV_KEYS", "").split(",") if item.strip()]
APP_REQUIRED_SECRET_KEYS = [item.strip() for item in os.environ.get("NUTSNEWS_APP_REQUIRED_SECRET_KEYS", "").split(",") if item.strip()]
DISK_SCAN_CACHE_FILE = Path(
    os.environ.get("NUTSNEWS_DISK_SCAN_CACHE_FILE", "/opt/nutsnews/portal-assets/data/disk-usage-cache.json")
)
DISK_SCAN_CACHE_SECONDS = int(os.environ.get("NUTSNEWS_DISK_SCAN_CACHE_SECONDS", "3600"))
DISK_SCAN_ROOTS = [
    item.strip()
    for item in os.environ.get("NUTSNEWS_DISK_SCAN_ROOTS", "/opt/nutsnews,/var/log,/var/lib/docker,/home").split(",")
    if item.strip()
]
COLLECTOR_TIMER_CADENCE_SECONDS = int(os.environ.get("NUTSNEWS_COLLECTOR_TIMER_CADENCE_SECONDS", "60"))
SLOW_CACHE_FILE = Path(
    os.environ.get("NUTSNEWS_COLLECTOR_SLOW_CACHE_FILE", "/opt/nutsnews/portal-assets/data/collector-slow-cache.json")
)
SLOW_SECTION_TTLS = {
    "docker_inspect": int(os.environ.get("NUTSNEWS_COLLECTOR_DOCKER_INSPECT_CACHE_SECONDS", "300")),
    "docker_compose": int(os.environ.get("NUTSNEWS_COLLECTOR_DOCKER_COMPOSE_CACHE_SECONDS", "300")),
    "processes": int(os.environ.get("NUTSNEWS_COLLECTOR_PROCESSES_CACHE_SECONDS", "300")),
    "logs": int(os.environ.get("NUTSNEWS_COLLECTOR_LOGS_CACHE_SECONDS", "300")),
    "security": int(os.environ.get("NUTSNEWS_COLLECTOR_SECURITY_CACHE_SECONDS", "900")),
    "backups": int(os.environ.get("NUTSNEWS_COLLECTOR_BACKUPS_CACHE_SECONDS", "300")),
    "free_tier_local": int(os.environ.get("NUTSNEWS_COLLECTOR_FREE_TIER_LOCAL_CACHE_SECONDS", "900")),
    "oom_evidence": int(os.environ.get("NUTSNEWS_COLLECTOR_OOM_EVIDENCE_CACHE_SECONDS", "900")),
    "observability": int(os.environ.get("NUTSNEWS_COLLECTOR_OBSERVABILITY_CACHE_SECONDS", "300")),
}
SWAP_USAGE_CACHE_FILE = Path(
    os.environ.get("NUTSNEWS_SWAP_USAGE_CACHE_FILE", "/opt/nutsnews/portal-assets/data/swap-usage-cache.json")
)
OOM_EVIDENCE_WINDOW = os.environ.get("NUTSNEWS_OOM_EVIDENCE_WINDOW", "-7 days").strip() or "-7 days"
OOM_EVIDENCE_RE = re.compile(r"(?i)(out of memory|oom-killer|killed process)")
DOCS_BASE_URL = os.environ.get("NUTSNEWS_DOCS_BASE_URL", "https://github.com/ramideltoro/nutsnews-docs")
INFRA_REPO_URL = os.environ.get("NUTSNEWS_INFRA_REPO_URL", "https://github.com/ramideltoro/nutsnews-infra")
ALLOY_ENABLED = os.environ.get("NUTSNEWS_ALLOY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
ALLOY_COLLECT_DOCKER = os.environ.get("NUTSNEWS_ALLOY_COLLECT_DOCKER", "0").strip().lower() in {"1", "true", "yes", "on"}
ALLOY_COLLECT_DOCKER_LOGS = os.environ.get("NUTSNEWS_ALLOY_COLLECT_DOCKER_LOGS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALLOY_SERVICE = os.environ.get("NUTSNEWS_ALLOY_SERVICE", "alloy.service").strip() or "alloy.service"
ALLOY_READY_URL = os.environ.get("NUTSNEWS_ALLOY_READY_URL", "http://127.0.0.1:12345/-/ready").strip()
ALLOY_ERROR_WINDOW = os.environ.get("NUTSNEWS_ALLOY_ERROR_WINDOW", "-30 min").strip() or "-30 min"
ALLOY_TEXTFILE_DIR = Path(os.environ.get("NUTSNEWS_ALLOY_TEXTFILE_DIR", "/var/lib/nutsnews/alloy/textfile"))
ALLOY_CONTAINERD_PERMISSION_ERROR = "containerd.sock: connect: permission denied"
ALLOY_FILE_PERMISSION_ERROR = r"failed to tail the file: open .*: permission denied"
ALLOY_PERMISSION_ERROR_PATTERNS = [
    ALLOY_CONTAINERD_PERMISSION_ERROR,
    ALLOY_FILE_PERMISSION_ERROR,
]
ALLOY_FILE_PERMISSION_ERROR_RE = re.compile(ALLOY_FILE_PERMISSION_ERROR)
COLLECTOR_STARTED_MONOTONIC = time.monotonic()
CACHE_EVENTS: list[dict[str, Any]] = []


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def backup_verification_status(backups: dict[str, Any]) -> dict[str, Any]:
    latest_snapshot = backups.get("latest_snapshot")
    latest = latest_snapshot if isinstance(latest_snapshot, dict) else {}
    last_check_value = backups.get("last_check")
    last_check = last_check_value if isinstance(last_check_value, dict) else {}
    threshold_seconds = safe_int(backups.get("verify_stale_after_seconds"), 691200)
    finished_at = last_check.get("finished_at")
    finished_at_age = age_seconds(finished_at)
    latest_snapshot_age = age_seconds(latest.get("time")) if latest else None
    check_status = str(last_check.get("status", "never")).lower()
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
    latest_id = latest.get("short_id") or latest.get("id", "")
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

    if not backups.get("enabled"):
        result.update({"status": "disabled", "policy_status": "disabled", "detail": "Backups are disabled."})
    elif not backups.get("configured"):
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


def run(argv: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{argv[0]} not found", "returncode": 127}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "command timed out", "returncode": 124}

    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def redact_line(line: str) -> str:
    value = line
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value


def safe_lines(text: str, limit: int = 80) -> list[str]:
    lines = [redact_line(line.rstrip()) for line in text.splitlines()]
    return lines[-limit:]


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_public_json(path: Path, data: dict[str, Any], mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_file.replace(path)
    path.chmod(mode)


def section_ttl(name: str) -> int:
    return max(safe_int(SLOW_SECTION_TTLS.get(name), 300), COLLECTOR_TIMER_CADENCE_SECONDS)


def cache_metadata(
    section: str,
    state: str,
    ttl_seconds: int,
    collected_at: str,
    duration_ms: int,
    error: str = "",
) -> dict[str, Any]:
    age = age_seconds(collected_at)
    stale = age is None or age >= ttl_seconds
    return {
        "section": section,
        "state": state,
        "ttl_seconds": ttl_seconds,
        "collected_at": collected_at,
        "age_seconds": age,
        "stale": stale,
        "duration_ms": duration_ms,
        "error": redact_line(error),
    }


def attach_cache_metadata(value: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
    else:
        result = {"value": value}
    result["_collector_cache"] = metadata
    CACHE_EVENTS.append(metadata)
    return result


def value_without_cache_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        result = dict(value)
        result.pop("_collector_cache", None)
        return result
    return value


def cached_slow_section(section: str, ttl_seconds: int, producer: Any) -> dict[str, Any]:
    now_epoch = int(time.time())
    cache = read_json(SLOW_CACHE_FILE, {})
    sections = cache.get("sections", {}) if isinstance(cache, dict) else {}
    if not isinstance(sections, dict):
        sections = {}
    entry = sections.get(section, {})
    if not isinstance(entry, dict):
        entry = {}
    collected_at_epoch = safe_int(entry.get("collected_at_epoch"), 0)
    cached_value = entry.get("value")
    if collected_at_epoch and now_epoch - collected_at_epoch < ttl_seconds and isinstance(cached_value, dict):
        metadata = cache_metadata(
            section,
            "fresh_cache",
            ttl_seconds,
            str(entry.get("collected_at", "unknown")),
            safe_int(entry.get("duration_ms"), 0),
        )
        return attach_cache_metadata(cached_value, metadata)

    started = time.monotonic()
    try:
        produced = producer()
    except Exception as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        if isinstance(cached_value, dict):
            metadata = cache_metadata(
                section,
                "stale_cache",
                ttl_seconds,
                str(entry.get("collected_at", "unknown")),
                safe_int(entry.get("duration_ms"), duration_ms),
                str(error),
            )
            return attach_cache_metadata(cached_value, metadata)
        metadata = cache_metadata(section, "unavailable", ttl_seconds, "unknown", duration_ms, str(error))
        return attach_cache_metadata({"available": False, "error": "Collector section failed."}, metadata)

    duration_ms = int((time.monotonic() - started) * 1000)
    collected_at = utc_now()
    clean_value = value_without_cache_metadata(produced)
    if isinstance(clean_value, dict):
        sections[section] = {
            "collected_at": collected_at,
            "collected_at_epoch": now_epoch,
            "duration_ms": duration_ms,
            "ttl_seconds": ttl_seconds,
            "value": clean_value,
        }
        try:
            write_public_json(
                SLOW_CACHE_FILE,
                {"schema_version": 1, "updated_at": collected_at, "sections": sections},
            )
        except OSError:
            pass
    metadata = cache_metadata(section, "live", ttl_seconds, collected_at, duration_ms)
    return attach_cache_metadata(clean_value, metadata)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def app_staged_health_url() -> str:
    if not APP_ENABLED or not APP_STAGED_ROUTE_ENABLED:
        return ""
    route_path = APP_ROUTE_PATH.strip()
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    route_path = route_path.rstrip("/")
    health_path = APP_HEALTH_PATH.strip()
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    return f"http://127.0.0.1:8080{route_path}{health_path}"


def app_public_health_url() -> str:
    if not APP_ENABLED or not APP_PUBLIC_ROUTE_ENABLED:
        return ""
    health_path = APP_HEALTH_PATH if APP_HEALTH_PATH.startswith("/") else f"/{APP_HEALTH_PATH}"
    return f"https://{APP_PUBLIC_DOMAIN}{health_path}"


def read_env_keys(path: Path) -> list[str]:
    keys: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return keys

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("export "):
            stripped = stripped[7:]
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def username_for_uid(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def parse_os_release() -> str:
    values: dict[str, str] = {}
    for line in read_text(Path("/etc/os-release")).splitlines():
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        values[key] = raw_value.strip().strip('"')
    return values.get("PRETTY_NAME") or values.get("NAME") or platform.platform()


def uptime_seconds() -> int:
    try:
        return int(float(read_text(Path("/proc/uptime")).split()[0]))
    except (IndexError, ValueError):
        return 0


def boot_time() -> str:
    for line in read_text(Path("/proc/stat")).splitlines():
        if line.startswith("btime "):
            try:
                return datetime.fromtimestamp(int(line.split()[1]), timezone.utc).replace(microsecond=0).isoformat()
            except (IndexError, ValueError, OSError):
                return "unknown"
    return "unknown"


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in read_text(Path("/proc/meminfo")).splitlines():
        parts = line.replace(":", "").split()
        if len(parts) >= 2:
            try:
                values[parts[0]] = int(parts[1]) * 1024
            except ValueError:
                continue
    return values


def cpu_sample() -> tuple[int, int]:
    fields = read_text(Path("/proc/stat")).splitlines()[0].split()[1:]
    numbers = [int(field) for field in fields]
    idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
    total = sum(numbers)
    return idle, total


def cpu_percent() -> float | None:
    try:
        idle_a, total_a = cpu_sample()
        time.sleep(0.15)
        idle_b, total_b = cpu_sample()
    except (IndexError, ValueError, OSError):
        return None

    total_delta = total_b - total_a
    idle_delta = idle_b - idle_a
    if total_delta <= 0:
        return None
    return round((1 - (idle_delta / total_delta)) * 100, 1)


def percent(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((used / total) * 100, 1)


def percent_or_none(used: int | None, total: int | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round((used / total) * 100, 1)


def swap_thresholds() -> dict[str, float | int]:
    return {
        "non_trivial_bytes": safe_int(os.environ.get("NUTSNEWS_SWAP_NON_TRIVIAL_BYTES"), 64 * 1024 * 1024),
        "warning_percent": safe_float(os.environ.get("NUTSNEWS_SWAP_WARNING_PERCENT"), 25.0),
        "critical_percent": safe_float(os.environ.get("NUTSNEWS_SWAP_CRITICAL_PERCENT"), 50.0),
        "sustained_seconds": safe_int(os.environ.get("NUTSNEWS_SWAP_SUSTAINED_SECONDS"), 900),
    }


def swap_usage_history(used_bytes: int, total_bytes: int, thresholds: dict[str, float | int]) -> dict[str, Any]:
    now = int(time.time())
    sustained_seconds = max(safe_int(thresholds.get("sustained_seconds"), 900), 60)
    keep_seconds = max(sustained_seconds * 4, 3600)
    non_trivial_bytes = safe_int(thresholds.get("non_trivial_bytes"), 64 * 1024 * 1024)
    cache = read_json(SWAP_USAGE_CACHE_FILE, {})
    raw_samples = cache.get("samples", []) if isinstance(cache, dict) else []
    samples = []
    if isinstance(raw_samples, list):
        for sample in raw_samples:
            if not isinstance(sample, dict):
                continue
            epoch = safe_int(sample.get("epoch"), -1)
            if epoch >= now - keep_seconds:
                samples.append(
                    {
                        "epoch": epoch,
                        "sampled_at": sample.get("sampled_at", "unknown"),
                        "used_bytes": safe_int(sample.get("used_bytes"), 0),
                        "total_bytes": safe_int(sample.get("total_bytes"), 0),
                    }
                )

    samples.append(
        {
            "epoch": now,
            "sampled_at": utc_now(),
            "used_bytes": used_bytes,
            "total_bytes": total_bytes,
        }
    )
    samples = sorted(samples, key=lambda item: item["epoch"])[-120:]
    sustained_samples = [sample for sample in samples if sample["epoch"] >= now - sustained_seconds]
    oldest_sustained_age = None
    sustained_non_trivial = False
    if sustained_samples:
        oldest_sustained_age = now - sustained_samples[0]["epoch"]
        sustained_non_trivial = (
            oldest_sustained_age >= sustained_seconds
            and len(sustained_samples) >= 2
            and all(sample["used_bytes"] >= non_trivial_bytes for sample in sustained_samples)
        )

    state = {
        "cache_file": str(SWAP_USAGE_CACHE_FILE),
        "sample_count": len(samples),
        "sustained_window_seconds": sustained_seconds,
        "oldest_sustained_sample_age_seconds": oldest_sustained_age,
        "sustained_non_trivial": sustained_non_trivial,
        "write_error": "",
    }

    try:
        write_public_json(
            SWAP_USAGE_CACHE_FILE,
            {"schema_version": 1, "updated_at": utc_now(), "samples": samples},
        )
    except OSError as error:
        state["write_error"] = f"Could not update swap usage cache: {error}"

    return state


def swap_state(mem: dict[str, int]) -> dict[str, Any]:
    if "SwapTotal" not in mem or "SwapFree" not in mem:
        return {
            "available": False,
            "status": "unavailable",
            "usage_state": "unavailable",
            "warning": False,
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "used_percent": None,
            "detail": "Swap totals are unavailable from /proc/meminfo.",
        }

    total = mem.get("SwapTotal", 0)
    free = mem.get("SwapFree", 0)
    used = max(total - free, 0)
    if total <= 0:
        return {
            "available": False,
            "status": "disabled",
            "usage_state": "disabled",
            "warning": False,
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "used_percent": None,
            "detail": "No swap device is configured on the host.",
        }

    thresholds = swap_thresholds()
    used_percent = percent_or_none(used, total)
    history = swap_usage_history(used, total, thresholds)
    non_trivial_bytes = safe_int(thresholds.get("non_trivial_bytes"), 64 * 1024 * 1024)
    warning_percent = safe_float(thresholds.get("warning_percent"), 25.0)
    critical_percent = safe_float(thresholds.get("critical_percent"), 50.0)

    if used <= 0:
        usage_state = "unused"
    elif used_percent is not None and used_percent >= critical_percent:
        usage_state = "critical"
    elif history.get("sustained_non_trivial") or (used_percent is not None and used_percent >= warning_percent):
        usage_state = "warning"
    elif used >= non_trivial_bytes:
        usage_state = "non_trivial"
    else:
        usage_state = "minor"

    warning = usage_state in {"non_trivial", "warning", "critical"}
    return {
        "available": True,
        "status": "enabled",
        "usage_state": usage_state,
        "warning": warning,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_percent": used_percent,
        "thresholds": thresholds,
        "history": history,
        "detail": (
            "Swap usage is sustained or non-trivial; inspect top memory processes and recent deploy activity."
            if warning
            else "Swap is available as a zram fallback and current usage is low."
        ),
    }


def oom_evidence_state() -> dict[str, Any]:
    journal = run(["journalctl", "-k", "--since", OOM_EVIDENCE_WINDOW, "--no-pager"], timeout=10)
    content = f"{journal['stdout']}\n{journal['stderr']}"
    if not journal["ok"]:
        return {
            "available": False,
            "status": "unavailable",
            "count": None,
            "window": OOM_EVIDENCE_WINDOW,
            "pattern": OOM_EVIDENCE_RE.pattern,
            "recent_lines": [],
            "error": journal["stderr"].strip() or "Could not read kernel journal.",
        }

    matches = [line for line in safe_lines(content, limit=300) if OOM_EVIDENCE_RE.search(line)]
    return {
        "available": True,
        "status": "recent" if matches else "clear",
        "count": len(matches),
        "window": OOM_EVIDENCE_WINDOW,
        "pattern": "out of memory|oom-killer|killed process",
        "recent_lines": matches[-8:],
        "error": "",
    }


def disk_usage(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        statvfs = os.statvfs(path)
    except OSError:
        return {"path": str(path), "available": False}

    inode_total = statvfs.f_files
    inode_free = statvfs.f_ffree
    inode_used = max(inode_total - inode_free, 0)
    return {
        "path": str(path),
        "available": True,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": percent(usage.used, usage.total),
        "inode_total": inode_total,
        "inode_used": inode_used,
        "inode_used_percent": percent(inode_used, inode_total),
    }


def cached_disk_hotspots() -> dict[str, Any]:
    now = time.time()
    cache = read_json(DISK_SCAN_CACHE_FILE, {})
    scanned_at_epoch = safe_int(cache.get("scanned_at_epoch"))
    if scanned_at_epoch and now - scanned_at_epoch < DISK_SCAN_CACHE_SECONDS:
        cache["from_cache"] = True
        cache["cache_seconds"] = DISK_SCAN_CACHE_SECONDS
        return cache

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    roots = [Path(item) for item in DISK_SCAN_ROOTS if Path(item).exists()]

    for root in roots:
        result = run(["du", "-x", "-B1", "--max-depth=1", str(root)], timeout=25)
        if not result["ok"]:
            errors.extend(safe_lines(result["stderr"], limit=4))
            continue

        for line in result["stdout"].splitlines():
            raw_size, _, raw_path = line.partition("\t")
            size_bytes = safe_int(raw_size, -1)
            if size_bytes < 0 or not raw_path:
                continue
            rows.append({"path": raw_path, "size_bytes": size_bytes})

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = deduped.get(row["path"])
        if not existing or row["size_bytes"] > existing["size_bytes"]:
            deduped[row["path"]] = row

    top_folders = sorted(deduped.values(), key=lambda item: item["size_bytes"], reverse=True)[:10]
    data = {
        "available": bool(top_folders),
        "from_cache": False,
        "cache_seconds": DISK_SCAN_CACHE_SECONDS,
        "scanned_at": utc_now(),
        "scanned_at_epoch": int(now),
        "scan_roots": [str(root) for root in roots],
        "top_folders": top_folders,
        "errors": errors[:10],
        "method": "du -x -B1 --max-depth=1 with a cache to avoid rescanning every portal refresh",
    }

    try:
        DISK_SCAN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = DISK_SCAN_CACHE_FILE.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_file.replace(DISK_SCAN_CACHE_FILE)
        DISK_SCAN_CACHE_FILE.chmod(0o644)
    except OSError as error:
        data["errors"].append(f"Could not update disk scan cache: {error}")

    return data


def network_usage() -> dict[str, Any]:
    interfaces = []
    total_rx = 0
    total_tx = 0
    for line in read_text(Path("/proc/net/dev")).splitlines()[2:]:
        if ":" not in line:
            continue
        name, raw_stats = line.split(":", 1)
        iface = name.strip()
        if iface == "lo":
            continue
        stats = raw_stats.split()
        if len(stats) < 16:
            continue
        rx_bytes = int(stats[0])
        tx_bytes = int(stats[8])
        total_rx += rx_bytes
        total_tx += tx_bytes
        interfaces.append({"name": iface, "rx_bytes": rx_bytes, "tx_bytes": tx_bytes})
    return {"rx_bytes": total_rx, "tx_bytes": total_tx, "interfaces": interfaces}


def process_network_state() -> dict[str, Any]:
    return {
        "available": False,
        "top_receivers": [],
        "top_senders": [],
        "method": "Standard Linux /proc does not expose reliable per-process network byte totals without extra telemetry.",
        "note": "Host interface counters are shown in Resource Utilization. Per-process network rankings need an approved lightweight agent in a later PR.",
    }


def read_process_cmdline(pid: str, fallback: str) -> str:
    try:
        raw = Path("/proc", pid, "cmdline").read_bytes()
    except OSError:
        return fallback
    command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    return command or fallback


def read_process_status(pid: str) -> dict[str, int]:
    details = {"rss_bytes": 0, "threads": 0, "uid": -1}
    for line in read_text(Path("/proc", pid, "status")).splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) >= 2:
                details["uid"] = safe_int(parts[1], -1)
        elif line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                details["rss_bytes"] = safe_int(parts[1]) * 1024
        elif line.startswith("Threads:"):
            parts = line.split()
            if len(parts) >= 2:
                details["threads"] = safe_int(parts[1])
    return details


def read_process(pid: str, clock_ticks: int, uptime: int, cpu_count: int) -> dict[str, Any] | None:
    try:
        raw_stat = Path("/proc", pid, "stat").read_text(encoding="utf-8")
        before, after = raw_stat.rsplit(")", 1)
    except (OSError, ValueError):
        return None

    name_start = before.find("(")
    name = before[name_start + 1 :] if name_start >= 0 else pid
    fields = after.strip().split()
    if len(fields) < 20:
        return None

    utime = safe_int(fields[11])
    stime = safe_int(fields[12])
    stat_threads = safe_int(fields[17])
    start_ticks = safe_int(fields[19])
    cpu_time_seconds = round((utime + stime) / clock_ticks, 2) if clock_ticks > 0 else 0
    elapsed_seconds = max(int(uptime - (start_ticks / clock_ticks)), 0) if clock_ticks > 0 else 0
    idle_seconds = max(round(elapsed_seconds - cpu_time_seconds, 2), 0)
    cpu_percent = 0.0
    if elapsed_seconds > 0 and cpu_count > 0:
        cpu_percent = round((cpu_time_seconds / elapsed_seconds / cpu_count) * 100, 2)

    status = read_process_status(pid)
    uid = status["uid"]
    if uid < 0:
        try:
            uid = Path("/proc", pid).stat().st_uid
        except OSError:
            uid = -1

    return {
        "pid": safe_int(pid),
        "name": name,
        "command": read_process_cmdline(pid, name),
        "user": username_for_uid(uid) if uid >= 0 else "unknown",
        "memory_bytes": status["rss_bytes"],
        "cpu_percent": cpu_percent,
        "threads": status["threads"] or stat_threads,
        "cpu_time_seconds": cpu_time_seconds,
        "elapsed_seconds": elapsed_seconds,
        "idle_seconds": idle_seconds,
    }


def process_state() -> dict[str, Any]:
    clock_ticks = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK"))
    cpu_count = os.cpu_count() or 1
    uptime = uptime_seconds()
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        item = read_process(entry.name, clock_ticks, uptime, cpu_count)
        if item:
            processes.append(item)

    return {
        "method": "CPU percent is a lifetime average normalized across available CPU cores, not a live sampling spike meter.",
        "sampled_at": utc_now(),
        "top_memory": sorted(processes, key=lambda item: item["memory_bytes"], reverse=True)[:10],
        "top_cpu": sorted(
            processes,
            key=lambda item: (item["cpu_percent"], item["cpu_time_seconds"]),
            reverse=True,
        )[:10],
    }


def local_ip_addresses() -> list[str]:
    result = run(["hostname", "-I"])
    if not result["ok"]:
        return []
    return [item for item in result["stdout"].split() if item]


def load_average() -> dict[str, float]:
    try:
        one, five, fifteen = os.getloadavg()
        return {"one": round(one, 2), "five": round(five, 2), "fifteen": round(fifteen, 2)}
    except OSError:
        return {"one": 0.0, "five": 0.0, "fifteen": 0.0}


def systemd_status(service: str) -> dict[str, str]:
    active = run(["systemctl", "is-active", service], timeout=4)
    enabled = run(["systemctl", "is-enabled", service], timeout=4)
    return {
        "name": service,
        "active": active["stdout"].strip() or "unknown",
        "enabled": enabled["stdout"].strip() or "unknown",
    }


def systemd_show(service: str, properties: list[str]) -> dict[str, str]:
    result = run(["systemctl", "show", service, f"--property={','.join(properties)}", "--no-pager"], timeout=4)
    values: dict[str, str] = {}
    if not result["ok"]:
        return values
    for line in result["stdout"].splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip()
    return values


def systemd_timer_schedule(timer: str) -> dict[str, str]:
    result = run(
        [
            "systemctl",
            "show",
            timer,
            "--property=NextElapseUSecRealtime,LastTriggerUSec,Result,ActiveState,SubState",
            "--no-pager",
        ],
        timeout=4,
    )
    values: dict[str, str] = {}
    if result["ok"]:
        for line in result["stdout"].splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip()

    next_run_at = values.get("NextElapseUSecRealtime", "unknown") or "unknown"
    last_timer_trigger_at = values.get("LastTriggerUSec", "never") or "never"

    return {
        "timer": timer,
        "timer_active": values.get("ActiveState", "unknown") or "unknown",
        "timer_sub_state": values.get("SubState", "unknown") or "unknown",
        "next_run_at": next_run_at,
        "last_timer_trigger_at": last_timer_trigger_at,
        "next_report_run_at": next_run_at,
        "last_report_timer_trigger_at": last_timer_trigger_at,
        "timer_result": values.get("Result", "unknown") or "unknown",
    }


def local_http_probe(url: str) -> dict[str, Any]:
    if not url:
        return {"ok": False, "status": 0, "error": "ready URL is not configured"}
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            status = int(response.status)
            return {"ok": 200 <= status < 300, "status": status, "error": ""}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": int(error.code), "error": str(error)}
    except (OSError, urllib.error.URLError) as error:
        return {"ok": False, "status": 0, "error": str(error)}


def alloy_textfile_files() -> list[dict[str, Any]]:
    files = []
    try:
        paths = sorted(ALLOY_TEXTFILE_DIR.glob("*.prom"))
    except OSError:
        return files
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"path": str(path), "size_bytes": stat.st_size})
    return files


def alloy_permission_error_state() -> dict[str, Any]:
    journal = run(["journalctl", "-u", ALLOY_SERVICE, "--since", ALLOY_ERROR_WINDOW, "--no-pager"], timeout=8)
    content = f"{journal['stdout']}\n{journal['stderr']}"
    matches = [
        line
        for line in safe_lines(content, limit=200)
        if ALLOY_CONTAINERD_PERMISSION_ERROR in line or ALLOY_FILE_PERMISSION_ERROR_RE.search(line)
    ]
    return {
        "available": journal["ok"],
        "count": content.count(ALLOY_CONTAINERD_PERMISSION_ERROR) + len(ALLOY_FILE_PERMISSION_ERROR_RE.findall(content)),
        "window": ALLOY_ERROR_WINDOW,
        "pattern": " | ".join(ALLOY_PERMISSION_ERROR_PATTERNS),
        "recent_lines": matches[-5:],
        "error": "" if journal["ok"] else journal["stderr"].strip(),
    }


def alloy_state() -> dict[str, Any]:
    service = systemd_status(ALLOY_SERVICE)
    unit = systemd_show(ALLOY_SERVICE, ["ActiveState", "SubState", "User", "SupplementaryGroups", "DropInPaths"])
    ready = local_http_probe(ALLOY_READY_URL) if ALLOY_ENABLED else {"ok": False, "status": 0, "error": "Alloy disabled"}
    permission_errors = alloy_permission_error_state() if ALLOY_ENABLED else {
        "available": False,
        "count": 0,
        "window": ALLOY_ERROR_WINDOW,
        "pattern": ALLOY_CONTAINERD_PERMISSION_ERROR,
        "recent_lines": [],
        "error": "Alloy disabled",
    }

    return {
        "enabled": ALLOY_ENABLED,
        "collect_docker": ALLOY_COLLECT_DOCKER,
        "collect_docker_logs": ALLOY_COLLECT_DOCKER_LOGS,
        "container_metrics_strategy": "docker_cadvisor_enabled" if ALLOY_COLLECT_DOCKER else "cadvisor_disabled",
        "log_shipping_strategy": "docker_api_logs_enabled" if ALLOY_COLLECT_DOCKER_LOGS else "docker_logs_disabled",
        "strategy_note": (
            "Docker/cAdvisor collection is enabled and should use only the Docker socket privilege boundary."
            if ALLOY_COLLECT_DOCKER
            else "Docker/cAdvisor collection is intentionally disabled; host, systemd, journal/file, Docker log, and textfile telemetry stay active."
        ),
        "log_strategy_note": (
            "Docker container logs are collected through the Docker API socket with Alloy running as a non-root docker group member."
            if ALLOY_COLLECT_DOCKER_LOGS
            else "Docker container logs are not collected by Alloy."
        ),
        "service": service,
        "unit": unit,
        "ready": ready,
        "ready_url": ALLOY_READY_URL,
        "permission_errors": permission_errors,
        "textfile_dir": str(ALLOY_TEXTFILE_DIR),
        "textfile_files": alloy_textfile_files(),
    }


def observability_state() -> dict[str, Any]:
    return {"alloy": alloy_state()}


def docker_state() -> dict[str, Any]:
    ps = run(["docker", "ps", "--all", "--format", "{{json .}}"], timeout=8)
    containers = []
    names = []
    if ps["ok"]:
        for line in ps["stdout"].splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = item.get("Names", "")
            if name:
                names.append(name)
            health = "unknown"
            status_text = str(item.get("Status", ""))
            if "(healthy)" in status_text:
                health = "healthy"
            elif "(unhealthy)" in status_text:
                health = "unhealthy"
            elif "(health: starting)" in status_text:
                health = "starting"
            containers.append(
                {
                    "name": name,
                    "image": item.get("Image", ""),
                    "state": item.get("State", ""),
                    "status": item.get("Status", ""),
                    "ports": item.get("Ports", ""),
                    "health": health,
                    "restart_count": 0,
                    "compose_project": item.get("Label", ""),
                    "configured_image": "",
                    "image_id": "",
                    "repo_digests": [],
                    "source_commit": "",
                    "build_id": "",
                }
            )

    inspect_cache = {"state": "not_needed"}
    if names:
        inspected_state = cached_slow_section(
            "docker_inspect",
            section_ttl("docker_inspect"),
            lambda: docker_inspect_state(names),
        )
        inspect_cache = inspected_state.get("_collector_cache", {})
        details = inspected_state.get("containers", {})
        if isinstance(details, dict):
            for container in containers:
                detail = details.get(container["name"], {})
                if not isinstance(detail, dict):
                    continue
                for key in (
                    "restart_count",
                    "health",
                    "compose_project",
                    "configured_image",
                    "image_id",
                    "repo_digests",
                    "source_commit",
                    "build_id",
                ):
                    if key in detail:
                        container[key] = detail[key]

    compose_state = cached_slow_section(
        "docker_compose",
        section_ttl("docker_compose"),
        docker_compose_state,
    )
    compose_projects = compose_state.get("compose_projects", [])
    if not isinstance(compose_projects, list):
        compose_projects = []

    return {
        "available": ps["ok"],
        "error": "" if ps["ok"] else ps["stderr"],
        "containers": containers,
        "compose_projects": compose_projects,
        "inspect_cache": inspect_cache,
        "compose_cache": compose_state.get("_collector_cache", {}),
    }


def docker_inspect_state(names: list[str]) -> dict[str, Any]:
    inspect = run(["docker", "inspect", *names], timeout=10)
    details_by_name: dict[str, dict[str, Any]] = {}
    if not inspect["ok"]:
        return {"available": False, "containers": details_by_name, "error": inspect["stderr"]}

    try:
        inspected = json.loads(inspect["stdout"])
    except json.JSONDecodeError:
        inspected = []
    details = {item.get("Name", "").lstrip("/"): item for item in inspected}
    image_ids = sorted({str(item.get("Image", "")) for item in details.values() if item.get("Image")})
    images_by_id: dict[str, dict[str, Any]] = {}
    if image_ids:
        image_inspect = run(["docker", "image", "inspect", *image_ids], timeout=10)
        if image_inspect["ok"]:
            try:
                inspected_images = json.loads(image_inspect["stdout"])
            except json.JSONDecodeError:
                inspected_images = []
            images_by_id = {item.get("Id", ""): item for item in inspected_images}

    for name, detail in details.items():
        state = detail.get("State", {})
        labels = detail.get("Config", {}).get("Labels", {}) or {}
        image_id = detail.get("Image", "")
        image_detail = images_by_id.get(image_id, {})
        repo_digests = image_detail.get("RepoDigests", [])
        if not isinstance(repo_digests, list):
            repo_digests = []
        details_by_name[name] = {
            "restart_count": detail.get("RestartCount", 0),
            "health": state.get("Health", {}).get("Status", "none"),
            "compose_project": labels.get("com.docker.compose.project", ""),
            "configured_image": detail.get("Config", {}).get("Image", ""),
            "image_id": image_id,
            "repo_digests": [str(item) for item in repo_digests],
            "source_commit": labels.get("org.opencontainers.image.revision", ""),
            "build_id": labels.get("io.nutsnews.build.id", "") or labels.get(
                "org.opencontainers.image.version",
                "",
            ),
        }
    return {"available": True, "containers": details_by_name, "error": ""}


def docker_compose_state() -> dict[str, Any]:
    compose = run(["docker", "compose", "ls", "--format", "json"], timeout=8)
    compose_projects: list[dict[str, Any]] = []
    if compose["ok"] and compose["stdout"].strip():
        try:
            parsed = json.loads(compose["stdout"])
            if isinstance(parsed, list):
                compose_projects = parsed
        except json.JSONDecodeError:
            compose_projects = []
    return {
        "available": compose["ok"],
        "compose_projects": compose_projects,
        "error": "" if compose["ok"] else compose["stderr"],
    }


def log_sections() -> dict[str, Any]:
    caddy = run(["docker", "logs", "--tail", "80", "nutsnews-caddy"], timeout=8)
    journal = run(["journalctl", "-p", "warning..alert", "-n", "80", "--no-pager"], timeout=8)
    auth_log = Path("/var/log/auth.log")
    auth_lines = []
    if auth_log.exists():
        auth_lines = safe_lines("\n".join(auth_log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))

    return {
        "redaction": "token, secret, password, authorization, credential, and private-key patterns are redacted",
        "caddy": safe_lines(caddy["stdout"] + caddy["stderr"]),
        "journal_warnings": safe_lines(journal["stdout"] + journal["stderr"]),
        "auth": auth_lines,
    }


def pending_updates() -> dict[str, Any]:
    apt = run(["apt", "list", "--upgradable"], timeout=10)
    if not apt["ok"]:
        return {"available": False, "count": 0, "security_count": 0, "sample": []}
    lines = [line for line in apt["stdout"].splitlines() if "/" in line and not line.startswith("Listing")]
    security = [line for line in lines if "security" in line.lower()]
    return {
        "available": True,
        "count": len(lines),
        "security_count": len(security),
        "sample": safe_lines("\n".join(lines[:12]), limit=12),
    }


def failed_login_summary() -> dict[str, int]:
    auth_log = Path("/var/log/auth.log")
    if not auth_log.exists():
        return {"recent_failed_login_lines": 0, "invalid_user_lines": 0}
    try:
        lines = auth_log.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]
    except OSError:
        return {"recent_failed_login_lines": 0, "invalid_user_lines": 0}
    failed = [line for line in lines if "Failed password" in line or "authentication failure" in line]
    invalid = [line for line in lines if "Invalid user" in line]
    return {"recent_failed_login_lines": len(failed), "invalid_user_lines": len(invalid)}


def ssh_hardening() -> dict[str, str]:
    conf = read_text(Path("/etc/ssh/sshd_config.d/20-nutsnews-baseline.conf"))
    values: dict[str, str] = {}
    for line in conf.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or " " not in stripped:
            continue
        key, value = stripped.split(None, 1)
        values[key] = value
    return {
        "password_authentication": values.get("PasswordAuthentication", "unknown"),
        "kbd_interactive_authentication": values.get("KbdInteractiveAuthentication", "unknown"),
        "permit_root_login": values.get("PermitRootLogin", "unknown"),
        "allow_tcp_forwarding": values.get("AllowTcpForwarding", "unknown"),
    }


def security_state() -> dict[str, Any]:
    ufw = run(["ufw", "status", "verbose"], timeout=8)
    ports = run(["ss", "-tulpenH"], timeout=8)
    return {
        "firewall": safe_lines(ufw["stdout"] + ufw["stderr"], limit=30),
        "open_ports": safe_lines(ports["stdout"] + ports["stderr"], limit=80),
        "ssh_hardening": ssh_hardening(),
        "pending_updates": pending_updates(),
        "last_reboot": boot_time(),
        "failed_logins": failed_login_summary(),
    }


def backup_state() -> dict[str, Any]:
    usage = run(["du", "-sb", str(BACKUPS_DIR)], timeout=8)
    size_bytes = None
    if usage["ok"] and usage["stdout"].strip():
        try:
            size_bytes = int(usage["stdout"].split()[0])
        except (IndexError, ValueError):
            size_bytes = None

    latest = None
    if BACKUPS_DIR.exists():
        try:
            files = [path for path in BACKUPS_DIR.rglob("*") if path.is_file()]
            if files:
                newest = max(files, key=lambda path: path.stat().st_mtime)
                latest = {
                    "path": str(newest),
                    "updated_at": datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                    "size_bytes": newest.stat().st_size,
                }
        except OSError:
            latest = None

    default = {
        "schema_version": 1,
        "updated_at": "unknown",
        "enabled": False,
        "configured": False,
        "repository": "rclone:nutsnews-onedrive:nutsnews-backups/vps",
        "repository_path": "nutsnews-backups/vps",
        "transport": "rclone OneDrive remote dedicated to NutsNews backups",
        "encryption": "restic",
        "encrypted_before_transport": True,
        "raw_onedrive_backups": False,
        "directory": str(BACKUPS_DIR),
        "size_bytes": size_bytes,
        "latest": latest,
        "latest_snapshot": None,
        "latest_snapshot_age_seconds": None,
        "latest_status": "disabled",
        "last_backup": {"status": "never"},
        "last_prune": {"status": "never"},
        "last_check": {"status": "never"},
        "retention": {},
        "stale_after_hours": 30,
        "stale_after_seconds": 108000,
        "verify_stale_after_hours": 192,
        "verify_stale_after_seconds": 691200,
        "missing_configuration": [],
        "backup_path_count": 0,
        "protected_path_count": 0,
        "missing_path_count": 0,
        "backup_paths_redacted": True,
        "backup_paths_source": "Root-only Ansible-managed path list.",
        "exclude_source": "Root-only Ansible-managed exclude list.",
        "services": {
            "backup_service": "nutsnews-restic-backup.service",
            "backup_timer": "nutsnews-restic-backup.timer",
            "verify_service": "nutsnews-restic-verify.service",
            "verify_timer": "nutsnews-restic-verify.timer",
        },
        "security_model": "restic encrypts snapshots locally before rclone transports ciphertext to OneDrive.",
        "snapshot_reminder": "Encrypted restic snapshots go to OneDrive through the dedicated nutsnews-onedrive rclone remote.",
    }
    data = read_json(BACKUP_STATUS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    combined = {**default, **data}
    raw_backup_paths = combined.get("backup_paths")
    raw_missing_paths = combined.get("missing_paths")
    if isinstance(raw_backup_paths, list) and not combined.get("backup_path_count"):
        combined["backup_path_count"] = len(raw_backup_paths)
        combined["protected_path_count"] = len(raw_backup_paths)
    if isinstance(raw_missing_paths, list) and not combined.get("missing_path_count"):
        combined["missing_path_count"] = len(raw_missing_paths)
    for key in ("backup_paths", "missing_paths", "backup_paths_file", "exclude_file"):
        combined.pop(key, None)
    latest_snapshot = combined.get("latest_snapshot")
    if isinstance(latest_snapshot, dict):
        raw_snapshot_paths = latest_snapshot.pop("paths", None)
        if raw_snapshot_paths is not None and "path_count" not in latest_snapshot:
            latest_snapshot["path_count"] = len(raw_snapshot_paths) if isinstance(raw_snapshot_paths, list) else 0
    combined["directory"] = str(BACKUPS_DIR)
    combined["size_bytes"] = size_bytes
    combined["latest"] = latest
    combined["status_file"] = str(BACKUP_STATUS_FILE)
    services = combined.get("services", {}) if isinstance(combined.get("services"), dict) else {}
    backup_timer = str(services.get("backup_timer") or "nutsnews-restic-backup.timer")
    backup_service = str(services.get("backup_service") or "nutsnews-restic-backup.service")
    verify_timer = str(services.get("verify_timer") or "nutsnews-restic-verify.timer")
    verify_service = str(services.get("verify_service") or "nutsnews-restic-verify.service")
    combined.update(systemd_timer_schedule(backup_timer))
    combined["backup_service"] = systemd_status(backup_service)
    combined["verify_service"] = systemd_status(verify_service)
    combined["verify_timer_state"] = systemd_timer_schedule(verify_timer)
    combined["verify_timer"] = verify_timer
    combined["verify_timer_active"] = combined["verify_timer_state"].get("timer_active", "unknown")
    combined["verify_timer_sub_state"] = combined["verify_timer_state"].get("timer_sub_state", "unknown")
    combined["verify_next_run_at"] = combined["verify_timer_state"].get("next_run_at", "unknown")
    combined["verify_last_timer_trigger_at"] = combined["verify_timer_state"].get("last_timer_trigger_at", "never")

    snapshot = combined.get("latest_snapshot")
    if isinstance(snapshot, dict):
        live_age = age_seconds(snapshot.get("time"))
        combined["latest_snapshot_age_seconds"] = live_age
        if combined.get("enabled"):
            if live_age is None:
                combined["latest_status"] = "unknown"
            else:
                combined["latest_status"] = (
                    "fresh" if live_age <= safe_int(combined.get("stale_after_seconds"), 108000) else "stale"
                )
    combined["latest_snapshot_verification"] = backup_verification_status(combined)
    combined["verification_status"] = combined["latest_snapshot_verification"].get("status", "unknown")
    combined["latest_snapshot_verified"] = combined["latest_snapshot_verification"].get("latest_snapshot_verified", False)
    return combined


def reporting_state() -> dict[str, Any]:
    default = {
        "enabled": False,
        "configured": False,
        "status": "disabled",
        "updated_at": "unknown",
        "smtp_host_configured": False,
        "last_alert_check_at": "unknown",
        "last_alert_sent_at": "never",
        "last_report_run_at": "never",
        "last_report_success_at": "never",
        "last_report_sent_at": "never",
        "last_error": "",
        "cooldown_seconds": 21600,
        "pending_alerts": 0,
        "suppressed_alerts": 0,
        "recipients_count": 0,
        "email_config_source": "Root-only environment file managed by Ansible.",
    }
    data = read_json(REPORTING_STATUS_FILE, default)
    if not isinstance(data, dict):
        return default
    return {**default, **data}


def free_tier_usage_state() -> dict[str, Any]:
    if collect_free_tier_usage is None:
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "providers": [],
            "errors": ["Free-tier usage collector module is not installed."],
        }
    try:
        return collect_free_tier_usage()
    except Exception:
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "providers": [],
            "errors": ["Free-tier usage collector failed; check the collector service journal."],
        }


def gib(value: Any) -> float | None:
    try:
        return round(float(value) / (1024**3), 2)
    except (TypeError, ValueError):
        return None


def display_number(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def display_amount(value: float | None, unit: str) -> str:
    if value is None:
        return "unknown"
    if unit == "%":
        return f"{display_number(value)}%"
    return f"{display_number(value)} {unit}".strip()


def display_used_percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


def usage_risk_status(used_percent: float | None, remaining: float | None) -> str:
    if used_percent is None:
        return "unknown"
    if used_percent >= 100 or (remaining is not None and remaining < 0):
        return "over_limit"
    if used_percent >= 85:
        return "critical"
    if used_percent >= 70:
        return "warning"
    return "safe"


def local_usage_metric(
    key: str,
    label: str,
    used: float | None,
    limit: float | None,
    unit: str,
    period: str,
) -> dict[str, Any]:
    remaining = None if used is None or limit is None else round(limit - used, 2)
    if used is None or limit is None:
        used_percent = None
    elif limit <= 0:
        used_percent = 0.0 if used <= 0 else 100.0
    else:
        used_percent = round((used / limit) * 100, 1)
    remaining_percent = None if used_percent is None else round(max(100.0 - used_percent, 0.0), 1)
    risk_status = usage_risk_status(used_percent, remaining)
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "period": period,
        "reset_at": "not applicable",
        "description": "",
        "quota_source": "Live local collector from kernel, filesystem, Docker, and backup status data.",
        "quota_last_verified": utc_now().split("T", 1)[0],
        "usage_source": "local",
        "measurement_status": "measured" if used is not None else "unavailable",
        "measurement_detail": (
            "Usage was measured by the local read-only collector."
            if used is not None
            else "The local read-only collector could not measure this metric."
        ),
        "usage": used,
        "limit": limit,
        "remaining": remaining,
        "percent_used": used_percent,
        "percent_remaining": remaining_percent,
        "usage_display": display_amount(used, unit),
        "limit_display": display_amount(limit, unit),
        "remaining_display": display_amount(remaining, unit),
        "percent_used_display": display_used_percent(used_percent),
        "percent_remaining_display": display_used_percent(remaining_percent),
        "health": "healthy" if risk_status == "safe" else risk_status,
        "risk_status": risk_status,
        "risk_label": risk_status.replace("_", " "),
    }


def provider_primary_metric(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    with_usage = [metric for metric in metrics if metric.get("percent_used") is not None]
    if with_usage:
        return max(with_usage, key=lambda item: item.get("percent_used") or 0)
    return metrics[0] if metrics else local_usage_metric("unknown", "Unknown", None, None, "", "current")


def local_usage_provider(
    key: str,
    platform_name: str,
    metrics: list[dict[str, Any]],
    source_detail: str,
    notes: str = "",
    status: str = "live",
    plan: str = "Current VPS allocation",
) -> dict[str, Any]:
    primary = provider_primary_metric(metrics)
    risk_status = primary.get("risk_status", "unknown")
    if status == "not configured":
        risk_status = "not_configured"
    return {
        "key": key,
        "platform": platform_name,
        "plan": plan,
        "status": status,
        "source_status": status,
        "source_detail": source_detail,
        "last_checked_at": utc_now() if status == "live" else "unknown",
        "stale": False,
        "quota_source": "Live local collector from kernel, filesystem, Docker, and backup status data.",
        "quota_last_verified": utc_now().split("T", 1)[0],
        "notes": notes,
        "current_usage": primary.get("usage_display", "unknown"),
        "quota": primary.get("limit_display", "unknown"),
        "remaining": primary.get("remaining_display", "unknown"),
        "percent_used": primary.get("percent_used"),
        "percent_remaining": primary.get("percent_remaining"),
        "percent_used_display": primary.get("percent_used_display", "unknown"),
        "percent_remaining_display": primary.get("percent_remaining_display", "unknown"),
        "health": primary.get("health", "unknown"),
        "risk_status": risk_status,
        "risk_label": str(risk_status).replace("_", " "),
        "metrics": metrics,
    }


def directory_size_bytes(path: Path) -> int | None:
    usage = run(["du", "-sb", str(path)], timeout=8)
    if not usage["ok"] or not usage["stdout"].strip():
        return None
    try:
        return int(usage["stdout"].split()[0])
    except (IndexError, ValueError):
        return None


def local_usage_providers(
    resources: dict[str, Any],
    docker: dict[str, Any],
    backups: dict[str, Any],
) -> list[dict[str, Any]]:
    disk = resources.get("disk", {})
    memory = resources.get("memory", {})
    swap = resources.get("swap", {})
    swap_available = swap.get("status") == "enabled"
    root_total_gib = gib(disk.get("total_bytes"))

    providers = [
        local_usage_provider(
            "vps_host",
            "VPS Host",
            [
                local_usage_metric(
                    "cpu_sample_percent",
                    "CPU Sample",
                    resources.get("cpu_percent"),
                    100.0,
                    "%",
                    "current",
                ),
                local_usage_metric(
                    "ram_gib",
                    "RAM",
                    gib(memory.get("used_bytes")),
                    gib(memory.get("total_bytes")),
                    "GiB",
                    "current",
                ),
                local_usage_metric(
                    "root_disk_gib",
                    "Root Disk",
                    gib(disk.get("used_bytes")),
                    root_total_gib,
                    "GiB",
                    "current",
                ),
                local_usage_metric(
                    "swap_gib",
                    "Swap",
                    gib(swap.get("used_bytes")) if swap_available else None,
                    gib(swap.get("total_bytes")) if swap_available else None,
                    "GiB",
                    "current",
                ),
            ],
            "Usage read directly from /proc and filesystem statistics on the VPS.",
            "CPU, RAM, root disk, and swap are usage-limited by the current VPS size.",
        )
    ]

    docker_size = directory_size_bytes(Path("/var/lib/docker"))
    docker_metrics = [
        local_usage_metric(
            "docker_data_gib",
            "Docker Data Directory",
            gib(docker_size),
            root_total_gib,
            "GiB",
            "current",
        )
    ]
    providers.append(
        local_usage_provider(
            "docker_storage",
            "Docker Storage",
            docker_metrics,
            "Docker storage is measured with a read-only du scan of /var/lib/docker.",
            "If /var/lib/docker is missing or unreadable, usage is shown as unavailable rather than estimated.",
            "live" if docker_size is not None else "unavailable",
        )
    )

    backup_size = gib(backups.get("size_bytes"))
    backup_metrics = [
        local_usage_metric(
            "backup_local_cache_gib",
            "Local Backup Cache",
            backup_size,
            root_total_gib,
            "GiB",
            "current",
        ),
    ]
    backup_status = "live" if backups.get("enabled") else "not configured"
    providers.append(
        local_usage_provider(
            "backup_storage",
            "Backup Local Cache",
            backup_metrics,
            "Local backup cache usage is measured against the VPS root filesystem capacity.",
            (
                "Snapshot age stays in backup freshness status. Remote OneDrive quota is not measured "
                "without a real read-only source."
            ),
            backup_status,
            plan="VPS root filesystem capacity",
        )
    )

    if not docker.get("available"):
        providers[-2]["source_detail"] = "Docker CLI is unavailable or returned an error; storage usage is unavailable."
        providers[-2]["risk_status"] = "unknown"
        providers[-2]["risk_label"] = "unknown"
        providers[-2]["health"] = "unknown"
    return providers


def summarize_free_tier_usage(free_tier: dict[str, Any]) -> dict[str, Any]:
    providers = free_tier.get("providers", [])
    if not isinstance(providers, list):
        providers = []
    if summarize_providers is not None:
        return summarize_providers(providers)
    counts = {"safe": 0, "warning": 0, "critical": 0, "over_limit": 0, "unknown": 0, "not_configured": 0}
    for provider in providers:
        status = str(provider.get("risk_status") or "unknown")
        counts[status if status in counts else "unknown"] += 1
    return {
        "total_services": len(providers),
        "safe": counts["safe"],
        "ok": counts["safe"],
        "warning": counts["warning"],
        "critical": counts["critical"],
        "over_limit": counts["over_limit"],
        "unknown": counts["unknown"],
        "not_configured": counts["not_configured"],
        "unknown_or_not_configured": counts["unknown"] + counts["not_configured"],
    }


def resource_state() -> dict[str, Any]:
    mem = meminfo()
    memory_total = mem.get("MemTotal", 0)
    memory_available = mem.get("MemAvailable", 0)
    memory_used = max(memory_total - memory_available, 0)

    return {
        "cpu_percent": cpu_percent(),
        "load_average": load_average(),
        "memory": {
            "total_bytes": memory_total,
            "used_bytes": memory_used,
            "available_bytes": memory_available,
            "used_percent": percent(memory_used, memory_total),
        },
        "swap": swap_state(mem),
        "oom_evidence": cached_slow_section(
            "oom_evidence",
            section_ttl("oom_evidence"),
            oom_evidence_state,
        ),
        "disk": disk_usage(Path("/")),
        "nutsnews_disk": disk_usage(ROOT_DIR),
        "network": network_usage(),
    }


def alert_item(identity: str, level: str, message: str) -> dict[str, str]:
    return {"id": identity, "level": level, "message": message}


def free_tier_alerts(free_tier: dict[str, Any]) -> list[dict[str, str]]:
    alerts = []
    for provider in free_tier.get("providers", []):
        if not isinstance(provider, dict):
            continue
        risk = str(provider.get("risk_status") or "unknown").lower()
        if risk not in {"warning", "critical", "over_limit"}:
            continue
        level = "critical" if risk in {"critical", "over_limit"} else "warning"
        name = str(provider.get("platform") or provider.get("key") or "Provider")
        remaining = provider.get("remaining") or "unknown remaining"
        used = provider.get("percent_used_display") or "unknown used"
        alerts.append(
            alert_item(
                f"free_tier.{provider.get('key') or 'unknown'}.quota_risk",
                level,
                f"{name} free-tier usage is {risk.replace('_', ' ')}: {remaining} remaining, {used} used.",
            )
        )
    return alerts


def alert_state(
    resources: dict[str, Any],
    docker: dict[str, Any],
    services: list[dict[str, str]],
    backups: dict[str, Any],
    free_tier: dict[str, Any],
    observability: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    alerts = []
    observability = observability or {}
    disk = resources.get("disk", {})
    memory = resources.get("memory", {})
    swap = resources.get("swap", {})

    if disk.get("used_percent", 0) >= 85:
        alerts.append(alert_item("resource.root_disk_usage", "warning", "Root disk usage is above 85 percent."))
    if disk.get("inode_used_percent", 0) >= 85:
        alerts.append(alert_item("resource.root_inode_usage", "warning", "Root inode usage is above 85 percent."))
    if memory.get("used_percent", 0) >= 90:
        alerts.append(alert_item("resource.memory_usage", "warning", "Memory usage is above 90 percent."))
    swap_usage_state = str(swap.get("usage_state") or "unknown")
    if swap_usage_state == "critical":
        alerts.append(alert_item("resource.swap_usage", "critical", "Swap usage is above the critical threshold."))
    elif swap.get("warning"):
        alerts.append(alert_item("resource.swap_usage", "warning", "Swap usage is sustained or non-trivial."))

    oom_evidence = resources.get("oom_evidence", {})
    oom_count = oom_evidence.get("count") if isinstance(oom_evidence, dict) else None
    if isinstance(oom_count, int) and oom_count > 0:
        alerts.append(alert_item("resource.kernel_oom", "critical", "Recent kernel OOM evidence was found."))

    unhealthy = [
        container["name"]
        for container in docker.get("containers", [])
        if container.get("health") not in ("healthy", "none", "unknown", "")
    ]
    if unhealthy:
        alerts.append(
            alert_item("runtime.unhealthy_containers", "critical", "Unhealthy containers: " + ", ".join(unhealthy))
        )

    inactive = [
        service["name"]
        for service in services
        if service["name"] in ("ssh.service", "docker.service", "fail2ban.service")
        and service.get("active") not in ("active", "activating")
    ]
    if inactive:
        alerts.append(
            alert_item(
                "runtime.important_services_inactive",
                "critical",
                "Important services are not active: " + ", ".join(inactive),
            )
        )

    if backups.get("enabled"):
        if not backups.get("configured"):
            missing = ", ".join(backups.get("missing_configuration", [])) or "backup secrets"
            alerts.append(
                alert_item("backup.configuration", "critical", f"VPS backups are enabled but misconfigured: {missing}.")
            )

        backup_status = str(backups.get("last_backup", {}).get("status", "")).lower()
        prune_status = str(backups.get("last_prune", {}).get("status", "")).lower()
        verification = backups.get("latest_snapshot_verification", {})
        if not isinstance(verification, dict):
            verification = backup_verification_status(backups)
        verification_status = str(verification.get("status", "")).lower()
        verification_overdue = bool(verification.get("overdue"))
        latest_status = str(backups.get("latest_status", "")).lower()
        latest_age = backups.get("latest_snapshot_age_seconds")
        stale_after = safe_int(backups.get("stale_after_seconds"), 108000)

        if backup_status == "failed":
            alerts.append(alert_item("backup.last_run_failed", "critical", "The latest VPS restic backup failed."))
        if prune_status == "failed":
            alerts.append(
                alert_item("backup.prune_failed", "warning", "The latest VPS restic prune failed after backup.")
            )
        if verification_status == "failed":
            alerts.append(
                alert_item("backup.verification_failed", "warning", "The latest VPS backup verification failed.")
            )
        elif verification_status == "stale" or verification_overdue:
            alerts.append(alert_item("backup.verification_overdue", "warning", "VPS backup verification is overdue."))
        if backups.get("timer_active") not in ("active", "activating"):
            alerts.append(alert_item("backup.timer_inactive", "warning", "The VPS backup timer is not active."))
        if backups.get("verify_timer_active") not in ("active", "activating"):
            alerts.append(
                alert_item(
                    "backup.verification_timer_inactive",
                    "warning",
                    "The VPS backup verification timer is not active.",
                )
            )
        if not backups.get("latest_snapshot"):
            alerts.append(
                alert_item("backup.snapshot_missing", "warning", "No VPS restic backup snapshot is available yet.")
            )
        elif latest_status == "stale" or (isinstance(latest_age, int) and latest_age > stale_after):
            alerts.append(
                alert_item("backup.snapshot_stale", "critical", "The latest VPS restic backup snapshot is stale.")
            )

    alerts.extend(free_tier_alerts(free_tier))

    alloy = observability.get("alloy", {})
    if isinstance(alloy, dict) and alloy.get("enabled"):
        ready = alloy.get("ready", {})
        if isinstance(ready, dict) and not ready.get("ok"):
            alerts.append(
                alert_item(
                    "observability.alloy_not_ready",
                    "critical",
                    "Grafana Alloy is enabled but its readiness endpoint is not healthy.",
                )
            )
        permission_errors = alloy.get("permission_errors", {})
        if isinstance(permission_errors, dict) and safe_int(permission_errors.get("count"), 0) > 0:
            alerts.append(
                alert_item(
                    "observability.alloy_permission_errors",
                    "warning",
                    "Recent Grafana Alloy permission errors detected.",
                )
            )

    if not alerts:
        alerts.append(
            alert_item("system.no_active_alerts", "ok", "No local threshold alerts from the current snapshot.")
        )
    return alerts


def app_state(docker: dict[str, Any]) -> dict[str, Any]:
    marker = read_json(APP_APPLY_MARKER_FILE, {})
    if not isinstance(marker, dict):
        marker = {}

    configured_keys = set(read_env_keys(APP_ENV_FILE))
    configured_secret_keys = [item for item in APP_SECRET_ENV_KEYS if item in configured_keys]
    missing_required_secret_keys = [item for item in APP_REQUIRED_SECRET_KEYS if item not in configured_keys]

    container = {}
    for item in docker.get("containers", []):
        if item.get("name") == APP_CONTAINER_NAME:
            container = item
            break

    container_state = container.get("state", "absent")
    container_health = container.get("health", "n/a")
    container_ports = container.get("ports", "")
    compose_project = container.get("compose_project", "")

    if APP_ENABLED:
        if container_state == "running" and container_health == "healthy":
            deployment_state = "running"
        elif container_state == "running":
            deployment_state = "started"
        else:
            deployment_state = "not_running"
    else:
        deployment_state = "disabled"

    repo_digests = container.get("repo_digests", [])
    actual_repo_digest = ""
    if isinstance(repo_digests, list):
        actual_repo_digest = next(
            (item for item in repo_digests if item.startswith(f"{APP_IMAGE_REPO}@sha256:")),
            "",
        )

    staged_health_url = app_staged_health_url()
    public_health_url = app_public_health_url()
    staged_probe = (
        local_http_probe(staged_health_url)
        if staged_health_url
        else {"ok": False, "status": 0, "error": "staged route disabled"}
    )
    public_probe = (
        local_http_probe(public_health_url)
        if public_health_url
        else {"ok": False, "status": 0, "error": "public route disabled"}
    )

    actual_source_commit = str(container.get("source_commit", ""))
    actual_build_id = str(container.get("build_id", ""))
    expected_reference = APP_IMAGE if APP_IMAGE_DIGEST else ""
    last_deployment_result = str(marker.get("deployment_result") or marker.get("status") or "not_deployed")

    state = {
        "enabled": APP_ENABLED,
        "health_path": APP_HEALTH_PATH,
        "staged_route_enabled": APP_STAGED_ROUTE_ENABLED,
        "public_route_enabled": APP_PUBLIC_ROUTE_ENABLED,
        "route_path": APP_ROUTE_PATH,
        "public_domain": APP_PUBLIC_DOMAIN,
        "expected": {
            "image_repository": APP_IMAGE_REPO,
            "image_digest": APP_IMAGE_DIGEST,
            "image_reference": expected_reference,
            "source_commit": APP_SOURCE_COMMIT,
            "build_id": APP_BUILD_ID,
            "source_workflow_run_id": str(marker.get("source_workflow_run_id", "")),
            "config_generation": str(marker.get("config_generation", "")),
            "deployment_target": APP_DEPLOYMENT_TARGET,
            "last_known_good_digest": APP_LAST_KNOWN_GOOD_DIGEST,
        },
        "actual": {
            "configured_image": str(container.get("configured_image", "")),
            "running_repo_digest": actual_repo_digest,
            "image_id": str(container.get("image_id", "")),
            "source_commit": actual_source_commit,
            "build_id": actual_build_id,
            "matches_expected_digest": bool(
                APP_IMAGE_DIGEST and actual_repo_digest == f"{APP_IMAGE_REPO}@{APP_IMAGE_DIGEST}"
            ),
            "matches_expected_source": bool(
                APP_SOURCE_COMMIT and actual_source_commit == APP_SOURCE_COMMIT
            ),
            "matches_expected_build": bool(APP_BUILD_ID and actual_build_id == APP_BUILD_ID),
        },
        "container_name": APP_CONTAINER_NAME,
        "container_port": APP_CONTAINER_PORT,
        "secrets": {
            "env_file": str(APP_ENV_FILE),
            "env_file_present": APP_ENV_FILE.exists(),
            "secret_env_keys": APP_SECRET_ENV_KEYS,
            "required_secret_keys": APP_REQUIRED_SECRET_KEYS,
            "configured_secret_keys": configured_secret_keys,
            "missing_required_secret_keys": missing_required_secret_keys,
            "required_secrets_configured": len(missing_required_secret_keys) == 0,
        },
        "deploy_status": {
            "status": "enabled" if APP_ENABLED else "disabled",
            "deployment_state": deployment_state,
            "last_deployment_result": last_deployment_result,
            "container_state": container_state,
            "container_health": container_health,
            "container_ports": container_ports,
            "compose_project": compose_project,
        },
        "routes": {
            "staged": {
                "enabled": APP_STAGED_ROUTE_ENABLED,
                "path": APP_ROUTE_PATH,
                "health_url": staged_health_url,
                "health": staged_probe,
            },
            "public": {
                "enabled": APP_PUBLIC_ROUTE_ENABLED,
                "domain": APP_PUBLIC_DOMAIN,
                "health_url": public_health_url,
                "health": public_probe,
            },
        },
        "marker": marker,
    }
    state["release_gate"] = release_gate_state(state, marker)
    return state


def gate_timestamp_state(expires_at: Any) -> str:
    parsed = parse_timestamp(expires_at)
    if not parsed:
        return "unknown"
    if datetime.now(timezone.utc) >= parsed:
        return "expired"
    return "current"


def release_gate_state(app: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
    expected = app.get("expected", {})
    actual = app.get("actual", {})
    if not isinstance(expected, dict):
        expected = {}
    if not isinstance(actual, dict):
        actual = {}
    marker_digest = str(marker.get("image_digest", "")).strip()
    marker_source = str(marker.get("source_commit", "")).strip()
    marker_build = str(marker.get("build_id", "")).strip()
    expected_digest = str(expected.get("image_digest", "")).strip()
    expected_source = str(expected.get("source_commit", "")).strip()
    expected_build = str(expected.get("build_id", "")).strip()
    qualification_run = str(marker.get("qualification_run_id", "")).strip()
    qualification_expires = str(marker.get("qualification_expires_at", "")).strip()
    staging_deployment_id = str(marker.get("staging_deployment_id") or marker.get("deployment_id") or "").strip()
    if not expected_digest:
        candidate_state = "not configured"
    elif marker_digest and marker_digest != expected_digest:
        candidate_state = "failed"
    elif marker_source and expected_source and marker_source != expected_source:
        candidate_state = "failed"
    elif marker_build and expected_build and marker_build != expected_build:
        candidate_state = "failed"
    else:
        candidate_state = "configured"

    if not expected_digest:
        qualification_state = "not configured"
    elif not qualification_run:
        qualification_state = "unknown"
    elif gate_timestamp_state(qualification_expires) == "expired":
        qualification_state = "expired"
    elif candidate_state == "failed":
        qualification_state = "failed"
    else:
        qualification_state = "passed"

    if qualification_state in {"passed", "expired"} and not staging_deployment_id:
        supersession_state = "unknown"
    elif qualification_state == "passed":
        supersession_state = "current"
    elif qualification_state == "expired":
        supersession_state = "expired"
    else:
        supersession_state = "unknown"

    return {
        "mode": "read-only",
        "state_catalog": ["unknown", "not configured", "failed", "expired", "superseded", "current", "passed"],
        "candidate": {
            "state": candidate_state,
            "image_digest": expected_digest,
            "source_commit": expected_source,
            "build_id": expected_build,
            "source_workflow_run_id": str(marker.get("source_workflow_run_id", "")).strip(),
            "config_generation": str(expected.get("config_generation", "")).strip()
            or str(marker.get("config_generation", "")).strip(),
            "test_suite_commit": marker_source or expected_source,
        },
        "staging": {
            "deployment_id": staging_deployment_id,
            "health_state": "unknown",
            "ready_state": "unknown",
            "supersession_state": supersession_state,
        },
        "qualification": {
            "state": qualification_state,
            "run_id": qualification_run,
            "expires_at": qualification_expires,
            "time_state": gate_timestamp_state(qualification_expires),
        },
        "production": {
            "expected_digest": expected_digest,
            "running_digest": str(actual.get("running_repo_digest", "")).strip(),
            "source_commit": expected_source,
            "running_source_commit": str(actual.get("source_commit", "")).strip(),
            "build_id": expected_build,
            "running_build_id": str(actual.get("build_id", "")).strip(),
            "promotion_run_id": str(marker.get("promotion_run_id", "")).strip(),
            "promotion_run_url": str(marker.get("promotion_run_url", "")).strip(),
            "promoted_at": str(marker.get("recorded_at", "")).strip(),
            "previous_digest": str(expected.get("last_known_good_digest", "")).strip(),
        },
        "rollback": {
            "state": str(marker.get("rollback_state", "")).strip() or "not configured",
            "previous_digest": str(expected.get("last_known_good_digest", "")).strip(),
        },
    }


def gitops_state() -> dict[str, Any]:
    deployed_commit = read_text(DEPLOYED_COMMIT_FILE, "unknown")
    apply_status = read_json(APPLY_STATUS_FILE, {"status": "unknown", "run_url": ""})
    return {
        "repository": INFRA_REPO_URL,
        "deployed_commit": deployed_commit,
        "last_apply": apply_status,
        "workflow_links": [
            {
                "name": "Protected Ansible Apply",
                "url": f"{INFRA_REPO_URL}/actions/workflows/protected-ansible-apply.yml",
            },
            {
                "name": "Send VPS Health Report",
                "url": f"{INFRA_REPO_URL}/actions/workflows/send-vps-health-report.yml",
            },
            {
                "name": "Run VPS Backup",
                "url": f"{INFRA_REPO_URL}/actions/workflows/run-vps-backup.yml",
            },
            {
                "name": "Verify VPS Backup",
                "url": f"{INFRA_REPO_URL}/actions/workflows/verify-vps-backup.yml",
            },
            {"name": "Pull requests", "url": f"{INFRA_REPO_URL}/pulls"},
            {"name": "Actions", "url": f"{INFRA_REPO_URL}/actions"},
        ],
        "drift_warning": "The portal reads local state only. Drift still has to be reconciled by PR and protected apply.",
    }


def runbook_links() -> list[dict[str, str]]:
    return [
        {"name": "Infrastructure operations guide", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_OPERATIONS_PORTAL_V1.md"},
        {"name": "Protected Ansible apply", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_PROTECTED_ANSIBLE_APPLY.md"},
        {"name": "VPS service foundation", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_VPS_SERVICE_FOUNDATION.md"},
        {"name": "VPS backup setup", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_VPS_BACKUPS.md"},
        {"name": "VPS restore", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_VPS_RESTORE.md"},
        {"name": "VPS disaster recovery", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_VPS_DISASTER_RECOVERY.md"},
        {"name": "Infra operations platform", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_INFRA_OPERATIONS_PLATFORM.md"},
    ]


def collect() -> dict[str, Any]:
    resources = resource_state()
    docker = docker_state()
    services = [
        systemd_status(name)
        for name in [
            "ssh.service",
            "docker.service",
            "unattended-upgrades.service",
            "ufw.service",
            "fail2ban.service",
            "crowdsec.service",
            "nutsnews-ops-portal-collector.timer",
            "nutsnews-ops-alert-check.timer",
            "nutsnews-ops-health-report.timer",
            "nutsnews-restic-backup.timer",
            "nutsnews-restic-backup.service",
            "nutsnews-restic-verify.timer",
            "nutsnews-restic-verify.service",
        ]
    ]
    reporting = reporting_state()
    reporting.update(systemd_timer_schedule("nutsnews-ops-health-report.timer"))
    backups = cached_slow_section("backups", section_ttl("backups"), backup_state)
    app = app_state(docker)
    free_tier = free_tier_usage_state()
    external_free_tier_providers = free_tier.get("providers", [])
    if not isinstance(external_free_tier_providers, list):
        external_free_tier_providers = []
    local_free_tier = cached_slow_section(
        "free_tier_local",
        section_ttl("free_tier_local"),
        lambda: {"providers": local_usage_providers(resources, docker, backups)},
    )
    local_free_tier_providers = local_free_tier.get("providers", [])
    if not isinstance(local_free_tier_providers, list):
        local_free_tier_providers = []
    free_tier["providers"] = local_free_tier_providers + external_free_tier_providers
    free_tier["local_cache"] = local_free_tier.get("_collector_cache", {})
    free_tier["summary"] = summarize_free_tier_usage(free_tier)
    observability = cached_slow_section(
        "observability",
        section_ttl("observability"),
        observability_state,
    )
    processes = cached_slow_section("processes", section_ttl("processes"), process_state)
    logs = cached_slow_section("logs", section_ttl("logs"), log_sections)
    security = cached_slow_section("security", section_ttl("security"), security_state)
    collector = {
        "runtime_seconds": round(time.monotonic() - COLLECTOR_STARTED_MONOTONIC, 3),
        "timer_cadence_seconds": COLLECTOR_TIMER_CADENCE_SECONDS,
        "slow_cache_file": str(SLOW_CACHE_FILE),
        "slow_section_ttls": SLOW_SECTION_TTLS,
        "slow_sections": CACHE_EVENTS,
    }

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "collector": collector,
        "portal": {
            "mode": "read-only",
            "public_exposure": "Caddy serves https://ops.nutsnews.com through Google OAuth and keeps 127.0.0.1:8080 for health checks and SSH tunnel fallback.",
            "management_policy": "Mutating actions must go through PR, CI, merge, and protected apply.",
        },
        "host": {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "uptime_seconds": uptime_seconds(),
            "public_ipv4": os.environ.get("NUTSNEWS_PUBLIC_IPV4", "unknown"),
            "public_ipv6": os.environ.get("NUTSNEWS_PUBLIC_IPV6", "unknown"),
            "local_addresses": local_ip_addresses(),
            "os": parse_os_release(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
        },
        "resources": resources,
        "processes": processes,
        "disk_usage": cached_disk_hotspots(),
        "process_network": process_network_state(),
        "docker": docker,
        "services": services,
        "logs": logs,
        "security": security,
        "backups": backups,
        "observability": observability,
        "free_tier_usage": free_tier,
        "email_reporting": reporting,
        "alerts": {
            "email_configuration": reporting.get("status", "disabled"),
            "items": alert_state(resources, docker, services, backups, free_tier, observability),
        },
        "gitops": gitops_state(),
        "app": app,
        "app_links": [
            {
                "name": "Dual-target web deployment",
                "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_DUAL_TARGET_WEB_DEPLOYMENT.md",
            },
            {"name": "NutsNews app layer setup", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_VPS_SERVICE_FOUNDATION.md#nutsnews-app-layer"},
            {"name": "Ops Portal app state", "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_OPERATIONS_PORTAL_V1.md#app-layer"},
            {
                "name": "Protected app rollout",
                "url": f"{DOCS_BASE_URL}/blob/main/NUTSNEWS_PROTECTED_ANSIBLE_APPLY.md#nutsnews-app-rollout-path",
            },
            {"name": "Rollback app rollout", "url": f"{DOCS_BASE_URL}/blob/main/TROUBLESHOOTING.md#nutsnews-app-rollback"},
            {"name": "Troubleshoot app rollout", "url": f"{DOCS_BASE_URL}/blob/main/TROUBLESHOOTING.md#nutsnews-app-rollout"},
        ],
        "runbooks": runbook_links(),
    }


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = collect()
    tmp_file = OUTPUT_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_file.replace(OUTPUT_FILE)
    OUTPUT_FILE.chmod(0o644)


if __name__ == "__main__":
    main()
