#!/usr/bin/env python3
"""Validate the managed Docker cleanup guardrails."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(".")
RUNNER = (ROOT / "ansible/roles/vps_service_foundation/files/docker_cleanup.py").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py").read_text(encoding="utf-8")
DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
SERVICE = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-docker-cleanup.service.j2"
).read_text(encoding="utf-8")
TIMER = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-docker-cleanup.timer.j2"
).read_text(encoding="utf-8")
COLLECTOR_UNIT = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-ops-portal-collector.service.j2"
).read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/infrastructure-checks.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("docker builder" not in RUNNER, "Runner must invoke docker builder through argv, not shell text.")
require('"builder", "prune"' in RUNNER, "Runner must prune build cache.")
require('"image", "prune"' in RUNNER, "Runner must prune Docker images.")
require('"system", "prune"' not in RUNNER, "Runner must not use broad docker system prune.")
require('"volume", "prune"' not in RUNNER, "Runner must not prune Docker volumes.")
require('"container", "prune"' not in RUNNER, "Runner must not prune containers.")
require('"--filter", f"until={BUILD_CACHE_UNTIL}"' in RUNNER, "Build cache prune must use an age filter.")
require('"--filter", f"until={IMAGE_UNTIL}"' in RUNNER, "Image prune must use an age filter.")
require("AGE_RE" in RUNNER and "invalid_age_filter" in RUNNER, "Runner must validate age filters.")
require("running_image_ids()" in RUNNER, "Runner must inspect running images before cleanup.")
require("PROTECTED_REFS" in RUNNER, "Runner must read protected image refs.")
require("protected_untagged_image_present" in RUNNER, "Runner must skip image prune for unsafe protected images.")
require("STATUS_FILE.write_text" in RUNNER and "LOG_FILE.open" in RUNNER, "Runner must write status and JSONL logs.")
require("capture_output=True" in RUNNER and "shell=True" not in RUNNER, "Runner must avoid shell invocation.")

for token in (
    "vps_service_foundation_docker_cleanup_enabled: true",
    "vps_service_foundation_docker_cleanup_image_until: 168h",
    "vps_service_foundation_docker_cleanup_build_cache_until: 168h",
    "vps_service_foundation_docker_cleanup_on_calendar:",
    "vps_service_foundation_docker_cleanup_status_file:",
    "vps_service_foundation_docker_cleanup_log_file:",
):
    require(token in DEFAULTS, f"Defaults missing {token}.")

for token in (
    "Install conservative Docker cleanup runner",
    "Validate conservative Docker cleanup settings",
    "Install conservative Docker cleanup service",
    "Install conservative Docker cleanup timer",
    "Seed Docker cleanup status before the first scheduled run",
    "Manage conservative Docker cleanup timer",
):
    require(token in TASKS, f"Ansible tasks missing {token}.")

require("force: false" in TASKS, "Initial status seeding must not overwrite cleanup history.")
require("installed_pending_first_run" in TASKS, "Initial status must advertise pending first run.")
require("vps_service_foundation_nutsnews_environment_names" in SERVICE, "Service must cover all app environments.")
require("last_known_good_digest" in SERVICE, "Service must protect last-known-good digest refs.")
require("NUTSNEWS_DOCKER_CLEANUP_PROTECTED_IMAGE_REFS" in SERVICE, "Service must pass protected image refs.")
require("RestrictAddressFamilies=AF_UNIX" in SERVICE, "Service must only need local Docker socket access.")
require("ReadWritePaths={{ vps_service_foundation_portal_data_dir }}" in SERVICE, "Service write paths must be narrow.")
require("OnCalendar={{ vps_service_foundation_docker_cleanup_on_calendar }}" in TIMER, "Timer must use managed cadence.")
require("Persistent=true" in TIMER, "Timer must be persistent.")

require("DOCKER_CLEANUP_STATUS_FILE" in COLLECTOR, "Collector must read Docker cleanup status.")
require("docker_cleanup_state" in COLLECTOR, "Collector must expose Docker cleanup state.")
require('"docker_cleanup": docker_cleanup' in COLLECTOR, "Portal status must include Docker cleanup state.")
require("protected_image_summary" in COLLECTOR, "Portal must publish only protected image counts.")
require("NUTSNEWS_DOCKER_CLEANUP_STATUS_FILE" in COLLECTOR_UNIT, "Collector unit must pass cleanup status path.")

require("py_compile ansible/roles/vps_service_foundation/files/docker_cleanup.py" in WORKFLOW, "CI must compile runner.")
require("validate_docker_cleanup.py" in WORKFLOW, "CI must run Docker cleanup validation.")
