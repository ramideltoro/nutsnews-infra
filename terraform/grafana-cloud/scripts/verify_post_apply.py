#!/usr/bin/env python3
"""Verify OpenTofu-managed Grafana Cloud resources after protected apply."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_CATALOG = ROOT / "catalog" / "backend-observability.json"
WORKER_UPLIFT_CATALOG = ROOT / "catalog" / "worker-uplift-rabbitmq-alerts.json"
EXTERNAL_RULE_CATALOG = ROOT / "catalog" / "non-terraform-alert-rules.json"
LINUX_ALERT_REPLACEMENT_CATALOG = (
    ROOT / "catalog" / "linux-integration-alert-replacements.json"
)

CONTACT_POINT_NAME = "NutsNews operations email"
REQUIRED_ALERT_LABELS = {
    "deployment_environment",
    "owner",
    "route",
    "service",
    "severity",
}
REQUIRED_ALERT_ANNOTATIONS = {"dashboard_url", "runbook_url", "summary"}
EXPECTED_SYNTHETIC_CHECKS = {
    "canonical_articles_api",
    "canonical_homepage",
    "canonical_readiness",
    "vercel_secondary_readiness",
    "vps_readiness",
}
SYNTHETIC_API_EXECUTION_CEILING_MONTHLY = 90_000
SYNTHETIC_MONTH_MILLISECONDS = 30 * 24 * 60 * 60 * 1000
EXPECTED_SLOS = {
    "api_latency",
    "feed_freshness",
    "public_availability",
    "worker_terminal_success",
}

GRAFANA_OBSERVABILITY_RUNBOOK_URL = (
    "https://github.com/ramideltoro/nutsnews-infra/blob/main/"
    "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md"
)
GRAFANA_SLO_FOLDER_UID = "nutsnews-observability"
GRAFANA_SLO_RECORDED_METRICS = (
    "grafana_slo_sli_window",
    "grafana_slo_sli_1h",
    "grafana_slo_sli_1d",
    "grafana_slo_objective",
)

EXPECTED_SLO_SPECS = {
    "public_availability": {
        "name": "NutsNews public availability",
        "description": "Canonical homepage availability from two independent public probes.",
        "objective": 0.995,
        "service": "web",
        "dashboard_url": "/d/nutsnews-synthetic-uptime-api-checks",
        "alerting_enabled": True,
        "query": (
            'sum(sum_over_time(probe_success{job="canonical_homepage"}[$__interval])) '
            '/ sum(count_over_time(probe_success{job="canonical_homepage"}[$__interval]))'
        ),
    },
    "api_latency": {
        "name": "NutsNews API latency",
        "description": (
            "At least 95% of successful read-only article API checks complete within "
            "750 milliseconds; failed checks are availability failures and are excluded "
            "from this latency denominator."
        ),
        "objective": 0.95,
        "service": "web-api",
        "dashboard_url": "/d/nutsnews-synthetic-uptime-api-checks",
        "alerting_enabled": True,
        "query": (
            '(sum(count_over_time(((probe_duration_seconds{job="canonical_articles_api"} '
            '<= 0.75) and on(job, instance, probe, config_version) '
            '(probe_success{job="canonical_articles_api"} == 1))[$__interval:])) or '
            '0 * sum(count_over_time((probe_success{job="canonical_articles_api"} == 1)'
            '[$__interval:]))) / sum(count_over_time('
            '(probe_success{job="canonical_articles_api"} == 1)[$__interval:]))'
        ),
    },
    "feed_freshness": {
        "name": "NutsNews feed freshness",
        "description": (
            "At least 99% of valid durable feed-freshness observations report published "
            "content no more than 15 minutes old, independent of the shadow worker path."
        ),
        "objective": 0.99,
        "service": "publication",
        "dashboard_url": "/d/nutsnews-worker-uplift-slos",
        "alerting_enabled": True,
        "query": (
            '(sum(count_over_time(((max(nutsnews_backend_public_feed_snapshot_newest_content_age_seconds'
            '{job="nutsnews-backend-host",deployment_environment="production",'
            'instance="backend.nutsnews.com"}) <= 900) and on() '
            '(max(nutsnews_backend_public_feed_snapshot_newest_content_age_seconds'
            '{job="nutsnews-backend-host",deployment_environment="production",'
            'instance="backend.nutsnews.com"}) >= 0) and on() '
            '(max(nutsnews_backend_content_coverage_available{job="nutsnews-backend-host",'
            'deployment_environment="production",instance="backend.nutsnews.com"}) == 1))'
            '[$__interval:])) or 0 * sum(count_over_time(((max('
            'nutsnews_backend_public_feed_snapshot_newest_content_age_seconds'
            '{job="nutsnews-backend-host",deployment_environment="production",'
            'instance="backend.nutsnews.com"}) >= 0) and on() '
            '(max(nutsnews_backend_content_coverage_available{job="nutsnews-backend-host",'
            'deployment_environment="production",instance="backend.nutsnews.com"}) == 1))'
            '[$__interval:]))) / sum(count_over_time(((max('
            'nutsnews_backend_public_feed_snapshot_newest_content_age_seconds'
            '{job="nutsnews-backend-host",deployment_environment="production",'
            'instance="backend.nutsnews.com"}) >= 0) and on() '
            '(max(nutsnews_backend_content_coverage_available{job="nutsnews-backend-host",'
            'deployment_environment="production",instance="backend.nutsnews.com"}) == 1))'
            '[$__interval:]))'
        ),
    },
    "worker_terminal_success": {
        "name": "NutsNews worker terminal success",
        "description": (
            "Terminal worker events complete successfully; generated burn alerts remain "
            "disabled while worker uplift is shadow-only."
        ),
        "objective": 0.99,
        "service": "worker",
        "dashboard_url": "/d/nutsnews-worker-uplift-slos",
        "alerting_enabled": False,
        "query": (
            'sum(rate(nutsnews_worker_uplift_stage_events_total{job="nutsnews-worker-uplift",'
            'instance="backend.nutsnews.com",service_namespace="nutsnews",'
            'host="backend.nutsnews.com",environment="production",'
            'deployment_environment="production",service=~"fetcher|canonicalizer|enrichment|'
            'approval|translation|persistence|publication",outcome=~"success|duplicate"}[$__rate_interval])) '
            '/ sum(rate(nutsnews_worker_uplift_stage_events_total{job="nutsnews-worker-uplift",'
            'instance="backend.nutsnews.com",service_namespace="nutsnews",'
            'host="backend.nutsnews.com",environment="production",'
            'deployment_environment="production",service=~"fetcher|canonicalizer|enrichment|'
            'approval|translation|persistence|publication",'
            'outcome=~"success|duplicate|invalid|failure|dlq"}[$__rate_interval]))'
        ),
    },
}

WORKER_QUEUE_STAGES = (
    "fetch",
    "canonicalization",
    "enrichment",
    "approval",
    "translation",
    "persistence",
    "publication",
)
EXPECTED_WORKER_MAIN_QUEUES = {
    f"nutsnews.worker.{stage}.v1" for stage in WORKER_QUEUE_STAGES
}
EXPECTED_WORKER_QUEUES = {
    *EXPECTED_WORKER_MAIN_QUEUES,
    *(f"nutsnews.worker.{stage}.v1.retry-{delay}" for stage in WORKER_QUEUE_STAGES for delay in ("30s", "5m", "30m")),
    *(f"nutsnews.worker.{stage}.v1.dlq" for stage in WORKER_QUEUE_STAGES),
}
WORKER_QUEUE_SELECTOR = (
    r'nutsnews[.]worker[.](fetch|canonicalization|enrichment|approval|translation|persistence|publication)'
    r'[.]v1([.]retry-(30s|5m|30m)|[.]dlq)?'
)
WORKER_MAIN_QUEUE_SELECTOR = (
    r'nutsnews[.]worker[.](fetch|canonicalization|enrichment|approval|translation|persistence|publication)[.]v1'
)
RABBITMQ_QUEUE_FAMILY_EXPECTATIONS = {
    "backend_rabbitmq_queue_messages": EXPECTED_WORKER_QUEUES,
    "backend_rabbitmq_queue_ready": EXPECTED_WORKER_QUEUES,
    "backend_rabbitmq_queue_unacked": EXPECTED_WORKER_QUEUES,
    "backend_rabbitmq_queue_consumers": EXPECTED_WORKER_MAIN_QUEUES,
    "backend_rabbitmq_queue_acked": EXPECTED_WORKER_QUEUES,
    "backend_rabbitmq_queue_delivered": EXPECTED_WORKER_QUEUES,
    "backend_rabbitmq_queue_redelivered": EXPECTED_WORKER_QUEUES,
}

VPS_DASHBOARD_UIDS = {
    "nutsnews-vps-overview",
    "nutsnews-logs-overview",
    "nutsnews-cpu-load-processes",
    "nutsnews-memory-swap",
    "nutsnews-disk-filesystem-io",
    "nutsnews-network-caddy-edge",
    "nutsnews-docker-compose-containers",
    "nutsnews-systemd-services-timers",
    "nutsnews-logs-security-auth",
    "nutsnews-backups-restore-verification",
    "nutsnews-ops-portal-reporting",
    "nutsnews-application-service-health",
    "nutsnews-synthetic-uptime-api-checks",
    "nutsnews-grafana-cloud-usage-quota",
    "nutsnews-production-ownership",
}

VPS_ALERT_RULE_GROUPS = {
    ("nutsnews-observability", "NutsNews Grafana Cloud quota guardrails"),
    ("nutsnews-observability", "NutsNews log pipeline health"),
    (
        "nutsnews-observability",
        "NutsNews Linux integration alert replacements",
    ),
}

LINUX_ALERT_REPLACEMENTS = json.loads(
    LINUX_ALERT_REPLACEMENT_CATALOG.read_text(encoding="utf-8")
)["rules"]
LINUX_ALERT_REPLACEMENT_UIDS = {
    str(item["replacementUid"]) for item in LINUX_ALERT_REPLACEMENTS
}

VPS_ALERT_UIDS = {
    *(f"nn-gc-metrics-series-{threshold}" for threshold in ("70", "85", "95")),
    *(f"nn-gc-logs-streams-{threshold}" for threshold in ("70", "85", "95")),
    *(f"nn-gc-logs-ingest-{threshold}" for threshold in ("70", "85", "95")),
    *(f"nn-gc-traces-ingest-{threshold}" for threshold in ("70", "85", "95")),
    *(f"nn-gc-synthetic-api-executions-{threshold}" for threshold in ("70", "85", "95")),
    "nn-gc-usage-telemetry-missing",
    "nn-alloy-readiness",
    "nn-alloy-self-metrics-missing",
    "nn-alloy-internal-metrics-missing",
    "nn-alloy-remote-write-failures",
    "nn-alloy-remote-write-backlog",
    "nn-observability-collector-stale",
    "nn-caddy-tls-expiry",
    "nn-caddy-tls-probe-missing",
    "nn-alloy-loki-dropped",
    "nn-alloy-loki-retries",
    "nn-logs-high-error-volume",
    "nn-health-audit-non-success",
    "nn-health-audit-success-missed",
    "nn-backup-verification-overdue",
    "nn-sm-probe-failure",
    "nn-sm-probe-series-contract",
    "nn-sm-inventory-audit-failed",
    "nn-sm-inventory-audit-overdue",
    *LINUX_ALERT_REPLACEMENT_UIDS,
}

PROMETHEUS_QUERIES = {
    "vps_host_exporter": (
        'up{job="integrations/unix",service="host-exporter",deployment_environment="production",instance="vps.nutsnews.com"} == 1',
        1,
    ),
    "vps_alloy_self": (
        'up{job="integrations/nutsnews-vps-alloy",service="alloy",deployment_environment="production",instance="vps.nutsnews.com"} == 1',
        1,
    ),
    "vps_caddy": (
        'up{job="integrations/nutsnews-vps-caddy",service="caddy",deployment_environment="production",instance="vps.nutsnews.com"} == 1',
        1,
    ),
    "vps_observability_collector": (
        'time() - nutsnews_observability_textfile_last_success_timestamp_seconds{deployment_environment="production",instance="vps.nutsnews.com"} < 300',
        1,
    ),
    "vps_docker_stats": (
        'nutsnews_docker_stats_available{deployment_environment="production",instance="vps.nutsnews.com"} == 1',
        1,
    ),
    "vps_production_ownership": (
        'nutsnews_production_ownership_info{job="integrations/unix",instance="vps.nutsnews.com",service_namespace="nutsnews",service="host-exporter",host="vps.nutsnews.com",deployment_environment="production"} == 1 and on() (nutsnews_production_ownership_available{job="integrations/unix",instance="vps.nutsnews.com",service_namespace="nutsnews",service="host-exporter",host="vps.nutsnews.com",deployment_environment="production"} == 1) and on() (time() - nutsnews_production_ownership_last_success_timestamp_seconds{job="integrations/unix",instance="vps.nutsnews.com",service_namespace="nutsnews",service="host-exporter",host="vps.nutsnews.com",deployment_environment="production"} < 300)',
        1,
    ),
    "vps_alert_status_available": (
        'nutsnews_alert_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_backup_status_available": (
        'nutsnews_backup_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_backup_verification_fresh": (
        '(nutsnews_backup_last_verify_finished_age_seconds{deployment_environment="production",instance="vps.nutsnews.com"} >= bool 0) * (nutsnews_backup_last_verify_finished_age_seconds{deployment_environment="production",instance="vps.nutsnews.com"} < bool 108000) * on() (nutsnews_backup_last_verify_success{deployment_environment="production",instance="vps.nutsnews.com"} == bool 1)',
        1,
    ),
    "vps_email_reporting_status_available": (
        'nutsnews_email_reporting_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_health_audit_conclusion": (
        'nutsnews_email_reporting_last_report_conclusion{deployment_environment="production",outcome="success"} == 1',
        1,
    ),
    "vps_health_audit_last_run_age": (
        'nutsnews_email_reporting_last_report_run_age_seconds{deployment_environment="production"} >= 0',
        1,
    ),
    "vps_health_audit_last_success_age": (
        'nutsnews_email_reporting_last_report_success_age_seconds{deployment_environment="production"} >= 0',
        1,
    ),
    "vps_health_audit_delivery_success_age": (
        'nutsnews_email_reporting_last_report_delivery_success_age_seconds{deployment_environment="production"} >= 0',
        1,
    ),
    "vps_health_audit_exit_code": (
        'nutsnews_email_reporting_last_report_exit_code{deployment_environment="production"} == 0',
        1,
    ),
    "vps_app_status_available": (
        'nutsnews_app_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_resource_status_available": (
        'nutsnews_resource_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_security_status_available": (
        'nutsnews_security_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_systemd_status_available": (
        'nutsnews_systemd_service_status_available{deployment_environment="production"} == 1',
        1,
    ),
    "vps_docker_container_state_available": (
        'nutsnews_docker_container_state_available{deployment_environment="production"} == 1',
        1,
    ),
    "backend_host": ('up{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1', 1),
    "backend_alloy_self": (
        'up{job="nutsnews-backend-alloy",service="alloy",deployment_environment="production",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "alloy_remote_write_pending_families": (
        'prometheus_remote_storage_samples_pending{job=~"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy",service_namespace="nutsnews",deployment_environment="production",service="alloy"}',
        2,
    ),
    "alloy_remote_write_failure_families": (
        'prometheus_remote_storage_samples_failed_total{job=~"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy",service_namespace="nutsnews",deployment_environment="production",service="alloy"}',
        2,
    ),
    "alloy_loki_drop_families": (
        'loki_write_dropped_entries_total{job=~"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy",service_namespace="nutsnews",deployment_environment="production",service="alloy"}',
        2,
    ),
    "alloy_loki_retry_families": (
        'loki_write_batch_retries_total{job=~"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy",service_namespace="nutsnews",deployment_environment="production",service="alloy"}',
        2,
    ),
    "vps_alloy_ready": (
        'nutsnews_alloy_ready{job="integrations/unix",instance="vps.nutsnews.com",deployment_environment="production"} == 1',
        1,
    ),
    "backend_alloy_ready": (
        'nutsnews_alloy_ready{job="nutsnews-backend-host",instance="backend.nutsnews.com",deployment_environment="production"} == 1',
        1,
    ),
    "backend_public_endpoint": (
        'nutsnews_backend_public_endpoint_ready{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_rabbitmq": (
        'up{job=~"nutsnews-rabbitmq|nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com"} == 1',
        2,
    ),
    "backend_rabbitmq_queue_messages": (
        f'rabbitmq_detailed_queue_messages{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}',
        35,
    ),
    "backend_rabbitmq_queue_ready": (
        f'rabbitmq_detailed_queue_messages_ready{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}',
        35,
    ),
    "backend_rabbitmq_queue_unacked": (
        f'rabbitmq_detailed_queue_messages_unacked{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}',
        35,
    ),
    "backend_rabbitmq_queue_consumers": (
        f'rabbitmq_detailed_queue_consumers{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_MAIN_QUEUE_SELECTOR}"}}',
        7,
    ),
    "backend_rabbitmq_queue_acked": (
        f'(rabbitmq_detailed_queue_messages_acked_total{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}) or on (queue) (0 * rabbitmq_detailed_queue_messages{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}})',
        35,
    ),
    "backend_rabbitmq_queue_delivered": (
        f'(rabbitmq_detailed_queue_messages_delivered_total{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}) or on (queue) (0 * rabbitmq_detailed_queue_messages{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}})',
        35,
    ),
    "backend_rabbitmq_queue_redelivered": (
        f'(rabbitmq_detailed_queue_messages_redelivered_total{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}}) or on (queue) (0 * rabbitmq_detailed_queue_messages{{job="nutsnews-rabbitmq-queues",environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",queue=~"{WORKER_QUEUE_SELECTOR}"}})',
        35,
    ),
    "backend_rabbitmq_canary": (
        'nutsnews_backend_rabbitmq_canary_success{job="nutsnews-backend-host",environment="production",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_rabbitmq_recovery": (
        'nutsnews_backend_rabbitmq_recovery_stage_healthy{job="nutsnews-backend-host",environment="production",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_health_audit_available": (
        'nutsnews_backend_health_audit_available{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_health_audit_conclusion": (
        'nutsnews_backend_health_audit_conclusion{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com",conclusion="success"} == 1',
        1,
    ),
    "backend_health_audit_last_run": (
        'nutsnews_backend_health_audit_last_run_timestamp_seconds{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} > 0',
        1,
    ),
    "backend_health_audit_last_run_age": (
        'nutsnews_backend_health_audit_last_run_age_seconds{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} >= 0',
        1,
    ),
    "backend_health_audit_last_success": (
        'nutsnews_backend_health_audit_last_success_timestamp_seconds{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} > 0',
        1,
    ),
    "backend_health_audit_last_success_age": (
        'nutsnews_backend_health_audit_last_success_age_seconds{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} >= 0',
        1,
    ),
    "backend_health_audit_consecutive_failures": (
        'nutsnews_backend_health_audit_consecutive_failures{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 0',
        1,
    ),
    "backend_health_audit_critical_checks": (
        'nutsnews_backend_health_audit_critical_checks{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 0',
        1,
    ),
    "backend_health_audit_expected_interval": (
        'nutsnews_backend_health_audit_expected_interval_seconds{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 86400',
        1,
    ),
    "backend_worker_uplift_ownership_available": (
        'nutsnews_backend_worker_uplift_ownership_available{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} == 1 and on() (time() - nutsnews_backend_metric_scrape_timestamp_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} < 600)',
        1,
    ),
    "backend_worker_uplift_expected_active": (
        'nutsnews_backend_worker_uplift_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} and on() (time() - nutsnews_backend_metric_scrape_timestamp_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} < 600)',
        1,
    ),
    "backend_worker_uplift_deployment_info": (
        'nutsnews_backend_worker_uplift_deployment_info{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} == 1 and on() (nutsnews_backend_worker_uplift_ownership_available{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} == 1) and on() (time() - nutsnews_backend_metric_scrape_timestamp_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",environment="production",deployment_environment="production",host="backend.nutsnews.com"} < 600)',
        1,
    ),
    "backend_worker_uplift_deployed_identity_available": (
        'nutsnews_backend_worker_uplift_deployed_identity_available{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_worker_uplift_deployed_service_info": (
        'nutsnews_backend_worker_uplift_deployed_service_info{job="nutsnews-backend-host",service="host",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"} == 1',
        8,
    ),
    "worker_endpoints_identity": (
        'count by (service, deployment_mode, expected_active) (up{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",environment="production",deployment_environment="production"})',
        8,
    ),
    "worker_endpoints_up": (
        'up{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",environment="production",deployment_environment="production"} == 1',
        8,
    ),
    "worker_endpoints_fresh": (
        'time() - timestamp(up{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",environment="production",deployment_environment="production"}) < 180',
        8,
    ),
    "worker_readiness_series": (
        'count by (service) (nutsnews_worker_health_probe{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",service_namespace="nutsnews",environment="production",deployment_environment="production",probe="readiness",outcome="ok"})',
        8,
    ),
    "worker_readiness_ok": (
        'nutsnews_worker_health_probe{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",service_namespace="nutsnews",environment="production",deployment_environment="production",probe="readiness",outcome="ok"} == 1',
        0,
    ),
    "worker_expected_active_signal": (
        'nutsnews_worker_expected_active{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",environment="production"}',
        8,
    ),
    "worker_deployment_info": (
        'nutsnews_worker_deployment_info{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",environment="production"} == 1',
        8,
    ),
    "worker_build_info": (
        'nutsnews_worker_build_info{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",environment="production"} == 1',
        8,
    ),
    "worker_scheduler_loop_active": (
        'nutsnews_worker_scheduler_loop_active{job="nutsnews-worker-uplift",service="scheduler",environment="production"} == 1',
        1,
    ),
    "worker_scheduler_loop_fresh": (
        'nutsnews_worker_scheduler_loop_fresh{job="nutsnews-worker-uplift",service="scheduler",environment="production"} == 1',
        1,
    ),
    "worker_scheduler_last_success": (
        'nutsnews_worker_last_success_timestamp_seconds{job="nutsnews-worker-uplift",service="scheduler",environment="production"} > 0',
        1,
    ),
    "worker_scheduler_cycle_histogram": (
        'count(nutsnews_worker_scheduler_cycle_duration_seconds_bucket{job="nutsnews-worker-uplift",service="scheduler",environment="production",le="+Inf"}) > 0',
        1,
    ),
    "worker_stage_terminal_series": (
        'nutsnews_worker_uplift_stage_events_total{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",service_namespace="nutsnews",environment="production",deployment_environment="production",service=~"fetcher|canonicalizer|enrichment|approval|translation|persistence|publication",outcome=~"success|duplicate|invalid|retry|dlq|failure"}',
        42,
    ),
    "worker_stage_latency_series": (
        'nutsnews_worker_uplift_stage_latency_seconds_bucket{job="nutsnews-worker-uplift",instance="backend.nutsnews.com",host="backend.nutsnews.com",service_namespace="nutsnews",environment="production",deployment_environment="production",service=~"fetcher|canonicalizer|enrichment|approval|translation|persistence|publication",le="+Inf"}',
        7,
    ),
    "worker_publication_terminal_series": (
        'count(nutsnews_worker_uplift_stage_events_total{job="nutsnews-worker-uplift",environment="production",service="publication",outcome=~"success|duplicate|invalid|retry|failure|dlq"}) > 0',
        1,
    ),
    "durable_feed_freshness": (
        'nutsnews_backend_public_feed_snapshot_newest_content_age_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com"} >= 0',
        1,
    ),
    "durable_feed_coverage_available": (
        'nutsnews_backend_content_coverage_available{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_api_up": (
        'nutsnews_backend_api_up{job="nutsnews-backend-api",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_api_postgresql_ready": (
        'nutsnews_backend_api_dependency_ready{job="nutsnews-backend-api",instance="backend.nutsnews.com",dependency="postgresql"} == 1',
        1,
    ),
    "backend_api_requests": (
        'count(nutsnews_backend_api_requests_total{job="nutsnews-backend-api",instance="backend.nutsnews.com"}) > 0',
        1,
    ),
    "backend_api_duration_histogram": (
        'count(nutsnews_backend_api_request_duration_seconds_bucket{job="nutsnews-backend-api",instance="backend.nutsnews.com",le="+Inf"}) > 0',
        1,
    ),
    "backend_api_build_identity": (
        'nutsnews_backend_api_build_info{job="nutsnews-backend-api",instance="backend.nutsnews.com",deployment_environment="production"} == 1',
        1,
    ),
    "backend_postgres_up": (
        'pg_up{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_postgres_connections": (
        'count(pg_stat_database_numbackends{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"}) > 0',
        1,
    ),
    "backend_postgres_metric_families": (
        'count(count by (__name__) ({job="nutsnews-backend-postgres",instance="backend.nutsnews.com",__name__=~"pg_(database|locks|long_running|postmaster|process_idle|replication|stat_activity|stat_bgwriter|stat_checkpointer|stat_database|stat_progress_vacuum|stat_user_tables|stat_wal_receiver|statio_user_indexes|statio_user_tables|wal|xlog).*"})) >= 8',
        1,
    ),
    "backend_postgres_checkpoint_counters": (
        '(count(count by (__name__) (pg_stat_bgwriter_buffers_alloc_total{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"})) == bool 1) * (count(count by (__name__) ({job="nutsnews-backend-postgres",instance="backend.nutsnews.com",__name__=~"pg_stat_(bgwriter_checkpoints_timed_total|checkpointer_num_timed_total)"})) == bool 1)',
        1,
    ),
    "backend_postgres_autovacuum_activity": (
        '({job="nutsnews-backend-postgres",instance="backend.nutsnews.com",__name__=~"pg_stat_(activity_autovacuum_timestamp_seconds|progress_vacuum_heap_blks|progress_vacuum_heap_blks_scanned|progress_vacuum_heap_blks_vacuumed)"}) or on() label_replace(((0 * (max(pg_up{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"}) == 1)) - 1), "activity", "idle", "", "")',
        1,
    ),
    "backend_postgres_wal_size": (
        'count(pg_wal_size_bytes{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"}) > 0',
        1,
    ),
    "backend_postgres_replication_state": (
        '((pg_replication_lag_seconds{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"} or pg_replication_slots_active{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"} or pg_replication_slots_slot_is_active{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"} or pg_replication_slots_pg_wal_lsn_diff{job="nutsnews-backend-postgres",instance="backend.nutsnews.com"}) and on() (max(nutsnews_backend_postgres_replication_lag_configured{job="nutsnews-backend-host",service="host",environment="production",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"}) == 1)) or on() label_replace(((0 * (max(nutsnews_backend_postgres_replication_lag_configured{job="nutsnews-backend-host",service="host",environment="production",deployment_environment="production",instance="backend.nutsnews.com",host="backend.nutsnews.com"}) == 0)) - 1), "replication_state", "disabled_by_configuration", "", "")',
        1,
    ),
    "backend_sync_relay_available": (
        '(nutsnews_backend_sync_relay_available{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_sync_relay_expected_active": (
        'nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"}',
        1,
    ),
    "backend_sync_relay_status": (
        'nutsnews_backend_sync_relay_status{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_sync_relay_fresh": (
        '(nutsnews_backend_sync_relay_collector_fresh{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_sync_relay_healthy": (
        '(nutsnews_backend_sync_relay_healthy{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_sync_relay_lag": (
        '(nutsnews_backend_sync_relay_lag_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com"} >= 0) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_sync_relay_failed_tables": (
        '(nutsnews_backend_sync_relay_failed_table_count{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 0) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_sync_relay_last_success_age": (
        '(nutsnews_backend_sync_relay_last_success_age_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com"} >= 0) and on() (nutsnews_backend_sync_relay_expected_active{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == 1)',
        1,
    ),
    "backend_backup_verification_fresh": (
        '(nutsnews_backend_backup_last_success_age_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com"} >= bool 0) * (nutsnews_backend_backup_last_success_age_seconds{job="nutsnews-backend-host",instance="backend.nutsnews.com"} < bool 108000) * on() (nutsnews_backend_backup_status_available{job="nutsnews-backend-host",instance="backend.nutsnews.com"} == bool 1)',
        1,
    ),
    "backend_caddy_up": (
        'up{job="nutsnews-backend-caddy",instance="backend.nutsnews.com"} == 1',
        1,
    ),
    "backend_caddy_terminal_requests": (
        'count(caddy_http_requests_total{job="nutsnews-backend-caddy",instance="backend.nutsnews.com",handler="subroute"}) > 0',
        1,
    ),
    "backend_caddy_upstream_errors": (
        '((sum(rate(caddy_http_request_errors_total{job="nutsnews-backend-caddy",instance="backend.nutsnews.com",handler="subroute"}[15m])) or on() (0 * sum(rate(caddy_http_request_duration_seconds_count{job="nutsnews-backend-caddy",instance="backend.nutsnews.com",handler="subroute"}[15m])))) and on() (max(up{job="nutsnews-backend-caddy",instance="backend.nutsnews.com"}) == 1)) >= 0',
        1,
    ),
    "backend_caddy_upstream_health_state": (
        'min by (upstream) (caddy_reverse_proxy_upstreams_healthy{job="nutsnews-backend-caddy",instance="backend.nutsnews.com"}) or on() label_replace(vector(-1), "upstream", "disabled_by_configuration", "", "")',
        1,
    ),
    "backend_caddy_tls_probe": (
        'probe_success{job="nutsnews-backend-caddy-tls",instance="backend.nutsnews.com",probe="tls",check="certificate"} == 1',
        1,
    ),
    "backend_caddy_tls_expiry": (
        'probe_ssl_earliest_cert_expiry{job="nutsnews-backend-caddy-tls",instance="backend.nutsnews.com",probe="tls",check="certificate"} - time() > 0',
        1,
    ),
}

WORKER_SERVICES = {
    "scheduler",
    "fetcher",
    "canonicalizer",
    "enrichment",
    "approval",
    "translation",
    "persistence",
    "publication",
}

DELIVERY_WORKER_SERVICES = WORKER_SERVICES - {"scheduler"}

EXPECTED_ONE_SAMPLE_QUERIES = {
    "vps_host_exporter",
    "vps_alloy_self",
    "vps_caddy",
    "vps_alloy_ready",
    "vps_docker_stats",
    "vps_production_ownership",
    "vps_alert_status_available",
    "vps_backup_status_available",
    "vps_backup_verification_fresh",
    "vps_email_reporting_status_available",
    "vps_health_audit_conclusion",
    "vps_app_status_available",
    "vps_resource_status_available",
    "vps_security_status_available",
    "vps_systemd_status_available",
    "vps_docker_container_state_available",
    "backend_host",
    "backend_alloy_self",
    "backend_alloy_ready",
    "backend_public_endpoint",
    "backend_rabbitmq",
    "backend_rabbitmq_canary",
    "backend_rabbitmq_recovery",
    "backend_health_audit_available",
    "backend_health_audit_conclusion",
    "backend_worker_uplift_ownership_available",
    "backend_worker_uplift_deployment_info",
    "backend_worker_uplift_deployed_identity_available",
    "worker_endpoints_up",
    "worker_readiness_ok",
    "worker_deployment_info",
    "worker_build_info",
    "worker_scheduler_loop_active",
    "worker_scheduler_loop_fresh",
    "worker_scheduler_cycle_histogram",
    "durable_feed_coverage_available",
    "backend_api_up",
    "backend_api_postgresql_ready",
    "backend_api_build_identity",
    "backend_postgres_up",
    "backend_postgres_checkpoint_counters",
    "backend_postgres_wal_size",
    "backend_sync_relay_available",
    "backend_sync_relay_fresh",
    "backend_sync_relay_healthy",
    "backend_backup_verification_fresh",
    "backend_caddy_up",
    "backend_caddy_tls_probe",
}

EXPECTED_ZERO_SAMPLE_QUERIES = {
    "vps_health_audit_exit_code",
    "backend_health_audit_consecutive_failures",
    "backend_health_audit_critical_checks",
    "backend_sync_relay_failed_tables",
}

RELAY_CONDITIONAL_QUERIES = {
    "backend_sync_relay_available",
    "backend_sync_relay_fresh",
    "backend_sync_relay_healthy",
    "backend_sync_relay_lag",
    "backend_sync_relay_failed_tables",
    "backend_sync_relay_last_success_age",
}

USAGE_QUERIES = {
    "metrics_active_series": "grafanacloud_instance_active_series",
    "metrics_active_series_limit": (
        'grafanacloud_instance_metrics_limits{limit_name="max_global_series_per_user"}'
    ),
    "metrics_active_series_ratio": (
        "max(grafanacloud_instance_active_series / on(id) "
        'grafanacloud_instance_metrics_limits{limit_name="max_global_series_per_user"})'
    ),
}

LOKI_QUERIES = {
    "backend_host_logs": '{deployment_environment="production",host="backend.nutsnews.com"}',
    "backend_journal": '{deployment_environment="production",host="backend.nutsnews.com",source="journal"}',
    "backend_rabbitmq_logs": (
        '{deployment_environment="production",host="backend.nutsnews.com",source="container",service="rabbitmq"}'
    ),
    "backend_api_logs": '{deployment_environment="production",host="backend.nutsnews.com",source="journal",service="backend-api"}',
    "backend_caddy_logs": '{deployment_environment="production",host="backend.nutsnews.com",service="caddy"}',
    "backend_alloy_logs": '{deployment_environment="production",host="backend.nutsnews.com",source="journal",service="alloy"}',
    "backend_postgresql_logs": '{deployment_environment="production",host="backend.nutsnews.com",source=~"journal|postgresql",service="postgresql"}',
    "backend_sync_relay_logs": '{deployment_environment="production",host="backend.nutsnews.com",source="journal",service="sync-relay"}',
    "vps_caddy_logs": '{deployment_environment="production",host="vps.nutsnews.com",service="caddy"}',
    "vps_web_logs": '{deployment_environment="production",host="vps.nutsnews.com",service="web"}',
    "vps_alloy_logs": '{deployment_environment="production",host="vps.nutsnews.com",source="journal",service="alloy"}',
    **{
        f"worker_{service}_logs": (
            '{deployment_environment="production",host="backend.nutsnews.com",'
            f'source="container",service="{service}"}}'
        )
        for service in sorted(WORKER_SERVICES)
    },
}

# Access and application services normally emit within the six-hour default.
# Quiet database and process-log sources may only emit at checkpoint/startup, so
# retain a bounded one-day proof window rather than creating synthetic log noise.
LOKI_QUERY_HOURS_OVERRIDES = {
    "backend_postgresql_logs": 24,
    "vps_caddy_logs": 24,
    "vps_web_logs": 24,
}

LOKI_INDEXED_LABELS = {
    "deployment_environment",
    "service",
    "service_version",
    "host",
    "source",
    "severity",
}
LOKI_PLATFORM_INDEXED_LABELS = {"service_name"}
LOKI_ALLOWED_INDEXED_LABELS = LOKI_INDEXED_LABELS | LOKI_PLATFORM_INDEXED_LABELS
SYNTHETIC_CONTRACT_ERROR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity", ("approved checks", "bounded production identity labels")),
    ("enabled", ("must be enabled",)),
    ("schedule", ("five minutes", "frequency differs", "timeout differs")),
    ("probes", ("probe ids", "public selection")),
    ("metrics_mode", ("basic metrics",)),
    ("target", ("approved read-only https route", "desired target")),
    ("http_settings", ("http settings",)),
    ("method_tls", ("tls-required read-only get",)),
    ("redirects", ("reject redirects",)),
    ("status_code", ("exactly http 200",)),
    ("assertion_shape", ("assertion families differ",)),
    ("desired_assertions", ("approved behavioral contract",)),
    ("homepage_content", ("homepage content", "maintenance payloads")),
    ("article_content", ("article response content",)),
    ("cache_header", ("cache-control header",)),
    ("readiness_content", ("ready=true and deployment identity",)),
    ("unknown_identity", ("unknown deployment identity",)),
    (
        "deployment_identity",
        (
            "production-vps identity",
            "vercel-production identity",
            "two production deployment identities",
        ),
    ),
)
GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"
SYNTHETIC_MONITORING_HOSTNAME = re.compile(
    r"synthetic-monitoring-api(?:[.-][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.grafana\.net",
    re.ASCII,
)
PUBLIC_TARGET_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.ASCII,
)
APPROVED_SYNTHETIC_ASSERTIONS: dict[str, dict[str, list[Any]]] = {
    "canonical_homepage": {
        "fail_if_body_matches_regexp": ["maintenance"],
        "fail_if_body_not_matches_regexp": ["NutsNews"],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [],
    },
    "canonical_articles_api": {
        "fail_if_body_matches_regexp": [],
        "fail_if_body_not_matches_regexp": ["articles"],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {
                "allow_missing": False,
                "header": "Cache-Control",
                "regexp": "public|max-age|s-maxage",
            }
        ],
    },
    "canonical_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*(production-vps|vercel-production)",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {"allow_missing": False, "header": "Cache-Control", "regexp": "no-store"}
        ],
    },
    "vps_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*production-vps",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {"allow_missing": False, "header": "Cache-Control", "regexp": "no-store"}
        ],
    },
    "vercel_secondary_readiness": {
        "fail_if_body_matches_regexp": ["deploymentTarget.*unknown"],
        "fail_if_body_not_matches_regexp": [
            "ready.*true",
            "deploymentTarget.*vercel-production",
        ],
        "fail_if_header_matches_regexp": [],
        "fail_if_header_not_matches_regexp": [
            {"allow_missing": False, "header": "Cache-Control", "regexp": "no-store"}
        ],
    },
}
REPORT_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE | re.ASCII)
REPORT_DATASOURCE_UID_PATH = re.compile(
    r"(?P<prefix>/api/datasources/(?:proxy/)?uid/)[^/]+",
    re.ASCII,
)
REPORT_LABEL_KEY_ALLOWLIST = {
    "__name__",
    "check",
    "config_version",
    "dependency",
    "deployment_environment",
    "environment",
    "host",
    "job",
    "language",
    "outcome",
    "probe",
    "provider",
    "queue",
    "service",
    "service_namespace",
    "service_version",
    "severity",
    "stage",
    "worker_service",
}
REPORT_QUERY_STATUSES = {"error", "success"}
REPORT_SLO_SAMPLE_STATES = {
    "dashboard-only-no-terminal-events",
    "dashboard-only-recorded-samples-visible",
    "not-evaluated",
    "required-finite-samples",
}
REPORT_WORKER_PHASES = {
    "not-evaluated",
    "production-runtime-v1-required",
    "shadow-runtime-identity-visible",
}
REPORT_EXTERNAL_BASELINE_STATES = {
    "approved",
    "pending_authenticated_rollout",
}
REPORT_EXTERNAL_DISPOSITIONS = {"remove_via_integration_upgrade", "retain"}
REPORT_EXTERNAL_STATES = {
    "pending-supported-upgrade",
    "removed-by-supported-integration-upgrade",
    "retained",
}
REPORT_EXTERNAL_FINGERPRINT_STATES = {
    "drifted",
    "matched-approved-baseline",
    "not-required-obsolete-upgrade-rule",
    "pending-approved-baseline",
}
REPORT_ERROR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("synthetic_monitoring", ("synthetic", "probe")),
    ("grafana_slo", ("grafana slo", "slo ")),
    ("logs", ("loki", "log ", "log-")),
    ("alerting", ("alert", "contact point", "notification policy", "rule")),
    ("terraform_state", ("terraform",)),
    (
        "telemetry",
        (
            "prometheus",
            "alloy",
            "rabbitmq",
            "worker",
            "backend",
            "caddy",
            "relay",
            "postgres",
            "collector",
        ),
    ),
    ("api_transport", ("grafana api",)),
)


def _reject_json_constant(value: str) -> None:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _normalize_sensitive_report_values(values: Iterable[str]) -> tuple[str, ...]:
    """Return protected values and common escaped representations, longest first."""
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        normalized.update(
            {
                value,
                repr(value),
                json.dumps(value, ensure_ascii=True),
                json.dumps(value, ensure_ascii=False),
                value.encode("unicode_escape").decode("ascii"),
                urllib.parse.quote(value, safe=""),
                urllib.parse.quote_plus(value, safe=""),
            }
        )
        url_redacted_value = REPORT_URL.sub("[redacted-url]", value)
        if url_redacted_value not in {value, "[redacted-url]"}:
            normalized.add(url_redacted_value)
    return tuple(
        sorted(
            normalized,
            key=len,
            reverse=True,
        )
    )


def _label_structure_summary(value: Any) -> dict[str, Any]:
    """Describe label shape without persisting any label values."""
    if not isinstance(value, list):
        return {
            "series_count": 0,
            "invalid_series_count": 1,
            "allowlisted_label_keys": [],
        }
    safe_keys: set[str] = set()
    invalid_series_count = 0
    for labels in value:
        if not isinstance(labels, dict):
            invalid_series_count += 1
            continue
        safe_keys.update(
            key
            for key in labels
            if isinstance(key, str) and key in REPORT_LABEL_KEY_ALLOWLIST
        )
    return {
        "series_count": len(value),
        "invalid_series_count": invalid_series_count,
        "allowlisted_label_keys": sorted(safe_keys),
    }


def _safe_nonnegative_integer(value: Any) -> int | None:
    """Return a JSON-safe nonnegative integer or null for untrusted values."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if (
        isinstance(value, float)
        and math.isfinite(value)
        and value >= 0
        and value.is_integer()
    ):
        return int(value)
    return None


def _safe_finite_number(value: Any) -> int | float | None:
    """Return a finite JSON number or null for nonnumeric/nonfinite input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _bounded_string(value: Any, approved: set[str], fallback: str = "unknown") -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in approved else fallback


def _bounded_counts(values: Iterable[Any], approved: set[str]) -> dict[str, int]:
    counts = {key: 0 for key in sorted(approved)}
    counts["unknown"] = 0
    for value in values:
        category = _bounded_string(value, approved)
        counts[category] += 1
    return counts


def _source_owned_external_rule_uids() -> set[str]:
    """Load the public catalog UIDs that may key safe fingerprint evidence."""
    catalog = json.loads(
        EXTERNAL_RULE_CATALOG.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )
    rules = catalog.get("rules") if isinstance(catalog, dict) else None
    return {
        str(rule["uid"])
        for rule in (rules if isinstance(rules, list) else [])
        if isinstance(rule, dict) and isinstance(rule.get("uid"), str)
    }


def _errors_summary(value: Any) -> dict[str, Any]:
    """Retain only bounded error categories, never exception/provider text."""
    if not isinstance(value, list):
        return {
            "error_count": 0,
            "invalid_error_count": 1,
            "category_counts": {},
        }
    category_counts: dict[str, int] = {}
    invalid_error_count = 0
    for error in value:
        if not isinstance(error, str):
            invalid_error_count += 1
            category = "invalid"
        else:
            lowered = error.lower()
            category = "other"
            for candidate, tokens in REPORT_ERROR_CATEGORIES:
                if any(token in lowered for token in tokens):
                    category = candidate
                    break
        category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "error_count": len(value),
        "invalid_error_count": invalid_error_count,
        "category_counts": dict(sorted(category_counts.items())),
    }


def _query_result_summary(value: Any) -> dict[str, Any]:
    """Project a Prometheus/Loki response into value-free structural evidence."""
    if not isinstance(value, dict):
        return {
            "status": "unknown",
            "indexed_series_status": "unknown",
            "result_count": None,
            "finite_sample_count": 0,
            "non_finite_sample_count": None,
            "invalid_sample_count": None,
            "line_count": None,
            "indexed_series_missing_normalized_label_count": 0,
            "indexed_series_unexpected_label_count": 0,
            "indexed_series_service_alias_mismatch_count": 0,
            "label_structure": _label_structure_summary(None),
        }
    samples = value.get("sample_values")
    finite_samples = (
        [
            sample
            for sample in samples
            if isinstance(sample, (int, float))
            and not isinstance(sample, bool)
            and (not isinstance(sample, float) or math.isfinite(sample))
        ]
        if isinstance(samples, list)
        else []
    )
    finite_sample_count = (
        len(finite_samples)
    )
    series_labels = value.get("series_labels")
    safe_series_labels = (
        [labels for labels in series_labels if isinstance(labels, dict)]
        if isinstance(series_labels, list)
        else []
    )
    labels = value.get("indexed_series_labels")
    if labels is None:
        labels = value.get("series_labels")
    if labels is None:
        labels = value.get("stream_labels")
    indexed_labels = value.get("indexed_series_labels")
    safe_indexed_labels = (
        [item for item in indexed_labels if isinstance(item, dict)]
        if isinstance(indexed_labels, list)
        else []
    )
    return {
        "status": _bounded_string(value.get("status"), REPORT_QUERY_STATUSES),
        "indexed_series_status": _bounded_string(
            value.get("indexed_series_status"), REPORT_QUERY_STATUSES
        ),
        "result_count": _safe_nonnegative_integer(value.get("result_count")),
        "finite_sample_count": finite_sample_count,
        # Counts reveal whether an allowlisted query is healthy without persisting
        # metric values, probe names, config revisions, targets, or credentials.
        "zero_sample_count": sum(sample == 0 for sample in finite_samples),
        "one_sample_count": sum(sample == 1 for sample in finite_samples),
        "other_finite_sample_count": sum(
            sample not in {0, 1} for sample in finite_samples
        ),
        "distinct_probe_label_count": len(
            {
                str(labels["probe"])
                for labels in safe_series_labels
                if isinstance(labels.get("probe"), str) and labels["probe"]
            }
        ),
        "distinct_config_version_count": len(
            {
                str(labels["config_version"])
                for labels in safe_series_labels
                if isinstance(labels.get("config_version"), str)
                and labels["config_version"]
            }
        ),
        "non_finite_sample_count": _safe_nonnegative_integer(
            value.get("non_finite_sample_count", 0)
        ),
        "invalid_sample_count": _safe_nonnegative_integer(
            value.get("invalid_sample_count", 0)
        ),
        "line_count": _safe_nonnegative_integer(value.get("line_count")),
        "indexed_series_missing_normalized_label_count": sum(
            bool(LOKI_ALLOWED_INDEXED_LABELS - set(item))
            for item in safe_indexed_labels
        ),
        "indexed_series_unexpected_label_count": sum(
            bool(set(item) - LOKI_ALLOWED_INDEXED_LABELS)
            for item in safe_indexed_labels
        ),
        "indexed_series_service_alias_mismatch_count": sum(
            item.get("service_name") != item.get("service")
            for item in safe_indexed_labels
        ),
        "label_structure": _label_structure_summary(labels),
    }


def _query_collection_summary(
    value: Any,
    approved_names: Iterable[str],
) -> dict[str, Any]:
    """Retain per-query evidence only for source-owned query names."""
    approved = set(approved_names)
    if not isinstance(value, dict):
        return {
            "expected_query_count": len(approved),
            "observed_query_count": 0,
            "approved_query_result_count": 0,
            "unexpected_query_count": 0,
            "results": {},
        }
    results = {
        name: _query_result_summary(value[name])
        for name in sorted(approved)
        if name in value
    }
    return {
        "expected_query_count": len(approved),
        "observed_query_count": len(value),
        "approved_query_result_count": len(results),
        "unexpected_query_count": len(set(value) - approved),
        "results": results,
    }


def _folder_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"expected_count": 2, "observed_count": 0, "verified_count": 0}
    return {
        "expected_count": 2,
        "observed_count": len(value),
        "verified_count": sum(isinstance(title, str) and bool(title) for title in value.values()),
    }


def _contact_point_summary(value: Any) -> dict[str, Any]:
    points = value if isinstance(value, list) else []
    valid_points = [point for point in points if isinstance(point, dict)]
    return {
        "managed_contact_point_count": len(valid_points),
        "email_integration_count": sum(
            _safe_nonnegative_integer(point.get("email_integration_count")) or 0
            for point in valid_points
        ),
        "recipient_configuration_present_count": sum(
            point.get("recipient_configuration_present") is True
            for point in valid_points
        ),
        "resolved_notifications_enabled_count": sum(
            point.get("resolved_notifications_enabled") is True
            for point in valid_points
        ),
    }


def _notification_policy_summary(value: Any) -> dict[str, Any]:
    expected_group_by = ["alertname", "service", "deployment_environment"]
    expected_root_timings = ["5m", "15m", "6h"]
    expected_routes = {
        "critical|major": ["30s", "5m", "1h"],
        "warning|minor|low": ["5m", "15m", "6h"],
    }
    if not isinstance(value, dict):
        return {
            "root_contract_matches": False,
            "expected_route_count": len(expected_routes),
            "observed_route_count": 0,
            "matched_route_count": 0,
            "contract_matches": False,
        }
    routes = value.get("routes") if isinstance(value.get("routes"), list) else []
    matched_route_count = 0
    for severity, timings in expected_routes.items():
        matches = [
            route
            for route in routes
            if isinstance(route, dict)
            and route.get("severity") == severity
            and route.get("receiver") == CONTACT_POINT_NAME
            and route.get("group_by") == expected_group_by
            and route.get("timings") == timings
            and route.get("matcher") == [["severity", "=~", severity]]
        ]
        matched_route_count += len(matches) == 1
    root_contract_matches = (
        value.get("receiver") == CONTACT_POINT_NAME
        and value.get("group_by") == expected_group_by
        and value.get("timings") == expected_root_timings
    )
    return {
        "root_contract_matches": root_contract_matches,
        "expected_route_count": len(expected_routes),
        "observed_route_count": len(routes),
        "matched_route_count": matched_route_count,
        "contract_matches": (
            root_contract_matches
            and len(routes) == len(expected_routes)
            and matched_route_count == len(expected_routes)
        ),
    }


def _slo_summary(value: Any) -> dict[str, Any]:
    slos = value if isinstance(value, dict) else {}
    safe_slos: dict[str, Any] = {}
    for key in sorted(EXPECTED_SLO_SPECS):
        item = slos.get(key)
        if not isinstance(item, dict):
            continue
        safe_slos[key] = {
            "recording_rule_count": _safe_nonnegative_integer(
                item.get("recording_rule_count")
            ),
            "alert_rule_count": _safe_nonnegative_integer(item.get("alert_rule_count")),
            "recorded_sample_state": _bounded_string(
                item.get("recorded_sample_state"), REPORT_SLO_SAMPLE_STATES
            ),
            "recorded_samples": _query_collection_summary(
                item.get("recorded_samples"), GRAFANA_SLO_RECORDED_METRICS
            ),
        }
    return {
        "expected_count": len(EXPECTED_SLO_SPECS),
        "observed_count": len(slos),
        "verified_count": len(safe_slos),
        "unexpected_slo_count": len(set(slos) - set(EXPECTED_SLO_SPECS)),
        "slos": safe_slos,
    }


def _worker_rollout_summary(value: Any) -> dict[str, Any]:
    rollout = value if isinstance(value, dict) else {}
    list_fields = {
        "readiness_ok_services": WORKER_SERVICES,
        "deployment_identity_services": WORKER_SERVICES,
        "build_identity_services": WORKER_SERVICES,
        "host_verified_deployed_identity_services": WORKER_SERVICES,
    }
    service_counts: dict[str, int] = {}
    approved_set_matches: dict[str, bool] = {}
    for field, expected in list_fields.items():
        items = rollout.get(field)
        service_counts[field] = len(items) if isinstance(items, list) else 0
        approved_set_matches[field] = (
            isinstance(items, list)
            and all(isinstance(item, str) for item in items)
            and set(items) == expected
            and len(items) == len(expected)
        )
    host_expected_active = _safe_finite_number(rollout.get("host_expected_active"))
    ownership_state = (
        "active"
        if host_expected_active == 1
        else "shadow"
        if host_expected_active == 0
        else "not-evaluated"
        if rollout.get("phase") == "not-evaluated"
        else "invalid"
    )
    return {
        "phase": _bounded_string(
            rollout.get("phase"), REPORT_WORKER_PHASES, "not-evaluated"
        ),
        "ownership_state": ownership_state,
        "deployment_mode": _bounded_string(
            rollout.get("host_deployment_mode"), {"production", "shadow"}
        ),
        "delivery_service_count": _safe_nonnegative_integer(
            rollout.get("delivery_service_count")
        ),
        "service_counts": service_counts,
        "approved_service_set_matches": approved_set_matches,
    }


def _external_rule_inventory_summary(value: Any) -> dict[str, Any]:
    inventory = value if isinstance(value, dict) else {}
    rules = inventory.get("rules") if isinstance(inventory.get("rules"), list) else []
    valid_rules = [rule for rule in rules if isinstance(rule, dict)]
    source_owned_uids = _source_owned_external_rule_uids()
    fingerprints = {
        str(rule["uid"]): str(rule["definition_fingerprint_sha256"]).lower()
        for rule in valid_rules
        if isinstance(rule.get("uid"), str)
        and rule.get("uid") in source_owned_uids
        and isinstance(rule.get("definition_fingerprint_sha256"), str)
        and re.fullmatch(
            r"[0-9a-fA-F]{64}", str(rule["definition_fingerprint_sha256"])
        )
        is not None
    }
    return {
        "expected_retained_count": _safe_nonnegative_integer(
            inventory.get("expected_retained_count")
        ),
        "expected_post_upgrade_count": _safe_nonnegative_integer(
            inventory.get("expected_post_upgrade_count")
        ),
        "definition_fingerprint_baseline_status": _bounded_string(
            inventory.get("definition_fingerprint_baseline_status"),
            REPORT_EXTERNAL_BASELINE_STATES,
        ),
        "definition_drift_validation": _safe_boolean(
            inventory.get("definition_drift_validation")
        ),
        "observed_live_count": _safe_nonnegative_integer(
            inventory.get("observed_live_count")
        ),
        "rule_count": len(valid_rules),
        "invalid_rule_count": len(rules) - len(valid_rules),
        "observed_definition_fingerprints_sha256": dict(sorted(fingerprints.items())),
        "kind_counts": _bounded_counts(
            (rule.get("kind") for rule in valid_rules), {"alert", "recording"}
        ),
        "health_status_counts": _bounded_counts(
            (_bounded_rule_health(rule.get("health")) for rule in valid_rules),
            {"error", "nodata", "ok"},
        ),
        "disposition_counts": _bounded_counts(
            (rule.get("disposition") for rule in valid_rules),
            REPORT_EXTERNAL_DISPOSITIONS,
        ),
        "state_counts": _bounded_counts(
            (rule.get("state") for rule in valid_rules), REPORT_EXTERNAL_STATES
        ),
        "fingerprint_status_counts": _bounded_counts(
            (rule.get("definition_fingerprint_status") for rule in valid_rules),
            REPORT_EXTERNAL_FINGERPRINT_STATES,
        ),
    }


def _terraform_state_summary(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    integer_fields = {
        "synthetic_check_count",
        "synthetic_public_probe_count",
        "slo_count",
    }
    number_fields = {
        "synthetic_execution_estimate",
        "synthetic_execution_guardrail",
        "synthetic_execution_major_threshold",
    }
    boolean_fields = {
        "synthetic_major_forecast_acknowledged",
        "rollout_decisions_enforced",
        "worker_terminal_slo_alerting_enabled",
    }
    return {
        **{field: _safe_nonnegative_integer(state.get(field)) for field in sorted(integer_fields)},
        **{field: _safe_finite_number(state.get(field)) for field in sorted(number_fields)},
        **{field: _safe_boolean(state.get(field)) for field in sorted(boolean_fields)},
    }


def _synthetic_inventory_summary(value: Any) -> dict[str, Any]:
    inventory = value if isinstance(value, dict) else {}
    checks = inventory.get("checks") if isinstance(inventory.get("checks"), list) else []
    valid_checks = [check for check in checks if isinstance(check, dict)]
    managed_contracts: dict[str, Any] = {}
    allowed_contract_categories = {
        name for name, _tokens in SYNTHETIC_CONTRACT_ERROR_CATEGORIES
    }
    for check in valid_checks:
        job = check.get("job")
        validation = check.get("contract_validation")
        if (
            job not in EXPECTED_SYNTHETIC_CHECKS
            or check.get("terraform_managed") is not True
            or not isinstance(validation, dict)
        ):
            continue
        raw_counts = validation.get("category_counts")
        safe_counts = (
            {
                name: _safe_nonnegative_integer(count)
                for name, count in sorted(raw_counts.items())
                if name in allowed_contract_categories
            }
            if isinstance(raw_counts, dict)
            else {}
        )
        managed_contracts[str(job)] = {
            "valid": _safe_boolean(validation.get("valid")),
            "error_count": _safe_nonnegative_integer(
                validation.get("error_count")
            ),
            "category_counts": safe_counts,
            "unexpected_category_count": (
                len(set(raw_counts) - allowed_contract_categories)
                if isinstance(raw_counts, dict)
                else 0
            ),
        }
    return {
        "enabled_api_check_count": _safe_nonnegative_integer(
            inventory.get("enabled_api_check_count")
        ),
        "enabled_browser_check_count": _safe_nonnegative_integer(
            inventory.get("enabled_browser_check_count")
        ),
        "monthly_api_execution_estimate": _safe_finite_number(
            inventory.get("monthly_api_execution_estimate")
        ),
        "monthly_api_execution_ceiling": _safe_finite_number(
            inventory.get("monthly_api_execution_ceiling")
        ),
        "execution_estimate_complete": _safe_boolean(
            inventory.get("execution_estimate_complete")
        ),
        "inventory_check_count": len(valid_checks),
        "terraform_managed_check_count": sum(
            check.get("terraform_managed") is True for check in valid_checks
        ),
        "enabled_check_count": sum(check.get("enabled") is True for check in valid_checks),
        "managed_contracts": dict(sorted(managed_contracts.items())),
    }


def _bounded_rule_health(value: Any) -> str:
    """Return a fixed health category for untrusted ruler status text."""
    status = str(value).strip().lower()
    return status if status in {"error", "nodata", "ok"} else "unknown"


def _bounded_rule_state(value: Any) -> str:
    """Return a fixed state category for untrusted ruler state text."""
    state = str(value).strip().lower()
    return (
        state
        if state
        in {
            "alerting",
            "error",
            "inactive",
            "nodata",
            "normal",
            "pending",
            "recovering",
        }
        else "unknown"
    )


def _alert_rule_health_summary(value: Any) -> dict[str, Any]:
    """Aggregate ruler health without retaining UIDs or provider error details."""
    if not isinstance(value, dict):
        return {
            "rule_count": 0,
            "invalid_rule_count": 1,
            "rules_with_error_detail_count": 0,
            "health_status_counts": {},
            "state_status_counts": {},
        }
    health_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    invalid_rule_count = 0
    rules_with_error_detail_count = 0
    for item in value.values():
        if not isinstance(item, dict):
            invalid_rule_count += 1
            continue
        health = _bounded_rule_health(item.get("health", "unknown"))
        state = _bounded_rule_state(item.get("state", "unknown"))
        health_counts[health] = health_counts.get(health, 0) + 1
        state_counts[state] = state_counts.get(state, 0) + 1
        if item.get("last_error") or item.get("lastError"):
            rules_with_error_detail_count += 1
    return {
        "rule_count": len(value),
        "invalid_rule_count": invalid_rule_count,
        "rules_with_error_detail_count": rules_with_error_detail_count,
        "health_status_counts": dict(sorted(health_counts.items())),
        "state_status_counts": dict(sorted(state_counts.items())),
    }


def _datasource_generated_alerts_summary(value: Any) -> dict[str, Any]:
    """Aggregate datasource alerts without retaining their raw alert labels."""
    counts = {"DatasourceError": 0, "DatasourceNoData": 0, "other": 0}
    if not isinstance(value, list):
        return {
            "active_alert_count": 0,
            "invalid_alert_count": 1,
            "alert_type_counts": counts,
        }
    invalid_alert_count = 0
    for item in value:
        if not isinstance(item, dict):
            invalid_alert_count += 1
            continue
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        alert_name = item.get("alertname") or labels.get("alertname")
        if not isinstance(alert_name, str):
            invalid_alert_count += 1
            category = "other"
        else:
            category = alert_name if alert_name in counts else "other"
        counts[category] += 1
    return {
        "active_alert_count": len(value),
        "invalid_alert_count": invalid_alert_count,
        "alert_type_counts": counts,
    }


def _sanitize_report_for_output(
    value: Any,
    sensitive_values: tuple[str, ...],
) -> Any:
    """Project in-memory validation data into a closed, value-free evidence schema."""
    del sensitive_values  # Projection, not replacement, is the primary trust boundary.
    if not isinstance(value, dict):
        raise ValueError("Grafana Cloud verification evidence must be an object.")

    sanitized: dict[str, Any] = {
        "schema_version": 4,
        "status": _bounded_string(value.get("status"), {"fail", "pass"}, "fail"),
    }
    count_fields = {
        "dashboard_count",
        "managed_alert_count",
        "backend_alert_count",
        "worker_uplift_alert_count",
        "linux_integration_alert_replacement_count",
    }
    for field in sorted(count_fields):
        if field in value:
            sanitized[field] = _safe_nonnegative_integer(value.get(field))

    field_projectors: dict[str, Callable[[Any], Any]] = {
        "folders": _folder_summary,
        "alert_rule_health": _alert_rule_health_summary,
        "datasource_generated_alerts": _datasource_generated_alerts_summary,
        "contact_points": _contact_point_summary,
        "notification_policy": _notification_policy_summary,
        "grafana_slos": _slo_summary,
        "worker_rollout": _worker_rollout_summary,
        "external_rule_inventory": _external_rule_inventory_summary,
        "terraform_state": _terraform_state_summary,
        "prometheus_queries": lambda item: _query_collection_summary(
            item, PROMETHEUS_QUERIES
        ),
        "synthetic_queries": lambda item: _query_collection_summary(
            item, EXPECTED_SYNTHETIC_CHECKS
        ),
        "synthetic_monitoring_inventory": _synthetic_inventory_summary,
        "usage_queries": lambda item: _query_collection_summary(item, USAGE_QUERIES),
        "loki_queries": lambda item: _query_collection_summary(item, LOKI_QUERIES),
        "errors": _errors_summary,
    }
    for field, projector in field_projectors.items():
        if field in value:
            sanitized[field] = projector(value[field])

    approved_fields = {"status", *count_fields, *field_projectors}
    sanitized["unexpected_top_level_field_count"] = len(set(value) - approved_fields)
    return sanitized


def sanitize_report_for_output(
    value: Any,
    sensitive_values: Iterable[str] = (),
) -> Any:
    """Return upload-safe evidence while leaving validation data intact in memory."""
    return _sanitize_report_for_output(value, _normalize_sensitive_report_values(sensitive_values))


def report_contains_sensitive_value(
    rendered_report: str,
    sensitive_values: Iterable[str],
) -> bool:
    """Fail-closed predicate for protected values surviving final serialization."""
    return any(
        isinstance(value, str) and bool(value) and value in rendered_report
        for value in sensitive_values
    )


def serialize_report_for_output(
    value: Any,
    sensitive_values: Iterable[str] = (),
) -> str:
    """Render the complete upload artifact after value-free structural sanitization."""
    all_sensitive_values = _normalize_sensitive_report_values(sensitive_values)
    safe_report = sanitize_report_for_output(value, all_sensitive_values)
    rendered = json.dumps(safe_report, indent=2, sort_keys=True, allow_nan=False)
    if report_contains_sensitive_value(rendered, all_sensitive_values):
        raise ValueError(
            "Grafana Cloud verification evidence still contains a protected value."
        )
    return rendered


def safe_api_path(path: str) -> str:
    """Return a non-query API path with every datasource UID structurally removed."""
    try:
        parsed = urllib.parse.urlsplit(path)
    except (UnicodeError, ValueError):
        return "/[invalid-path]"
    if not parsed.path.startswith("/"):
        return "/[invalid-path]"
    return REPORT_DATASOURCE_UID_PATH.sub(
        r"\g<prefix>[redacted-datasource-uid]", parsed.path
    )


def safe_urlsplit(value: str, error_message: str) -> urllib.parse.SplitResult:
    """Parse an untrusted URL without ever surfacing the raw netloc in an error."""
    try:
        parsed = urllib.parse.urlsplit(value)
        # Force validation of lazy SplitResult properties while the exception can still
        # be replaced with a fixed message.
        _ = (parsed.hostname, parsed.port, parsed.username, parsed.password)
    except (UnicodeError, ValueError):
        raise ValueError(error_message) from None
    return parsed


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every HTTP redirect into an error before bearer credentials can move."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class GrafanaClient:
    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = validate_grafana_cloud_url(url, "GRAFANA_URL")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def request(self, method: str, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code if isinstance(exc.code, int) else "unknown"
            exc.close()
            raise RuntimeError(
                f"Grafana API {method} {safe_api_path(path)} failed with HTTP {status}"
            ) from None
        except urllib.error.URLError:
            raise RuntimeError(
                f"Grafana API {method} {safe_api_path(path)} failed before an HTTP response"
            ) from None
        try:
            decoded = raw.decode("utf-8")
            return (
                json.loads(decoded, parse_constant=_reject_json_constant)
                if decoded
                else {}
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise RuntimeError(
                f"Grafana API {method} {safe_api_path(path)} returned invalid JSON"
            ) from None


class SyntheticMonitoringClient(GrafanaClient):
    """Read-only client for the regional Synthetic Monitoring API."""

    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = validate_synthetic_monitoring_url(url)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())


def _validate_role_origin(
    value: str,
    name: str,
    *,
    hostname_is_allowed: Callable[[str], bool],
    role: str,
) -> str:
    invalid_origin = f"{name} must be a query-free HTTPS {role} origin"
    if value != value.strip():
        raise ValueError(invalid_origin)
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        # Force lazy userinfo validation while raw input is still inside the
        # fixed-message exception boundary.
        _ = (parsed.username, parsed.password)
    except (UnicodeError, ValueError):
        raise ValueError(invalid_origin) from None
    if (
        parsed.scheme != "https"
        or not hostname
        or not hostname_is_allowed(hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower()
        not in {hostname, f"{hostname}:443"}
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(invalid_origin)
    return f"https://{hostname}"


def validate_grafana_cloud_url(value: str, name: str) -> str:
    return _validate_role_origin(
        value,
        name,
        hostname_is_allowed=lambda hostname: hostname == GRAFANA_UI_HOSTNAME,
        role="kindcantaloupe2036.grafana.net Grafana UI API",
    )


def validate_synthetic_monitoring_url(value: str) -> str:
    return _validate_role_origin(
        value,
        "GRAFANA_SM_URL",
        hostname_is_allowed=lambda hostname: (
            SYNTHETIC_MONITORING_HOSTNAME.fullmatch(hostname) is not None
        ),
        role="synthetic-monitoring-api*.grafana.net service API",
    )


def env(name: str, fallback: str = "") -> str:
    return os.environ.get(name, os.environ.get(fallback, "")).strip()


def raw_env(name: str, fallback: str = "") -> str:
    """Read security-sensitive URL input without normalizing attacker-controlled text."""
    return os.environ.get(name, os.environ.get(fallback, ""))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )


def prometheus_query(client: GrafanaClient, datasource_uid: str, query: str) -> dict[str, Any]:
    encoded = urllib.parse.urlencode({"query": query})
    response = client.request(
        "GET",
        f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid, safe='')}/api/v1/query?{encoded}",
    )
    data = response.get("data", {})
    result = data.get("result", []) if isinstance(data, dict) else []
    labels = [
        item.get("metric", {})
        for item in result
        if isinstance(item, dict) and isinstance(item.get("metric", {}), dict)
    ] if isinstance(result, list) else []
    sample_values: list[float] = []
    sample_timestamps: list[float] = []
    non_finite_sample_count = 0
    invalid_sample_count = 0
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            invalid_sample_count += 1
            continue
        sample = item.get("value")
        if not (isinstance(sample, list) and len(sample) >= 2):
            values = item.get("values")
            sample = values[-1] if isinstance(values, list) and values else None
        if not (isinstance(sample, list) and len(sample) >= 2):
            invalid_sample_count += 1
            continue
        try:
            timestamp = float(sample[0])
            value = float(sample[1])
        except (TypeError, ValueError):
            invalid_sample_count += 1
            continue
        if math.isfinite(timestamp) and math.isfinite(value):
            sample_timestamps.append(timestamp)
            sample_values.append(value)
        else:
            non_finite_sample_count += 1
    return {
        "query": query,
        "status": response.get("status", "unknown"),
        "result_count": len(result) if isinstance(result, list) else 0,
        "series_labels": labels,
        "sample_values": sample_values,
        "sample_timestamps": sample_timestamps,
        "non_finite_sample_count": non_finite_sample_count,
        "invalid_sample_count": invalid_sample_count,
    }


def loki_query_range(
    client: GrafanaClient,
    datasource_uid: str,
    query: str,
    hours: int,
) -> dict[str, Any]:
    end = int(time.time() * 1_000_000_000)
    start = end - (hours * 60 * 60 * 1_000_000_000)
    encoded = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start),
            "end": str(end),
            "limit": "20",
            "direction": "backward",
        }
    )
    response = client.request(
        "GET",
        f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid, safe='')}/loki/api/v1/query_range?{encoded}",
    )
    data = response.get("data", {})
    result = data.get("result", []) if isinstance(data, dict) else []
    line_count = sum(
        len(stream.get("values", []))
        for stream in result
        if isinstance(stream, dict) and isinstance(stream.get("values", []), list)
    ) if isinstance(result, list) else 0
    stream_labels = [
        stream.get("stream", {})
        for stream in result
        if isinstance(stream, dict) and isinstance(stream.get("stream", {}), dict)
    ] if isinstance(result, list) else []
    return {
        "query": query,
        "status": response.get("status", "unknown"),
        "result_count": len(result) if isinstance(result, list) else 0,
        "line_count": line_count,
        "stream_labels": stream_labels,
    }


def loki_series(
    client: GrafanaClient,
    datasource_uid: str,
    query: str,
    hours: int,
) -> dict[str, Any]:
    """Return Loki's actual indexed series labels for the bounded query window."""
    end = int(time.time() * 1_000_000_000)
    start = end - (hours * 60 * 60 * 1_000_000_000)
    encoded = urllib.parse.urlencode(
        [("match[]", query), ("start", str(start)), ("end", str(end))]
    )
    response = client.request(
        "GET",
        f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid, safe='')}/loki/api/v1/series?{encoded}",
    )
    data = response.get("data", [])
    labels = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    return {
        "status": response.get("status", "unknown"),
        "result_count": len(labels),
        "indexed_series_labels": labels,
    }


def loki_query_evidence(
    client: GrafanaClient,
    datasource_uid: str,
    query: str,
    hours: int,
) -> dict[str, Any]:
    """Combine recent line evidence with the authoritative Loki series index."""
    result = loki_query_range(client, datasource_uid, query, hours)
    series = loki_series(client, datasource_uid, query, hours)
    result["indexed_series_labels"] = series["indexed_series_labels"]
    result["indexed_series_status"] = series["status"]
    return result


def validate_loki_indexed_labels(
    name: str,
    indexed_series_labels: Any,
    errors: list[str],
) -> None:
    """Require normalized boundary labels plus Grafana's service-name alias."""
    if not isinstance(indexed_series_labels, list):
        errors.append(f"Loki indexed series response is invalid for {name}")
        return
    for labels in indexed_series_labels:
        if not isinstance(labels, dict):
            errors.append(f"Loki indexed series labels are invalid for {name}")
            continue
        unexpected = sorted(set(labels) - LOKI_ALLOWED_INDEXED_LABELS)
        missing_labels = sorted(LOKI_ALLOWED_INDEXED_LABELS - set(labels))
        if unexpected:
            errors.append(
                f"Loki series has unapproved indexed labels for {name}: {unexpected!r}"
            )
        if missing_labels:
            errors.append(
                f"Loki series is missing normalized indexed labels for {name}: "
                f"{missing_labels!r}"
            )
        if labels.get("service_name") != labels.get("service"):
            errors.append(f"Loki service_name alias does not match service for {name}")


def loki_log_is_required(name: str, relay_status: str) -> bool:
    """Return whether a recent line is required for this bounded service state."""
    return not (
        name == "backend_sync_relay_logs" and relay_status == "not_configured"
    )


def safe_check(
    name: str,
    check: Callable[[], Any],
    errors: list[str],
    default: Any,
) -> Any:
    try:
        return check()
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"{name}: {exc}")
        return default


def list_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "data", "results", "slos", "checks"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                nested_items = list_items(nested)
                if nested_items:
                    return nested_items
    return []


def synthetic_labels_as_map(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    labels: dict[str, str] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "value"}
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("value"), str)
            or item["name"] in labels
        ):
            return {}
        labels[item["name"]] = item["value"]
    return labels


def synthetic_assertion_strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def canonical_desired_synthetic_assertions(
    check: dict[str, Any],
) -> dict[str, list[Any]] | None:
    normalized: dict[str, list[Any]] = {}
    for field in (
        "fail_if_body_matches_regexp",
        "fail_if_body_not_matches_regexp",
    ):
        value = check.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return None
        normalized[field] = value
    for field in (
        "fail_if_header_matches_regexp",
        "fail_if_header_not_matches_regexp",
    ):
        value = check.get(field, [])
        if not isinstance(value, list):
            return None
        headers: list[dict[str, Any]] = []
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("allow_missing", False), bool)
                or not isinstance(item.get("header"), str)
                or not isinstance(item.get("regexp"), str)
            ):
                return None
            headers.append(
                {
                    "allow_missing": item.get("allow_missing", False),
                    "header": item["header"],
                    "regexp": item["regexp"],
                }
            )
        normalized[field] = headers
    return normalized


def validate_synthetic_target(job: str, value: Any, message: str) -> str:
    """Return a public target hostname after enforcing the exact protected route."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(message)
    parsed = safe_urlsplit(value, message)
    hostname = parsed.hostname or ""
    expected_path = (
        "/"
        if job == "canonical_homepage"
        else "/api/articles"
        if job == "canonical_articles_api"
        else "/readyz"
    )
    forbidden_segments = {"refresh", "controller", "ingest", "trigger", "publish"}
    path_segments = {segment.lower() for segment in parsed.path.split("/") if segment}
    if (
        parsed.scheme != "https"
        or PUBLIC_TARGET_HOSTNAME.fullmatch(hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.netloc.lower() not in {hostname, f"{hostname}:443"}
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != expected_path.rstrip("/")
        or bool(path_segments & forbidden_segments)
    ):
        raise ValueError(message)
    return hostname


def has_synthetic_header_assertion(
    value: Any, header: str, required_terms: tuple[str, ...]
) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        regexp = str(item.get("regexp", "")).lower()
        if (
            str(item.get("header", "")).lower() == header.lower()
            and item.get("allowMissing") is False
            and any(term in regexp for term in required_terms)
        ):
            return True
    return False


def parse_desired_synthetic_checks(value: str) -> dict[str, dict[str, Any]]:
    try:
        checks = json.loads(value, parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "protected Synthetic Monitoring check configuration must be valid JSON"
        ) from exc
    if (
        not isinstance(checks, dict)
        or set(checks) != EXPECTED_SYNTHETIC_CHECKS
        or any(not isinstance(check, dict) for check in checks.values())
    ):
        raise ValueError(
            "protected Synthetic Monitoring configuration must contain exactly the five approved checks"
        )
    target_parse_error = "protected Synthetic Monitoring targets must use approved public HTTPS routes"
    for job, check in checks.items():
        validate_synthetic_target(job, check.get("target"), target_parse_error)
        if canonical_desired_synthetic_assertions(check) != APPROVED_SYNTHETIC_ASSERTIONS[job]:
            raise ValueError(
                "protected Synthetic Monitoring assertions must exactly match the approved "
                "behavioral contract"
            )
    canonical_hosts = {
        validate_synthetic_target(name, checks[name].get("target"), target_parse_error)
        for name in (
            "canonical_homepage",
            "canonical_readiness",
            "canonical_articles_api",
        )
    }
    direct_host = validate_synthetic_target(
        "vps_readiness", checks["vps_readiness"].get("target"), target_parse_error
    )
    secondary_host = validate_synthetic_target(
        "vercel_secondary_readiness",
        checks["vercel_secondary_readiness"].get("target"),
        target_parse_error,
    )
    if (
        len(canonical_hosts) != 1
        or None in canonical_hosts
        or not direct_host
        or not secondary_host
        or direct_host == secondary_host
        or direct_host in canonical_hosts
        or secondary_host in canonical_hosts
    ):
        raise ValueError(
            "protected synthetic targets must use one canonical host plus distinct direct-VPS and Vercel-secondary hosts"
        )
    return checks


def protected_report_values(
    grafana_token: str,
    synthetic_monitoring_token: str,
    desired_synthetic_checks_raw: str,
    desired_synthetic_checks: dict[str, dict[str, Any]],
    *datasource_uids: str,
) -> tuple[str, ...]:
    """Collect credentials, targets, and datasource identities excluded from evidence."""
    values = [
        grafana_token,
        synthetic_monitoring_token,
        desired_synthetic_checks_raw,
        *datasource_uids,
    ]
    for job, check in desired_synthetic_checks.items():
        target = check.get("target")
        if isinstance(target, str):
            values.extend(
                (
                    target,
                    validate_synthetic_target(
                        job,
                        target,
                        "protected Synthetic Monitoring target is invalid",
                    ),
                )
            )
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def desired_header_assertions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return []
        normalized.append(
            {
                "allowMissing": item.get("allow_missing", False),
                "header": item.get("header"),
                "regexp": item.get("regexp"),
            }
        )
    return normalized


def canonical_synthetic_assertion_family(field: str, value: Any) -> tuple[Any, ...] | None:
    # The Synthetic Monitoring API schema declares every assertion array
    # nullable and serializes an unconfigured family as null. Terraform models
    # the same state as an empty list, so normalize only that representation;
    # individual null/invalid assertions continue to fail closed below.
    if value is None:
        value = []
    if not isinstance(value, list):
        return None
    if field == "validStatusCodes":
        if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
            return None
        normalized: list[Any] = list(value)
    elif field in {"failIfBodyMatchesRegexp", "failIfBodyNotMatchesRegexp"}:
        if any(not isinstance(item, str) for item in value):
            return None
        normalized = list(value)
    else:
        normalized = []
        for item in value:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("allowMissing"), bool)
                or not isinstance(item.get("header"), str)
                or not isinstance(item.get("regexp"), str)
            ):
                return None
            normalized.append(
                (item["allowMissing"], item["header"], item["regexp"])
            )
    if len(set(normalized)) != len(normalized):
        return None
    return tuple(sorted(normalized))


def validate_remote_synthetic_contract(
    check: dict[str, Any],
    expected_probe_ids: set[int],
    desired: dict[str, Any],
    errors: list[str],
) -> bool:
    """Validate one managed check without returning targets or assertion text."""
    starting_error_count = len(errors)
    job = str(check.get("job", ""))
    prefix = f"remote synthetic contract {job or '<missing-job>'}"
    if job not in EXPECTED_SYNTHETIC_CHECKS:
        errors.append(f"{prefix} is not one of the five approved checks")
        return False
    if check.get("enabled") is not True:
        errors.append(f"{prefix} must be enabled")
    if check.get("frequency") != 300_000:
        errors.append(f"{prefix} must run every five minutes")
    if check.get("frequency") != desired.get("frequency_ms", 300_000):
        errors.append(f"{prefix} frequency differs from the protected desired contract")
    if check.get("timeout") != desired.get("timeout_ms", 5_000):
        errors.append(f"{prefix} timeout differs from the protected desired contract")
    probes = check.get("probes")
    if (
        not isinstance(probes, list)
        or len(probes) != 2
        or any(
            not isinstance(probe, int) or isinstance(probe, bool) or probe <= 0
            for probe in probes
        )
        or len(set(probes)) != 2
    ):
        errors.append(f"{prefix} must use exactly two distinct positive probe IDs")
    elif set(probes) != expected_probe_ids:
        errors.append(f"{prefix} probe IDs do not match the protected public selection")
    if check.get("basicMetricsOnly") is not True or check.get("alertSensitivity") != "none":
        errors.append(f"{prefix} must use basic metrics with native alert sensitivity disabled")

    labels = synthetic_labels_as_map(check.get("labels"))
    expected_labels = {
        "service_namespace": "nutsnews",
        "deployment_environment": "production",
        "check": job,
        "owner": "nutsnews-observability",
        "service": "synthetic-monitoring",
    }
    if any(labels.get(name) != value for name, value in expected_labels.items()):
        errors.append(f"{prefix} is missing its bounded production identity labels")

    try:
        validate_synthetic_target(
            job,
            check.get("target"),
            f"{prefix} does not use its approved read-only HTTPS route",
        )
    except ValueError:
        errors.append(f"{prefix} does not use its approved read-only HTTPS route")
    if check.get("target") != desired.get("target"):
        errors.append(f"{prefix} target differs from the protected desired target")

    settings = check.get("settings")
    http = settings.get("http") if isinstance(settings, dict) else None
    if not isinstance(http, dict):
        errors.append(f"{prefix} must use HTTP settings")
        return False
    if http.get("method", 0) not in (0, "GET") or http.get("failIfNotSSL") is not True:
        errors.append(f"{prefix} must remain a TLS-required read-only GET check")
    if http.get("noFollowRedirects") is not True:
        errors.append(f"{prefix} must reject redirects")
    if http.get("validStatusCodes") != [200]:
        errors.append(f"{prefix} must require exactly HTTP 200")
    exact_assertion_contract = {
        "validStatusCodes": desired.get("valid_status_codes", [200]),
        "failIfBodyMatchesRegexp": desired.get("fail_if_body_matches_regexp", []),
        "failIfBodyNotMatchesRegexp": desired.get(
            "fail_if_body_not_matches_regexp", []
        ),
        "failIfHeaderMatchesRegexp": desired_header_assertions(
            desired.get("fail_if_header_matches_regexp", [])
        ),
        "failIfHeaderNotMatchesRegexp": desired_header_assertions(
            desired.get("fail_if_header_not_matches_regexp", [])
        ),
    }
    remote_contract = {
        field: canonical_synthetic_assertion_family(field, http.get(field, []))
        for field in exact_assertion_contract
    }
    desired_contract = {
        field: canonical_synthetic_assertion_family(field, expected)
        for field, expected in exact_assertion_contract.items()
    }
    if (
        any(value is None for value in remote_contract.values())
        or any(value is None for value in desired_contract.values())
        or remote_contract != desired_contract
    ):
        errors.append(f"{prefix} assertion families differ from the protected desired contract")

    if canonical_desired_synthetic_assertions(desired) != APPROVED_SYNTHETIC_ASSERTIONS[job]:
        errors.append(f"{prefix} desired assertions are not the approved behavioral contract")

    body_matches = synthetic_assertion_strings(http.get("failIfBodyMatchesRegexp"))
    body_not_matches = synthetic_assertion_strings(
        http.get("failIfBodyNotMatchesRegexp")
    )
    body_matches_lower = [pattern.lower() for pattern in body_matches]
    body_not_matches_lower = [pattern.lower() for pattern in body_not_matches]
    if job == "canonical_homepage":
        if not any("nutsnews" in pattern for pattern in body_not_matches_lower):
            errors.append(f"{prefix} must require real NutsNews homepage content")
        if not any("maintenance" in pattern for pattern in body_matches_lower):
            errors.append(f"{prefix} must reject maintenance payloads")
    elif job == "canonical_articles_api":
        if not any("articles" in pattern for pattern in body_not_matches_lower):
            errors.append(f"{prefix} must require article response content")
        if not has_synthetic_header_assertion(
            http.get("failIfHeaderNotMatchesRegexp"),
            "cache-control",
            ("public", "max-age", "s-maxage"),
        ):
            errors.append(f"{prefix} must require a present public cache-control header")
    else:
        joined_required = " ".join(body_not_matches_lower)
        if not all(term in joined_required for term in ("ready", "true", "deploymenttarget")):
            errors.append(f"{prefix} must require ready=true and deployment identity")
        if not any(
            "deploymenttarget" in pattern and "unknown" in pattern
            for pattern in body_matches_lower
        ):
            errors.append(f"{prefix} must reject an unknown deployment identity")
        if not has_synthetic_header_assertion(
            http.get("failIfHeaderNotMatchesRegexp"), "cache-control", ("no-store", "no.?store")
        ):
            errors.append(f"{prefix} must require a present no-store cache-control header")
        if job == "vps_readiness" and "production-vps" not in joined_required:
            errors.append(f"{prefix} must require the production-vps identity")
        if job == "vercel_secondary_readiness" and "vercel-production" not in joined_required:
            errors.append(f"{prefix} must require the vercel-production identity")
        if job == "canonical_readiness" and not any(
            "production-vps" in pattern
            and "vercel-production" in pattern
            and "|" in pattern
            for pattern in body_not_matches_lower
        ):
            errors.append(f"{prefix} must allow exactly the two production deployment identities")
    return len(errors) == starting_error_count


def synthetic_contract_error_summary(contract_errors: Any) -> dict[str, Any]:
    """Classify verifier-owned contract failures without retaining API values."""
    if not isinstance(contract_errors, list):
        return {"valid": False, "error_count": 0, "category_counts": {}}
    counts: dict[str, int] = {}
    for error in contract_errors:
        category = "other"
        if isinstance(error, str):
            lowered = error.lower()
            for candidate, tokens in SYNTHETIC_CONTRACT_ERROR_CATEGORIES:
                if any(token in lowered for token in tokens):
                    category = candidate
                    break
        counts[category] = counts.get(category, 0) + 1
    return {
        "valid": not contract_errors,
        "error_count": len(contract_errors),
        "category_counts": dict(sorted(counts.items())),
    }


def remote_synthetic_inventory(
    client: SyntheticMonitoringClient,
    managed_ids: Any,
    selected_probes: Any,
    desired_checks: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    """Inventory every remote check while retaining only non-target metadata."""
    response = client.request("GET", "/api/v1/check")
    summaries = list_items(response)
    if not summaries:
        errors.append("Synthetic Monitoring inventory returned no checks")

    normalized_managed_ids: dict[str, int] = {}
    if not isinstance(managed_ids, dict) or set(managed_ids) != EXPECTED_SYNTHETIC_CHECKS:
        errors.append("Terraform state does not contain exactly the five managed synthetic IDs")
    else:
        for job, check_id in managed_ids.items():
            normalized_check_id = (
                check_id
                if isinstance(check_id, int) and not isinstance(check_id, bool)
                else int(check_id)
                if isinstance(check_id, str)
                and re.fullmatch(r"[1-9][0-9]*", check_id) is not None
                else 0
            )
            if normalized_check_id <= 0:
                errors.append(f"Terraform synthetic check ID is invalid for {job}")
            else:
                normalized_managed_ids[job] = normalized_check_id

    expected_probe_ids: set[int] = set()
    if not isinstance(selected_probes, dict) or len(selected_probes) != 2:
        errors.append("Terraform state does not contain exactly two selected public probes")
    else:
        for probe in selected_probes.values():
            probe_id = probe.get("id") if isinstance(probe, dict) else None
            if (
                not isinstance(probe, dict)
                or probe.get("public") is not True
                or not isinstance(probe_id, int)
                or isinstance(probe_id, bool)
                or probe_id <= 0
            ):
                errors.append("Terraform selected probe state contains an invalid or private probe")
            else:
                expected_probe_ids.add(probe_id)
        if len(expected_probe_ids) != 2:
            errors.append("Terraform selected probe IDs must be two distinct public probes")

    safe_inventory: list[dict[str, Any]] = []
    enabled_details: list[dict[str, Any]] = []
    enabled_api_details: list[dict[str, Any]] = []
    observed_ids: set[int] = set()
    execution_estimate = 0
    execution_estimate_complete = True
    for summary in summaries:
        check_id = summary.get("id")
        if (
            not isinstance(check_id, int)
            or isinstance(check_id, bool)
            or check_id <= 0
        ):
            errors.append("Synthetic Monitoring inventory contains an invalid check ID")
            execution_estimate_complete = False
            continue
        if check_id in observed_ids:
            errors.append(f"Synthetic Monitoring inventory contains duplicate check ID {check_id}")
            execution_estimate_complete = False
            continue
        observed_ids.add(check_id)
        detail_response = client.request("GET", f"/api/v1/check/{check_id}")
        detail = (
            detail_response.get("check")
            if isinstance(detail_response, dict)
            and isinstance(detail_response.get("check"), dict)
            else detail_response
        )
        if not isinstance(detail, dict) or detail.get("id") != check_id:
            errors.append(f"Synthetic Monitoring detail does not match check ID {check_id}")
            execution_estimate_complete = False
            continue
        job = str(detail.get("job", ""))
        settings = detail.get("settings")
        check_kind = (
            "browser"
            if isinstance(settings, dict) and settings.get("browser") is not None
            else "api"
        )
        enabled = detail.get("enabled") is True
        frequency = detail.get("frequency")
        probes = detail.get("probes")
        probe_count = len(probes) if isinstance(probes, list) else 0
        safe_inventory.append(
            {
                "check_id": check_id,
                "job": job,
                "enabled": enabled,
                "check_kind": check_kind,
                "frequency_ms": frequency if isinstance(frequency, int) else None,
                "probe_count": probe_count,
                "terraform_managed": normalized_managed_ids.get(job) == check_id,
            }
        )
        if enabled and check_kind == "api":
            enabled_details.append(detail)
            enabled_api_details.append(detail)
            if (
                not isinstance(frequency, int)
                or isinstance(frequency, bool)
                or frequency <= 0
                or not isinstance(probes, list)
                or not probes
            ):
                errors.append(
                    f"Enabled Synthetic Monitoring API check has invalid scheduling metadata: {job or check_id}"
                )
                execution_estimate_complete = False
            else:
                execution_estimate += math.ceil(
                    SYNTHETIC_MONTH_MILLISECONDS / frequency
                ) * len(probes)
        elif enabled:
            enabled_details.append(detail)

    enabled_jobs = [str(item.get("job", "")) for item in enabled_api_details]
    if len(enabled_details) != 5 or len(enabled_api_details) != 5 or set(enabled_jobs) != EXPECTED_SYNTHETIC_CHECKS:
        errors.append(
            "Enabled Synthetic Monitoring inventory must contain exactly the five approved HTTP API checks and no browser checks"
        )
    if len(enabled_jobs) != len(set(enabled_jobs)):
        errors.append("Enabled Synthetic Monitoring API inventory contains duplicate jobs")
    for detail in enabled_api_details:
        job = str(detail.get("job", ""))
        check_id = detail.get("id")
        if job in EXPECTED_SYNTHETIC_CHECKS:
            if normalized_managed_ids.get(job) != check_id:
                errors.append(f"Remote Synthetic Monitoring ID does not match Terraform for {job}")
            contract_errors: list[str] = []
            validate_remote_synthetic_contract(
                detail,
                expected_probe_ids,
                desired_checks.get(job, {}),
                contract_errors,
            )
            errors.extend(contract_errors)
            for item in safe_inventory:
                if item.get("check_id") == check_id and item.get("job") == job:
                    item["contract_validation"] = synthetic_contract_error_summary(
                        contract_errors
                    )
                    break

    if not execution_estimate_complete:
        errors.append("Live Synthetic Monitoring API execution estimate is incomplete")
    if execution_estimate >= SYNTHETIC_API_EXECUTION_CEILING_MONTHLY:
        errors.append(
            "Live Synthetic Monitoring API execution estimate must remain below 90,000 per month"
        )
    return {
        "enabled_api_check_count": len(enabled_api_details),
        "enabled_browser_check_count": len(enabled_details) - len(enabled_api_details),
        "monthly_api_execution_estimate": execution_estimate,
        "monthly_api_execution_ceiling": SYNTHETIC_API_EXECUTION_CEILING_MONTHLY,
        "execution_estimate_complete": execution_estimate_complete,
        "checks": sorted(
            safe_inventory,
            key=lambda item: (str(item["job"]), int(item["check_id"])),
        ),
    }


def synthetic_probe_result_is_current(
    result: dict[str, Any], since_epoch: float, expected_value: float = 1
) -> bool:
    labels = result.get("series_labels", [])
    values = result.get("sample_values", [])
    timestamps = result.get("sample_timestamps", [])
    if not (
        result.get("result_count") == 2
        and len(labels) == 2
        and len(values) == 2
        and len(timestamps) == 2
    ):
        return False
    probes = {
        str(item.get("probe", "")) for item in labels if isinstance(item, dict)
    }
    config_versions = {
        str(item.get("config_version", ""))
        for item in labels
        if isinstance(item, dict)
    }
    return (
        "" not in probes
        and len(probes) == 2
        and "" not in config_versions
        and len(config_versions) == 1
        and all(value == expected_value for value in values)
        and all(timestamp >= since_epoch - 5 for timestamp in timestamps)
    )


def poll_current_synthetic_probe_results(
    client: GrafanaClient,
    datasource_uid: str,
    since_epoch: float,
    timeout_seconds: int = 780,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Poll all five jobs together until each has one fresh two-probe config."""
    deadline = monotonic() + timeout_seconds
    latest: dict[str, dict[str, Any]] = {}
    while True:
        for name in sorted(EXPECTED_SYNTHETIC_CHECKS):
            source_freshness_cutoff = int(since_epoch - 5)
            query = (
                f'(probe_success{{job="{name}"}} '
                "and on(job, instance, probe, config_version) "
                f'(timestamp(probe_success{{job="{name}"}}) >= {source_freshness_cutoff})) '
                "* on(job, instance, probe, config_version) group_left() "
                f'sm_check_info{{job="{name}",label_service_namespace="nutsnews",'
                'label_deployment_environment="production"}'
            )
            try:
                latest[name] = prometheus_query(client, datasource_uid, query)
            except (RuntimeError, ValueError, TypeError, KeyError):
                latest[name] = {
                    "query": query,
                    "status": "error",
                    "result_count": 0,
                    "series_labels": [],
                    "sample_values": [],
                    "sample_timestamps": [],
                }
        pending = [
            name
            for name, result in latest.items()
            if not synthetic_probe_result_is_current(result, since_epoch)
        ]
        if not pending or monotonic() >= deadline:
            return latest, sorted(pending)
        sleep(15)


def rule_uid(rule: dict[str, Any]) -> str:
    grafana_alert = rule.get("grafana_alert", {})
    if not isinstance(grafana_alert, dict):
        grafana_alert = {}
    return str(rule.get("uid") or grafana_alert.get("uid") or "")


def summarize_contact_points(response: Any, errors: list[str]) -> list[dict[str, Any]]:
    summary = []
    for point in list_items(response):
        if point.get("name") != CONTACT_POINT_NAME:
            continue
        integrations = (
            [point]
            if str(point.get("type", "")).lower() == "email"
            else point.get("grafana_managed_receiver_configs") or point.get("receivers") or []
        )
        if not isinstance(integrations, list):
            integrations = []
        email_integrations = [
            item for item in integrations
            if isinstance(item, dict) and str(item.get("type", "")).lower() == "email"
        ]
        resolve_enabled = bool(email_integrations) and all(
            not bool(
                item.get("disableResolveMessage", item.get("disable_resolve_message", False))
            )
            for item in email_integrations
        )
        recipient_configuration_present = bool(email_integrations) and all(
            isinstance(item.get("settings"), dict)
            and bool(item["settings"].get("addresses"))
            for item in email_integrations
        )
        summary.append(
            {
                "name": CONTACT_POINT_NAME,
                "email_integration_count": len(email_integrations),
                "recipient_configuration_present": recipient_configuration_present,
                "resolved_notifications_enabled": resolve_enabled,
            }
        )
        if not email_integrations:
            errors.append(f"managed contact point has no email integration: {CONTACT_POINT_NAME}")
        if not resolve_enabled:
            errors.append(f"resolved notifications are disabled: {CONTACT_POINT_NAME}")
        if not recipient_configuration_present:
            errors.append(f"managed contact point has no configured recipients: {CONTACT_POINT_NAME}")
    if not summary:
        errors.append(f"missing managed contact point: {CONTACT_POINT_NAME}")
    return summary


def policy_routes(policy: dict[str, Any]) -> list[dict[str, Any]]:
    routes = policy.get("routes") or policy.get("policy") or []
    return [route for route in routes if isinstance(route, dict)] if isinstance(routes, list) else []


def policy_matchers(route: dict[str, Any]) -> list[tuple[str, str, str]] | None:
    """Return the exact provisioning-API matcher triples for one policy route."""
    matchers = route.get("object_matchers")
    if not isinstance(matchers, list):
        return None
    normalized: list[tuple[str, str, str]] = []
    for matcher in matchers:
        if (
            not isinstance(matcher, list)
            or len(matcher) != 3
            or not all(isinstance(value, str) for value in matcher)
        ):
            return None
        normalized.append((matcher[0], matcher[1], matcher[2]))
    return normalized


def verify_notification_policy(policy: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(policy, dict):
        errors.append("notification policy response is not an object")
        return {}
    expected_group_by = ["alertname", "service", "deployment_environment"]
    expected_root_timings = ("5m", "15m", "6h")
    receiver = policy.get("receiver") or policy.get("contact_point")
    if receiver != CONTACT_POINT_NAME:
        errors.append(f"notification policy root receiver is {receiver!r}, expected {CONTACT_POINT_NAME!r}")
    if policy.get("group_by") != expected_group_by:
        errors.append(
            "notification policy root group_by mismatch: "
            f"{policy.get('group_by')!r}, expected {expected_group_by!r}"
        )
    root_timings = (
        policy.get("group_wait"),
        policy.get("group_interval"),
        policy.get("repeat_interval"),
    )
    if root_timings != expected_root_timings:
        errors.append(
            "notification policy root timing mismatch: "
            f"{root_timings!r}, expected {expected_root_timings!r}"
        )
    routes = policy_routes(policy)
    expected = {
        "critical|major": ("30s", "5m", "1h"),
        "warning|minor|low": ("5m", "15m", "6h"),
    }
    if len(routes) != len(expected):
        errors.append(
            "notification policy must contain exactly two managed severity routes: "
            f"observed {len(routes)}"
        )
    route_summary = []
    for severity, timings in expected.items():
        expected_matcher = [("severity", "=~", severity)]
        matching = [route for route in routes if policy_matchers(route) == expected_matcher]
        if len(matching) != 1:
            errors.append(
                "notification policy must contain exactly one structural severity matcher "
                f"{expected_matcher!r}: observed {len(matching)}"
            )
            continue
        route = matching[0]
        route_receiver = route.get("receiver") or route.get("contact_point")
        if route_receiver != CONTACT_POINT_NAME:
            errors.append(
                f"notification policy receiver mismatch for {severity}: "
                f"{route_receiver!r}, expected {CONTACT_POINT_NAME!r}"
            )
        if route.get("group_by") != expected_group_by:
            errors.append(
                f"notification policy group_by mismatch for {severity}: "
                f"{route.get('group_by')!r}, expected {expected_group_by!r}"
            )
        actual = (
            route.get("group_wait"),
            route.get("group_interval"),
            route.get("repeat_interval"),
        )
        if actual != timings:
            errors.append(
                f"notification policy timing mismatch for {severity}: {actual!r}, expected {timings!r}"
            )
        route_summary.append(
            {
                "severity": severity,
                "matcher": [list(item) for item in expected_matcher],
                "receiver": route_receiver,
                "group_by": route.get("group_by"),
                "timings": list(actual),
            }
        )
    return {
        "receiver": receiver,
        "group_by": policy.get("group_by"),
        "timings": list(root_timings),
        "routes": route_summary,
    }


def ruler_health(response: Any) -> dict[str, dict[str, Any]]:
    data = response.get("data", {}) if isinstance(response, dict) else {}
    groups = data.get("groups", []) if isinstance(data, dict) else []
    health: dict[str, dict[str, Any]] = {}
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []):
            if not isinstance(rule, dict):
                continue
            uid = rule_uid(rule)
            if uid:
                health[uid] = {
                    "health": rule.get("health", "unknown"),
                    "last_error": rule.get("lastError") or rule.get("last_error") or "",
                    "state": rule.get("state", "unknown"),
                }
    return health


def ruler_rules(response: Any) -> list[dict[str, Any]]:
    data = response.get("data", {}) if isinstance(response, dict) else {}
    groups = data.get("groups", []) if isinstance(data, dict) else []
    flattened: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules", []):
            if isinstance(rule, dict):
                flattened.append({"group": group.get("name", ""), **rule})
    return flattened


def strict_key_value_list(
    value: Any,
    context: str,
    errors: list[str],
) -> dict[str, str]:
    """Normalize an SLO API key/value list while rejecting ambiguous shapes."""
    if not isinstance(value, list):
        errors.append(f"{context} must be a key/value list")
        return {}
    result: dict[str, str] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "value"}
            or not isinstance(item.get("key"), str)
            or not isinstance(item.get("value"), str)
            or not item["key"]
            or item["key"] in result
        ):
            errors.append(f"{context} contains invalid or duplicate key/value entries")
            return {}
        result[item["key"]] = item["value"]
    return result


def remote_slo_item(response: Any, slo_uuid: str) -> dict[str, Any]:
    """Return the one exact UUID-matched SLO from a detail or list response."""
    candidates = (
        [response]
        if isinstance(response, dict) and response.get("uuid") == slo_uuid
        else [item for item in list_items(response) if item.get("uuid") == slo_uuid]
    )
    if len(candidates) != 1:
        raise ValueError(
            f"Grafana SLO API returned {len(candidates)} objects for managed UUID {slo_uuid!r}"
        )
    return candidates[0]


def wait_for_remote_slo(
    client: GrafanaClient,
    slo_uuid: str,
    timeout_seconds: int = 120,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Wait for one Terraform-owned SLO to reach a stable API lifecycle state."""
    deadline = monotonic() + timeout_seconds
    while True:
        response = client.request(
            "GET",
            "/api/plugins/grafana-slo-app/resources/v1/slo/"
            f"{urllib.parse.quote(slo_uuid, safe='')}",
        )
        item = remote_slo_item(response, slo_uuid)
        read_only = item.get("readOnly")
        status = read_only.get("status") if isinstance(read_only, dict) else None
        status_type = (
            str(status.get("type", "")).lower() if isinstance(status, dict) else ""
        )
        if status_type in {"created", "updated"}:
            return item
        if status_type == "error":
            raise RuntimeError(
                f"Grafana SLO {slo_uuid!r} entered error lifecycle state"
            )
        if monotonic() >= deadline:
            bounded_status = (
                status_type
                if status_type in {"creating", "updating", "deleting"}
                else "missing-or-unknown"
            )
            raise RuntimeError(
                f"Grafana SLO {slo_uuid!r} did not settle: {bounded_status}"
            )
        sleep(10)


def verify_remote_slo_contract(
    key: str,
    remote_slo: dict[str, Any],
    spec: dict[str, Any],
    prometheus_uid: str,
    alerting_enabled: bool,
    errors: list[str],
) -> None:
    """Verify the exact Terraform-owned SLO definition returned by Grafana."""
    if remote_slo.get("name") != spec["name"]:
        errors.append(f"Grafana SLO name mismatch for {key}")
    if remote_slo.get("description") != spec["description"]:
        errors.append(f"Grafana SLO description mismatch for {key}")

    objectives = remote_slo.get("objectives")
    if not isinstance(objectives, list) or len(objectives) != 1:
        errors.append(f"Grafana SLO must have exactly one objective for {key}")
    else:
        objective = objectives[0] if isinstance(objectives[0], dict) else {}
        try:
            remote_objective = float(objective.get("value"))
        except (TypeError, ValueError):
            remote_objective = math.nan
        if objective.get("window") != "30d" or not math.isclose(
            remote_objective,
            float(spec["objective"]),
            rel_tol=0,
            abs_tol=1e-9,
        ):
            errors.append(
                f"Grafana SLO objective mismatch for {key}: {objective!r}, "
                f"expected {spec['objective']} over 30d"
            )

    query = remote_slo.get("query")
    freeform = query.get("freeform") if isinstance(query, dict) else None
    if (
        not isinstance(query, dict)
        or query.get("type") != "freeform"
        or not isinstance(freeform, dict)
        or freeform.get("query") != spec["query"]
    ):
        errors.append(f"Grafana SLO exact freeform query mismatch for {key}")

    destination = remote_slo.get("destinationDatasource")
    if (
        not isinstance(destination, dict)
        or destination.get("uid") != prometheus_uid
        or destination.get("type") not in (None, "prometheus")
    ):
        errors.append(f"Grafana SLO destination datasource mismatch for {key}")
    folder = remote_slo.get("folder")
    if not isinstance(folder, dict) or folder.get("uid") != GRAFANA_SLO_FOLDER_UID:
        errors.append(f"Grafana SLO folder ownership mismatch for {key}")

    labels = strict_key_value_list(remote_slo.get("labels"), f"Grafana SLO labels {key}", errors)
    expected_labels = {
        "deployment_environment": "production",
        "owner": "nutsnews-observability",
        "service": str(spec["service"]),
    }
    if labels != expected_labels:
        errors.append(
            f"Grafana SLO labels mismatch for {key}: "
            f"observed keys={sorted(labels)!r}"
        )

    read_only = remote_slo.get("readOnly")
    status = read_only.get("status") if isinstance(read_only, dict) else None
    if not isinstance(read_only, dict) or read_only.get("provenance") != "terraform":
        errors.append(f"Grafana SLO is not Terraform-owned for {key}")
    if not isinstance(status, dict) or str(status.get("type", "")).lower() not in {
        "created",
        "updated",
    }:
        errors.append(f"Grafana SLO lifecycle is not settled for {key}")

    alerting = remote_slo.get("alerting")
    if not alerting_enabled:
        if alerting is not None:
            errors.append(f"shadow worker SLO unexpectedly has API alerting configuration")
        return
    if not isinstance(alerting, dict):
        errors.append(f"Grafana SLO is missing API alerting configuration for {key}")
        return

    alert_labels = strict_key_value_list(
        alerting.get("labels"), f"Grafana SLO alert labels {key}", errors
    )
    alert_annotations = strict_key_value_list(
        alerting.get("annotations"), f"Grafana SLO alert annotations {key}", errors
    )
    expected_alert_labels = {
        "deployment_environment": "production",
        "owner": "nutsnews-observability",
        "route": "operations-email",
        "service": str(spec["service"]),
    }
    expected_alert_annotations = {
        "summary": f"{spec['name']} error budget burn requires operator attention.",
        "dashboard_url": str(spec["dashboard_url"]),
        "runbook_url": GRAFANA_OBSERVABILITY_RUNBOOK_URL,
    }
    if alert_labels != expected_alert_labels:
        errors.append(f"Grafana SLO alert labels mismatch for {key}")
    if alert_annotations != expected_alert_annotations:
        errors.append(f"Grafana SLO alert annotations mismatch for {key}")
    for field, severity in (("fastBurn", "critical"), ("slowBurn", "warning")):
        burn = alerting.get(field)
        burn_labels = strict_key_value_list(
            burn.get("labels") if isinstance(burn, dict) else None,
            f"Grafana SLO {field} labels {key}",
            errors,
        )
        if burn_labels != {"severity": severity}:
            errors.append(f"Grafana SLO {field} severity mismatch for {key}")


def generated_rule_slo_uuid(rule: dict[str, Any]) -> str:
    labels = rule.get("labels")
    return str(labels.get("grafana_slo_uuid", "")) if isinstance(labels, dict) else ""


def generated_slo_burn_windows(rule: dict[str, Any]) -> set[str]:
    """Extract the canonical Grafana SLO burn windows from a generated query."""
    query = rule.get("query")
    if not isinstance(query, str):
        return set()
    return set(
        re.findall(r"grafana_slo_sli_(5m|30m|1h|2h|6h|1d|3d)\b", query)
    )


def verify_recorded_slo_samples(
    client: GrafanaClient,
    prometheus_uid: str,
    key: str,
    slo_uuid: str,
    objective: float,
    require_query_data: bool,
    errors: list[str],
    *,
    require_samples: bool = True,
    timeout_seconds: int = 120,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Poll exact generated SLI/objective metrics until their contract is stable.

    Enabled SLOs must publish one finite series per generated family. The worker
    SLO may truthfully remain absent while its protected burn-alert/cutover switch
    is off; any series that is present must still satisfy the exact contract.
    """
    deadline = monotonic() + max(0, timeout_seconds)
    while True:
        results: dict[str, Any] = {}
        attempt_errors: list[str] = []
        for metric in GRAFANA_SLO_RECORDED_METRICS:
            query = f'{metric}{{grafana_slo_uuid="{slo_uuid}"}}'
            result = safe_check(
                f"Grafana SLO recorded metric {key}/{metric}",
                lambda query=query: prometheus_query(client, prometheus_uid, query),
                attempt_errors,
                {
                    "query": query,
                    "status": "error",
                    "result_count": 0,
                    "sample_values": [],
                    "series_labels": [],
                },
            )
            results[metric] = result

        validation_errors: list[str] = []
        if require_query_data:
            for metric, result in results.items():
                values = result.get("sample_values", [])
                labels = result.get("series_labels", [])
                result_count = result.get("result_count")
                if not require_samples and result_count == 0 and not values and not labels:
                    continue
                if result_count != 1 or len(values) != 1 or len(labels) != 1:
                    validation_errors.append(
                        "Grafana SLO recorded metric must have exactly one series for "
                        f"{key}/{metric}"
                    )
                    continue
                if labels[0].get("grafana_slo_uuid") != slo_uuid:
                    validation_errors.append(
                        f"Grafana SLO recorded metric UUID mismatch for {key}/{metric}"
                    )
                value = values[0]
                if metric == "grafana_slo_objective":
                    if not math.isclose(value, objective, rel_tol=0, abs_tol=1e-9):
                        validation_errors.append(
                            f"Grafana SLO recorded objective mismatch for {key}"
                        )
                elif value < 0 or value > 1:
                    validation_errors.append(
                        "Grafana SLO recorded SLI is outside [0,1] for "
                        f"{key}/{metric}: {value}"
                    )

            if not require_samples:
                sli_counts = {
                    results[metric].get("result_count", 0)
                    for metric in GRAFANA_SLO_RECORDED_METRICS
                    if metric != "grafana_slo_objective"
                }
                if not sli_counts.issubset({0, 1}) or len(sli_counts) > 1:
                    validation_errors.append(
                        "dashboard-only worker SLO has a partial generated SLI sample set"
                    )

        if not attempt_errors and not validation_errors:
            return results
        if not require_query_data or not require_samples or monotonic() >= deadline:
            errors.extend(attempt_errors)
            errors.extend(validation_errors)
            return results
        sleep(10)


def rule_definition_fingerprint(definition: dict[str, Any]) -> str:
    """Return the catalog's deterministic SHA-256 rule-definition evidence."""
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_external_rule_inventory(
    catalog: dict[str, Any],
    provisioned_rules: dict[str, dict[str, Any]],
    ruler_rules_by_uid: dict[str, dict[str, Any]],
    health: dict[str, dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Validate vendor rule identity, kind, context, definition baseline, and health."""
    folder_uid = str(catalog.get("folderUid", ""))
    marker = catalog.get("managedByLabel", {})
    marker_key = str(marker.get("key", "")) if isinstance(marker, dict) else ""
    marker_value = str(marker.get("value", "")) if isinstance(marker, dict) else ""
    context = catalog.get("contextPolicy", {})
    required_alert_labels = set(context.get("requiredAlertLabels", []))
    required_alert_annotations = set(context.get("requiredAlertAnnotations", []))
    required_alert_label_values = context.get("requiredAlertLabelValues", {})
    required_alert_annotation_values = context.get(
        "requiredAlertAnnotationValues", {}
    )
    severity_normalization = context.get("severityNormalization", {})
    normalization_status = str(context.get("normalizationStatus", ""))
    if normalization_status != "approved":
        errors.append(
            "integration alert normalization is an explicit rollout blocker: "
            f"{normalization_status or 'missing'}"
        )
    if not isinstance(required_alert_label_values, dict) or not isinstance(
        required_alert_annotation_values, dict
    ):
        errors.append("integration alert normalization values are invalid")
        required_alert_label_values = {}
        required_alert_annotation_values = {}
    if not isinstance(severity_normalization, dict):
        errors.append("integration alert severity normalization is invalid")
        severity_normalization = {}
    fingerprint_policy = catalog.get("definitionFingerprintPolicy", {})
    baseline_status = str(fingerprint_policy.get("baselineStatus", ""))
    if fingerprint_policy.get("algorithm") != "sha256":
        errors.append("integration definition fingerprint policy must use sha256")
    if fingerprint_policy.get("requiredDisposition") != "retain":
        errors.append("integration definition fingerprints must be required for retained rules")
    if baseline_status not in {"pending_authenticated_rollout", "approved"}:
        errors.append("integration definition fingerprint baseline status is invalid")
    elif baseline_status == "pending_authenticated_rollout":
        errors.append(
            "integration definition fingerprint baseline is pending authenticated "
            "operator review; use the observed hashes in this report to create and "
            "commit the approved retained-rule baseline"
        )
    expected_rules = {
        str(item.get("uid", "")): item
        for item in catalog.get("rules", [])
        if isinstance(item, dict) and item.get("uid")
    }
    retained = {
        uid: item
        for uid, item in expected_rules.items()
        if item.get("disposition") == "retain"
    }
    obsolete = {
        uid: item
        for uid, item in expected_rules.items()
        if item.get("disposition") == "remove_via_integration_upgrade"
    }
    replaced = {
        uid: item
        for uid, item in expected_rules.items()
        if item.get("disposition")
        == "replaced_by_terraform_normalized_equivalent"
    }
    upgrade_status = str(catalog.get("integrationUpgradeStatus", ""))
    if upgrade_status not in {
        "not_available_from_live_api",
        "completed_supported_integration_upgrade",
    }:
        errors.append("integration upgrade availability status is invalid")
    folder_rules = {
        uid: rule
        for uid, rule in provisioned_rules.items()
        if str(rule.get("folderUID") or rule.get("folderUid") or "") == folder_uid
    }
    unexpected = sorted(set(folder_rules) - set(expected_rules))
    if unexpected:
        errors.append(
            "integration folder contains unreviewed replacement rules; refresh the "
            f"source inventory before proceeding: {unexpected!r}"
        )

    inventory: list[dict[str, Any]] = []
    for uid, expected in sorted(expected_rules.items()):
        live = provisioned_rules.get(uid)
        ruler = ruler_rules_by_uid.get(uid, {})
        is_obsolete = uid in obsolete
        is_replaced = uid in replaced
        if not live:
            if is_obsolete:
                inventory.append(
                    {
                        "uid": uid,
                        "title": expected.get("title", ""),
                        "group": expected.get("group", ""),
                        "kind": expected.get("kind", ""),
                        "owner": catalog.get("owner", ""),
                        "source": catalog.get("source", ""),
                        "disposition": expected.get("disposition", ""),
                        "state": "removed-by-supported-integration-upgrade",
                    }
                )
                continue
            if is_replaced:
                inventory.append(
                    {
                        "uid": uid,
                        "title": expected.get("title", ""),
                        "group": expected.get("group", ""),
                        "kind": expected.get("kind", ""),
                        "owner": catalog.get("owner", ""),
                        "source": catalog.get("source", ""),
                        "disposition": expected.get("disposition", ""),
                        "replacement_uid": expected.get("replacementUid", ""),
                        "state": "disabled-after-reviewed-terraform-replacement",
                    }
                )
                continue
            errors.append(f"missing retained integration-owned rule UID: {uid}")
            continue

        if is_replaced:
            errors.append(
                f"replaced integration alert {uid} is still active; verify the exact "
                "Terraform-owned equivalent, then disable the vendor alert bundle "
                "through the protected integration configuration workflow"
            )
            inventory.append(
                {
                    "uid": uid,
                    "title": expected.get("title", ""),
                    "group": expected.get("group", ""),
                    "kind": expected.get("kind", ""),
                    "owner": catalog.get("owner", ""),
                    "source": catalog.get("source", ""),
                    "disposition": expected.get("disposition", ""),
                    "replacement_uid": expected.get("replacementUid", ""),
                    "state": "duplicate-vendor-alert-still-active",
                }
            )
            continue

        labels: dict[str, Any] = {}
        if isinstance(ruler.get("labels"), dict):
            labels.update(ruler["labels"])
        if isinstance(live.get("labels"), dict):
            labels.update(live["labels"])
        annotations: dict[str, Any] = {}
        if isinstance(ruler.get("annotations"), dict):
            annotations.update(ruler["annotations"])
        if isinstance(live.get("annotations"), dict):
            annotations.update(live["annotations"])

        live_folder = str(live.get("folderUID") or live.get("folderUid") or "")
        live_group = str(
            live.get("ruleGroup")
            or live.get("rule_group")
            or ruler.get("group")
            or ""
        )
        live_title = str(live.get("title") or ruler.get("name") or "")
        raw_kind = str(ruler.get("type", "")).lower()
        if raw_kind in {"alert", "alerting"}:
            live_kind = "alert"
        elif raw_kind in {"record", "recording"} or live.get("record"):
            live_kind = "recording"
        else:
            live_kind = "unknown"

        for actual, wanted, field in (
            (live_folder, folder_uid, "folder"),
            (live_group, str(expected.get("group", "")), "group"),
            (live_title, str(expected.get("title", "")), "title"),
            (live_kind, str(expected.get("kind", "")), "kind"),
        ):
            if actual != wanted:
                errors.append(
                    f"integration rule {uid} {field} drifted: {actual!r} != {wanted!r}"
                )
        if str(labels.get(marker_key, "")).lower() != marker_value.lower():
            errors.append(
                f"integration rule {uid} is missing the {marker_key} ownership marker"
            )

        query_material = ruler.get("query")
        if not query_material:
            data = live.get("data")
            query_material = data if isinstance(data, list) and data else None
        if not query_material:
            errors.append(f"integration rule {uid} has no query definition to fingerprint")

        rule_health = health.get(uid, {}) if live_kind == "alert" else ruler
        health_value = str(rule_health.get("health", "unknown")).lower()
        if live_kind == "alert" and health_value not in {"ok", "nodata"}:
            errors.append(
                f"integration alert {uid} is unhealthy: "
                f"{_bounded_rule_health(health_value)}"
            )
        if live_kind == "recording" and not is_obsolete and health_value != "ok":
            errors.append(
                f"integration recording rule {uid} is unhealthy: "
                f"{_bounded_rule_health(health_value)}"
            )

        if live_kind == "alert":
            missing_labels = sorted(required_alert_labels - set(labels))
            missing_annotations = sorted(required_alert_annotations - set(annotations))
            if missing_labels:
                errors.append(
                    f"integration alert {uid} is missing vendor context labels: {missing_labels!r}"
                )
            if missing_annotations:
                errors.append(
                    f"integration alert {uid} is missing vendor context annotations: "
                    f"{missing_annotations!r}"
                )
            for label, value in sorted(required_alert_label_values.items()):
                if labels.get(label) != value:
                    errors.append(
                        f"integration alert {uid} normalized label {label!r} "
                        "drifted from its approved value"
                    )
            for annotation, value in sorted(
                required_alert_annotation_values.items()
            ):
                if annotations.get(annotation) != value:
                    errors.append(
                        f"integration alert {uid} normalized annotation "
                        f"{annotation!r} drifted from its approved value"
                    )
            expected_severity = severity_normalization.get(expected.get("severity"))
            if not expected_severity:
                errors.append(
                    f"integration alert {uid} has no approved severity normalization"
                )
            elif labels.get("severity") != expected_severity:
                errors.append(
                    f"integration alert {uid} normalized severity drifted from "
                    "its approved mapping"
                )
            if not (live.get("noDataState") or live.get("no_data_state")):
                errors.append(f"integration alert {uid} has no explicit no-data behavior")
            if not (live.get("execErrState") or live.get("exec_err_state")):
                errors.append(f"integration alert {uid} has no explicit datasource-error behavior")

        definition = {
            "folder": live_folder,
            "group": live_group,
            "title": live_title,
            "kind": live_kind,
            "labels": labels,
            "annotations": annotations,
            "query": query_material,
            "for": live.get("for") or ruler.get("duration") or "0s",
            "no_data_state": live.get("noDataState") or live.get("no_data_state") or "",
            "error_state": live.get("execErrState") or live.get("exec_err_state") or "",
        }
        fingerprint = rule_definition_fingerprint(definition)
        expected_fingerprint = str(
            expected.get("definitionFingerprintSha256", "")
        ).lower()
        if not is_obsolete:
            if not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
                errors.append(
                    f"retained integration rule {uid} has no approved definition "
                    "fingerprint baseline; review the observed hash from this report "
                    "during authenticated rollout and commit it before claiming drift validation"
                )
                fingerprint_status = "pending-approved-baseline"
            elif fingerprint != expected_fingerprint:
                errors.append(
                    f"integration rule {uid} definition fingerprint drifted: "
                    f"{fingerprint} != {expected_fingerprint}"
                )
                fingerprint_status = "drifted"
            else:
                fingerprint_status = "matched-approved-baseline"
        else:
            fingerprint_status = "not-required-obsolete-upgrade-rule"
        inventory.append(
            {
                "uid": uid,
                "title": live_title,
                "group": live_group,
                "kind": live_kind,
                "owner": catalog.get("owner", ""),
                "source": catalog.get("source", ""),
                "disposition": expected.get("disposition", ""),
                "state": "pending-supported-upgrade" if is_obsolete else "retained",
                "health": health_value,
                "definition_fingerprint_sha256": fingerprint,
                "definition_fingerprint_status": fingerprint_status,
            }
        )

    retained_observed = [
        item for item in inventory if item.get("disposition") == "retain"
    ]
    if len(retained_observed) != catalog.get("expectedRetainedRuleCount"):
        errors.append(
            "retained integration-owned rule count mismatch: "
            f"{len(retained_observed)} != {catalog.get('expectedRetainedRuleCount')}"
        )
    observed_folder_count = len(folder_rules)
    legacy_count = catalog.get("legacyObservedRuleCount")
    alerts_disabled_count = catalog.get("expectedAlertsDisabledRuleCount")
    post_upgrade_count = catalog.get("expectedPostUpgradeRuleCount")
    if observed_folder_count == legacy_count and legacy_count != post_upgrade_count:
        errors.append(
            "integration folder still contains the vendor alert bundle; complete the "
            "protected Terraform-equivalence migration and configurable-alert disable"
        )
    elif observed_folder_count == alerts_disabled_count:
        if upgrade_status != "not_available_from_live_api":
            errors.append(
                "integration folder retains obsolete recording rules after the catalog "
                "says the supported Linux integration upgrade completed"
            )
    elif observed_folder_count != post_upgrade_count:
        errors.append(
            "integration folder is not in the reviewed initial, alerts-disabled, or "
            f"supported post-upgrade shape: {observed_folder_count} not in "
            f"{sorted({legacy_count, alerts_disabled_count, post_upgrade_count})!r}"
        )
    retained_kind_counts = {
        kind: sum(item.get("kind") == kind for item in retained_observed)
        for kind in ("alert", "recording")
        if any(item.get("kind") == kind for item in retained_observed)
    }
    if retained_kind_counts != catalog.get("expectedPostUpgradeKindCounts"):
        errors.append(
            "retained integration rule kind counts drifted: "
            f"{retained_kind_counts!r} != {catalog.get('expectedPostUpgradeKindCounts')!r}"
        )
    return inventory


def terraform_output_value(outputs: dict[str, Any], name: str) -> Any:
    item = outputs.get(name, {})
    return item.get("value") if isinstance(item, dict) else None


def validate_synthetic_execution_guardrail(
    synthetic_execution_guardrail: Any,
    errors: list[str],
) -> None:
    """Require the reviewed absolute monthly Synthetic Monitoring ceiling."""
    if synthetic_execution_guardrail != SYNTHETIC_API_EXECUTION_CEILING_MONTHLY:
        errors.append(
            "Terraform synthetic execution guardrail must remain exactly 90,000 "
            "monthly API executions"
        )


def validate_worker_runtime_identity_rollout(
    host_expected_active: float,
    deployment_services: set[str],
    build_services: set[str],
    errors: list[str],
) -> str:
    """Require visible release/deployment identity in both shadow and production."""
    unknown_deployment = sorted(deployment_services - WORKER_SERVICES)
    unknown_build = sorted(build_services - WORKER_SERVICES)
    if unknown_deployment:
        errors.append(
            "worker deployment-info contains unapproved services: "
            f"{unknown_deployment!r}"
        )
    if unknown_build:
        errors.append(
            f"worker build-info contains unapproved services: {unknown_build!r}"
        )

    missing_deployment = sorted(WORKER_SERVICES - deployment_services)
    missing_build = sorted(WORKER_SERVICES - build_services)
    if missing_deployment:
        errors.append(
            "worker ownership requires deployment-info for all eight services in shadow "
            f"and production; missing {missing_deployment!r}"
        )
    if missing_build:
        errors.append(
            "worker ownership requires immutable build identity for all eight services in "
            f"shadow and production; missing {missing_build!r}"
        )
    return (
        "production-runtime-v1-required"
        if host_expected_active == 1
        else "shadow-runtime-identity-visible"
    )


def validate_deployed_worker_identities(
    identity_labels: list[dict[str, Any]], errors: list[str]
) -> dict[str, dict[str, str]]:
    """Require one host-verified immutable running-image identity per worker."""
    identities: dict[str, dict[str, str]] = {}
    for labels in identity_labels:
        service = str(labels.get("worker_service", "")).strip()
        if service in identities:
            errors.append(f"duplicate deployed worker identity for {service!r}")
            continue
        identities[service] = {
            "service_version": str(labels.get("service_version", "")).strip(),
            "revision": str(labels.get("revision", "")).strip(),
            "image_digest": str(labels.get("image_digest", "")).strip(),
        }

    observed_services = set(identities)
    unknown_services = sorted(observed_services - WORKER_SERVICES)
    missing_services = sorted(WORKER_SERVICES - observed_services)
    if unknown_services:
        errors.append(
            f"deployed worker identity contains unapproved services: {unknown_services!r}"
        )
    if missing_services:
        errors.append(
            "host-verified deployed identity must cover all eight worker services; "
            f"missing {missing_services!r}"
        )

    for service, identity in sorted(identities.items()):
        service_version = identity["service_version"]
        revision = identity["revision"]
        image_digest = identity["image_digest"]
        if not service_version or service_version.lower() == "unknown":
            errors.append(
                f"deployed worker identity has no service version for {service!r}"
            )
        if not revision or revision.lower() == "unknown":
            errors.append(
                f"deployed worker identity has no immutable revision for {service!r}"
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
            errors.append(
                "deployed worker identity must contain an immutable sha256 image digest for "
                f"{service!r}: {image_digest!r}"
            )
    return identities


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-query-data", action="store_true")
    parser.add_argument("--loki-hours", type=int, default=6)
    parser.add_argument("--terraform-outputs", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verification_started = time.time()
    url = raw_env("TF_VAR_grafana_url", "GRAFANA_URL")
    token = env("TF_VAR_grafana_service_account_token", "GRAFANA_SERVICE_ACCOUNT_TOKEN")
    prometheus_uid = env("TF_VAR_prometheus_datasource_uid", "GRAFANA_PROMETHEUS_DATASOURCE_UID")
    loki_uid = env("TF_VAR_loki_datasource_uid", "GRAFANA_LOKI_DATASOURCE_UID")
    usage_uid = env("TF_VAR_usage_datasource_uid", "GRAFANA_USAGE_DATASOURCE_UID")
    synthetic_monitoring_url = raw_env("GRAFANA_SM_URL")
    synthetic_monitoring_token = env("GRAFANA_SM_ACCESS_TOKEN")
    desired_synthetic_checks_raw = env(
        "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON", "TF_VAR_synthetic_http_checks"
    )

    required_env = {
        "TF_VAR_grafana_url": url,
        "TF_VAR_grafana_service_account_token": token,
        "TF_VAR_prometheus_datasource_uid": prometheus_uid,
        "TF_VAR_loki_datasource_uid": loki_uid,
        "TF_VAR_usage_datasource_uid": usage_uid,
        "GRAFANA_SM_URL": synthetic_monitoring_url,
        "GRAFANA_SM_ACCESS_TOKEN": synthetic_monitoring_token,
        "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON": desired_synthetic_checks_raw,
    }
    missing = [name for name, value in required_env.items() if not value]
    if missing:
        print(f"Missing required environment values: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        synthetic_monitoring_origin = validate_synthetic_monitoring_url(
            synthetic_monitoring_url
        )
        desired_synthetic_checks = parse_desired_synthetic_checks(
            desired_synthetic_checks_raw
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    backend_catalog = load_json(BACKEND_CATALOG)
    worker_catalog = load_json(WORKER_UPLIFT_CATALOG)
    external_catalog = load_json(EXTERNAL_RULE_CATALOG)
    terraform_outputs = (
        load_json(args.terraform_outputs)
        if args.terraform_outputs and args.terraform_outputs.exists()
        else {}
    )
    slo_state = terraform_output_value(terraform_outputs, "slo_uuids")
    synthetic_state = terraform_output_value(terraform_outputs, "synthetic_check_ids")
    synthetic_probe_state = terraform_output_value(
        terraform_outputs, "synthetic_probe_selection"
    )
    worker_slo_alerting_state = terraform_output_value(
        terraform_outputs, "worker_terminal_slo_alerting_enabled"
    )
    rollout_decision_enforcement_state = terraform_output_value(
        terraform_outputs, "enforce_rollout_decisions"
    )
    synthetic_major_acknowledgment_state = terraform_output_value(
        terraform_outputs, "synthetic_major_forecast_acknowledged"
    )
    linux_replacement_state = terraform_output_value(
        terraform_outputs, "linux_integration_alert_replacement_uids"
    )
    client = GrafanaClient(url, token)
    synthetic_monitoring_client = SyntheticMonitoringClient(
        synthetic_monitoring_origin, synthetic_monitoring_token
    )
    errors: list[str] = []

    backend_dashboard_uids = {item["uid"] for item in backend_catalog["dashboards"]}
    worker_dashboard_uids = {item["uid"] for item in worker_catalog["dashboards"]}
    backend_alert_uids = {item["uid"] for item in backend_catalog["alerts"]}
    worker_alert_uids = {item["uid"] for item in worker_catalog["alerts"]}
    expected_alert_uids = VPS_ALERT_UIDS | backend_alert_uids | worker_alert_uids

    folders = {}
    for uid in ("nutsnews-observability", backend_catalog["folder"]["uid"]):
        response = safe_check(
            f"folder {uid}",
            lambda uid=uid: client.request("GET", f"/api/folders/{urllib.parse.quote(uid)}"),
            errors,
            {},
        )
        if isinstance(response, dict):
            folders[uid] = response.get("title", "")

    dashboards = {}
    for uid in sorted(VPS_DASHBOARD_UIDS | backend_dashboard_uids | worker_dashboard_uids):
        response = safe_check(
            f"dashboard {uid}",
            lambda uid=uid: client.request("GET", f"/api/dashboards/uid/{urllib.parse.quote(uid)}"),
            errors,
            {},
        )
        if isinstance(response, dict):
            dashboards[uid] = response.get("dashboard", {}).get("title", "")

    provisioning_response = safe_check(
        "provisioned alert rules",
        lambda: client.request("GET", "/api/v1/provisioning/alert-rules"),
        errors,
        [],
    )
    provisioned_rules = {rule_uid(rule): rule for rule in list_items(provisioning_response) if rule_uid(rule)}
    managed_alerts = {}
    for uid in sorted(expected_alert_uids):
        rule = provisioned_rules.get(uid)
        if not rule:
            errors.append(f"missing alert rule UID: {uid}")
            continue
        labels = rule.get("labels", {}) if isinstance(rule.get("labels", {}), dict) else {}
        annotations = rule.get("annotations", {}) if isinstance(rule.get("annotations", {}), dict) else {}
        missing_labels = sorted(REQUIRED_ALERT_LABELS - set(labels))
        missing_annotations = sorted(REQUIRED_ALERT_ANNOTATIONS - set(annotations))
        if missing_labels:
            errors.append(f"alert {uid} missing labels: {', '.join(missing_labels)}")
        if missing_annotations:
            errors.append(f"alert {uid} missing annotations: {', '.join(missing_annotations)}")
        managed_alerts[uid] = {
            "title": rule.get("title", ""),
            "folder_uid": rule.get("folderUID") or rule.get("folderUid") or "",
            "rule_group": rule.get("ruleGroup") or rule.get("rule_group") or "",
            "labels_complete": not missing_labels,
            "annotations_complete": not missing_annotations,
        }
        if uid in VPS_ALERT_UIDS:
            ownership = (managed_alerts[uid]["folder_uid"], managed_alerts[uid]["rule_group"])
            if ownership not in VPS_ALERT_RULE_GROUPS:
                errors.append(
                    f"VPS alert {uid} has unexpected folder/group ownership: {ownership!r}"
                )

    for expected in LINUX_ALERT_REPLACEMENTS:
        uid = str(expected["replacementUid"])
        rule = provisioned_rules.get(uid)
        if not rule:
            continue
        labels = rule.get("labels", {}) if isinstance(rule.get("labels"), dict) else {}
        annotations = (
            rule.get("annotations", {})
            if isinstance(rule.get("annotations"), dict)
            else {}
        )
        query_data = rule.get("data", []) if isinstance(rule.get("data"), list) else []
        query_model = (
            query_data[0].get("model", {})
            if query_data and isinstance(query_data[0], dict)
            else {}
        )
        expected_context = {
            "service_namespace": "nutsnews",
            "deployment_environment": "production",
            "managed_by": "nutsnews-infra",
            "owner": "nutsnews-observability",
            "route": "operations-email",
            "service": "vps-host",
            "severity": expected["normalizedSeverity"],
            "source_integration": "linux-node",
        }
        expected_annotations = {
            "summary": expected["summary"],
            "description": expected["description"],
            "dashboard_url": "/d/nutsnews-vps-overview",
            "runbook_url": GRAFANA_OBSERVABILITY_RUNBOOK_URL,
        }
        if any(labels.get(key) != value for key, value in expected_context.items()):
            errors.append(f"Linux integration replacement {uid} context drifted")
        if any(
            annotations.get(key) != value
            for key, value in expected_annotations.items()
        ):
            errors.append(f"Linux integration replacement {uid} annotations drifted")
        if (
            rule.get("title") != expected["title"]
            or rule.get("condition") != expected["condition"]
            or rule.get("for") != expected["for"]
            or rule.get("noDataState") != expected["noDataState"]
            or rule.get("execErrState") != expected["execErrState"]
            or query_model.get("expr") != expected["expr"]
        ):
            errors.append(
                f"Linux integration replacement {uid} definition drifted from "
                "the reviewed vendor equivalent"
            )

    ruler_response = safe_check(
        "alert rule health",
        lambda: client.request("GET", "/api/prometheus/grafana/api/v1/rules?type=alert"),
        errors,
        {},
    )
    health = ruler_health(ruler_response)
    all_ruler_response = safe_check(
        "all recording and alert rules",
        lambda: client.request("GET", "/api/prometheus/grafana/api/v1/rules"),
        errors,
        {},
    )
    all_generated_rules = ruler_rules(all_ruler_response)
    all_ruler_rules_by_uid = {
        rule_uid(rule): rule for rule in all_generated_rules if rule_uid(rule)
    }
    for uid in sorted(expected_alert_uids):
        item = health.get(uid)
        if not item:
            errors.append(f"alert rule health missing for UID: {uid}")
        elif str(item["health"]).lower() not in {"ok", "nodata"}:
            errors.append(
                f"alert rule {uid} is unhealthy: "
                f"{_bounded_rule_health(item['health'])}"
            )

    active_alerts_response = safe_check(
        "active Grafana alerts",
        lambda: client.request(
            "GET",
            "/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false",
        ),
        errors,
        [],
    )
    datasource_alerts = []
    for alert in list_items(active_alerts_response):
        labels = alert.get("labels", {}) if isinstance(alert.get("labels", {}), dict) else {}
        alert_name = labels.get("alertname", "")
        if alert_name in {"DatasourceNoData", "DatasourceError"}:
            datasource_alerts.append({"alertname": alert_name, "labels": labels})
    if datasource_alerts:
        errors.append(f"unexpected datasource-generated alerts are active: {len(datasource_alerts)}")

    contact_response = safe_check(
        "contact points",
        lambda: client.request("GET", "/api/v1/provisioning/contact-points"),
        errors,
        [],
    )
    contact_points = summarize_contact_points(contact_response, errors)
    policy_response = safe_check(
        "notification policy",
        lambda: client.request("GET", "/api/v1/provisioning/policies"),
        errors,
        {},
    )
    policy = verify_notification_policy(policy_response, errors)

    external_inventory = validate_external_rule_inventory(
        external_catalog,
        provisioned_rules,
        all_ruler_rules_by_uid,
        health,
        errors,
    )

    remote_slo_response = safe_check(
        "Grafana SLO resource API",
        lambda: client.request("GET", "/api/plugins/grafana-slo-app/resources/v1/slo"),
        errors,
        [],
    )
    remote_slos = list_items(remote_slo_response)
    slo_verification: dict[str, Any] = {}
    state_slos = slo_state if isinstance(slo_state, dict) else {}
    for key, spec in EXPECTED_SLO_SPECS.items():
        alerting_enabled = (
            bool(worker_slo_alerting_state)
            if key == "worker_terminal_success"
            else bool(spec["alerting_enabled"])
        )
        slo_uuid = str(state_slos.get(key, ""))
        candidates = [item for item in remote_slos if item.get("uuid") == slo_uuid]
        if len(candidates) != 1:
            errors.append(
                f"Grafana SLO API must return exactly one managed SLO for {key}: "
                f"observed {len(candidates)}"
            )
            remote_slo: dict[str, Any] = {}
        else:
            remote_slo = safe_check(
                f"Grafana SLO settled resource {key}",
                lambda slo_uuid=slo_uuid: wait_for_remote_slo(client, slo_uuid),
                errors,
                candidates[0],
            )
        verify_remote_slo_contract(
            key,
            remote_slo,
            spec,
            prometheus_uid,
            alerting_enabled,
            errors,
        )

        generated = [
            rule
            for rule in all_generated_rules
            if slo_uuid and generated_rule_slo_uuid(rule) == slo_uuid
        ]
        recording_rules = [
            rule
            for rule in generated
            if str(rule.get("type", "")).lower() in {"recording", "record"}
        ]
        alert_rules = [
            rule
            for rule in generated
            if str(rule.get("type", "")).lower() in {"alerting", "alert"}
        ]
        if len(recording_rules) < 10:
            errors.append(
                f"Grafana SLO generated fewer than ten recording rules for {key}: "
                f"{len(recording_rules)}"
            )
        for rule in recording_rules + alert_rules:
            if str(rule.get("health", "")).lower() != "ok":
                errors.append(
                    f"Grafana SLO generated rule is not healthy for {key}: "
                    f"{rule.get('name', '')}={rule.get('health', 'unknown')}"
                )
        if alerting_enabled and len(alert_rules) < 2:
            errors.append(
                f"Grafana SLO is missing generated fast/slow burn alerts for {key}"
            )
        if not alerting_enabled and alert_rules:
            errors.append(
                f"shadow worker SLO unexpectedly has generated burn alerts: {len(alert_rules)}"
            )
        generated_severities: set[str] = set()
        for rule in alert_rules:
            labels = rule.get("labels", {}) if isinstance(rule.get("labels"), dict) else {}
            annotations = (
                rule.get("annotations", {})
                if isinstance(rule.get("annotations"), dict)
                else {}
            )
            missing_labels = sorted(REQUIRED_ALERT_LABELS - set(labels))
            missing_annotations = sorted(REQUIRED_ALERT_ANNOTATIONS - set(annotations))
            if missing_labels:
                errors.append(
                    f"Grafana SLO alert for {key} is missing labels: {missing_labels!r}"
                )
            if missing_annotations:
                errors.append(
                    f"Grafana SLO alert for {key} is missing annotations: "
                    f"{missing_annotations!r}"
                )
            if labels.get("grafana_slo_uuid") != slo_uuid:
                errors.append(f"Grafana SLO alert UUID label mismatch for {key}")
            builtin_severity = str(labels.get("grafana_slo_severity", ""))
            if builtin_severity:
                generated_severities.add(builtin_severity)
            expected_windows = {
                "critical": {"5m", "30m", "1h", "6h"},
                "warning": {"2h", "6h", "1d", "3d"},
            }.get(builtin_severity)
            if expected_windows is not None:
                observed_windows = generated_slo_burn_windows(rule)
                if observed_windows != expected_windows:
                    errors.append(
                        f"Grafana SLO {builtin_severity} burn-window query mismatch for "
                        f"{key}: observed {sorted(observed_windows)!r}"
                    )
        if alerting_enabled and not {"critical", "warning"}.issubset(
            generated_severities
        ):
            errors.append(
                f"Grafana SLO generated alerts lack critical/warning burn severities for {key}"
            )

        recorded_samples_required = not (
            key == "worker_terminal_success" and not alerting_enabled
        )
        recorded_samples = verify_recorded_slo_samples(
            client,
            prometheus_uid,
            key,
            slo_uuid,
            float(spec["objective"]),
            args.require_query_data,
            errors,
            require_samples=recorded_samples_required,
        )
        recorded_sli_counts = {
            metric: recorded_samples.get(metric, {}).get("result_count", 0)
            for metric in GRAFANA_SLO_RECORDED_METRICS
            if metric != "grafana_slo_objective"
        }
        if not args.require_query_data:
            recorded_sample_state = "not-evaluated"
        elif recorded_samples_required:
            recorded_sample_state = "required-finite-samples"
        elif all(count == 0 for count in recorded_sli_counts.values()):
            recorded_sample_state = "dashboard-only-no-terminal-events"
        else:
            recorded_sample_state = "dashboard-only-recorded-samples-visible"
        slo_verification[key] = {
            "uuid": slo_uuid,
            "remote_name": remote_slo.get("name", ""),
            "objective": remote_slo.get("objectives", []),
            "recording_rule_count": len(recording_rules),
            "alert_rule_count": len(alert_rules),
            "recorded_sample_state": recorded_sample_state,
            "recorded_samples": recorded_samples,
        }

    prometheus = {}
    for name, (query, minimum) in PROMETHEUS_QUERIES.items():
        prometheus[name] = safe_check(
            f"Prometheus query {name}",
            lambda query=query: prometheus_query(client, prometheus_uid, query),
            errors,
            {"query": query, "status": "error", "result_count": 0, "sample_values": []},
        )
        if (
            args.require_query_data
            and name not in RELAY_CONDITIONAL_QUERIES
            and prometheus[name]["result_count"] < minimum
        ):
            errors.append(f"Prometheus query returned insufficient data: {name}")
        if (
            args.require_query_data
            and name not in RELAY_CONDITIONAL_QUERIES
            and len(prometheus[name].get("sample_values", [])) < minimum
        ):
            errors.append(f"Prometheus query returned insufficient numeric samples: {name}")
        if args.require_query_data and len(
            prometheus[name].get("sample_values", [])
        ) != prometheus[name].get("result_count", 0):
            errors.append(
                f"Prometheus query contains invalid or non-finite samples: {name}"
            )
        values = prometheus[name].get("sample_values", [])
        if (
            args.require_query_data
            and name not in RELAY_CONDITIONAL_QUERIES
            and name in EXPECTED_ONE_SAMPLE_QUERIES
            and any(value != 1 for value in values)
        ):
            errors.append(f"Prometheus health samples must equal one: {name}={values!r}")
        if (
            args.require_query_data
            and name not in RELAY_CONDITIONAL_QUERIES
            and name in EXPECTED_ZERO_SAMPLE_QUERIES
            and any(value != 0 for value in values)
        ):
            errors.append(f"Prometheus health samples must equal zero: {name}={values!r}")
        if (
            args.require_query_data
            and name == "backend_health_audit_expected_interval"
            and any(value != 86400 for value in values)
        ):
            errors.append(
                "backend health-audit expected interval must equal 86,400 seconds: "
                f"{values!r}"
            )
        if (
            args.require_query_data
            and name == "backend_caddy_upstream_health_state"
            and any(value not in {-1, 0, 1} for value in values)
        ):
            errors.append(
                "Caddy upstream health must be healthy, unhealthy, or explicitly disabled: "
                f"{values!r}"
            )

    relay_status = "unknown"
    if args.require_query_data:
        expected_alloy_targets = {
            ("integrations/nutsnews-vps-alloy", "vps.nutsnews.com"),
            ("nutsnews-backend-alloy", "backend.nutsnews.com"),
        }
        for name in (
            "alloy_remote_write_pending_families",
            "alloy_remote_write_failure_families",
            "alloy_loki_drop_families",
            "alloy_loki_retry_families",
        ):
            labels = prometheus[name].get("series_labels", [])
            targets = {
                (str(item.get("job", "")), str(item.get("instance", "")))
                for item in labels
                if isinstance(item, dict)
            }
            values = prometheus[name].get("sample_values", [])
            if targets != expected_alloy_targets:
                errors.append(
                    f"Alloy internal family {name} must cover exactly the VPS and backend "
                    f"self targets: {sorted(targets)!r}"
                )
            if not values or any(value < 0 for value in values):
                errors.append(
                    f"Alloy internal family {name} has missing, invalid, or negative "
                    f"current values: {values!r}"
                )

        rabbitmq_jobs = {
            str(labels.get("job", ""))
            for labels in prometheus["backend_rabbitmq"].get("series_labels", [])
            if isinstance(labels, dict)
        }
        if (
            prometheus["backend_rabbitmq"].get("result_count", 0) != 2
            or rabbitmq_jobs != {"nutsnews-rabbitmq", "nutsnews-rabbitmq-queues"}
        ):
            errors.append(
                "RabbitMQ must expose exactly one healthy core scrape and one healthy "
                f"detailed-queue scrape: {sorted(rabbitmq_jobs)!r}"
            )
        for name, expected_queues in RABBITMQ_QUEUE_FAMILY_EXPECTATIONS.items():
            labels = prometheus[name].get("series_labels", [])
            observed_queues = [
                str(item.get("queue", ""))
                for item in labels
                if isinstance(item, dict)
            ]
            values = prometheus[name].get("sample_values", [])
            if (
                prometheus[name].get("result_count", 0) != len(expected_queues)
                or len(observed_queues) != len(expected_queues)
                or set(observed_queues) != expected_queues
                or len(set(observed_queues)) != len(observed_queues)
            ):
                errors.append(
                    f"RabbitMQ queue family {name} must expose exactly one current series "
                    f"for each approved queue: observed={sorted(observed_queues)!r}"
                )
            if len(values) != len(expected_queues) or any(value < 0 for value in values):
                errors.append(
                    f"RabbitMQ queue family {name} contains missing, invalid, or negative "
                    f"current values: {values!r}"
                )

    if args.require_query_data:
        relay_status_labels = prometheus["backend_sync_relay_status"].get(
            "series_labels", []
        )
        relay_statuses = {
            str(labels.get("status", ""))
            for labels in relay_status_labels
            if isinstance(labels, dict)
        }
        if len(relay_status_labels) != 1 or len(relay_statuses) != 1:
            errors.append(
                "sync relay must expose exactly one bounded configuration status: "
                f"{sorted(relay_statuses)!r}"
            )
            relay_status = "unknown"
        else:
            relay_status = next(iter(relay_statuses))
        relay_expected_active_values = prometheus[
            "backend_sync_relay_expected_active"
        ].get("sample_values", [])
        if (
            prometheus["backend_sync_relay_expected_active"].get("result_count") != 1
            or len(relay_expected_active_values) != 1
            or relay_expected_active_values[0] not in {0, 1}
        ):
            errors.append(
                "sync relay must expose exactly one bounded expected-active signal: "
                f"{relay_expected_active_values!r}"
            )
            relay_expected_active = None
        else:
            relay_expected_active = relay_expected_active_values[0]
        if relay_status == "not_configured":
            if relay_expected_active != 0:
                errors.append(
                    "disabled sync relay must expose expected_active=0: "
                    f"{relay_expected_active_values!r}"
                )
            unexpected_relay_data = sorted(
                name
                for name in RELAY_CONDITIONAL_QUERIES
                if prometheus[name].get("result_count", 0) != 0
            )
            if unexpected_relay_data:
                errors.append(
                    "disabled sync relay emitted configured-only healthy samples: "
                    f"{unexpected_relay_data!r}"
                )
        elif relay_status == "pass":
            if relay_expected_active != 1:
                errors.append(
                    "configured sync relay must expose expected_active=1: "
                    f"{relay_expected_active_values!r}"
                )
            for name in sorted(RELAY_CONDITIONAL_QUERIES):
                values = prometheus[name].get("sample_values", [])
                if not values:
                    errors.append(f"configured sync relay has no healthy sample: {name}")
                if name in EXPECTED_ONE_SAMPLE_QUERIES and any(
                    value != 1 for value in values
                ):
                    errors.append(
                        f"configured sync relay health samples must equal one: "
                        f"{name}={values!r}"
                    )
                if name in EXPECTED_ZERO_SAMPLE_QUERIES and any(
                    value != 0 for value in values
                ):
                    errors.append(
                        f"configured sync relay failure samples must equal zero: "
                        f"{name}={values!r}"
                    )
        else:
            errors.append(f"sync relay has an unhealthy bounded status: {relay_status!r}")

    worker_rollout: dict[str, Any] = {
        "phase": "not-evaluated",
        "delivery_service_count": 0,
        "deployment_identity_services": [],
        "build_identity_services": [],
        "host_verified_deployed_identity_services": [],
    }
    if args.require_query_data:
        backend_build_labels = prometheus["backend_api_build_identity"].get(
            "series_labels", []
        )
        if not backend_build_labels:
            errors.append("backend API deployment identity is missing")
        for labels in backend_build_labels:
            service_version = str(labels.get("service_version", "")).strip()
            revision = str(labels.get("revision", "")).strip()
            if (
                not service_version
                or not revision
                or revision.lower() == "unknown"
            ):
                errors.append(
                    "backend API identity must contain a service version and immutable "
                    f"revision: service_version={service_version!r}, revision={revision!r}"
                )
        for name in (
            "worker_endpoints_identity",
            "worker_endpoints_up",
            "worker_endpoints_fresh",
            "worker_readiness_series",
        ):
            if prometheus[name]["result_count"] != len(WORKER_SERVICES):
                errors.append(
                    f"Prometheus query must return exactly eight worker services: {name}"
                )
            observed_services = {
                str(labels.get("service", ""))
                for labels in prometheus[name].get("series_labels", [])
            }
            if observed_services != WORKER_SERVICES:
                errors.append(
                    f"{name} service identities differ from the approved eight-service set: "
                    f"{sorted(observed_services)!r}"
                )
        stage_labels = prometheus["worker_stage_terminal_series"].get(
            "series_labels", []
        )
        observed_stage_pairs = {
            (str(labels.get("service", "")), str(labels.get("outcome", "")))
            for labels in stage_labels
            if isinstance(labels, dict)
        }
        expected_stage_pairs = {
            (service, outcome)
            for service in DELIVERY_WORKER_SERVICES
            for outcome in {
                "success",
                "duplicate",
                "invalid",
                "retry",
                "dlq",
                "failure",
            }
        }
        if (
            prometheus["worker_stage_terminal_series"]["result_count"] != 42
            or len(stage_labels) != 42
            or observed_stage_pairs != expected_stage_pairs
        ):
            errors.append(
                "worker lifecycle telemetry must expose exactly six seeded outcomes for "
                "each of the seven delivery services"
            )
        latency_labels = prometheus["worker_stage_latency_series"].get(
            "series_labels", []
        )
        latency_services = {
            str(labels.get("service", ""))
            for labels in latency_labels
            if isinstance(labels, dict)
        }
        if (
            prometheus["worker_stage_latency_series"]["result_count"] != 7
            or len(latency_labels) != 7
            or latency_services != DELIVERY_WORKER_SERVICES
        ):
            errors.append(
                "worker lifecycle latency telemetry must expose exactly one +Inf bucket "
                "for each of the seven delivery services"
            )
        host_expected_active_values = prometheus[
            "backend_worker_uplift_expected_active"
        ].get("sample_values", [])
        host_deployment_labels = prometheus[
            "backend_worker_uplift_deployment_info"
        ].get("series_labels", [])
        deployed_worker_identities = validate_deployed_worker_identities(
            prometheus["backend_worker_uplift_deployed_service_info"].get(
                "series_labels", []
            ),
            errors,
        )
        if len(host_expected_active_values) != 1 or host_expected_active_values[0] not in {
            0,
            1,
        }:
            errors.append(
                "protected host worker ownership must expose exactly one bounded "
                f"expected_active value: {host_expected_active_values!r}"
            )
            host_expected_active = math.nan
        else:
            host_expected_active = host_expected_active_values[0]
        host_modes = {
            str(labels.get("mode", ""))
            for labels in host_deployment_labels
            if isinstance(labels, dict)
        }
        if len(host_deployment_labels) != 1 or host_modes not in ({"shadow"}, {"production"}):
            errors.append(
                "protected host worker ownership must expose exactly one shadow/production "
                f"mode: {sorted(host_modes)!r}"
            )
            host_deployment_mode = ""
        else:
            host_deployment_mode = next(iter(host_modes))
        expected_mode_for_active = "production" if host_expected_active == 1 else "shadow"
        if host_deployment_mode and host_deployment_mode != expected_mode_for_active:
            errors.append(
                "protected host worker ownership mode/expected_active pairing is invalid: "
                f"mode={host_deployment_mode!r}, expected_active={host_expected_active!r}"
            )
        if isinstance(worker_slo_alerting_state, bool) and worker_slo_alerting_state != (
            host_expected_active == 1
        ):
            errors.append(
                "protected worker terminal SLO alert switch disagrees with host ownership: "
                f"alerting={worker_slo_alerting_state!r}, "
                f"expected_active={host_expected_active!r}"
            )
        target_identity = {
            str(labels.get("service", "")): labels
            for labels in prometheus["worker_endpoints_identity"].get("series_labels", [])
        }
        emitted_expected_active = {
            str(labels.get("service", "")): value
            for labels, value in zip(
                prometheus["worker_expected_active_signal"].get("series_labels", []),
                prometheus["worker_expected_active_signal"].get("sample_values", []),
                strict=False,
            )
        }
        emitted_deployment = {
            str(labels.get("service", "")): str(labels.get("deployment", ""))
            for labels in prometheus["worker_deployment_info"].get("series_labels", [])
        }
        build_identity = {
            str(labels.get("service", "")): labels
            for labels in prometheus["worker_build_info"].get("series_labels", [])
        }
        if set(emitted_expected_active) != WORKER_SERVICES:
            errors.append("worker expected-active signals do not cover all eight services")
        readiness_ok_services = {
            str(labels.get("service", ""))
            for labels in prometheus["worker_readiness_ok"].get("series_labels", [])
        }
        active_services = {
            service for service, value in emitted_expected_active.items() if value == 1
        }
        missing_active_readiness = sorted(active_services - readiness_ok_services)
        if missing_active_readiness:
            errors.append(
                "production-owned workers must report readiness outcome=ok; missing "
                f"{missing_active_readiness!r}"
            )
        rollout_phase = validate_worker_runtime_identity_rollout(
            host_expected_active,
            set(emitted_deployment),
            set(build_identity),
            errors,
        )
        for service, labels in sorted(build_identity.items()):
            version = str(labels.get("version", "")).strip()
            revision = str(labels.get("revision", "")).strip()
            if not version or not revision or revision.lower() == "unknown":
                errors.append(
                    "worker build identity must contain a version and immutable revision in "
                    f"shadow and production for {service}: "
                    f"version={version!r}, revision={revision!r}"
                )
        for labels in prometheus["worker_deployment_info"].get(
            "series_labels", []
        ):
            service = str(labels.get("service", ""))
            deployment = str(labels.get("deployment", "")).strip()
            adapter = str(labels.get("adapter", "")).strip()
            if not deployment or not adapter:
                errors.append(
                    "worker deployment identity must contain deployment and adapter for "
                    f"{service}: deployment={deployment!r}, adapter={adapter!r}"
                )
        for service in sorted(WORKER_SERVICES):
            target = target_identity.get(service, {})
            expected_active_label = str(target.get("expected_active", ""))
            expected_active_value = emitted_expected_active.get(service)
            if expected_active_label not in {"0", "1"} or expected_active_value != float(
                expected_active_label or "nan"
            ):
                errors.append(
                    f"worker target/emitted expected_active mismatch for {service}: "
                    f"target={expected_active_label!r}, emitted={expected_active_value!r}"
                )
            if expected_active_label in {"0", "1"} and float(
                expected_active_label
            ) != host_expected_active:
                errors.append(
                    f"worker target/protected host expected_active mismatch for {service}: "
                    f"target={expected_active_label!r}, host={host_expected_active!r}"
                )
            deployment_mode = str(target.get("deployment_mode", ""))
            emitted_mode = emitted_deployment.get(service)
            if emitted_mode and emitted_mode != deployment_mode:
                errors.append(
                    f"worker target/emitted deployment mode mismatch for {service}: "
                    f"target={deployment_mode!r}, emitted={emitted_mode!r}"
                )
            if not deployment_mode:
                errors.append(f"worker target deployment mode is missing for {service}")
            if deployment_mode and deployment_mode != host_deployment_mode:
                errors.append(
                    f"worker target/protected host deployment mode mismatch for {service}: "
                    f"target={deployment_mode!r}, host={host_deployment_mode!r}"
                )
        worker_rollout = {
            "phase": rollout_phase,
            "host_expected_active": host_expected_active,
            "host_deployment_mode": host_deployment_mode,
            "delivery_service_count": len(DELIVERY_WORKER_SERVICES),
            "readiness_ok_services": sorted(readiness_ok_services),
            "deployment_identity_services": sorted(emitted_deployment),
            "build_identity_services": sorted(build_identity),
            "host_verified_deployed_identity_services": sorted(
                deployed_worker_identities
            ),
        }

    synthetic_inventory = safe_check(
        "live Synthetic Monitoring inventory",
        lambda: remote_synthetic_inventory(
            synthetic_monitoring_client,
            synthetic_state,
            synthetic_probe_state,
            desired_synthetic_checks,
            errors,
        ),
        errors,
        {
            "enabled_api_check_count": 0,
            "enabled_browser_check_count": 0,
            "monthly_api_execution_estimate": 0,
            "monthly_api_execution_ceiling": SYNTHETIC_API_EXECUTION_CEILING_MONTHLY,
            "execution_estimate_complete": False,
            "checks": [],
        },
    )

    if args.require_query_data:
        synthetic_queries, pending_synthetic_jobs = poll_current_synthetic_probe_results(
            client,
            prometheus_uid,
            verification_started,
            timeout_seconds=780,
        )
        if pending_synthetic_jobs:
            errors.append(
                "synthetic checks did not publish one fresh current two-probe config within "
                f"13 minutes: {pending_synthetic_jobs!r}"
            )
    else:
        synthetic_queries, _ = poll_current_synthetic_probe_results(
            client,
            prometheus_uid,
            verification_started - 600,
            timeout_seconds=0,
        )
    for name in sorted(EXPECTED_SYNTHETIC_CHECKS):
        result = synthetic_queries[name]
        if args.require_query_data and result.get("result_count") != 2:
            errors.append(f"synthetic check must have exactly two current probe series: {name}")
        if args.require_query_data and len(result.get("sample_values", [])) != result.get(
            "result_count", 0
        ):
            errors.append(f"synthetic check contains invalid or non-finite samples: {name}")
        observed_probes = {
            str(labels.get("probe", ""))
            for labels in result.get("series_labels", [])
            if isinstance(labels, dict) and labels.get("probe")
        }
        observed_config_versions = {
            str(labels.get("config_version", ""))
            for labels in result.get("series_labels", [])
            if isinstance(labels, dict) and labels.get("config_version")
        }
        if args.require_query_data and len(observed_probes) != 2:
            errors.append(f"synthetic check must report two distinct public probe labels: {name}")
        if args.require_query_data and len(observed_config_versions) != 1:
            errors.append(f"synthetic check must report exactly one current config_version: {name}")
        if args.require_query_data and any(
            value != 1 for value in result.get("sample_values", [])
        ):
            errors.append(f"synthetic check has an unhealthy probe sample: {name}")
        if args.require_query_data and any(
            timestamp < verification_started - 5
            for timestamp in result.get("sample_timestamps", [])
        ):
            errors.append(f"synthetic check retained only pre-apply probe samples: {name}")

    usage = {}
    for name, query in USAGE_QUERIES.items():
        usage[name] = safe_check(
            f"usage query {name}",
            lambda query=query: prometheus_query(client, usage_uid, query),
            errors,
            {"query": query, "status": "error", "result_count": 0, "sample_values": []},
        )
        if usage[name]["result_count"] < 1:
            errors.append(f"Grafana Cloud usage query returned no data: {name}")
        if not usage[name].get("sample_values"):
            errors.append(f"Grafana Cloud usage query returned no finite numeric samples: {name}")
        if len(usage[name].get("sample_values", [])) != usage[name].get(
            "result_count", 0
        ):
            errors.append(
                f"Grafana Cloud usage query contains invalid or non-finite samples: {name}"
            )
    if any(value < 0 for value in usage["metrics_active_series"].get("sample_values", [])):
        errors.append("Grafana Cloud active-series numerator must be nonnegative")
    if any(
        value >= 7000
        for value in usage["metrics_active_series"].get("sample_values", [])
    ):
        errors.append("Grafana Cloud active series must remain below the 7,000-series steady-state guardrail")
    if any(
        value <= 0
        for value in usage["metrics_active_series_limit"].get("sample_values", [])
    ):
        errors.append("Grafana Cloud max_global_series_per_user denominator must be positive")
    if any(
        value < 0
        for value in usage["metrics_active_series_ratio"].get("sample_values", [])
    ):
        errors.append("Grafana Cloud active-series ratio must be nonnegative")

    loki = {}
    for name, query in LOKI_QUERIES.items():
        loki[name] = safe_check(
            f"Loki query {name}",
            lambda query=query, name=name: loki_query_evidence(
                client,
                loki_uid,
                query,
                LOKI_QUERY_HOURS_OVERRIDES.get(name, args.loki_hours),
            ),
            errors,
            {
                "query": query,
                "status": "error",
                "result_count": 0,
                "line_count": 0,
                "stream_labels": [],
                "indexed_series_labels": [],
            },
        )
        if (
            args.require_query_data
            and loki_log_is_required(name, relay_status)
            and loki[name]["line_count"] < 1
        ):
            errors.append(f"Loki query returned no log lines: {name}")
        if loki[name].get("status") != "success":
            errors.append(f"Loki query-range status is not success: {name}")
        if loki[name].get("indexed_series_status") != "success":
            errors.append(f"Loki indexed-series status is not success: {name}")
        if loki[name]["line_count"] > 0 and not loki[name].get(
            "indexed_series_labels"
        ):
            errors.append(f"Loki returned lines without indexed-series evidence: {name}")
        validate_loki_indexed_labels(
            name,
            loki[name].get("indexed_series_labels", []),
            errors,
        )

    synthetic_execution_estimate = terraform_output_value(
        terraform_outputs, "synthetic_monthly_api_execution_estimate"
    )
    synthetic_execution_guardrail = terraform_output_value(
        terraform_outputs, "synthetic_monthly_api_execution_guardrail"
    )
    synthetic_execution_major_threshold = terraform_output_value(
        terraform_outputs, "synthetic_monthly_api_major_threshold"
    )
    contact_state = terraform_output_value(terraform_outputs, "operations_contact_point")
    if args.terraform_outputs:
        if set(synthetic_state or {}) != EXPECTED_SYNTHETIC_CHECKS:
            errors.append("Terraform state does not contain exactly the five approved synthetic checks")
        if not isinstance(synthetic_probe_state, dict) or len(synthetic_probe_state) != 2:
            errors.append("Terraform state does not contain exactly two resolved synthetic probes")
        elif any(
            not isinstance(probe, dict) or probe.get("public") is not True
            for probe in synthetic_probe_state.values()
        ):
            errors.append("Terraform state contains a non-public Synthetic Monitoring probe")
        if synthetic_execution_estimate != 86400:
            errors.append(
                "Terraform synthetic execution estimate must be 86,400 for five checks, "
                f"two probes, and a five-minute interval; observed {synthetic_execution_estimate!r}"
            )
        validate_synthetic_execution_guardrail(
            synthetic_execution_guardrail,
            errors,
        )
        if (
            not isinstance(synthetic_execution_guardrail, (int, float))
            or not isinstance(synthetic_execution_estimate, (int, float))
            or synthetic_execution_estimate >= synthetic_execution_guardrail
        ):
            errors.append("Terraform synthetic execution estimate must remain below the 90% guardrail")
        if synthetic_execution_major_threshold != 85000:
            errors.append(
                "Terraform synthetic major threshold must remain 85,000 for the current "
                f"100,000-execution allowance; observed {synthetic_execution_major_threshold!r}"
            )
        if rollout_decision_enforcement_state is not True:
            errors.append("production Terraform state disabled rollout-decision enforcement")
        if (
            isinstance(synthetic_execution_estimate, (int, float))
            and isinstance(synthetic_execution_major_threshold, (int, float))
            and synthetic_execution_estimate >= synthetic_execution_major_threshold
            and synthetic_major_acknowledgment_state is not True
        ):
            errors.append(
                "standing-major synthetic topology lacks its protected reviewed acknowledgment"
            )
        if set(slo_state or {}) != EXPECTED_SLOS:
            errors.append("Terraform state does not contain exactly the four approved Grafana SLOs")
        if not isinstance(worker_slo_alerting_state, bool):
            errors.append("Terraform state is missing the protected worker SLO alerting switch")
        if contact_state != CONTACT_POINT_NAME:
            errors.append("Terraform state contact point name does not match the managed operations contact")
        expected_linux_replacement_state = {
            str(item["sourceUid"]): str(item["replacementUid"])
            for item in LINUX_ALERT_REPLACEMENTS
        }
        if linux_replacement_state != expected_linux_replacement_state:
            errors.append(
                "Terraform state does not contain exactly the 24 reviewed Linux "
                "integration alert replacements"
            )

    report = {
        "status": "pass" if not errors else "fail",
        "folders": folders,
        "dashboard_count": len(dashboards),
        "managed_alert_count": len(managed_alerts),
        "backend_alert_count": sum(uid in managed_alerts for uid in backend_alert_uids),
        "worker_uplift_alert_count": sum(uid in managed_alerts for uid in worker_alert_uids),
        "linux_integration_alert_replacement_count": sum(
            uid in managed_alerts for uid in LINUX_ALERT_REPLACEMENT_UIDS
        ),
        "alert_rule_health": health,
        "datasource_generated_alerts": datasource_alerts,
        "contact_points": contact_points,
        "notification_policy": policy,
        "grafana_slos": slo_verification,
        "worker_rollout": worker_rollout,
        "external_rule_inventory": {
            "expected_retained_count": external_catalog["expectedRetainedRuleCount"],
            "expected_alerts_disabled_count": external_catalog[
                "expectedAlertsDisabledRuleCount"
            ],
            "expected_post_upgrade_count": external_catalog["expectedPostUpgradeRuleCount"],
            "definition_fingerprint_baseline_status": external_catalog[
                "definitionFingerprintPolicy"
            ]["baselineStatus"],
            "definition_drift_validation": (
                external_catalog["definitionFingerprintPolicy"]["baselineStatus"]
                == "approved"
                and sum(
                    item.get("disposition") == "retain"
                    for item in external_inventory
                )
                == external_catalog["expectedRetainedRuleCount"]
                and all(
                    item.get("definition_fingerprint_status")
                    == "matched-approved-baseline"
                    for item in external_inventory
                    if item.get("disposition") == "retain"
                )
            ),
            "observed_live_count": sum(
                item.get("state")
                not in {
                    "removed-by-supported-integration-upgrade",
                    "disabled-after-reviewed-terraform-replacement",
                }
                for item in external_inventory
            ),
            "rules": external_inventory,
        },
        "terraform_state": {
            "synthetic_check_count": len(synthetic_state or {}),
            "synthetic_public_probe_count": sum(
                1
                for probe in (synthetic_probe_state or {}).values()
                if isinstance(probe, dict) and probe.get("public") is True
            ),
            "synthetic_execution_estimate": synthetic_execution_estimate,
            "synthetic_execution_guardrail": synthetic_execution_guardrail,
            "synthetic_execution_major_threshold": synthetic_execution_major_threshold,
            "synthetic_major_forecast_acknowledged": synthetic_major_acknowledgment_state,
            "rollout_decisions_enforced": rollout_decision_enforcement_state,
            "slo_count": len(slo_state or {}),
            "worker_terminal_slo_alerting_enabled": worker_slo_alerting_state,
        },
        "prometheus_queries": prometheus,
        "synthetic_queries": synthetic_queries,
        "synthetic_monitoring_inventory": synthetic_inventory,
        "usage_queries": usage,
        "loki_queries": loki,
        "errors": errors,
    }
    sensitive_values = protected_report_values(
        token,
        synthetic_monitoring_token,
        desired_synthetic_checks_raw,
        desired_synthetic_checks,
        prometheus_uid,
        loki_uid,
        usage_uid,
    )
    try:
        text = serialize_report_for_output(report, sensitive_values)
    except ValueError:
        print(
            "Refusing to emit Grafana Cloud verification evidence containing a protected value.",
            file=sys.stderr,
        )
        return 1
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
