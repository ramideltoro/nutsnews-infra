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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ops_free_tier_usage import collect_free_tier_usage
except ImportError:
    collect_free_tier_usage = None


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
APP_ROUTE_ENABLED = os.environ.get("NUTSNEWS_APP_ROUTE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
APP_CONTAINER_NAME = os.environ.get("NUTSNEWS_APP_CONTAINER_NAME", "nutsnews-app").strip()
APP_CONTAINER_PORT = int(os.environ.get("NUTSNEWS_APP_CONTAINER_PORT", "3000") or 0)
APP_IMAGE = os.environ.get("NUTSNEWS_APP_IMAGE", "").strip()
APP_IMAGE_REPO = os.environ.get("NUTSNEWS_APP_IMAGE_REPO", "").strip()
APP_IMAGE_TAG = os.environ.get("NUTSNEWS_APP_IMAGE_TAG", "").strip()
APP_HEALTH_PATH = os.environ.get("NUTSNEWS_APP_HEALTH_PATH", "/healthz").strip() or "/healthz"
APP_ROUTE_PATH = os.environ.get("NUTSNEWS_APP_ROUTE_PATH", "/app-stage").strip() or "/app-stage"
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
DOCS_BASE_URL = os.environ.get("NUTSNEWS_DOCS_BASE_URL", "https://github.com/ramideltoro/nutsnews-docs")
INFRA_REPO_URL = os.environ.get("NUTSNEWS_INFRA_REPO_URL", "https://github.com/ramideltoro/nutsnews-infra")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value: Any) -> datetime | None:
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
    return parsed


def age_seconds(value: Any) -> int | None:
    parsed = parse_timestamp(value)
    if not parsed:
        return None
    return max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)


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


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def app_health_url() -> str:
    if not APP_ENABLED or not APP_ROUTE_ENABLED:
        return ""
    route_path = APP_ROUTE_PATH.strip()
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    route_path = route_path.rstrip("/")
    health_path = APP_HEALTH_PATH.strip()
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    return f"http://127.0.0.1:8080{route_path}{health_path}"


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
            containers.append(
                {
                    "name": name,
                    "image": item.get("Image", ""),
                    "state": item.get("State", ""),
                    "status": item.get("Status", ""),
                    "ports": item.get("Ports", ""),
                    "health": "unknown",
                    "restart_count": 0,
                    "compose_project": item.get("Label", ""),
                }
            )

    if names:
        inspect = run(["docker", "inspect", *names], timeout=10)
        if inspect["ok"]:
            try:
                inspected = json.loads(inspect["stdout"])
            except json.JSONDecodeError:
                inspected = []
            details = {item.get("Name", "").lstrip("/"): item for item in inspected}
            for container in containers:
                detail = details.get(container["name"], {})
                state = detail.get("State", {})
                labels = detail.get("Config", {}).get("Labels", {}) or {}
                container["restart_count"] = detail.get("RestartCount", 0)
                container["health"] = state.get("Health", {}).get("Status", "none")
                container["compose_project"] = labels.get("com.docker.compose.project", "")

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
        "available": ps["ok"],
        "error": "" if ps["ok"] else ps["stderr"],
        "containers": containers,
        "compose_projects": compose_projects,
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
    size_bytes = 0
    if usage["ok"] and usage["stdout"].strip():
        try:
            size_bytes = int(usage["stdout"].split()[0])
        except (IndexError, ValueError):
            size_bytes = 0

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
        "missing_configuration": [],
        "backup_paths": [],
        "missing_paths": [],
        "security_model": "restic encrypts snapshots locally before rclone transports ciphertext to OneDrive.",
        "snapshot_reminder": "Encrypted restic snapshots go to OneDrive through the dedicated nutsnews-onedrive rclone remote.",
    }
    data = read_json(BACKUP_STATUS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    combined = {**default, **data}
    combined["directory"] = str(BACKUPS_DIR)
    combined["size_bytes"] = size_bytes
    combined["latest"] = latest
    combined["status_file"] = str(BACKUP_STATUS_FILE)
    combined.update(systemd_timer_schedule("nutsnews-restic-backup.timer"))
    combined["backup_service"] = systemd_status("nutsnews-restic-backup.service")
    combined["verify_service"] = systemd_status("nutsnews-restic-verify.service")

    snapshot = combined.get("latest_snapshot")
    if isinstance(snapshot, dict) and combined.get("latest_snapshot_age_seconds") is None:
        combined["latest_snapshot_age_seconds"] = age_seconds(snapshot.get("time"))
    if combined.get("enabled") and combined.get("latest_snapshot_age_seconds") is not None:
        combined["latest_status"] = (
            "fresh"
            if safe_int(combined.get("latest_snapshot_age_seconds")) <= safe_int(combined.get("stale_after_seconds"), 108000)
            else "stale"
        )
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


def resource_state() -> dict[str, Any]:
    mem = meminfo()
    memory_total = mem.get("MemTotal", 0)
    memory_available = mem.get("MemAvailable", 0)
    memory_used = max(memory_total - memory_available, 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    return {
        "cpu_percent": cpu_percent(),
        "load_average": load_average(),
        "memory": {
            "total_bytes": memory_total,
            "used_bytes": memory_used,
            "available_bytes": memory_available,
            "used_percent": percent(memory_used, memory_total),
        },
        "swap": {
            "total_bytes": swap_total,
            "used_bytes": swap_used,
            "free_bytes": swap_free,
            "used_percent": percent(swap_used, swap_total),
        },
        "disk": disk_usage(Path("/")),
        "nutsnews_disk": disk_usage(ROOT_DIR),
        "network": network_usage(),
    }


def alert_state(
    resources: dict[str, Any],
    docker: dict[str, Any],
    services: list[dict[str, str]],
    backups: dict[str, Any],
) -> list[dict[str, str]]:
    alerts = []
    disk = resources.get("disk", {})
    memory = resources.get("memory", {})
    swap = resources.get("swap", {})

    if disk.get("used_percent", 0) >= 85:
        alerts.append({"level": "warning", "message": "Root disk usage is above 85 percent."})
    if disk.get("inode_used_percent", 0) >= 85:
        alerts.append({"level": "warning", "message": "Root inode usage is above 85 percent."})
    if memory.get("used_percent", 0) >= 90:
        alerts.append({"level": "warning", "message": "Memory usage is above 90 percent."})
    if swap.get("used_percent", 0) >= 50:
        alerts.append({"level": "warning", "message": "Swap usage is above 50 percent."})

    unhealthy = [
        container["name"]
        for container in docker.get("containers", [])
        if container.get("health") not in ("healthy", "none", "unknown", "")
    ]
    if unhealthy:
        alerts.append({"level": "critical", "message": "Unhealthy containers: " + ", ".join(unhealthy)})

    inactive = [
        service["name"]
        for service in services
        if service["name"] in ("ssh.service", "docker.service", "fail2ban.service")
        and service.get("active") not in ("active", "activating")
    ]
    if inactive:
        alerts.append({"level": "critical", "message": "Important services are not active: " + ", ".join(inactive)})

    if backups.get("enabled"):
        if not backups.get("configured"):
            missing = ", ".join(backups.get("missing_configuration", [])) or "backup secrets"
            alerts.append({"level": "critical", "message": f"VPS backups are enabled but misconfigured: {missing}."})

        backup_status = str(backups.get("last_backup", {}).get("status", "")).lower()
        prune_status = str(backups.get("last_prune", {}).get("status", "")).lower()
        check_status = str(backups.get("last_check", {}).get("status", "")).lower()
        latest_status = str(backups.get("latest_status", "")).lower()
        latest_age = backups.get("latest_snapshot_age_seconds")
        stale_after = safe_int(backups.get("stale_after_seconds"), 108000)

        if backup_status == "failed":
            alerts.append({"level": "critical", "message": "The latest VPS restic backup failed."})
        if prune_status == "failed":
            alerts.append({"level": "warning", "message": "The latest VPS restic prune failed after backup."})
        if check_status == "failed":
            alerts.append({"level": "warning", "message": "The latest VPS backup verification failed."})
        if backups.get("timer_active") not in ("active", "activating"):
            alerts.append({"level": "warning", "message": "The VPS backup timer is not active."})
        if not backups.get("latest_snapshot"):
            alerts.append({"level": "warning", "message": "No VPS restic backup snapshot is available yet."})
        elif latest_status == "stale" or (isinstance(latest_age, int) and latest_age > stale_after):
            alerts.append({"level": "critical", "message": "The latest VPS restic backup snapshot is stale."})

    if not alerts:
        alerts.append({"level": "ok", "message": "No local threshold alerts from the current snapshot."})
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
        if container_state == "running":
            if container_health in ("healthy", "none"):
                deployment_state = "running"
            else:
                deployment_state = "started"
        else:
            deployment_state = "not_running"
    else:
        deployment_state = "disabled"

    app_image_repo = APP_IMAGE_REPO or APP_IMAGE
    app_image_tag = APP_IMAGE_TAG
    if APP_IMAGE and ":" in APP_IMAGE and not APP_IMAGE_TAG:
        app_image_repo = APP_IMAGE.rsplit(":", 1)[0]
        app_image_tag = APP_IMAGE.rsplit(":", 1)[1]
    if not app_image_tag:
        app_image_tag = "latest"

    if APP_ROUTE_ENABLED and APP_ENABLED and deployment_state == "running":
        route_state = "staged"
    elif APP_ROUTE_ENABLED and APP_ENABLED:
        route_state = "pending"
    else:
        route_state = "disabled"

    if not APP_ROUTE_ENABLED:
        route_health_status = "disabled"
    elif container_state != "running":
        route_health_status = "not_running"
    elif container_health in ("healthy", "none"):
        route_health_status = "ready"
    else:
        route_health_status = "not_ready"

    return {
        "enabled": APP_ENABLED,
        "route_enabled": APP_ROUTE_ENABLED,
        "route_path": APP_ROUTE_PATH,
        "health_path": APP_HEALTH_PATH,
        "image": APP_IMAGE,
        "image_repo": app_image_repo,
        "image_tag": app_image_tag,
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
            "container_state": container_state,
            "container_health": container_health,
            "container_ports": container_ports,
            "compose_project": compose_project,
        },
        "routing": {
            "status": route_state,
            "health_url": app_health_url() if APP_ROUTE_ENABLED else "",
            "health_status": route_health_status,
        },
        "marker": marker,
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
            "nutsnews-restic-verify.service",
        ]
    ]
    reporting = reporting_state()
    reporting.update(systemd_timer_schedule("nutsnews-ops-health-report.timer"))
    backups = backup_state()
    app = app_state(docker)

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "portal": {
            "mode": "read-only",
            "public_exposure": "Caddy binds the portal to host loopback only on 127.0.0.1:8080 and routes dashboard access through Google OAuth.",
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
        "processes": process_state(),
        "disk_usage": cached_disk_hotspots(),
        "process_network": process_network_state(),
        "docker": docker,
        "services": services,
        "logs": log_sections(),
        "security": security_state(),
        "backups": backups,
        "free_tier_usage": free_tier_usage_state(),
        "email_reporting": reporting,
        "alerts": {
            "email_configuration": reporting.get("status", "disabled"),
            "items": alert_state(resources, docker, services, backups),
        },
        "gitops": gitops_state(),
        "app": app,
        "app_links": [
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
