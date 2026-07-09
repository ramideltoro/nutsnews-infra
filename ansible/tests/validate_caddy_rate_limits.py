#!/usr/bin/env python3
"""Validate Caddy rate-limit guardrails for the VPS edge."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(".")
DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "ansible/roles/vps_service_foundation/templates/caddy-rate-limits.j2").read_text(encoding="utf-8")
CADDYFILE = (ROOT / "compose/caddy/Caddyfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose/caddy/compose.yml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "compose/caddy/Dockerfile").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "runbooks/VPS_SERVICE_FOUNDATION.md").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "vps_service_foundation_caddy_rate_limits_enabled: true",
    "vps_service_foundation_caddy_rate_limit_key: \"{remote_host}\"",
    "vps_service_foundation_caddy_rate_limit_ipv6_prefix: 64",
    "name: nutsnews_health",
    "events: 30",
    "name: nutsnews_auth_sensitive",
    "events: 20",
    "name: nutsnews_api",
    "events: 60",
    "name: nutsnews_public",
    "events: 600",
):
    require(token in DEFAULTS, f"Defaults missing {token}.")

for token in (
    "Install Caddy image build file",
    "Validate Caddy rate limit inputs",
    "Validate Caddy rate limit zones",
    "Install Caddy rate limit config",
    "vps_service_foundation_caddy_rate_limit_install.changed",
    "--build",
):
    require(token in TASKS, f"Ansible tasks missing {token}.")

for token in ("rate_limit {", "zone {{ zone.name }}", "log_key", "ipv6_prefix"):
    require(token in TEMPLATE, f"Caddy rate-limit template missing {token}.")

require(CADDYFILE.count("import /etc/nutsnews/caddy/rate-limits") == 3, "Every Caddy server block must import rate limits.")
require(CADDYFILE.count("output stdout") == 3, "Every Caddy server block must log to stdout.")
require(CADDYFILE.count("format json") == 3, "Every Caddy server block must emit JSON logs for Loki parsing.")
require("github.com/mholt/caddy-ratelimit@${CADDY_RATELIMIT_VERSION}" in DOCKERFILE, "Caddy module must be pinned by build arg.")
require("CADDY_RATELIMIT_VERSION=16aecbb" in DOCKERFILE, "Caddy rate-limit module pin changed unexpectedly.")
require("CADDY_VERSION=2.10.0" in DOCKERFILE, "Caddy base version changed unexpectedly.")
require("USER caddy" in DOCKERFILE, "Caddy Dockerfile must declare a non-root runtime user.")
require("build:" in COMPOSE and "CADDY_RATELIMIT_VERSION" in COMPOSE, "Compose must build the custom Caddy image.")
require("/etc/nutsnews/caddy/rate-limits:ro" in COMPOSE, "Compose must mount the generated rate-limit config.")

for token in (
    "Rate Limiting",
    "30 requests per minute",
    "20 requests per minute",
    "60 requests per minute",
    "600 requests per minute",
    "HTTP 429",
    "sudo docker logs nutsnews-caddy",
):
    require(token in RUNBOOK, f"Runbook missing {token}.")

print("Caddy rate-limit guardrails passed.")
