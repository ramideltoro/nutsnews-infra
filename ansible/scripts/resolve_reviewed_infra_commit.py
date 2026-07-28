#!/usr/bin/env python3
"""Resolve the infrastructure commit most recently installed by a protected apply."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = "ramideltoro/nutsnews-infra"
APPLY_TITLE = "Protected Ansible Apply (apply)"
AUTOMATED_APPLY_PREFIX = "Automated VPS release "


class ResolutionError(ValueError):
    """Raised when protected-apply provenance cannot be established."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-file", type=Path, required=True)
    parser.add_argument("--artifacts-file", type=Path, required=True)
    parser.add_argument("--current-commit", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    return parser.parse_args()


def _is_apply_title(title: object) -> bool:
    return isinstance(title, str) and (
        title == APPLY_TITLE or title.startswith(AUTOMATED_APPLY_PREFIX)
    )


def resolve_run(payload: object, artifacts_payload: object) -> tuple[str, int, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ResolutionError("Protected apply response must contain workflow_runs.")
    if not isinstance(artifacts_payload, dict) or not isinstance(
        artifacts_payload.get("artifacts"), list
    ):
        raise ResolutionError("Infrastructure artifact response must contain artifacts.")

    attestations: set[tuple[int, str]] = set()
    for artifact in artifacts_payload["artifacts"]:
        if (
            not isinstance(artifact, dict)
            or artifact.get("expired") is not False
            or not isinstance(artifact.get("id"), int)
            or artifact["id"] <= 0
            or not isinstance(artifact.get("workflow_run"), dict)
        ):
            continue
        workflow_run = artifact["workflow_run"]
        run_id = workflow_run.get("id")
        head_sha = workflow_run.get("head_sha")
        if (
            isinstance(run_id, int)
            and run_id > 0
            and isinstance(head_sha, str)
            and FULL_SHA.fullmatch(head_sha) is not None
            and workflow_run.get("head_branch") == "main"
            and workflow_run.get("repository_id") == workflow_run.get("head_repository_id")
            and artifact.get("name") == f"staging-reviewed-infra-{head_sha}"
        ):
            attestations.add((run_id, head_sha))

    candidates: list[tuple[int, str, str]] = []
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        run_id = run.get("id")
        head_sha = run.get("head_sha")
        if (
            not isinstance(run_id, int)
            or run_id <= 0
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or not _is_apply_title(run.get("display_title"))
            or not isinstance(head_sha, str)
            or FULL_SHA.fullmatch(head_sha) is None
            or (run_id, head_sha) not in attestations
        ):
            continue

        expected_url = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"
        if run.get("html_url") != expected_url:
            continue
        candidates.append((run_id, head_sha, expected_url))

    if not candidates:
        raise ResolutionError(
            "No attested successful staging-boundary infrastructure apply on main was found."
        )
    run_id, head_sha, run_url = max(candidates, key=lambda candidate: candidate[0])
    return head_sha, run_id, run_url


def _git(repository_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )


def validate_commit_lineage(
    repository_root: Path, reviewed_commit: str, current_commit: str
) -> None:
    if FULL_SHA.fullmatch(current_commit) is None:
        raise ResolutionError("Current infrastructure commit must be a full lowercase SHA.")

    for label, commit in (
        ("reviewed", reviewed_commit),
        ("current", current_commit),
    ):
        result = _git(repository_root, "cat-file", "-e", f"{commit}^{{commit}}")
        if result.returncode != 0:
            raise ResolutionError(f"The {label} infrastructure commit is unavailable.")

    ancestry = _git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        reviewed_commit,
        current_commit,
    )
    if ancestry.returncode != 0:
        raise ResolutionError(
            "The reviewed infrastructure commit is not an ancestor of the current commit."
        )


def main() -> int:
    args = parse_args()
    try:
        payload: Any = json.loads(args.runs_file.read_text(encoding="utf-8"))
        artifacts_payload: Any = json.loads(args.artifacts_file.read_text(encoding="utf-8"))
        reviewed_commit, run_id, run_url = resolve_run(payload, artifacts_payload)
        validate_commit_lineage(
            args.repository_root.resolve(),
            reviewed_commit,
            args.current_commit,
        )
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"infra_commit={reviewed_commit}\n")
            output.write(f"protected_apply_run_id={run_id}\n")
            output.write(f"protected_apply_run_url={run_url}\n")
    except (OSError, json.JSONDecodeError, ResolutionError) as error:
        print(f"Unable to resolve protected-applied infrastructure commit: {error}", file=sys.stderr)
        return 1

    print(
        f"Resolved protected-applied infrastructure commit {reviewed_commit} "
        f"from workflow run {run_id}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
