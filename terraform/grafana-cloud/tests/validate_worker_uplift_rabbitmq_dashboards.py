#!/usr/bin/env python3
"""Validate worker-uplift RabbitMQ dashboard guardrails for issue #89."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = json.loads((ROOT / "catalog/backend-observability.json").read_text(encoding="utf-8"))
TEMPLATE = (ROOT / "dashboards/nutsnews-dashboard.json.tftpl").read_text(encoding="utf-8")
BACKEND_TF = (ROOT / "backend.tf").read_text(encoding="utf-8")
VERIFY = (ROOT / "scripts/verify_post_apply.py").read_text(encoding="utf-8")
PLAN_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
APPLY_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-apply.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (REPO / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")

STAGES = (
    "fetch",
    "canonicalization",
    "enrichment",
    "approval",
    "translation",
    "persistence",
    "publication",
)
SERVICES = ("rabbitmq", "scheduler", "fetcher", "canonicalizer", "enrichment", "approval", "translation", "persistence", "publication")
QUEUES = tuple(
    queue
    for stage in STAGES
    for queue in (
        f"nutsnews.worker.{stage}.v1",
        f"nutsnews.worker.{stage}.v1.retry-30s",
        f"nutsnews.worker.{stage}.v1.retry-5m",
        f"nutsnews.worker.{stage}.v1.retry-30m",
        f"nutsnews.worker.{stage}.v1.dlq",
    )
)
DASHBOARD_UIDS = (
    "nutsnews-worker-uplift-rabbitmq-overview",
    "nutsnews-worker-uplift-rabbitmq-queues",
    "nutsnews-worker-uplift-rabbitmq-resources",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def dashboard(uid: str) -> dict:
    for item in CATALOG["dashboards"]:
        if item["uid"] == uid:
            return item
    raise SystemExit(f"missing dashboard uid: {uid}")


def variable_block(name: str) -> str:
    pattern = re.compile(r"\{(?P<body>.*?\"name\": \"" + re.escape(name) + r"\".*?)\n      \}", re.DOTALL)
    match = pattern.search(TEMPLATE)
    require(match is not None, f"{name} dashboard variable is missing")
    return match.group("body")


guardrails = CATALOG["guardrails"]
dashboards = CATALOG["dashboards"]
all_panels = [panel for item in dashboards for panel in item["panels"]]
queries = {panel["expr"] for panel in all_panels}

require(len(dashboards) <= guardrails["max_dashboards"], "dashboard count exceeds catalog guardrail")
require(max(len(item["panels"]) for item in dashboards) <= guardrails["max_panels_per_dashboard"], "panel count exceeds per-dashboard guardrail")
require(len(all_panels) <= guardrails["max_total_panels"], "total panel count exceeds catalog guardrail")
require(len(queries) <= guardrails["max_unique_queries"], "unique query count exceeds catalog guardrail")
require(len(all_panels) == 79, "backend catalog must stay within the approved 79 panel total for #89")

for uid in DASHBOARD_UIDS:
    item = dashboard(uid)
    require(item.get("importExisting") is False, f"{uid} must be source-created by infra OpenTofu")
    require("ramideltoro/nutsnews-worker#89" in item.get("missingRemoteObjectReason", ""), f"{uid} must document #89 ownership")
    require(item.get("description"), f"{uid} must include a dashboard description")
    for panel in item["panels"]:
        require(panel.get("description"), f"{uid} panel lacks description: {panel['title']}")
        require(panel.get("unit") or panel.get("type") == "logs", f"{uid} panel lacks unit: {panel['title']}")
        require(panel.get("expr"), f"{uid} panel lacks query expression: {panel['title']}")
        require("trace" not in json.dumps(panel).lower(), f"{uid} must not add trace links while #144 defers traces")

for variable in ("environment", "host", "vhost", "stage", "queue", "service"):
    block = variable_block(variable)
    require('"includeAll": true' in block, f"{variable} variable must include All")
    require('"allValue": ".*"' in block, f"{variable} variable must use regex wildcard All")

for host in ("backend.nutsnews.com", "vps.nutsnews.com"):
    require(host in variable_block("host"), f"host variable missing {host}")
for stage in STAGES:
    require(stage in variable_block("stage"), f"stage variable missing {stage}")
for service in SERVICES:
    require(service in variable_block("service"), f"service variable missing {service}")
for queue in QUEUES:
    require(queue in variable_block("queue"), f"queue variable missing {queue}")

overview_titles = {panel["title"] for panel in dashboard("nutsnews-worker-uplift-rabbitmq-overview")["panels"]}
for title in (
    "Broker Scrape Up",
    "Queue Metrics Scrape Up",
    "Ready Messages Total",
    "Unacked Messages Total",
    "Ingress Publish Rate",
    "Egress Ack Rate",
    "Redelivery Rate",
    "DLQ Ready Messages",
    "Stage Ack Throughput",
    "Queue Metric Freshness",
    "Broker Alarms",
    "Alloy Remote Write Pending Samples",
):
    require(title in overview_titles, f"overview dashboard missing {title}")

queue_titles = {panel["title"] for panel in dashboard("nutsnews-worker-uplift-rabbitmq-queues")["panels"]}
for title in (
    "Selected Queue Depth",
    "Ready By Queue",
    "Unacked By Queue",
    "Consumer Count By Queue",
    "Consumer Utilisation By Queue",
    "Consumer Capacity By Queue",
    "Deliver Rate By Queue",
    "Ack Rate By Queue",
    "Retry Queue Depth",
    "DLQ Queue Depth",
    "Worker Service Versions And Logs",
):
    require(title in queue_titles, f"queue drilldown dashboard missing {title}")

resource_titles = {panel["title"] for panel in dashboard("nutsnews-worker-uplift-rabbitmq-resources")["panels"]}
for title in (
    "RabbitMQ Connections",
    "RabbitMQ Channels",
    "RabbitMQ Consumers",
    "RabbitMQ Queue Count",
    "RabbitMQ Memory Used",
    "RabbitMQ Disk Free",
    "RabbitMQ File Descriptor Usage",
    "RabbitMQ Resource Logs",
):
    require(title in resource_titles, f"resource dashboard missing {title}")

catalog_text = json.dumps({uid: dashboard(uid) for uid in DASHBOARD_UIDS})
for token in (
    "rabbitmq_detailed_queue_messages_ready",
    "rabbitmq_detailed_queue_messages_unacked",
    "rabbitmq_detailed_queue_messages_acked_total",
    "rabbitmq_detailed_queue_messages_delivered_total",
    "rabbitmq_detailed_queue_messages_redelivered_total",
    "rabbitmq_detailed_queue_consumers",
    "rabbitmq_detailed_queue_consumer_utilisation",
    "rabbitmq_detailed_queue_consumer_capacity",
    "rabbitmq_connections",
    "rabbitmq_channels",
    "rabbitmq_process_open_fds",
    "{host=~\\\"$host\\\",source=\\\"container\\\"",
):
    require(token in catalog_text, f"RabbitMQ dashboard catalog missing {token}")

require("/explore?left=" in catalog_text, "RabbitMQ dashboards must include Grafana Explore Loki links")
require("%24%24%7Bloki_datasource_uid%7D" in catalog_text, "Loki links must use the encoded datasource UID placeholder")
require("urlencode(var.loki_datasource_uid)" in BACKEND_TF, "backend generator must replace encoded Loki datasource UID placeholders")
require("description = panel.description" in BACKEND_TF, "backend generator must emit panel descriptions")
require("links = panel.links" in BACKEND_TF, "backend generator must emit panel links")
require("noValue  = panel.noValue" in BACKEND_TF, "backend generator must emit no-data text")

for token in (
    "backend_rabbitmq",
    "backend_rabbitmq_queues",
    "backend_rabbitmq_logs",
    'rabbitmq_detailed_queue_messages{job="nutsnews-rabbitmq-queues"',
    '{host="backend.nutsnews.com",source="container",service="rabbitmq"}',
):
    require(token in VERIFY, f"post-apply verification missing {token}")

for workflow, name in ((PLAN_WORKFLOW, "Grafana Cloud Plan"), (APPLY_WORKFLOW, "Grafana Cloud Apply")):
    require("validate_worker_uplift_rabbitmq_dashboards.py" in workflow, f"{name} must run the #89 dashboard validator")

for text, name in ((README, "module README"), (RUNBOOK, "Grafana runbook")):
    require("NutsNews Worker-Uplift RabbitMQ Overview" in text, f"{name} must document the #89 overview dashboard")
    require("NutsNews Worker-Uplift Queue Drilldown" in text, f"{name} must document the #89 queue drilldown dashboard")
    require("NutsNews Worker-Uplift RabbitMQ Resources" in text, f"{name} must document the #89 resource dashboard")
    require("traces remain deferred" in text, f"{name} must state that traces remain deferred")

print("Worker-uplift RabbitMQ dashboard guardrails passed.")
