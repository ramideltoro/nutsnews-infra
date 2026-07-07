#!/usr/bin/env python3
"""Validate Cloudflare DDNS configuration guardrails."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(".")
sys.path.insert(0, str(ROOT / "ansible/roles/vps_service_foundation/files"))

import cloudflare_ddns  # noqa: E402


DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "ansible/roles/vps_service_foundation/templates/cloudflare-ddns.env.j2").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/protected-ansible-apply.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("vps_service_foundation_cloudflare_ddns_record_names:" in DEFAULTS, "DDNS record list default is missing.")
require("- vps.nutsnews.com" in DEFAULTS, "DDNS must manage the VPS hostname.")
require("- ops.nutsnews.com" in DEFAULTS, "DDNS must manage the public Ops Portal hostname.")
require("CLOUDFLARE_RECORD_NAMES=" in TEMPLATE, "DDNS env template must render the record list.")
require("CLOUDFLARE_RECORD_NAMES:" in TASKS, "DDNS dry-run verification must pass the record list.")
require("Run Cloudflare DDNS updater immediately" in TASKS, "DDNS must run once during protected apply.")
require("vps_service_foundation_cloudflare_ddns_service" in TASKS, "Immediate DDNS run must use the managed systemd service.")
require("not ansible_check_mode" in TASKS, "Immediate DDNS run must be skipped in Ansible check mode.")
require("CLOUDFLARE_RECORD_NAMES: vps.nutsnews.com,ops.nutsnews.com" in WORKFLOW, "Workflow inspection must include both DDNS records.")

previous_record_name = os.environ.get("CLOUDFLARE_RECORD_NAME")
previous_record_names = os.environ.get("CLOUDFLARE_RECORD_NAMES")

try:
    os.environ["CLOUDFLARE_RECORD_NAME"] = "vps.nutsnews.com"
    os.environ.pop("CLOUDFLARE_RECORD_NAMES", None)
    require(cloudflare_ddns.record_names() == ("vps.nutsnews.com",), "Single DDNS record fallback is broken.")

    os.environ["CLOUDFLARE_RECORD_NAMES"] = "vps.nutsnews.com, ops.nutsnews.com"
    require(
        cloudflare_ddns.record_names() == ("vps.nutsnews.com", "ops.nutsnews.com"),
        "Multi-record DDNS parsing is broken.",
    )

    os.environ["CLOUDFLARE_RECORD_NAMES"] = "vps.nutsnews.com,vps.nutsnews.com"
    try:
        cloudflare_ddns.record_names()
    except cloudflare_ddns.DdnsError:
        pass
    else:
        raise SystemExit("Duplicate DDNS records must be rejected.")
finally:
    if previous_record_name is None:
        os.environ.pop("CLOUDFLARE_RECORD_NAME", None)
    else:
        os.environ["CLOUDFLARE_RECORD_NAME"] = previous_record_name

    if previous_record_names is None:
        os.environ.pop("CLOUDFLARE_RECORD_NAMES", None)
    else:
        os.environ["CLOUDFLARE_RECORD_NAMES"] = previous_record_names

print("Cloudflare DDNS guardrails passed.")
