#!/usr/bin/env python3
"""Revalidate preflight outputs before making a staging candidate file."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from validate_staging_candidate import CandidateError, validate_candidate


ENVIRONMENT_FIELDS = {
    "schema_version": "STAGING_SCHEMA_VERSION",
    "migration_head": "STAGING_MIGRATION_HEAD",
    "supabase_project_ref": "STAGING_SUPABASE_PROJECT_REF",
    "source_repository": "STAGING_SOURCE_REPOSITORY",
    "source_commit": "STAGING_SOURCE_COMMIT",
    "image_repository": "STAGING_IMAGE_REPOSITORY",
    "image_digest": "STAGING_IMAGE_DIGEST",
    "build_id": "STAGING_BUILD_ID",
    "source_workflow_run_id": "STAGING_SOURCE_WORKFLOW_RUN_ID",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        candidate = validate_candidate({key: os.environ.get(name) for key, name in ENVIRONMENT_FIELDS.items()})
    except CandidateError as error:
        raise SystemExit(f"Preflight output candidate was unexpectedly invalid: {error}") from error
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(candidate.as_dict(), sort_keys=True) + "\n", encoding="utf-8")
    arguments.output.chmod(0o600)


if __name__ == "__main__":
    main()
