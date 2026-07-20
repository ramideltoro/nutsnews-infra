#!/usr/bin/env python3
"""Regression coverage for automatic, reviewed NutsNews VPS releases."""

from __future__ import annotations

import importlib.util
import re
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT_PATH = ROOT / "scripts/promote_nutsnews_release.py"
PROMOTION_WORKFLOW = REPO / ".github/workflows/nutsnews-release-promotion.yml"
PROTECTED_WORKFLOW = REPO / ".github/workflows/protected-ansible-apply.yml"
PAUSE_CONFIG = REPO / ".github/release-promotion-pause.yml"


spec = importlib.util.spec_from_file_location("promote_nutsnews_release", SCRIPT_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest(
    digest: str,
    source_commit: str,
    build_id: str,
    migration_head: str,
    schema_version: str,
    supabase_project_ref: str,
    last_known_good: str = "",
) -> str:
    return "\n".join(
        (
            "vps_service_foundation_nutsnews_app_enabled: true",
            "vps_service_foundation_nutsnews_app_staged_route_enabled: true",
            "vps_service_foundation_nutsnews_app_public_route_enabled: true",
            "vps_service_foundation_nutsnews_app_image_repo: ghcr.io/ramideltoro/nutsnews",
            f'vps_service_foundation_nutsnews_app_image_digest: "{digest}"',
            f'vps_service_foundation_nutsnews_app_source_commit: "{source_commit}"',
            f'vps_service_foundation_nutsnews_app_build_id: "{build_id}"',
            f'vps_service_foundation_nutsnews_app_config_generation: "production-{build_id}-{migration_head}"',
            f'vps_service_foundation_nutsnews_app_migration_head: "{migration_head}"',
            f'vps_service_foundation_nutsnews_app_schema_version: "{schema_version}"',
            f'vps_service_foundation_nutsnews_app_supabase_project_ref: "{supabase_project_ref}"',
            "vps_service_foundation_nutsnews_app_deployment_target: production-vps",
            f'vps_service_foundation_nutsnews_app_last_known_good_digest: "{last_known_good}"',
            "vps_service_foundation_nutsnews_app_secret_env_keys: []",
            "vps_service_foundation_nutsnews_app_required_secrets: []",
            "",
        )
    )


old_digest = "sha256:" + "a" * 64
new_digest = "sha256:" + "b" * 64
old_commit = "a" * 40
new_commit = "b" * 40
migration_head = "20260713000000"
schema_version = "20260712170000"
supabase_project_ref = "mpqfulvvagyzqneiaqky"

with tempfile.TemporaryDirectory() as temporary_directory:
    path = Path(temporary_directory) / "vps.nutsnews.com.yml"
    path.write_text(
        manifest(
            old_digest,
            old_commit,
            "101-1",
            migration_head,
            schema_version,
            supabase_project_ref,
        ),
        encoding="utf-8",
    )

    result = module.promote_manifest(
        path,
        "ghcr.io/ramideltoro/nutsnews",
        new_digest,
        new_commit,
        "202-3",
        migration_head,
        schema_version,
        supabase_project_ref,
        write=True,
    )
    values = module.manifest_values(path.read_text(encoding="utf-8"))
    assert result["changed"] == "true"
    assert result["previous_digest"] == old_digest
    assert values["vps_service_foundation_nutsnews_app_image_digest"] == new_digest
    assert values["vps_service_foundation_nutsnews_app_source_commit"] == new_commit
    assert values["vps_service_foundation_nutsnews_app_build_id"] == "202-3"
    assert values["vps_service_foundation_nutsnews_app_config_generation"] == f"production-202-3-{migration_head}"
    assert values["vps_service_foundation_nutsnews_app_migration_head"] == migration_head
    assert values["vps_service_foundation_nutsnews_app_schema_version"] == schema_version
    assert values["vps_service_foundation_nutsnews_app_supabase_project_ref"] == supabase_project_ref
    assert values["vps_service_foundation_nutsnews_app_last_known_good_digest"] == old_digest

    verified = module.verify_manifest(
        path,
        "ghcr.io/ramideltoro/nutsnews",
        new_digest,
        new_commit,
        "202-3",
        migration_head,
        schema_version,
        supabase_project_ref,
    )
    assert verified["deployment_target"] == "production-vps"

    original = path.read_text(encoding="utf-8")
    for invalid_digest, invalid_commit, invalid_build_id, invalid_head, invalid_schema, invalid_project_ref in (
        ("latest", new_commit, "202-3", migration_head, schema_version, supabase_project_ref),
        (new_digest, "not-a-commit", "202-3", migration_head, schema_version, supabase_project_ref),
        (new_digest, new_commit, "build-202", migration_head, schema_version, supabase_project_ref),
        (new_digest, new_commit, "202-3", "latest", schema_version, supabase_project_ref),
        (new_digest, new_commit, "202-3", migration_head, "legacy", supabase_project_ref),
        (new_digest, new_commit, "202-3", migration_head, schema_version, "staging"),
    ):
        try:
            module.promote_manifest(
                path,
                "ghcr.io/ramideltoro/nutsnews",
                invalid_digest,
                invalid_commit,
                invalid_build_id,
                invalid_head,
                invalid_schema,
                invalid_project_ref,
                write=True,
            )
        except module.PromotionError:
            pass
        else:
            raise AssertionError("Invalid immutable release input must be rejected.")
        assert path.read_text(encoding="utf-8") == original

promotion_workflow = PROMOTION_WORKFLOW.read_text(encoding="utf-8")
protected_workflow = PROTECTED_WORKFLOW.read_text(encoding="utf-8")

for required in (
    "workflow_run:",
    "Qualify Verified NutsNews Staging Candidate",
    "workflow_dispatch:",
    "qualification_run_id:",
    "promote-qualified-staging-release",
    "gh run view \"$qualification_run_id\"",
    "gh run download \"$qualification_run_id\"",
    "staging-qualification-*",
    "*/qualification/staging-qualification.json",
    "staging_qualification.py validate-record",
    "Verify qualified source commit remains reachable from NutsNews main",
    "compare/{source_commit}...main",
    "Checkout exact app source for release contract",
    "getMigrationContract",
    "readApplicationMigrationContract",
    "Verify production Supabase schema contract",
    "api/runtime-config",
    "Production runtime config",
    "nutsnews_migration_schema_contract",
    "production-supabase-migration.yml",
    "Verify staging qualification attestation is current",
    "gh attestation verify",
    "verify_production_eligibility.py verify",
    "timeout-minutes: 180",
    "git fetch origin main --prune",
    "current-vps-release.yml",
    'git switch -c "$release_branch" origin/main',
    'dispatch_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
    "--json databaseId,displayTitle,status,createdAt",
    "Wait for promotion checks to pass and merge",
    "gh pr checks \"$PR_URL\" --json name,bucket",
    "Promotion checks did not pass before the timeout.",
    "gh pr merge \"$PR_URL\" --merge",
    "GH_TOKEN: ${{ secrets.NUTSNEWS_INFRA_RELEASE_TOKEN }}",
    "NUTSNEWS_INFRA_RELEASE_TOKEN is required for release GitOps automation.",
    "NUTSNEWS_INFRA_RELEASE_TOKEN is required to dispatch Protected Ansible Apply.",
    "gh workflow run protected-ansible-apply.yml",
    "--field production_writes_paused",
    "--field release_smoke_helper_ref",
    "smoke_helper_ref=",
    "steps.app_main.outputs.smoke_helper_ref",
    "gh run watch \"$run_id\"",
    "--exit-status",
    "Request and wait for Vercel production deploy",
    "NUTSNEWS_APP_RELEASE_TOKEN",
    "nutsnews-vercel-production-release",
    "PRODUCTION_WRITES_PAUSED",
    "STAGING_DEPLOYMENT_ID",
    "QUALIFICATION_RUN_ID",
    'release_kind: "release"',
    "DATABASE_PROVIDER_MODE: backend_postgres_primary",
    "BACKEND_API_URL: https://backend.nutsnews.com/api/app/db",
    "PROVIDER_SWITCH_CONFIRMATION: enable-backend-postgres-primary",
    "Backend PostgreSQL primary Vercel release requires provider switch confirmation.",
    "provider_switch:",
    "database_provider_mode: process.env.DATABASE_PROVIDER_MODE",
    "--workflow vercel-production-release.yml",
    "--repo ramideltoro/nutsnews",
    "--jq '.conclusion // \"unknown\"'",
    "deploy_failed=$([[ \"$vercel_status\" == \"failed\" ]] && echo true || echo false)",
    "ansible/scripts/promote_nutsnews_release.py",
    "MIGRATION_HEAD",
    "SCHEMA_VERSION",
    "SUPABASE_PROJECT_REF",
    "--migration-head",
    "--schema-version",
    "--supabase-project-ref",
):
    assert required in promotion_workflow, f"Promotion workflow is missing required guardrail: {required}"

assert "await verifyHealth(deploymentUrl)" not in promotion_workflow, (
    "Promotion must not probe the protected Vercel deployment URL for health; use the public production alias."
)

assert (
    promotion_workflow.index("Verify staging qualification attestation is current")
    < promotion_workflow.index("current-vps-release.yml")
    < promotion_workflow.index("gh pr list")
), "A rerun must verify staging qualification before it reuses or creates a release pull request."
assert (
    promotion_workflow.index("Verify production Supabase schema contract")
    < promotion_workflow.index("Verify staging qualification attestation is current")
), "Production Supabase gate must pass before promotion PR creation."
assert (
    promotion_workflow.index("Verify staging qualification attestation is current")
    < promotion_workflow.index("NUTSNEWS_INFRA_RELEASE_TOKEN")
), "Release automation token must be used only after the staging qualification gate."
assert (
    promotion_workflow.index("current-vps-release.yml")
    < promotion_workflow.index("gh pr list")
), "A rerun must verify current main before it reuses or creates a release pull request."
assert (
    promotion_workflow.index("gh workflow run protected-ansible-apply.yml")
    < promotion_workflow.index("nutsnews-vercel-production-release")
), "Vercel production must deploy only after the protected VPS apply is started and watched."
protected_apply_dispatch_step = promotion_workflow.split(
    "- name: Start and wait for protected VPS apply",
    1,
)[1].split("- name: Request and wait for Vercel production deploy", 1)[0]
assert "SMOKE_HELPER_REF: ${{ steps.app_main.outputs.smoke_helper_ref }}" in protected_apply_dispatch_step, (
    "Protected apply dispatch must export the smoke helper ref before using it under set -u."
)
assert "run.get(\"createdAt\", \"\") >= sys.argv[3]" in promotion_workflow, (
    "The release workflow must select only a protected apply started by its own dispatch."
)

payload_match = re.search(r"const payload = \{\n(?P<body>.*?)\n\s+\};", promotion_workflow, re.DOTALL)
assert payload_match, "The Vercel production dispatch payload must be explicit."
dispatch_payload_keys = re.findall(r"^            ([a-z_]+):", payload_match.group("body"), re.MULTILINE)
assert dispatch_payload_keys == [
    "source_repository",
    "source_commit",
    "image_digest",
    "build_id",
    "vps_apply_run_id",
    "staging_deployment_id",
    "qualification_run_id",
    "release_kind",
    "provider_switch",
    "production_writes_paused",
], "The Vercel dispatch payload should contain only fields consumed by the app workflow."
assert len(dispatch_payload_keys) <= 10, "GitHub repository dispatch rejects more than 10 client_payload keys."

for required in (
    "release_source_commit:",
    "release_image_digest:",
    "release_build_id:",
    "release_smoke_helper_ref:",
    "production_writes_paused:",
    "release_migration_head:",
    "release_schema_version:",
    "release_supabase_project_ref:",
    "Validate requested automated release identity",
    "RELEASE_IMAGE_DEPLOYMENT_TARGET",
    "RELEASE_HEALTH_DEPLOYMENT_TARGET",
    "Verify released Docker image over SSH",
    "Verify released public health identity",
    "Checkout exact app post-production smoke suite",
    "Checkout current app smoke helper",
    "Install current app smoke helper",
    "Run safe production app smoke surfaces",
    "PRODUCTION_WRITES_PAUSED: ${{ inputs.production_writes_paused }}",
    "--expected-production-writes-paused",
    "--production-safe-surfaces",
):
    assert required in protected_workflow, f"Protected apply is missing required release verification: {required}"

assert protected_workflow.count("PRODUCTION_WRITES_PAUSED: ${{ inputs.production_writes_paused }}") >= 3, (
    "Protected apply must validate, materialize, and smoke-test the production write pause input."
)
assert "release_smoke_helper_ref must be a full lowercase SHA when set." in protected_workflow
assert "nutsnews-current-smoke/scripts/dual_target_web_smoke.mjs" in protected_workflow
assert 'release_deployment_target" != "production-vps"' in protected_workflow
assert 'healthDeploymentTarget !== "production-vps"' in protected_workflow
assert 'payload?.deploymentTarget === healthDeploymentTarget' in protected_workflow
assert 'response.headers.get("x-nutsnews-deployment-target") === healthDeploymentTarget' in protected_workflow
assert "--expected-deployment-target production-vps" in protected_workflow
assert "--expected-health-deployment-target production-vps" in protected_workflow

assert "NUTSNEWS_APP_IMAGE_TAG" not in promotion_workflow
assert "repository_dispatch:" not in promotion_workflow
assert "nutsnews-production-release" not in promotion_workflow
assert "VERCEL_DEPLOYMENT_URL" not in promotion_workflow
assert "nutsnews-production-release is paused" not in promotion_workflow
assert ":latest" not in promotion_workflow.lower()
assert "gh pr checks \"$PR_URL\" --required" not in promotion_workflow
assert "environment: production-vps" not in promotion_workflow
assert "contents: write" not in promotion_workflow
assert "actions: write" not in promotion_workflow

promotion_pause_scan = promotion_workflow.replace("production_writes_paused", "production_writes_state")
pause_step = re.search(
    r"(?ms)^\s+- name:\s+.*pause.*?\n(?:(?!^\s+- name:).)*^\s+run:\s*\|\n(?:(?!^\s+- name:).)*^\s+exit\s+1\s*$",
    promotion_pause_scan,
)
permanent_pause_markers = (
    "Pause direct production release dispatch",
    "is paused until",
    "production release is paused",
)
if pause_step or any(marker in promotion_workflow for marker in permanent_pause_markers):
    assert PAUSE_CONFIG.exists(), (
        "A promotion pause must be controlled by .github/release-promotion-pause.yml, "
        "not by an unconditional workflow exit."
    )
    pause_config = PAUSE_CONFIG.read_text(encoding="utf-8")
    assert re.search(r"(?m)^enabled:\s*true\s*$", pause_config), "Pause config must explicitly enable the pause."
    assert re.search(r"(?m)^reason:\s*\"[^\"]{12,}\"\s*$", pause_config), "Pause config must include a reviewed reason."
    assert re.search(r"(?m)^expires_at:\s*\"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\"\s*$", pause_config), (
        "Pause config must include an explicit UTC expiry timestamp."
    )

print("Automatic NutsNews VPS release promotion guardrails passed.")
