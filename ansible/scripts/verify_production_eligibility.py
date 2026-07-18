#!/usr/bin/env python3
"""Verify that a production app release is backed by a fresh staging pass."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_nutsnews_release
import rollback_nutsnews_release


STAGING_QUALIFICATION = ROOT / "scripts/staging_qualification.py"
INFRA_REPOSITORY = "ramideltoro/nutsnews-infra"
INFRA_REPOSITORY_URI = f"https://github.com/{INFRA_REPOSITORY}"
TRUSTED_REF = "refs/heads/main"
QUALIFIER_WORKFLOW = ".github/workflows/nutsnews-staging-qualification.yml"
QUALIFIER_WORKFLOW_URI = f"{INFRA_REPOSITORY_URI}/{QUALIFIER_WORKFLOW}@{TRUSTED_REF}"
STAGING_ENVIRONMENT = "staging"


spec = importlib.util.spec_from_file_location("staging_qualification", STAGING_QUALIFICATION)
assert spec and spec.loader
staging_qualification = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = staging_qualification
spec.loader.exec_module(staging_qualification)


class EligibilityError(ValueError):
    """Raised when a release is not eligible for production."""


def parse_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EligibilityError(f"{path} is missing or invalid JSON.") from error


def parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EligibilityError(f"{label} timestamp is invalid.") from error
    return parsed.astimezone(timezone.utc)


def complete_release_inputs(values: dict[str, str]) -> bool:
    supplied = [key for key, value in values.items() if value]
    if not supplied:
        return False
    if len(supplied) != len(values):
        raise EligibilityError("Production app release verification requires the complete release identity bundle.")
    return True


def manifest_release_changed(previous_manifest: Path | None, current_manifest: Path) -> bool:
    if previous_manifest is None or not previous_manifest.exists():
        return False
    previous = promote_nutsnews_release.validate_manifest(
        promote_nutsnews_release.manifest_values(previous_manifest.read_text(encoding="utf-8"))
    )
    current = promote_nutsnews_release.validate_manifest(
        promote_nutsnews_release.manifest_values(current_manifest.read_text(encoding="utf-8"))
    )
    release_keys = {
        "image_digest",
        "source_commit",
        "build_id",
        "migration_head",
        "schema_version",
        "supabase_project_ref",
    }
    return any(previous[key] != current[key] for key in release_keys)


def statement_certificate(item: dict[str, Any]) -> dict[str, Any]:
    signature = item.get("verificationResult", {}).get("signature", {})
    certificate = signature.get("certificate")
    return certificate if isinstance(certificate, dict) else {}


def certificate_is_trusted(item: dict[str, Any]) -> bool:
    certificate = statement_certificate(item)
    return (
        certificate.get("sourceRepositoryURI") == INFRA_REPOSITORY_URI
        and certificate.get("sourceRepositoryRef") == TRUSTED_REF
        and certificate.get("buildSignerURI") == QUALIFIER_WORKFLOW_URI
    )


def predicate_for(item: dict[str, Any]) -> dict[str, Any]:
    statement = item.get("verificationResult", {}).get("statement", {})
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise EligibilityError("Verified attestation is missing a predicate.")
    return predicate


def required_string(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise EligibilityError(f"Qualification predicate is missing {key}.")
    return value


def matches_release(record: dict[str, Any], expected: dict[str, str]) -> None:
    image = record.get("image")
    source = record.get("source")
    test_suite = record.get("test_suite")
    if not isinstance(image, dict) or not isinstance(source, dict) or not isinstance(test_suite, dict):
        raise EligibilityError("Qualification predicate is missing image, source, or test-suite identity.")
    if image.get("repository") != promote_nutsnews_release.IMAGE_REPOSITORY:
        raise EligibilityError("Qualification image repository is not the approved GHCR repository.")
    if image.get("digest") != expected["image_digest"]:
        raise EligibilityError("Qualification digest does not match the release digest.")
    if source.get("repository") != "ramideltoro/nutsnews":
        raise EligibilityError("Qualification source repository is not trusted.")
    if source.get("commit") != expected["source_commit"]:
        raise EligibilityError("Qualification source commit does not match the release commit.")
    if source.get("build_id") != expected["build_id"]:
        raise EligibilityError("Qualification build ID does not match the release build.")
    if source.get("workflow_run_id") != expected["source_workflow_run_id"]:
        raise EligibilityError("Qualification source workflow run does not match the release build run.")
    if source.get("migration_head") != expected["migration_head"]:
        raise EligibilityError("Qualification migration head does not match the release.")
    if source.get("schema_version") != expected["schema_version"]:
        raise EligibilityError("Qualification schema version does not match the release.")
    if source.get("supabase_project_ref") != expected["supabase_project_ref"]:
        raise EligibilityError("Qualification Supabase project reference does not match the release.")
    if test_suite.get("repository") != "ramideltoro/nutsnews" or test_suite.get("commit") != expected["source_commit"]:
        raise EligibilityError("Qualification test-suite revision does not match the release source.")


def successful_staging_deployments(deployments: list[dict[str, Any]], fetch_statuses) -> list[dict[str, Any]]:
    successful: list[dict[str, Any]] = []
    for deployment in deployments:
        if deployment.get("environment") != STAGING_ENVIRONMENT:
            continue
        payload = deployment.get("payload")
        if not isinstance(payload, dict):
            continue
        statuses = fetch_statuses(deployment)
        status = statuses[0] if statuses else {}
        if not isinstance(status, dict) or status.get("state") != "success":
            continue
        successful.append({"deployment": deployment, "payload": payload, "status": status})
    successful.sort(
        key=lambda item: str(item["status"].get("created_at") or item["deployment"].get("created_at") or ""),
        reverse=True,
    )
    return successful


def validate_current_staging(record: dict[str, Any], deployments: list[dict[str, Any]], fetch_statuses) -> None:
    staging = record.get("staging")
    image = record.get("image")
    infra = record.get("infra")
    source = record.get("source")
    if not all(isinstance(section, dict) for section in (staging, image, infra, source)):
        raise EligibilityError("Qualification predicate is missing staging identity.")
    deployment_id = required_string(staging, "deployment_id")
    current = successful_staging_deployments(deployments, fetch_statuses)
    if not current:
        raise EligibilityError("No successful staging deployment is current.")
    latest = current[0]
    payload = latest["payload"]
    if payload.get("deployment_id") != deployment_id:
        raise EligibilityError("Qualification has been superseded by a newer staging deployment.")
    checks = {
        "requested_digest": image.get("digest"),
        "source_commit": source.get("commit"),
        "build_id": source.get("build_id"),
        "infra_commit": infra.get("commit"),
        "config_generation": infra.get("config_generation"),
        "migration_head": source.get("migration_head"),
        "schema_version": source.get("schema_version"),
        "supabase_project_ref": source.get("supabase_project_ref"),
    }
    for key, expected in checks.items():
        if payload.get(key) != expected:
            raise EligibilityError(f"Current staging deployment {key} does not match the qualification.")


def select_record(
    verified_attestations: list[dict[str, Any]],
    expected: dict[str, str],
    deployments: list[dict[str, Any]],
    fetch_statuses,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    for item in verified_attestations:
        if not isinstance(item, dict):
            continue
        try:
            if not certificate_is_trusted(item):
                raise EligibilityError("Qualification certificate issuer/ref/workflow is not trusted.")
            record = predicate_for(item)
            staging_qualification.validate_record(
                record,
                now=now,
                expected_image_digest=expected["image_digest"],
                verified_attestation=item,
            )
            matches_release(record, expected)
            validate_current_staging(record, deployments, fetch_statuses)
            return record
        except (EligibilityError, staging_qualification.QualificationError) as error:
            errors.append(str(error))
    raise EligibilityError("No trusted, fresh, current staging qualification matched this release: " + "; ".join(errors[:4]))


def github_json(path: str, token: str) -> Any:
    request = Request(
        f"https://api.github.com/repos/{INFRA_REPOSITORY}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub API origin
        return json.load(response)


def command_check_no_release(arguments: argparse.Namespace) -> None:
    changed = manifest_release_changed(arguments.previous_manifest, arguments.current_manifest)
    if changed:
        raise EligibilityError("Production app release manifest changed without a verified staging qualification gate.")
    if arguments.github_output:
        arguments.github_output.write_text("app_release=false\n", encoding="utf-8")
    print("No production app release gate is required for this baseline apply.")


def command_verify(arguments: argparse.Namespace) -> None:
    expected = {
        "source_commit": arguments.source_commit,
        "image_digest": arguments.image_digest,
        "build_id": arguments.build_id,
        "source_workflow_run_id": arguments.source_workflow_run_id,
        "migration_head": arguments.migration_head,
        "schema_version": arguments.schema_version,
        "supabase_project_ref": arguments.supabase_project_ref,
    }
    promote_nutsnews_release.verify_manifest(
        arguments.manifest,
        promote_nutsnews_release.IMAGE_REPOSITORY,
        arguments.image_digest,
        arguments.source_commit,
        arguments.build_id,
        arguments.migration_head,
        arguments.schema_version,
        arguments.supabase_project_ref,
    )
    verified = parse_json_file(arguments.verified_attestation)
    if not isinstance(verified, list) or not verified:
        raise EligibilityError("Verified attestation JSON must be a non-empty list.")
    if arguments.deployments_json:
        deployments = parse_json_file(arguments.deployments_json)
        if not isinstance(deployments, list):
            raise EligibilityError("Deployments JSON must be a list.")

        def fetch_statuses(deployment: dict[str, Any]) -> list[dict[str, Any]]:
            statuses = deployment.get("statuses", [])
            return statuses if isinstance(statuses, list) else []

    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            raise EligibilityError("GH_TOKEN or GITHUB_TOKEN is required to verify current staging deployment state.")
        deployments = github_json("/deployments?environment=staging&per_page=100", token)
        if not isinstance(deployments, list):
            raise EligibilityError("GitHub deployments response must be a list.")

        def fetch_statuses(deployment: dict[str, Any]) -> list[dict[str, Any]]:
            deployment_id = deployment.get("id")
            statuses = github_json(f"/deployments/{deployment_id}/statuses", token)
            return statuses if isinstance(statuses, list) else []

    record = select_record(verified, expected, deployments, fetch_statuses)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if arguments.github_output:
        arguments.github_output.write_text(
            "\n".join(
                (
                    "app_release=true",
                    f"staging_deployment_id={record['staging']['deployment_id']}",
                    f"qualification_run_id={record['qualifier']['run_id']}",
                    f"expires_at={record['timing']['expires_at']}",
                    "",
                )
            ),
            encoding="utf-8",
        )
    print(f"Production release is eligible via staging qualification {record['qualifier']['run_id']}.")


def command_verify_rollback(arguments: argparse.Namespace) -> None:
    expected = {
        "source_commit": arguments.source_commit,
        "image_digest": arguments.image_digest,
        "build_id": arguments.build_id,
        "source_workflow_run_id": arguments.source_workflow_run_id,
    }
    if arguments.confirmation != "rollback-recorded-last-known-good":
        raise EligibilityError("Rollback eligibility requires the fixed rollback confirmation phrase.")
    if arguments.reason.strip() != arguments.reason or len(arguments.reason.strip()) < 10:
        raise EligibilityError("Rollback eligibility requires a sanitized operator reason.")
    if expected["source_workflow_run_id"] != expected["build_id"].split("-", 1)[0]:
        raise EligibilityError("Rollback source workflow run must match the restored build ID.")
    restored = promote_nutsnews_release.verify_manifest(
        arguments.manifest,
        promote_nutsnews_release.IMAGE_REPOSITORY,
        arguments.image_digest,
        arguments.source_commit,
        arguments.build_id,
        arguments.migration_head,
        arguments.schema_version,
        arguments.supabase_project_ref,
    )
    if not arguments.previous_manifest or not arguments.previous_manifest.exists():
        raise EligibilityError("Rollback verification requires the previous reviewed production manifest.")
    previous = promote_nutsnews_release.validate_manifest(
        promote_nutsnews_release.manifest_values(arguments.previous_manifest.read_text(encoding="utf-8"))
    )
    if previous["image_digest"] != arguments.failed_image_digest:
        raise EligibilityError("Rollback failed digest does not match the previous production manifest.")
    if previous["last_known_good_digest"] != restored["image_digest"]:
        raise EligibilityError("Rollback target is not the previous manifest's recorded last-known-good digest.")
    try:
        history_commit, selected = rollback_nutsnews_release.find_recorded_release(
            arguments.manifest,
            restored["image_digest"],
            cwd=arguments.repo,
        )
    except rollback_nutsnews_release.RollbackError:
        selected_pair = rollback_nutsnews_release.resolve_previous_release(
            arguments.previous_manifest,
            restored["image_digest"],
            restored=restored,
        )
        if selected_pair is None:
            raise
        history_commit, selected = selected_pair
    for key in ("image_digest", "source_commit", "build_id", "migration_head", "schema_version", "supabase_project_ref"):
        if selected[key] != restored[key]:
            raise EligibilityError(f"Rollback restored {key} does not match recorded history.")
    evidence = {
        "schema_version": "nutsnews.production_rollback.v1",
        "result": "verified",
        "reason": arguments.reason,
        "failed": previous,
        "restored": restored,
        "restored_manifest_commit": history_commit,
    }
    if arguments.github_output:
        arguments.github_output.write_text(
            "\n".join(
                (
                    "app_release=true",
                    "rollback=true",
                    f"failed_image_digest={arguments.failed_image_digest}",
                    f"restored_image_digest={restored['image_digest']}",
                    "",
                )
            ),
            encoding="utf-8",
        )
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Production rollback is eligible for recorded digest {restored['image_digest']}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    no_release = subparsers.add_parser("check-no-release")
    no_release.add_argument("--current-manifest", type=Path, required=True)
    no_release.add_argument("--previous-manifest", type=Path)
    no_release.add_argument("--github-output", type=Path)
    no_release.set_defaults(func=command_check_no_release)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--verified-attestation", type=Path, required=True)
    verify.add_argument("--deployments-json", type=Path)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--image-digest", required=True)
    verify.add_argument("--build-id", required=True)
    verify.add_argument("--source-workflow-run-id", required=True)
    verify.add_argument("--migration-head", required=True)
    verify.add_argument("--schema-version", required=True)
    verify.add_argument("--supabase-project-ref", required=True)
    verify.add_argument("--output", type=Path)
    verify.add_argument("--github-output", type=Path)
    verify.set_defaults(func=command_verify)

    rollback = subparsers.add_parser("verify-rollback")
    rollback.add_argument("--repo", type=Path, default=Path.cwd())
    rollback.add_argument("--manifest", type=Path, required=True)
    rollback.add_argument("--previous-manifest", type=Path, required=True)
    rollback.add_argument("--source-commit", required=True)
    rollback.add_argument("--image-digest", required=True)
    rollback.add_argument("--build-id", required=True)
    rollback.add_argument("--source-workflow-run-id", required=True)
    rollback.add_argument("--migration-head", required=True)
    rollback.add_argument("--schema-version", required=True)
    rollback.add_argument("--supabase-project-ref", required=True)
    rollback.add_argument("--failed-image-digest", required=True)
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--confirmation", required=True)
    rollback.add_argument("--output", type=Path)
    rollback.add_argument("--github-output", type=Path)
    rollback.set_defaults(func=command_verify_rollback)
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        arguments.func(arguments)
    except (EligibilityError, promote_nutsnews_release.PromotionError) as error:
        raise SystemExit(f"Production eligibility rejected: {error}") from error


if __name__ == "__main__":
    main()
