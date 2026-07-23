#!/usr/bin/env python3
"""Validate worker-uplift RabbitMQ alert and SLO guardrails for issue #90."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = json.loads((ROOT / "catalog/worker-uplift-rabbitmq-alerts.json").read_text(encoding="utf-8"))
BACKEND_TF = (ROOT / "backend.tf").read_text(encoding="utf-8")
LOCALS_TF = (ROOT / "locals.tf").read_text(encoding="utf-8")
VERIFY = (ROOT / "scripts/verify_post_apply.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (REPO / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")
PLAN_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
APPLY_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-apply.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


alerts = CATALOG["alerts"]
slos = CATALOG["slos"]
dashboards = CATALOG["dashboards"]
alert_uids = [alert["uid"] for alert in alerts]
alert_categories = {alert["alert_category"] for alert in alerts}
alert_titles = {alert["title"] for alert in alerts}
slo_ids = {slo["id"] for slo in slos}
allowed_drills = {
    "restart",
    "consumer-loss",
    "network-interruption",
    "disk-watermark",
    "invalid-credentials",
    "unroutable",
    "full-queue",
    "poison-message",
    "grafana-connectivity-loss",
}
required_categories = {
    "broker-down",
    "canary-failure",
    "telemetry-loss",
    "consumer-loss",
    "backlog-age",
    "publish-ack-imbalance",
    "unacked-growth",
    "dlq",
    "retry-redelivery",
    "connection-churn",
    "broker-alarm",
    "low-disk",
    "descriptor-pressure",
    "recovery-proof",
    "restart",
    "slo-burn",
    "slo-latency",
    "slo-freshness",
    "slo-publication",
}
required_slos = {
    "rabbitmq_broker_availability",
    "stage_success_latency",
    "end_to_end_feed_freshness",
    "retry_dlq_rate",
    "final_publication_success",
}
required_titles = {
    "NutsNews worker-uplift RabbitMQ broker down",
    "NutsNews worker-uplift RabbitMQ canary failed",
    "NutsNews worker-uplift Alloy metrics write loss",
    "NutsNews worker-uplift work exists with no consumers",
    "NutsNews worker-uplift backlog or oldest age sustained",
    "NutsNews worker-uplift publish and ack imbalance",
    "NutsNews worker-uplift unacked messages growing",
    "NutsNews worker-uplift DLQ is non-empty",
    "NutsNews worker-uplift retry or redelivery excessive",
    "NutsNews worker-uplift RabbitMQ connection churn",
    "NutsNews worker-uplift RabbitMQ memory or disk alarm",
    "NutsNews worker-uplift RabbitMQ low disk",
    "NutsNews worker-uplift RabbitMQ descriptor pressure",
    "NutsNews worker-uplift RabbitMQ recovery proof stale",
    "NutsNews worker-uplift RabbitMQ repeated restarts",
}

require(CATALOG["trackingIssue"] == "ramideltoro/nutsnews-worker#90", "catalog must reference issue #90")
require(CATALOG["alert_group"]["name"] == "NutsNews Worker-Uplift RabbitMQ Guardrails", "worker-uplift alert group name changed")
require(CATALOG["alert_group"]["folder_uid"] == "nutsnews-backend-ops", "alerts must stay in the backend Grafana folder")
require(CATALOG["owner"] == "worker-uplift-observability", "catalog must declare the single alert owner")
require(CATALOG["contact_route"] == "default", "catalog must declare the default contact route")
require("production-vps maintenance" in CATALOG["maintenance_suppression"], "catalog must document maintenance suppression")
require(len(dashboards) == 1, "catalog must add exactly one worker-uplift SLO dashboard")
require(dashboards[0]["uid"] == "nutsnews-worker-uplift-slos", "SLO dashboard UID changed")
require(dashboards[0].get("importExisting") is False, "SLO dashboard must be source-created")
require("ramideltoro/nutsnews-worker#90" in dashboards[0].get("missingRemoteObjectReason", ""), "SLO dashboard must document #90 ownership")
require(len(dashboards[0]["panels"]) == 12, "SLO dashboard must keep the approved 12-panel scope")

panel_titles = {panel["title"] for panel in dashboards[0]["panels"]}
for title in (
    "Broker Availability SLI",
    "Canary Success",
    "Canary Latency",
    "Canary Message Age",
    "Stage Success Ratio",
    "Stage P95 Latency",
    "Retry And DLQ Budget Ratio",
    "Publication Success Ratio",
    "Feed Freshness Age",
    "Worker-Uplift Alert State",
    "Canary Fixture Signal",
    "RabbitMQ Recovery Proof Age",
):
    require(title in panel_titles, f"SLO dashboard missing panel {title}")

require(len(slos) == 5, "catalog must define the five approved SLOs")
require(slo_ids == required_slos, "catalog SLO IDs changed")
for slo in slos:
    require(slo.get("target"), f"SLO {slo['id']} must declare a target")
    require(slo.get("error_budget"), f"SLO {slo['id']} must declare an error budget")
    require(slo.get("query_metric"), f"SLO {slo['id']} must declare a query metric")
require(
    {"5m", "1h"}.issubset(set(next(slo for slo in slos if slo["id"] == "rabbitmq_broker_availability")["burn_rate_windows"])),
    "broker availability SLO must use multi-window burn-rate windows",
)
require(
    {"5m", "1h"}.issubset(set(next(slo for slo in slos if slo["id"] == "retry_dlq_rate")["burn_rate_windows"])),
    "retry/DLQ SLO must use multi-window burn-rate windows",
)

require(len(alerts) == 20, "catalog must keep the approved 20 alert rules for #90")
require(len(alert_uids) == len(set(alert_uids)), "worker-uplift alert UIDs must be unique")
require(max(len(uid) for uid in alert_uids) <= 40, "Grafana alert UIDs must stay below 40 characters")
require(required_categories.issubset(alert_categories), f"catalog missing alert categories: {sorted(required_categories - alert_categories)}")
require(required_titles.issubset(alert_titles), f"catalog missing required alert titles: {sorted(required_titles - alert_titles)}")
require("nn-wu-slo-broker-burn" in alert_uids, "broker SLO burn alert UID missing")
require("nn-wu-slo-retry-dlq-burn" in alert_uids, "retry/DLQ SLO burn alert UID missing")

for alert in alerts:
    require(alert["severity"] in {"critical", "warning"}, f"{alert['uid']} has unsupported severity")
    require(alert["slo_id"] in required_slos, f"{alert['uid']} must map to an approved SLO")
    require(alert["test_drill"] in allowed_drills, f"{alert['uid']} must map to a fixed #91 drill")
    require(alert["queue"], f"{alert['uid']} must declare a queue label value")
    require(alert["service"], f"{alert['uid']} must declare a service label value")
    require(alert["summary"], f"{alert['uid']} must declare a summary")
    require(alert["description"], f"{alert['uid']} must declare a description")
    require(alert["threshold_description"], f"{alert['uid']} must declare a reader-facing threshold")
    require(alert["keep_firing_for"], f"{alert['uid']} must declare a recovery window")
    require(alert["range_seconds"] >= 600, f"{alert['uid']} must evaluate over a bounded range")
    require("http://" not in alert["expr"] and "amqp://" not in alert["expr"], f"{alert['uid']} must not expose public endpoints")
    if alert["severity"] == "critical":
        require(alert["test_drill"] != "restart", f"{alert['uid']} critical alerts must use a deliberate firing fixture")
    if "nutsnews_worker_uplift_" in alert["expr"]:
        require(alert["no_data_state"] == "OK", f"{alert['uid']} future worker metric alerts must be no-data OK")

for drill in allowed_drills - {"unroutable"}:
    require(any(alert["test_drill"] == drill for alert in alerts), f"catalog must exercise drill {drill}")

for token in (
    "worker_uplift_catalog",
    'resource "grafana_rule_group" "worker_uplift_guardrails"',
    "local.worker_uplift_alert_rules",
    "runbook_url             = rule.value.runbook_url",
    "owner                  = rule.value.owner",
    "route                  = rule.value.route",
    "queue                  = rule.value.queue",
    "threshold              = rule.value.threshold_label",
    "maintenance_suppression",
    "value={{ $values.B.Value }}",
):
    require(token in BACKEND_TF, f"Terraform worker-uplift alert wiring missing {token}")

for token in (
    "quota_alert_thresholds",
    '"70" = 0.70',
    '"85" = 0.85',
    '"95" = 0.95',
    "grafanacloud_instance_metrics_limits",
    "grafanacloud_logs_instance_limits",
):
    require(token in LOCALS_TF, f"quota guardrail missing {token}")

for token in (
    "WORKER_UPLIFT_CATALOG",
    "worker_uplift_dashboard_uids",
    "backend_rabbitmq_canary",
    "backend_rabbitmq_recovery",
    "worker_uplift_alert_count",
):
    require(token in VERIFY, f"post-apply verification missing {token}")

for workflow, name in ((PLAN_WORKFLOW, "Grafana Cloud Plan"), (APPLY_WORKFLOW, "Grafana Cloud Apply")):
    require("validate_worker_uplift_alerts_slos.py" in workflow, f"{name} must run the #90 alert/SLO validator")

for text, name in ((README, "module README"), (RUNBOOK, "Grafana runbook")):
    require("NutsNews Worker-Uplift Pipeline SLOs" in text, f"{name} must document the #90 SLO dashboard")
    require("NutsNews Worker-Uplift RabbitMQ Guardrails" in text, f"{name} must document the #90 alert group")
    require("worker-uplift RabbitMQ alert and SLO" in text, f"{name} must document worker-uplift RabbitMQ alert and SLO ownership")
    require("Backend RabbitMQ Canary" in text, f"{name} must document #91 drill verification")

catalog_text = json.dumps(CATALOG)
require(
    "or max(nutsnews_backend_rabbitmq_canary_failure_fixture" not in catalog_text,
    "fixture terms must be additive so a healthy zero-valued left-hand series does not suppress drill firing",
)
for token in (
    "nutsnews_backend_rabbitmq_canary_success",
    "nutsnews_backend_rabbitmq_canary_failure_fixture",
    "nutsnews_backend_rabbitmq_definition_export_age_seconds",
    "rabbitmq_detailed_queue_messages_ready",
    "rabbitmq_detailed_queue_messages_unacked",
    "rabbitmq_detailed_queue_messages_redelivered_total",
    "rabbitmq_detailed_queue_messages_acked_total",
    "rabbitmq_process_open_fds",
    "rabbitmq_process_max_fds",
    "node_systemd_service_restart_total",
    "nutsnews_worker_uplift_feed_freshness_age_seconds",
    "nutsnews_worker_uplift_stage_events_total",
    "nutsnews_worker_uplift_stage_latency_seconds_bucket",
):
    require(token in catalog_text, f"catalog must include query token {token}")

require("publish production articles" in catalog_text, "catalog must state alert tests do not publish production articles")

print("Worker-uplift RabbitMQ alert and SLO guardrails passed.")
