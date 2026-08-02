#!/usr/bin/env python3
"""Validate worker-uplift telemetry policy and quota guardrails."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "catalog/worker-uplift-telemetry-scope.json").read_text(encoding="utf-8"))
LOCALS = (ROOT / "locals.tf").read_text(encoding="utf-8")
ALERTS = (ROOT / "alerts.tf").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


require(POLICY["trackingIssue"] == "ramideltoro/nutsnews-worker#144", "policy must reference issue #144")
require(POLICY["productionPathEnabled"] is False, "policy must not enable the worker-uplift production path")
require(
    POLICY["grafanaBudget"]["approvedIncrementalMonthlySpendUsd"] == 0,
    "worker uplift must have a zero incremental paid telemetry budget",
)

signals = {item["telemetryClass"]: item for item in POLICY["signalMatrix"]}
expected_decisions = {
    "rabbitmq_metrics": "required",
    "worker_metrics": "required",
    "structured_logs": "required",
    "traces": "deferred",
    "exemplars": "deferred",
    "profiles": "deferred",
    "sentry_exceptions_and_replays": "canonical-existing-provider",
    "faro_frontend_telemetry": "deferred",
    "article_or_model_payloads": "forbidden",
}
for telemetry_class, decision in expected_decisions.items():
    require(telemetry_class in signals, f"signal matrix missing {telemetry_class}")
    require(signals[telemetry_class]["decision"] == decision, f"{telemetry_class} must be {decision}")

require(signals["traces"]["destination"] == "none", "full trace export must not be an implicit dependency")
require(signals["traces"]["allowedInitialSampleRatio"] == 0, "initial trace sampling must remain disabled")
require(signals["traces"]["futureMaximumInitialSampleRatio"] <= 0.01, "future initial trace sampling must stay capped")
require(
    "NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN" in signals["structured_logs"]["credentialSecrets"],
    "logs must use the scoped telemetry write token",
)
require(
    "NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN" in signals["worker_metrics"]["credentialSecrets"],
    "metrics must use the scoped telemetry write token",
)

allowed_metric_labels = {
    "service",
    "stage",
    "queue",
    "outcome",
    "dependency",
    "language",
    "provider",
    "probe",
    "check",
}
allowed_loki_labels = {
    "deployment_environment",
    "service",
    "service_version",
    "host",
    "source",
    "severity",
}
for label_scope, expected_labels in (
    ("allowedMetricLabels", allowed_metric_labels),
    ("allowedLokiStreamLabels", allowed_loki_labels),
):
    labels = set(POLICY["labels"][label_scope])
    require(labels == expected_labels, f"{label_scope} must be exactly the approved low-cardinality labels")
    for label in labels:
        for fragment in POLICY["labels"]["forbiddenMetricAndStreamLabelFragments"]:
            require(fragment not in label, f"{label_scope} includes forbidden high-cardinality fragment {fragment}: {label}")

require(
    set(POLICY["labels"]["lifecycleOutcomes"])
    == {"success", "duplicate", "invalid", "retry", "dlq", "failure"},
    "lifecycle outcomes must match the shared stage metric contract",
)
require(
    set(POLICY["labels"]["terminalSuccessOutcomes"]) == {"success", "duplicate"},
    "terminal success must count accepted and duplicate completions",
)
require(
    set(POLICY["labels"]["terminalDenominatorOutcomes"])
    == {"success", "duplicate", "invalid", "dlq", "failure"},
    "terminal denominator must exclude intermediate retries",
)
require(
    set(POLICY["labels"]["scrapeAndInfoIdentityLabels"])
    == {
        "deployment_environment",
        "host",
        "service",
        "service_version",
        "revision",
        "deployment_mode",
        "adapter_mode",
        "expected_active",
    },
    "scrape/info identity labels must remain bounded and distinct from event dimensions",
)

for field in ("pipelineRunId", "correlationId", "causationId", "messageId", "idempotencyKey", "traceparent"):
    require(field in POLICY["labels"]["structuredLogFieldsOnly"], f"{field} must remain a structured log field only")

coverage = POLICY["topologyCoverage"]
expected_services = {
    "scheduler",
    "fetcher",
    "canonicalizer",
    "enrichment",
    "approval",
    "translation",
    "persistence",
    "publication",
}
require(set(coverage["services"]) == expected_services, "policy must cover every worker-uplift service")
require("backend.nutsnews.com" in coverage["backendHosts"], "policy must cover the backend host")
require(len(coverage["routes"]) == 7, "policy must cover all seven worker-uplift routes")

retry_count = sum(len(route["retryQueues"]) for route in coverage["routes"])
dlq_count = sum(1 for route in coverage["routes"] if route["terminalDlq"].endswith(".dlq"))
main_count = sum(1 for route in coverage["routes"] if route["mainQueue"].startswith("nutsnews.worker."))
counts = POLICY["cardinalityAndVolumeEstimate"]["queueClassCounts"]
require(main_count == counts["stageQueues"] == 7, "policy must count all stage queues")
require(retry_count == counts["retryQueues"] == 21, "policy must count all retry queues")
require(dlq_count == counts["terminalDlqs"] == 7, "policy must count all terminal DLQs")
require(main_count + retry_count + dlq_count == counts["totalRabbitmqQueues"] == 35, "policy must count all RabbitMQ queues")

series = POLICY["cardinalityAndVolumeEstimate"]["activeSeriesCeilings"]
require(series["totalWorkerUpliftAndHostCeiling"] <= 5000, "approved active-series ceiling must remain bounded")
require(series["rabbitmqQueueSeries"] >= counts["totalRabbitmqQueues"], "queue metric estimate must cover every queue")

logs = POLICY["cardinalityAndVolumeEstimate"]["monthlyLogCeilingsGb"]
require(logs["backendHostTotalIncludingWorker"] >= logs["workerServicesNormal"], "backend host log ceiling must include worker services")
require(logs["backendHostTotalIncludingWorker"] <= 5.0, "backend host worker-uplift log ceiling must stay bounded")

require(
    POLICY["grafanaBudget"]["steadyStateActiveSeriesCeiling"] == 7000,
    "steady-state active series ceiling must preserve free-tier headroom",
)
require(
    POLICY["grafanaBudget"]["syntheticApiExecutionCeilingMonthly"] == 90000,
    "synthetic API execution ceiling must preserve free-tier headroom",
)
require(
    "grafanacloud_instance_active_series / on(id) grafanacloud_instance_metrics_limits"
    in POLICY["grafanaBudget"]["guardrailSource"],
    "policy must name the documented active-series numerator/denominator join",
)
require(
    signals["sentry_exceptions_and_replays"]["destination"] == "sentry",
    "Sentry must remain the canonical scrubbed exception/replay provider",
)
require(
    all(signals[name]["destination"] == "none" for name in ("traces", "exemplars", "profiles", "faro_frontend_telemetry")),
    "Tempo, exemplars, profiling, and Faro must remain deferred with no destination",
)

for stale in (
    "max(grafanacloud_instance_metrics_usage) / ${var.free_metrics_active_series_monthly}",
    "max(grafanacloud_logs_instance_usage) / ${var.free_logs_ingested_gb_monthly}",
):
    require(stale not in LOCALS, f"quota guardrail still uses hard-coded assumption: {stale}")

for token in (
    "grafanacloud_instance_active_series",
    "grafanacloud_instance_metrics_limits",
    "grafanacloud_logs_instance_active_streams",
    "grafanacloud_logs_instance_limits",
    "grafanacloud_logs_instance_bytes_received_per_second",
    "grafanacloud_traces_instance_bytes_received_per_second",
    "grafanacloud_traces_instance_limits",
):
    require(token in LOCALS, f"quota guardrail missing live usage/limit metric {token}")

require(
    r'max(grafanacloud_instance_active_series / on(id) grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})'
    in LOCALS,
    "metrics active-series quota must join the live series and matching per-instance limit on id",
)
require(
    "grafanacloud_instance_metrics_usage" not in LOCALS,
    "obsolete grafanacloud_instance_metrics_usage must not be reintroduced",
)

for token in (
    "no_data_state  = rule.value.no_data_state",
    "Grafana Cloud traces ingestion rate",
):
    require(token in ALERTS or token in LOCALS, f"alert guardrail missing {token}")

for token in (
    "worker-uplift telemetry scope",
    "worker-uplift-telemetry-scope.json",
):
    require(token in README, f"module README must point to the worker telemetry policy: {token}")

print("Worker-uplift telemetry policy guardrails passed.")
