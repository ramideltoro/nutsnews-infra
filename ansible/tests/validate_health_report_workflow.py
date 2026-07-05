#!/usr/bin/env python3
"""Validate the manual VPS health report workflow stays narrow."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/send-vps-health-report.yml")
TEXT = WORKFLOW.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("name: Send VPS Health Report" in TEXT, "Unexpected workflow name.")
require(re.search(r"(?m)^  workflow_dispatch:\s*$", TEXT) is not None, "Workflow must be manual-only.")
require("pull_request:" not in TEXT, "Health report workflow must not run on pull_request.")
require("push:" not in TEXT, "Health report workflow must not run on push.")
require("schedule:" not in TEXT, "Health report workflow must not run on a schedule.")
require("inputs:" not in TEXT, "Health report workflow must not accept dispatch inputs.")
require("environment: production-vps" in TEXT, "Workflow must use the production-vps environment.")
require("NUTSNEWS_VPS_SSH_PRIVATE_KEY" in TEXT, "Workflow must use the existing SSH private key secret.")
require("NUTSNEWS_VPS_KNOWN_HOSTS" in TEXT, "Workflow must use the existing known_hosts secret.")
require("VPS_USER: nutsnews_ops" in TEXT, "Workflow must connect as nutsnews_ops.")
require("sudo -n /bin/systemctl start ${REPORT_SERVICE}" in TEXT, "Workflow must start only the fixed report service.")
require("REPORT_SERVICE: nutsnews-ops-health-report.service" in TEXT, "Unexpected report service target.")
require("${{ inputs." not in TEXT, "Workflow must not interpolate dispatch inputs.")
require("bash -s" not in TEXT, "Workflow must not stream arbitrary shell over SSH.")
require("sudo -n /bin/bash" not in TEXT, "Workflow must not start a remote shell with sudo.")
require("systemctl start ${REPORT_SERVICE}" in TEXT, "Workflow must trigger the existing systemd report unit.")
require("set -x" not in TEXT, "Workflow must not enable shell tracing around secrets.")
require("cat \"$HOME/.ssh/nutsnews_vps\"" not in TEXT, "Workflow must not print the private key.")
require("cat \"$HOME/.ssh/known_hosts\"" not in TEXT, "Workflow must not print known_hosts.")

remote_commands = re.findall(r'ssh "\$\{ssh_args\[@\]\}" "\$target" "([^"]+)', TEXT)
require(remote_commands, "Could not find fixed SSH commands.")
for command in remote_commands:
    require("${{" not in command, f"Remote command must not use GitHub expressions: {command}")

print("Health report workflow guardrails passed.")
