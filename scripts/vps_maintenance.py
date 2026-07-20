#!/usr/bin/env python3
"""Run fixed NutsNews VPS maintenance checks and actions.

This script is intended to be streamed by the protected maintenance workflow to
the production VPS and executed with sudo. It intentionally exposes only a small
set of fixed modes and prints sanitized status summaries.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_STATUS_FILE = Path("/opt/nutsnews/portal-assets/data/backup-status.json")
REBOOT_REQUIRED_FILE = Path("/var/run/reboot-required")
REBOOT_REQUIRED_PACKAGES_FILE = Path("/var/run/reboot-required.pkgs")
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")

REQUIRED_CONTAINERS = ("nutsnews-caddy", "nutsnews-ops-auth")
APP_CONTAINERS = ("nutsnews-app",)
APP_READYZ_URL = "http://127.0.0.1:3000/readyz"
LOCAL_HEALTH_URL = "http://127.0.0.1:8080/healthz"
PUBLIC_HEALTH_URL = "https://vps.nutsnews.com/health"
OPS_PORTAL_URL = "https://ops.nutsnews.com/"

SENSITIVE_TEXT_PATTERNS = (
    (
        re.compile(r"(?i)(postgres(?:ql)?://)[^\s@/]+(?::[^\s@/]*)?@"),
        r"\1<redacted>@",
    ),
    (
        re.compile(
            r"(?i)\b((?:DATABASE|POSTGRES|SUPABASE|NUTSNEWS|AUTH|JWT|API|ACCESS|REFRESH)"
            r"_[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|URL)|TOKEN|PASSWORD|SECRET)=([^\s,;]+)"
        ),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/\-]+=*"), r"\1<redacted>"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run(argv: list[str], timeout: int = 20, env: dict[str, str] | None = None) -> dict[str, Any]:
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
            "returncode": 127,
            "stdout": "",
            "stderr": f"{argv[0]} not found",
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": "",
            "stderr": "command timed out",
            "duration_seconds": round(time.monotonic() - started, 3),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def sanitize_text(value: str, limit: int = 6000) -> str:
    sanitized = value.replace("\r", "")
    for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if len(sanitized) > limit:
        return sanitized[-limit:]
    return sanitized


def safe_package_names(path: Path, limit: int = 12) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    names = []
    for line in lines:
        name = line.strip()
        if re.fullmatch(r"[A-Za-z0-9_.:+~-]+", name):
            names.append(name)
    return names[:limit]


def boot_id() -> str:
    try:
        value = BOOT_ID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value if re.fullmatch(r"[0-9a-fA-F-]{32,36}", value) else "unknown"


def parse_upgradable_packages() -> dict[str, Any]:
    result = run(["apt", "list", "--upgradable"], timeout=20)
    if not result["ok"]:
        return {
            "available": False,
            "count": 0,
            "informational_count": 0,
            "security_count": 0,
            "error": "apt list failed",
        }
    lines = [line for line in result["stdout"].splitlines() if "/" in line and not line.startswith("Listing")]
    security_lines = [line for line in lines if "security" in line.lower()]
    return {
        "available": True,
        "count": len(lines),
        "informational_count": max(len(lines) - len(security_lines), 0),
        "security_count": len(security_lines),
    }


def read_backup_status() -> dict[str, Any]:
    try:
        data = json.loads(BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "status": "missing"}

    verification = data.get("latest_snapshot_verification")
    if not isinstance(verification, dict):
        verification = {}

    return {
        "available": True,
        "enabled": data.get("enabled"),
        "configured": data.get("configured"),
        "status": data.get("status"),
        "latest_status": data.get("latest_status"),
        "latest_snapshot_present": bool(data.get("latest_snapshot")),
        "latest_snapshot_age_seconds": data.get("latest_snapshot_age_seconds"),
        "stale_after_seconds": data.get("stale_after_seconds"),
        "last_backup_status": (data.get("last_backup") or {}).get("status"),
        "last_prune_status": (data.get("last_prune") or {}).get("status"),
        "verification_status": verification.get("status") or data.get("verification_status"),
        "verification_policy_status": verification.get("policy_status"),
        "verification_overdue": verification.get("overdue"),
    }


def backup_is_fresh(backups: dict[str, Any]) -> tuple[bool, str]:
    if not backups.get("available"):
        return False, "backup status is unavailable"
    if backups.get("enabled") is not True or backups.get("configured") is not True:
        return False, "backups are not enabled and configured"
    if backups.get("latest_snapshot_present") is not True:
        return False, "latest backup snapshot is missing"
    if backups.get("latest_status") != "fresh":
        return False, "latest backup snapshot is not fresh"
    if backups.get("last_backup_status") != "success":
        return False, "latest backup run did not succeed"
    if backups.get("last_prune_status") not in {"success", "unknown", None}:
        return False, "latest backup prune did not succeed"
    if backups.get("verification_status") == "failed" or backups.get("verification_overdue") is True:
        return False, "backup verification is failed or overdue"
    return True, "backup freshness policy passed"


def curl_status(url: str, timeout: int = 12) -> dict[str, Any]:
    result = run(
        ["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout), url],
        timeout=timeout + 4,
    )
    code = result["stdout"].strip()
    return {"ok": result["ok"], "status_code": code or "000"}


def docker_container_summary(name: str) -> dict[str, Any]:
    result = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Name}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            name,
        ],
        timeout=10,
    )
    if not result["ok"]:
        return {"name": name, "running": False, "health": "missing"}
    parts = result["stdout"].strip().split("|")
    return {
        "name": name,
        "running": len(parts) > 1 and parts[1] == "true",
        "health": parts[2] if len(parts) > 2 and parts[2] else "unknown",
    }


def docker_container_http_probe(name: str, url: str) -> dict[str, Any]:
    node_script = (
        "const http=require('http');"
        "const target=process.argv[1];"
        "const req=http.get(target,(res)=>{"
        "let body='';"
        "res.setEncoding('utf8');"
        "res.on('data',(chunk)=>{if(body.length<6000) body+=chunk;});"
        "res.on('end',()=>{"
        "console.log(JSON.stringify({status_code:res.statusCode,body:body.slice(0,6000)}));"
        "});"
        "});"
        "req.setTimeout(5000,()=>req.destroy(new Error('timeout')));"
        "req.on('error',(err)=>{console.log(JSON.stringify({error:err.message||String(err)}));});"
    )
    result = run(["docker", "exec", name, "node", "-e", node_script, url], timeout=10)
    probe: dict[str, Any] = {
        "url": url,
        "returncode": result["returncode"],
        "command_ok": result["ok"],
    }
    stdout = result["stdout"].strip()
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            data = json.loads(last_line)
        except json.JSONDecodeError:
            probe["stdout"] = sanitize_text(stdout, limit=3000)
        else:
            if "body" in data:
                data["body"] = sanitize_text(str(data["body"]), limit=3000)
            probe.update(data)
    if result["stderr"].strip():
        probe["stderr"] = sanitize_text(result["stderr"], limit=3000)
    return probe


def docker_container_diagnostics(name: str) -> dict[str, Any]:
    summary = docker_container_summary(name)

    state = run(["docker", "inspect", "--format", "{{json .State}}", name], timeout=10)
    if state["ok"] and state["stdout"].strip():
        try:
            state_data = json.loads(state["stdout"])
        except json.JSONDecodeError:
            state_data = {}
        health = state_data.get("Health") if isinstance(state_data, dict) else {}
        health_log = health.get("Log") if isinstance(health, dict) else []
        if isinstance(health_log, list):
            summary["health_failing_streak"] = health.get("FailingStreak")
            summary["health_log"] = [
                {
                    "exit_code": entry.get("ExitCode"),
                    "started_at": entry.get("Start"),
                    "ended_at": entry.get("End"),
                    "output": sanitize_text(str(entry.get("Output", "")), limit=1200),
                }
                for entry in health_log[-5:]
                if isinstance(entry, dict)
            ]
    else:
        summary["inspect_error"] = sanitize_text(state["stderr"] or state["stdout"], limit=1200)

    healthcheck = run(["docker", "inspect", "--format", "{{json .Config.Healthcheck}}", name], timeout=10)
    if healthcheck["ok"] and healthcheck["stdout"].strip():
        try:
            summary["healthcheck"] = json.loads(healthcheck["stdout"])
        except json.JSONDecodeError:
            summary["healthcheck"] = sanitize_text(healthcheck["stdout"], limit=1200)

    summary["readyz_probe"] = docker_container_http_probe(name, APP_READYZ_URL)

    logs = run(["docker", "logs", "--tail", "120", "--since", "20m", name], timeout=15)
    log_text = sanitize_text((logs["stdout"] or "") + (logs["stderr"] or ""), limit=6000)
    summary["recent_logs_returncode"] = logs["returncode"]
    summary["recent_logs"] = [line for line in log_text.splitlines() if line][-120:]
    return summary


def system_summary() -> dict[str, Any]:
    failed = run(["systemctl", "--failed", "--plain", "--no-legend"], timeout=10)
    failed_lines = [line for line in failed["stdout"].splitlines() if line.strip()]
    running = run(["systemctl", "is-system-running"], timeout=10)
    kernel = run(["uname", "-r"], timeout=5)
    return {
        "system_state": running["stdout"].strip() or "unknown",
        "failed_units_count": len(failed_lines),
        "running_kernel": kernel["stdout"].strip() or "unknown",
        "boot_id": boot_id(),
        "reboot_required": REBOOT_REQUIRED_FILE.exists(),
        "reboot_required_packages": safe_package_names(REBOOT_REQUIRED_PACKAGES_FILE),
    }


def collect_status() -> dict[str, Any]:
    backups = read_backup_status()
    backup_ok, backup_detail = backup_is_fresh(backups)
    local_health = curl_status(LOCAL_HEALTH_URL)
    public_health = curl_status(PUBLIC_HEALTH_URL)
    ops_portal = curl_status(OPS_PORTAL_URL)
    containers = [docker_container_summary(name) for name in REQUIRED_CONTAINERS]
    app_containers = [docker_container_diagnostics(name) for name in APP_CONTAINERS]
    return {
        "schema_version": 1,
        "collected_at": utc_now(),
        "system": system_summary(),
        "package_updates": parse_upgradable_packages(),
        "backups": backups,
        "backup_fresh": backup_ok,
        "backup_fresh_detail": backup_detail,
        "health": {
            "local_caddy_healthz_status": local_health["status_code"],
            "local_caddy_healthz_ok": local_health["ok"],
            "public_health_status": public_health["status_code"],
            "public_health_ok": public_health["ok"] and public_health["status_code"] == "200",
            "ops_portal_status": ops_portal["status_code"],
            "ops_portal_auth_redirect_ok": ops_portal["status_code"] in {"200", "302", "303"},
        },
        "docker": {"required_containers": containers, "app_containers": app_containers},
    }


def require_preflight(status: dict[str, Any]) -> None:
    errors = []
    system = status["system"]
    health = status["health"]
    containers = status["docker"]["required_containers"]
    app_containers = status["docker"].get("app_containers", [])
    if system["system_state"] != "running":
        errors.append(f"system state is {system['system_state']}")
    if system["failed_units_count"] != 0:
        errors.append(f"{system['failed_units_count']} failed systemd unit(s)")
    if not status["backup_fresh"]:
        errors.append(status["backup_fresh_detail"])
    if not health["local_caddy_healthz_ok"]:
        errors.append("local Caddy health check failed")
    if not health["public_health_ok"]:
        errors.append("public /health check failed")
    if not health["ops_portal_auth_redirect_ok"]:
        errors.append("Ops Portal auth redirect check failed")
    bad_containers = [
        f"{item['name']}:{item['health']}"
        for item in containers
        if not item["running"] or item["health"] not in {"healthy", "none"}
    ]
    if bad_containers:
        errors.append("required Docker containers unhealthy: " + ", ".join(bad_containers))
    bad_app_containers = [
        f"{item['name']}:{item['health']}"
        for item in app_containers
        if not item["running"] or item["health"] not in {"healthy", "none"}
    ]
    if bad_app_containers:
        errors.append("app Docker containers unhealthy: " + ", ".join(bad_app_containers))
    if errors:
        raise SystemExit("Preflight failed: " + "; ".join(errors))


def print_status(status: dict[str, Any]) -> None:
    print(json.dumps(status, indent=2, sort_keys=True))


def run_package_maintenance(confirm: str) -> None:
    if confirm != "apply-package-maintenance":
        raise SystemExit("Package maintenance requires confirm=apply-package-maintenance.")
    before = collect_status()
    require_preflight(before)
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    update = run(["apt-get", "update", "-qq"], timeout=600, env=env)
    upgrade = run(
        [
            "apt-get",
            "-y",
            "-o",
            "Dpkg::Options::=--force-confold",
            "upgrade",
        ],
        timeout=2400,
        env=env,
    )
    after = collect_status()
    result = {
        "mode": "package-maintenance",
        "started_at": before["collected_at"],
        "completed_at": utc_now(),
        "apt_update_returncode": update["returncode"],
        "apt_upgrade_returncode": upgrade["returncode"],
        "before": {
            "package_updates": before["package_updates"],
            "reboot_required": before["system"]["reboot_required"],
        },
        "after": {
            "package_updates": after["package_updates"],
            "reboot_required": after["system"]["reboot_required"],
            "reboot_required_packages": after["system"]["reboot_required_packages"],
        },
    }
    print_status(result)
    if not update["ok"] or not upgrade["ok"]:
        raise SystemExit("Package maintenance failed.")


def run_reboot(confirm: str) -> None:
    if confirm != "reboot-vps.nutsnews.com":
        raise SystemExit("Reboot requires confirm=reboot-vps.nutsnews.com.")
    status = collect_status()
    require_preflight(status)
    print_status(
        {
            "mode": "reboot",
            "accepted_at": utc_now(),
            "boot_id_before": status["system"]["boot_id"],
            "reboot_required": status["system"]["reboot_required"],
            "backup_fresh": status["backup_fresh"],
            "health": status["health"],
        }
    )
    sys.stdout.flush()
    result = run(["systemctl", "reboot"], timeout=10)
    if not result["ok"]:
        raise SystemExit("systemctl reboot failed before the host started rebooting.")


def run_post_reboot(expected_boot_id: str) -> None:
    status = collect_status()
    require_preflight(status)
    current_boot_id = status["system"]["boot_id"]
    if expected_boot_id and current_boot_id == expected_boot_id:
        raise SystemExit("Post-reboot validation failed: boot ID did not change.")
    if status["system"]["reboot_required"]:
        raise SystemExit("Post-reboot validation failed: /var/run/reboot-required still exists.")
    print_status({"mode": "post-reboot", "validated_at": utc_now(), "status": status})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["preflight", "package-maintenance", "boot-id", "reboot", "post-reboot"],
        required=True,
    )
    parser.add_argument("--confirm", default="")
    parser.add_argument("--expected-boot-id", default="")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.mode == "boot-id":
        print(boot_id())
        return 0
    if args.mode == "preflight":
        status = collect_status()
        print_status(status)
        require_preflight(status)
        return 0
    if args.mode == "package-maintenance":
        run_package_maintenance(args.confirm)
        return 0
    if args.mode == "reboot":
        run_reboot(args.confirm)
        return 0
    if args.mode == "post-reboot":
        run_post_reboot(args.expected_boot_id)
        return 0
    raise SystemExit(f"unsupported mode: {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
