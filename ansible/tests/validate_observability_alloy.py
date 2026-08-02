#!/usr/bin/env python3
"""Validate Grafana Alloy installation guardrails."""

from __future__ import annotations

import json
import re
from pathlib import Path


TASKS = Path("ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
DEFAULTS = Path("ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
ALLOY_CONFIG = Path("ansible/roles/vps_service_foundation/templates/grafana-alloy.config.alloy.j2").read_text(
    encoding="utf-8"
)
ALLOY_DROPIN = Path("ansible/roles/vps_service_foundation/templates/grafana-alloy.service-dropin.conf.j2").read_text(
    encoding="utf-8"
)
CADDYFILE = Path("compose/caddy/Caddyfile").read_text(encoding="utf-8")
CADDYFILE_TEMPLATE = Path("ansible/roles/vps_service_foundation/templates/Caddyfile.j2").read_text(
    encoding="utf-8"
)
CADDY_COMPOSE = Path("compose/caddy/compose.yml").read_text(encoding="utf-8")
INVENTORY_HOST_VARS = Path("ansible/inventories/production/host_vars/vps.nutsnews.com.yml").read_text(
    encoding="utf-8"
)
TEXTFILE_EXPORTER = Path(
    "ansible/roles/vps_service_foundation/files/observability_textfile_exporter.py"
).read_text(encoding="utf-8")
TEXTFILE_SERVICE = Path(
    "ansible/roles/vps_service_foundation/templates/nutsnews-observability-textfile.service.j2"
).read_text(encoding="utf-8")
PROTECTED_APPLY = Path(".github/workflows/protected-ansible-apply.yml").read_text(encoding="utf-8")
AUTOMATED_ALLOY_DISPATCHERS = (
    Path(".github/workflows/nutsnews-premerge-production-vps-deploy.yml"),
    Path(".github/workflows/nutsnews-release-promotion.yml"),
    Path(".github/workflows/protected-nutsnews-rollback.yml"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def replace_first_capture(pattern: re.Pattern[str], value: str, replacement_value: str = "[redacted]") -> str:
    def replacement(match: re.Match[str]) -> str:
        start = match.start(1) - match.start()
        end = match.end(1) - match.start()
        return f"{match.group(0)[:start]}{replacement_value}{match.group(0)[end:]}"

    return pattern.sub(replacement, value)


for token in (
    "vps_service_foundation_grafana_alloy_enabled: false",
    "vps_service_foundation_grafana_alloy_disable_confirmed: false",
    "vps_service_foundation_grafana_alloy_install_repo: true",
    "vps_service_foundation_grafana_alloy_apt_repo_uri: https://apt.grafana.com",
    "vps_service_foundation_grafana_alloy_apt_repo_suite: stable",
    "vps_service_foundation_grafana_alloy_package: alloy",
    "vps_service_foundation_grafana_alloy_collect_docker: false",
    "vps_service_foundation_grafana_alloy_collect_docker_logs: true",
    "vps_service_foundation_grafana_alloy_docker_socket: unix:///var/run/docker.sock",
    "vps_service_foundation_grafana_alloy_docker_log_compose_projects:",
    "vps_service_foundation_grafana_alloy_ready_url: http://127.0.0.1:12345/-/ready",
    "vps_service_foundation_observability_production_readiness_url: https://www.nutsnews.com/readyz",
    "vps_service_foundation_grafana_alloy_caddy_metrics_address: 127.0.0.1:2019",
    'vps_service_foundation_grafana_alloy_containerd_permission_error_pattern: "containerd\\\\.sock: connect: permission denied"',
    'vps_service_foundation_grafana_alloy_file_permission_error_pattern: "failed to tail the file: open .*: permission denied"',
    "vps_service_foundation_backup_log_group: adm",
):
    require(token in DEFAULTS, f"Grafana Alloy defaults missing {token}.")

configure_repo = TASKS.find("- name: Configure Grafana apt repository")
refresh_cache = TASKS.find("- name: Refresh apt cache after configuring Grafana repository")
install_package = TASKS.find("- name: Install Grafana Alloy package")

require(configure_repo >= 0, "Grafana apt repository task is missing.")
require(refresh_cache >= 0, "Grafana apt cache refresh task is missing.")
require(install_package >= 0, "Grafana Alloy package install task is missing.")
require(
    configure_repo < refresh_cache < install_package,
    "Grafana apt cache must refresh after repository setup and before installing Alloy.",
)

refresh_block = TASKS[refresh_cache:install_package]
require("ansible.builtin.apt:" in refresh_block, "Grafana cache refresh must use the apt module.")
require("update_cache: true" in refresh_block, "Grafana cache refresh must update apt cache.")
require(
    "vps_service_foundation_grafana_alloy_install_repo | bool" in refresh_block,
    "Grafana cache refresh must be guarded by repository management flag.",
)

install_block = TASKS[install_package:TASKS.find("- name: Set Alloy supplementary groups", install_package)]
require("cache_valid_time:" not in install_block, "Alloy install must not skip cache refresh by age.")
require("update_cache:" not in install_block, "Alloy install must rely on the explicit post-repository refresh.")
require("prometheus.exporter.cadvisor" in ALLOY_CONFIG, "Alloy cAdvisor exporter block is missing.")
require(
    "{% if vps_service_foundation_grafana_alloy_collect_docker | bool %}" in ALLOY_CONFIG,
    "Alloy cAdvisor blocks must stay gated by the container metrics collection flag.",
)
require(
    "{% if vps_service_foundation_grafana_alloy_collect_docker_logs | bool %}" in ALLOY_CONFIG,
    "Alloy Docker log blocks must be gated by the Docker log collection flag.",
)
require(
    ALLOY_CONFIG.find("prometheus.exporter.cadvisor") < ALLOY_CONFIG.find(
        "{% if vps_service_foundation_grafana_alloy_collect_docker_logs | bool %}"
    ),
    "cAdvisor must not move under the Docker log collection gate.",
)
for token in (
    'service_version = sys.env("NUTSNEWS_ALLOY_SERVICE_VERSION")',
    'host                   = sys.env("NUTSNEWS_ALLOY_HOSTNAME")',
    'source          = "journal"',
    'source             = "auth"',
    'source             = "file"',
    'target_label = "source"',
    'stage.json',
    'stage.structured_metadata',
    'stage.label_keep',
    'drop_counter_reason = "file_debug"',
    'drop_counter_reason = "docker_debug"',
    'drop_counter_reason = "docker_line_too_large"',
    'max_streams = 500',
    'job_name        = "integrations/unix"',
    'job_name        = "integrations/nutsnews-vps-alloy"',
    'job_name        = "integrations/nutsnews-vps-caddy"',
    'replacement   = "host-exporter"',
    'replacement  = "alloy"',
    'target_label  = "severity"',
):
    require(token in ALLOY_CONFIG, f"Structured Alloy log guardrail missing {token}.")
require(
    ALLOY_CONFIG.count('selector            = "{severity=\\"debug\\"}"') == 3,
    "Journal, file, and Docker pipelines must drop only normalized debug/trace entries.",
)
require(
    'expression          = "(?i)\\\\b(debug|trace)\\\\b"' not in ALLOY_CONFIG,
    "Log-message text must not determine whether a non-debug entry is dropped.",
)
require(
    'source_labels = ["service"]\n    regex         = "^$"\n    target_label  = "service"\n'
    '    replacement   = "host-exporter"' in ALLOY_CONFIG,
    "Host scrape identity must preserve service labels emitted by textfile collectors.",
)
require(
    'job_name        = "integrations/unix"' in ALLOY_CONFIG,
    "Host and textfile metrics must publish the embedded Unix exporter job identity.",
)
for token in (
    "prometheus_remote_storage_",
    "samples_(pending|failed_total|retries_total|retried_total|total)",
    "loki_write_",
    "dropped_entries_total",
    "batch_retries_total",
    "caddy_rate_limit_.*",
    "caddy_http_(request|response)_size_bytes_.*",
    "caddy_http_response_duration_seconds_.*",
):
    require(token in ALLOY_CONFIG, f"Alloy active-series control missing {token}.")
require(
    "caddy_http_request_duration_seconds_.*" not in ALLOY_CONFIG,
    "Caddy request-duration RED histogram must remain in remote write.",
)
for scrape_name, expected_address in (
    ("alloy", '127.0.0.1:12345'),
    ("caddy", "{{ vps_service_foundation_grafana_alloy_caddy_metrics_address }}"),
):
    scrape_start = ALLOY_CONFIG.index(f'prometheus.scrape "{scrape_name}"')
    scrape_end = ALLOY_CONFIG.index("\nprometheus.relabel", scrape_start)
    scrape_block = ALLOY_CONFIG[scrape_start:scrape_end]
    require(
        f'__address__ = "{expected_address}"' in scrape_block,
        f"{scrape_name} self-scrape must retain its private loopback address.",
    )
    require(
        re.search(r'instance\s*=\s*sys\.env\("NUTSNEWS_ALLOY_HOSTNAME"\)', scrape_block) is not None,
        f"{scrape_name} self-scrape must override the loopback instance with the host identity.",
    )
for project in ("nutsnews-service-foundation", "nutsnews-app"):
    require(project in DEFAULTS, f"Alloy Docker log discovery defaults must include {project}.")
require(
    "status               = \"status\"" in ALLOY_CONFIG and "uri                  = \"request.uri\"" in ALLOY_CONFIG,
    "Docker/Caddy JSON parsing must extract status and URI as structured metadata.",
)
normalized_loki_labels = (
    'values = ["deployment_environment", "service", "service_version", "host", "source", "severity"]'
)
require(
    ALLOY_CONFIG.count(normalized_loki_labels) == 3,
    "Every Loki pipeline must keep only the normalized bounded label contract.",
)
require(
    ALLOY_CONFIG.count('by_label_name       = "service"') == 3,
    "Every Loki pipeline must rate-limit independently by its bounded service label.",
)
require(
    "|name|" not in ALLOY_CONFIG and "|name)" not in ALLOY_CONFIG,
    "Shared metric relabeling must preserve node_exporter systemd unit names.",
)
for metadata_name in (
    "request_id",
    "message_id",
    "correlation_id",
    "traceparent",
    "trace_id",
    "article_id",
    "feed_id",
    "idempotency_key",
):
    require(metadata_name in ALLOY_CONFIG, f"Loki structured metadata missing {metadata_name}.")
for unit in (
    "nutsnews-infra-health.service",
    "nutsnews-docker-cleanup.timer",
    "nutsnews-staging-auto-idle.timer",
    "nutsnews-observability-textfile.timer",
):
    require(unit in DEFAULTS, f"Bounded journal unit inventory missing {unit}.")
for backend_only in (
    "nutsnews-worker-db-api.service",
    "nutsnews-supabase-sync-relay.service",
    "postgresql@18-main.service",
    "nutsnews-rabbitmq-canary.service",
    "/worker-api/",
    "/sync-relay/",
):
    require(backend_only not in DEFAULTS + ALLOY_CONFIG, f"Backend-only source leaked into VPS Alloy: {backend_only}.")
for service in ("backup", "caddy", "infra-health", "ops-portal", "docker-cleanup", "staging-auto-idle"):
    require(f'service            = "{service}"' in ALLOY_CONFIG, f"Fixed file log source missing service {service}.")
require(
    'loki.source.journal "unit_{{ journal_unit | regex_replace(\'[^A-Za-z0-9_]\', \'_\') }}"' in ALLOY_CONFIG,
    "Journal source identities must be stable across inventory reordering.",
)
require('loki.source.journal "unit_{{ loop.index0 }}"' not in ALLOY_CONFIG, "Journal source identities cannot use indexes.")
require(
    "{% if journal_unit.startswith('nutsnews-') %}" in ALLOY_CONFIG
    and 'service_version = "unknown"' in ALLOY_CONFIG,
    "External systemd units must not inherit the NutsNews infrastructure revision.",
)
require(
    "map('regex_replace', '[^A-Za-z0-9_]', '_')" in TASKS,
    "Stable sanitized journal component identities must be collision-checked.",
)
require('service  = "log_service"' not in ALLOY_CONFIG, "JSON log fields must not become unbounded service labels.")
require(
    ALLOY_CONFIG.count('template = "{{ $s := .Value }}') == 2,
    "File and Docker severities must be normalized to a fixed taxonomy.",
)
require(
    'message              = ""' not in ALLOY_CONFIG and 'msg                  = ""' not in ALLOY_CONFIG,
    "Full log messages must not be copied into structured metadata before redaction.",
)
bearer_expression = 'expression = "(?i)bearer\\\\s+([A-Za-z0-9._~+/=-]+)"'
array_secret_expression = (
    'expression = "(?i)(?:password|passwd|token|secret|authorization|proxy[_-]?authorization|credential|'
    'api[_-]?key|x[_-]?api[_-]?key)[\\\"\']?\\\\s*[:=]\\\\s*(\\\\[[^\\\\]]*\\\\])"'
)
quoted_secret_expression = (
    'expression = "(?i)(?:password|passwd|token|secret|authorization|proxy[_-]?authorization|credential|'
    'api[_-]?key|x[_-]?api[_-]?key)[\\\"\']?\\\\s*[:=]\\\\s*[\\\"\']([^\\\"\']*)"'
)
authorization_scheme_expression = (
    'expression = "(?i)(?:authorization|proxy[_-]?authorization)[\\\"\']?\\\\s*[:=]\\\\s*'
    '(?:basic|digest|bearer|token)\\\\s+([^\\\\s,}\\\\]]+)"'
)
require(ALLOY_CONFIG.count(array_secret_expression) == 3, "Every Loki pipeline must redact credential arrays.")
require(ALLOY_CONFIG.count(quoted_secret_expression) == 3, "Every Loki pipeline must redact full quoted secret values.")
require(
    ALLOY_CONFIG.count(authorization_scheme_expression) == 3,
    "Every Loki pipeline must redact unquoted authorization schemes.",
)
for pipeline_name, next_pipeline in (("journal", "files"), ("files", "docker"), ("docker", None)):
    start = ALLOY_CONFIG.index(f'loki.process "{pipeline_name}"')
    end = ALLOY_CONFIG.index(f'loki.process "{next_pipeline}"', start) if next_pipeline else len(ALLOY_CONFIG)
    pipeline = ALLOY_CONFIG[start:end]
    require(
        pipeline.index(bearer_expression)
        < pipeline.index(array_secret_expression)
        < pipeline.index(quoted_secret_expression)
        < pipeline.index(authorization_scheme_expression),
        f"{pipeline_name} must redact Bearer, array, quoted, then unquoted authorization credentials.",
    )
bearer_pattern = re.compile(r"(?i)bearer\s+([A-Za-z0-9._~+/=-]+)")
array_secret_pattern = re.compile(
    r"(?i)(?:password|passwd|token|secret|authorization|proxy[_-]?authorization|credential|"
    r"api[_-]?key|x[_-]?api[_-]?key)[\"']?\s*[:=]\s*(\[[^\]]*\])"
)
quoted_secret_pattern = re.compile(
    r"(?i)(?:password|passwd|token|secret|authorization|proxy[_-]?authorization|credential|"
    r"api[_-]?key|x[_-]?api[_-]?key)[\"']?\s*[:=]\s*[\"']([^\"']*)"
)
authorization_scheme_pattern = re.compile(
    r"(?i)(?:authorization|proxy[_-]?authorization)[\"']?\s*[:=]\s*"
    r"(?:basic|digest|bearer|token)\s+([^\s,}\]]+)"
)
for fixture, secret, is_json in (
    ('{"authorization":"Bearer abc.def"}', "abc.def", True),
    ('{"api_key":"s3cr3t"}', "s3cr3t", True),
    ('password="correct horse battery staple"', "correct horse battery staple", False),
    ('{"headers":{"x-api-key":["first-secret","second-secret"]}}', "first-secret", True),
    ('{"headers":{"x-api-key":["first-secret","second-secret"]}}', "second-secret", True),
    ("authorization: Basic basic-secret", "basic-secret", False),
):
    redacted = replace_first_capture(bearer_pattern, fixture)
    redacted = replace_first_capture(array_secret_pattern, redacted, '["[redacted]"]')
    redacted = replace_first_capture(quoted_secret_pattern, redacted)
    redacted = replace_first_capture(authorization_scheme_pattern, redacted)
    require(secret not in redacted, f"Loki redaction fixture leaked {secret}.")
    if is_json:
        json.loads(redacted)
loki_write_block = ALLOY_CONFIG[ALLOY_CONFIG.index('loki.write "grafana_cloud"'):ALLOY_CONFIG.index("prometheus.exporter.unix")]
require(
    "service_version" not in loki_write_block,
    "Loki must use truthful per-source service versions, not one global infra revision.",
)
require("append: false" in TASKS, "Alloy supplementary groups must be reconciled to avoid stale Docker access.")
require("Ensure Alloy Docker telemetry group exists" in TASKS, "Alloy Docker telemetry group must be explicit.")
require(
    "vps_service_foundation_grafana_alloy_docker_groups" in TASKS,
    "Alloy Docker group membership must be added only when Docker telemetry is enabled.",
)
require(
    "vps_service_foundation_grafana_alloy_collect_docker_logs | bool" in TASKS,
    "Alloy Docker group membership must account for Docker log collection.",
)
require("Validate Grafana Alloy readiness endpoint" in TASKS, "Alloy readiness validation is missing.")
require(
    "containerd socket permission errors" in TASKS and "journalctl" in TASKS,
    "Alloy journal validation for containerd socket permission errors is missing.",
)
require(
    "file log permission errors" in TASKS and "vps_service_foundation_grafana_alloy_file_permission_error_pattern" in TASKS,
    "Alloy journal validation for file log permission errors is missing.",
)
disabled_reconcile = TASKS.find("- name: Reconcile disabled Grafana Alloy observability agent")
enabled_service = TASKS.find("- name: Enable Grafana Alloy service")
validation_start = TASKS.find("- name: Capture Grafana Alloy post-apply validation start")
require(disabled_reconcile >= 0, "Disabled Alloy reconciliation block is missing.")
require(
    "Require explicit confirmation before disabling Grafana Alloy" in TASKS
    and "vps_service_foundation_grafana_alloy_disable_confirmed | bool" in TASKS,
    "Disabled Alloy reconciliation must require an explicit role-level confirmation.",
)
require(
    TASKS.index("- name: Require explicit confirmation before disabling Grafana Alloy")
    < TASKS.index("- name: Install service foundation packages"),
    "Alloy disable confirmation must fail before any role mutation, including in check mode.",
)
require(
    enabled_service < disabled_reconcile < validation_start,
    "Disabled Alloy reconciliation must run after enabled management and before post-apply validation.",
)
disabled_block = TASKS[disabled_reconcile:validation_start]
for token in (
    "not (vps_service_foundation_grafana_alloy_enabled | bool)",
    "ansible.builtin.service_facts:",
    "Stop, disable, and mask Grafana Alloy service when disabled",
    "enabled: false",
    "masked: true",
    "state: stopped",
    "Remove disabled Grafana Alloy supplementary access",
    'groups: ""',
    "vps_service_foundation_grafana_alloy_env_file",
    "vps_service_foundation_grafana_alloy_config_file",
    "vps_service_foundation_grafana_alloy_systemd_dropin_file",
    "vps_service_foundation_observability_textfile_service",
    "vps_service_foundation_observability_textfile_timer",
    "Reload systemd after disabled Grafana Alloy artifact cleanup",
    "Clear stale disabled Grafana Alloy textfile unit failures",
    "reset-failed",
):
    require(token in disabled_block, f"Disabled Alloy reconciliation missing {token}.")
require("masked: false" in TASKS[enabled_service:disabled_reconcile], "Enabled Alloy management must unmask Alloy for rollback.")
require(
    "Allow observability agent to read encrypted VPS backup logs" in TASKS,
    "Existing backup logs must be reconciled for Alloy read access.",
)
require(
    "Find existing Docker cleanup log files" in TASKS
    and "Allow observability agent to read existing Docker cleanup logs" in TASKS
    and "vps_service_foundation_docker_cleanup_log_files.files" in TASKS,
    "Existing Docker cleanup logs must be reconciled for Alloy read access.",
)
BACKUP_SERVICE = Path("ansible/roles/vps_service_foundation/templates/nutsnews-restic-backup.service.j2").read_text(
    encoding="utf-8"
)
VERIFY_SERVICE = Path("ansible/roles/vps_service_foundation/templates/nutsnews-restic-verify.service.j2").read_text(
    encoding="utf-8"
)
DOCKER_CLEANUP_SERVICE = Path(
    "ansible/roles/vps_service_foundation/templates/nutsnews-docker-cleanup.service.j2"
).read_text(encoding="utf-8")
for service_name, service_text in (
    ("backup", BACKUP_SERVICE),
    ("verify", VERIFY_SERVICE),
    ("docker cleanup", DOCKER_CLEANUP_SERVICE),
):
    require(
        "Group={{ vps_service_foundation_backup_log_group }}" in service_text,
        f"{service_name} service must write logs with the observability log group.",
    )
    require("UMask=0027" in service_text, f"{service_name} service must preserve group-read logs.")
require("User=root" not in ALLOY_DROPIN, "Alloy drop-in must not run Alloy as root.")
require(CADDYFILE.count("format filter {") == 4, "Every production Caddy access log must be filtered.")
require(CADDYFILE.count("wrap json") == 4, "Every production Caddy access log block must emit JSON.")
require(CADDYFILE_TEMPLATE.count("format filter {") == 5, "Production and staging Caddy logs must be filtered.")
require(CADDYFILE_TEMPLATE.count("wrap json") == 5, "Production and staging Caddy logs must remain JSON.")
for caddy_config, expected_log_blocks in ((CADDYFILE, 4), (CADDYFILE_TEMPLATE, 5)):
    require("\n  metrics\n" in caddy_config, "Caddy native request metrics must be enabled globally.")
    require("http://:2019 {\n  metrics /metrics\n}" in caddy_config, "Caddy must expose metrics privately.")
    for protected_field in (
        'request>uri regexp "\\\\?.*$" ""',
        "request>remote_ip delete",
        "request>client_ip delete",
        "request>headers>Cookie delete",
        "request>headers>Authorization delete",
        "request>headers>Proxy-Authorization delete",
        "request>headers>X-Api-Key delete",
        "request>headers>Cf-Access-Authenticated-User-Email delete",
        "request>headers>Cf-Access-Jwt-Assertion delete",
        "request>headers>Cf-Access-Client-Id delete",
        "request>headers>Cf-Access-Client-Secret delete",
        "request>headers>Cf-Connecting-Ip delete",
        "request>headers>X-Forwarded-For delete",
        "request>headers>X-Real-Ip delete",
        "request>headers>Forwarded delete",
        "request>headers>Referer delete",
        "resp_headers>Set-Cookie delete",
        "resp_headers>Location delete",
    ):
        require(
            caddy_config.count(protected_field) == expected_log_blocks,
            f"Every Caddy access log must protect {protected_field}.",
        )
require(
    '"127.0.0.1:2019:2019/tcp"' in CADDY_COMPOSE,
    "Caddy metrics must be published on host loopback only.",
)
require(
    "vps_service_foundation_grafana_alloy_enabled: true" in INVENTORY_HOST_VARS,
    "Production inventory must persist Alloy as desired state.",
)
require(
    "enable_grafana_alloy:\n        description: Keep production Grafana Alloy telemetry enabled.\n"
    '        required: true\n        default: "true"' in PROTECTED_APPLY
    and "CONFIRM_DISABLE_GRAFANA_ALLOY: ${{ inputs.confirm_disable_grafana_alloy }}" in PROTECTED_APPLY
    and '== "disable-grafana-alloy"' in PROTECTED_APPLY
    and '"vps_service_foundation_grafana_alloy_disable_confirmed": grafana_alloy_disable_confirmed' in PROTECTED_APPLY,
    "Protected apply must default Alloy on and require typed disable confirmation.",
)
for workflow_path in AUTOMATED_ALLOY_DISPATCHERS:
    workflow = workflow_path.read_text(encoding="utf-8")
    require(
        "--field enable_grafana_alloy=true" in workflow,
        f"Automated production dispatcher must preserve Alloy desired state: {workflow_path}.",
    )
require(
    "ConditionPathExists=" not in TEXTFILE_SERVICE,
    "Textfile collection must run and overwrite stale output even when portal status is absent.",
)
for token in (
    'Environment="HOME=/tmp"',
    'Environment="DOCKER_CONFIG=/tmp/docker"',
    'Environment="DOCKER_HOST={{ vps_service_foundation_grafana_alloy_docker_socket }}"',
    'Environment="XDG_CACHE_HOME=/tmp/cache"',
    'Environment="NUTSNEWS_PRODUCTION_READINESS_URL={{ vps_service_foundation_observability_production_readiness_url }}"',
    'Environment="NUTSNEWS_DEPLOYED_INFRA_COMMIT_FILE={{ vps_service_foundation_deployed_infra_commit_file }}"',
):
    require(token in TEXTFILE_SERVICE, f"Textfile Docker collection is not pinned locally: {token}.")
require(
    "NUTSNEWS_PRODUCTION_OWNERSHIP" not in TEXTFILE_SERVICE
    and "vps_service_foundation_observability_production_ownership:" not in DEFAULTS,
    "Production ownership must not be inferred from static Ansible desired-state labels.",
)
for token in (
    'CANONICAL_PRODUCTION_READINESS_URL = "https://www.nutsnews.com/readyz"',
    'PRODUCTION_WEB_TARGETS = {"production-vps", "vercel-production"}',
    'PRODUCTION_DATABASE_PROVIDERS = {"supabase_primary", "backend_postgres_primary"}',
    'payload.get("ready") is not True',
    'payload.get("service") != "nutsnews-web"',
    'response.headers.get("X-NutsNews-Deployment-Target") != web_target',
    'response.headers.get("X-NutsNews-Database-Provider-Mode") != database_provider',
    'response.headers.get("X-NutsNews-Source-Commit") != web_revision',
    "PRODUCTION_READINESS_MAX_BYTES + 1",
    "DEPLOYED_INFRA_COMMIT_FILE.read_text",
):
    require(token in TEXTFILE_EXPORTER, f"Canonical production ownership validation is missing {token}.")
require(
    "Validate private Caddy metrics endpoint" in TASKS
    and "caddy_http_requests_total" in TASKS
    and "caddy_http_request_duration_seconds_" in TASKS,
    "Post-start Caddy metrics validation must assert native RED families.",
)
require(
    "Refresh NutsNews observability textfile metrics" in TASKS
    and 'name: "{{ vps_service_foundation_observability_textfile_service }}"' in TASKS,
    "Apply-time textfile refresh must use the rendered systemd environment.",
)
apply_marker = TASKS.find("- name: Write deployed infrastructure commit marker")
ownership_refresh = TASKS.find("- name: Refresh production ownership telemetry after apply marker update")
require(
    apply_marker >= 0
    and ownership_refresh > apply_marker
    and "vps_service_foundation_apply_metadata_enabled | bool" in TASKS[ownership_refresh : ownership_refresh + 600]
    and "vps_service_foundation_grafana_alloy_enabled | bool" in TASKS[ownership_refresh : ownership_refresh + 600],
    "Production ownership telemetry must refresh after the authoritative infrastructure receipt changes.",
)
for token in (
    "nutsnews_observability_textfile_collector_success",
    "nutsnews_docker_stats_available",
    "nutsnews_docker_container_state_available",
    "nutsnews_docker_container_cpu_percent",
    "nutsnews_docker_container_memory_used_bytes",
    "nutsnews_alloy_ready",
    "nutsnews_caddy_tls_certificate_expiry_seconds",
    "nutsnews_production_ownership_info",
    "nutsnews_production_ownership_last_success_timestamp_seconds",
    "nutsnews_backup_status_available",
    "nutsnews_app_status_available",
    "nutsnews_resource_status_available",
):
    require(token in TEXTFILE_EXPORTER, f"Textfile exporter missing {token}.")

print("Grafana Alloy guardrails passed.")
