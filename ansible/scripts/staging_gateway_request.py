#!/usr/bin/env python3
"""Write a private request for the server-side staging deployment gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from validate_staging_candidate import CandidateError, validate_candidate
from write_staging_ansible_vars import parse_staging_envs


def protected_staging_oauth_overrides(env: dict[str, str]) -> dict[str, str]:
    client_id = str(env.get("NUTSNEWS_STAGING_AUTH_GOOGLE_ID", "")).strip()
    client_secret = str(env.get("NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET", "")).strip()
    if not client_id or not client_secret:
        raise CandidateError("Dedicated staging OAuth credentials are incomplete.")
    return {
        "AUTH_GOOGLE_ID": client_id,
        "AUTH_GOOGLE_SECRET": client_secret,
        "NUTSNEWS_OAUTH_CREDENTIALS_ENV": "staging",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("check", "apply", "verify"), required=True)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--infra-commit", required=True)
    parser.add_argument("--config-generation", default="")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", arguments.infra_commit):
        raise SystemExit("Infra commit must be a full lowercase SHA.")
    try:
        raw_candidate = json.loads(arguments.candidate_file.read_text(encoding="utf-8"))
        candidate = validate_candidate(raw_candidate)
    except (OSError, json.JSONDecodeError, CandidateError) as error:
        raise SystemExit(f"Cannot prepare staging gateway request: {error}") from error
    request: dict[str, object] = {
        "operation": arguments.operation,
        "infra_commit": arguments.infra_commit,
    }
    if arguments.operation in {"check", "apply"}:
        try:
            request["staging_app_envs"] = parse_staging_envs(
                os.environ.get("NUTSNEWS_STAGING_APP_ENVS_JSON", ""),
                protected_staging_oauth_overrides(os.environ),
            )
        except CandidateError as error:
            raise SystemExit(f"Cannot prepare staging gateway request: {error}") from error
        request["candidate"] = raw_candidate
    else:
        request.update(
            {
                "candidate": raw_candidate,
                "config_generation": arguments.config_generation,
                "image_digest": candidate.image_digest,
            }
        )
    arguments.output.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
    arguments.output.chmod(0o600)


if __name__ == "__main__":
    main()
