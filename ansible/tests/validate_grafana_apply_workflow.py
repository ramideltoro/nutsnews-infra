#!/usr/bin/env python3
"""Validate Grafana Cloud apply workflow dispatch guardrails."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/grafana-cloud-apply.yml")
TEXT = WORKFLOW.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require("name: Grafana Cloud Apply" in TEXT, "Unexpected Grafana Cloud apply workflow name.")
require(re.search(r"(?m)^  workflow_dispatch:\s*$", TEXT) is not None, "Workflow must be manual-only.")
require("pull_request:" not in TEXT, "Grafana Cloud apply must not run on pull_request.")
require("push:" not in TEXT, "Grafana Cloud apply must not run on push.")
require("schedule:" not in TEXT, "Grafana Cloud apply must not run on schedule.")
require("environment: production-vps" in TEXT, "Grafana Cloud apply must use production-vps environment.")
require('if [[ "$GITHUB_REF" != "refs/heads/main" ]]; then' in TEXT, "Apply must be restricted to main.")
require('if [[ "$CONFIRM_APPLY" != "grafana-cloud" ]]; then' in TEXT, "Apply guard must require grafana-cloud.")

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
