#!/usr/bin/env python3
"""Validate Grafana Alloy installation guardrails."""

from __future__ import annotations

from pathlib import Path


TASKS = Path("ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
DEFAULTS = Path("ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
ALLOY_CONFIG = Path("ansible/roles/vps_service_foundation/templates/grafana-alloy.config.alloy.j2").read_text(
    encoding="utf-8"
)
ALLOY_DROPIN = Path("ansible/roles/vps_service_foundation/templates/grafana-alloy.service-dropin.conf.j2").read_text(
    encoding="utf-8"
)
CADDYFILE = Path("compose/caddy/Caddyfile").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "vps_service_foundation_grafana_alloy_enabled: false",
    "vps_service_foundation_grafana_alloy_install_repo: true",
    "vps_service_foundation_grafana_alloy_apt_repo_uri: https://apt.grafana.com",
    "vps_service_foundation_grafana_alloy_apt_repo_suite: stable",
    "vps_service_foundation_grafana_alloy_package: alloy",
    "vps_service_foundation_grafana_alloy_collect_docker: false",
    "vps_service_foundation_grafana_alloy_collect_docker_logs: true",
    "vps_service_foundation_grafana_alloy_docker_socket: unix:///var/run/docker.sock",
    "vps_service_foundation_grafana_alloy_docker_log_compose_projects:",
    "vps_service_foundation_grafana_alloy_ready_url: http://127.0.0.1:12345/-/ready",
    'vps_service_foundation_grafana_alloy_containerd_permission_error_pattern: "containerd\\\\.sock: connect: permission denied"',
    'vps_service_foundation_grafana_alloy_file_permission_error_pattern: "failed to tail the file: open .*: permission denied"',
    "vps_service_foundation_backup_log_group: adm",
):
    require(token in DEFAULTS, f"Grafana Alloy defaults missing {token}.")

configure_repo = TASKS.find("- name: Configure Grafana apt repository")
refresh_cache = TASKS.find("- name: Refresh apt cache after configuring Grafana repository")
install_package = TASKS.find("- name: Install Grafana Alloy package")

require(configure_repo >= 0, "Grafana apt repository task is missing.")
require(refresh_cache >= 0, "Grafana apt cache refresh task is missing.")
require(install_package >= 0, "Grafana Alloy package install task is missing.")
require(
    configure_repo < refresh_cache < install_package,
    "Grafana apt cache must refresh after repository setup and before installing Alloy.",
)

refresh_block = TASKS[refresh_cache:install_package]
require("ansible.builtin.apt:" in refresh_block, "Grafana cache refresh must use the apt module.")
require("update_cache: true" in refresh_block, "Grafana cache refresh must update apt cache.")
require(
    "vps_service_foundation_grafana_alloy_install_repo | bool" in refresh_block,
    "Grafana cache refresh must be guarded by repository management flag.",
)

install_block = TASKS[install_package:TASKS.find("- name: Set Alloy supplementary groups", install_package)]
require("cache_valid_time:" not in install_block, "Alloy install must not skip cache refresh by age.")
require("update_cache:" not in install_block, "Alloy install must rely on the explicit post-repository refresh.")
require("prometheus.exporter.cadvisor" in ALLOY_CONFIG, "Alloy cAdvisor exporter block is missing.")
require(
    "{% if vps_service_foundation_grafana_alloy_collect_docker | bool %}" in ALLOY_CONFIG,
    "Alloy cAdvisor blocks must stay gated by the container metrics collection flag.",
)
require(
    "{% if vps_service_foundation_grafana_alloy_collect_docker_logs | bool %}" in ALLOY_CONFIG,
    "Alloy Docker log blocks must be gated by the Docker log collection flag.",
)
require(
    ALLOY_CONFIG.find("prometheus.exporter.cadvisor") < ALLOY_CONFIG.find(
        "{% if vps_service_foundation_grafana_alloy_collect_docker_logs | bool %}"
    ),
    "cAdvisor must not move under the Docker log collection gate.",
)
for token in (
    'env                    = sys.env("NUTSNEWS_ALLOY_ENVIRONMENT")',
    'host                   = sys.env("NUTSNEWS_ALLOY_HOSTNAME")',
    'source     = "journal"',
    'source             = "auth"',
    'source             = "nutsnews-service"',
    'target_label = "source"',
    'stage.json',
    'stage.structured_metadata',
    'stage.label_keep',
    'drop_counter_reason = "debug_or_trace_line"',
    'drop_counter_reason = "docker_debug_or_trace_line"',
    'drop_counter_reason = "docker_line_too_large"',
    'max_streams = 500',
):
    require(token in ALLOY_CONFIG, f"Structured Alloy log guardrail missing {token}.")
for project in ("nutsnews-service-foundation", "nutsnews-app"):
    require(project in DEFAULTS, f"Alloy Docker log discovery defaults must include {project}.")
require(
    "status               = \"status\"" in ALLOY_CONFIG and "uri                  = \"request.uri\"" in ALLOY_CONFIG,
    "Docker/Caddy JSON parsing must extract status and URI as structured metadata.",
)
require("append: false" in TASKS, "Alloy supplementary groups must be reconciled to avoid stale Docker access.")
require("Ensure Alloy Docker telemetry group exists" in TASKS, "Alloy Docker telemetry group must be explicit.")
require(
    "vps_service_foundation_grafana_alloy_docker_groups" in TASKS,
    "Alloy Docker group membership must be added only when Docker telemetry is enabled.",
)
require(
    "vps_service_foundation_grafana_alloy_collect_docker_logs | bool" in TASKS,
    "Alloy Docker group membership must account for Docker log collection.",
)
require("Validate Grafana Alloy readiness endpoint" in TASKS, "Alloy readiness validation is missing.")
require(
    "containerd socket permission errors" in TASKS and "journalctl" in TASKS,
    "Alloy journal validation for containerd socket permission errors is missing.",
)
require(
    "file log permission errors" in TASKS and "vps_service_foundation_grafana_alloy_file_permission_error_pattern" in TASKS,
    "Alloy journal validation for file log permission errors is missing.",
)
disabled_reconcile = TASKS.find("- name: Reconcile disabled Grafana Alloy observability agent")
enabled_service = TASKS.find("- name: Enable Grafana Alloy service")
validation_start = TASKS.find("- name: Capture Grafana Alloy post-apply validation start")
require(disabled_reconcile >= 0, "Disabled Alloy reconciliation block is missing.")
require(
    enabled_service < disabled_reconcile < validation_start,
    "Disabled Alloy reconciliation must run after enabled management and before post-apply validation.",
)
disabled_block = TASKS[disabled_reconcile:validation_start]
for token in (
    "not (vps_service_foundation_grafana_alloy_enabled | bool)",
    "ansible.builtin.service_facts:",
    "Stop, disable, and mask Grafana Alloy service when disabled",
    "enabled: false",
    "masked: true",
    "state: stopped",
    "Remove disabled Grafana Alloy supplementary access",
    'groups: ""',
    "vps_service_foundation_grafana_alloy_env_file",
    "vps_service_foundation_grafana_alloy_config_file",
    "vps_service_foundation_grafana_alloy_systemd_dropin_file",
    "vps_service_foundation_observability_textfile_service",
    "vps_service_foundation_observability_textfile_timer",
    "Reload systemd after disabled Grafana Alloy artifact cleanup",
):
    require(token in disabled_block, f"Disabled Alloy reconciliation missing {token}.")
require("masked: false" in TASKS[enabled_service:disabled_reconcile], "Enabled Alloy management must unmask Alloy for rollback.")
require(
    "Allow observability agent to read encrypted VPS backup logs" in TASKS,
    "Existing backup logs must be reconciled for Alloy read access.",
)
BACKUP_SERVICE = Path("ansible/roles/vps_service_foundation/templates/nutsnews-restic-backup.service.j2").read_text(
    encoding="utf-8"
)
VERIFY_SERVICE = Path("ansible/roles/vps_service_foundation/templates/nutsnews-restic-verify.service.j2").read_text(
    encoding="utf-8"
)
for service_name, service_text in (
    ("backup", BACKUP_SERVICE),
    ("verify", VERIFY_SERVICE),
):
    require(
        "Group={{ vps_service_foundation_backup_log_group }}" in service_text,
        f"{service_name} service must write logs with the observability log group.",
    )
    require("UMask=0027" in service_text, f"{service_name} service must preserve group-read logs.")
require("User=root" not in ALLOY_DROPIN, "Alloy drop-in must not run Alloy as root.")
require(CADDYFILE.count("format json") == 3, "Every Caddy access log block must emit JSON.")

print("Grafana Alloy guardrails passed.")
