# Grafana Cloud Observability

This OpenTofu module manages Grafana Cloud folders, dashboards, quota guardrail alert rules, log-pipeline alert rules, and optional Synthetic Monitoring HTTP checks for the NutsNews VPS.

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
