# Grafana Cloud Observability

This OpenTofu module manages Grafana Cloud folders, dashboards, quota guardrail alert rules, log-pipeline alert rules, backend host observability imports, and optional Synthetic Monitoring HTTP checks for NutsNews hosts.

## Ownership

Grafana management/service-account credentials stay only in ramideltoro/nutsnews-infra. Host repositories may keep only telemetry write credentials needed by their collectors, such as Prometheus remote_write and Loki push credentials. nutsnews-backend is a telemetry producer and collector owner; it is not the Grafana resource provisioner after this handoff.

| Scope | Host | Folder UID | OpenTofu address | Owning repository |
| --- | --- | --- | --- | --- |
| VPS observability | `vps.nutsnews.com` | `nutsnews-observability` | `grafana_folder.observability` | `ramideltoro/nutsnews-infra` |
| Backend observability | `backend.nutsnews.com` | `nutsnews-backend-ops` | `grafana_folder.backend_observability` | `ramideltoro/nutsnews-infra` |

Backend dashboards are managed at `grafana_dashboard.backend_observability["<dashboard_uid>"]`, and the backend alert group is managed at `grafana_rule_group.backend_guardrails`. The backend catalog in `catalog/backend-observability.json` preserves the UIDs already used by the previous direct API provisioning path so OpenTofu can import existing objects instead of creating duplicate dashboards or alert rules. A catalog dashboard may set `importExisting` to `false` only when a protected apply proves the UID is missing remotely; OpenTofu then creates that missing dashboard from the same catalog.

Do not remove existing backend Grafana resources until import and query/alert verification pass. The protected apply workflow uploads `grafana-cloud-post-apply-verification`, and backend direct provisioning should remain retired only after that report shows the backend folder, dashboards, alert rules, Prometheus queries, and Loki queries are present.

## Worker-Uplift Telemetry Scope

The worker-uplift telemetry scope is approved in `catalog/worker-uplift-telemetry-scope.json`. It makes RabbitMQ metrics, worker service metrics, and structured logs required; keeps traces and exemplars deferred; forbids profiles and article/model payload telemetry; and fixes the worker metric/Loki stream label set to `environment`, `host`, `service`, `version`, `queue`, and `outcome`.

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

The service account token should be scoped to manage Grafana folders, dashboards, alert rules, and Synthetic Monitoring checks. Telemetry write tokens are separate and belong to the Ansible-managed Alloy deployment.

Backend telemetry write credentials remain in `ramideltoro/nutsnews-backend` for the backend Alloy deployment. Do not add `GRAFANA_URL` or a Grafana service account token back to the backend repository; use this infra module and the protected `production-vps` environment for Grafana resource management.

## Optional Synthetic Checks

Synthetic checks are disabled unless both of these are supplied:

- `TF_VAR_synthetic_monitoring_probe_ids`
- `TF_VAR_synthetic_http_checks`

When synthetic checks are enabled, also supply `GRAFANA_SM_ACCESS_TOKEN` from the protected GitHub Environment secret `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN`. The Grafana provider uses this separate Synthetic Monitoring token for `grafana_synthetic_monitoring_check` resources.

Example shape for the checks variable:

```hcl
synthetic_http_checks = {
  public_health = {
    target             = local.public_health_url
    frequency_ms       = 1800000
    timeout_ms         = 5000
    valid_status_codes = [200]
  }
}
```

Keep real targets in protected variables or untracked local tfvars. The module requires a 15-minute or slower interval and blocks apply when the projected monthly API executions exceed 70% of the configured free-tier assumption.

Set `TF_VAR_synthetic_http_checks` to `{}` to disable Synthetic Monitoring resources while keeping dashboards and quota alerts managed.

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

Metrics, logs, and traces quota guardrails use live `grafanacloud_*_usage` and `grafanacloud_*_limits` data from the `grafanacloud-usage` datasource instead of hard-coded free-plan constants. Current alert thresholds are 70%, 85%, and 95% of the live platform limit for metrics active series, log active streams, log ingestion rate, and trace ingestion rate. Trace alert `NoData` is OK because full worker trace export and exemplars are explicitly deferred.

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
```
