#!/usr/bin/env python3
"""Validate the manual Ops Portal status verifier stays narrow."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/verify-ops-portal-status.yml")
TEXT = WORKFLOW.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("name: Verify Ops Portal Status" in TEXT, "Unexpected workflow name.")
require(re.search(r"(?m)^  workflow_dispatch:\s*$", TEXT) is not None, "Workflow must be manual-only.")
require("pull_request:" not in TEXT, "Verifier workflow must not run on pull_request.")
require("push:" not in TEXT, "Verifier workflow must not run on push.")
require("schedule:" not in TEXT, "Verifier workflow must not run on a schedule.")
require("inputs:" not in TEXT, "Verifier workflow must not accept dispatch inputs.")
require("environment: production-vps" in TEXT, "Workflow must use the production-vps environment.")
require("NUTSNEWS_VPS_SSH_PRIVATE_KEY" in TEXT, "Workflow must use the existing SSH private key secret.")
require("NUTSNEWS_VPS_KNOWN_HOSTS" in TEXT, "Workflow must use the existing known_hosts secret.")
require("VPS_USER: nutsnews_ops" in TEXT, "Workflow must connect as nutsnews_ops.")
require("PORTAL_STATUS_FILE: /opt/nutsnews/portal-assets/data/status.json" in TEXT, "Unexpected portal status file.")
require("free_tier_usage" in TEXT, "Workflow must read free-tier usage status.")
require("vercel" in TEXT, "Workflow must inspect the Vercel provider.")
require("${{ inputs." not in TEXT, "Workflow must not interpolate dispatch inputs.")
require("bash -s" not in TEXT, "Workflow must not stream arbitrary shell over SSH.")
require("sudo -n /bin/bash" not in TEXT, "Workflow must not start a remote shell with sudo.")
require("sudo -n bash" not in TEXT, "Workflow must not start a remote shell with sudo.")
require("set -x" not in TEXT, "Workflow must not enable shell tracing around secrets.")
require("cat \"$HOME/.ssh/nutsnews_vps\"" not in TEXT, "Workflow must not print the private key.")
require("cat \"$HOME/.ssh/known_hosts\"" not in TEXT, "Workflow must not print known_hosts.")
require("NUTSNEWS_VERCEL_API_TOKEN" not in TEXT, "Workflow must not read provider API tokens.")

remote_commands = re.findall(r'ssh "\$\{ssh_args\[@\]\}" "\$target" "([^"]+)', TEXT)
require(remote_commands, "Could not find fixed SSH command.")
for command in remote_commands:
    require("${{" not in command, f"Remote command must not use GitHub expressions: {command}")

print("Ops Portal status workflow guardrails passed.")
