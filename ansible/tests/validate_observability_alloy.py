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
    "vps_service_foundation_grafana_alloy_ready_url: http://127.0.0.1:12345/-/ready",
    'vps_service_foundation_grafana_alloy_containerd_permission_error_pattern: "containerd\\\\.sock: connect: permission denied"',
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
    "Alloy Docker/cAdvisor blocks must stay gated by the Docker collection flag.",
)
require("append: false" in TASKS, "Alloy supplementary groups must be reconciled to avoid stale Docker access.")
require(
    "vps_service_foundation_grafana_alloy_docker_groups" in TASKS,
    "Alloy Docker group membership must be added only when Docker collection is enabled.",
)
require("Validate Grafana Alloy readiness endpoint" in TASKS, "Alloy readiness validation is missing.")
require(
    "containerd socket permission errors" in TASKS and "journalctl" in TASKS,
    "Alloy journal validation for containerd socket permission errors is missing.",
)
require("User=root" not in ALLOY_DROPIN, "Alloy drop-in must not run Alloy as root.")

print("Grafana Alloy guardrails passed.")
