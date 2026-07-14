#!/usr/bin/env python3
"""Render validated, staging-only Ansible variables without logging secrets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from validate_staging_candidate import CandidateError, validate_candidate


SAFE_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
RESERVED_ENV_KEYS = {
    "NUTSNEWS_SOURCE_COMMIT",
    "NUTSNEWS_BUILD_ID",
    "NUTSNEWS_DEPLOYMENT_TARGET",
    "NUTSNEWS_EXPECTED_SOURCE_COMMIT",
    "NUTSNEWS_EXPECTED_BUILD_ID",
    "NUTSNEWS_EXPECTED_IMAGE_DIGEST",
    "NUTSNEWS_DEPLOYED_IMAGE_DIGEST",
    "NUTSNEWS_CONFIG_GENERATION",
    "NUTSNEWS_EXPECTED_SCHEMA_VERSION",
}
REQUIRED_STAGING_ENV_KEYS = {
    "AUTH_GOOGLE_ID",
    "AUTH_GOOGLE_SECRET",
    "AUTH_SECRET",
    "AUTH_URL",
    "NEXTAUTH_URL",
    "NUTSNEWS_EMAIL_MODE",
    "NUTSNEWS_OAUTH_CREDENTIALS_ENV",
    "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF",
    "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY",
    "NUTSNEWS_PUBLIC_SUPABASE_URL",
    "NUTSNEWS_SITE_URL",
    "NUTSNEWS_SUPABASE_PROJECT_REF",
    "NUTSNEWS_SUPABASE_URL",
    "NUTSNEWS_TELEMETRY_ENVIRONMENT",
    "SUPABASE_SERVICE_ROLE_KEY",
}
STAGING_SECRET_ENV_KEYS = {
    "AUTH_GOOGLE_SECRET",
    "AUTH_SECRET",
    "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TURNSTILE_SECRET_KEY",
}


def parse_staging_envs(
    raw: str, protected_overrides: dict[str, str] | None = None
) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CandidateError("NUTSNEWS_STAGING_APP_ENVS_JSON must be valid JSON when configured.") from error
    if not isinstance(values, dict):
        raise CandidateError("NUTSNEWS_STAGING_APP_ENVS_JSON must be a JSON object of string values.")
    if protected_overrides:
        values = {**values, **protected_overrides}
    output: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not SAFE_ENV_KEY.fullmatch(key):
            raise CandidateError("Staging application environment keys must be safe shell-style identifiers.")
        if key in RESERVED_ENV_KEYS:
            raise CandidateError(f"Staging application environment may not override release identity key {key}.")
        if not isinstance(value, str):
            raise CandidateError("Staging application environment values must be strings.")
        if not value.strip():
            raise CandidateError(f"Staging application environment value {key} must not be empty.")
        if "TEST_USER" in key or key.startswith("NUTSNEWS_TEST_"):
            raise CandidateError("Test-user material belongs only in the staging-tests Environment.")
        if "PRODUCTION" in key and key != "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF":
            raise CandidateError("Production-scoped variables may not enter staging configuration.")
        output[key] = value.strip()
    missing = sorted(REQUIRED_STAGING_ENV_KEYS - output.keys())
    if missing:
        raise CandidateError("Staging application configuration is incomplete: " + ", ".join(missing))
    if output["NUTSNEWS_SUPABASE_PROJECT_REF"] == output["NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF"]:
        raise CandidateError("Staging and production Supabase project identities must differ.")
    staging_project = output["NUTSNEWS_SUPABASE_PROJECT_REF"]
    for key in ("NUTSNEWS_SUPABASE_URL", "NUTSNEWS_PUBLIC_SUPABASE_URL"):
        if output[key] != f"https://{staging_project}.supabase.co":
            raise CandidateError(f"{key} must resolve to the declared staging Supabase project.")
    for key in ("AUTH_URL", "NEXTAUTH_URL", "NUTSNEWS_SITE_URL"):
        if output[key].rstrip("/") != "https://staging.nutsnews.com":
            raise CandidateError(f"{key} must be https://staging.nutsnews.com.")
    if output["NUTSNEWS_EMAIL_MODE"] not in {"disabled", "sandbox"}:
        raise CandidateError("Staging email must be disabled or sandboxed.")
    if output["NUTSNEWS_EMAIL_MODE"] == "disabled" and "RESEND_API_KEY" in output:
        raise CandidateError("Disabled staging email must not receive a Resend credential.")
    if output["NUTSNEWS_TELEMETRY_ENVIRONMENT"] != "staging":
        raise CandidateError("Staging telemetry must use the staging environment identity.")
    if output["NUTSNEWS_OAUTH_CREDENTIALS_ENV"] != "staging":
        raise CandidateError("Staging OAuth credentials must use the staging environment identity.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--infra-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    arguments = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", arguments.infra_commit):
        raise SystemExit("Infra commit must be a full lowercase SHA.")
    try:
        candidate = validate_candidate(json.loads(arguments.candidate_file.read_text(encoding="utf-8")))
        staging_envs = parse_staging_envs(os.environ.get("NUTSNEWS_STAGING_APP_ENVS_JSON", ""))
    except (OSError, json.JSONDecodeError, CandidateError) as error:
        raise SystemExit(f"Cannot render staging Ansible variables: {error}") from error

    config_generation = f"staging-{candidate.deployment_id}-{arguments.infra_commit[:12]}"
    configured_secret_env_keys = sorted(STAGING_SECRET_ENV_KEYS & staging_envs.keys())
    values = {
        "vps_service_foundation_nutsnews_staging_deploy_authorized": True,
        "vps_service_foundation_nutsnews_staging_enabled": True,
        "vps_service_foundation_nutsnews_staging_image_digest": candidate.image_digest,
        "vps_service_foundation_nutsnews_staging_source_commit": candidate.source_commit,
        "vps_service_foundation_nutsnews_staging_build_id": candidate.build_id,
        "vps_service_foundation_nutsnews_staging_schema_version": candidate.schema_version,
        "vps_service_foundation_nutsnews_staging_deployment_id": candidate.deployment_id,
        "vps_service_foundation_nutsnews_staging_config_generation": config_generation,
        "vps_service_foundation_nutsnews_staging_app_envs": staging_envs,
        "vps_service_foundation_nutsnews_staging_secret_env_keys": configured_secret_env_keys,
        "vps_service_foundation_nutsnews_staging_required_secrets": configured_secret_env_keys,
        "vps_service_foundation_apply_metadata_enabled": True,
        "vps_service_foundation_apply_context": {
            "workflow": "nutsnews-staging-deploy",
            "environment": "staging-vps",
            "deployment_id": candidate.deployment_id,
            "config_generation": config_generation,
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(values, sort_keys=True) + "\n", encoding="utf-8")
    arguments.output.chmod(0o600)
    if arguments.github_output:
        with arguments.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"config_generation={config_generation}\n")


if __name__ == "__main__":
    main()
