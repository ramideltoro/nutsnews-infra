# Grafana Cloud Observability Runbook

Use this runbook to enable Grafana Cloud observability for the NutsNews VPS through GitOps-managed Ansible and OpenTofu.

## What This Adds

- Optional Grafana Alloy installation on the VPS.
- Linux host metrics from Alloy's Unix exporter.
- Docker/container metrics and logs when Docker is present.
- Journald, auth, Caddy, app/service, backup, reporting, and Ops Portal logs with redaction and rate controls.
- Low-cardinality NutsNews status metrics derived from the read-only Ops Portal status JSON.
- Grafana Cloud folders, dashboards, and quota guardrail alert rules managed by OpenTofu.
- Optional low-frequency Synthetic Monitoring HTTP checks when targets and probe IDs are supplied outside Git.

The VPS side remains read-only and agent-based. This change does not add portal mutation buttons, arbitrary shell access, or broad workflow dispatch command execution.

## Grafana Cloud Secrets

Add these to the protected `production-vps` GitHub Environment before enabling Alloy telemetry writes:

- `NUTSNEWS_GRAFANA_CLOUD_METRICS_URL`
- `NUTSNEWS_GRAFANA_CLOUD_METRICS_USERNAME`
- `NUTSNEWS_GRAFANA_CLOUD_LOGS_URL`
- `NUTSNEWS_GRAFANA_CLOUD_LOGS_USERNAME`
- `NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN`

The token must be a Grafana Cloud Access Policy token that can write metrics and logs. Do not use a Grafana service account token for telemetry writes.

Add these to the same environment before running Grafana Cloud OpenTofu plan/apply:

- `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`
- `NUTSNEWS_GRAFANA_CLOUD_URL`
- `NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN`
- `NUTSNEWS_GRAFANA_CLOUD_PROMETHEUS_DATASOURCE_UID`
- `NUTSNEWS_GRAFANA_CLOUD_LOKI_DATASOURCE_UID`
- `NUTSNEWS_GRAFANA_CLOUD_USAGE_DATASOURCE_UID`

The service account token should be scoped to manage Grafana folders, dashboards, alert rules, and Synthetic Monitoring checks. Keep Terraform state remote; do not commit state, tfvars, backend coordinates, tenant IDs, endpoints, usernames, or tokens.

Optional Synthetic Monitoring secrets:

- `NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON`: JSON array of probe IDs.
- `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON`: JSON object of HTTP checks.

Keep real target URLs in the protected secret JSON or local untracked variables, not in Git.

## Free-Quota Guardrails

The current committed assumptions are:

- Metrics: 10,000 active series per month.
- Logs: 50 GB ingested per month.
- Synthetic Monitoring API tests: 100,000 executions per month.
- Synthetic Monitoring browser tests: 10,000 executions per month.
- k6: 500 virtual user hours per month.

Grafana can change these limits. Check the live pricing page before enabling more telemetry: https://grafana.com/pricing/

Grafana Cloud publishes usage and limit metrics in the `grafanacloud-usage` datasource. The dashboard and alerts use those metrics where available: https://grafana.com/docs/grafana-cloud/cost-management-and-billing/manage-invoices/understand-your-invoice/usage-limits/

Synthetic Monitoring execution estimate:

```text
probes x tests x rounded-duration-minutes x (43200 / frequency-minutes)
```

The OpenTofu module blocks apply when configured API checks exceed 70% of the current free API execution assumption. Browser checks and cloud k6 runs are not enabled by default.

## Intentionally Excluded

- Debug/trace logs.
- Log lines larger than 8 KB.
- Rotated compressed logs and logs older than the Alloy file discovery window.
- High-cardinality labels such as container IDs, image IDs, request IDs, user IDs, raw IP addresses, and full dynamic paths.
- Traces, profiles, browser Synthetic Monitoring, and Grafana Cloud k6 execution until explicitly approved.

## Apply Grafana Assets

1. Open the `Grafana Cloud Plan` workflow.
2. Run it from the PR branch or from `main` after the protected secrets are configured.
3. Confirm OpenTofu fmt, validate, and plan output.
4. Merge the PR after required checks pass.
5. Open the `Grafana Cloud Apply` workflow on `main`.
6. Type `grafana-cloud` in `confirm_apply`.
7. Approve the `production-vps` Environment gate.
8. Review the final OpenTofu apply output and dashboard URLs.

If the backend secret is missing, stop and configure remote state before applying. Do not use local state from a GitHub Actions runner for production Grafana assets.

## Enable Alloy On The VPS

1. Open the `Protected Ansible Apply` workflow.
2. Set `run_mode` to `check`.
3. Set `enable_grafana_alloy` to `true`.
4. Keep `confirm_apply` blank.
5. Review the diff. Alloy should install from the Grafana apt repository, render `/etc/alloy/config.alloy`, render a root-only env file, create the textfile metrics timer, and validate the Alloy config.
6. Rerun with `run_mode=apply`, `confirm_apply=vps.nutsnews.com`, and `enable_grafana_alloy=true`.
7. Approve the `production-vps` Environment gate.

The existing protected apply workflow still connects as `nutsnews_ops`, never root SSH, and applies only the declared Ansible baseline.

## Verify Telemetry

Use Grafana Explore after apply:

```promql
up{service_namespace="nutsnews"}
node_load1{service_namespace="nutsnews"}
nutsnews_ops_portal_status_available{service_namespace="nutsnews"}
nutsnews_backup_last_success{service_namespace="nutsnews"}
```

Use Loki Explore:

```logql
{service_namespace="nutsnews"}
{service_namespace="nutsnews", log_source="auth"}
{service_namespace="nutsnews", log_source="docker"}
```

Use Synthetic Monitoring metrics when checks are configured:

```promql
probe_success{service_namespace="nutsnews"}
```

Use quota metrics:

```promql
grafanacloud_instance_metrics_limits
grafanacloud_logs_instance_limits
```

Expected dashboards are in the `NutsNews Observability` folder:

- NutsNews VPS Overview
- NutsNews CPU Load Processes
- NutsNews Memory Swap
- NutsNews Disk Filesystem IO
- NutsNews Network Caddy Edge
- NutsNews Docker Compose Containers
- NutsNews Systemd Services Timers
- NutsNews Logs Security Auth
- NutsNews Backups Restore Verification
- NutsNews Ops Portal Reporting
- NutsNews Application Service Health
- NutsNews Synthetic Uptime API Checks
- NutsNews Grafana Cloud Usage Quota

## Follow-Up App Hooks

This repo can observe deployment-owned container state, health, logs, and Caddy routing. Deeper application metrics, tracing, or structured request telemetry belong in `ramideltoro/nutsnews` or `ramideltoro/nutsnews-worker`. Create a follow-up issue or prompt there before changing application code.
