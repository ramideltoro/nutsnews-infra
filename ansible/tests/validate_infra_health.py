#!/usr/bin/env python3
"""Validate the Better Stack-compatible infrastructure health endpoint wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(".")
HEALTH = (ROOT / "ansible/roles/vps_service_foundation/files/infra_health.py").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
CADDY = (ROOT / "compose/caddy/Caddyfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose/caddy/compose.yml").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "runbooks/VPS_SERVICE_FOUNDATION.md").read_text(encoding="utf-8")
LOGROTATE = (ROOT / "ansible/roles/vps_baseline/templates/logrotate_nutsnews.j2").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "THRESHOLD_PERCENT",
    "REQUIRED_SERVICES",
    "REQUIRED_CONTAINERS",
    "REQUIRED_DISKS",
    "health-failures.jsonl",
    "ThreadingHTTPServer",
    "503",
    "failed_checks",
):
    require(token in HEALTH, f"Health script missing {token}.")

for forbidden in ("os.environ)", "traceback", "password=", "token=", "authorization="):
    require(forbidden not in HEALTH.lower(), f"Health script includes forbidden detail: {forbidden}.")

for token in (
    "vps_service_foundation_infra_health_threshold_percent: 60",
    "vps_service_foundation_infra_health_required_services:",
    "vps_service_foundation_infra_health_required_containers:",
    "vps_service_foundation_infra_health_log_file:",
):
    require(token in DEFAULTS, f"Defaults missing {token}.")

for token in (
    "Install NutsNews infrastructure health endpoint",
    "Install NutsNews infrastructure health service",
    "Enable NutsNews infrastructure health service",
    "Wait for local infrastructure health endpoint",
):
    require(token in TASKS, f"Ansible tasks missing {token}.")

require("handle /health" in CADDY, "Caddy must expose /health.")
require("reverse_proxy host.docker.internal:18080" in CADDY, "Caddy must proxy /health to the host health service.")
require("vps.nutsnews.com" in CADDY, "Caddy must define the public VPS hostname.")
require("auto_https off" not in CADDY, "Caddy automatic HTTPS must remain enabled for the public hostname.")
require("host.docker.internal:host-gateway" in COMPOSE, "Compose must expose host-gateway to Caddy.")
require('"80:80/tcp"' in COMPOSE, "Compose must publish public HTTP for ACME and redirects.")
require('"443:443/tcp"' in COMPOSE, "Compose must publish public HTTPS for Better Stack.")
require("curl -i http://127.0.0.1:8080/health" in RUNBOOK, "Runbook must document local /health curl.")
require("https://vps.nutsnews.com/health" in RUNBOOK, "Runbook must document Better Stack URL.")
require("/opt/nutsnews/logs/health/*.jsonl" in LOGROTATE, "Health failure logs must be rotated.")

print("Infrastructure health endpoint guardrails passed.")
