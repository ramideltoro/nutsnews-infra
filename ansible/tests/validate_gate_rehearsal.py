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
SCHEMA_CONTRACT_SCRIPT = (ROOT / "scripts/verify_production_schema_contract.mjs").read_text(encoding="utf-8")
STAGING_DEPLOY = (WORKFLOWS / "nutsnews-staging-deploy.yml").read_text(encoding="utf-8")
QUALIFIER = (WORKFLOWS / "nutsnews-staging-qualification.yml").read_text(encoding="utf-8")
PREMERGE_PRODUCTION = (WORKFLOWS / "nutsnews-premerge-production-vps-deploy.yml").read_text(
    encoding="utf-8"
)
GRAFANA_DEPLOYMENT_ANNOTATION = (
    WORKFLOWS / "grafana-deployment-annotation.yml"
).read_text(encoding="utf-8")
GRAFANA_FAILOVER_ANNOTATION = (
    WORKFLOWS / "grafana-failover-annotation.yml"
).read_text(encoding="utf-8")
PROVIDER_SWITCH = (WORKFLOWS / "protected-vercel-provider-switch.yml").read_text(
    encoding="utf-8"
)
FAILOVER_APPLY = (WORKFLOWS / "cloudflare-dns-failover-apply.yml").read_text(
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
GRAFANA_READONLY_RUNBOOK = (
    ROOT / "runbooks/GRAFANA_OBSERVABILITY_READONLY_ENVIRONMENT.md"
).read_text(encoding="utf-8")


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
    "needs: [verify-production-eligibility, audit-production-vps-policy]",
    "environment: production-vps",
    "Verify released Docker image over SSH",
    "Verify released public health identity",
    "Run safe production app smoke surfaces",
    "Run production admin backend operation smoke",
    "NUTSNEWS_BACKEND_API_TOKEN",
):
    require(required in baseline_job, f"Protected apply missing required gate/app verification: {required}.")

for required in (
    "workflow_run:",
    "Qualify Verified NutsNews Staging Candidate",
    "workflow_dispatch:",
    "qualification_run_id:",
    "promote-qualified-staging-release",
    "Verify production Supabase schema contract",
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
for required in ("api/runtime-config", "production-supabase-migration.yml"):
    require(required in SCHEMA_CONTRACT_SCRIPT, f"Production schema verifier missing required gate: {required}.")
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
    "--field sync_vercel_production=true",
    "--field release_smoke_helper_ref",
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
require("queue: max" in STAGING_DEPLOY, "Concurrent staging candidates must not replace a pending run.")

require("environment: staging-tests" in QUALIFIER, "Qualifier must attach only staging-tests.")
require("production-vps" not in QUALIFIER, "Qualifier must not reference production-vps.")
require(
    "staging-qualification-${{ steps.deployment.outputs.staging_deployment_id || 'unresolved' }}-${{ github.run_id }}-${{ github.run_attempt }}" in QUALIFIER,
    "Qualifier evidence artifact must include deployment ID, run ID, and attempt.",
)
require("cancel-in-progress: false" in QUALIFIER, "Qualifier reruns must not overwrite or cancel history.")
require("queue: max" in QUALIFIER, "Qualifier reruns must retain every pending qualification.")

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

annotation_job = job_block(GRAFANA_DEPLOYMENT_ANNOTATION, "publish")
annotation_trust_gate = (
    "if: ${{ github.repository == 'ramideltoro/nutsnews-infra' && "
    "github.ref == 'refs/heads/main' }}"
)
for required in (
    "workflow_dispatch:",
    "workflow_call:",
    annotation_trust_gate,
    "environment: production-vps",
    "publish_deployment_annotation.py",
    "NUTSNEWS_GRAFANA_CLOUD_URL",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "Report annotation delivery failure",
):
    require(required in GRAFANA_DEPLOYMENT_ANNOTATION, f"Grafana annotation workflow missing guardrail: {required}.")
annotation_call_contract = GRAFANA_DEPLOYMENT_ANNOTATION.split("  workflow_call:", 1)[1].split(
    "\npermissions:", 1
)[0]
for required_secret in (
    "NUTSNEWS_GRAFANA_CLOUD_URL",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
):
    require(
        f"      {required_secret}:" in annotation_call_contract,
        f"Grafana annotation workflow_call must declare only the required secret {required_secret}.",
    )
require(
    annotation_call_contract.count("        required: true") == 8,
    "Grafana annotation workflow_call must keep six required inputs and exactly two required secrets.",
)
require(
    annotation_job.index(annotation_trust_gate) < annotation_job.index("environment: production-vps"),
    "Grafana annotation workflow must reject non-main callers before attaching production-vps.",
)
require(
    annotation_job.count("continue-on-error: true") == 1,
    "Only Grafana annotation receipt upload may remain best effort.",
)
annotation_delivery = annotation_job.split(
    "- name: Checkout reviewed annotation publisher", 1
)[1].split("- name: Retain deployment annotation receipt", 1)[0]
require(
    "continue-on-error: true" not in annotation_delivery,
    "Grafana annotation checkout and publish must fail the reusable workflow.",
)
require(
    "Report annotation delivery failure" in annotation_delivery
    and "exit 1" in annotation_delivery,
    "Grafana annotation failure report must preserve a failed conclusion.",
)

failover_validation_job = job_block(
    GRAFANA_FAILOVER_ANNOTATION,
    "validate-confirmed-transition",
    "publish-confirmed-transition",
)
failover_publish_job = job_block(GRAFANA_FAILOVER_ANNOTATION, "publish-confirmed-transition")
for required in (
    "workflow_dispatch:",
    "workflow_call:",
    annotation_trust_gate,
    "record-confirmed-production-failover",
    '{"production-vps", "vercel-production"}',
    '{"succeeded", "rolled-back"}',
    "provide either source_commit or release_identity",
    "https://github.com/ramideltoro/<repo>/actions/runs/<run-id>",
    'parsed.netloc != "github.com"',
    'any(segment in {".", ".."} for segment in parsed.path.split("/"))',
    'r"[1-9][0-9]*(?:/artifacts/[1-9][0-9]*)?"',
):
    require(required in GRAFANA_FAILOVER_ANNOTATION, f"Confirmed failover annotation missing guardrail: {required}.")
require(
    'parsed.path.startswith("/ramideltoro/")' not in GRAFANA_FAILOVER_ANNOTATION,
    "Confirmed failover evidence must not accept arbitrary ramideltoro GitHub paths.",
)
require(
    "environment: production-vps" not in failover_validation_job,
    "Failover evidence validation must complete before protected Grafana secrets are attached.",
)
failover_publish_permissions = failover_publish_job.split("    permissions:", 1)[1].split(
    "    uses:", 1
)[0]
require(
    re.search(r"(?m)^      actions: read(?:\s+#.*)?$", failover_publish_permissions) is not None
    and re.search(r"(?m)^      contents: read(?:\s+#.*)?$", failover_publish_permissions) is not None,
    "Confirmed failover publication must grant actions:read to the nested environment-policy audit and contents:read to the publisher.",
)
for required in (
    "needs: validate-confirmed-transition",
    "uses: ./.github/workflows/grafana-deployment-annotation.yml",
    "event_type: failover",
    "image_digest: not-applicable",
    "evidence: ${{ inputs.transition_evidence_url }}",
    "NUTSNEWS_GRAFANA_CLOUD_URL: ${{ secrets.NUTSNEWS_GRAFANA_CLOUD_URL }}",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN: ${{ secrets.NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN }}",
):
    require(required in failover_publish_job, f"Confirmed failover publication missing guardrail: {required}.")
require(
    "secrets: inherit" not in failover_publish_job,
    "Confirmed failover publication must pass only the two Grafana annotation secrets.",
)
for forbidden in ("started", "failed"):
    options = GRAFANA_FAILOVER_ANNOTATION.split("outcome:", 1)[1].split("source_commit:", 1)[0]
    require(
        f"          - {forbidden}\n" not in options,
        f"Confirmed failover workflow must not offer {forbidden} as a completed transition outcome.",
    )

promotion_annotation = job_block(PROMOTION, "annotate-promotion")
for required in (
    "always()",
    "continue-on-error: true",
    "gh workflow run grafana-deployment-annotation.yml",
    "--ref main",
    "--field event_type=promotion",
    "--field target=production-vps",
    "--field evidence=\"$PROMOTION_RUN_URL\"",
    "'rolled-back'",
    "'succeeded'",
    "'failed'",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "Record final annotation dispatch failure without changing promotion authority",
    "The completed production promotion result remains authoritative",
):
    require(required in promotion_annotation, f"Promotion annotation wiring missing: {required}.")
require(
    "uses: ./.github/workflows/grafana-deployment-annotation.yml" not in promotion_annotation,
    "Promotion annotation delivery must run separately from promotion authority.",
)
for forbidden_secret in (
    "NUTSNEWS_GRAFANA_CLOUD_URL",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "secrets: inherit",
):
    require(
        forbidden_secret not in promotion_annotation,
        f"Promotion dispatch must not receive separate-workflow secret {forbidden_secret}.",
    )
require(
    "exit 1" not in promotion_annotation.split(
        "- name: Record final annotation dispatch failure without changing promotion authority", 1
    )[1],
    "Promotion annotation dispatch reporting must not fail an authoritative promotion.",
)

promotion_start_annotation = PROMOTION.split(
    "- name: Queue Grafana promotion start annotation", 1
)[1].split("- name: Report promotion start annotation delivery failure", 1)[0]
for required in (
    "continue-on-error: true",
    "gh workflow run grafana-deployment-annotation.yml",
    "--ref main",
    "--field outcome=started",
):
    require(required in promotion_start_annotation, f"Promotion start annotation wiring missing: {required}.")
require(
    PROMOTION.index("Queue Grafana promotion start annotation")
    < PROMOTION.index("Start and wait for protected VPS apply"),
    "Promotion start annotation must be queued before protected VPS apply.",
)

rollback_start_annotation = ROLLBACK.split("- name: Publish rollback start annotation", 1)[1].split(
    "- name: Report rollback start annotation delivery failure", 1
)[0]
for required in (
    "continue-on-error: true",
    "--event-type rollback",
    "--outcome started",
):
    require(required in rollback_start_annotation, f"Rollback start annotation wiring missing: {required}.")
require(
    ROLLBACK.index("Publish rollback start annotation")
    < ROLLBACK.index("Create rollback pull request"),
    "Rollback start annotation must be published before creating the rollback pull request.",
)

rollback_annotation = ROLLBACK.split("- name: Publish final rollback outcome", 1)[1].split(
    "- name: Report rollback annotation delivery failure", 1
)[0]
for required in (
    "always()",
    "continue-on-error: true",
    "--event-type rollback",
    "outcome=rolled-back",
):
    require(required in rollback_annotation, f"Rollback annotation wiring missing: {required}.")

provider_start_annotation = PROVIDER_SWITCH.split(
    "- name: Publish database provider change start annotation", 1
)[1].split("- name: Report provider start annotation delivery failure", 1)[0]
for required in (
    "if: inputs.operation == 'apply'",
    "continue-on-error: true",
    "--event-type database-provider-change",
    "--outcome started",
):
    require(required in provider_start_annotation, f"Provider start annotation wiring missing: {required}.")
require(
    "dispatch_vercel_release" not in provider_start_annotation,
    "Provider start annotations must cover every applied provider mutation, even without a release dispatch.",
)
require(
    PROVIDER_SWITCH.index("Publish database provider change start annotation")
    < PROVIDER_SWITCH.index("Plan or apply Vercel provider switch"),
    "Provider start annotation must precede production provider-variable mutation.",
)

provider_annotation = PROVIDER_SWITCH.split(
    "- name: Publish final database provider change outcome", 1
)[1].split("- name: Report provider annotation delivery failure", 1)[0]
for required in (
    "always()",
    "inputs.operation == 'apply'",
    "continue-on-error: true",
    "--event-type database-provider-change",
):
    require(required in provider_annotation, f"Provider annotation wiring missing: {required}.")
require(
    "dispatch_vercel_release" not in provider_annotation,
    "Provider outcome annotations must cover every applied provider mutation, even without a release dispatch.",
)

for forbidden in ("publish_deployment_annotation.py", "--event-type failover", "event_type: failover"):
    require(
        forbidden not in FAILOVER_APPLY,
        "Failover-controller deployment must not emit a false DNS failover annotation: " + forbidden,
    )

production_env_workflows = {
    path.name
    for path in WORKFLOWS.glob("*.yml")
    if "environment: production-vps" in path.read_text(encoding="utf-8")
}
synthetic_audit_workflow = (WORKFLOWS / "grafana-cloud-synthetic-audit.yml").read_text(
    encoding="utf-8"
)
for token in (
    'cron: "17 8 * * *"',
    "permissions:\n  contents: read",
    "environment: grafana-observability-readonly",
    "github.repository == 'ramideltoro/nutsnews-infra'",
    "github.ref == 'refs/heads/main'",
    "persist-credentials: false",
    "NUTSNEWS_GRAFANA_SYNTHETIC_EXPECTED_INVENTORY_JSON",
    "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_READONLY_ACCESS_TOKEN",
    "audit_synthetic_inventory.py",
    "Upload sanitized audit evidence",
    "retention-days: 90",
):
    require(token in synthetic_audit_workflow, f"Synthetic inventory audit guard missing: {token}")
for forbidden in (
    "environment: production-vps",
    "audit-production-vps-policy",
    "NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN",
    "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN",
    "tofu apply",
    "terraform apply",
    "ssh ",
    "workflow_call:",
):
    require(
        forbidden not in synthetic_audit_workflow,
        f"Synthetic inventory audit must stay read-only: {forbidden}",
    )
require("write" not in synthetic_audit_workflow.split("permissions:", 1)[1].split("concurrency:", 1)[0],
        "Synthetic inventory audit must not grant a write permission.")
for token in (
    "grafana-observability-readonly",
    "exact `main` branch",
    "Leave **Required reviewers** empty",
    "least-privilege",
    "read checks and probes only",
    "do not add the OpenTofu backend configuration",
    "do not grant checks, probes, alerts",
):
    require(token in GRAFANA_READONLY_RUNBOOK, f"Read-only Grafana Environment runbook missing: {token}")
allowed_production_env_workflows = {
    "grafana-cloud-apply.yml",
    "grafana-deployment-annotation.yml",
    "grafana-failure-drill.yml",
    "grafana-notification-canary.yml",
    "grafana-synthetic-recovery-watchdog.yml",
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
require(
    "production-vps-environment-policy-audit.yml" not in production_env_workflows,
    "The read-only environment-policy prerequisite must never attach production-vps.",
)

print("Staging gate rehearsal and bypass inventory guardrails passed.")
