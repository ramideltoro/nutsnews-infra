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

## Remote State Bootstrap

If you do not already have an S3-compatible remote state bucket, use the protected `Grafana State Bootstrap` workflow before running Grafana Cloud plan/apply.

This workflow creates a private Cloudflare R2 bucket through `terraform/grafana-state-bootstrap/cloudflare-r2`. The bootstrap is intentionally separate from the Grafana Cloud module because the Grafana Cloud module cannot initialize its remote backend until the bucket already exists.

Cloudflare R2 currently includes a free monthly allowance for Standard storage and operations, and the OpenTofu state object should be tiny. R2 can still bill above included usage if the account is reused for other storage or high request volume, so check current pricing first: https://developers.cloudflare.com/r2/pricing/

Add these to the protected `production-vps` GitHub Environment before running the bootstrap workflow:

- `NUTSNEWS_CLOUDFLARE_ACCOUNT_ID`
- `NUTSNEWS_CLOUDFLARE_R2_ADMIN_API_TOKEN`

Where to find/create them:

- `NUTSNEWS_CLOUDFLARE_ACCOUNT_ID`: Cloudflare dashboard -> the account that owns R2 -> account home or account details. Use the account ID only in the protected GitHub Environment secret.
- `NUTSNEWS_CLOUDFLARE_R2_ADMIN_API_TOKEN`: Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> custom token with account-level R2 bucket management permission for the account. This token is only for creating the state bucket and is separate from the bucket-scoped S3 API credentials used by OpenTofu's backend.

Then run:

1. Open `Grafana State Bootstrap`.
2. Keep `bucket_name` as `nutsnews-grafana-cloud-tofu-state`, or choose another private lowercase bucket name.
3. Keep `location_hint` at the default unless you intentionally need a different R2 location.
4. Type `create-r2-state-bucket` in `confirm_bootstrap`.
5. Approve the `production-vps` Environment gate.

After the bucket exists, create an R2 S3 API token:

1. Cloudflare dashboard -> R2 object storage.
2. Under Account Details, select `Manage` next to API Tokens.
3. Create an account or user API token with `Object Read and Write`, scoped to the state bucket.
4. Copy the Access Key ID and Secret Access Key once, and store them only inside `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`.

Use this backend config shape for `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`, replacing placeholders outside Git:

```hcl
bucket                      = "nutsnews-grafana-cloud-tofu-state"
key                         = "grafana-cloud/terraform.tfstate"
region                      = "auto"
endpoints                   = { s3 = "https://<cloudflare-account-id>.r2.cloudflarestorage.com" }
access_key                  = "<r2-access-key-id>"
secret_key                  = "<r2-secret-access-key>"
skip_credentials_validation = true
skip_metadata_api_check     = true
skip_region_validation      = true
skip_requesting_account_id  = true
skip_s3_checksum            = true
use_path_style              = true
use_lockfile                = true
```

Do not paste account IDs, endpoints, access keys, secret keys, or the final backend config into Git, issues, PR comments, or chat.

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

### Secret Inventory

Store every value below in `ramideltoro/nutsnews-infra` -> Settings -> Environments -> `production-vps` -> Environment secrets.

| Secret | Where to find or create it | Used by |
| --- | --- | --- |
| `NUTSNEWS_CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard account details for the account that owns R2. | One-time R2 state bootstrap |
| `NUTSNEWS_CLOUDFLARE_R2_ADMIN_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> custom token with account-level R2 bucket management permission. | One-time R2 state bootstrap |
| `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG` | Build from the R2 backend config template after the bucket and bucket-scoped R2 S3 API token exist. | Grafana Cloud OpenTofu plan/apply |
| `NUTSNEWS_GRAFANA_CLOUD_URL` | Grafana Cloud portal -> your stack -> Grafana URL. | Grafana provider |
| `NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN` | Grafana UI -> Administration -> Users and access -> Service accounts -> create service account/token for Terraform-managed folders, dashboards, alerts, and synthetic checks. | Grafana provider |
| `NUTSNEWS_GRAFANA_CLOUD_PROMETHEUS_DATASOURCE_UID` | Grafana UI -> Connections -> Data sources -> Grafana Cloud Prometheus data source settings; copy the UID from the URL or JSON/API details. | Dashboards and alert rules |
| `NUTSNEWS_GRAFANA_CLOUD_LOKI_DATASOURCE_UID` | Grafana UI -> Connections -> Data sources -> Grafana Cloud Loki data source settings; copy the UID from the URL or JSON/API details. | Dashboards |
| `NUTSNEWS_GRAFANA_CLOUD_USAGE_DATASOURCE_UID` | Grafana UI -> Connections -> Data sources -> Grafana Cloud usage data source. If usage metrics are exposed through the same Prometheus data source, reuse that UID. | Usage/quota dashboard and alerts |
| `NUTSNEWS_GRAFANA_CLOUD_METRICS_URL` | Grafana Cloud portal -> your stack -> sending metrics / Prometheus remote_write endpoint. | Alloy metrics remote write |
| `NUTSNEWS_GRAFANA_CLOUD_METRICS_USERNAME` | Grafana Cloud portal -> your stack -> sending metrics / Prometheus username or instance ID. | Alloy metrics remote write |
| `NUTSNEWS_GRAFANA_CLOUD_LOGS_URL` | Grafana Cloud portal -> your stack -> sending logs / Loki endpoint. | Alloy Loki write |
| `NUTSNEWS_GRAFANA_CLOUD_LOGS_USERNAME` | Grafana Cloud portal -> your stack -> sending logs / Loki username or instance ID. | Alloy Loki write |
| `NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN` | Grafana Cloud portal -> Security -> Access Policies -> create token with `metrics:write` and `logs:write` scoped to this stack. | Alloy telemetry writes |
| `NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON` | Grafana Cloud Synthetic Monitoring -> probes; JSON array of selected low-cost public probe IDs. | Optional synthetic checks |
| `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON` | Hand-authored protected JSON object of public-safe endpoints and intervals. Keep URLs outside Git. | Optional synthetic checks |

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
