#!/usr/bin/env python3
"""Issue #123 rehearsal coverage for the staging-first production gate."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
PROTECTED_APPLY = (WORKFLOWS / "protected-ansible-apply.yml").read_text(encoding="utf-8")
ROLLBACK = (WORKFLOWS / "protected-nutsnews-rollback.yml").read_text(encoding="utf-8")
PROMOTION = (WORKFLOWS / "nutsnews-release-promotion.yml").read_text(encoding="utf-8")
STAGING_DEPLOY = (WORKFLOWS / "nutsnews-staging-deploy.yml").read_text(encoding="utf-8")
QUALIFIER = (WORKFLOWS / "nutsnews-staging-qualification.yml").read_text(encoding="utf-8")
PREMERGE_PRODUCTION = (WORKFLOWS / "nutsnews-premerge-production-vps-deploy.yml").read_text(
    encoding="utf-8"
)
WORKFLOW_SAFETY = (WORKFLOWS / "workflow-safety.yml").read_text(encoding="utf-8")
PORTAL_STATUS = (ROOT / "portal/data/status.example.json").read_text(encoding="utf-8")
PORTAL_JS = (ROOT / "portal/assets/app.js").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py").read_text(
    encoding="utf-8"
)
PRODUCTION_ELIGIBILITY_TEST = (ROOT / "ansible/tests/validate_production_eligibility.py").read_text(
    encoding="utf-8"
)
STAGING_QUALIFICATION_TEST = (ROOT / "ansible/tests/validate_staging_qualification.py").read_text(
    encoding="utf-8"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def job_block(workflow: str, job_name: str, next_job: str | None = None) -> str:
    start = workflow.index(f"  {job_name}:")
    if next_job:
        return workflow[start : workflow.index(f"  {next_job}:", start)]
    return workflow[start:]


pre_secret_gate = job_block(PROTECTED_APPLY, "verify-production-eligibility", "baseline")
baseline_job = job_block(PROTECTED_APPLY, "baseline")

for label in (
    "missing attestation",
    "wrong digest",
    "wrong source",
    "wrong build",
    "wrong source workflow",
    "wrong migration head",
    "wrong schema version",
    "wrong Supabase project ref",
    "wrong issuer",
    "wrong ref",
    "expired",
    "tampered json",
    "skipped suite",
    "stale staging",
    "superseded",
):
    require(label in PRODUCTION_ELIGIBILITY_TEST, f"Production gate negative rehearsal missing: {label}.")

for label in ("skip", "cancelled", "timeout", "fail", "pre/post identity mismatch"):
    require(label in STAGING_QUALIFICATION_TEST, f"Staging qualification negative rehearsal missing: {label}.")

require("verify-production-eligibility:" in PROTECTED_APPLY, "Protected apply must have a no-secret gate job.")
require(
    PROTECTED_APPLY.index("verify-production-eligibility:") < PROTECTED_APPLY.index("environment: production-vps"),
    "Verifier must run before any production-vps environment attachment.",
)
require("environment: production-vps" not in pre_secret_gate, "Verifier must not attach production-vps.")
for forbidden in (
    "NUTSNEWS_VPS_SSH_PRIVATE_KEY",
    "NUTSNEWS_APP_ENVS_JSON",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "secrets.",
):
    require(forbidden not in pre_secret_gate, f"Verifier must not access production authority: {forbidden}.")

for required in (
    "Verify exact staged qualification",
    "gh attestation verify",
    "verify_production_eligibility.py verify",
):
    require(required in pre_secret_gate, f"No-secret verifier missing required attestation check: {required}.")

for required in (
    "needs: verify-production-eligibility",
    "environment: production-vps",
    "Verify released Docker image over SSH",
    "Verify released public health identity",
    "Run safe production app smoke surfaces",
):
    require(required in baseline_job, f"Protected apply missing required gate/app verification: {required}.")

for required in (
    "workflow_run:",
    "Qualify Verified NutsNews Staging Candidate",
    "workflow_dispatch:",
    "qualification_run_id:",
    "promote-qualified-staging-release",
    "Verify production Supabase schema contract",
    "api/runtime-config",
    "production-supabase-migration.yml",
    "Verify staging qualification attestation is current",
    "verify_production_eligibility.py verify",
    "Request and wait for Vercel production deploy",
    "NUTSNEWS_APP_RELEASE_TOKEN",
    "nutsnews-vercel-production-release",
    "STAGING_DEPLOYMENT_ID",
    "QUALIFICATION_RUN_ID",
    'release_kind: "release"',
):
    require(required in PROMOTION, f"Promotion workflow missing staging-qualified production gate: {required}.")
require("repository_dispatch:" not in PROMOTION, "Promotion workflow must not accept direct production repository dispatch.")
require("nutsnews-production-release" not in PROMOTION, "Promotion workflow must not accept the old direct production event.")
require("environment: production-vps" not in PROMOTION, "Old promotion workflow must not attach production-vps.")
require("NUTSNEWS_INFRA_RELEASE_TOKEN" in PROMOTION, "Promotion workflow must use the existing release token for GitOps mechanics.")
require(
    PROMOTION.index("Verify staging qualification attestation is current") < PROMOTION.index("NUTSNEWS_INFRA_RELEASE_TOKEN"),
    "Promotion workflow must not expose the release token before staging qualification is reverified.",
)
require(
    PROMOTION.index("Verify production Supabase schema contract")
    < PROMOTION.index("Verify staging qualification attestation is current")
    < PROMOTION.index("Create or reuse the checked release promotion pull request"),
    "Promotion workflow must pass Supabase and attestation gates before the GitOps PR.",
)
require(
    PROMOTION.index("Verify staging qualification attestation is current")
    < PROMOTION.index("gh workflow run protected-ansible-apply.yml"),
    "Promotion workflow must reverify staging qualification before protected apply dispatch.",
)
require(
    PROMOTION.index("gh workflow run protected-ansible-apply.yml")
    < PROMOTION.index("nutsnews-vercel-production-release"),
    "Promotion workflow must wait for protected VPS apply before dispatching Vercel production.",
)

for required in (
    "repository_dispatch:",
    "nutsnews-production-vps-release",
    "Validate pre-merge production candidate payload",
    "repository_dispatch client_payload must not exceed 10 top-level keys",
    "release_manifest_mode=premerge_candidate",
    "--field enable_staging_access=true",
    "gh workflow run protected-ansible-apply.yml",
    "gh run watch \"$run_id\"",
):
    require(required in PREMERGE_PRODUCTION, f"Pre-merge production workflow missing guardrail: {required}.")
require("environment: production-vps" not in PREMERGE_PRODUCTION, "Pre-merge dispatcher must not attach production-vps directly.")
require(
    PREMERGE_PRODUCTION.index("Validate pre-merge production candidate payload")
    < PREMERGE_PRODUCTION.index("gh workflow run protected-ansible-apply.yml"),
    "Pre-merge production workflow must validate the compact payload before dispatching protected apply.",
)

for required in (
    "environment: production-vps",
    "rollback-recorded-last-known-good",
    "rollback_nutsnews_release.py",
    "gh workflow run protected-ansible-apply.yml",
    "--field enable_staging_access=true",
):
    require(required in ROLLBACK, f"Fixed rollback workflow missing guardrail: {required}.")
require(
    "verify_production_eligibility.py verify-rollback" in PROTECTED_APPLY,
    "Protected apply must verify fixed rollback eligibility before app restore.",
)
for forbidden in ("docker compose", "ssh ", "restored_image_digest:", "NUTSNEWS_APP_IMAGE_TAG", ":latest"):
    require(forbidden not in ROLLBACK, f"Rollback workflow must not expose bypass surface: {forbidden}.")

require("repository_dispatch:" in STAGING_DEPLOY, "Staging deploy must accept the app handoff event.")
require("workflow_dispatch:" in STAGING_DEPLOY, "Staging deploy must keep a controlled rehearsal path.")
require("environment: staging-vps" in STAGING_DEPLOY, "Staging apply must attach only staging-vps.")
require(
    "environment: staging-vps" not in job_block(STAGING_DEPLOY, "preflight", "rehearsal"),
    "Staging preflight must not attach staging-vps.",
)
require("production-vps" not in STAGING_DEPLOY, "Staging deploy must not reference production-vps.")
require("nutsnews-production-release" not in STAGING_DEPLOY, "Staging deploy must not trigger production release.")
require("cancel-in-progress: false" in STAGING_DEPLOY, "Concurrent staging candidates must serialize, not cancel history.")

require("environment: staging-tests" in QUALIFIER, "Qualifier must attach only staging-tests.")
require("production-vps" not in QUALIFIER, "Qualifier must not reference production-vps.")
require(
    "staging-qualification-${{ steps.deployment.outputs.staging_deployment_id || 'unresolved' }}-${{ github.run_id }}-${{ github.run_attempt }}" in QUALIFIER,
    "Qualifier evidence artifact must include deployment ID, run ID, and attempt.",
)
require("cancel-in-progress: false" in QUALIFIER, "Qualifier reruns must not overwrite or cancel history.")

for workflow_path in WORKFLOWS.glob("*.yml"):
    text = workflow_path.read_text(encoding="utf-8")
    if workflow_path.name in {
        "protected-ansible-apply.yml",
        "protected-nutsnews-rollback.yml",
        "nutsnews-release-promotion.yml",
        "nutsnews-premerge-production-vps-deploy.yml",
        "nutsnews-staging-deploy.yml",
        "nutsnews-staging-qualification.yml",
    }:
        continue
    app_mutation_tokens = (
        "release_image_digest",
        "vps_service_foundation_nutsnews_app_image_digest",
        "promote_nutsnews_release.py",
        "nutsnews-app@",
        "NUTSNEWS_APP_IMAGE_TAG",
    )
    require(
        not any(token in text for token in app_mutation_tokens),
        f"{workflow_path.name} must not mutate the production app digest outside the gate.",
    )

for forbidden in ("NUTSNEWS_APP_IMAGE_TAG", "image_tag:", "ghcr.io/ramideltoro/nutsnews:latest"):
    combined = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
    require(forbidden not in combined, f"Workflow bypass uses mutable image input: {forbidden}.")

for required in (
    "validate_gate_rehearsal.py",
    "validate_production_eligibility.py",
    "validate_staging_qualification.py",
    "validate_production_rollback.py",
):
    require(required in WORKFLOW_SAFETY, f"Workflow safety must run {required}.")

for required in (
    '"release_gate"',
    '"state": "not configured"',
    '"health_state": "unknown"',
    '"supersession_state": "unknown"',
    '"failed"',
    '"expired"',
    '"superseded"',
):
    require(required in PORTAL_STATUS, f"Portal fixture missing release state coverage: {required}.")

for required in (
    "release_gate",
    "state_catalog",
    "candidate",
    "qualification",
    "production",
    "rollback",
    "gate_timestamp_state",
):
    require(required in COLLECTOR, f"Collector missing release gate state field: {required}.")

for required in (
    "release-gate-grid",
    "Candidate",
    "Staging deploy",
    "Qualification",
    "Supersession",
    "Previous digest",
):
    require(required in PORTAL_JS or required in (ROOT / "portal/index.html").read_text(encoding="utf-8"), f"Portal UI missing release gate field: {required}.")

production_env_workflows = {
    path.name
    for path in WORKFLOWS.glob("*.yml")
    if "environment: production-vps" in path.read_text(encoding="utf-8")
}
allowed_production_env_workflows = {
    "grafana-cloud-apply.yml",
    "grafana-cloud-plan.yml",
    "grafana-state-bootstrap.yml",
    "protected-ansible-apply.yml",
    "protected-nutsnews-rollback.yml",
    "protected-vercel-provider-switch.yml",
    "protected-vps-maintenance.yml",
    "run-vps-backup.yml",
    "send-vps-health-report.yml",
    "verify-ops-portal-status.yml",
    "verify-vps-backup.yml",
}
require(
    production_env_workflows <= allowed_production_env_workflows,
    "Unexpected production-vps workflow(s): " + ", ".join(sorted(production_env_workflows - allowed_production_env_workflows)),
)

print("Staging gate rehearsal and bypass inventory guardrails passed.")
