#!/usr/bin/env python3
"""Validate manual VPS backup workflows stay narrow."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOWS = [
    {
        "path": Path(".github/workflows/run-vps-backup.yml"),
        "name": "Run VPS Backup",
        "service_env": "BACKUP_SERVICE",
        "service_name": "nutsnews-restic-backup.service",
        "start_line": "sudo -n /bin/systemctl start ${BACKUP_SERVICE}",
        "status_file_env": "BACKUP_STATUS_FILE",
        "success_field": "last_backup.status",
    },
    {
        "path": Path(".github/workflows/verify-vps-backup.yml"),
        "name": "Verify VPS Backup",
        "service_env": "VERIFY_SERVICE",
        "service_name": "nutsnews-restic-verify.service",
        "start_line": "sudo -n /bin/systemctl start ${VERIFY_SERVICE}",
        "status_file_env": "BACKUP_STATUS_FILE",
        "success_field": "last_check.status",
    },
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def validate_workflow(item: dict[str, object]) -> None:
    path = item["path"]
    assert isinstance(path, Path)
    text = path.read_text(encoding="utf-8")
    name = str(item["name"])
    service_env = str(item["service_env"])
    service_name = str(item["service_name"])
    start_line = str(item["start_line"])

    require(f"name: {name}" in text, f"{path}: unexpected workflow name.")
    require(re.search(r"(?m)^  workflow_dispatch:\s*$", text) is not None, f"{path}: must be manual-only.")
    require("pull_request:" not in text, f"{path}: must not run on pull_request.")
    require("push:" not in text, f"{path}: must not run on push.")
    require("schedule:" not in text, f"{path}: must not run on a schedule.")
    require("inputs:" not in text, f"{path}: must not accept dispatch inputs.")
    require("environment: production-vps" in text, f"{path}: must use the production-vps environment.")
    require("NUTSNEWS_VPS_SSH_PRIVATE_KEY" in text, f"{path}: must use the existing SSH private key secret.")
    require("NUTSNEWS_VPS_KNOWN_HOSTS" in text, f"{path}: must use the existing known_hosts secret.")
    require("VPS_USER: nutsnews_ops" in text, f"{path}: must connect as nutsnews_ops.")
    require(f"{service_env}: {service_name}" in text, f"{path}: unexpected systemd service target.")
    require(start_line in text, f"{path}: must start only the fixed systemd unit.")
    require(str(item["status_file_env"]) in text, f"{path}: must read the fixed backup status file.")
    require(str(item["success_field"]) in text, f"{path}: must validate the expected fixed status field.")
    require("${{ inputs." not in text, f"{path}: must not interpolate dispatch inputs.")
    require("bash -s" not in text, f"{path}: must not stream arbitrary shell over SSH.")
    require("sudo -n /bin/bash" not in text, f"{path}: must not start a remote shell with sudo.")
    require("sudo -n bash" not in text, f"{path}: must not start a remote shell with sudo.")
    require("set -x" not in text, f"{path}: must not enable shell tracing around secrets.")
    require("cat \"$HOME/.ssh/nutsnews_vps\"" not in text, f"{path}: must not print the private key.")
    require("cat \"$HOME/.ssh/known_hosts\"" not in text, f"{path}: must not print known_hosts.")

    remote_commands = re.findall(r'ssh "\$\{ssh_args\[@\]\}" "\$target" "([^"]+)', text)
    require(remote_commands, f"{path}: could not find fixed SSH commands.")
    for command in remote_commands:
        require("${{" not in command, f"{path}: remote command must not use GitHub expressions: {command}")


for workflow in WORKFLOWS:
    validate_workflow(workflow)

print("Backup workflow guardrails passed.")
