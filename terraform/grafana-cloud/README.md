# Grafana Cloud Observability

This OpenTofu module manages Grafana Cloud folders, dashboards, alert routing, quota and pipeline guardrails, backend host observability imports, five Synthetic Monitoring HTTP checks, and four Grafana SLOs for NutsNews hosts.

## Ownership

Grafana management/service-account credentials stay only in ramideltoro/nutsnews-infra. Host repositories may keep only telemetry write credentials needed by their collectors, such as Prometheus remote_write and Loki push credentials. nutsnews-backend is a telemetry producer and collector owner; it is not the Grafana resource provisioner after this handoff.

| Scope | Host | Folder UID | OpenTofu address | Owning repository |
| --- | --- | --- | --- | --- |
| VPS observability | `vps.nutsnews.com` | `nutsnews-observability` | `grafana_folder.observability` | `ramideltoro/nutsnews-infra` |
| Backend observability | `backend.nutsnews.com` | `nutsnews-backend-ops` | `grafana_folder.backend_observability` | `ramideltoro/nutsnews-infra` |

Backend dashboards are managed at `grafana_dashboard.backend_observability["<dashboard_uid>"]`, and the backend alert group is managed at `grafana_rule_group.backend_guardrails`. The backend catalog in `catalog/backend-observability.json` preserves the UIDs already used by the previous direct API provisioning path so OpenTofu can import existing objects instead of creating duplicate dashboards or alert rules. A catalog dashboard may set `importExisting` to `false` only when a protected apply proves the UID is missing remotely; OpenTofu then creates that missing dashboard from the same catalog.

Do not remove existing backend Grafana resources until import and query/alert verification pass. The protected apply workflow uploads `grafana-cloud-post-apply-verification`, and backend direct provisioning should remain retired only after that report shows the backend folder, dashboards, alert rules, Prometheus queries, and Loki queries are present.

## Worker-Uplift Telemetry Scope

The worker-uplift telemetry scope is approved in `catalog/worker-uplift-telemetry-scope.json`. It makes RabbitMQ metrics, worker service metrics, and structured logs required; keeps Tempo traces, exemplars, profiling, and Faro deferred; retains Sentry as the canonical scrubbed exception/replay provider; and forbids article/model payload telemetry. Application event and histogram dimensions are bounded to `service`, `stage`, `queue`, `outcome`, `dependency`, `language`, `provider`, `probe`, and `check`. Loki indexing is bounded separately to `deployment_environment`, `service`, `service_version`, `host`, `source`, and `severity`; identifiers remain structured metadata.

This policy is source-controlled only and does not enable the worker-uplift production path. The shared operating guide is `ramideltoro/nutsnews-docs/NUTSNEWS_WORKER_UPLIFT_TELEMETRY_SCOPE.md`.

## Worker-Uplift RabbitMQ Dashboards

Issue `ramideltoro/nutsnews-worker#89` adds three source-created dashboards to
the backend ops folder:

- `NutsNews Worker-Uplift RabbitMQ Overview`
- `NutsNews Worker-Uplift Queue Drilldown`
- `NutsNews Worker-Uplift RabbitMQ Resources`

The dashboards use bounded `environment`, `host`, `vhost`, `stage`, `queue`,
and `service` variables. The `queue` variable lists all 35 declared main,
retry, and DLQ names so operators can select any contract queue without editing
queries. Queue and service panels include Grafana Explore links to filtered
Loki logs. Trace links are intentionally absent because traces remain deferred
under the approved worker-uplift telemetry policy.

## Worker-Uplift Alerts And SLOs

Issue `ramideltoro/nutsnews-worker#90` adds the source-created
`NutsNews Worker-Uplift Pipeline SLOs` dashboard and the
`NutsNews Worker-Uplift RabbitMQ Guardrails` alert group to the backend ops
folder. The worker-uplift RabbitMQ alert and SLO catalog is
`catalog/worker-uplift-rabbitmq-alerts.json`; OpenTofu owns it through
`grafana_rule_group.worker_uplift_guardrails` and the existing backend dashboard
resource.

The alert catalog covers broker down, private canary failure, Alloy
scrape/write loss, zero consumers on any main queue even when that queue is
empty, sustained production-owned queue backlog or oldest-unconfirmed durable
outbox age, per-queue publish/ack imbalance across the seven main worker queues,
unacked growth, DLQs, retry and
redelivery pressure, connection churn, broker memory/disk alarms, low disk,
file descriptor pressure, stale recovery proof, repeated restarts, and
multi-window SLO burn-rate alerts. Every rule carries severity, owner, route,
service, queue, threshold, recovery window, runbook URL, and maintenance
suppression metadata.

The SLO dashboard exposes broker availability, private canary success and
latency, stage success/latency, feed freshness, retry/DLQ budget, final
publication success, alert state, canary fixtures, and recovery proof age.
Seven delivery processors initialize their bounded terminal-outcome counters
and fixed-bucket histogram series to zero before traffic, so acceptance is
deterministic without synthetic Prometheus samples. Worker-owned paging remains
host-gated while the deployment is shadow-only.

Use the backend `Backend RabbitMQ Canary` workflow from #91 to exercise alert
firing and recovery with fixed drills such as `network-interruption`,
`invalid-credentials`, `consumer-loss`, `disk-watermark`, `full-queue`,
`poison-message`, and `grafana-connectivity-loss`. The drill path must not
publish production articles, expose private AMQP endpoints, disable legacy
ingestion/failover, or mutate contact points. Recover by running a normal
canary after each fixture and waiting for the Grafana recovery window.

## State

The repo did not previously have a remote Terraform/OpenTofu backend pattern. This module declares a partial `s3` backend and intentionally commits no backend coordinates, state files, tfvars, Grafana URLs, tenant IDs, usernames, or tokens.

Do not apply this module until a protected remote state backend is configured through the `production-vps` GitHub Environment secret `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`.

If there is no existing S3-compatible remote state bucket, use the one-time [`grafana-state-bootstrap/cloudflare-r2`](../grafana-state-bootstrap/cloudflare-r2/README.md) module through the protected `Grafana State Bootstrap` workflow to create a private Cloudflare R2 bucket first. Then create a bucket-scoped R2 S3 API token and store the backend config in `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`.

## Required Inputs

Supply these values through protected GitHub environment secrets or local environment variables, not committed files:

- `TF_VAR_grafana_url`
- `TF_VAR_grafana_service_account_token`
- `TF_VAR_prometheus_datasource_uid`
- `TF_VAR_loki_datasource_uid`
- `TF_VAR_usage_datasource_uid`
- `TF_VAR_operations_email_recipients`, sourced from the existing protected `NUTSNEWS_EMAIL_TO` secret

The service account token should be scoped to manage Grafana folders, dashboards, alert rules, and Synthetic Monitoring checks. Telemetry write tokens are separate and belong to the Ansible-managed Alloy deployment.

Backend telemetry write credentials remain in `ramideltoro/nutsnews-backend` for the backend Alloy deployment. Do not add `GRAFANA_URL` or a Grafana service account token back to the backend repository; use this infra module and the protected `production-vps` environment for Grafana resource management.

## Synthetic Checks

Production plan/apply requires both of these protected inputs:

- `TF_VAR_synthetic_monitoring_probe_ids`
- `TF_VAR_synthetic_http_checks`
- `TF_VAR_synthetic_major_forecast_acknowledged`, sourced from the protected environment variable `NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED`

Also supply `GRAFANA_SM_ACCESS_TOKEN` and `GRAFANA_SM_URL` from the protected
GitHub Environment secrets
`NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN` and
`NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL`. The URL must be the regional
Synthetic Monitoring API backend reported by the live Grafana plugin, not the
Grafana stack UI URL. The provider uses this separate endpoint and token for
`grafana_synthetic_monitoring_check` resources; the controlled mismatch drill
uses the same pair for exact snapshot/update/restore operations.

Copy the stack-region endpoint from **Testing & synthetics > Synthetics >
Config > General**. The protected validator accepts only a query-free HTTPS
origin in the `synthetic-monitoring-api*.grafana.net` service family, so plan
and apply fail closed before attaching the token when the endpoint is absent,
malformed, or belongs to another Grafana service role.

The check map must contain exactly `canonical_homepage`, `canonical_readiness`, `canonical_articles_api`, `vps_readiness`, and `vercel_secondary_readiness`, use exactly two unique public probes, and run every five minutes. The three canonical checks share one host; direct VPS and Vercel-secondary readiness use two other distinct hosts. Targets are credential-free, query-free HTTPS URLs on port 443 with exact approved paths. Every check must assert response content or headers in addition to status. Refresh, controller, ingestion, trigger, and publication routes are rejected. Checks stay in Grafana's default Synthetic Monitoring folder; do not assign the general NutsNews dashboard folder because Synthetic Monitoring only supports its own folder tree.

Example shape for one member of the protected checks map:

```hcl
canonical_readiness = {
    target                          = "https://<protected-target>/readyz"
    frequency_ms                    = 300000
    timeout_ms                      = 5000
    valid_status_codes              = [200]
    fail_if_body_matches_regexp     = ["deploymentTarget.*unknown"]
    fail_if_body_not_matches_regexp = [
      "ready.*true",
      "deploymentTarget.*(production-vps|vercel-production)",
    ]
    fail_if_header_not_matches_regexp = [{
      allow_missing = false
      header        = "Cache-Control"
      regexp        = "no-store"
    }]
  }
```

Keep real targets in protected variables or untracked local tfvars. The input schema preserves Grafana's 10-second through 60-minute API-check bounds and one- through 60-second timeout bounds, while the production policy requires exactly five checks across two probes every five minutes. That topology projects to 86,400 executions in a 30-day month. The module blocks at or above the lower of 90% of the configured free API allowance and the absolute 90,000-execution ceiling; the literal topology consumes 86.4% of the 100,000-execution allowance and therefore enters the 85% `major` forecast band. The protected validator emits only counts, interval bounds, the projected execution total, thresholds, and the reviewed-decision state; it never emits check names, targets, probe IDs, or credentials.

That contradiction is an explicit fail-closed rollout decision, not an accepted default. Production plan/apply keeps `enforce_rollout_decisions=true` and fails until a reviewer chooses one of: retain the requested topology and set the protected `NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED=true`; change cadence/topology in source; or change the threshold/allowance in source with supporting quota evidence. Setting the acknowledgment true means only that the operator chose the standing-major five-check/two-probe/five-minute option; it must not silence or reclassify the alert. `enforce_rollout_decisions=false` is permitted only for an explicitly non-mutating static CI fixture, never a saved production plan or apply.

The Terraform variable defaults remain empty for backendless local validation; resource preconditions and protected production workflows reject plan/apply unless all five checks and two probes are present.

Post-apply verification enumerates `GET /api/v1/check`, compares the exact managed IDs, public probe IDs, protected targets, and all status/body/header assertion families, rejects any enabled browser or unmanaged API check, and forecasts every enabled API check from its live frequency and probe count. Those raw provider values remain in memory only. The uploaded report uses a closed schema of bounded statuses, counts, booleans, label-key structure, and source-catalog-UID-keyed SHA-256 definition fingerprints needed for the reviewed vendor-rule baseline; it omits check IDs, jobs, probe IDs, targets, provider text, raw errors, and scheduling details. It then polls for up to 13 minutes for a source-fresh `timestamp(probe_success)` sample from exactly two distinct probes on one current `config_version` for every check.

`Grafana Cloud Synthetic Inventory Audit` repeats the read-only remote inventory/quota check daily between applies. It runs from the dedicated exact-main `grafana-observability-readonly` Environment without a reviewer gate and receives only a Synthetic Monitoring reader token plus bounded expected inventory/configuration; see `runbooks/GRAFANA_OBSERVABILITY_READONLY_ENVIRONMENT.md`. The VPS collector reads only that workflow's scheduled-run status from GitHub, exports its conclusion plus last-run/last-success ages, and Grafana alerts on a failed audit or a 30-hour dead-man breach. The controlled mismatch drill accepts one source-controlled check target at a time and covers status, body, and header assertion failures for all five checks. Before the parent can mutate Synthetic Monitoring, it dispatches a separate protected recovery watchdog. That watchdog verifies the exact live parent run and revision, fetches the remote check, keeps the complete restore payload only in a mode-`0600` runner-local snapshot, and publishes a sanitized, freshness-bounded armed handshake. An HMAC binds that handshake to the private payload so the parent fails closed if the check changes before injection. The parent self-restores, publishes a sanitized release handshake, and waits for the watchdog to exact-restore and verify independently; a missing release instead triggers restoration after a bounded 7,200-second hold or when the parent terminates. Neither targets nor assertion values are uploaded. Restoration writes only when the current check is either the saved state or this drill's one approved mutation, so a later unrelated configuration change is never overwritten.

The watchdog intentionally uses a separate per-check concurrency group. Once the parent failure-drill workflow shares the `grafana-cloud-apply` group, reusing that group in the child would deadlock because the parent waits for the child while holding the group. On the normal release path, the child restores while the still-running parent transitively excludes Grafana applies. If the parent terminates or remains hung until the watchdog deadline, that transitive lock can disappear; the exact-state ownership check is the fail-closed fallback and may report recovery failure instead of overwriting a newer apply. This is the safest serialization GitHub Actions can provide without uploading the private snapshot or introducing an external lock service.

## Alert Delivery, SLOs, And Ownership

`grafana_contact_point.operations_email` reuses `NUTSNEWS_EMAIL_TO`, sends firing and resolved notifications, and is protected from destroy. The global managed policy routes `critical|major` with 30-second grouping, five-minute updates, and hourly repeats; `warning|minor|low` uses five-minute grouping, 15-minute updates, and six-hour repeats. Every Terraform-managed rule carries bounded owner, route, service, severity, environment, dashboard, and runbook context.

`grafana_slo.nutsnews` creates public availability (99.5%), API latency (95% of successful checks within 750 ms), feed freshness (99% of valid durable observations within 15 minutes), and worker terminal success (99%) objectives over 30 days. Public and API SLOs use Grafana's documented gauge execution ratios over `$__interval`; feed freshness is a ratio of good valid observations to all valid observations, so it remains event-style and supports generated burn alerts. The API latency denominator contains only successful article-API checks; failed article-API probes trigger the all-check synthetic operational alert and do not dilute the latency objective. Fast/slow burn alerts are generated for the first three. Worker terminal burn alerts stay disabled while the split worker path is shadow-only.

The `integration---linux-node` inventory distinguishes 24 vendor alert rules from 16 recording rules. Thirty-five rules remain retained and integration-owned. The five legacy `asserts-node.rules` recording rules are explicitly marked for removal only through the supported Linux integration 1.6.3 upgrade, whose changelog says the Asserts base pipeline now provides them; the post-upgrade inventory is therefore 24 alerts plus 11 recording rules. Post-apply verification recognizes the reviewed 40-rule pre-upgrade shape for diagnosis but passes only the exact 35-rule post-upgrade shape, rejects partial or unknown replacement inventories, and checks each retained rule's live folder/group/title/kind, integration marker, query material, and evaluation health. Never delete the five recording rules by UID; use the supported integration upgrade.

The 24 vendor alerts are not exempt from the universal NutsNews label and annotation contract. Their catalog status is deliberately `blocked_pending_owned_replacements_or_supported_vendor_relabel`, and authenticated post-apply verification fails until each alert has normalized `severity`, `owner`, `route`, `service`, `deployment_environment`, dashboard, and runbook context. Resolve that blocker only by proving a supported in-place integration relabel or by comparing pinned official definitions, provisioning source-owned normalized equivalents, and then disabling vendor duplicates after authenticated equivalence review. Root-policy fallback alone is not normalization. Separately, the catalog starts with `baselineStatus=pending_authenticated_rollout`: the first authenticated verification exports deterministic definition hashes and fails closed. An operator must review the live definitions, commit `definitionFingerprintSha256` for all 35 retained rules, and set the status to `approved`; only subsequent matching runs validate definition drift. Until both blockers are resolved, do not claim all-rule normalization or fingerprint drift validation.

All dashboards include the `nutsnews-deployment` annotation stream. Deployment workflows should append promotion, rollback, failover, and database-provider events through the Grafana annotations API; Terraform-managed `grafana_annotation` is intentionally not used because it updates one stateful event rather than retaining append-only history. Database-provider start and outcome annotations cover every applied provider mutation, including an apply where the optional Vercel release dispatch is disabled. Final promotion annotations are dispatched as a separate workflow run: an annotation API outage leaves that observability run failed with a retained `delivery_unverified` receipt, but cannot change an already-authoritative production promotion result or cause the production mutation to be retried. Grafana UI/API publishers accept only the exact query-free `https://nutsnews.grafana.net` origin (with optional explicit port 443) before attaching a bearer token; Synthetic Monitoring clients independently require the `synthetic-monitoring-api*.grafana.net` service family and every client refuses redirects. The `NutsNews Current Production Ownership` dashboard observes the routed web target, database provider, and web revision from the canonical `https://www.nutsnews.com/readyz` response after validating status, no-store semantics, bounded identity values, and matching identity headers; it joins that observation with the VPS deployment receipt for the infrastructure revision. The ingestion-owner and worker mode/write-gate cards consume the backend host's protected deployment signal, validate its mode/expected-active pair, and gate it on exporter freshness. This signal reflects the protected deployment configuration; a future direct read of `worker_uplift_final.cutover_control` is required before calling it database-confirmed cutover state. The remaining cards show backend API revision, host-verified version/revision/running-image-digest identity for all eight workers, runtime deployment/adapter identity, ownership-aware readiness, and source freshness. Until telemetry-enabled worker images are published and repinned, the runtime deployment and readiness cards explicitly show that rollout dependency rather than inferring health from the host image inventory. Shadow worker readiness renders as an explicit disabled state; only production-owned workers are required to report `outcome="ok"`.

## Backend Import Handoff

The backend import blocks are declared in `imports.tf`:

- `grafana_folder.backend_observability` imports `nutsnews-backend-ops`.
- `grafana_dashboard.backend_observability[each.key]` imports each existing dashboard by UID from `catalog/backend-observability.json`; catalog entries with `importExisting = false` are created from source instead of imported.
- `grafana_rule_group.backend_guardrails` imports `nutsnews-backend-ops:NutsNews Backend Guardrails`.

Run the protected `Grafana Cloud Plan` workflow first. It performs a normal plan and a refresh-only drift check against remote state. If drift is reported, reconcile it before applying.

After merge, run `Grafana Cloud Apply` from `main`. The workflow applies the remote-state-backed plan and then runs `scripts/verify_post_apply.py --require-query-data`. The required data checks include backend host metrics, RabbitMQ aggregate/detailed metrics, backend host logs, backend journal logs, and worker-uplift RabbitMQ container logs. Treat a failed verification as a blocked handoff: keep the legacy backend resources intact, fix the missing import/query/alert condition, and rerun plan/apply.

Rollback is GitOps-based: revert the infra PR on `main`, run `Grafana Cloud Plan`, confirm the plan does not destroy protected folders/dashboards/rule groups unexpectedly, and then run `Grafana Cloud Apply`. The managed folders, dashboards, and rule groups use `prevent_destroy` so destructive rollback requires an explicit reviewed code change.

## Free-Tier And Live-Limit Guardrails

The committed defaults for optional Synthetic Monitoring and k6 still assume the current Grafana Cloud Free limits documented in the shared runbook. Check Grafana pricing before changing them:

- Synthetic Monitoring API tests: 100,000 executions per month.
- Synthetic Monitoring browser tests: 10,000 executions per month.
- k6: 500 virtual user hours per month.

Metrics active-series quota uses `grafanacloud_instance_active_series` joined to the matching `grafanacloud_instance_metrics_limits` series on `id`. Logs and traces use their live usage/limit series instead of hard-coded free-plan constants. Threshold alerts remain at 70%, 85%, and 95% and use `NoData=OK`; a separate major alert detects missing required usage numerator/denominator telemetry. Trace threshold `NoData` is expected because full trace export and exemplars remain deferred.

The `NutsNews Logs Overview` dashboard uses the Loki datasource for source, service, level, systemd unit, Docker container, Caddy status-class, and recent-error views. Log active-stream and ingest-rate quota risk are covered by the quota guardrail rules, while the log-pipeline rules alert on Alloy Loki dropped entries, write retries, and high error log volume.

## Local Validation

```bash
tofu fmt -recursive terraform/grafana-cloud
tofu -chdir=terraform/grafana-cloud init -backend=false -input=false
tofu -chdir=terraform/grafana-cloud validate -no-color
python3 terraform/grafana-cloud/tests/validate_dashboard_definitions.py
python3 terraform/grafana-cloud/tests/validate_grafana_ownership.py
python3 terraform/grafana-cloud/tests/validate_worker_uplift_telemetry_scope.py
python3 terraform/grafana-cloud/tests/validate_worker_uplift_rabbitmq_dashboards.py
python3 terraform/grafana-cloud/tests/validate_worker_uplift_alerts_slos.py
python3 terraform/grafana-cloud/tests/test_validate_synthetic_monitoring_inputs.py
python3 terraform/grafana-cloud/tests/validate_observability_enhancements.py
python3 terraform/grafana-cloud/tests/test_verify_post_apply.py
```
