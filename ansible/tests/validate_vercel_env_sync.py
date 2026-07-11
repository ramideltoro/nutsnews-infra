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
require('"decrypt": "true"' in SCRIPT_PATH.read_text(encoding="utf-8"), "Vercel API fetch must request decrypted values in memory.")

selected = [name for name, rule in mapping["variables"].items() if rule.get("sync")]
require(selected, "Mapping must contain an explicit synchronization allowlist.")
require("patterns" in mapping, "Mapping must contain explicit classification patterns.")
require(all(rule.get("sync") is False for rule in mapping["variables"].values() if rule.get("category") == "manual_review"), "Manual-review rules must never sync implicitly.")

print("Vercel-to-VPS environment sync guardrails passed.")
