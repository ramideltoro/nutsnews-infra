#!/usr/bin/env python3
"""Regression coverage for automatic, reviewed NutsNews VPS releases."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT_PATH = ROOT / "scripts/promote_nutsnews_release.py"
PROMOTION_WORKFLOW = REPO / ".github/workflows/nutsnews-release-promotion.yml"
PROTECTED_WORKFLOW = REPO / ".github/workflows/protected-ansible-apply.yml"


spec = importlib.util.spec_from_file_location("promote_nutsnews_release", SCRIPT_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest(digest: str, source_commit: str, build_id: str, last_known_good: str = "") -> str:
    return "\n".join(
        (
            "vps_service_foundation_nutsnews_app_enabled: true",
            "vps_service_foundation_nutsnews_app_staged_route_enabled: true",
            "vps_service_foundation_nutsnews_app_public_route_enabled: true",
            "vps_service_foundation_nutsnews_app_image_repo: ghcr.io/ramideltoro/nutsnews",
            f'vps_service_foundation_nutsnews_app_image_digest: "{digest}"',
            f'vps_service_foundation_nutsnews_app_source_commit: "{source_commit}"',
            f'vps_service_foundation_nutsnews_app_build_id: "{build_id}"',
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

with tempfile.TemporaryDirectory() as temporary_directory:
    path = Path(temporary_directory) / "vps.nutsnews.com.yml"
    path.write_text(manifest(old_digest, old_commit, "101-1"), encoding="utf-8")

    result = module.promote_manifest(
        path,
        "ghcr.io/ramideltoro/nutsnews",
        new_digest,
        new_commit,
        "202-3",
        write=True,
    )
    values = module.manifest_values(path.read_text(encoding="utf-8"))
    assert result["changed"] == "true"
    assert result["previous_digest"] == old_digest
    assert values["vps_service_foundation_nutsnews_app_image_digest"] == new_digest
    assert values["vps_service_foundation_nutsnews_app_source_commit"] == new_commit
    assert values["vps_service_foundation_nutsnews_app_build_id"] == "202-3"
    assert values["vps_service_foundation_nutsnews_app_last_known_good_digest"] == old_digest

    module.verify_manifest(
        path,
        "ghcr.io/ramideltoro/nutsnews",
        new_digest,
        new_commit,
        "202-3",
    )

    original = path.read_text(encoding="utf-8")
    for invalid_digest, invalid_commit, invalid_build_id in (
        ("latest", new_commit, "202-3"),
        (new_digest, "not-a-commit", "202-3"),
        (new_digest, new_commit, "build-202"),
    ):
        try:
            module.promote_manifest(
                path,
                "ghcr.io/ramideltoro/nutsnews",
                invalid_digest,
                invalid_commit,
                invalid_build_id,
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
    "repository_dispatch:",
    "nutsnews-production-release",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "git fetch origin main --prune",
    "current-vps-release.yml",
    'git switch -c "$release_branch" origin/main',
    'dispatch_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
    "--json databaseId,displayTitle,status,createdAt",
    "Wait for promotion checks to pass and merge",
    "gh pr checks \"$PR_URL\" --json name,bucket",
    "Promotion checks did not pass before the timeout.",
    "gh pr merge \"$PR_URL\" --merge",
    "gh workflow run protected-ansible-apply.yml",
    "gh run watch \"$run_id\"",
    "--exit-status",
    "ansible/scripts/promote_nutsnews_release.py",
):
    assert required in promotion_workflow, f"Promotion workflow is missing required guardrail: {required}"

assert (
    promotion_workflow.index("current-vps-release.yml")
    < promotion_workflow.index("gh pr list")
), "A rerun must verify current main before it reuses or creates a release pull request."
assert "run.get(\"createdAt\", \"\") >= sys.argv[3]" in promotion_workflow, (
    "The release workflow must select only a protected apply started by its own dispatch."
)

for required in (
    "release_source_commit:",
    "release_image_digest:",
    "release_build_id:",
    "Validate requested automated release identity",
    "Verify released Docker image over SSH",
    "Verify released public health identity",
):
    assert required in protected_workflow, f"Protected apply is missing required release verification: {required}"

assert "NUTSNEWS_APP_IMAGE_TAG" not in promotion_workflow
assert ":latest" not in promotion_workflow.lower()
assert "gh pr checks \"$PR_URL\" --required" not in promotion_workflow

print("Automatic NutsNews VPS release promotion guardrails passed.")
