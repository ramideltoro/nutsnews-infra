#!/usr/bin/env python3
"""Validate Grafana Alloy installation guardrails."""

from __future__ import annotations

from pathlib import Path


TASKS = Path("ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
DEFAULTS = Path("ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "vps_service_foundation_grafana_alloy_enabled: false",
    "vps_service_foundation_grafana_alloy_install_repo: true",
    "vps_service_foundation_grafana_alloy_apt_repo_uri: https://apt.grafana.com",
    "vps_service_foundation_grafana_alloy_apt_repo_suite: stable",
    "vps_service_foundation_grafana_alloy_package: alloy",
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

install_block = TASKS[install_package:TASKS.find("- name: Grant Alloy read-only log", install_package)]
require("cache_valid_time:" not in install_block, "Alloy install must not skip cache refresh by age.")
require("update_cache:" not in install_block, "Alloy install must rely on the explicit post-repository refresh.")

print("Grafana Alloy guardrails passed.")
