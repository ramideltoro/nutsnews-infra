#!/usr/bin/env python3
"""Validate the Grafana Cloud observability control-plane enhancements."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
NOTIFICATIONS = (ROOT / "notifications.tf").read_text(encoding="utf-8")
VARIABLES = (ROOT / "variables.tf").read_text(encoding="utf-8")
LOCALS = (ROOT / "locals.tf").read_text(encoding="utf-8")
MAIN = (ROOT / "main.tf").read_text(encoding="utf-8")
ALERTS = (ROOT / "alerts.tf").read_text(encoding="utf-8")
BACKEND = (ROOT / "backend.tf").read_text(encoding="utf-8")
SYNTHETICS = (ROOT / "synthetics.tf").read_text(encoding="utf-8")
SLOS = (ROOT / "slos.tf").read_text(encoding="utf-8")
OUTPUTS = (ROOT / "outputs.tf").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "dashboards/nutsnews-dashboard.json.tftpl").read_text(encoding="utf-8")
VERIFY = (ROOT / "scripts/verify_post_apply.py").read_text(encoding="utf-8")
SYNTHETIC_INPUT_VALIDATOR = (
    ROOT / "scripts/validate_synthetic_monitoring_inputs.py"
).read_text(encoding="utf-8")
PLAN_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
APPLY_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-apply.yml").read_text(encoding="utf-8")
ROLLOUT_DISPATCH_WORKFLOW = (
    REPO / ".github/workflows/grafana-cloud-rollout-dispatch.yml"
).read_text(encoding="utf-8")
CANARY_WORKFLOW = (REPO / ".github/workflows/grafana-notification-canary.yml").read_text(encoding="utf-8")
CANARY = (ROOT / "scripts/exercise_notification_canary.py").read_text(encoding="utf-8")
CANARY_ATTESTATION = (ROOT / "scripts/attest_notification_canary.py").read_text(encoding="utf-8")
ANNOTATION = (ROOT / "scripts/publish_deployment_annotation.py").read_text(encoding="utf-8")
EXTERNAL = json.loads((ROOT / "catalog/non-terraform-alert-rules.json").read_text(encoding="utf-8"))
LINUX_REPLACEMENTS = json.loads(
    (ROOT / "catalog/linux-integration-alert-replacements.json").read_text(
        encoding="utf-8"
    )
)
LINUX_REPLACEMENT_TF = (ROOT / "linux_integration_alerts.tf").read_text(
    encoding="utf-8"
)
BACKEND_CATALOG = json.loads((ROOT / "catalog/backend-observability.json").read_text(encoding="utf-8"))
# Health-audit coverage is validated against this source catalog below.
WORKER_CATALOG = json.loads((ROOT / "catalog/worker-uplift-rabbitmq-alerts.json").read_text(encoding="utf-8"))
ANNOTATION_WORKFLOW = (REPO / ".github/workflows/grafana-deployment-annotation.yml").read_text(encoding="utf-8")
PROMOTION_WORKFLOW = (REPO / ".github/workflows/nutsnews-release-promotion.yml").read_text(encoding="utf-8")
ROLLBACK_WORKFLOW = (REPO / ".github/workflows/protected-nutsnews-rollback.yml").read_text(encoding="utf-8")
PROVIDER_WORKFLOW = (REPO / ".github/workflows/protected-vercel-provider-switch.yml").read_text(encoding="utf-8")
FAILURE_WORKFLOW = (REPO / ".github/workflows/grafana-failure-drill.yml").read_text(encoding="utf-8")
LINUX_MIGRATION_WORKFLOW = (
    REPO / ".github/workflows/grafana-linux-integration-alert-migration.yml"
).read_text(encoding="utf-8")
LINUX_MIGRATOR = (
    ROOT / "scripts/migrate_linux_integration_alerts.py"
).read_text(encoding="utf-8")
FAILURE_CONTRACT = json.loads((REPO / "config/grafana-failure-drills.json").read_text(encoding="utf-8"))
FAILURE_RUNNER = (REPO / "scripts/grafana_failure_drill.py").read_text(encoding="utf-8")
BACKEND_DRILL_EVIDENCE = (REPO / "scripts/validate_backend_drill_evidence.py").read_text(
    encoding="utf-8"
)
SYNTHETIC_DRILL = (ROOT / "scripts/exercise_synthetic_failure_drill.py").read_text(encoding="utf-8")
SYNTHETIC_AUDIT = (ROOT / "scripts/audit_synthetic_inventory.py").read_text(encoding="utf-8")
SYNTHETIC_AUDIT_WORKFLOW = (
    REPO / ".github/workflows/grafana-cloud-synthetic-audit.yml"
).read_text(encoding="utf-8")
SYNTHETIC_WATCHDOG_WORKFLOW = (
    REPO / ".github/workflows/grafana-synthetic-recovery-watchdog.yml"
).read_text(encoding="utf-8")
RUNBOOK = (REPO / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")
READONLY_ENVIRONMENT_RUNBOOK = (
    REPO / "runbooks/GRAFANA_OBSERVABILITY_READONLY_ENVIRONMENT.md"
).read_text(encoding="utf-8")
VPS_EXPORTER = (
    REPO / "ansible/roles/vps_service_foundation/files/observability_textfile_exporter.py"
).read_text(encoding="utf-8")
VPS_COLLECTOR = (
    REPO / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py"
).read_text(encoding="utf-8")
VPS_DEFAULTS = (
    REPO / "ansible/roles/vps_service_foundation/defaults/main.yml"
).read_text(encoding="utf-8")
VPS_BACKUP_SETUP = (REPO / "runbooks/VPS_BACKUP_SETUP.md").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


INVALID_PROMQL_STRING_ESCAPE = re.compile(r"(?<!\\)\\[.()+|?*{}\[\]-]")


def catalog_expressions(value: object, path: str = "catalog") -> list[tuple[str, str]]:
    expressions: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "expr" and isinstance(child, str):
                expressions.append((child_path, child))
            else:
                expressions.extend(catalog_expressions(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            expressions.extend(catalog_expressions(child, f"{path}[{index}]"))
    return expressions


for catalog_name, catalog in (
    ("backend", BACKEND_CATALOG),
    ("worker", WORKER_CATALOG),
):
    for expression_path, expression in catalog_expressions(catalog, catalog_name):
        require(
            INVALID_PROMQL_STRING_ESCAPE.search(expression) is None,
            f"{expression_path} contains an invalid single-backslash PromQL string escape",
        )


for workflow, name in (
    (APPLY_WORKFLOW, "apply"),
    (SYNTHETIC_AUDIT_WORKFLOW, "synthetic audit"),
    (ANNOTATION_WORKFLOW, "deployment annotation"),
    (CANARY_WORKFLOW, "notification canary"),
    (FAILURE_WORKFLOW, "failure drill"),
    (LINUX_MIGRATION_WORKFLOW, "Linux integration alert migration"),
):
    require(
        "queue: max" in workflow and "cancel-in-progress: false" in workflow,
        f"Grafana {name} workflow must retain every pending non-canceling run",
    )
require(
    "inputs.drill == 'synthetic-mismatch' && 'grafana-cloud-apply' || "
    "'production-vps-ansible-baseline'" in FAILURE_WORKFLOW,
    "synthetic mutation drill must share the Grafana apply concurrency lock",
)

for token in (
    "workflow_dispatch:",
    "actions: write",
    "contents: read",
    "github.ref == 'refs/heads/main'",
    "grafana-cloud-plan.yml",
    "grafana-cloud-apply.yml",
    "protected-ansible-apply.yml",
    "grafana-notification-canary.yml",
    "grafana-failure-drill.yml",
    "grafana-linux-integration-alert-migration.yml",
    "grafana-plan",
    "grafana-apply",
    "vps-check",
    "vps-apply",
    "notification-canary-fire-resolve",
    "linux-integration-alert-migrate",
    "failure-drill-dry-run",
    "failure-drill-execute",
    "config/grafana-failure-drills.json",
    "execute-grafana-failure-drill:$target:$DRILL",
    "linux-integration-alert-migration",
    "GH_TOKEN: ${{ github.token }}",
    '.actor.login == "github-actions[bot]"',
    "The exact-main production-vps policy releases secrets without a manual reviewer",
):
    require(token in ROLLOUT_DISPATCH_WORKFLOW, f"Observability rollout dispatcher is incomplete: {token}")
for forbidden in (
    "environment: production-vps",
    "NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN",
    "NUTSNEWS_VPS_SSH_PRIVATE_KEY",
):
    require(
        forbidden not in ROLLOUT_DISPATCH_WORKFLOW,
        f"Observability rollout dispatcher crosses the protected boundary: {forbidden}",
    )

for token in (
    "environment: production-vps",
    "github.ref == 'refs/heads/main'",
    "group: grafana-cloud-apply",
    "linux-integration-alert-migration",
    "migrate_linux_integration_alerts.py",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "retention-days: 90",
):
    require(
        token in LINUX_MIGRATION_WORKFLOW,
        f"Linux integration migration workflow is incomplete: {token}",
    )
for token in (
    "converted_prometheus",
    "configurable_alerts",
    "alerts_disabled",
    "integrationVersionObserved",
    "integrationVersionAvailable",
    "integrationUpgradeStatus",
    "not_available_from_live_api",
    "source_alerts_equivalence_verified",
    "terraform_replacements_verified",
    "recording_rules_changed",
    "logs_changed",
    "INTEGRATION_RULES_PATH",
    "CONVERTED_RULES_NAMESPACE",
    "Integration - Linux Node",
    "X-Grafana-Alerting-Datasource-UID",
    "grafanacloud-prom",
    "rollback_full_namespace",
    "automatic full-bundle rollback verified",
):
    require(token in LINUX_MIGRATOR, f"Linux integration migrator is incomplete: {token}")

for token in (
    r'(?i)^https://kindcantaloupe2036\\.grafana\\.net(:443)?/?\\z',
    "grafana_url must be the exact query-free https://kindcantaloupe2036.grafana.net origin using implicit or explicit port 443.",
    "check.enabled && length(check.valid_status_codes) == 1 && check.valid_status_codes[0] == 200",
):
    require(token in VARIABLES, f"Terraform Grafana origin validation is missing {token}")
require(
    'startswith(var.grafana_url, "https://")' not in VARIABLES,
    "Terraform must not rely on prefix-only Grafana URL validation",
)
require(
    "check.valid_status_codes == [200]" not in VARIABLES,
    "Terraform must not compare a typed status-code list with an untyped tuple literal",
)

for workflow, name in ((PLAN_WORKFLOW, "plan"), (APPLY_WORKFLOW, "apply")):
    require(
        "python3 terraform/grafana-cloud/scripts/validate_synthetic_monitoring_inputs.py" in workflow,
        f"Grafana {name} must run the protected Synthetic Monitoring input validator",
    )
    for token in (
        "NUTSNEWS_GRAFANA_CLOUD_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL",
        "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED",
        "--output \"$RUNNER_TEMP/grafana-cloud-input-validation.json\"",
    ):
        require(token in workflow, f"Grafana {name} protected-input preflight is missing {token}")

for token in (
    'GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"',
    "SYNTHETIC_MONITORING_HOSTNAME = re.compile(",
    "PUBLIC_TARGET_HOSTNAME.fullmatch(parsed.hostname) is None",
    "value != value.strip()",
    "not hostname_is_allowed(hostname)",
    "port not in (None, 443)",
    'parsed.netloc.lower() not in {hostname, f"{hostname}:443"}',
    'parsed.path not in ("", "/")',
    '"NUTSNEWS_GRAFANA_CLOUD_URL"',
    '"NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL"',
):
    require(token in SYNTHETIC_INPUT_VALIDATOR, f"shared Grafana origin preflight is missing {token}")
require(
    'sensitive   = true' in VARIABLES
    and 'trimsuffix(lower(split("/", trimprefix(var.synthetic_http_checks[name].target, "https://"))[0]), ":443")'
    in LOCALS,
    "protected synthetic inputs must remain sensitive and normalize explicit TLS port 443",
)

for token in (
    "synthetic_http_check_names = nonsensitive(toset(keys(var.synthetic_http_checks)))",
    "for_each = local.enabled_synthetic_http_checks",
    "target             = var.synthetic_http_checks[each.key].target",
    "fail_if_body_matches_regexp     = var.synthetic_http_checks[each.key].fail_if_body_matches_regexp",
    "nonsensitive(length(var.synthetic_http_checks[each.key].fail_if_header_matches_regexp))",
    "nonsensitive(length(var.synthetic_http_checks[each.key].fail_if_header_not_matches_regexp))",
):
    require(
        token in f"{LOCALS}\n{SYNTHETICS}",
        f"sensitive Synthetic Monitoring resource expansion is missing {token}",
    )
require(
    "nonsensitive(var.synthetic_http_checks)" not in f"{LOCALS}\n{SYNTHETICS}",
    "the complete protected Synthetic Monitoring map must never be declassified",
)

for script, name in (
    (VERIFY, "post-apply verifier"),
    (CANARY, "notification canary"),
    (ANNOTATION, "deployment annotation"),
    (SYNTHETIC_DRILL, "synthetic drill/watchdog"),
    (FAILURE_RUNNER, "failure-drill alert observer"),
):
    for token in (
        "class NoRedirectHandler(urllib.request.HTTPRedirectHandler)",
        "urllib.request.build_opener(NoRedirectHandler())",
        "self.opener.open(request, timeout=self.timeout)",
    ):
        require(token in script, f"Grafana {name} bearer client is missing {token}")
    require(
        "urllib.request.urlopen(request" not in script,
        f"Grafana {name} must not use the redirect-following urllib convenience opener",
    )

for script, name in (
    (VERIFY, "post-apply verifier"),
    (CANARY, "notification canary"),
    (ANNOTATION, "deployment annotation"),
    (SYNTHETIC_DRILL, "synthetic drill/watchdog"),
):
    require(
        'GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"' in script,
        f"Grafana {name} must pin UI bearer traffic to the NutsNews tenant",
    )
for script, name in (
    (VERIFY, "post-apply verifier"),
    (SYNTHETIC_DRILL, "synthetic drill/watchdog"),
):
    require(
        "SYNTHETIC_MONITORING_HOSTNAME = re.compile(" in script,
        f"Grafana {name} must pin SM bearer traffic to the SM service family",
    )
require(
    'GRAFANA_UI_ORIGIN = "https://kindcantaloupe2036.grafana.net"' in FAILURE_RUNNER
    and "GRAFANA_UI_ORIGIN_SPELLINGS" in FAILURE_RUNNER,
    "failure-drill alert observer must pin bearer traffic to the NutsNews tenant",
)


for token in (
    'resource "grafana_contact_point" "operations_email"',
    'name = "NutsNews operations email"',
    "addresses               = local.operations_email_addresses",
    "disable_resolve_message = false",
    "{{ .CommonLabels.alertname }}",
    'resource "grafana_notification_policy" "operations_email"',
    'value = "critical|major"',
    'value = "warning|minor|low"',
    'group_wait      = "30s"',
    'group_interval  = "5m"',
    'repeat_interval = "1h"',
    'group_wait      = "5m"',
    'group_interval  = "15m"',
    'repeat_interval = "6h"',
    "prevent_destroy = true",
):
    require(token in NOTIFICATIONS, f"managed notification configuration missing {token}")

require('variable "operations_email_recipients"' in VARIABLES, "protected operations email input is missing")
require("sensitive   = true" in VARIABLES, "operations email input must remain sensitive")

active_series_ratio = (
    r'max(grafanacloud_instance_active_series / on(id) '
    r'grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})'
)
require(active_series_ratio in LOCALS, "active-series dashboard and alerts must use the documented live ratio")
require("grafanacloud_instance_metrics_usage" not in LOCALS, "obsolete metrics usage series must not be used")
require(LOCALS.count('no_data_state = "OK"') >= 4, "quota threshold sources must be NoData=OK")
for token in (
    "usage_telemetry_missing_rule",
    'uid           = "nn-gc-usage-telemetry-missing"',
    'no_data_state = "Alerting"',
):
    require(token in LOCALS, f"dedicated usage telemetry missing alert is incomplete: {token}")

for token in (
    'uid           = "nn-caddy-tls-expiry"',
    'uid           = "nn-caddy-tls-probe-missing"',
    "nutsnews_caddy_tls_certificate_probe_success",
    'no_data_state = "OK"',
):
    require(token in LOCALS, f"Caddy TLS expiry/probe alert separation is incomplete: {token}")

require(
    "clamp_min(sum(rate(caddy_http_request_duration_seconds_count" not in LOCALS,
    "Caddy RED ratios must use the true request-rate denominator below one request per second",
)
require(
    LOCALS.count("and on() (sum(rate(caddy_http_request_duration_seconds_count") >= 3,
    "Caddy 4xx, 429, and 5xx ratios must be gated when the true denominator is zero",
)


def bounded_ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator


require(bounded_ratio(0.1, 0.2) == 0.5, "low-traffic ratios must not clamp the denominator to one")
require(bounded_ratio(0.0, 0.0) is None, "zero-traffic ratios must remain undefined")

for text, name in ((ALERTS, "VPS alerts"), (BACKEND, "backend alerts")):
    for token in ("deployment_environment", "owner", "route", "service", "severity"):
        require(token in text, f"{name} are missing normalized {token} labels")
    for token in ("dashboard_url", "runbook_url"):
        require(token in text, f"{name} are missing {token} annotations")

approved_checks = {
    "canonical_articles_api",
    "canonical_homepage",
    "canonical_readiness",
    "vercel_secondary_readiness",
    "vps_readiness",
}
for check in approved_checks:
    require(f'"{check}"' in VARIABLES, f"synthetic input contract missing {check}")
require("length(var.synthetic_monitoring_probe_ids) == 2" in VARIABLES, "synthetics must require two probes")
require(
    "check.frequency_ms == 300000" in VARIABLES,
    "protected synthetic input must retain its legacy five-minute value",
)
for check, frequency in {
    "canonical_articles_api": 300000,
    "canonical_homepage": 300000,
    "canonical_readiness": 300000,
    "vercel_secondary_readiness": 600000,
    "vps_readiness": 600000,
}.items():
    require(
        f"{check}" in LOCALS and f"= {frequency}" in LOCALS,
        f"source-controlled synthetic cadence missing {check}={frequency}",
    )
require(
    "frequency          = local.synthetic_http_check_frequency_ms[each.key]" in SYNTHETICS,
    "synthetic resources must use the source-controlled effective cadence",
)
require(
    "check.enabled && length(check.valid_status_codes) == 1 && check.valid_status_codes[0] == 200"
    in VARIABLES,
    "synthetic checks must fail closed on disabled checks or non-200 status contracts",
)
for token in (
    'jsonencode(["maintenance"])',
    'jsonencode(["NutsNews"])',
    'jsonencode(["deploymentTarget.*unknown"])',
    '"deploymentTarget.*(production-vps|vercel-production)"',
    '"deploymentTarget.*production-vps"',
    '"deploymentTarget.*vercel-production"',
    'regexp        = "no-store"',
    'regexp        = "public|max-age|s-maxage"',
):
    require(token in VARIABLES, f"exact synthetic assertion contract is missing {token}")
for forbidden in ("refresh", "controller", "ingest", "trigger", "publish"):
    require(forbidden in VARIABLES, f"synthetic target validation must forbid {forbidden} routes")
for token in (
    "production-vps",
    "vercel-production",
    "deploymenttarget",
    "cache-control",
    "no-store",
    "maintenance",
    "articles",
):
    require(token in VARIABLES.lower(), f"synthetic semantic contract is missing {token}")
for token in (
    'resource "grafana_synthetic_monitoring_check" "http"',
    "fail_if_header_matches_regexp",
    "fail_if_header_not_matches_regexp",
    "fail_if_body_matches_regexp",
    "fail_if_body_not_matches_regexp",
    "no_follow_redirects             = true",
):
    require(token in SYNTHETICS, f"synthetic checks are missing assertion support: {token}")
for token in (
    'data "grafana_synthetic_monitoring_probes" "available"',
    'data "grafana_synthetic_monitoring_probe" "selected"',
    "length(local.selected_synthetic_monitoring_probe_names) == length(var.synthetic_monitoring_probe_ids)",
    "alltrue([for probe in data.grafana_synthetic_monitoring_probe.selected : probe.public])",
    'output "synthetic_probe_selection"',
):
    require(token in SYNTHETICS + OUTPUTS, f"public synthetic probe resolution is incomplete: {token}")
require(
    "folder_uid" not in SYNTHETICS,
    "synthetic checks must remain in Grafana's default Synthetic Monitoring folder unless a descendant is explicitly managed",
)
require(
    "sm_check_info" in LOCALS
    and 'label_service_namespace=\\"nutsnews\\"' in LOCALS
    and 'label_deployment_environment' in LOCALS,
    "dashboards must join probe series to Synthetic Monitoring check metadata",
)
for text, name in ((LOCALS, "dashboards"), (SLOS, "SLO queries")):
    require(
        re.search(
            r"(?<![A-Za-z0-9_:])probe_success\{(?:service_namespace|deployment_environment)=",
            text,
        )
        is None,
        f"{name} must not assume custom check labels are copied directly onto probe series",
    )
# Grafana's documented gauge SLI form applies range functions directly to the
# uniquely managed job series. The exact five-check API inventory and labels
# are verified independently after apply; joining the info gauge first would
# require a resampling subquery and would no longer count actual executions.
require(
    'probe_success{job=\\"canonical_homepage\\"}[$__interval]' in SLOS
    and 'probe_success{job=\\"canonical_articles_api\\"} == 1' in SLOS,
    "synthetic SLOs must select exact source-controlled check jobs",
)
require(
    "synthetic_monthly_api_hard_ceiling    = 90000" in LOCALS
    and "min(local.synthetic_monthly_api_hard_ceiling, var.free_synthetic_api_executions_monthly * 0.90)"
    in LOCALS,
    "synthetic API execution guardrail must be the lower of 90,000 and 90% of the free allowance",
)
require("* 0.85" in LOCALS, "synthetic API execution major threshold must remain 85% of the free allowance")
require(
    "local.synthetic_monthly_api_executions < local.synthetic_monthly_api_guardrail" in MAIN,
    "synthetic API execution guardrail must fail closed at 90%, not only above it",
)
for token in (
    'variable "enforce_rollout_decisions"',
    'variable "synthetic_major_forecast_acknowledged"',
    "default     = true",
    "default     = false",
    'output "synthetic_monthly_api_major_threshold"',
    'output "synthetic_major_forecast_acknowledged"',
    'output "enforce_rollout_decisions"',
):
    require(
        token in VARIABLES + LOCALS + MAIN + OUTPUTS,
        f"synthetic standing-major rollout gate is incomplete: {token}",
    )
for workflow, name in ((PLAN_WORKFLOW, "plan"), (APPLY_WORKFLOW, "apply")):
    require(
        "NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED" in workflow
        and "TF_VAR_synthetic_major_forecast_acknowledged" in workflow,
        f"Grafana {name} workflow must pass the protected synthetic-major decision",
    )
    require(
        "TF_VAR_enforce_rollout_decisions" not in workflow,
        f"Grafana {name} workflow must retain fail-closed rollout-decision enforcement",
    )
for token in (
    "synthetic_api_execution_projection",
    'uid           = "nn-gc-synthetic-api-executions"',
    "local.synthetic_monthly_api_executions / var.free_synthetic_api_executions_monthly",
    ': "warning"',
):
    require(token in LOCALS, f"synthetic execution quota alerting is incomplete: {token}")
for token in (
    "synthetic_probe_series_contract",
    'uid           = "nn-sm-probe-series-contract"',
    "count by (job, config_version)",
    "!= bool 2",
    'service       = "synthetic-monitoring"',
    'dashboard_url = "/d/nutsnews-synthetic-uptime-api-checks"',
):
    require(token in LOCALS, f"continuous synthetic probe-series alert is incomplete: {token}")
synthetic_failure = LOCALS.split("    synthetic_probe_failure = {", 1)[1].split(
    "\n    }", 1
)[0]
for token in (
    'uid           = "nn-sm-probe-failure"',
    "local.synthetic_joined_probe_series",
    'for_period    = "10m"',
    'no_data_state = "OK"',
):
    require(token in synthetic_failure, f"synthetic probe outcome alert is incomplete: {token}")
require(
    'expr          = "max by (job, probe) (1 - (${local.synthetic_joined_probe_series}))"'
    in synthetic_failure,
    "synthetic probe outcome alert must preserve per-job/per-probe context",
)
for token in (
    "/api/v1/check",
    "monthly_api_execution_estimate",
    "SYNTHETIC_API_EXECUTION_CEILING_MONTHLY",
    "synthetic_check_ids",
    "synthetic_probe_selection",
):
    require(token in SYNTHETIC_AUDIT + VERIFY, f"remote synthetic inventory audit is incomplete: {token}")
require(
    "import verify_post_apply as verifier" in SYNTHETIC_AUDIT
    and "verifier.SyntheticMonitoringProxyClient" in SYNTHETIC_AUDIT,
    "scheduled synthetic audit must inherit the verifier's no-redirect bearer client",
)
require(
    "exercise_synthetic_failure_drill.py watchdog-arm" in SYNTHETIC_WATCHDOG_WORKFLOW
    and "exercise_synthetic_failure_drill.py restore" in SYNTHETIC_WATCHDOG_WORKFLOW,
    "synthetic recovery watchdog must inherit the synthetic drill's no-redirect bearer client",
)
for token in (
    "validate_synthetic_target(",
    "PUBLIC_TARGET_HOSTNAME.fullmatch(hostname) is None",
    'parsed.netloc.lower() not in {hostname, f"{hostname}:443"}',
):
    require(token in VERIFY, f"remote synthetic inventory target validation is missing {token}")
for token in (
    "target_port = parsed_target.port",
    "target_port not in (None, 443)",
    "parsed_target.query",
):
    require(token in SYNTHETIC_DRILL, f"synthetic mutation target validation is missing {token}")
for token in (
    'cron: "17 8 * * *"',
    "if: ${{ github.repository == 'ramideltoro/nutsnews-infra' && github.ref == 'refs/heads/main' }}",
    "environment: grafana-observability-readonly",
    "NUTSNEWS_GRAFANA_SYNTHETIC_EXPECTED_INVENTORY_JSON",
    "NUTSNEWS_GRAFANA_CLOUD_READONLY_SERVICE_ACCOUNT_TOKEN",
    "NUTSNEWS_GRAFANA_SYNTHETIC_DATASOURCE_UID",
    "audit_synthetic_inventory.py",
    "retention-days: 90",
):
    require(token in SYNTHETIC_AUDIT_WORKFLOW, f"scheduled synthetic audit workflow is incomplete: {token}")
plan_protected_job = PLAN_WORKFLOW.split("  plan:", 1)[1]
plan_main_gate = (
    "if: ${{ github.repository == 'ramideltoro/nutsnews-infra' && "
    "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' }}"
)
require(plan_main_gate in plan_protected_job, "protected Grafana plan must require a manual exact-main dispatch")
require(
    plan_protected_job.index(plan_main_gate) < plan_protected_job.index("environment: production-vps"),
    "protected Grafana plan must reject non-main refs before attaching the production Environment",
)
synthetic_audit_job = SYNTHETIC_AUDIT_WORKFLOW.split("  audit:", 1)[1]
synthetic_main_gate = (
    "if: ${{ github.repository == 'ramideltoro/nutsnews-infra' && "
    "github.ref == 'refs/heads/main' }}"
)
require(
    synthetic_audit_job.index(synthetic_main_gate)
    < synthetic_audit_job.index("environment: grafana-observability-readonly"),
    "synthetic audit must reject non-main refs before attaching the read-only Environment",
)
for forbidden in (
    "environment: production-vps",
    "audit-production-vps-policy",
    "NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
    "NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN",
    "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN",
):
    require(forbidden not in synthetic_audit_job, f"scheduled synthetic audit crosses its credential boundary: {forbidden}")
for token in (
    "exact `main` branch",
    "Leave **Required reviewers** empty",
    "least-privilege",
    "Viewer role",
    "datasource proxy",
    "do not add the OpenTofu backend configuration",
):
    require(token in READONLY_ENVIRONMENT_RUNBOOK, f"read-only Environment runbook is incomplete: {token}")
for token in (
    "Deployment branches and tags",
    "Selected branches and tags",
    "exact `main` branch",
    "Leave **Required reviewers** empty",
    "exact `refs/heads/main` ref at job level",
):
    require(token in RUNBOOK, f"production Grafana Environment protection documentation missing: {token}")
for token in (
    "synthetic_inventory_audit_failed",
    "synthetic_inventory_audit_overdue",
    "nutsnews_synthetic_inventory_audit_last_success_age_seconds",
):
    require(token in LOCALS, f"Grafana synthetic audit dead-man alerting is incomplete: {token}")
synthetic_audit_overdue = LOCALS.split(
    "    synthetic_inventory_audit_overdue = {", 1
)[1].split("\n    }", 1)[0]
require(
    synthetic_audit_overdue.count(") or vector(0)) +") >= 5
    and ") > bool 108000) or (max(" not in synthetic_audit_overdue
    and ") < bool 0) or max(absent(" not in synthetic_audit_overdue,
    "synthetic audit overdue rule must add true/false and missing-data arms instead of set-union masking",
)
for token in (
    "grafana-cloud-synthetic-audit.yml/runs?branch=main&event=schedule",
    "def synthetic_inventory_audit_state()",
    '"last_success_at"',
):
    require(token in VPS_COLLECTOR, f"scheduled synthetic audit status collection is incomplete: {token}")
for token in (
    "nutsnews_synthetic_inventory_audit_status_available",
    "nutsnews_synthetic_inventory_audit_conclusion",
    "nutsnews_synthetic_inventory_audit_last_run",
    "nutsnews_synthetic_inventory_audit_last_success",
):
    require(token in VPS_EXPORTER, f"scheduled synthetic audit metric export is incomplete: {token}")

for slo in (
    "public_availability",
    "api_latency",
    "feed_freshness",
    "worker_terminal_success",
):
    require(f"    {slo} = {{" in SLOS, f"Grafana SLO map missing {slo}")
for token in (
    'resource "grafana_slo" "nutsnews"',
    'window = "30d"',
    "fastburn",
    "slowburn",
    "each.value.alerting_enabled ? [true] : []",
    "outcome=~\\\"success|duplicate\\\"",
    "outcome=~\\\"success|duplicate|invalid|failure|dlq\\\"",
    "$__interval",
    "sum_over_time",
    "count_over_time",
):
    require(token in SLOS, f"native Grafana SLO configuration missing {token}")
public_availability_block = SLOS.split("    public_availability = {", 1)[1].split(
    "\n    }\n    api_latency", 1
)[0]
require(
    'sum(sum_over_time(probe_success{job=\\"canonical_homepage\\"}[$__interval]))'
    in public_availability_block
    and 'sum(count_over_time(probe_success{job=\\"canonical_homepage\\"}[$__interval]))'
    in public_availability_block
    and "clamp_max" not in public_availability_block,
    "public availability must use Grafana's gauge execution ratio over $__interval",
)
api_latency_block = SLOS.split("    api_latency = {", 1)[1].split(
    "\n    }\n    feed_freshness", 1
)[0]
api_latency_query = next(
    line for line in api_latency_block.splitlines() if "query" in line
)
require(
    'probe_duration_seconds{job=\\"canonical_articles_api\\"} <= 0.75'
    in api_latency_query
    and 'probe_success{job=\\"canonical_articles_api\\"} == 1' in api_latency_query
    and "count_over_time" in api_latency_query
    and "$__interval:" in api_latency_query,
    "API latency good events must be fast checks intersected with successful checks",
)
require(
    api_latency_query.count(
        'sum(count_over_time((probe_success{job=\\"canonical_articles_api\\"} == 1)[$__interval:]))'
    ) >= 2,
    "API latency denominator must include only successful checks and exclude failed probes",
)
feed_freshness_block = SLOS.split("    feed_freshness = {", 1)[1].split(
    "\n    }\n    worker_terminal_success", 1
)[0]
require(
    "count_over_time" in feed_freshness_block
    and "$__interval:" in feed_freshness_block
    and "<= 900" in feed_freshness_block
    and "nutsnews_backend_content_coverage_available" in feed_freshness_block
    and ") / sum(count_over_time(" in feed_freshness_block
    and "<= bool 900" not in feed_freshness_block,
    "feed freshness must be an alert-compatible event ratio of valid <=15m observations",
)
worker_terminal_block = SLOS.split("    worker_terminal_success = {", 1)[1].split(
    "\n    }\n  }", 1
)[0]
require(
    worker_terminal_block.count("$__rate_interval") == 2
    and "[5m]" not in worker_terminal_block,
    "worker terminal success must use Grafana's required dynamic rate interval",
)
for token in (
    'variable "worker_terminal_slo_alerting_enabled"',
    "default     = false",
    "alerting_enabled = var.worker_terminal_slo_alerting_enabled",
    'output "worker_terminal_slo_alerting_enabled"',
):
    require(token in VARIABLES + SLOS + OUTPUTS, f"protected worker SLO cutover switch is incomplete: {token}")
for workflow, name in ((PLAN_WORKFLOW, "plan"), (APPLY_WORKFLOW, "apply")):
    require(
        "NUTSNEWS_WORKER_TERMINAL_SLO_ALERTING_ENABLED" in workflow
        and "TF_VAR_worker_terminal_slo_alerting_enabled" in workflow,
        f"Grafana {name} workflow must pass the protected worker SLO cutover state",
    )
for token in (
    "target_hosts",
    "parsed.username is not None",
    "parsed.password is not None",
    "port not in (None, 443)",
    "parsed.query",
    "parsed.fragment",
    "distinct direct-VPS and Vercel-secondary hosts",
):
    require(token in SYNTHETIC_INPUT_VALIDATOR, f"shared synthetic target preflight is missing {token}")
require(
    'terraform_output_value(\n        terraform_outputs, "worker_terminal_slo_alerting_enabled"' in VERIFY,
    "post-apply verification must read the protected worker SLO cutover state",
)
require('output "slo_uuids"' in OUTPUTS, "Grafana SLO state output is missing")
require('output "slo_uuids"' in OUTPUTS, "Grafana SLO state output is missing")

require('uid         = "nutsnews-production-ownership"' in LOCALS, "production ownership dashboard is missing")
for label in (
    "web_target",
    "database_provider",
    "ingestion_owner",
    "mode",
    "write_gate",
    "web_revision",
    "infra_revision",
):
    require(label in LOCALS, f"production ownership dashboard missing {label}")
require(
    "nutsnews_production_ownership_last_success_timestamp_seconds" in LOCALS,
    "canonical ownership freshness panel is missing",
)
require(
    "nutsnews_production_ownership_generated_age_seconds" not in LOCALS,
    "ownership freshness must not be derived from the unrelated Ops Portal generated timestamp",
)
require("nutsnews_production_ownership_available" in LOCALS, "ownership source availability panel is missing")
for token in (
    "nutsnews_backend_worker_uplift_deployment_info",
    "nutsnews_backend_worker_uplift_ownership_available",
    "max by (ingestion_owner)",
    "max by (mode, write_gate)",
    'job=\\"nutsnews-backend-host\\"',
    'service_namespace=\\"nutsnews\\"',
    'service=\\"host\\"',
    'host=\\"backend.nutsnews.com\\"',
    'environment=\\"${var.deployment_environment}\\"',
):
    require(token in LOCALS, f"backend protected production ownership query is missing {token}")
require(
    "max by (ingestion_owner) (nutsnews_production_ownership_info" not in LOCALS
    and "max by (worker_uplift_mode, write_gate) (nutsnews_production_ownership_info" not in LOCALS,
    "VPS desired-state textfile metrics must not claim ingestion or worker cutover ownership",
)
for token in (
    'CANONICAL_PRODUCTION_READINESS_URL = "https://www.nutsnews.com/readyz"',
    'payload.get("ready") is not True',
    'payload.get("service") != "nutsnews-web"',
    'response.headers.get("X-NutsNews-Deployment-Target") != web_target',
    'response.headers.get("X-NutsNews-Database-Provider-Mode") != database_provider',
    'response.headers.get("X-NutsNews-Source-Commit") != web_revision',
):
    require(token in VPS_EXPORTER, f"canonical production readiness validation is missing {token}")
require(
    "NUTSNEWS_PRODUCTION_OWNERSHIP" not in VPS_EXPORTER
    and "DEFAULT_PRODUCTION_OWNERSHIP" not in VPS_EXPORTER
    and "PRODUCTION_OWNERSHIP" not in VPS_EXPORTER,
    "production ownership exporter must not retain static desired-state fallbacks",
)
for token in (
    "nutsnews_backend_api_build_info",
    "nutsnews_backend_worker_uplift_deployed_identity_available",
    "nutsnews_backend_worker_uplift_deployed_service_info",
    "worker_service, service_version, revision, image_digest",
    "nutsnews_worker_deployment_info",
    "Awaiting republished worker images",
    "Worker readiness by ownership mode",
    "Disabled by configuration — shadow",
):
    require(token in LOCALS, f"production ownership dashboard missing {token}")
require('try(panel.noValue, local.dashboard_no_value[dashboard_key])' in LOCALS, "VPS panels must resolve source-specific no-value states")
require('lookup(\n              local.backend_dashboard_no_value' in BACKEND, "backend panels must resolve source-specific no-value states")
for dashboard in BACKEND_CATALOG["dashboards"]:
    require(f'"{dashboard["uid"]}"' in BACKEND, f"backend no-value contract missing dashboard {dashboard['uid']}")
worker_slo_panels = WORKER_CATALOG["dashboards"][0]["panels"]
require(all(panel.get("noValue") for panel in worker_slo_panels), "every worker SLO panel must declare its exact no-value semantics")
no_value_by_title = {panel["title"]: panel["noValue"] for panel in worker_slo_panels}
require(
    no_value_by_title["Stage Success Ratio"] == "Unavailable — stage event telemetry itself is missing"
    and no_value_by_title["Publication Success Ratio"] == "Unavailable — publication event telemetry itself is missing",
    "worker success panels must reserve no-value text for genuinely missing telemetry",
)
mapped_worker_panels = {
    panel["title"]: panel
    for panel in worker_slo_panels
    if panel["title"] in {
        "Stage Success Ratio",
        "Stage P95 Latency",
        "Retry And DLQ Budget Ratio",
        "Publication Success Ratio",
    }
}
require(len(mapped_worker_panels) == 4, "worker SLO dashboard is missing a required lifecycle panel")
for title, panel in mapped_worker_panels.items():
    mappings = panel.get("mappings") or []
    if title == "Stage P95 Latency":
        require(
            "(0 * max by (service)" in panel["expr"]
            and "unless on(service)" in panel["expr"]
            and "stage_latency_seconds_count" in panel["expr"],
            "Stage P95 Latency must derive an idle sentinel from a present zero-count histogram",
        )
    else:
        require("vector(-1)" in panel["expr"], f"{title} must emit an explicit bounded disabled/empty sentinel")
    require(
        any("-1" in (mapping.get("options") or {}) for mapping in mappings),
        f"{title} must map its -1 sentinel to an operator-readable state",
    )
require(
    "or on() label_replace(vector(-1)" in mapped_worker_panels["Stage Success Ratio"]["expr"],
    "Stage Success Ratio sentinel must be a global empty-result fallback, not an extra live series",
)
require(
    no_value_by_title["Feed Freshness Age"] == "Unavailable — durable production content freshness telemetry is missing or stale",
    "feed freshness no-value semantics must treat durable production telemetry as required",
)
for text, name in ((LOCALS, "VPS dashboard generator"), (BACKEND, "backend dashboard generator")):
    require('"No data"' not in text, f"{name} must not emit the generic No data state")
    require("mappings" in text, f"{name} must render explicit panel value mappings")
ownership_dashboard = LOCALS.split("    production_ownership = {", 1)[1].split(
    "\n    }\n  }\n\n  dashboard_panels", 1
)[0]
ownership_panels = [
    line.strip() for line in ownership_dashboard.splitlines() if line.lstrip().startswith("{ title =")
]
require(len(ownership_panels) == 11, "ownership dashboard must keep eleven summary, identity, readiness, and freshness cards")
require(
    ownership_dashboard.count("nutsnews_backend_metric_scrape_timestamp_seconds") >= 3
    and ownership_dashboard.count("nutsnews_backend_metric_exporter_available") >= 3
    and "< 600" in ownership_dashboard
    and "< bool 600" in ownership_dashboard,
    "backend ownership cards and combined availability must reject stale or failed exporter state",
)
require(
    all('type = "stat"' in panel for panel in ownership_panels),
    "ownership dashboard must remain summary-first with stat cards",
)
require(
    all(
        "description =" in panel
        and ("textfile export" in panel.lower() or "ownership" in panel.lower())
        for panel in ownership_panels
    ),
    "ownership dashboard cards must identify their authoritative durable source",
)
require(
    all(
        'noValue = "Unavailable' in panel
        or 'noValue = "Awaiting republished worker images' in panel
        for panel in ownership_panels
    ),
    "ownership dashboard cards must expose explicit unavailable or rollout states",
)
require(
    all('deployment_environment=\\"${var.deployment_environment}\\"' in panel for panel in ownership_panels),
    "ownership dashboard cards must use one consistent production-environment query boundary",
)
overview_dashboard = LOCALS.split("    vps_overview = {", 1)[1].split("\n    }\n\n    logs_overview", 1)[0]
overview_panels = [
    line.strip() for line in overview_dashboard.splitlines() if line.lstrip().startswith("{ title =")
]
require(
    all("description =" in panel and "noValue = \"Unavailable" in panel for panel in overview_panels),
    "default VPS overview panels must expose source context and explicit unavailable states",
)
require(
    'expr = "vector(-1)"' in LOCALS
    and "Disabled by configuration — Tempo deferred" in LOCALS,
    "deferred trace panels must render an explicit disabled-by-configuration state",
)

relay_dashboard = next(
    dashboard
    for dashboard in BACKEND_CATALOG["dashboards"]
    if dashboard["uid"] == "nutsnews-backend-postgres-failover"
)
for panel in relay_dashboard["panels"]:
    if not panel["title"].startswith("Sync Relay") or panel["title"] in {
        "Sync Relay Configuration State",
        "Sync Relay Expected Active",
    }:
        continue
    mappings = panel.get("mappings") or []
    require("-1 * max" in panel["expr"], f"relay panel must totalize not-configured state: {panel['title']}")
    require(
        any(
            any("Disabled by configuration" in str(option.get("text", "")) for option in (mapping.get("options") or {}).values())
            for mapping in mappings
        ),
        f"relay panel must label the not-configured state explicitly: {panel['title']}",
    )
for token in (
    'relay_status == "not_configured"',
    '"disabled sync relay emitted configured-only healthy samples',
    'relay_status == "pass"',
):
    require(token in VERIFY, f"post-apply relay verification is missing explicit state handling: {token}")

backend_backup = next(
    dashboard
    for dashboard in BACKEND_CATALOG["dashboards"]
    if dashboard["uid"] == "nutsnews-backend-backups"
)
require(
    any(
        panel["title"] == "Last Successfully Verified Backup Age"
        and "nutsnews_backend_backup_last_success_age_seconds" in panel["expr"]
        for panel in backend_backup["panels"]
    ),
    "backend backup dashboard must expose successfully verified backup age",
)
for token in (
    "Last successful verification age",
    "nutsnews_backup_last_verify_finished_age_seconds",
    "> bool 108000",
    'vps_service_foundation_backup_verify_on_calendar: "*-*-* 05:15:00"',
    "nutsnews_backup_last_verify_finished",
    "runs daily at 05:15",
    "stale after 30 hours",
):
    require(
        token in LOCALS + VPS_DEFAULTS + VPS_EXPORTER + VPS_BACKUP_SETUP,
        f"standardized 30-hour backup verification contract is incomplete: {token}",
    )
require("NutsNews deployment events" in TEMPLATE, "dashboard annotation query is missing")
require("nutsnews-deployment" in TEMPLATE, "dashboard annotation tag is missing")
for token in (
    "/api/annotations",
    '"nutsnews-deployment"',
    '"commit"',
    '"image_digest"',
    '"version"',
    '"target"',
    '"outcome"',
):
    require(token in ANNOTATION, f"append-only deployment annotation publisher missing {token}")
for token in (
    "def validated_annotation_id(",
    "positive integer id",
    '"annotation_id": annotation_id',
):
    require(token in ANNOTATION, f"deployment annotation receipt verification missing {token}")

for token in (
    "workflow_call",
    "github.repository == 'ramideltoro/nutsnews-infra'",
    "github.ref == 'refs/heads/main'",
    "publish_deployment_annotation.py",
    "retention-days: 90",
):
    require(token in ANNOTATION_WORKFLOW, f"reusable deployment annotation workflow missing {token}")
annotation_call_contract = ANNOTATION_WORKFLOW.split("  workflow_call:", 1)[1].split("\npermissions:", 1)[0]
for secret in (
    "NUTSNEWS_GRAFANA_CLOUD_URL",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
):
    require(
        f"      {secret}:" in annotation_call_contract,
        f"reusable deployment annotation workflow must declare required secret {secret}",
    )
require(
    annotation_call_contract.count("        required: true") == 8,
    "reusable annotation contract must expose six required inputs and exactly two required secrets",
)
annotation_publish_block = ANNOTATION_WORKFLOW.split(
    "      - name: Checkout reviewed annotation publisher", 1
)[1].split("      - name: Retain deployment annotation receipt", 1)[0]
require(
    "continue-on-error: true" not in annotation_publish_block,
    "annotation checkout/publish failures must produce a failed workflow conclusion",
)
require(
    "Report annotation delivery failure" in annotation_publish_block
    and "exit 1" in annotation_publish_block
    and '"status": "delivery_unverified"' in annotation_publish_block
    and "::error title=Grafana deployment annotation delivery failed" in annotation_publish_block,
    "annotation failure reporting must remain visibly failed",
)
promotion_annotation_job = PROMOTION_WORKFLOW.split("  annotate-promotion:", 1)[1]
for token in (
    "always()",
    "continue-on-error: true",
    "gh workflow run grafana-deployment-annotation.yml",
    "--ref main",
    "--field event_type=promotion",
    "--field target=production-vps",
    "--field evidence=\"$PROMOTION_RUN_URL\"",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "Record final annotation dispatch failure without changing promotion authority",
    "The completed production promotion result remains authoritative",
):
    require(token in promotion_annotation_job, f"promotion annotation dispatch is missing {token}")
for secret in (
    "NUTSNEWS_GRAFANA_CLOUD_URL",
    "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN",
):
    require(
        secret not in promotion_annotation_job,
        f"promotion must leave protected annotation secret {secret} in the separate workflow",
    )
require(
    "uses: ./.github/workflows/grafana-deployment-annotation.yml" not in promotion_annotation_job,
    "promotion final annotation must run separately so delivery cannot change promotion authority",
)
require("secrets: inherit" not in PROMOTION_WORKFLOW, "promotion must not pass all repository secrets to the annotation workflow")
require("--event-type rollback" in ROLLBACK_WORKFLOW and "always()" in ROLLBACK_WORKFLOW, "rollback final outcome annotation is incomplete")
require(
    "--event-type database-provider-change" in PROVIDER_WORKFLOW and "always()" in PROVIDER_WORKFLOW,
    "database-provider final outcome annotation is incomplete",
)

drilldown = next(
    dashboard
    for dashboard in BACKEND_CATALOG["dashboards"]
    if dashboard["uid"] == "nutsnews-worker-pipeline-run-drilldown"
)
require(len(drilldown["uid"]) <= 40, "pipeline drilldown UID exceeds Grafana's API limit")
require(drilldown.get("importExisting") is False, "pipeline drilldown must be source-created")
variables = {item["name"]: item for item in drilldown.get("variables", [])}
require(variables.get("identifier_value", {}).get("type") == "textbox", "pipeline identifier must use a scoped textbox")
drilldown_text = json.dumps(drilldown)
for token in (
    "${identifier_value:regex}",
    "pipelineRunId",
    "pipeline_run_id",
    "correlationId",
    "correlation_id",
    "traceparent",
    'deployment_environment=~\\"$environment\\"',
    'source=\\"container\\"',
    "| keep service, outcome",
    '"id": "links"',
    "${__value.raw}",
):
    require(token in drilldown_text, f"pipeline correlation drilldown missing {token}")
for forbidden in (
    '{pipelineRunId=',
    '{pipeline_run_id=',
    '{correlationId=',
    '{correlation_id=',
    '{traceparent=',
    '"datasource": "tempo"',
    'trace_id=',
):
    require(forbidden.lower() not in drilldown_text.lower(), f"pipeline drilldown violates the log-only correlation policy: {forbidden}")
for token in (
    "variables   = try(dashboard.variables, [])",
    "field_overrides = try(panel.fieldOverrides, [])",
    "extra_variables           = each.value.variables",
    "overrides = panel.field_overrides",
):
    require(token in BACKEND, f"backend dashboard generator missing pipeline drilldown support: {token}")
require("for variable in extra_variables" in TEMPLATE, "dashboard template does not render dashboard-scoped variables")

require(EXTERNAL["folderUid"] == "integration---linux-node", "external rule folder changed")
require(EXTERNAL["owner"] == "grafana-cloud-linux-integration", "external rule owner is missing")
require(EXTERNAL["schemaVersion"] == 2, "external rule inventory schema must classify alerts and recording rules")
require(EXTERNAL["integrationVersionTarget"] == "1.6.3", "Linux integration upgrade target drifted")
require("grafana.com/docs/" in EXTERNAL["integrationEvidenceUrl"], "vendor-obsolete evidence must cite Grafana documentation")
external_context = EXTERNAL["contextPolicy"]
require(
    set(external_context["requiredAlertLabels"])
    == {
        "severity",
        "owner",
        "route",
        "service",
        "deployment_environment",
        "service_namespace",
        "managed_by",
        "source_integration",
    },
    "vendor alerts must not be exempted from the universal label contract",
)
require(
    set(external_context["requiredAlertAnnotations"])
    == {"summary", "description", "dashboard_url", "runbook_url"},
    "vendor alerts must not be exempted from dashboard/runbook context",
)
require(
    external_context["requiredAlertLabelValues"]
    == {
        "owner": "nutsnews-observability",
        "route": "operations-email",
        "service": "vps-host",
        "deployment_environment": "production",
        "service_namespace": "nutsnews",
        "managed_by": "nutsnews-infra",
        "source_integration": "linux-node",
    }
    and external_context["requiredAlertAnnotationValues"]
    == {
        "dashboard_url": "/d/nutsnews-vps-overview",
        "runbook_url": "https://github.com/ramideltoro/nutsnews-infra/blob/main/runbooks/GRAFANA_CLOUD_OBSERVABILITY.md",
    },
    "vendor alert normalized ownership/routing context drifted",
)
require(
    external_context["severityNormalization"]
    == {"info": "low", "warning": "warning", "critical": "critical"},
    "vendor alert severity mapping must normalize info to low",
)
require(
    external_context["normalizationStatus"] == "approved"
    and "Terraform provisions exact source-reviewed normalized equivalents"
    in external_context["normalizationMechanism"],
    "vendor normalization must use reviewed Terraform replacements and the protected migration",
)
require(
    EXTERNAL["integrationVersionObserved"] == "1.6.2"
    and EXTERNAL["integrationVersionAvailable"] == "1.6.2"
    and EXTERNAL["integrationUpgradeStatus"] == "not_available_from_live_api",
    "Linux integration upgrade availability must match authenticated live evidence",
)
retained_external = [rule for rule in EXTERNAL["rules"] if rule["disposition"] == "retain"]
obsolete_external = [
    rule
    for rule in EXTERNAL["rules"]
    if rule["disposition"] == "remove_via_integration_upgrade"
]
replaced_external = [
    rule
    for rule in EXTERNAL["rules"]
    if rule["disposition"] == "replaced_by_terraform_normalized_equivalent"
]
require(
    len(retained_external) == EXTERNAL["expectedRetainedRuleCount"] == EXTERNAL["expectedPostUpgradeRuleCount"],
    "external retained inventory must match the safe post-upgrade count",
)
require(
    len(retained_external) == 11
    and all(rule["kind"] == "recording" for rule in retained_external),
    "only the 11 long-term integration recording rules may remain retained",
)
require(
    len(replaced_external) == 24
    and all(rule["kind"] == "alert" for rule in replaced_external),
    "all 24 vendor alerts must map to source-owned normalized replacements",
)
replacement_by_source = {
    rule["sourceUid"]: rule for rule in LINUX_REPLACEMENTS["rules"]
}
require(
    LINUX_REPLACEMENTS["schemaVersion"] == 1
    and LINUX_REPLACEMENTS["sourceFolderUid"] == EXTERNAL["folderUid"]
    and LINUX_REPLACEMENTS["destinationFolderUid"] == "nutsnews-observability"
    and LINUX_REPLACEMENTS["groupName"]
    == "NutsNews Linux integration alert replacements"
    and len(replacement_by_source) == 24,
    "Linux integration replacement catalog identity or count drifted",
)
require(
    all(
        rule.get("replacementUid")
        == replacement_by_source.get(rule["uid"], {}).get("replacementUid")
        for rule in replaced_external
    ),
    "vendor alert replacement UID mapping drifted",
)
require(
    all(
        replacement["normalizedSeverity"]
        == external_context["severityNormalization"][replacement["sourceSeverity"]]
        and replacement["condition"] == "threshold"
        and replacement["queryFrom"] == 660
        and replacement["queryTo"] == 60
        and bool(replacement["expr"])
        and bool(replacement["summary"])
        and bool(replacement["description"])
        for replacement in LINUX_REPLACEMENTS["rules"]
    ),
    "Linux integration replacement definitions or severity mapping drifted",
)
for token in (
    'resource "grafana_rule_group" "linux_integration_alert_replacements"',
    "prevent_destroy = true",
    "linux-integration-alert-replacements.json",
    "rule.value.replacementUid",
    "rule.value.normalizedSeverity",
    "source_integration     = \"linux-node\"",
    "linux_integration_alert_replacement_uids",
):
    require(token in LINUX_REPLACEMENT_TF, f"Linux replacement Terraform is incomplete: {token}")
require(
    len(EXTERNAL["rules"]) == EXTERNAL["legacyObservedRuleCount"],
    "legacy observed inventory metadata drifted",
)
require(
    {rule["group"] for rule in obsolete_external} == {"asserts-node.rules"}
    and all(rule["kind"] == "recording" for rule in obsolete_external),
    "only the vendor-proven obsolete Asserts recording rules may be upgrade-removed",
)
uids = [rule["uid"] for rule in EXTERNAL["rules"]]
require(len(uids) == len(set(uids)), "external inventory rule UIDs must be unique")
require(Counter(rule["group"] for rule in EXTERNAL["rules"]) == Counter(EXTERNAL["legacyGroups"]), "external legacy group counts drifted")
require(
    Counter(rule["kind"] for rule in EXTERNAL["rules"]) == Counter(EXTERNAL["legacyKindCounts"]),
    "external legacy alert/recording classification drifted",
)
require(
    Counter(rule["kind"] for rule in retained_external) == Counter(EXTERNAL["expectedPostUpgradeKindCounts"]),
    "external post-upgrade kind counts drifted",
)
for rule in retained_external:
    require(rule["kind"] in {"alert", "recording"}, f"external rule kind missing: {rule['uid']}")
    if rule["kind"] == "alert":
        require(rule.get("severity") in {"info", "warning", "critical"}, f"external alert severity missing: {rule['uid']}")
fingerprint_policy = EXTERNAL["definitionFingerprintPolicy"]
require(fingerprint_policy["algorithm"] == "sha256", "external definition baseline must use SHA-256")
require(
    fingerprint_policy["requiredDisposition"] == "retain",
    "external definition baselines must cover every retained rule",
)
require(
    fingerprint_policy["baselineStatus"]
    in {"pending_authenticated_rollout", "approved"},
    "external definition baseline status is invalid",
)
if fingerprint_policy["baselineStatus"] == "pending_authenticated_rollout":
    require(
        all("definitionFingerprintSha256" not in rule for rule in retained_external),
        "pending external definition baselines must not contain a partial or invented hash set",
    )
else:
    require(
        all(
            re.fullmatch(r"[0-9a-f]{64}", rule.get("definitionFingerprintSha256", ""))
            for rule in retained_external
        ),
        "approved external definition baselines must cover all 11 retained rules",
    )
for token in (
    "def validate_external_rule_inventory(",
    "definition_fingerprint_sha256",
    "definition_fingerprint_status",
    "definition_drift_validation",
    "pending_authenticated_rollout",
    "matched-approved-baseline",
    "protected Terraform-equivalence migration",
    "requiredAlertLabels",
    "requiredAlertAnnotations",
    "requiredAlertLabelValues",
    "requiredAlertAnnotationValues",
    "severityNormalization",
    "normalizationStatus",
    "explicit rollout blocker",
    "remove_via_integration_upgrade",
    "expectedPostUpgradeRuleCount",
    "expectedAlertsDisabledRuleCount",
    "replaced_by_terraform_normalized_equivalent",
    "LINUX_ALERT_REPLACEMENT_UIDS",
    "all_ruler_rules_by_uid",
):
    require(token in VERIFY, f"external live rule verification missing {token}")

for token in (
    "USAGE_QUERIES",
    "metrics_active_series_ratio",
    "contact-points",
    "provisioning/policies",
    "DatasourceNoData",
    "DatasourceError",
    "external_rule_inventory",
    "EXPECTED_SYNTHETIC_CHECKS",
    "EXPECTED_SLOS",
):
    require(token in VERIFY, f"post-apply verification missing {token}")

for workflow, name in ((PLAN_WORKFLOW, "plan"), (APPLY_WORKFLOW, "apply")):
    require("validate_observability_enhancements.py" in workflow, f"Grafana {name} workflow must run enhancement validation")
    require(
        "test_migrate_linux_integration_alerts.py" in workflow,
        f"Grafana {name} workflow must test the Linux integration migration contract",
    )
    require("NUTSNEWS_EMAIL_TO" in workflow, f"Grafana {name} workflow must reuse the protected report recipient")
    require(
        'check.get("valid_status_codes", [200]) != [200]' in SYNTHETIC_INPUT_VALIDATOR,
        "shared input validator must reject a weakened synthetic status contract",
    )
    require("test_notification_canary.py" in workflow, f"Grafana {name} workflow must test the canary payload")
    require("test_deployment_annotation.py" in workflow, f"Grafana {name} workflow must test annotations")

for token in (
    "workflow_dispatch",
    'cron: "17 15 15 1,4,7,10 *"',
    "environment: production-vps",
    "fire-resolve",
    "attest-receipt",
    "exercise_notification_canary.py",
    "attest_notification_canary.py",
    "--hold-seconds 45",
    'echo "canary_id=github-${GITHUB_RUN_ID}"',
    "steps.guard.outputs.should_fire == 'true'",
):
    require(token in CANARY_WORKFLOW, f"quarterly notification canary workflow missing {token}")
for token in (
    "/api/alertmanager/grafana/api/v2/alerts",
    "/api/v1/provisioning/alert-rules",
    "NutsNewsNotificationCanary-",
    '"route": "operations-email"',
    '"severity": "critical"',
    '"ephemeral_rule_created": False',
    '"ephemeral_rule_deleted": False',
    '"vector(0)"',
):
    require(token in CANARY, f"notification canary implementation missing {token}")

# The fire/resolve phase must point at the exact runbook section and prove only
# Alertmanager state transitions. Receipt evidence belongs to a separate,
# no-refire human-attestation phase keyed to the same stable canary ID.
for token in (
    "#alert-delivery-and-notification-canary",
    "firing_state_observed",
    "resolved_state_observed",
):
    require(token in CANARY, f"notification canary evidence contract missing {token}")
for token in (
    'r"github-([1-9][0-9]*)"',
    "SHA256_REFERENCE",
    "HUMAN_CONFIRMATION_TEMPLATE",
    "normalized_sha256_reference",
    '"phase": "receipt_human_attested"',
    '"receipt_status": "human_attested"',
    '"attestation_method": "explicit_github_human"',
    '"human_confirmation_recorded": True',
    '"attested_by": actor',
    '"attestation_run_id": attestation_run_id',
    '"attestation_run_attempt": attestation_run_attempt',
    '"evidence_store_allowlisted": False',
    '"evidence_store_fetched": False',
    '"independent_verification_performed": False',
    '"firing_receipt_human_attested": True',
    '"resolved_receipt_human_attested": True',
    '"refired": False',
):
    require(token in CANARY_ATTESTATION, f"receipt attestation contract missing {token}")
for token in (
    "if: always()",
    'phase: "pending_receipt"',
    'receipt_status: "pending_receipt"',
    "receipt_attestation_required: true",
    "api_transition_failed",
    "grafana-notification-canary.json",
    "grafana-notification-receipt-evidence.json",
    "grafana-notification-receipt-attestation.json",
    "if-no-files-found: error",
    "retention-days: 90",
    "opaque sha256:<64-hex> references",
    "Never paste an evidence URL or path-embedded share token",
    "github.triggering_actor",
    '--github-run-id "$GITHUB_RUN_ID"',
    '--github-run-attempt "$GITHUB_RUN_ATTEMPT"',
    "receipt human-attested",
    "not independent delivery verification",
    "A rerun never refires the email pair",
):
    require(token in CANARY_WORKFLOW, f"notification canary workflow evidence is incomplete: {token}")
canary_id_line = next(
    (line for line in CANARY_WORKFLOW.splitlines() if "canary_id=github-" in line),
    "",
)
require("GITHUB_RUN_ATTEMPT" not in canary_id_line, "stable canary ID must survive workflow reruns")
attestation_job = CANARY_WORKFLOW.split("  attest-receipt:", maxsplit=1)[1]
require("exercise_notification_canary.py" not in attestation_job, "receipt attestation must not refire")
require("GRAFANA_SERVICE_ACCOUNT_TOKEN" not in attestation_job, "receipt attestation must not receive Alertmanager credentials")
for forbidden in (
    "api_transition_evidence_reference",
    "receipt_evidence_reference",
    "original_run_url",
    "receipt verified",
):
    require(
        forbidden not in attestation_job.lower(),
        f"human receipt attestation must not retain locator/verification wording: {forbidden}",
    )
require(
    "urllib" not in CANARY_ATTESTATION and "urlopen" not in CANARY_ATTESTATION,
    "human receipt attestation must not imply evidence retrieval without an allowlisted fetcher",
)
require("api_verified_pending_receipt" not in CANARY_WORKFLOW, "pending receipt is a successful intermediate phase, not an expected-red workflow state")
require(
    "## alert delivery and notification canary" in RUNBOOK.lower(),
    "notification canary runbook anchor target is missing",
)

# Every protected Grafana entry point must receive the live regional Synthetic
# Monitoring backend URL; the check provider is not the Grafana UI endpoint.
for workflow, name in (
    (PLAN_WORKFLOW, "plan"),
    (APPLY_WORKFLOW, "apply"),
    (FAILURE_WORKFLOW, "failure drill"),
):
    require(
        "NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL" in workflow,
        f"Grafana {name} workflow is missing the Synthetic Monitoring backend URL",
    )
require(
    "GRAFANA_SM_URL" in PLAN_WORKFLOW + APPLY_WORKFLOW + FAILURE_WORKFLOW,
    "Synthetic Monitoring provider/drill environment linkage is missing",
)

# Post-apply data checks must reject NaN/Inf/invalid samples and retain hard
# free-tier ceilings, rather than treating a result vector as inherently valid.
for token in (
    "math.isfinite(value)",
    "non_finite_sample_count",
    "invalid_sample_count",
    'len(usage[name].get("sample_values", [])) != usage[name].get(',
    'value >= 7000',
    "max_global_series_per_user denominator must be positive",
    "active-series ratio must be nonnegative",
):
    require(token in VERIFY, f"finite usage/series post-apply assertion missing {token}")
for token in (
    'synthetic_execution_estimate != 69120',
    "synthetic_execution_guardrail != SYNTHETIC_API_EXECUTION_CEILING_MONTHLY",
    "synthetic_execution_estimate >= synthetic_execution_guardrail",
    "synthetic_execution_major_threshold != 85000",
    "rollout_decision_enforcement_state is not True",
    "synthetic_major_acknowledgment_state is not True",
    "synthetic check must have exactly two current probe series",
    "synthetic check contains invalid or non-finite samples",
    'client.request("GET", "/api/v1/check")',
    "execution_estimate >= SYNTHETIC_API_EXECUTION_CEILING_MONTHLY",
    "timeout_seconds=780",
    "timestamp(probe_success",
    "exactly one current config_version",
):
    require(token in VERIFY, f"finite synthetic execution assertion missing {token}")
require(
    "69,120 API executions" in RUNBOOK
    and "69.12%" in RUNBOOK
    and "should remain `false`" in RUNBOOK,
    "runbook must document the resolved mixed synthetic cadence",
)

# Verify all eight worker targets dynamically. Target labels, worker-emitted
# deployment/ownership labels, and the durable host-owned cutover signal must
# agree before worker SLO alerting can be enabled.
for service in (
    "scheduler",
    "fetcher",
    "canonicalizer",
    "enrichment",
    "approval",
    "translation",
    "persistence",
    "publication",
):
    require(f'    "{service}",' in VERIFY, f"post-apply worker service inventory missing {service}")
for token in (
    'prometheus[name]["result_count"] != len(WORKER_SERVICES)',
    'observed_services != WORKER_SERVICES',
    "worker target/emitted expected_active mismatch",
    "worker target/protected host expected_active mismatch",
    "worker target/emitted deployment mode mismatch",
    "worker target/protected host deployment mode mismatch",
    "protected worker terminal SLO alert switch disagrees with host ownership",
):
    require(token in VERIFY, f"dynamic all-eight worker verification is incomplete: {token}")
for token in (
    '"worker_readiness_series"',
    '"worker_readiness_ok"',
    'DELIVERY_WORKER_SERVICES = WORKER_SERVICES - {"scheduler"}',
    '"worker_scheduler_cycle_histogram"',
    "must expose exactly six seeded outcomes for ",
    "each of the seven delivery services",
    'outcome="success"',
    "production-owned workers must report readiness outcome=ok",
    "def validate_worker_runtime_identity_rollout(",
    "shadow-runtime-identity-visible",
    "production-runtime-v1-required",
    "worker ownership requires deployment-info for all eight services in shadow",
    "worker ownership requires immutable build identity for all eight services",
):
    require(token in VERIFY, f"staged worker runtime acceptance is incomplete: {token}")
for token in (
    "nutsnews-worker-contracts`/`nutsnews-worker-runtime` 1.0",
    "worker endpoints to be up and fresh",
    "producer-initialized stage-event and fixed-bucket latency families",
    "validates the scheduler through its loop/cycle metrics",
    "shadow readiness is an explicit disabled state",
    "all eight services must emit deployment and immutable build identity",
):
    require(token in RUNBOOK, f"worker runtime staged-release dependency is undocumented: {token}")

worker_alerts = {alert["uid"]: alert for alert in WORKER_CATALOG["alerts"]}
content_telemetry_alert = worker_alerts[
    "nn-wu-feed-freshness-telemetry"
]
require(
    content_telemetry_alert["no_data_state"] == "Alerting"
    and "nutsnews_backend_content_coverage_available" in content_telemetry_alert["expr"]
    and "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds" in content_telemetry_alert["expr"],
    "feed-freshness SLO gating requires a separate fail-closed telemetry-unavailable alert",
)
host_gate_alerts = {
    "nn-wu-worker-scrape-missing",
    "nn-wu-rmq-queue-metrics-missing",
    "nn-wu-rmq-publish-metrics-missing",
    "nn-wu-scheduler-loop-stale",
    "nn-wu-rmq-no-consumers",
    "nn-wu-rmq-backlog-age",
    "nn-wu-rmq-pub-ack-gap",
    "nn-wu-rmq-unacked-growth",
    "nn-wu-rmq-dlq-nonempty",
    "nn-wu-rmq-retry-redelivery",
    "nn-wu-slo-stage-latency",
    "nn-wu-slo-retry-dlq-burn",
    "nn-wu-slo-publication-success",
}
for uid in host_gate_alerts:
    expression = worker_alerts[uid]["expr"]
    require(
        "nutsnews_backend_worker_uplift_expected_active" in expression,
        f"{uid} must use the durable host-owned production gate",
    )
    require(
        "nutsnews_worker_expected_active" not in expression,
        f"{uid} must not rely on a worker-owned gate that disappears with the scrape",
    )
for uid in (
    "nn-wu-rmq-backlog-age",
    "nn-wu-rmq-dlq-nonempty",
    "nn-wu-rmq-retry-redelivery",
):
    require(
        "or vector(0)) + (max(nutsnews_backend_rabbitmq_canary_failure_fixture"
        in worker_alerts[uid]["expr"],
        f"{uid} canary override must still fire when the normal shadow arm is empty",
    )
scrape_expression = worker_alerts["nn-wu-worker-scrape-missing"]["expr"]
require(
    "8 - (count(count by (service)" in scrape_expression
    and 'drill="worker-unavailable"' in scrape_expression,
    "worker scrape-loss rule must totalize all-eight absence and retain a bounded drill path",
)

# The backend fixture series is an exact, telemetry-only test seam. Readiness's
# filtering comparison intentionally yields 0 only when the fixture is active;
# with the fixture inactive, absent real telemetry remains governed by NoData.
backend_alerts = {alert["uid"]: alert for alert in BACKEND_CATALOG["alerts"]}
backend_host_alert = backend_alerts["nutsnews-backend-host-metrics-missing"]
require(
    "1 - min(up{" in backend_host_alert["expr"]
    and "max(absent(up{" in backend_host_alert["expr"]
    and 'instance="backend.nutsnews.com"' in backend_host_alert["expr"]
    and 'deployment_environment="production"' in backend_host_alert["expr"]
    and backend_host_alert["no_data_state"] == "Alerting",
    "backend host telemetry guardrail must detect an exact target that is down or absent",
)

relay_contract_alert = backend_alerts["nn-backend-sync-relay-contract"]
require(
    "nutsnews_backend_sync_relay_expected_active" in json.dumps(BACKEND_CATALOG)
    and '"backend_sync_relay_expected_active"' in VERIFY
    and "disabled sync relay must expose expected_active=0" in VERIFY
    and "configured sync relay must expose expected_active=1" in VERIFY,
    "sync-relay deployment-mode telemetry must gate disabled and configured verification",
)
for token in (
    "nutsnews_backend_sync_relay_status",
    "nutsnews_backend_sync_relay_(available|collector_fresh|healthy|lag_seconds|failed_table_count|last_success_age_seconds)",
    "abs(6 - (count(",
    "count by (__name__)",
    'status!="not_configured"',
    "count({__name__=~",
    "< 0",
    "> 1",
):
    require(token in relay_contract_alert["expr"], f"sync-relay telemetry contract missing {token}")
require(
    relay_contract_alert["no_data_state"] == "Alerting",
    "sync-relay telemetry contract must fail closed while threshold rules preserve disabled mode",
)
fixture_alerts = {
    "nn-backend-sync-relay-lag": "postgres-relay-lag",
    "nn-backend-api-not-ready": "backend-readiness-failed",
    "nn-backend-endpoint-unhealthy": "backend-readiness-failed",
}
for uid, drill_id in fixture_alerts.items():
    expression = backend_alerts[uid]["expr"]
    require(
        'job=\\"nutsnews-backend-host\\",instance=\\"backend.nutsnews.com\\",service_namespace=\\"nutsnews\\",service=\\"host\\",environment=\\"production\\",deployment_environment=\\"production\\",host=\\"backend.nutsnews.com\\"'
        in json.dumps(expression),
        f"{uid} fixture selector must carry the exact backend host identity",
    )
    require(f'drill=\\"{drill_id}\\"' in json.dumps(expression), f"{uid} is not linked to {drill_id}")
for uid in ("nn-backend-api-not-ready", "nn-backend-endpoint-unhealthy"):
    alert = backend_alerts[uid]
    require(
        "or ((1 -" in alert["expr"]
        and "== 0)" in alert["expr"]
        and alert["evaluator"] == "lt"
        and alert["threshold"] == 1,
        f"{uid} must force a zero only for the active readiness fixture",
    )
    require(
        "or vector(1)" not in alert["expr"],
        f"{uid} must preserve NoData when real API-readiness telemetry is missing",
    )

api_readiness_expression = backend_alerts["nn-backend-api-not-ready"]["expr"]
require(
    'nutsnews_backend_api_up{job="nutsnews-backend-api"} * on() nutsnews_backend_api_dependency_ready' in api_readiness_expression
    and 'nutsnews_backend_api_up{job="nutsnews-backend-api"} and on()' not in api_readiness_expression,
    "backend API readiness must combine process and PostgreSQL readiness values, not merely test series presence",
)


def backend_api_ready(api_up: int, postgresql_ready: int) -> int:
    return api_up * postgresql_ready


require(backend_api_ready(1, 1) == 1, "healthy API and PostgreSQL must be ready")
require(backend_api_ready(1, 0) == 0, "API up with PostgreSQL unready must alert")
require(backend_api_ready(0, 1) == 0, "API down with PostgreSQL ready must alert")

# The drill catalog, protected workflow, Grafana rule seams, and backend
# executor must be one executable eight-drill contract, not disconnected docs.
required_drills = {
    "alloy-stopped",
    "textfile-stale",
    "worker-unavailable",
    "rabbitmq-zero-consumer",
    "rabbitmq-growing-dlq",
    "postgres-relay-lag",
    "backend-readiness-failed",
    "synthetic-mismatch",
}
contract_drills = {item["id"]: item for item in FAILURE_CONTRACT["drills"]}
require(set(contract_drills) == required_drills, "failure-drill contract must contain exactly the eight accepted drills")
require(FAILURE_CONTRACT["default_mode"] == "dry-run", "failure drills must remain dry-run-first")
require(
    FAILURE_CONTRACT["artifact_retention_days"] == 90,
    "public-repository failure-drill artifacts must use GitHub's 90-day maximum",
)
for drill_id in required_drills:
    require(f"          - {drill_id}" in FAILURE_WORKFLOW, f"failure-drill workflow input missing {drill_id}")
for token in (
    "len(drills) != 8",
    "default_mode",
    "mutation_performed",
    "execute-grafana-failure-drill",
):
    require(token in FAILURE_RUNNER, f"failure-drill evidence runner missing {token}")
for token in (
    "environment: production-vps",
    "NUTSNEWS_BACKEND_OBSERVABILITY_DRILL_TOKEN",
    "backend-observability-failure-drills.yml",
    "production-backend",
    "scripts/validate_backend_drill_evidence.py",
    "--artifact-zip",
    "--artifact-digest",
    "timeout_seconds=1200",
    "backend-observability-failure-drill-evidence",
    "retention-days: 90",
):
    require(token in FAILURE_WORKFLOW, f"protected backend failure-drill linkage missing {token}")
for token in (
    "BACKEND_DURATION_SECONDS = 900",
    'members[0].filename != "evidence.json"',
    'value.get("duration_seconds") != BACKEND_DURATION_SECONDS',
    'evidence.get("duration_seconds") != BACKEND_DURATION_SECONDS',
):
    require(token in BACKEND_DRILL_EVIDENCE, f"backend failure-drill evidence validation missing {token}")
for token in (
    "GRAFANA_SM_URL",
    "canonical_readiness",
    "status",
    "body",
    "header",
    "restore",
):
    require(token in FAILURE_WORKFLOW + SYNTHETIC_DRILL, f"controlled synthetic mismatch drill missing {token}")

# Error-volume alerting must use the normalized indexed severity label. Text
# regex matching would regress both correctness and Loki query efficiency.
high_error_log_volume = LOCALS.split("    high_error_log_volume = {", 1)[1].split(
    "\n    }", 1
)[0]
require(
    'severity=~\\"error|critical\\"' in high_error_log_volume
    and "|~" not in high_error_log_volume,
    "high error log volume alert must use normalized severity labels",
)

# Loki's normalized six-label index, Grafana's service-name alias, and
# generated SLO rules/samples are enforced post-apply; high-cardinality
# request/correlation/article identifiers stay structured metadata.
for label in (
    "deployment_environment",
    "service",
    "service_version",
    "host",
    "source",
    "severity",
):
    require(f'    "{label}",' in VERIFY, f"post-apply Loki indexed-label contract missing {label}")
for token in (
    "def loki_series(",
    "/loki/api/v1/series?",
    "def validate_loki_indexed_labels(",
    "set(labels) - LOKI_ALLOWED_INDEXED_LABELS",
    "LOKI_ALLOWED_INDEXED_LABELS - set(labels)",
    'LOKI_PLATFORM_INDEXED_LABELS = {"service_name"}',
    'labels.get("service_name") != labels.get("service")',
    '"backend_sync_relay_logs"',
    'f"worker_{service}_logs"',
):
    require(token in VERIFY, f"strict Loki service/label verification missing {token}")
for token in (
    "/api/plugins/grafana-slo-app/resources/v1/slo",
    "/api/prometheus/grafana/api/v1/rules",
    'freeform.get("query") != spec["query"]',
    'destination.get("uid") != prometheus_uid',
    'read_only.get("provenance") != "terraform"',
    "generated_rule_slo_uuid(rule) == slo_uuid",
    "recording_rule_count",
    "generated fast/slow burn alerts",
    '"grafana_slo_sli_window"',
    '"grafana_slo_sli_1h"',
    '"grafana_slo_sli_1d"',
    '"grafana_slo_objective"',
    "recorded SLI is outside [0,1]",
    "recorded_samples_required",
    'recorded_sample_state = "dashboard-only-no-terminal-events"',
    "monotonic() >= deadline",
    "sleep(10)",
):
    require(token in VERIFY, f"Grafana SLO API/ruler/data verification missing {token}")

for token in (
    "def policy_matchers(",
    'expected_matcher = [("severity", "=~", severity)]',
    "if len(routes) != len(expected):",
    "route_receiver != CONTACT_POINT_NAME",
    "route.get(\"group_by\") != expected_group_by",
    "root_timings != expected_root_timings",
):
    require(token in VERIFY, f"exact notification-policy verification missing {token}")

require(len(BACKEND_CATALOG["alerts"]) == 20, "backend alert catalog count drifted")
require(len([panel for dashboard in BACKEND_CATALOG["dashboards"] for panel in dashboard["panels"]]) == 126, "backend panel catalog count drifted")
backend_catalog_text = json.dumps(BACKEND_CATALOG, ensure_ascii=False)
for token in (
    "pg_stat_bgwriter_checkpoints_timed_total",
    "pg_stat_checkpointer_num_timed_total",
    "pg_stat_activity_autovacuum_timestamp_seconds",
    "pg_stat_progress_vacuum_heap_blks_scanned",
    "deriv(pg_wal_size_bytes",
    "pg_replication_lag_seconds",
    "pg_replication_slots_active",
    "caddy_http_request_errors_total",
    "Disabled by configuration — no active health_uri",
    "label_replace(vector(-1)",
):
    require(token in backend_catalog_text, f"backend dashboard catalog missing emitted metric {token}")
postgres_operations = next(
    dashboard
    for dashboard in BACKEND_CATALOG["dashboards"]
    if dashboard["uid"] == "nutsnews-backend-postgres-operations"
)
postgres_panels = {panel["title"]: panel for panel in postgres_operations["panels"]}
autovacuum_panel = postgres_panels["Autovacuum Activity"]
require(
    'or on() label_replace(((0 * (max(pg_up{job="nutsnews-backend-postgres"}) == 1)) - 1)' in autovacuum_panel["expr"]
    and "Idle — no autovacuum or manual vacuum is active" in json.dumps(autovacuum_panel, ensure_ascii=False),
    "idle autovacuum must use a source-backed sentinel without masking exporter loss",
)
replication_panel = postgres_panels["Replication State And Lag"]
require(
    "nutsnews_backend_postgres_replication_lag_configured" in replication_panel["expr"]
    and 'replication_state", "disabled_by_configuration"' in replication_panel["expr"]
    and "Disabled by configuration — standalone primary" in json.dumps(replication_panel, ensure_ascii=False),
    "standalone PostgreSQL must expose an explicit replication-disabled state",
)
for token in (
    '"backend_postgres_checkpoint_counters"',
    '"backend_postgres_autovacuum_activity"',
    '"backend_postgres_wal_size"',
    '"backend_postgres_replication_state"',
    '"backend_caddy_upstream_errors"',
    '"backend_caddy_upstream_health_state"',
    '"backend_api_build_identity"',
    '"backend_worker_uplift_deployed_identity_available"',
    '"backend_worker_uplift_deployed_service_info"',
):
    require(token in VERIFY, f"post-apply representative query missing {token}")
require(
    '"activity", "idle"' in VERIFY
    and '"replication_state", "disabled_by_configuration"' in VERIFY,
    "post-apply PostgreSQL checks must accept only the explicit source-backed idle/disabled sentinels",
)
require(
    'and on() (max(up{job=\\"nutsnews-backend-caddy\\"}) == 1)'
    in backend_catalog_text,
    "Caddy error-rate zero must be derived only while Caddy and request telemetry are present",
)
require(
    'job=~\\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\\"'
    in LOCALS,
    "remote-write alert rules must independently retain both Alloy self-scrape identities",
)
require(
    '(1 - min(up{job=\\"integrations/nutsnews-vps-alloy\\",instance=\\"vps.nutsnews.com\\"' in LOCALS
    and '(1 - min(up{job=\\"nutsnews-backend-alloy\\",instance=\\"backend.nutsnews.com\\"' in LOCALS,
    "Alloy self-scrape alert must use stable host identities and detect down or absent targets independently",
)
for token in (
    'uid           = "nn-alloy-internal-metrics-missing"',
    "abs(2 - (count(count by (instance, job)",
    "prometheus_remote_storage_samples_pending",
    "prometheus_remote_storage_samples_failed_total",
    "loki_write_dropped_entries_total",
    "loki_write_batch_retries_total",
):
    require(token in LOCALS, f"two-host Alloy internal-family contract missing {token}")

backup_rule_text = LOCALS.split("    backup_verification_overdue = {", 1)[1].split("\n    }", 1)[0]
require(
    backup_rule_text.count("> bool 108000) + (max(") == 2,
    "backup overdue guardrail must add stale and negative-sentinel boolean arms",
)
require(
    "> bool 108000) or (max(" not in backup_rule_text,
    "backup overdue guardrail must not use set-or where a present false sample masks age=-1",
)


def backup_age_violation(age_seconds: int) -> int:
    return int(age_seconds > 108000) + int(age_seconds < 0)


require(backup_age_violation(-1) == 1, "never-successful backup sentinel must alert")
require(backup_age_violation(0) == 0, "fresh backup age must remain healthy")
require(backup_age_violation(108001) == 1, "backup age above 30h must alert")
require(
    'or on() label_replace(vector(-1), \\"upstream\\"' in backend_catalog_text,
    "Caddy upstream disabled sentinel must be a global-absence fallback",
)
require(
    'rate(pg_stat_database_xact_commit' in backend_catalog_text
    and ' + sum by (datname) (rate(pg_stat_database_xact_rollback' in backend_catalog_text,
    "PostgreSQL transaction panel must add commit and rollback rates",
)
require(
    'or sum(rate(pg_stat_checkpointer_num_timed_total' in backend_catalog_text
    and ')) + sum(rate(pg_stat_bgwriter_buffers_alloc_total' in backend_catalog_text,
    "PostgreSQL checkpoint panel must use version fallback before adding buffer allocations",
)
require("20 backend alert rules" in RUNBOOK, "post-apply runbook backend alert count is stale")

print("Grafana Cloud observability enhancement guardrails passed.")
