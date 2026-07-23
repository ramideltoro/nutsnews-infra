# Grafana Cloud Observability

This OpenTofu module manages Grafana Cloud folders, dashboards, quota guardrail alert rules, log-pipeline alert rules, backend host observability imports, and optional Synthetic Monitoring HTTP checks for NutsNews hosts.

## Ownership

Grafana management/service-account credentials stay only in ramideltoro/nutsnews-infra. Host repositories may keep only telemetry write credentials needed by their collectors, such as Prometheus remote_write and Loki push credentials. nutsnews-backend is a telemetry producer and collector owner; it is not the Grafana resource provisioner after this handoff.

| Scope | Host | Folder UID | OpenTofu address | Owning repository |
| --- | --- | --- | --- | --- |
| VPS observability | `vps.nutsnews.com` | `nutsnews-observability` | `grafana_folder.observability` | `ramideltoro/nutsnews-infra` |
| Backend observability | `backend.nutsnews.com` | `nutsnews-backend-ops` | `grafana_folder.backend_observability` | `ramideltoro/nutsnews-infra` |

Backend dashboards are managed at `grafana_dashboard.backend_observability["<dashboard_uid>"]`, and the backend alert group is managed at `grafana_rule_group.backend_guardrails`. The backend catalog in `catalog/backend-observability.json` preserves the UIDs already used by the previous direct API provisioning path so OpenTofu can import existing objects instead of creating duplicate dashboards or alert rules.

Do not remove existing backend Grafana resources until import and query/alert verification pass. The protected apply workflow uploads `grafana-cloud-post-apply-verification`, and backend direct provisioning should remain retired only after that report shows the backend folder, dashboards, alert rules, Prometheus queries, and Loki queries are present.

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
- `grafana_dashboard.backend_observability[each.key]` imports each dashboard by UID from `catalog/backend-observability.json`.
- `grafana_rule_group.backend_guardrails` imports `nutsnews-backend-ops:NutsNews Backend Guardrails`.

Run the protected `Grafana Cloud Plan` workflow first. It performs a normal plan and a refresh-only drift check against remote state. If drift is reported, reconcile it before applying.

After merge, run `Grafana Cloud Apply` from `main`. The workflow applies the remote-state-backed plan and then runs `scripts/verify_post_apply.py --require-query-data`. Treat a failed verification as a blocked handoff: keep the legacy backend resources intact, fix the missing import/query/alert condition, and rerun plan/apply.

Rollback is GitOps-based: revert the infra PR on `main`, run `Grafana Cloud Plan`, confirm the plan does not destroy protected folders/dashboards/rule groups unexpectedly, and then run `Grafana Cloud Apply`. The managed folders, dashboards, and rule groups use `prevent_destroy` so destructive rollback requires an explicit reviewed code change.

## Free-Tier Assumptions

The committed defaults assume the current Grafana Cloud Free limits documented in the shared runbook. Check Grafana pricing before changing them:

- Metrics: 10,000 active series per month.
- Logs: 50 GB ingested per month with 14-day retention.
- Synthetic Monitoring API tests: 100,000 executions per month.
- Synthetic Monitoring browser tests: 10,000 executions per month.
- k6: 500 virtual user hours per month.

The `NutsNews Logs Overview` dashboard uses the Loki datasource for source, service, level, systemd unit, Docker container, Caddy status-class, and recent-error views. Log ingest and active stream quota risk are covered by the quota guardrail rules, while the log-pipeline rules alert on Alloy Loki dropped entries, write retries, and high error log volume.

## Local Validation

```bash
tofu fmt -recursive terraform/grafana-cloud
tofu -chdir=terraform/grafana-cloud init -backend=false -input=false
tofu -chdir=terraform/grafana-cloud validate -no-color
```
