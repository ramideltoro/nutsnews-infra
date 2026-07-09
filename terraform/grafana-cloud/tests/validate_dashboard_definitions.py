#!/usr/bin/env python3
"""Validate generated Grafana dashboard definition guardrails."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "dashboards/nutsnews-dashboard.json.tftpl").read_text(encoding="utf-8")
LOCALS = (ROOT / "locals.tf").read_text(encoding="utf-8")
ALERTS = (ROOT / "alerts.tf").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def variable_block(name: str) -> str:
    pattern = re.compile(
        r"\{\n"
        r"(?P<body>.*?"
        rf'"name": "{re.escape(name)}"'
        r".*?)\n      \}",
        re.DOTALL,
    )
    match = pattern.search(TEMPLATE)
    require(match is not None, f"{name} variable block is missing.")
    return match.group("body")


for variable in ("environment", "instance"):
    block = variable_block(variable)
    require('"includeAll": true' in block, f"{variable} variable must include All.")
    require('"allValue": ".*"' in block, f"{variable} variable All value must be regex wildcard.")
    require('"value": ".*"' in block, f"{variable} variable current All value must be regex wildcard.")

require(
    'deployment_environment=~\\"$environment\\"' in LOCALS,
    "Metric/log filters must interpolate the environment variable as a regex matcher.",
)
require(
    'instance=~\\"$instance\\"' in LOCALS,
    "Metric/log filters must interpolate the instance variable as a regex matcher.",
)
require(
    'node_exporter_metric_filter = "job=~\\"integrations/node_exporter\\", instance=~\\"$instance\\""' in LOCALS,
    "Node exporter panels must use the Grafana Cloud integration job label instead of service_namespace.",
)
require(
    'label_values(up{job=~\\"integrations/node_exporter\\"}, instance)' in TEMPLATE,
    "Instance variable must be populated from the node exporter integration job.",
)
require(
    'label_values(up{service_namespace=\\"nutsnews\\"}, instance)' not in TEMPLATE,
    "Instance variable must not require service_namespace for node exporter metrics.",
)
require(
    'rate(node_cpu_seconds_total{${local.node_exporter_metric_filter},mode=\\"idle\\"}[$__rate_interval])' in LOCALS,
    "CPU busy must use $__rate_interval for the PromQL rate window.",
)

load_panel = re.search(
    r'title\s+=\s+"Load averages".*?targets\s+=\s+\[(?P<body>.*?)\n\s+\]',
    LOCALS,
    re.DOTALL,
)
require(load_panel is not None, "Load averages panel must define explicit targets.")
load_targets = load_panel.group("body")

for metric, legend in (
    ("node_load1", "1m"),
    ("node_load5", "5m"),
    ("node_load15", "15m"),
):
    require(metric in load_targets, f"Load averages target missing {metric}.")
    require(f'legend = "{legend}"' in load_targets, f"Load averages target missing {legend} legend.")

require(
    "node_load1{${local.base_metric_filter}} or node_load5" not in LOCALS,
    "Load averages must not combine all load metrics with PromQL or in one target.",
)
require(
    "node_load1{${local.node_exporter_metric_filter}}" in load_targets,
    "Load averages must use the node exporter integration filter.",
)
require('legendFormat = target.legend' in LOCALS, "Explicit panel targets must use their configured legends.")
require('refId        = ["A", "B", "C", "D", "E", "F"][target_index]' in LOCALS, "Explicit panel targets must get stable refIds.")

for token in (
    'uid         = "nutsnews-logs-overview"',
    "Log volume by source",
    "Log volume by service",
    "Log volume by level",
    "Systemd journal by unit",
    "Docker logs by container",
    "Caddy status classes",
    "Dropped log guardrails",
    'source=\\"docker\\",container=\\"nutsnews-caddy\\"',
    'loki_write_dropped_entries_total',
    'loki_write_batch_retries_total',
    'high_error_log_volume',
):
    require(token in LOCALS, f"Logs dashboard or alert locals missing {token}.")

require(
    'resource "grafana_rule_group" "log_pipeline"' in ALERTS,
    "Grafana log pipeline alert rule group is missing.",
)
require(
    "local.datasource_types[rule.value.datasource]" in ALERTS,
    "Log pipeline alert rules must choose the datasource type from the existing datasource map.",
)

for stale_query in re.findall(r"node_[a-zA-Z0-9_]+\\{\\$\\{local\\.base_metric_filter\\}", LOCALS):
    require(
        False,
        f"Node exporter query still uses the service_namespace base filter: {stale_query}",
    )

print("Grafana dashboard definition guardrails passed.")
