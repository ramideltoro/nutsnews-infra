#!/usr/bin/env python3
"""Validate the protected VPS maintenance workflow stays fixed-purpose."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/protected-vps-maintenance.yml")
RUNNER = Path("scripts/vps_maintenance.py")
TEXT = WORKFLOW.read_text(encoding="utf-8")
RUNNER_TEXT = RUNNER.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("name: Protected VPS Maintenance" in TEXT, "Unexpected workflow name.")
require(re.search(r"(?m)^  workflow_dispatch:\s*$", TEXT) is not None, "Workflow must be manual-only.")
for forbidden in ("pull_request:", "push:", "schedule:"):
    require(forbidden not in TEXT, f"Maintenance workflow must not run on {forbidden}")

require("environment: production-vps" in TEXT, "Workflow must require production-vps approval/secrets.")
require("NUTSNEWS_VPS_SSH_PRIVATE_KEY" in TEXT, "Workflow must use the existing VPS SSH key secret.")
require("NUTSNEWS_VPS_KNOWN_HOSTS" in TEXT, "Workflow must use the existing known_hosts secret.")
require("VPS_USER: nutsnews_ops" in TEXT, "Workflow must connect as nutsnews_ops.")
require("permissions:\n  contents: read" in TEXT, "Workflow permissions must be contents: read only.")
require("cancel-in-progress: false" in TEXT, "Maintenance workflow must not cancel active maintenance.")
require("bash -s" not in TEXT, "Workflow must not stream arbitrary shell over SSH.")
require("sudo -n /bin/bash" not in TEXT, "Workflow must not start a remote root shell.")
require("cat \"$HOME/.ssh/nutsnews_vps\"" not in TEXT, "Workflow must not print the private key.")
require("set -x" not in TEXT, "Workflow must not enable shell tracing around secrets.")
require("scripts/vps_maintenance.py" in TEXT, "Workflow must run the reviewed maintenance script.")

for option in ("preflight", "package-maintenance", "reboot", "post-reboot"):
    require(re.search(rf"(?m)^          - {re.escape(option)}\s*$", TEXT), f"Missing mode option: {option}")

require("apply-package-maintenance" in TEXT, "Package maintenance must require an explicit confirmation choice.")
require("reboot-vps.nutsnews.com" in TEXT, "Reboot must require the exact host confirmation choice.")
require("--mode package-maintenance --confirm" in TEXT, "Package mode must pass a confirmation to the runner.")
require("--mode reboot --confirm" in TEXT, "Reboot mode must pass a confirmation to the runner.")
require("--expected-boot-id" in TEXT, "Reboot mode must validate the post-reboot boot ID changed.")

for forbidden in ("${{ inputs.command", "${{ inputs.ssh", "workflow_run:", "repository_dispatch:"):
    require(forbidden not in TEXT, f"Workflow must not expose arbitrary dispatch surface: {forbidden}")

require('"package-maintenance", "boot-id", "reboot", "post-reboot"' in RUNNER_TEXT, "Runner mode choices are missing.")
require("apt-get" in RUNNER_TEXT and "upgrade" in RUNNER_TEXT, "Runner must use fixed apt maintenance commands.")
require("systemctl\", \"reboot" in RUNNER_TEXT, "Runner must use a fixed reboot command.")
require("backup_is_fresh" in RUNNER_TEXT, "Runner must check backup freshness.")
require("curl_status(PUBLIC_HEALTH_URL)" in RUNNER_TEXT, "Runner must check public health.")
require("OPS_PORTAL_URL" in RUNNER_TEXT, "Runner must check Ops Portal auth redirect.")
require("REQUIRED_CONTAINERS" in RUNNER_TEXT, "Runner must check required Docker containers.")
require("shell=True" not in RUNNER_TEXT, "Runner must avoid shell=True.")

print("Protected VPS maintenance workflow guardrails passed.")
