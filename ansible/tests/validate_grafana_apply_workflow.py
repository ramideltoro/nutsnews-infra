#!/usr/bin/env python3
"""Validate Grafana Cloud apply workflow dispatch guardrails."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/grafana-cloud-apply.yml")
TEXT = WORKFLOW.read_text(encoding="utf-8")
PLAN_TEXT = Path(".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
INPUT_VALIDATOR_TEXT = Path(
    "terraform/grafana-cloud/scripts/validate_synthetic_monitoring_inputs.py"
).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("name: Grafana Cloud Apply" in TEXT, "Unexpected Grafana Cloud apply workflow name.")
require(re.search(r"(?m)^  workflow_dispatch:\s*$", TEXT) is not None, "Workflow must be manual-only.")
require("pull_request:" not in TEXT, "Grafana Cloud apply must not run on pull_request.")
require("push:" not in TEXT, "Grafana Cloud apply must not run on push.")
require("schedule:" not in TEXT, "Grafana Cloud apply must not run on schedule.")
require("environment: production-vps" in TEXT, "Grafana Cloud apply must use production-vps environment.")
require("group: grafana-cloud-apply" in TEXT, "Grafana Cloud apply must retain its mutation lock.")
require("queue: max" in TEXT, "Grafana Cloud apply must queue every pending mutation.")
require("cancel-in-progress: false" in TEXT, "Grafana Cloud apply must not cancel an active mutation.")
require('if [[ "$GITHUB_REF" != "refs/heads/main" ]]; then' in TEXT, "Apply must be restricted to main.")
require('if [[ "$CONFIRM_APPLY" != "grafana-cloud" ]]; then' in TEXT, "Apply guard must require grafana-cloud.")
require(
    "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED" in TEXT
    and "TF_VAR_synthetic_major_forecast_acknowledged" in TEXT,
    "Apply must pass the protected standing-major synthetic decision.",
)
for workflow, name in ((PLAN_TEXT, "plan"), (TEXT, "apply")):
    require(
        "python3 terraform/grafana-cloud/scripts/validate_synthetic_monitoring_inputs.py" in workflow,
        f"Grafana Cloud {name} must run the protected input validator.",
    )
    for token in (
        "NUTSNEWS_GRAFANA_CLOUD_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED",
        "--output \"$RUNNER_TEMP/grafana-cloud-input-validation.json\"",
    ):
        require(token in workflow, f"Grafana Cloud {name} origin preflight is missing {token}.")
    require(
        '.startswith("https://")' not in workflow,
        f"Grafana Cloud {name} must not rely on a prefix-only Grafana origin check.",
    )

for token in (
    'GRAFANA_UI_HOSTNAME = "nutsnews.grafana.net"',
    "SYNTHETIC_MONITORING_HOSTNAME = re.compile(",
    'r"synthetic-monitoring-api(?:[.-]',
    "validate_grafana_ui_origin(",
    "validate_synthetic_monitoring_origin(",
    "value != value.strip()",
    "not hostname_is_allowed(hostname)",
    'parsed.scheme != "https"',
    "parsed.username is not None",
    "parsed.password is not None",
    "port not in (None, 443)",
    'parsed.netloc.lower() not in {hostname, f"{hostname}:443"}',
    'parsed.path not in ("", "/")',
    "parsed.query",
    "parsed.fragment",
):
    require(token in INPUT_VALIDATOR_TEXT, f"Shared Grafana origin preflight is missing {token}.")

confirm_block = re.search(
    r"(?ms)^      confirm_apply:\n(?P<body>.*?)(?:^      [a-zA-Z0-9_-]+:|\npermissions:)",
    TEXT,
)
require(confirm_block is not None, "confirm_apply input block is missing.")
body = confirm_block.group("body")

require("type: choice" in body, "confirm_apply must be a choice input to prevent invalid free-text values.")
require("type: string" not in body, "confirm_apply must not be a free-text string input.")
require("options:" in body, "confirm_apply choice input must define options.")
require(re.search(r"(?m)^          - grafana-cloud\s*$", body) is not None, "confirm_apply must offer grafana-cloud.")
options = re.findall(r"(?m)^          - (.+?)\s*$", body)
require(options == ["grafana-cloud"], "confirm_apply must offer only grafana-cloud as a selectable value.")

print("Grafana Cloud apply workflow guardrails passed.")
