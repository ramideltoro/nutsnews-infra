#!/usr/bin/env python3
"""Validate the static portal fixture and secret-safety guardrails."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(".")
STATUS = json.loads((ROOT / "portal/data/status.example.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "portal/assets/app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "portal/index.html").read_text(encoding="utf-8")
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
BACKUP_VERIFY_TIMER = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-restic-verify.timer.j2"
).read_text(encoding="utf-8")
BACKUP_ENV = (ROOT / "ansible/roles/vps_service_foundation/templates/vps-backup.env.j2").read_text(encoding="utf-8")
RUN_BACKUP_WORKFLOW = (ROOT / ".github/workflows/run-vps-backup.yml").read_text(encoding="utf-8")
VERIFY_BACKUP_WORKFLOW = (ROOT / ".github/workflows/verify-vps-backup.yml").read_text(encoding="utf-8")
sys.dont_write_bytecode = True
COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "ops_portal_collector_for_validation",
    ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py",
)
require_collector_loader = COLLECTOR_SPEC is not None and COLLECTOR_SPEC.loader is not None
if not require_collector_loader:
    raise SystemExit("Could not load collector module for verification-state validation.")
COLLECTOR_MODULE = importlib.util.module_from_spec(COLLECTOR_SPEC)
assert COLLECTOR_SPEC is not None and COLLECTOR_SPEC.loader is not None
COLLECTOR_SPEC.loader.exec_module(COLLECTOR_MODULE)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def verification_case(
    *,
    enabled: bool = True,
    configured: bool = True,
    check_status: str = "success",
    checked_id: str = "abc123de",
    checked_time: str = "2026-07-05T00:30:00+00:00",
    finished_at: str | None = None,
) -> dict[str, object]:
    if finished_at is None:
        finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "enabled": enabled,
        "configured": configured,
        "verify_stale_after_seconds": 691200,
        "latest_snapshot": {
            "id": "abc123def456",
            "short_id": "abc123de",
            "time": "2026-07-05T00:30:00+00:00",
        },
        "last_check": {
            "status": check_status,
            "finished_at": finished_at,
            "latest_snapshot_id": checked_id,
            "latest_snapshot_time": checked_time,
        },
    }


host = STATUS["host"]
require(host.get("public_ipv4") not in ("", "unknown", None), "Fixture public IPv4 must be known.")
require(host.get("public_ipv6") not in ("", "unknown", None), "Fixture public IPv6 must be known.")
require("NUTSNEWS_PUBLIC_IPV4={{ vps_service_foundation_public_ipv4 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv4.")
require("NUTSNEWS_PUBLIC_IPV6={{ vps_service_foundation_public_ipv6 }}" in COLLECTOR_UNIT, "Collector unit must pass IPv6.")

alert_items = STATUS.get("alerts", {}).get("items", [])
alert_ids = [item.get("id") for item in alert_items if isinstance(item, dict)]
require(alert_ids and all(isinstance(item, str) and item for item in alert_ids), "Every portal alert must have a stable ID.")
require(len(alert_ids) == len(set(alert_ids)), "Portal alert IDs must be unique in a snapshot.")
require('alert["id"]' in REPORTER, "Reporter cooldown must use stable alert identity.")
require("ALERT_STATE_MAX_ENTRIES" in REPORTER, "Reporter alert state must have a bounded cleanup policy.")

alloy = STATUS.get("observability", {}).get("alloy", {})
require(alloy.get("enabled") is True, "Fixture must show Alloy enabled for portal visibility.")
require(alloy.get("collect_docker") is False, "Fixture must show Docker/cAdvisor collection disabled by default.")
require(alloy.get("collect_docker_logs") is True, "Fixture must show Docker log collection enabled by default.")
require(alloy.get("container_metrics_strategy") == "cadvisor_disabled", "Fixture must document disabled cAdvisor strategy.")
require(
    alloy.get("log_shipping_strategy") == "docker_api_logs_enabled",
    "Fixture must document enabled Docker API log shipping.",
)
require(alloy.get("permission_errors", {}).get("count") == 0, "Fixture must show zero recent Alloy permission errors.")
require("NUTSNEWS_ALLOY_ENABLED=" in COLLECTOR_UNIT, "Collector unit must pass Alloy enabled state.")
require("NUTSNEWS_ALLOY_COLLECT_DOCKER=" in COLLECTOR_UNIT, "Collector unit must pass Alloy Docker collection state.")
require("NUTSNEWS_ALLOY_COLLECT_DOCKER_LOGS=" in COLLECTOR_UNIT, "Collector unit must pass Alloy Docker log state.")
require("NUTSNEWS_ALLOY_ERROR_WINDOW=" in COLLECTOR_UNIT, "Collector unit must pass Alloy error window.")
require("observability_state" in COLLECTOR, "Collector must expose observability state.")
require("containerd.sock: connect: permission denied" in COLLECTOR, "Collector must count Alloy containerd permission errors.")
require("failed to tail the file: open .*: permission denied" in COLLECTOR, "Collector must count Alloy file-tail permission errors.")
require("collect_docker_logs" in COLLECTOR, "Collector must expose Docker log shipping state.")
require("renderObservability" in APP_JS, "Portal JavaScript must render observability state.")
require("alloy-errors-table" in APP_JS, "Portal JavaScript must render Alloy exporter error visibility.")
require("Recent Alloy Permission Errors" in INDEX_HTML, "Portal must label Alloy permission errors generically.")

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
vercel_fixture = next(provider for provider in providers if provider.get("key") == "vercel")
require(
    "display_unmeasured_status" in DEFAULTS,
    "Vercel quota defaults must opt into explicit unmeasured display states.",
)
require(
    "vercel_api_error_detail" in FREE_TIER_COLLECTOR,
    "Vercel API failures must include Vercel-specific non-secret guidance.",
)
require(
    vercel_fixture.get("current_usage") != "unknown",
    "Vercel provider fixture must not render current usage as generic unknown.",
)
require(
    "Costs not found" in vercel_fixture.get("source_detail", "")
    and "teamId or slug" in vercel_fixture.get("source_detail", ""),
    "Vercel unavailable fixture must include current safe Billing Charges guidance.",
)
for metric in vercel_fixture.get("metrics", []):
    status = metric.get("measurement_status")
    if status in {"missing credential", "unavailable", "unsupported"}:
        require(
            metric.get("usage") is None,
            f"Vercel unmeasured metric {metric.get('key')} must keep numeric usage null.",
        )
        require(
            metric.get("usage_display") == status,
            f"Vercel unmeasured metric {metric.get('key')} must display {status}, not generic unknown.",
        )
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
require("DEFAULT_FREE_TIER_ENV_FILE" in FREE_TIER_COLLECTOR, "Free-tier collector must know the root-only env file.")
require("runtime_env_with_free_tier_file" in FREE_TIER_COLLECTOR, "Free-tier collector must load the env file when env is missing.")
require("vps_service_foundation_free_tier_env_file" in COLLECTOR_UNIT, "Collector unit must load the free-tier env file.")
require(
    "vps_service_foundation_source_free_tier_collector_module" in TASKS
    and "vps_service_foundation_free_tier_collector_module_file" in TASKS,
    "Free-tier collector module must be installed by Ansible.",
)
require("free-tier-usage.env.j2" in TASKS, "Free-tier env template must be installed by Ansible.")
require("vps_service_foundation_free_tier_quotas" in DEFAULTS, "Free-tier quota defaults must be config-driven.")
first_refresh_block = TASKS.split("- name: Refresh operations portal status snapshot", 1)[1].split(
    "- name: Refresh operations portal reporting status snapshot",
    1,
)[0]
second_refresh_block = TASKS.split("- name: Refresh operations portal status after reporting update", 1)[1].split(
    "- name: Validate Caddy Compose configuration",
    1,
)[0]
for refresh_block in (first_refresh_block, second_refresh_block):
    require("ansible.builtin.systemd_service" in refresh_block, "Portal status refresh must use systemd.")
    require(
        "vps_service_foundation_collector_service" in refresh_block,
        "Portal status refresh must use the collector service with its EnvironmentFile.",
    )
    require("state: restarted" in refresh_block, "Portal status refresh must rerun the one-shot collector service.")
    require(
        "vps_service_foundation_collector_bin" not in refresh_block,
        "Portal status refresh must not bypass the collector unit free-tier env.",
    )
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
    "staged_route_enabled",
    "public_route_enabled",
    "route_path",
    "routes",
    "secrets",
    "deploy_status",
    "marker",
    "expected",
    "actual",
):
    require(key in app, f"App fixture is missing {key}.")
require(app["enabled"] is False, "App fixture should remain disabled by default.")
require(app["staged_route_enabled"] is False, "App fixture should keep staged routing disabled by default.")
require(app["public_route_enabled"] is False, "App fixture should keep public routing disabled by default.")
require(app["routes"]["staged"]["enabled"] is False, "Staged route fixture must stay disabled.")
require(app["routes"]["public"]["enabled"] is False, "Public route fixture must stay disabled.")
require(app["expected"]["image_repository"] == "ghcr.io/ramideltoro/nutsnews", "Unexpected app image repo.")
require(app["expected"]["image_digest"] == "", "Prepared fixture must not invent an image digest.")
require("latest" not in json.dumps(app).lower(), "App status must not expose a mutable latest reference.")
for key in ("running_repo_digest", "source_commit", "build_id"):
    require(key in app["actual"], f"App actual runtime status is missing {key}.")
for label in (
    "Expected digest",
    "Running RepoDigest",
    "Source commit",
    "Build ID",
    "Rollback digest",
    "Staged route",
    "Public route",
):
    require(label in APP_JS, f"Portal UI is missing app field: {label}.")
app_links = STATUS["app_links"]
require(isinstance(app_links, list) and len(app_links) >= 3, "Fixture app links missing app-layer links.")
for required_name in (
    "Dual-target web deployment",
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
verification = backups.get("latest_snapshot_verification", {})
require(isinstance(verification, dict), "Fixture must include latest snapshot verification status.")
require(verification.get("status") == "success", "Fixture latest snapshot verification must be success.")
require(verification.get("policy_status") == "current", "Fixture verification policy must be current.")
require(verification.get("latest_snapshot_verified") is True, "Fixture latest snapshot must be marked verified.")
require(verification.get("checked_latest_snapshot") is True, "Fixture verification must match the latest snapshot.")
require(verification.get("stale") is False, "Fixture verification should not be stale.")
require(verification.get("pending") is False, "Fixture successful verification must not be pending.")
require(verification.get("overdue") is False, "Fixture successful verification must not be overdue.")
require(verification.get("deadline_at") not in (None, "", "unknown"), "Fixture must show verification policy deadline.")
require(backups.get("verification_status") == "success", "Fixture top-level verification status must be success.")
require(backups.get("latest_snapshot_verified") is True, "Fixture top-level latest snapshot verified flag must be true.")
require(backups.get("verify_timer") == "nutsnews-restic-verify.timer", "Fixture verify timer missing.")
require(backups.get("verify_timer_active") == "active", "Fixture verify timer must be active.")
require(backups.get("verify_stale_after_hours") == 192, "Fixture verify stale threshold must be 192 hours.")
require(backups.get("retention", {}).get("prune_after_backup") is True, "Backups must prune after backup.")
require(backups.get("backup_paths_redacted") is True, "Backup paths must be redacted from public status.")
require("backup_paths" not in backups, "Public backup fixture must not expose raw backup paths.")
require("missing_paths" not in backups, "Public backup fixture must not expose raw missing paths.")
require("paths" not in backups.get("latest_snapshot", {}), "Public latest snapshot fixture must not expose raw paths.")
require(backups.get("protected_path_count", 0) >= 1, "Backups must expose a protected path count.")
require(
    "Latest Verification" in APP_JS and "Verify Next Run" in APP_JS and "Last Prune" in APP_JS and "deadline_at" in APP_JS,
    "Portal UI missing backup verification status.",
)
require("NUTSNEWS_BACKUP_STATUS_FILE" in COLLECTOR_UNIT, "Collector unit must pass backup status file path.")
require("vps-backup.env.j2" in TASKS, "Backup environment template must be managed by Ansible.")
require("vps_service_foundation_backup_restic_password_file" in TASKS, "Restic password file must be managed by Ansible.")
require("vps_service_foundation_backup_rclone_config_file" in TASKS, "rclone config must be managed by Ansible.")
require("nutsnews-restic-verify.timer.j2" in TASKS, "Verify timer template must be managed by Ansible.")
require("vps_service_foundation_backup_verify_timer" in DEFAULTS, "Verify timer name must be configurable.")
require("vps_service_foundation_backup_verify_on_calendar" in DEFAULTS, "Verify timer cadence must be configurable.")
require("vps_service_foundation_backup_verify_randomized_delay_seconds" in DEFAULTS, "Verify randomized delay must be configurable.")
require("vps_service_foundation_backup_verify_stale_after_hours" in DEFAULTS, "Verify stale threshold must be configurable.")
require("no_log: true" in TASKS, "Secret-bearing backup tasks must use no_log.")
require("RESTIC_PASSWORD_FILE" in BACKUP_ENV, "Backup service must use RESTIC_PASSWORD_FILE.")
require("RCLONE_CONFIG" in BACKUP_ENV, "Backup service must use an explicit rclone config.")
require("NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS" in BACKUP_ENV, "Backup env must pass verify stale threshold.")
require("NUTSNEWS_BACKUP_VERIFY_TIMER" in BACKUP_ENV, "Backup env must pass verify timer name.")
require("ReadWritePaths=" in BACKUP_SERVICE, "Backup service must constrain writable paths.")
require("Unit={{ vps_service_foundation_backup_verify_service }}" in BACKUP_VERIFY_TIMER, "Verify timer must start the fixed verify service.")
require("RandomizedDelaySec={{ vps_service_foundation_backup_verify_randomized_delay_seconds }}" in BACKUP_VERIFY_TIMER, "Verify timer must use configured randomized delay.")
require("restic encrypts snapshots locally" in BACKUP_RUNNER, "Backup status must explain encryption before transport.")
require("--keep-daily" in BACKUP_RUNNER and "--prune" in BACKUP_RUNNER, "Backup runner must enforce retention pruning.")
require("latest_snapshot_verification" in BACKUP_RUNNER, "Backup runner must expose latest snapshot verification.")
require("backup_paths_redacted" in BACKUP_RUNNER, "Backup runner must redact raw backup paths from public status.")
require("latest_snapshot_verification" in COLLECTOR, "Collector must expose latest snapshot verification.")

stale_finished_at = (datetime.now(timezone.utc) - timedelta(days=9)).replace(microsecond=0).isoformat()
verification_cases = {
    "success": verification_case(),
    "failed": verification_case(check_status="failed"),
    "stale": verification_case(finished_at=stale_finished_at),
    "latest_unverified": verification_case(checked_id="old12345", checked_time="2026-07-04T00:30:00+00:00"),
    "disabled": verification_case(enabled=False),
    "misconfigured": verification_case(configured=False),
}
for expected_status, case in verification_cases.items():
    actual = COLLECTOR_MODULE.backup_verification_status(case).get("status")
    require(actual == expected_status, f"Verification case {expected_status} returned {actual}.")

require("Run VPS Backup" in RUN_BACKUP_WORKFLOW, "Manual run backup workflow missing.")
require("Verify VPS Backup" in VERIFY_BACKUP_WORKFLOW, "Manual verify backup workflow missing.")
require("inputs:" not in RUN_BACKUP_WORKFLOW, "Run backup workflow must not accept arbitrary input.")
require("inputs:" not in VERIFY_BACKUP_WORKFLOW, "Verify backup workflow must not accept arbitrary input.")

for forbidden in ("<button", "<form", "docker.sock", "child_process", "execFile", "spawn"):
    require(forbidden not in APP_JS, f"Portal JavaScript includes forbidden control surface: {forbidden}.")

require("last_report_run_at" in REPORTER, "Reporter must record report attempts.")
require("last_report_success_at" in REPORTER, "Reporter must record successful report sends.")
backup_provider = next(provider for provider in providers if provider.get("key") == "backup_storage")
require(backup_provider.get("platform") == "Backup Local Cache", "Backup free-tier provider must describe local cache only.")
require(
    {metric.get("unit") for metric in backup_provider.get("metrics", [])} == {"GiB"},
    "Backup free-tier provider must use measurable GiB capacity.",
)
require(
    not any(metric.get("key") == "latest_snapshot_age_hours" for metric in backup_provider.get("metrics", [])),
    "Snapshot freshness must not be modeled as free-tier storage usage.",
)
require("ops-reporter.env.j2" in TASKS, "Reporter environment template must be managed by Ansible.")
require("no_log: true" in TASKS, "Reporter environment task must keep SMTP secrets out of logs.")

print("Portal fixture and secret-safety guardrails passed.")
