#!/usr/bin/env python3
"""Validate worker-uplift RabbitMQ alert and SLO guardrails for issue #90."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = json.loads((ROOT / "catalog/worker-uplift-rabbitmq-alerts.json").read_text(encoding="utf-8"))
BACKEND_TF = (ROOT / "backend.tf").read_text(encoding="utf-8")
LOCALS_TF = (ROOT / "locals.tf").read_text(encoding="utf-8")
SLOS_TF = (ROOT / "slos.tf").read_text(encoding="utf-8")
VERIFY = (ROOT / "scripts/verify_post_apply.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (REPO / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")
PLAN_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
APPLY_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-apply.yml").read_text(encoding="utf-8")
EXPECTED_ACTIVE_SELECTOR = (
    'nutsnews_backend_worker_uplift_expected_active{job="nutsnews-backend-host",'
    'instance="backend.nutsnews.com",service_namespace="nutsnews",service="host",'
    'environment="production",deployment_environment="production",'
    'host="backend.nutsnews.com"}'
)
WORKER_SOURCE_IDENTITY = (
    'job="nutsnews-worker-uplift",instance="backend.nutsnews.com",'
    'service_namespace="nutsnews",host="backend.nutsnews.com",'
    'environment="production",deployment_environment="production"'
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


INVALID_PROMQL_STRING_ESCAPE = re.compile(r"(?<!\\)\\[.()+|?*{}\[\]-]")
for collection in (CATALOG["dashboards"], CATALOG["alerts"], CATALOG["slos"]):
    for item in collection:
        expressions = [item.get("expr")]
        expressions.extend(
            panel.get("expr")
            for panel in item.get("panels", [])
            if isinstance(panel, dict)
        )
        for expression in expressions:
            if isinstance(expression, str):
                require(
                    INVALID_PROMQL_STRING_ESCAPE.search(expression) is None,
                    "worker catalog contains an invalid single-backslash PromQL string escape",
                )


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
    "NutsNews worker-uplift main queue has zero consumers",
    "NutsNews worker-uplift backlog or oldest outbox age sustained",
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
require(CATALOG["contact_route"] == "operations-email", "catalog must route alerts to the managed operations email policy")
require("production-vps maintenance" in CATALOG["maintenance_suppression"], "catalog must document maintenance suppression")
require(
    "route=operations-email" in CATALOG["maintenance_suppression"]
    and "route=default" not in CATALOG["maintenance_suppression"],
    "maintenance suppression must match the managed operations-email notification route",
)
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
    "Newest Published Content Age",
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

require(len(alerts) == 28, "catalog must preserve the approved rules plus freshness, ownership, lifecycle, and telemetry-loss guardrails")
require(len(alert_uids) == len(set(alert_uids)), "worker-uplift alert UIDs must be unique")
require(max(len(uid) for uid in alert_uids) <= 40, "Grafana alert UIDs must stay below 40 characters")
require(required_categories.issubset(alert_categories), f"catalog missing alert categories: {sorted(required_categories - alert_categories)}")
require(required_titles.issubset(alert_titles), f"catalog missing required alert titles: {sorted(required_titles - alert_titles)}")
require("nn-wu-slo-broker-burn" in alert_uids, "broker SLO burn alert UID missing")
require("nn-wu-slo-retry-dlq-burn" in alert_uids, "retry/DLQ SLO burn alert UID missing")
require("nn-wu-feed-freshness-critical" in alert_uids, "three-hour feed freshness critical alert UID missing")
require("nn-wu-worker-scrape-missing" in alert_uids, "worker service scrape-missing alert UID missing")
require("nn-wu-rmq-queue-metrics-missing" in alert_uids, "queue-depth telemetry-loss alert UID missing")
require("nn-wu-rmq-publish-metrics-missing" in alert_uids, "per-queue publish telemetry-loss alert UID missing")
require("nn-wu-ownership-telemetry-missing" in alert_uids, "ownership-control telemetry-loss alert UID missing")
require("nn-wu-lifecycle-telemetry-missing" in alert_uids, "lifecycle/readiness telemetry-loss alert UID missing")
require("nn-wu-scheduler-loop-stale" in alert_uids, "scheduler loop freshness alert UID missing")
require(
    "nn-wu-feed-freshness-telemetry" in alert_uids,
    "durable content freshness telemetry-loss alert UID missing",
)
content_telemetry_alert = next(
    alert
    for alert in alerts
    if alert["uid"] == "nn-wu-feed-freshness-telemetry"
)
for token in (
    "nutsnews_backend_content_coverage_available",
    "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds",
    ">= 0",
    "> bool 0",
):
    require(token in content_telemetry_alert["expr"], f"content freshness telemetry alert missing {token}")
require(
    content_telemetry_alert["evaluator"] == "lt"
    and content_telemetry_alert["threshold"] == 1
    and content_telemetry_alert["no_data_state"] == "Alerting",
    "content freshness telemetry alert must fail on unavailable, invalid, or absent source series",
)

consumer_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-rmq-no-consumers")
require(
    "max by (queue)" in consumer_alert["expr"],
    "consumer-loss alert must evaluate each main queue independently",
)
require(
    "rabbitmq_detailed_queue_messages_ready" not in consumer_alert["expr"],
    "consumer-loss alert must detect zero consumers even when the queue is empty",
)
require(
    "group_left" not in consumer_alert["expr"],
    "consumer-loss canary and drill overrides must remain independent series outside the per-queue gate",
)
require(
    f"and on() (max({EXPECTED_ACTIVE_SELECTOR}) == 1)"
    in consumer_alert["expr"],
    "consumer-loss alert must use the durable host-owned expected-active gate",
)
require(
    'drill="rabbitmq-zero-consumer"' in consumer_alert["expr"]
    and consumer_alert["expr"].rfind('drill="rabbitmq-zero-consumer"')
    > consumer_alert["expr"].rfind("nutsnews_backend_worker_uplift_expected_active"),
    "zero-consumer drill fixture must remain outside the shadow ownership gate",
)

backlog_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-rmq-backlog-age")
for token in (
    "nutsnews_backend_worker_uplift_oldest_unconfirmed_outbox_age_seconds",
    "nutsnews_backend_worker_uplift_outbox_available",
    ">= 0",
    "on(stage)",
    "nutsnews_backend_worker_uplift_expected_active",
):
    require(token in backlog_alert["expr"], f"outbox backlog alert missing {token}")
require(
    "nutsnews_worker_uplift_queue_oldest_age_seconds" not in backlog_alert["expr"],
    "backlog alert must not query the nonexistent queue-oldest-age family",
)
require(
    backlog_alert["expr"].rfind("nutsnews_backend_rabbitmq_canary_failure_fixture")
    > backlog_alert["expr"].rfind("nutsnews_backend_worker_uplift_expected_active"),
    "backlog canary override must remain outside the shadow ownership gate",
)

for uid in (
    "nn-wu-rmq-pub-ack-gap",
    "nn-wu-rmq-unacked-growth",
    "nn-wu-rmq-dlq-nonempty",
    "nn-wu-rmq-retry-redelivery",
    "nn-wu-slo-retry-dlq-burn",
):
    alert = next(item for item in alerts if item["uid"] == uid)
    require(
        "nutsnews_backend_worker_uplift_expected_active" in alert["expr"],
        f"{uid} normal worker-owned arm must be disabled while shadowed",
    )
for uid in (
    "nn-wu-rmq-backlog-age",
    "nn-wu-rmq-dlq-nonempty",
    "nn-wu-rmq-retry-redelivery",
):
    alert = next(item for item in alerts if item["uid"] == uid)
    require(
        "or vector(0)) + (max(nutsnews_backend_rabbitmq_canary_failure_fixture"
        in alert["expr"],
        f"{uid} must totalize its shadow-disabled normal arm before adding the canary override",
    )
for uid in ("nn-wu-rmq-dlq-nonempty", "nn-wu-slo-retry-dlq-burn"):
    alert = next(item for item in alerts if item["uid"] == uid)
    require(
        alert["expr"].rfind('drill="rabbitmq-growing-dlq"')
        > alert["expr"].rfind("nutsnews_backend_worker_uplift_expected_active"),
        f"{uid} telemetry-only drill override must remain outside the shadow gate",
    )

scrape_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-worker-scrape-missing")
for token in (
    "8 - (count(count by (service)",
    'service=~"scheduler|fetcher|canonicalizer|enrichment|approval|translation|persistence|publication"',
    'expected_active="1"',
    "nutsnews_backend_worker_uplift_expected_active",
    'drill="worker-unavailable"',
    "or vector(0)",
):
    require(token in scrape_alert["expr"], f"worker scrape-missing alert must include {token}")
worker_up_selectors = re.findall(r'up\{([^}]*)\}', scrape_alert["expr"])
require(len(worker_up_selectors) == 2, "worker scrape-missing alert must have exact normal and drill worker up selectors")
for selector in worker_up_selectors:
    for token in (
        'job="nutsnews-worker-uplift"',
        'instance="backend.nutsnews.com"',
        'service_namespace="nutsnews"',
        'host="backend.nutsnews.com"',
        'environment="production"',
        'deployment_environment="production"',
    ):
        require(token in selector, f"worker scrape-missing up selector missing {token}")

queue_metrics_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-rmq-queue-metrics-missing")
for token in (
    "abs(35 - (count(",
    "abs(7 - (count(",
    "rabbitmq_detailed_queue_messages",
    "rabbitmq_detailed_queue_messages_ready",
    "rabbitmq_detailed_queue_messages_unacked",
    "rabbitmq_detailed_queue_consumers",
    "rabbitmq_detailed_queue_messages_acked_total",
    "rabbitmq_detailed_queue_messages_delivered_total",
    "rabbitmq_detailed_queue_messages_redelivered_total",
    "retry-(30s|5m|30m)",
    "dlq",
    "nutsnews_backend_worker_uplift_expected_active",
    "or vector(0)",
):
    require(token in queue_metrics_alert["expr"], f"queue telemetry-loss alert must include {token}")

publish_metrics_alert = next(
    alert for alert in alerts if alert["uid"] == "nn-wu-rmq-publish-metrics-missing"
)
for token in (
    "abs(7 - (count(count by (queue)",
    "rabbitmq_detailed_queue_exchange_messages_published_total",
    'job="nutsnews-rabbitmq-queues"',
    'environment="production"',
    'host="backend.nutsnews.com"',
    'queue=~"nutsnews\\\\.worker\\\\.(fetch|canonicalization|enrichment|approval|translation|persistence|publication)\\\\.v1"',
    "nutsnews_backend_worker_uplift_expected_active",
):
    require(token in publish_metrics_alert["expr"], f"publish telemetry-loss alert must include {token}")
require(
    publish_metrics_alert["no_data_state"] == "OK"
    and publish_metrics_alert["evaluator"] == "gt"
    and publish_metrics_alert["threshold"] == 0,
    "publish telemetry loss must stay ownership-gated and evaluate an explicit distinct-queue count",
)

ownership_telemetry_alert = next(
    alert for alert in alerts if alert["uid"] == "nn-wu-ownership-telemetry-missing"
)
for token in (
    "nutsnews_backend_worker_uplift_ownership_available",
    "nutsnews_backend_worker_uplift_expected_active",
    "abs(1 - (count(",
    "!= bool 0",
    "!= bool 1",
    'job="nutsnews-backend-host"',
    'instance="backend.nutsnews.com"',
    'service_namespace="nutsnews"',
    'environment="production"',
    'deployment_environment="production"',
    'host="backend.nutsnews.com"',
):
    require(token in ownership_telemetry_alert["expr"], f"ownership telemetry alert missing {token}")
require(
    ownership_telemetry_alert["no_data_state"] == "Alerting"
    and ownership_telemetry_alert["evaluator"] == "gt"
    and ownership_telemetry_alert["threshold"] == 0,
    "ownership telemetry guardrail must fail closed independently of the shadow gate",
)

lifecycle_telemetry_alert = next(
    alert for alert in alerts if alert["uid"] == "nn-wu-lifecycle-telemetry-missing"
)
for token in (
    "abs(42 - (count(nutsnews_worker_uplift_stage_events_total",
    'outcome=~"success|duplicate|invalid|retry|dlq|failure"',
    "abs(7 - (count(nutsnews_worker_uplift_stage_latency_seconds_bucket",
    'le="+Inf"',
    "nutsnews_worker_uplift_stage_latency_seconds_count",
    "abs(8 - (count(nutsnews_worker_health_probe",
    'probe="readiness"',
    'outcome="ok"',
    "< bool 1",
    "nutsnews_backend_worker_uplift_expected_active",
    'job="nutsnews-worker-uplift"',
    'instance="backend.nutsnews.com"',
    'service_namespace="nutsnews"',
    'host="backend.nutsnews.com"',
):
    require(token in lifecycle_telemetry_alert["expr"], f"lifecycle telemetry alert missing {token}")
require(
    "max(nutsnews_worker_health_probe{" in lifecycle_telemetry_alert["expr"]
    and "sum(nutsnews_worker_health_probe{" not in lifecycle_telemetry_alert["expr"],
    "lifecycle telemetry must detect any non-ready worker without summing readiness values",
)

publish_ack_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-rmq-pub-ack-gap")
main_queue_selector = (
    'queue=~"nutsnews\\\\.worker\\\\.(fetch|canonicalization|enrichment|approval|translation|persistence|publication)'
    '\\\\.v1"'
)
for metric in (
    "rabbitmq_detailed_queue_exchange_messages_published_total",
    "rabbitmq_detailed_queue_messages_acked_total",
):
    selector = re.search(rf"{metric}\{{([^}}]*)\}}", publish_ack_alert["expr"])
    require(selector is not None, f"publish/ack imbalance must query {metric}")
    for token in (
        'job="nutsnews-rabbitmq-queues"',
        'environment="production"',
        'host="backend.nutsnews.com"',
        main_queue_selector,
    ):
        require(token in selector.group(1), f"{metric} publish/ack selector missing {token}")
require(
    EXPECTED_ACTIVE_SELECTOR in publish_ack_alert["expr"],
    "publish/ack imbalance must remain dashboard-only until the host-owned production gate is active",
)
require(
    "rabbitmq_queue_messages_published_total" not in publish_ack_alert["expr"],
    "publish/ack imbalance must not compare all-vhost aggregate publishes with worker queue acknowledgements",
)

scheduler_alert = next(alert for alert in alerts if alert["uid"] == "nn-wu-scheduler-loop-stale")
require(
    "nutsnews_worker_scheduler_loop_fresh" in scheduler_alert["expr"],
    "scheduler readiness alert must use the successful-cycle freshness signal",
)
require(
    "nutsnews_worker_scheduler_loop_active" not in scheduler_alert["expr"],
    "scheduler readiness alert must not treat raw loop-active state as fresh",
)
require(
    "nutsnews_backend_worker_uplift_expected_active" in scheduler_alert["expr"],
    "scheduler freshness must remain dashboard-only while shadowed",
)
require(
    WORKER_SOURCE_IDENTITY in scheduler_alert["expr"],
    "scheduler freshness must use the exact worker scrape identity",
)

ingestion_freshness_alert = next(
    alert for alert in alerts if alert["uid"] == "nn-wu-slo-feed-freshness"
)
for token in (
    "nutsnews_backend_legacy_worker_last_scheduled_success_age_seconds",
    "nutsnews_backend_worker_uplift_deployment_info",
    'ingestion_owner="legacy_shards"',
    "nutsnews_worker_scheduler_loop_fresh",
    "nutsnews_backend_worker_uplift_expected_active",
    "absent(",
    "> bool 900",
):
    require(token in ingestion_freshness_alert["expr"], f"ingestion freshness alert missing {token}")
require(
    "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds"
    not in ingestion_freshness_alert["expr"],
    "ingestion freshness warning must not page on quiet upstream publishers",
)
require(
    ingestion_freshness_alert["threshold"] == 0
    and ingestion_freshness_alert["evaluator"] == "gt",
    "ingestion freshness warning must evaluate an explicit owner-routed failure signal",
)

critical_freshness_alert = next(
    alert for alert in alerts if alert["uid"] == "nn-wu-feed-freshness-critical"
)
for token in (
    "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds",
    "nutsnews_backend_content_coverage_available",
    ">= 0",
):
    require(token in critical_freshness_alert["expr"], f"critical content alert missing {token}")
require(
    "nutsnews_backend_worker_uplift_expected_active" not in critical_freshness_alert["expr"],
    "critical content freshness must protect the live feed independently of worker ownership",
)
require(
    critical_freshness_alert["threshold"] == 10800,
    "critical feed freshness must remain three hours",
)

for alert in alerts:
    expression = alert["expr"]
    if "nutsnews_backend_worker_uplift_expected_active{" not in expression:
        continue
    require(
        "nutsnews_backend_worker_uplift_expected_active{"
        not in expression.replace(EXPECTED_ACTIVE_SELECTOR, ""),
        f"{alert['uid']} must use only the exact singleton host-owned expected-active selector",
    )

worker_selector_tokens = (
    'job="nutsnews-worker-uplift"',
    'instance="backend.nutsnews.com"',
    'service_namespace="nutsnews"',
    'host="backend.nutsnews.com"',
    'environment="production"',
    'deployment_environment="production"',
)
worker_metric_selector_count = 0
for alert in alerts:
    for metric, selector in re.findall(
        r"(nutsnews_worker_[A-Za-z0-9_:]+)\{([^}]*)\}",
        alert["expr"],
    ):
        worker_metric_selector_count += 1
        for token in worker_selector_tokens:
            require(token in selector, f"{alert['uid']} {metric} selector missing {token}")
require(worker_metric_selector_count > 0, "worker alert catalog must contain worker metric selectors")

for uid in ("nn-wu-slo-stage-latency", "nn-wu-slo-publication-success"):
    ownership_alert = next(alert for alert in alerts if alert["uid"] == uid)
    require(
        "nutsnews_backend_worker_uplift_expected_active" in ownership_alert["expr"],
        f"{uid} must remain dashboard-only while worker services are shadowed",
    )
    require(
        WORKER_SOURCE_IDENTITY in ownership_alert["expr"],
        f"{uid} must use the exact worker scrape identity",
    )


def worker_scrape_missing(observed: int, expected_active: bool) -> bool:
    return expected_active and observed < 8


require(not worker_scrape_missing(8, True), "all eight production scrapes must be healthy")
require(worker_scrape_missing(7, True), "one missing production scrape must alert")
require(worker_scrape_missing(0, True), "complete worker telemetry loss must still alert")
require(not worker_scrape_missing(0, False), "shadow worker telemetry loss must stay dashboard-only")

for alert in alerts:
    require(alert["severity"] in {"critical", "major", "warning"}, f"{alert['uid']} has unsupported severity")
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
    "worker_dashboard_uids",
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
for alert in alerts:
    for selector in re.findall(
        r"nutsnews_backend_rabbitmq_canary_failure_fixture\{([^}]*)\}",
        alert["expr"],
    ):
        for token in (
            'job="nutsnews-backend-host"',
            'instance="backend.nutsnews.com"',
            'service_namespace="nutsnews"',
            'service="host"',
            'environment="production"',
            'deployment_environment="production"',
            'host="backend.nutsnews.com"',
        ):
            require(token in selector, f"{alert['uid']} fixture selector missing {token}")
    for selector in re.findall(
        r"nutsnews_observability_failure_drill_active\{([^}]*)\}",
        alert["expr"],
    ):
        for token in (
            'job="nutsnews-backend-host"',
            'instance="backend.nutsnews.com"',
            'service_namespace="nutsnews"',
            'service="host"',
            'environment="production"',
            'deployment_environment="production"',
            'host="backend.nutsnews.com"',
            'drill="',
        ):
            require(token in selector, f"{alert['uid']} drill selector missing {token}")
for token in (
    "nutsnews_backend_rabbitmq_canary_success",
    "nutsnews_backend_rabbitmq_canary_failure_fixture",
    "nutsnews_backend_rabbitmq_definition_export_age_seconds",
    "rabbitmq_detailed_queue_messages_ready",
    "rabbitmq_detailed_queue_messages_unacked",
    "rabbitmq_detailed_queue_exchange_messages_published_total",
    "rabbitmq_detailed_queue_messages_redelivered_total",
    "rabbitmq_detailed_queue_messages_acked_total",
    "rabbitmq_process_open_fds",
    "rabbitmq_process_max_fds",
    "node_systemd_service_restart_total",
    "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds",
    "nutsnews_backend_content_coverage_available",
    "nutsnews_worker_uplift_stage_events_total",
    "nutsnews_worker_uplift_stage_latency_seconds_bucket",
):
    require(token in catalog_text, f"catalog must include query token {token}")

require(
    "clamp_min(sum(rate(nutsnews_worker_uplift_stage_events_total" not in catalog_text,
    "worker success ratios must not turn a zero terminal-event denominator into a synthetic failure",
)
require(
    "clamp_min((sum(rate(rabbitmq_detailed_queue_messages_acked_total" not in catalog_text,
    "retry/DLQ ratios must use the true acknowledgement-rate denominator below one event per second",
)
require(
    "and on() (sum(rate(rabbitmq_detailed_queue_messages_acked_total" in catalog_text,
    "retry/DLQ ratios must be gated when acknowledgement traffic is zero",
)
require(
    "clamp_min(sum(rate(nutsnews_worker_uplift_stage_events_total" not in SLOS_TF,
    "native worker SLO must preserve NoData when no terminal events occur",
)
for token in (
    'job=\\"nutsnews-worker-uplift\\"',
    'instance=\\"backend.nutsnews.com\\"',
    'service_namespace=\\"nutsnews\\"',
    'host=\\"backend.nutsnews.com\\"',
    'environment=\\"${var.deployment_environment}\\"',
    'deployment_environment=\\"${var.deployment_environment}\\"',
    'service=~\\"fetcher|canonicalizer|enrichment|approval|translation|persistence|publication\\"',
):
    require(token in SLOS_TF, f"native worker SLO is missing its exact worker identity selector: {token}")
for token in (
    "production_legacy_ingestion_age_selector",
    "nutsnews_backend_legacy_worker_last_scheduled_success_age_seconds",
    "production_legacy_owner_selector",
    'ingestion_owner=\\"legacy_shards\\"',
    "production_uplift_scheduler_selector",
    "nutsnews_worker_scheduler_loop_fresh",
    "production_uplift_owner_selector",
    "nutsnews_backend_worker_uplift_expected_active",
    "production_ingestion_fresh_good",
    "production_ingestion_fresh_valid",
):
    require(token in SLOS_TF, f"native ingestion freshness SLO missing {token}")
require(
    "nutsnews_backend_public_feed_snapshot_newest_content_age_seconds" not in SLOS_TF,
    "native ingestion freshness SLO must not classify quiet publishers as failed ingestion",
)


def terminal_success_ratio(
    success: int,
    duplicate: int,
    invalid: int,
    failure: int,
    dlq: int,
) -> float | None:
    denominator = success + duplicate + invalid + failure + dlq
    return None if denominator == 0 else (success + duplicate) / denominator


require(terminal_success_ratio(1, 0, 0, 0, 0) == 1.0, "one successful terminal event must be 100%")
require(terminal_success_ratio(0, 0, 0, 0, 0) is None, "zero terminal traffic must remain NoData")

require("publish production articles" in catalog_text, "catalog must state alert tests do not publish production articles")

print("Worker-uplift RabbitMQ alert and SLO guardrails passed.")
