#!/usr/bin/env python3
"""Static safety contract for protected Grafana observability failure drills."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/grafana-failure-drill.yml").read_text(encoding="utf-8")
SYNTHETIC_WATCHDOG = (
    ROOT / ".github/workflows/grafana-synthetic-recovery-watchdog.yml"
).read_text(encoding="utf-8")
CONTRACT = json.loads((ROOT / "config/grafana-failure-drills.json").read_text(encoding="utf-8"))
VPS_HOOK = (
    ROOT / "ansible/roles/vps_service_foundation/files/observability_failure_drill.py"
).read_text(encoding="utf-8")
SYNTHETIC = (
    ROOT / "terraform/grafana-cloud/scripts/exercise_synthetic_failure_drill.py"
).read_text(encoding="utf-8")
BACKEND_EVIDENCE_VALIDATOR = (
    ROOT / "scripts/validate_backend_drill_evidence.py"
).read_text(encoding="utf-8")
RUNBOOK = (ROOT / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")

EXPECTED = {
    "alloy-stopped",
    "textfile-stale",
    "worker-unavailable",
    "rabbitmq-zero-consumer",
    "rabbitmq-growing-dlq",
    "postgres-relay-lag",
    "backend-readiness-failed",
    "synthetic-mismatch",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


drills = {item["id"] for item in CONTRACT.get("drills", [])}
require(drills == EXPECTED, "Failure-drill contract must contain exactly the eight approved drills.")
require(CONTRACT.get("default_mode") == "dry-run", "Failure drills must default to dry-run.")
require(
    CONTRACT.get("artifact_retention_days") == 90,
    "Public-repository failure-drill Actions artifacts must use the 90-day maximum.",
)

for drill in EXPECTED:
    require(f"          - {drill}" in WORKFLOW, f"Workflow choice missing exact drill: {drill}.")
require(WORKFLOW.count("          - alloy-stopped") == 1, "Workflow drill choice block is duplicated.")
for variant in ("status", "body", "header"):
    require(f"          - {variant}" in WORKFLOW, f"Synthetic variant missing: {variant}.")

for required in (
    "workflow_dispatch:",
    "default: dry-run",
    "inputs.drill == 'synthetic-mismatch' && 'grafana-cloud-apply' || 'production-vps-ansible-baseline'",
    "queue: max",
    "cancel-in-progress: false",
    "Validate inputs before protected secrets",
    "scripts/grafana_failure_drill.py plan",
    "scripts/grafana_failure_drill.py initialize",
    "execute-grafana-failure-drill:<target>:<drill>",
    "environment: production-vps",
    "if: always()",
    "retention-days: 90",
    "--failsafe-seconds 1800",
    "recover-grafana-failure-drill:vps.nutsnews.com:${DRILL}",
    "Fail-safe restore after any unsuccessful exercise",
    "Dispatch independent synthetic recovery watchdog",
    "Require sanitized armed watchdog handshake before mutation",
    "Require independent watchdog exact restoration evidence",
    'recover-grafana-failure-drill:${SYNTHETIC_CHECK}:synthetic-mismatch',
    "--timeout-seconds 1200",
    "timeout-minutes: 150",
):
    require(required in WORKFLOW, f"Protected drill workflow missing safety control: {required}.")

plan_start = WORKFLOW.index("  plan:")
vps_start = WORKFLOW.index("  execute-vps:")
plan_block = WORKFLOW[plan_start:vps_start]
require("environment:" not in plan_block, "Dry-run planning must occur before protected environment access.")
require("secrets." not in plan_block, "Dry-run planning must not read protected secrets.")
require(vps_start > plan_start, "Protected execution must follow dry-run-first validation.")
require(
    WORKFLOW.count("needs: [plan, audit-production-vps-policy]") == 3,
    "Every executable drill family must depend on validation and the live environment-policy audit.",
)
require(WORKFLOW.count("environment: production-vps") == 3, "Only executable drill jobs may attach production-vps.")
require(
    WORKFLOW.count("Require target Grafana rules to be initially resolved") == 2,
    "VPS and backend drills must prove target rules are resolved before injection.",
)
require(
    WORKFLOW.index("Require target Grafana rules to be initially resolved")
    < WORKFLOW.index("Inject fixed VPS failure after scheduling host recovery"),
    "VPS rule-state precheck must run before injection.",
)
backend_start = WORKFLOW.index("  execute-backend:")
backend_block = WORKFLOW[backend_start:]
require(
    backend_block.index("Require target Grafana rules to be initially resolved")
    < backend_block.index("Dispatch exact protected backend hook"),
    "Backend rule-state precheck must run before protected dispatch.",
)

require("schedule:" not in WORKFLOW, "Destructive drill execution must never be scheduled automatically.")
require("repository_dispatch:" not in WORKFLOW, "Drills must not accept repository dispatch payloads.")
for forbidden in ("refresh", "controller", "ingest", "publish"):
    require(
        f"/{forbidden}" not in WORKFLOW.lower(),
        f"Failure-drill workflow must not call an ingestion-triggering route: {forbidden}.",
    )

require(
    "No backend service, queue, database, or readiness state was changed." in WORKFLOW
    or "backend-observability-failure-drills.yml" in WORKFLOW,
    "Backend drills must be linked to the exact protected hook or fail closed.",
)

for required in (
    "NUTSNEWS_BACKEND_OBSERVABILITY_DRILL_TOKEN",
    "gh workflow run backend-observability-failure-drills.yml",
    "--repo ramideltoro/nutsnews-backend",
    "--field confirm_environment=production-backend",
    "--field confirm_target=backend.nutsnews.com",
    '(.path == ".github/workflows/backend-observability-failure-drills.yml")',
    '--run-attempt "$backend_run_attempt"',
    '--revision "$backend_revision"',
    '--evidence-id "$evidence_id"',
    '--artifact-digest "$artifact_digest"',
    "scripts/validate_backend_drill_evidence.py",
    'if [[ "$DRILL" == "rabbitmq-growing-dlq" ]]',
    "timeout_seconds=1200",
    "backend-observability-failure-drill-evidence",
):
    require(required in WORKFLOW or required in RUNBOOK, f"Backend drill route missing: {required}.")
require(
    WORKFLOW.index('if [[ -z "$GH_TOKEN" ]]')
    < WORKFLOW.index("gh workflow run backend-observability-failure-drills.yml"),
    "Backend drill token must be required before cross-repository dispatch.",
)
require(
    "NUTSNEWS_INFRA_RELEASE_TOKEN" not in WORKFLOW,
    "Backend drills must not silently reuse the broader infra release token.",
)
require(
    "backend drill token is not currently present" in RUNBOOK,
    "Runbook must surface the missing protected backend drill token as a rollout blocker.",
)
for required in (
    "ROOT_KEYS = {",
    "REPORT_KEYS = {",
    'CHECK_KEYS = {"name", "status"}',
    "object_pairs_hook=unique_object",
    "set(value) != expected",
    "set(REPORT_ACTIONS)",
    'evidence.get("workflow") != BACKEND_WORKFLOW',
    'evidence.get("run_id") != run_id',
    'evidence.get("run_attempt") != run_attempt',
    'evidence.get("revision") != revision',
    'evidence.get("evidence_id") != evidence_id',
    'evidence.get("drill") != drill',
    'evidence.get("duration_seconds") != BACKEND_DURATION_SECONDS',
    'evidence.get("dry_run") is not False',
    "downloaded_digest != artifact_digest",
    '"evidence_sha256": evidence_sha256',
):
    require(
        required in BACKEND_EVIDENCE_VALIDATOR,
        f"Backend evidence validator missing strict trust-boundary control: {required}.",
    )
backend_upload = WORKFLOW[WORKFLOW.index("Upload reconstructed value-free backend and Grafana evidence") :]
require(
    "backend-observability-drill-evidence-summary.json" in backend_upload,
    "Backend upload must include the locally reconstructed value-free summary.",
)
for forbidden in (
    "backend-observability-drill-evidence/evidence.json",
    "backend-observability-drill-private",
    "upstream-evidence.zip",
):
    require(
        forbidden not in backend_upload,
        f"Backend upload must never include raw upstream evidence: {forbidden}.",
    )

for required in (
    "schedule_recovery(run_id, failsafe_seconds)",
    'run_command(["systemctl", "stop", ALLOY_UNIT])',
    "recover(run_id, AUTOMATIC_RECOVERY_CONFIRMATION)",
    "collector_is_fresh",
):
    require(required in VPS_HOOK, f"VPS drill hook missing fail-safe control: {required}.")
require(
    VPS_HOOK.index("schedule_recovery(run_id, failsafe_seconds)")
    < VPS_HOOK.index('run_command(["systemctl", "stop", ALLOY_UNIT])'),
    "VPS fail-safe recovery must be scheduled before mutation.",
)
require(
    'if state_value.get("status") == "recovered":\n            cancel_recovery_timer(run_id)' in VPS_HOOK,
    "A failed explicit VPS recovery must retain the independently scheduled retry.",
)

for required in (
    'return self.request("POST", f"/api/v1/check/{check_id}", payload)',
    "private_snapshot_write(snapshot, remote, base)",
    "finally:",
    "restore_exact_if_owned(",
    "validate_single_mutation(base, result, variant)",
    "active_alert_instances",
    'alert.get("alert_uid") == ALERT_UID and alert.get("job") == JOB',
    "observed_probes == expected_probes",
    'expected_probes=set(precheck["probes"])',
):
    require(required in SYNTHETIC, f"Synthetic drill missing exact restoration control: {required}.")
for target in (
    "canonical_articles_api",
    "canonical_homepage",
    "canonical_readiness",
    "vercel_secondary_readiness",
    "vps_readiness",
):
    require(target in SYNTHETIC and f"          - {target}" in WORKFLOW, f"Synthetic drill target missing: {target}.")
require(
    SYNTHETIC.index("private_snapshot_write(snapshot, remote, base)")
    < SYNTHETIC.index("sm_client.update_check(check_id, changed)"),
    "Synthetic snapshot must be saved before the first mutation request.",
)
require(
    WORKFLOW.index("Dispatch independent synthetic recovery watchdog")
    < WORKFLOW.index("Require sanitized armed watchdog handshake before mutation")
    < WORKFLOW.index("exercise_synthetic_failure_drill.py execute"),
    "Independent synthetic recovery must be armed before the parent mutation helper runs.",
)
for required in (
    "grafana-synthetic-recovery-${{ inputs.synthetic_check }}",
    "queue: max",
    "cancel-in-progress: false",
    "Fetch exact remote check and arm private recovery snapshot",
    "Publish sanitized armed recovery handshake",
    "deadline=$((SECONDS + 7200))",
    "Exact-restore and verify after release or bounded wait",
    "exercise_synthetic_failure_drill.py restore",
    "private_snapshot_uploaded: false",
):
    require(
        required in SYNTHETIC_WATCHDOG,
        f"Independent synthetic recovery watchdog missing safety control: {required}.",
    )
require(
    "grafana-cloud-apply" not in SYNTHETIC_WATCHDOG[
        SYNTHETIC_WATCHDOG.index("concurrency:") : SYNTHETIC_WATCHDOG.index("jobs:")
    ],
    "The child watchdog must not deadlock the parent apply/drill concurrency group.",
)
require(
    "path: ${{ runner.temp }}/synthetic-watchdog-private" not in SYNTHETIC_WATCHDOG,
    "The private Synthetic Monitoring snapshot must never be uploaded.",
)

for drill in EXPECTED:
    require(drill in RUNBOOK, f"Grafana runbook must document the {drill} drill.")
for token in ("90 days", "operator-owned durable evidence store"):
    require(token in RUNBOOK, f"Grafana runbook must document durable drill evidence retention: {token}.")

print("Grafana failure-drill workflow safety validation passed.")
