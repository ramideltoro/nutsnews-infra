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
    "failover_status_hmac_secret = os.environ.get(\"NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET\", \"\").strip()" in WORKFLOW
    and '"NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": failover_status_hmac_secret' in WORKFLOW
    and "failover_controller_status_url(" in WORKFLOW,
    "Protected production apply must support the scoped failover status HMAC overlay when Vercel lacks the controller secret.",
)
require(
    "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET: ${{ secrets.NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET }}" not in WORKFLOW
    and "NUTSNEWS_FAILOVER_CONTROLLER_ACTION_URL: " not in WORKFLOW,
    "Protected production apply must not implicitly enable failover action controls.",
)
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
require(
    '"AUTH_URL": admin_canonical_origin' in WORKFLOW
    and '"NEXTAUTH_URL": admin_canonical_origin' in WORKFLOW
    and '"AUTH_TRUST_HOST": "true"' in WORKFLOW
    and '"NUTSNEWS_ADMIN_CANONICAL_ORIGIN": admin_canonical_origin' in WORKFLOW
    and '"NUTSNEWS_ADMIN_DIRECT_ORIGIN": admin_direct_origin' in WORKFLOW,
    "Protected production apply must force the canonical admin Auth.js origin after merging synced envs.",
)
require(
    "https://www.nutsnews.com" in WORKFLOW and "https://vps.nutsnews.com" in WORKFLOW,
    "Protected production apply must document the canonical and direct admin origins in code.",
)

selected = [name for name, rule in mapping["variables"].items() if rule.get("sync")]
require(selected, "Mapping must contain an explicit synchronization allowlist.")
require("patterns" in mapping, "Mapping must contain explicit classification patterns.")
require(
    all(rule.get("sync") is False for rule in mapping["variables"].values() if rule.get("category") == "manual_review"),
    "Manual-review rules must never sync implicitly.",
)

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

backend_api_token_rule = mapping["variables"].get("NUTSNEWS_BACKEND_API_TOKEN", {})
require(
    backend_api_token_rule.get("category") == "server_side_secret"
    and backend_api_token_rule.get("sync") is True
    and backend_api_token_rule.get("destination") == "NUTSNEWS_BACKEND_API_TOKEN",
    "Backend PostgreSQL provider token must synchronize as an explicit server-side VPS runtime secret.",
)

failover_status_rule = mapping["variables"].get("NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET", {})
require(
    failover_status_rule.get("category") == "server_side_secret"
    and failover_status_rule.get("sync") is True
    and failover_status_rule.get("destination") == "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET",
    "Failover status HMAC secret must synchronize as an explicit server-side VPS runtime secret.",
)

failover_readonly_destinations = {
    "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL",
    "NUTSNEWS_FAILOVER_RUNBOOK_URL": "NUTSNEWS_FAILOVER_RUNBOOK_URL",
    "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL": "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL",
}
for source, destination in failover_readonly_destinations.items():
    rule = mapping["variables"].get(source, {})
    require(
        rule.get("category") == "safe_to_synchronize"
        and rule.get("sync") is True
        and rule.get("destination") == destination,
        f"{source} must remain an explicitly synchronized read-only failover dashboard setting.",
    )

require(
    any(
        rule.get("category") == "manual_review"
        and rule.get("sync") is False
        and "ACTION_HMAC_SECRET" in rule.get("pattern", "")
        for rule in mapping.get("patterns", [])
    ),
    "Manual failover action credentials must remain manual-review only until intentionally enabled.",
)

runtime_safety_destinations = {
    "NUTSNEWS_BACKEND_API_URL": "NUTSNEWS_BACKEND_API_URL",
    "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION": "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION",
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

admin_auth_destinations = {
    "AUTH_URL": "AUTH_URL",
    "NEXTAUTH_URL": "NEXTAUTH_URL",
    "AUTH_TRUST_HOST": "AUTH_TRUST_HOST",
    "NUTSNEWS_ADMIN_CANONICAL_ORIGIN": "NUTSNEWS_ADMIN_CANONICAL_ORIGIN",
    "NUTSNEWS_ADMIN_DIRECT_ORIGIN": "NUTSNEWS_ADMIN_DIRECT_ORIGIN",
}
for source, destination in admin_auth_destinations.items():
    rule = mapping["variables"].get(source, {})
    require(
        rule.get("category") == "safe_to_synchronize"
        and rule.get("sync") is True
        and rule.get("destination") == destination,
        f"{source} must remain an explicitly synchronized and validated admin Auth.js setting.",
    )

valid_runtime_values = {
    "AUTH_GOOGLE_ID": "1234567890-fixture.apps.googleusercontent.com",
    "AUTH_GOOGLE_SECRET": "google-secret-fixture",
    "AUTH_SECRET": "x" * 32,
    "AUTH_URL": "https://www.nutsnews.com",
    "NEXTAUTH_URL": "https://www.nutsnews.com",
    "AUTH_TRUST_HOST": "true",
    "NUTSNEWS_ADMIN_CANONICAL_ORIGIN": "https://www.nutsnews.com",
    "NUTSNEWS_ADMIN_DIRECT_ORIGIN": "https://vps.nutsnews.com",
    "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://nutsnews-controller.nutsnews.workers.dev/status?mode=dashboard",
    "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": "x" * 64,
    "NUTSNEWS_FAILOVER_RUNBOOK_URL": "https://github.com/ramideltoro/nutsnews/blob/main/.github/deployment/failover-visibility-runbook.md",
    "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL": "https://dash.cloudflare.com/example/nutsnews.com/dns/records",
    "NUTSNEWS_DATABASE_PROVIDER_MODE": "backend_postgres_primary",
    "NUTSNEWS_BACKEND_API_URL": "https://backend.nutsnews.com/api/app/db",
    "NUTSNEWS_BACKEND_API_TOKEN": "backend-token-fixture",
    "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION": "enable-backend-postgres-primary",
}
sync.validate_selected_values(valid_runtime_values)
for invalid_values in (
    {**valid_runtime_values, "NUTSNEWS_DATABASE_PROVIDER_MODE": "unknown"},
    {**valid_runtime_values, "NUTSNEWS_BACKEND_API_URL": "https://example.com/api/app/db"},
    {**valid_runtime_values, "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION": "deploy-supabase-primary"},
    {**valid_runtime_values, "AUTH_URL": "https://vps.nutsnews.com"},
    {**valid_runtime_values, "NEXTAUTH_URL": "https://vps.nutsnews.com"},
    {**valid_runtime_values, "AUTH_TRUST_HOST": "false"},
    {**valid_runtime_values, "NUTSNEWS_ADMIN_CANONICAL_ORIGIN": "http://www.nutsnews.com"},
    {**valid_runtime_values, "NUTSNEWS_ADMIN_DIRECT_ORIGIN": "https://www.nutsnews.com"},
    {**valid_runtime_values, "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://example.com/status"},
    {**valid_runtime_values, "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": "too-short"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_BACKEND_API_URL"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_BACKEND_API_TOKEN"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL"},
    {key: value for key, value in valid_runtime_values.items() if key != "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET"},
):
    try:
        sync.validate_selected_values(invalid_values)
    except SystemExit:
        pass
    else:
        raise SystemExit("Provider switch runtime values must fail closed when invalid.")

print("Vercel-to-VPS environment sync guardrails passed.")
