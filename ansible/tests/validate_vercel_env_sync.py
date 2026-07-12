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

print("Vercel-to-VPS environment sync guardrails passed.")
