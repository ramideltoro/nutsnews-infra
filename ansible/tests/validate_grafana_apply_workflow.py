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


def workflow_step(workflow: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}\s*$\n(?P<body>.*?)(?=^      - name: |\Z)",
        workflow,
    )
    require(match is not None, f"Workflow step is missing: {name}.")
    assert match is not None
    return match.group("body")


def workflow_job(workflow: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\s*$\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
        workflow,
    )
    require(match is not None, f"Workflow job is missing: {name}.")
    assert match is not None
    return match.group("body")


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
for workflow, name, validator_step_name, tofu_step_name in (
    (PLAN_TEXT, "plan", "Validate optional JSON inputs", "Run Grafana Cloud plan"),
    (TEXT, "apply", "Validate JSON inputs", "Run Grafana Cloud apply"),
):
    require(
        "python3 terraform/grafana-cloud/scripts/validate_synthetic_monitoring_inputs.py" in workflow,
        f"Grafana Cloud {name} must run the protected input validator.",
    )
    validator_step = workflow_step(workflow, validator_step_name)
    tofu_step = workflow_step(workflow, tofu_step_name)
    for token in (
        "NUTSNEWS_GRAFANA_CLOUD_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON",
        "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED",
        "--output \"$RUNNER_TEMP/grafana-cloud-input-validation.json\"",
    ):
        require(token in validator_step, f"Grafana Cloud {name} input preflight is missing {token}.")
    for token in (
        "TF_VAR_synthetic_monitoring_probe_ids",
        "TF_VAR_synthetic_http_checks",
        "TF_VAR_synthetic_major_forecast_acknowledged",
        "GRAFANA_SM_ACCESS_TOKEN",
        "GRAFANA_SM_URL",
    ):
        require(token in tofu_step, f"Grafana Cloud {name} OpenTofu invocation is missing {token}.")
    require(
        '.startswith("https://")' not in workflow,
        f"Grafana Cloud {name} must not rely on a prefix-only Grafana origin check.",
    )

plan_job = workflow_job(PLAN_TEXT, "plan")
for token in (
    "environment: production-vps",
    "concurrency:",
    "group: grafana-cloud-apply",
    "queue: max",
    "cancel-in-progress: false",
    "timeout-minutes: 20",
):
    require(token in plan_job, f"Grafana Cloud protected plan job is missing {token}.")
for token in (
    "python3 terraform/grafana-cloud/scripts/verify_post_apply.py",
    "tofu -chdir=terraform/grafana-cloud output -json",
    "grafana-cloud-current-state-verification",
    "--require-query-data",
    "--terraform-outputs",
):
    require(
        token not in PLAN_TEXT,
        f"Grafana Cloud plan must not use apply-only exact-state evidence: {token}.",
    )
plan_upload_step = workflow_step(
    PLAN_TEXT, "Upload value-free protected-input evidence"
)
for token in ("grafana-cloud-input-validation", "if-no-files-found: error"):
    require(token in plan_upload_step, f"Grafana Cloud plan input artifact is missing {token}.")
drift_step = workflow_step(PLAN_TEXT, "Run Grafana Cloud drift check")
for token in (
    "-refresh-only",
    '-out="$refresh_plan"',
    'show -json "$refresh_plan"',
    "validate_refresh_only_plan.py",
    "trap 'rm -f \"$refresh_plan\"' EXIT",
):
    require(token in drift_step, f"Grafana Cloud resource-drift guard is missing {token}.")
require(
    "-detailed-exitcode" not in drift_step,
    "Grafana Cloud drift guard must not classify root output changes as resource drift.",
)

require(
    "timeout-minutes: 45" in workflow_job(TEXT, "apply"),
    "Grafana Cloud apply must allow the verifier's bounded polling windows.",
)
apply_tofu_step = workflow_step(TEXT, "Run Grafana Cloud apply")
require(
    'tofu -chdir=terraform/grafana-cloud output -json > "$RUNNER_TEMP/grafana-cloud-outputs.json"'
    in apply_tofu_step,
    "Grafana Cloud apply must capture outputs only after apply.",
)
verification_step = workflow_step(TEXT, "Verify Grafana Cloud resources and telemetry")
for token in (
    "TF_VAR_prometheus_datasource_uid",
    "TF_VAR_loki_datasource_uid",
    "TF_VAR_usage_datasource_uid",
    "GRAFANA_SM_ACCESS_TOKEN",
    "GRAFANA_SM_URL",
    "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON",
    "python3 terraform/grafana-cloud/scripts/verify_post_apply.py",
    "--require-query-data",
    '--terraform-outputs "$RUNNER_TEMP/grafana-cloud-outputs.json"',
):
    require(token in verification_step, f"Grafana Cloud apply verifier is missing {token}.")
verification_upload_step = workflow_step(TEXT, "Upload Grafana Cloud verification report")
for token in ("grafana-cloud-post-apply-verification", "if-no-files-found: error"):
    require(
        token in verification_upload_step,
        f"Grafana Cloud apply must require its sanitized verification artifact: {token}.",
    )

for token in (
    'GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"',
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
