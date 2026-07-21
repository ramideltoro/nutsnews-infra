#!/usr/bin/env python3
"""Validate CI cost controls remain explicit and conservative."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def count_pinned_action(workflow: str, action: str) -> int:
    pattern = rf"uses:\s+{re.escape(action)}@[0-9a-fA-F]{{40}}"
    return len(re.findall(pattern, workflow))


classifier = read(".github/scripts/ci_classify_changes.py")
infrastructure = read(".github/workflows/infrastructure-checks.yml")
runtime = read(".github/workflows/runtime-checks.yml")
portal = read(".github/workflows/portal-checks.yml")
supply_chain = read(".github/workflows/supply-chain.yml")
workflow_safety = read(".github/workflows/workflow-safety.yml")
secrets_scan = read(".github/workflows/secrets-scan.yml")
nightly = read(".github/workflows/nightly-audit.yml")

for category in (
    "docs_only",
    "workflows",
    "terraform",
    "ansible",
    "portal",
    "runtime",
    "dependency",
    "run_supply_chain",
):
    require(f'"{category}"' in classifier, f"classifier missing {category} output.")

for workflow in (infrastructure, runtime, portal, supply_chain):
    require("name: Classify changed paths" in workflow, "gated workflows must classify changed paths.")
    require("fetch-depth: 0" in workflow, "classification checkout must fetch history for diffs.")
    require(".github/scripts/ci_classify_changes.py" in workflow, "workflow must use the shared classifier.")
    require("CI path classification" in workflow or "needs.changes.outputs" in workflow, "workflow must expose skip context.")

require("needs.changes.outputs.run_yaml == 'true'" in infrastructure, "YAML lint must be path-gated.")
require("needs.changes.outputs.run_terraform == 'true'" in infrastructure, "Terraform checks must be path-gated.")
require("needs.changes.outputs.run_ansible == 'true'" in infrastructure, "Ansible lint must be path-gated.")
require("needs.changes.outputs.run_checkov == 'true'" in infrastructure, "Checkov must skip docs-only changes.")
require(
    count_pinned_action(infrastructure, "actions/setup-python") >= 2,
    "pip tooling must use actions/setup-python pinned to full commit SHAs; "
    "the generic workflow action pin validator rejects mutable refs.",
)
require("cache: pip" in infrastructure, "pip tooling must use setup-python pip cache.")
require(".github/requirements/yamllint.txt" in infrastructure, "yamllint must install from a pinned requirements file.")
require(".github/requirements/ansible-lint.txt" in infrastructure, "ansible-lint must install from a pinned requirements file.")

require("needs.changes.outputs.run_runtime == 'true'" in runtime, "Runtime checks must be path-gated.")
require("needs.changes.outputs.run_portal == 'true'" in portal, "Portal checks must be path-gated.")
require("needs.changes.outputs.run_supply_chain == 'true'" in supply_chain, "Supply-chain scans must skip docs-only changes.")

require("needs.changes.outputs" not in workflow_safety, "Workflow Safety must remain ungated.")
require("needs.changes.outputs" not in secrets_scan, "Secrets Scan must remain ungated.")
require("pull_request:" in secrets_scan and "schedule:" in secrets_scan, "Secrets Scan must keep PR and scheduled coverage.")
require("continue-on-error: true" in secrets_scan, "Secrets Scan must tolerate transient hosted-action failures.")
require(
    count_pinned_action(secrets_scan, "actions/setup-go") == 1,
    "Secrets Scan fallback Go setup must use actions/setup-go pinned to a full commit SHA; "
    "the generic workflow action pin validator rejects mutable refs.",
)
require('go-version: "1.24.x"' in secrets_scan, "Secrets Scan fallback Go version must be explicit.")
require("github.com/zricethezav/gitleaks/v8@${GITLEAKS_VERSION}" in secrets_scan, "Secrets Scan must keep a pinned OSS CLI fallback.")
require('gitleaks git --no-banner --redact --log-opts="${BASE_SHA}..${HEAD_SHA}"' in secrets_scan, "Secrets Scan fallback must stay PR scoped.")
require("schedule:" in nightly and "OSV-Scanner" in nightly and "Trivy filesystem" in nightly, "Nightly audit must keep deep scans.")
require("validate_ci_cost_controls.py" in workflow_safety, "Workflow Safety must validate CI cost controls.")

print("CI cost-control workflow guardrails passed.")
