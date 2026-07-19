#!/usr/bin/env python3
"""Guardrails for the protected Vercel provider-switch workflow."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = (ROOT / ".github/workflows/protected-vercel-provider-switch.yml").read_text(encoding="utf-8")
SCRIPT = (ROOT / "scripts/vercel_provider_switch.py").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for fragment in [
    "workflow_dispatch:",
    "operation:",
    "database_provider_mode:",
    "production_writes_paused:",
    "backend_api_url:",
    "provider_switch_confirmation:",
    "database_provider_mode: process.env.DATABASE_PROVIDER_MODE",
    "backend_api_url: process.env.BACKEND_API_URL",
    "provider_switch_confirmation: process.env.PROVIDER_SWITCH_CONFIRMATION",
    "source_commit:",
    "image_digest:",
    "build_id:",
    "vps_apply_run_id:",
    "staging_deployment_id:",
    "qualification_run_id:",
    "dispatch_vercel_release:",
    "environment: production-vps",
    "concurrency:",
    "cancel-in-progress: false",
    "secrets.NUTSNEWS_VERCEL_TOKEN",
    "secrets.NUTSNEWS_VERCEL_PROJECT_ID",
    "secrets.NUTSNEWS_VERCEL_TEAM_ID",
    "secrets.NUTSNEWS_APP_RELEASE_TOKEN",
    "GH_TOKEN: ${{ secrets.NUTSNEWS_APP_RELEASE_TOKEN }}",
    "scripts/vercel_provider_switch.py",
    "ansible/tests/validate_vercel_provider_switch.py",
    "protected-vercel-provider-switch",
]:
    require(fragment in WORKFLOW, f"Provider switch workflow missing guardrail: {fragment}")

for fragment in [
    "backend_postgres_primary",
    "supabase_primary",
    "enable-backend-postgres-primary",
    "deploy-supabase-primary",
    "https://backend.nutsnews.com/api/app/db",
    "https://api.vercel.com/v10/projects/",
    "upsert",
    "NUTSNEWS_DATABASE_PROVIDER_MODE",
    "NUTSNEWS_PRODUCTION_WRITES_PAUSED",
    "NUTSNEWS_BACKEND_API_URL",
    "safe_metadata_only",
    "mutation_performed",
]:
    require(fragment in SCRIPT, f"Provider switch script missing guardrail: {fragment}")

for forbidden in [
    "set -x",
    "vercel env pull",
    "print(value)",
    "print(variables)",
]:
    require(forbidden not in WORKFLOW, f"Provider switch workflow must not contain {forbidden}")
    require(forbidden not in SCRIPT, f"Provider switch script must not contain {forbidden}")

require(
    WORKFLOW.index("Validate provider switch guardrails") < WORKFLOW.index("Plan or apply Vercel provider switch"),
    "Guardrail validation must run before provider switch apply.",
)
require(
    WORKFLOW.index("Plan or apply Vercel provider switch") < WORKFLOW.index("Dispatch and wait for Vercel production release"),
    "Vercel env update must happen before production release dispatch.",
)

print("Protected Vercel provider switch guardrails passed.")
