#!/usr/bin/env python3
"""Validate Cloudflare DNS failover Worker guardrails."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = ROOT / "cloudflare/dns-failover"
WRANGLER = WORKER_DIR / "wrangler.toml"
CORE = WORKER_DIR / "src/core.mjs"
ENTRYPOINT = WORKER_DIR / "src/index.mjs"
TESTS = WORKER_DIR / "tests/core.test.mjs"
CI_WORKFLOW = ROOT / ".github/workflows/cloudflare-dns-failover-ci.yml"
APPLY_WORKFLOW = ROOT / ".github/workflows/cloudflare-dns-failover-apply.yml"
RUNBOOK = ROOT / "runbooks/CLOUDFLARE_DNS_FAILOVER.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


wrangler = tomllib.loads(read(WRANGLER))
core = read(CORE)
entrypoint = read(ENTRYPOINT)
tests = read(TESTS)
ci_workflow = read(CI_WORKFLOW)
apply_workflow = read(APPLY_WORKFLOW)
runbook = read(RUNBOOK)

require(wrangler["name"] == "nutsnews-dns-failover", "Worker name must be stable.")
require(wrangler["main"] == "src/index.mjs", "Worker entrypoint must stay under cloudflare/dns-failover.")
require(wrangler["workers_dev"] is True, "Admin endpoints must use workers.dev, not apex/www routes.")
require("route" not in wrangler and "routes" not in wrangler, "Worker must not have apex/www per-request routes.")
require(wrangler["triggers"]["crons"] == ["* * * * *"], "Cron must be minute-level watchdog only.")

bindings = wrangler["durable_objects"]["bindings"]
require(bindings == [{"name": "DNS_FAILOVER", "class_name": "DnsFailoverController"}], "Durable Object binding changed.")
exports = wrangler["exports"]["DnsFailoverController"]
require(exports == {"type": "durable-object", "storage": "sqlite"}, "Durable Object must use SQLite exports.")

vars_ = wrangler["vars"]
expected_vars = {
    "CONTROLLER_NAME": "nutsnews-production-vps-primary",
    "HEALTH_CHECK_URL": "https://vps.nutsnews.com/readyz",
    "EXPECTED_READINESS_TARGET": "production-vps",
    "CHECK_INTERVAL_SECONDS": "15",
    "FAILURE_THRESHOLD": "3",
    "RECOVERY_THRESHOLD": "1",
    "DNS_RECORD_TYPE": "CNAME",
    "DNS_TTL": "1",
    "DNS_PROXIED": "true",
    "PRIMARY_DNS_TARGET": "vps.nutsnews.com",
    "SECONDARY_DNS_TARGET": "cname.vercel-dns.com",
}
for key, value in expected_vars.items():
    require(vars_.get(key) == value, f"Wrangler var {key} must be {value!r}.")

for phrase in (
    "setAlarm(nextAlarm)",
    "bootstrapAlarm()",
    "ctx.waitUntil(stub.fetch",
    "/manual-lock",
    "/manual-failover",
    "/manual-failback",
    "/test-health-override",
    "force-vps-health-failure",
    "clear-vps-health-override",
    "adminAuthorized",
    "CLOUDFLARE_DNS_API_TOKEN",
):
    require(phrase in entrypoint, f"Worker entrypoint must include {phrase}.")

require("AUTOMATIC_DNS_WRITES_ENABLED" in core, "Core config must parse the automatic DNS write gate.")
require("DNS_RECORDS_JSON" in core, "Core config must parse managed DNS record configuration.")

for phrase in (
    "none:failure_threshold_not_met",
    "pending:vps_failure_threshold:vercel",
    "pending:vps_recovered:vps",
    "suppressed:dns_writes_disabled",
    "suppressed:manual_lock",
    "none:vps_already_primary",
    "Operator test health override active",
):
    require(phrase in core + tests, f"State-machine coverage missing {phrase}.")

for path in (CI_WORKFLOW, APPLY_WORKFLOW):
    text = read(path)
    require("actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in text, f"{path} checkout must be pinned.")
    require("actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e" in text, f"{path} setup-node must be pinned.")

for phrase in (
    "environment: cloudflare-admin",
    "refs/heads/main",
    "dns-failover.nutsnews.com",
    "enable-dns-writes-for-nutsnews.com",
    "NUTSNEWS_DNS_FAILOVER_DEPLOY_API_TOKEN",
    "NUTSNEWS_DNS_FAILOVER_DNS_API_TOKEN",
    "NUTSNEWS_DNS_FAILOVER_ZONE_ID",
    "NUTSNEWS_DNS_FAILOVER_RECORDS_JSON",
    "NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN",
    "wrangler@4.113.0 deploy",
    "wrangler@4.113.0 secret put",
):
    require(phrase in apply_workflow, f"Apply workflow must include {phrase}.")

require("production-vps" not in apply_workflow, "Cloudflare deploy must not use VPS SSH environments.")
require("staging-vps" not in apply_workflow, "Cloudflare deploy must not use staging SSH environments.")
require("DNS_WRITES_ENABLED: ${{ inputs.dns_writes_enabled }}" in apply_workflow, "DNS writes input must feed only the Worker secret.")

for phrase in (
    "normal visitor requests do not execute this Worker",
    "Durable Object alarm",
    "15 seconds",
    "Cron Trigger",
    "Auto TTL",
    "300 seconds",
    "manual failover",
    "manual failback",
    "controlled failover drill",
    "force-vps-health-failure",
    "clear-vps-health-override",
    "NUTSNEWS_DNS_FAILOVER_RECORDS_JSON",
    "After #396, with writes enabled and VPS primary active",
):
    require(phrase in runbook, f"Runbook missing required phrase: {phrase}")

node_result = subprocess.run(
    ["node", "--test", "cloudflare/dns-failover/tests/core.test.mjs"],
    cwd=ROOT,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
require(node_result.returncode == 0, "Cloudflare DNS failover node tests failed:\n" + node_result.stdout)

sample_records = [
    {"id": "a" * 32, "name": "nutsnews.com", "type": "CNAME"},
    {"id": "b" * 32, "name": "www.nutsnews.com", "type": "CNAME"},
]
require(json.loads(json.dumps(sample_records))[0]["name"] == "nutsnews.com", "Record JSON fixture is invalid.")

print("Cloudflare DNS failover guardrails passed.")
