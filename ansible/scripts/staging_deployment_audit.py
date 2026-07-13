#!/usr/bin/env python3
"""Create safe GitHub Deployment API request bodies for staging audit history."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from validate_staging_candidate import CandidateError, DIGEST_PATTERN, validate_candidate


def load_result(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CandidateError("Staging runtime result file is invalid.") from error
    if not isinstance(result, dict):
        raise CandidateError("Staging runtime result file must be an object.")
    return {str(key): str(value) for key, value in result.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--infra-commit", required=True)
    parser.add_argument("--config-generation", required=True)
    parser.add_argument("--github-run-id", required=True)
    parser.add_argument("--phase", choices=("deployment", "status"), required=True)
    parser.add_argument("--job-outcome", choices=("success", "failure", "cancelled"), default="failure")
    parser.add_argument("--runtime-result", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", arguments.infra_commit):
        raise SystemExit("Infra commit must be a full lowercase SHA.")
    if not re.fullmatch(r"staging-stg-[0-9a-f]{24}-[0-9a-f]{12}", arguments.config_generation):
        raise SystemExit("Config generation is not a sanitized staging generation.")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", arguments.github_run_id):
        raise SystemExit("GitHub run ID must be numeric.")

    try:
        candidate = validate_candidate(json.loads(arguments.candidate_file.read_text(encoding="utf-8")))
        result = load_result(arguments.runtime_result)
    except (OSError, json.JSONDecodeError, CandidateError) as error:
        raise SystemExit(f"Cannot prepare staging deployment audit record: {error}") from error

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    actual_digest = result.get("actual_digest", "")
    verified = actual_digest == candidate.image_digest and bool(DIGEST_PATTERN.fullmatch(actual_digest))
    if arguments.phase == "deployment":
        payload = {
            "ref": arguments.infra_commit,
            "task": "nutsnews-staging-deploy",
            "auto_merge": False,
            "required_contexts": [],
            "environment": "staging",
            "transient_environment": True,
            "production_environment": False,
            "description": f"Staging candidate {candidate.deployment_id}",
            "payload": {
                "deployment_id": candidate.deployment_id,
                "requested_digest": candidate.image_digest,
                "actual_digest": None,
                "source_repository": candidate.source_repository,
                "source_commit": candidate.source_commit,
                "build_id": candidate.build_id,
                "source_workflow_run_id": candidate.source_workflow_run_id,
                "schema_version": candidate.schema_version,
                "infra_commit": arguments.infra_commit,
                "config_generation": arguments.config_generation,
                "requested_at": timestamp,
                "github_run_id": arguments.github_run_id,
                "status": "in_progress",
            },
        }
    else:
        state = "success" if arguments.job_outcome == "success" and verified else "failure"
        description = (
            f"{candidate.deployment_id} status={state} actual={actual_digest}"
            if verified
            else f"{candidate.deployment_id} status={state}; readiness/digest verification did not complete"
        )
        payload = {
            "state": state,
            "description": description,
            "log_url": f"https://github.com/ramideltoro/nutsnews-infra/actions/runs/{arguments.github_run_id}",
            "environment": "staging",
            "auto_inactive": False,
        }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
