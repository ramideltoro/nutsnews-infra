#!/usr/bin/env python3
"""Regression coverage for the no-secret production eligibility gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT = ROOT / "scripts/verify_production_eligibility.py"
PROTECTED_WORKFLOW = REPO / ".github/workflows/protected-ansible-apply.yml"
PROMOTION_WORKFLOW = REPO / ".github/workflows/nutsnews-release-promotion.yml"

spec = importlib.util.spec_from_file_location("verify_production_eligibility", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
SOURCE_COMMIT = "c" * 40
INFRA_COMMIT = "d" * 40
BUILD_ID = "29454959927-1"
SOURCE_RUN_ID = "29454959927"
DEPLOYMENT_ID = "stg-" + "e" * 24
CONFIG_GENERATION = f"staging-{DEPLOYMENT_ID}-{INFRA_COMMIT[:12]}"
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def manifest(digest: str = DIGEST, source_commit: str = SOURCE_COMMIT, build_id: str = BUILD_ID) -> str:
    return "\n".join(
        (
            "vps_service_foundation_nutsnews_app_enabled: true",
            "vps_service_foundation_nutsnews_app_staged_route_enabled: true",
            "vps_service_foundation_nutsnews_app_public_route_enabled: true",
            "vps_service_foundation_nutsnews_app_image_repo: ghcr.io/ramideltoro/nutsnews",
            f'vps_service_foundation_nutsnews_app_image_digest: "{digest}"',
            f'vps_service_foundation_nutsnews_app_source_commit: "{source_commit}"',
            f'vps_service_foundation_nutsnews_app_build_id: "{build_id}"',
            f'vps_service_foundation_nutsnews_app_config_generation: "production-{build_id}-20260713000000"',
            'vps_service_foundation_nutsnews_app_migration_head: "20260713000000"',
            'vps_service_foundation_nutsnews_app_schema_version: "20260712170000"',
            'vps_service_foundation_nutsnews_app_supabase_project_ref: "mpqfulvvagyzqneiaqky"',
            "vps_service_foundation_nutsnews_app_deployment_target: production-vps",
            'vps_service_foundation_nutsnews_app_last_known_good_digest: ""',
            "vps_service_foundation_nutsnews_app_secret_env_keys: []",
            "vps_service_foundation_nutsnews_app_required_secrets: []",
            "",
        )
    )


def record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "nutsnews.staging_qualification.v1",
        "predicate_type": "https://nutsnews.com/attestations/staging-qualification/v1",
        "result": "pass",
        "image": {"repository": "ghcr.io/ramideltoro/nutsnews", "digest": DIGEST},
        "source": {
            "repository": "ramideltoro/nutsnews",
            "commit": SOURCE_COMMIT,
            "build_id": BUILD_ID,
            "workflow_run_id": SOURCE_RUN_ID,
            "workflow_run_url": f"https://github.com/ramideltoro/nutsnews/actions/runs/{SOURCE_RUN_ID}",
        },
        "infra": {
            "repository": "ramideltoro/nutsnews-infra",
            "commit": INFRA_COMMIT,
            "config_generation": CONFIG_GENERATION,
        },
        "staging": {
            "deployment_id": DEPLOYMENT_ID,
            "github_deployment_id": 42,
            "target_hostname": "staging.nutsnews.com",
            "deploy_workflow_run_id": "123456789",
            "deploy_workflow_run_url": "https://github.com/ramideltoro/nutsnews-infra/actions/runs/123456789",
            "github_deployment_url": "https://api.github.com/repos/ramideltoro/nutsnews-infra/deployments/42",
            "github_deployment_status_url": "https://api.github.com/repos/ramideltoro/nutsnews-infra/deployments/42/statuses/1",
            "deployed_at": "2026-07-15T11:50:00Z",
            "pre_test_identity": {
                "checked_at": "2026-07-15T11:55:00Z",
                "target_hostname": "staging.nutsnews.com",
                "health_status": 200,
                "ready_status": 200,
                "source_commit": SOURCE_COMMIT,
                "build_id": BUILD_ID,
                "image_digest": DIGEST,
                "runtime_environment": "staging",
                "deployment_target": "vps-staging",
                "config_generation": CONFIG_GENERATION,
                "ready_code": "ready",
            },
            "post_test_identity": {
                "checked_at": "2026-07-15T11:58:00Z",
                "target_hostname": "staging.nutsnews.com",
                "health_status": 200,
                "ready_status": 200,
                "source_commit": SOURCE_COMMIT,
                "build_id": BUILD_ID,
                "image_digest": DIGEST,
                "runtime_environment": "staging",
                "deployment_target": "vps-staging",
                "config_generation": CONFIG_GENERATION,
                "ready_code": "ready",
            },
        },
        "test_suite": {"repository": "ramideltoro/nutsnews", "commit": SOURCE_COMMIT},
        "qualifier": {
            "repository": "ramideltoro/nutsnews-infra",
            "workflow": ".github/workflows/nutsnews-staging-qualification.yml",
            "commit": INFRA_COMMIT,
            "ref": "refs/heads/main",
            "workflow_ref": "ramideltoro/nutsnews-infra/.github/workflows/nutsnews-staging-qualification.yml@refs/heads/main",
            "run_id": "111222333",
            "run_attempt": "1",
            "run_url": "https://github.com/ramideltoro/nutsnews-infra/actions/runs/111222333",
        },
        "evidence_urls": {
            "source_workflow_run": f"https://github.com/ramideltoro/nutsnews/actions/runs/{SOURCE_RUN_ID}",
            "staging_deploy_workflow_run": "https://github.com/ramideltoro/nutsnews-infra/actions/runs/123456789",
            "qualifier_workflow_run": "https://github.com/ramideltoro/nutsnews-infra/actions/runs/111222333",
        },
        "timing": {
            "started_at": "2026-07-15T11:55:00Z",
            "completed_at": "2026-07-15T11:58:00Z",
            "expires_at": "2026-07-16T11:58:00Z",
            "ttl_hours": 24,
        },
        "invalidated_by": ["staging redeploy", "infra config revision", "required test-suite revision", "qualification expiration"],
        "required_suites": [
            {"name": name, "required": True, "result": "pass", "duration_seconds": 0.1}
            for name in (
                "cloudflare-access-and-runtime-identity",
                "github-staging-deployment-identity",
                "existing-deployment-smoke",
                "isolated-staging-synthetic-write",
                "bounded-http-auth-contact-security",
                "bounded-chromium-accessibility",
                "unconditional-fixture-cleanup",
            )
        ],
    }
    value.update(overrides)
    return value


def verified(record_value: dict[str, object] | None = None, *, cert: dict[str, str] | None = None) -> list[dict[str, object]]:
    predicate = record_value or record()
    certificate = {
        "sourceRepositoryURI": "https://github.com/ramideltoro/nutsnews-infra",
        "sourceRepositoryRef": "refs/heads/main",
        "buildSignerURI": "https://github.com/ramideltoro/nutsnews-infra/.github/workflows/nutsnews-staging-qualification.yml@refs/heads/main",
    }
    certificate.update(cert or {})
    return [
        {
            "verificationResult": {
                "signature": {"certificate": certificate},
                "statement": {
                    "subject": [
                        {
                            "name": "ghcr.io/ramideltoro/nutsnews",
                            "digest": {"sha256": DIGEST.removeprefix("sha256:")},
                        }
                    ],
                    "predicate": predicate,
                },
            }
        }
    ]


def deployments(*, digest: str = DIGEST, deployment_id: str = DEPLOYMENT_ID, newer: bool = False) -> list[dict[str, object]]:
    deployment = {
        "id": 42,
        "environment": "staging",
        "created_at": "2026-07-15T11:50:00Z",
        "payload": {
            "deployment_id": deployment_id,
            "target_hostname": "staging.nutsnews.com",
            "image_repository": "ghcr.io/ramideltoro/nutsnews",
            "requested_digest": digest,
            "source_repository": "ramideltoro/nutsnews",
            "source_commit": SOURCE_COMMIT,
            "build_id": BUILD_ID,
            "source_workflow_run_id": SOURCE_RUN_ID,
            "infra_commit": INFRA_COMMIT,
            "config_generation": CONFIG_GENERATION,
            "github_run_id": "123456789",
        },
        "statuses": [{"state": "success", "created_at": "2026-07-15T11:51:00Z"}],
    }
    if not newer:
        return [deployment]
    return [
        {
            **deployment,
            "id": 43,
            "created_at": "2026-07-15T12:01:00Z",
            "payload": {**deployment["payload"], "deployment_id": "stg-" + "f" * 24},
            "statuses": [{"state": "success", "created_at": "2026-07-15T12:01:00Z"}],
        },
        deployment,
    ]


def expect_reject(label: str, operation) -> None:
    try:
        operation()
    except (module.EligibilityError, module.staging_qualification.QualificationError):
        return
    raise AssertionError(f"{label} must be rejected.")


expected = {
    "source_commit": SOURCE_COMMIT,
    "image_digest": DIGEST,
    "build_id": BUILD_ID,
    "source_workflow_run_id": SOURCE_RUN_ID,
}

selected = module.select_record(verified(), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW)
assert selected["staging"]["deployment_id"] == DEPLOYMENT_ID

expect_reject("missing attestation", lambda: module.select_record([], expected, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong digest", lambda: module.select_record(verified(), {**expected, "image_digest": OTHER_DIGEST}, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong source", lambda: module.select_record(verified(), {**expected, "source_commit": "1" * 40}, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong build", lambda: module.select_record(verified(), {**expected, "build_id": "1-2"}, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong source workflow", lambda: module.select_record(verified(), {**expected, "source_workflow_run_id": "1"}, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong issuer", lambda: module.select_record(verified(cert={"sourceRepositoryURI": "https://github.com/ramideltoro/nutsnews"}), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("wrong ref", lambda: module.select_record(verified(cert={"sourceRepositoryRef": "refs/heads/feature"}), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("expired", lambda: module.select_record(verified(), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW + timedelta(hours=25)))
expect_reject("tampered json", lambda: module.select_record(verified({**record(), "result": "fail"}), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("skipped suite", lambda: module.select_record(verified({**record(), "required_suites": [{"name": "suite", "required": True, "result": "skip"}]}), expected, deployments(), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("stale staging", lambda: module.select_record(verified(), expected, deployments(digest=OTHER_DIGEST), lambda deployment: deployment["statuses"], now=NOW))
expect_reject("superseded", lambda: module.select_record(verified(), expected, deployments(newer=True), lambda deployment: deployment["statuses"], now=NOW))

with tempfile.TemporaryDirectory() as temporary:
    previous = Path(temporary) / "previous.yml"
    current = Path(temporary) / "current.yml"
    previous.write_text(manifest(), encoding="utf-8")
    current.write_text(manifest(), encoding="utf-8")
    assert module.manifest_release_changed(previous, current) is False
    current.write_text(manifest(digest=OTHER_DIGEST), encoding="utf-8")
    assert module.manifest_release_changed(previous, current) is True

protected_workflow = PROTECTED_WORKFLOW.read_text(encoding="utf-8")
promotion_workflow = PROMOTION_WORKFLOW.read_text(encoding="utf-8")

for required in (
    "verify-production-eligibility:",
    "environment: production-vps",
    "needs: verify-production-eligibility",
    "release_source_workflow_run_id:",
    "gh attestation verify",
    "verify_production_eligibility.py verify",
    "verify_production_eligibility.py check-no-release",
    "attestations: read",
    "deployments: read",
):
    assert required in protected_workflow, f"Protected apply missing eligibility guardrail: {required}"

assert protected_workflow.index("verify-production-eligibility:") < protected_workflow.index("environment: production-vps")
assert "production-vps" not in protected_workflow.split("verify-production-eligibility:", 1)[1].split("baseline:", 1)[0]
assert "NUTSNEWS_VPS_SSH_PRIVATE_KEY" not in protected_workflow.split("verify-production-eligibility:", 1)[1].split("baseline:", 1)[0]
assert "NUTSNEWS_INFRA_RELEASE_TOKEN" not in protected_workflow

assert "nutsnews-production-release is paused" in promotion_workflow
assert "environment: production-vps" not in promotion_workflow

print("Production eligibility gate guardrails passed.")
