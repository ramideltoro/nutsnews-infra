#!/usr/bin/env python3
"""Regression coverage for fixed-purpose NutsNews production rollback."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ROLLBACK_SCRIPT = ROOT / "scripts/rollback_nutsnews_release.py"
ELIGIBILITY_SCRIPT = ROOT / "scripts/verify_production_eligibility.py"
ROLLBACK_WORKFLOW = REPO / ".github/workflows/protected-nutsnews-rollback.yml"
PROTECTED_WORKFLOW = REPO / ".github/workflows/protected-ansible-apply.yml"
WORKFLOW_SAFETY = REPO / ".github/workflows/workflow-safety.yml"

sys.path.insert(0, str(ROOT / "scripts"))


def import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rollback = import_module("rollback_nutsnews_release", ROLLBACK_SCRIPT)
eligibility = import_module("verify_production_eligibility", ELIGIBILITY_SCRIPT)


def manifest(
    digest: str,
    source_commit: str,
    build_id: str,
    migration_head: str = "20260713000000",
    schema_version: str = "20260712170000",
    supabase_project_ref: str = "mpqfulvvagyzqneiaqky",
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


def git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=repo, text=True)


def expect_reject(label: str, operation) -> None:
    try:
        operation()
    except (rollback.RollbackError, eligibility.EligibilityError):
        return
    raise AssertionError(f"{label} must be rejected.")


old_digest = "sha256:" + "a" * 64
new_digest = "sha256:" + "b" * 64
other_digest = "sha256:" + "c" * 64
old_commit = "1" * 40
new_commit = "2" * 40
reason = "critical production health failure during controlled test"

with tempfile.TemporaryDirectory() as temporary:
    repo = Path(temporary)
    git(repo, "init")
    git(repo, "config", "user.name", "rollback-test")
    git(repo, "config", "user.email", "rollback-test@example.invalid")
    manifest_path = repo / "ansible/inventories/production/host_vars/vps.nutsnews.com.yml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(manifest(old_digest, old_commit, "101-1"), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "deploy old")
    old_manifest = manifest_path.read_text(encoding="utf-8")

    manifest_path.write_text(manifest(new_digest, new_commit, "202-1", last_known_good=old_digest), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "deploy new")
    previous_manifest = repo / "previous.yml"
    previous_manifest.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    evidence = rollback.select_rollback(manifest_path, new_digest, reason, cwd=repo)
    assert evidence["failed"]["image_digest"] == new_digest
    assert evidence["restored"]["image_digest"] == old_digest
    assert evidence["restored"]["source_commit"] == old_commit

    expect_reject("wrong failed digest", lambda: rollback.select_rollback(manifest_path, other_digest, reason, cwd=repo))
    expect_reject("short reason", lambda: rollback.select_rollback(manifest_path, new_digest, "bad", cwd=repo))

    updated = rollback.replace_manifest_release(manifest_path.read_text(encoding="utf-8"), evidence["restored"], old_digest)
    manifest_path.write_text(updated, encoding="utf-8")
    restored = rollback.read_manifest(manifest_path)
    assert restored["image_digest"] == old_digest
    assert restored["last_known_good_digest"] == old_digest

    args = type(
        "Args",
        (),
        {
            "repo": repo,
            "manifest": manifest_path,
            "previous_manifest": previous_manifest,
            "source_commit": old_commit,
            "image_digest": old_digest,
            "build_id": "101-1",
            "source_workflow_run_id": "101",
            "migration_head": "20260713000000",
            "schema_version": "20260712170000",
            "supabase_project_ref": "mpqfulvvagyzqneiaqky",
            "failed_image_digest": new_digest,
            "reason": reason,
            "confirmation": "rollback-recorded-last-known-good",
            "github_output": None,
            "output": None,
        },
    )()
    eligibility.command_verify_rollback(args)
    args.confirmation = "apply-anything"
    expect_reject("wrong rollback confirmation", lambda: eligibility.command_verify_rollback(args))

    missing_history_repo = repo / "missing-history"
    missing_history_repo.mkdir()
    git(missing_history_repo, "init")
    git(missing_history_repo, "config", "user.name", "rollback-test")
    git(missing_history_repo, "config", "user.email", "rollback-test@example.invalid")
    missing_manifest = missing_history_repo / "ansible/inventories/production/host_vars/vps.nutsnews.com.yml"
    missing_manifest.parent.mkdir(parents=True)
    missing_manifest.write_text(manifest(new_digest, new_commit, "202-1", last_known_good=old_digest), encoding="utf-8")
    git(missing_history_repo, "add", ".")
    git(missing_history_repo, "commit", "-m", "deploy missing")
    expect_reject(
        "last-known-good absent from history",
        lambda: rollback.select_rollback(missing_manifest, new_digest, reason, cwd=missing_history_repo),
    )

    assert old_manifest

rollback_workflow = ROLLBACK_WORKFLOW.read_text(encoding="utf-8")
protected_workflow = PROTECTED_WORKFLOW.read_text(encoding="utf-8")
workflow_safety = WORKFLOW_SAFETY.read_text(encoding="utf-8")

for required in (
    "rollback-recorded-last-known-good",
    "rollback_nutsnews_release.py",
    "gh pr create",
    "gh pr checks",
    "gh pr merge",
    "gh workflow run protected-ansible-apply.yml",
    "--field rollback_failed_image_digest",
    "--field rollback_reason",
    "--field rollback_confirmation=rollback-recorded-last-known-good",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "environment: production-vps",
):
    assert required in rollback_workflow, f"Rollback workflow missing guardrail: {required}"

assert "ssh " not in rollback_workflow
assert "docker " not in rollback_workflow
assert "command" not in rollback_workflow.lower()
assert "verify-rollback" in protected_workflow
assert "rollback_failed_image_digest:" in protected_workflow
assert "rollback_reason:" in protected_workflow
assert "rollback_confirmation:" in protected_workflow
assert "validate_production_rollback.py" in workflow_safety

print("Fixed production rollback guardrails passed.")
