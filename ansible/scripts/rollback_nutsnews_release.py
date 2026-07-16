#!/usr/bin/env python3
"""Select and write a fixed-purpose NutsNews production rollback manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import promote_nutsnews_release


REASON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_/@#()+='\"-]{9,500}$")


class RollbackError(ValueError):
    """Raised when a rollback request is not fixed to recorded release state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_manifest(path: Path) -> dict[str, str]:
    return promote_nutsnews_release.validate_manifest(
        promote_nutsnews_release.manifest_values(path.read_text(encoding="utf-8"))
    )


def git_output(arguments: list[str], *, cwd: Path) -> str:
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True, stderr=subprocess.DEVNULL)


def repo_relative(path: Path, *, cwd: Path) -> Path:
    path = path.resolve()
    try:
        return path.relative_to(cwd.resolve())
    except ValueError as error:
        raise RollbackError("Manifest path must be inside the repository.") from error


def manifest_at(commit: str, manifest_path: Path, *, cwd: Path) -> dict[str, str] | None:
    try:
        text = git_output(["show", f"{commit}:{repo_relative(manifest_path, cwd=cwd).as_posix()}"], cwd=cwd)
    except subprocess.CalledProcessError:
        return None
    try:
        return promote_nutsnews_release.validate_manifest(promote_nutsnews_release.manifest_values(text))
    except promote_nutsnews_release.PromotionError:
        return None


def find_recorded_release(manifest_path: Path, digest: str, *, cwd: Path) -> tuple[str, dict[str, str]]:
    relative_manifest = repo_relative(manifest_path, cwd=cwd)
    commits = git_output(["log", "--format=%H", "--", relative_manifest.as_posix()], cwd=cwd).splitlines()
    for commit in commits[1:]:
        release = manifest_at(commit, manifest_path, cwd=cwd)
        if release and release["image_digest"] == digest:
            return commit, release
    raise RollbackError("The recorded last-known-good digest was not found in manifest history.")


def replace_manifest_release(text: str, release: dict[str, str], last_known_good_digest: str) -> str:
    updated = text
    for name, value in (
        ("vps_service_foundation_nutsnews_app_image_repo", release["image_repository"]),
        ("vps_service_foundation_nutsnews_app_image_digest", release["image_digest"]),
        ("vps_service_foundation_nutsnews_app_source_commit", release["source_commit"]),
        ("vps_service_foundation_nutsnews_app_build_id", release["build_id"]),
        ("vps_service_foundation_nutsnews_app_config_generation", release["config_generation"]),
        ("vps_service_foundation_nutsnews_app_migration_head", release["migration_head"]),
        ("vps_service_foundation_nutsnews_app_schema_version", release["schema_version"]),
        ("vps_service_foundation_nutsnews_app_supabase_project_ref", release["supabase_project_ref"]),
        ("vps_service_foundation_nutsnews_app_deployment_target", "production-vps"),
        ("vps_service_foundation_nutsnews_app_last_known_good_digest", last_known_good_digest),
    ):
        updated = promote_nutsnews_release.replace_value(updated, name, value)
    return updated


def validate_reason(reason: str) -> str:
    value = reason.strip()
    if not REASON_RE.fullmatch(value):
        raise RollbackError("Rollback reason must be a 10-500 character sanitized operator reason.")
    return value


def select_rollback(
    manifest_path: Path,
    failed_digest: str,
    reason: str,
    *,
    cwd: Path,
) -> dict[str, Any]:
    promote_nutsnews_release.require_match(
        promote_nutsnews_release.SHA256_RE,
        failed_digest,
        "Failed image digest",
    )
    reason = validate_reason(reason)
    current = read_manifest(manifest_path)
    if current["image_digest"] != failed_digest:
        raise RollbackError("Rollback failed digest does not match the current reviewed production digest.")
    restored_digest = current.get("last_known_good_digest", "")
    if not restored_digest:
        raise RollbackError("Current manifest has no recorded last-known-good digest.")
    if restored_digest == failed_digest:
        raise RollbackError("Recorded last-known-good digest must differ from the failed digest.")
    source_commit, restored = find_recorded_release(manifest_path, restored_digest, cwd=cwd)
    return {
        "schema_version": "nutsnews.production_rollback.v1",
        "result": "selected",
        "reason": reason,
        "selected_at": utc_now(),
        "failed": current,
        "restored": restored,
        "restored_manifest_commit": source_commit,
    }


def write_outputs(path: Path | None, values: dict[str, str]) -> None:
    if not path:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def command_select(arguments: argparse.Namespace) -> None:
    evidence = select_rollback(
        arguments.manifest,
        arguments.failed_image_digest,
        arguments.reason,
        cwd=arguments.repo,
    )
    if arguments.evidence:
        arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
        arguments.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    restored = evidence["restored"]
    write_outputs(
        arguments.github_output,
        {
            "failed_image_digest": evidence["failed"]["image_digest"],
            "restored_image_digest": restored["image_digest"],
            "restored_source_commit": restored["source_commit"],
            "restored_build_id": restored["build_id"],
            "restored_source_workflow_run_id": restored["build_id"].split("-", 1)[0],
            "restored_migration_head": restored["migration_head"],
            "restored_schema_version": restored["schema_version"],
            "restored_supabase_project_ref": restored["supabase_project_ref"],
        },
    )
    print(f"Selected recorded last-known-good digest {restored['image_digest']} for rollback.")


def command_write(arguments: argparse.Namespace) -> None:
    evidence = select_rollback(
        arguments.manifest,
        arguments.failed_image_digest,
        arguments.reason,
        cwd=arguments.repo,
    )
    restored = evidence["restored"]
    original = arguments.manifest.read_text(encoding="utf-8")
    updated = replace_manifest_release(original, restored, restored["image_digest"])
    if updated == original:
        raise RollbackError("Rollback did not change the production manifest.")
    arguments.manifest.write_text(updated, encoding="utf-8")
    if arguments.evidence:
        arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
        arguments.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_outputs(
        arguments.github_output,
        {
            "failed_image_digest": evidence["failed"]["image_digest"],
            "restored_image_digest": restored["image_digest"],
            "restored_source_commit": restored["source_commit"],
            "restored_build_id": restored["build_id"],
            "restored_source_workflow_run_id": restored["build_id"].split("-", 1)[0],
            "restored_migration_head": restored["migration_head"],
            "restored_schema_version": restored["schema_version"],
            "restored_supabase_project_ref": restored["supabase_project_ref"],
        },
    )
    print(f"Wrote rollback manifest for {restored['image_digest']}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--failed-image-digest", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--github-output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--select", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        if arguments.select:
            command_select(arguments)
        else:
            command_write(arguments)
    except (RollbackError, promote_nutsnews_release.PromotionError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Production rollback rejected: {error}") from error


if __name__ == "__main__":
    main()
