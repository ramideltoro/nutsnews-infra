#!/usr/bin/env python3
"""Collect read-only VPS status for the NutsNews Operations Portal."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRIVATE_KEY_LINE_PATTERN = ".*PRIVATE" + r"\s+" + "KEY.*"

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|authorization|credential|api[_-]?key)=\S+"),
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
DOCS_BASE_URL = os.environ.get("NUTSNEWS_DOCS_BASE_URL", "https://github.com/ramideltoro/nutsnews-docs")
INFRA_REPO_URL = os.environ.get("NUTSNEWS_INFRA_REPO_URL", "https://github.com/ramideltoro/nutsnews-infra")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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

    return {
        "directory": str(BACKUPS_DIR),
        "size_bytes": size_bytes,
        "latest": latest,
        "latest_status": "placeholder",
        "snapshot_reminder": "Provider snapshots and encrypted offsite backups are planned but not managed by this portal yet.",
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


def alert_state(resources: dict[str, Any], docker: dict[str, Any], services: list[dict[str, str]]) -> list[dict[str, str]]:
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

    if not alerts:
        alerts.append({"level": "ok", "message": "No local threshold alerts from the current snapshot."})
    return alerts


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
            {"name": "Pull requests", "url": f"{INFRA_REPO_URL}/pulls"},
            {"name": "Actions", "url": f"{INFRA_REPO_URL}/actions"},
        ],
        "drift_warning": "The portal reads local state only. Drift still has to be reconciled by PR and protected apply.",
    }


def runbook_links() -> list[dict[str, str]]:
    return [
        {"name": "Infrastructure operations guide", "url": f"{DOCS_BASE_URL}/blob/main/infra/operations-portal-v1.md"},
        {"name": "Protected Ansible apply", "url": f"{DOCS_BASE_URL}/blob/main/infra/protected-ansible-apply.md"},
        {"name": "VPS service foundation", "url": f"{DOCS_BASE_URL}/blob/main/infra/vps-service-foundation.md"},
        {"name": "Operations charter", "url": f"{DOCS_BASE_URL}/blob/main/infra/operations-charter.md"},
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
        ]
    ]

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "portal": {
            "mode": "read-only",
            "public_exposure": "Caddy binds the portal to host loopback only on 127.0.0.1:8080.",
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
        "docker": docker,
        "services": services,
        "logs": log_sections(),
        "security": security_state(),
        "backups": backup_state(),
        "alerts": {
            "email_configuration": "placeholder",
            "items": alert_state(resources, docker, services),
        },
        "gitops": gitops_state(),
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
