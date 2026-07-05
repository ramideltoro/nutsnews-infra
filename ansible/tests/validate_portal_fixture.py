#!/usr/bin/env python3
"""Validate the static portal fixture and secret-safety guardrails."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(".")
STATUS = json.loads((ROOT / "portal/data/status.example.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "portal/assets/app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "portal/assets/styles.css").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py").read_text(encoding="utf-8")
REPORTER = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_reporter.py").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
COLLECTOR_UNIT = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-ops-portal-collector.service.j2"
).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


host = STATUS["host"]
require(host.get("public_ipv4") not in ("", "unknown", None), "Fixture public IPv4 must be known.")
require(host.get("public_ipv6") not in ("", "unknown", None), "Fixture public IPv6 must be known.")
require("NUTSNEWS_PUBLIC_IPV4={{ vps_service_foundation_public_ipv4 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv4.")
require("NUTSNEWS_PUBLIC_IPV6={{ vps_service_foundation_public_ipv6 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv6.")

reporting = STATUS["email_reporting"]
for key in (
    "enabled",
    "configured",
    "smtp_host_configured",
    "next_report_run_at",
    "last_report_run_at",
    "last_report_success_at",
    "last_report_sent_at",
    "last_error",
):
    require(key in reporting, f"Email reporting fixture missing {key}.")

process_network = STATUS["process_network"]
require(process_network.get("available") is False, "Per-app network totals must not be faked in the fixture.")
require("does not expose reliable per-process network byte totals" in process_network.get("method", ""), "Network label must be honest.")

redaction = STATUS["logs"]["redaction"].lower()
for word in ("token", "secret", "password", "authorization", "credential", "private-key"):
    require(word in redaction, f"Log redaction description missing {word}.")
    require(word.replace("-", "_") in COLLECTOR.lower() or word in COLLECTOR.lower(), f"Collector redaction missing {word}.")

for token in ("gauge-card", "temperature-card", "Health Score", "renderEmailReporting"):
    require(token in APP_JS or token in STYLES, f"Portal UI missing {token}.")

for forbidden in ("<button", "<form", "docker.sock", "child_process", "execFile", "spawn"):
    require(forbidden not in APP_JS, f"Portal JavaScript includes forbidden control surface: {forbidden}.")

require("last_report_run_at" in REPORTER, "Reporter must record report attempts.")
require("last_report_success_at" in REPORTER, "Reporter must record successful report sends.")
require("ops-reporter.env.j2" in TASKS, "Reporter environment template must be managed by Ansible.")
require("no_log: true" in TASKS, "Reporter environment task must keep SMTP secrets out of logs.")

print("Portal fixture and secret-safety guardrails passed.")
