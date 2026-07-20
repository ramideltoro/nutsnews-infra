#!/usr/bin/env python3
"""Guardrails for the protected Vercel Production to VPS sync path."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = (ROOT / ".github/workflows/protected-ansible-apply.yml").read_text(encoding="utf-8")
SCRIPT_PATH = ROOT / "scripts/vercel_vps_env_sync.py"
MAPPING_PATH = ROOT / "config/vercel-vps-env-sync.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


spec = importlib.util.spec_from_file_location("vercel_vps_env_sync", SCRIPT_PATH)
require(spec is not None and spec.loader is not None, "Could not load the sync validator.")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
mapping = sync.load_mapping(MAPPING_PATH)

require('sync_vercel_production:' in WORKFLOW, "Protected workflow must expose the Vercel sync input.")
require('default: "true"' in WORKFLOW, "Vercel Production sync must be the default operation.")
require("environment: production-vps" in WORKFLOW, "Vercel sync must remain behind the production-vps Environment.")
require("concurrency:" in WORKFLOW and "cancel-in-progress: false" in WORKFLOW, "Sync runs must be serialized.")
require("--check --diff" in WORKFLOW or "args+=(--check --diff)" in WORKFLOW, "Check mode must use Ansible check and diff mode.")
require("--report-output" in WORKFLOW and "--report" in WORKFLOW, "Sync must retain a classification report without values.")
require("sudo -n python3 - fingerprint" in WORKFLOW, "VPS comparison must be read-only and name/hash-only.")
require("shred -u" in WORKFLOW, "Temporary secret-bearing files must be removed after the run.")
require("set -x" not in WORKFLOW, "Secret-bearing workflow must not enable shell tracing.")
require("vercel env pull" not in WORKFLOW, "Sync must not use a plaintext Vercel env export file.")
require("VERCEL_TOKEN" in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel token must be consumed from the environment.")
require("/v1/projects/" in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel sync must use the documented per-variable decrypted-value endpoint.")
require('"decrypt": "true"' not in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel sync must not rely on the deprecated decrypt query parameter.")
require("looks_like_encrypted_envelope" in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel sync must reject encrypted envelope values.")
require("validate_selected_values" in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel sync must validate semantic runtime values.")
require("app_envs.update(vercel_envs)" in WORKFLOW, "Vercel values must be merged before passing extra vars to Ansible.")
require(
    WORKFLOW.count("PRODUCTION_WRITES_PAUSED: ${{ inputs.production_writes_paused }}") >= 3,
    "The production write pause input must be validated, materialized into the VPS runtime, and smoke-tested.",
)
require(
    "--expected-production-writes-paused" in WORKFLOW,
    "The VPS smoke must assert the expected production write pause state.",
)
require(
    '"vps_service_foundation_nutsnews_deployment_environments": (' in WORKFLOW
    and '["production"] if truthy("SYNC_VERCEL_PRODUCTION") else []' in WORKFLOW,
    "Production runtime materialization must be disabled when the reviewed Vercel sync is disabled.",
)

selected = [name for name, rule in mapping["variables"].items() if rule.get("sync")]
require(selected, "Mapping must contain an explicit synchronization allowlist.")
require("patterns" in mapping, "Mapping must contain explicit classification patterns.")
require(all(rule.get("sync") is False for rule in mapping["variables"].values() if rule.get("category") == "manual_review"), "Manual-review rules must never sync implicitly.")

runtime_public_destinations = {
    "NEXT_PUBLIC_SENTRY_DSN": "NUTSNEWS_PUBLIC_SENTRY_DSN",
    "NEXT_PUBLIC_GA_ID": "NUTSNEWS_PUBLIC_GA_ID",
    "NEXT_PUBLIC_NUTSNEWS_IOS_APP_STORE_URL": "NUTSNEWS_PUBLIC_IOS_APP_STORE_URL",
    "NEXT_PUBLIC_TURNSTILE_SITE_KEY": "NUTSNEWS_PUBLIC_TURNSTILE_SITE_KEY",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY",
}
for source, destination in runtime_public_destinations.items():
    require(
        mapping["variables"].get(source, {}).get("destination") == destination,
        f"{source} must synchronize into the runtime-only {destination} destination.",
    )

supabase_url_destinations = mapping["variables"].get("NEXT_PUBLIC_SUPABASE_URL", {}).get("destinations", [])
require(
    "NUTSNEWS_PUBLIC_SUPABASE_URL" in supabase_url_destinations,
    "NEXT_PUBLIC_SUPABASE_URL must synchronize into NUTSNEWS_PUBLIC_SUPABASE_URL.",
)
require(
    "NEXT_PUBLIC_SUPABASE_URL" not in supabase_url_destinations,
    "Legacy NEXT_PUBLIC_SUPABASE_URL must not be rendered into the VPS runtime environment.",
)

runtime_safety_destinations = {
    "NUTSNEWS_BACKEND_API_URL": "NUTSNEWS_BACKEND_API_URL",
    "NUTSNEWS_DATABASE_PROVIDER_MODE": "NUTSNEWS_DATABASE_PROVIDER_MODE",
    "NUTSNEWS_DATA_ENVIRONMENT": "NUTSNEWS_DATA_ENVIRONMENT",
    "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF": "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF",
    "NUTSNEWS_PRODUCTION_WRITES_PAUSED": "NUTSNEWS_PRODUCTION_WRITES_PAUSED",
    "NUTSNEWS_RUNTIME_ENV": "NUTSNEWS_RUNTIME_ENV",
    "NUTSNEWS_SIDE_EFFECTS_MODE": "NUTSNEWS_SIDE_EFFECTS_MODE",
    "NUTSNEWS_SUPABASE_CREDENTIALS_ENV": "NUTSNEWS_SUPABASE_CREDENTIALS_ENV",
    "NUTSNEWS_SUPABASE_PROJECT_REF": "NUTSNEWS_SUPABASE_PROJECT_REF",
}
for source, destination in runtime_safety_destinations.items():
    require(
        mapping["variables"].get(source, {}).get("destination") == destination,
        f"{source} must remain an explicitly synchronized runtime safety identity.",
    )

valid_runtime_values = {
    "AUTH_GOOGLE_ID": "1234567890-fixture.apps.googleusercontent.com",
    "AUTH_GOOGLE_SECRET": "google-secret-fixture",
    "AUTH_SECRET": "x" * 32,
    "NUTSNEWS_DATABASE_PROVIDER_MODE": "backend_postgres_primary",
    "NUTSNEWS_BACKEND_API_URL": "https://backend.nutsnews.com/api/app/db",
}
sync.validate_selected_values(valid_runtime_values)
for invalid_values in (
    {**valid_runtime_values, "NUTSNEWS_DATABASE_PROVIDER_MODE": "unknown"},
    {**valid_runtime_values, "NUTSNEWS_BACKEND_API_URL": "https://example.com/api/app/db"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_BACKEND_API_URL"},
):
    try:
        sync.validate_selected_values(invalid_values)
    except SystemExit:
        pass
    else:
        raise SystemExit("Provider switch runtime values must fail closed when invalid.")

print("Vercel-to-VPS environment sync guardrails passed.")
