#!/usr/bin/env python3
"""Validate the static portal fixture and secret-safety guardrails."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(".")
STATUS = json.loads((ROOT / "portal/data/status.example.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "portal/assets/app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "portal/assets/styles.css").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py").read_text(encoding="utf-8")
FREE_TIER_COLLECTOR = (
    ROOT / "ansible/roles/vps_service_foundation/files/ops_free_tier_usage.py"
).read_text(encoding="utf-8")
REPORTER = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_reporter.py").read_text(encoding="utf-8")
BACKUP_RUNNER = (ROOT / "ansible/roles/vps_service_foundation/files/vps_restic_backup.py").read_text(encoding="utf-8")
DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
COLLECTOR_UNIT = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-ops-portal-collector.service.j2"
).read_text(encoding="utf-8")
FREE_TIER_ENV = (
    ROOT / "ansible/roles/vps_service_foundation/templates/free-tier-usage.env.j2"
).read_text(encoding="utf-8")
BACKUP_SERVICE = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-restic-backup.service.j2"
).read_text(encoding="utf-8")
BACKUP_ENV = (ROOT / "ansible/roles/vps_service_foundation/templates/vps-backup.env.j2").read_text(encoding="utf-8")
RUN_BACKUP_WORKFLOW = (ROOT / ".github/workflows/run-vps-backup.yml").read_text(encoding="utf-8")
VERIFY_BACKUP_WORKFLOW = (ROOT / ".github/workflows/verify-vps-backup.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


host = STATUS["host"]
require(host.get("public_ipv4") not in ("", "unknown", None), "Fixture public IPv4 must be known.")
require(host.get("public_ipv6") not in ("", "unknown", None), "Fixture public IPv6 must be known.")
require("NUTSNEWS_PUBLIC_IPV4={{ vps_service_foundation_public_ipv4 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv4.")
require("NUTSNEWS_PUBLIC_IPV6={{ vps_service_foundation_public_ipv6 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv6.")

reporting = STATUS["email_reporting"]
for key in (
    "enabled",
    "configured",
    "smtp_host_configured",
    "next_report_run_at",
    "last_report_run_at",
    "last_report_success_at",
    "last_report_sent_at",
    "last_error",
):
    require(key in reporting, f"Email reporting fixture missing {key}.")

process_network = STATUS["process_network"]
require(process_network.get("available") is False, "Per-app network totals must not be faked in the fixture.")
require("does not expose reliable per-process network byte totals" in process_network.get("method", ""), "Network label must be honest.")

redaction = STATUS["logs"]["redaction"].lower()
for word in ("token", "secret", "password", "authorization", "credential", "private-key"):
    require(word in redaction, f"Log redaction description missing {word}.")
    require(word.replace("-", "_") in COLLECTOR.lower() or word in COLLECTOR.lower(), f"Collector redaction missing {word}.")

for token in (
    "gauge-card",
    "temperature-card",
    "Health Score",
    "renderEmailReporting",
    "renderAppLayer",
    "renderFreeTierUsage",
    "app-links",
    "quota-card",
    "quota-metric-name",
    "measurement_status",
    "metric_status_counts",
):
    require(token in APP_JS or token in STYLES, f"Portal UI missing {token}.")

free_tier = STATUS["free_tier_usage"]
providers = free_tier.get("providers", [])
require(isinstance(providers, list) and len(providers) == 10, "Fixture must include all tracked free-tier providers.")
provider_keys = {provider.get("key") for provider in providers}
for key in (
    "vps_host",
    "docker_storage",
    "backup_storage",
    "vercel",
    "sentry",
    "cloudflare",
    "better_stack",
    "supabase",
    "grafana_cloud",
    "github_actions",
):
    require(key in provider_keys, f"Free-tier fixture missing provider {key}.")
source_statuses = {provider.get("source_status") for provider in providers}
for status in ("live", "cached", "not configured", "unavailable"):
    require(status in source_statuses, f"Free-tier fixture missing {status} source state.")
health_states = {provider.get("health") for provider in providers}
for health in ("healthy", "warning", "critical", "over_limit", "unknown", "not_configured"):
    require(health in health_states, f"Free-tier fixture missing {health} health state.")
risk_states = {provider.get("risk_status") for provider in providers}
for risk_status in ("safe", "warning", "critical", "over_limit", "unknown", "not_configured"):
    require(risk_status in risk_states, f"Free-tier fixture missing {risk_status} risk state.")
summary = free_tier.get("summary", {})
for key in ("safe", "warning", "critical", "over_limit", "unknown_or_not_configured"):
    require(key in summary, f"Free-tier summary missing {key}.")
require(any(provider.get("stale") is True for provider in providers), "Free-tier fixture must include stale cache coverage.")
require(any((provider.get("percent_used") or 0) > 100 for provider in providers), "Free-tier fixture must include exceeded quota coverage.")
require("Free Tier Usage" in (ROOT / "portal/index.html").read_text(encoding="utf-8"), "Portal markup missing Free Tier Usage.")
require("free-tier-summary" in (ROOT / "portal/index.html").read_text(encoding="utf-8"), "Portal markup missing free-tier summary.")
require("text(metric.usage_display)" in APP_JS, "Portal metric rows must render backend usage_display.")
require("Number(metric.usage" not in APP_JS, "Portal must not coerce unknown metric usage to numeric zero.")
require("free_tier_usage_state" in COLLECTOR, "Collector must include free-tier usage state.")
require("collect_free_tier_usage" in FREE_TIER_COLLECTOR, "Free-tier collector module missing entrypoint.")
require("local_usage_providers" in COLLECTOR, "Collector must include local usage-limited services.")
require("free_tier_alerts" in COLLECTOR, "Collector must emit free-tier alert pressure.")
require("NUTSNEWS_FREE_TIER_QUOTAS_JSON" in FREE_TIER_ENV, "Free-tier env must pass quota config.")
require("NUTSNEWS_GITHUB_ACTIONS_USAGE_API_URL" in FREE_TIER_ENV, "Free-tier env must pass GitHub usage URL.")
require("NUTSNEWS_CLOUDFLARE_ACCOUNT_ID" in FREE_TIER_ENV, "Free-tier env must pass Cloudflare account ID.")
require("vps_service_foundation_free_tier_env_file" in COLLECTOR_UNIT, "Collector unit must load the free-tier env file.")
require(
    "vps_service_foundation_source_free_tier_collector_module" in TASKS
    and "vps_service_foundation_free_tier_collector_module_file" in TASKS,
    "Free-tier collector module must be installed by Ansible.",
)
require("free-tier-usage.env.j2" in TASKS, "Free-tier env template must be installed by Ansible.")
require("vps_service_foundation_free_tier_quotas" in DEFAULTS, "Free-tier quota defaults must be config-driven.")
for quota_key in (
    "fast_origin_transfer_gb",
    "logs_gb",
    "application_metrics_gb",
    "kv_reads",
    "kv_storage_account_gb",
    "web_events_gb",
    "monthly_active_third_party_users",
    "realtime_peak_connections",
    "synthetic_api_executions",
):
    require(quota_key in DEFAULTS, f"Free-tier quota defaults missing {quota_key}.")
for quota_source in (
    "https://vercel.com/docs/limits",
    "https://sentry.io/pricing/",
    "https://developers.cloudflare.com/kv/platform/limits/",
    "https://developers.cloudflare.com/r2/pricing/",
    "https://supabase.com/docs/guides/platform/billing-on-supabase",
    "https://docs.github.com/en/billing/concepts/product-billing/github-actions",
):
    require(quota_source in DEFAULTS, f"Free-tier quota source missing {quota_source}.")
require("cloudflare_graphql" in FREE_TIER_COLLECTOR, "Free-tier collector must support Cloudflare GraphQL.")
require("No live API credentials" in FREE_TIER_COLLECTOR, "Free-tier collector must degrade when tokens are missing.")
require("ALLOWED_MEASUREMENT_STATUSES" in FREE_TIER_COLLECTOR, "Free-tier collector must expose metric measurement states.")
require("Free-tier usage summary" in REPORTER, "Reporter must include free-tier usage in health reports.")

app = STATUS["app"]
require(isinstance(app, dict), "Fixture app section is missing.")
for key in (
    "enabled",
    "route_enabled",
    "route_path",
    "routing",
    "secrets",
    "deploy_status",
    "marker",
    "image_repo",
    "image_tag",
    "image",
):
    require(key in app, f"App fixture is missing {key}.")
require(app["enabled"] is False, "App fixture should remain disabled by default.")
require(app["route_enabled"] is False, "App fixture should keep route disabled by default.")
require(app["routing"]["status"] == "disabled", "App route status should be disabled by default.")
app_links = STATUS["app_links"]
require(isinstance(app_links, list) and len(app_links) >= 3, "Fixture app links missing app-layer links.")
for required_name in (
    "NutsNews app layer setup",
    "Ops Portal app state",
    "Protected app rollout",
    "Troubleshoot app rollout",
):
    require(
        any(item.get("name") == required_name for item in app_links),
        f"App fixture missing app link: {required_name}.",
    )

backups = STATUS["backups"]
require(backups.get("enabled") is True, "Backup fixture must show enabled backups.")
require(backups.get("configured") is True, "Backup fixture must show configured backups.")
require(backups.get("encryption") == "restic", "Backups must use restic encryption.")
require(backups.get("encrypted_before_transport") is True, "Backups must be encrypted before transport.")
require(backups.get("raw_onedrive_backups") is False, "Fixture must not describe raw readable OneDrive backups.")
require(backups.get("repository") == "rclone:nutsnews-onedrive:nutsnews-backups/vps", "Unexpected backup repo.")
require(backups.get("rclone_remote") == "nutsnews-onedrive", "Backup remote must be dedicated to NutsNews.")
require(backups.get("latest_status") == "fresh", "Fixture latest backup must be fresh.")
require(backups.get("last_backup", {}).get("status") == "success", "Fixture backup status must be success.")
require(backups.get("last_prune", {}).get("status") == "success", "Fixture prune status must be success.")
require(backups.get("last_check", {}).get("status") == "success", "Fixture verify status must be success.")
require(backups.get("retention", {}).get("prune_after_backup") is True, "Backups must prune after backup.")
require("/opt/nutsnews" in backups.get("backup_paths", []), "Backups must include /opt/nutsnews.")
require("/etc/nutsnews" in backups.get("backup_paths", []), "Backups must include /etc/nutsnews.")
require("backup_paths" in APP_JS and "Last Prune" in APP_JS and "Last Verify" in APP_JS, "Portal UI missing backup status.")
require("NUTSNEWS_BACKUP_STATUS_FILE" in COLLECTOR_UNIT, "Collector unit must pass backup status file path.")
require("vps-backup.env.j2" in TASKS, "Backup environment template must be managed by Ansible.")
require("vps_service_foundation_backup_restic_password_file" in TASKS, "Restic password file must be managed by Ansible.")
require("vps_service_foundation_backup_rclone_config_file" in TASKS, "rclone config must be managed by Ansible.")
require("no_log: true" in TASKS, "Secret-bearing backup tasks must use no_log.")
require("RESTIC_PASSWORD_FILE" in BACKUP_ENV, "Backup service must use RESTIC_PASSWORD_FILE.")
require("RCLONE_CONFIG" in BACKUP_ENV, "Backup service must use an explicit rclone config.")
require("ReadWritePaths=" in BACKUP_SERVICE, "Backup service must constrain writable paths.")
require("restic encrypts snapshots locally" in BACKUP_RUNNER, "Backup status must explain encryption before transport.")
require("--keep-daily" in BACKUP_RUNNER and "--prune" in BACKUP_RUNNER, "Backup runner must enforce retention pruning.")
require("Run VPS Backup" in RUN_BACKUP_WORKFLOW, "Manual run backup workflow missing.")
require("Verify VPS Backup" in VERIFY_BACKUP_WORKFLOW, "Manual verify backup workflow missing.")
require("inputs:" not in RUN_BACKUP_WORKFLOW, "Run backup workflow must not accept arbitrary input.")
require("inputs:" not in VERIFY_BACKUP_WORKFLOW, "Verify backup workflow must not accept arbitrary input.")

for forbidden in ("<button", "<form", "docker.sock", "child_process", "execFile", "spawn"):
    require(forbidden not in APP_JS, f"Portal JavaScript includes forbidden control surface: {forbidden}.")

require("last_report_run_at" in REPORTER, "Reporter must record report attempts.")
require("last_report_success_at" in REPORTER, "Reporter must record successful report sends.")
require("ops-reporter.env.j2" in TASKS, "Reporter environment template must be managed by Ansible.")
require("no_log: true" in TASKS, "Reporter environment task must keep SMTP secrets out of logs.")

print("Portal fixture and secret-safety guardrails passed.")
