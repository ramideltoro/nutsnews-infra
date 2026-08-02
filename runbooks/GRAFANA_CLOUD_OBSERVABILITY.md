# Grafana Cloud Observability Runbook

Use this runbook to enable Grafana Cloud observability for NutsNews hosts through GitOps-managed Ansible and OpenTofu.

## What This Adds

- Grafana Alloy enabled as the production desired state on the VPS; disabling it requires the protected explicit-confirmation path.
- Linux host metrics from Alloy's Unix exporter.
- Host, systemd, journal/file, and textfile metrics without requiring Docker or containerd socket access.
- Bounded Docker CPU, memory, network, block-IO, PID, state, health, and restart metrics from the root-run textfile collector; cAdvisor remains disabled.
- Docker log discovery for the NutsNews Compose projects through the Docker API socket.
- Journald, auth, Caddy JSON access/error logs, app/service, backup, reporting, and Ops Portal logs with redaction and rate controls.
- Low-cardinality NutsNews status metrics derived from the read-only Ops Portal status JSON.
- Grafana Cloud folders, dashboards, operations-email routing, quota/pipeline guardrails, five synthetics, and four native SLOs managed by OpenTofu.
- Imported backend Grafana dashboards and alert rules that keep the existing backend UIDs.
- Source-controlled worker-uplift telemetry scope for required metrics/logs, Sentry-owned exceptions, deferred Tempo/exemplars/profiles/Faro, and bounded labels before production traffic.
- Five read-only HTTP synthetics from exactly two public probes every five minutes when protected targets and probe IDs are supplied outside Git.

The VPS side remains read-only and agent-based. This change does not add portal mutation buttons, arbitrary shell access, or broad workflow dispatch command execution.

## Centralized Grafana Ownership

Grafana management/service-account credentials stay only in ramideltoro/nutsnews-infra. Host repositories keep telemetry write credentials only when their collectors need them. nutsnews-backend is a telemetry producer and collector owner; it is not the Grafana resource provisioner after the import handoff.

| Scope | Host | Folder UID | OpenTofu address | Owning repository |
| --- | --- | --- | --- | --- |
| VPS observability | `vps.nutsnews.com` | `nutsnews-observability` | `grafana_folder.observability` | `ramideltoro/nutsnews-infra` |
| Backend observability | `backend.nutsnews.com` | `nutsnews-backend-ops` | `grafana_folder.backend_observability` | `ramideltoro/nutsnews-infra` |

Backend dashboards use `grafana_dashboard.backend_observability["<dashboard_uid>"]`, and backend alert rules are owned by `grafana_rule_group.backend_guardrails`. The source catalog is `terraform/grafana-cloud/catalog/backend-observability.json`.

Do not remove existing backend Grafana resources until import and query/alert verification pass. If a protected apply proves a catalog UID is missing remotely, set that dashboard's `importExisting` field to `false` with the apply evidence so OpenTofu creates the missing dashboard from source instead of failing import. The `Grafana Cloud Apply` workflow writes the `grafana-cloud-post-apply-verification` artifact after checking the backend folder, dashboards, alert rules, Prometheus query data, and backend host/source Loki query data.

Worker-uplift RabbitMQ dashboards from `ramideltoro/nutsnews-worker#89` are
source-created in the backend folder after the #141 ownership handoff:

- `NutsNews Worker-Uplift RabbitMQ Overview`
- `NutsNews Worker-Uplift Queue Drilldown`
- `NutsNews Worker-Uplift RabbitMQ Resources`

They use bounded `environment`, `host`, `vhost`, `stage`, `queue`, and
`service` variables. The `queue` variable lists all 35 declared worker-uplift
main, retry, and DLQ queues. Loki drill-down links filter by the approved
worker-uplift container labels. The pipeline-run dashboard parses
`pipelineRunId`, `pipeline_run_id`, `correlationId`, `correlation_id`, and
`traceparent` only at query time and adds per-field links back to the same
logs-only drilldown. Grafana provider 4.41 cannot safely merge derived-field
configuration into the existing Grafana Cloud Loki data source without taking
ownership of the whole data source, so this rollout deliberately uses
dashboard field links. The fallback is to paste the exact identifier into the
dashboard textbox or a bounded Loki Explore query. Tempo links are not present
because traces remain deferred by the approved #144 telemetry scope.

### Worker-Uplift Alerts And SLOs

Worker-uplift RabbitMQ alert and SLO assets from
`ramideltoro/nutsnews-worker#90` are source-controlled in
`terraform/grafana-cloud/catalog/worker-uplift-rabbitmq-alerts.json` and owned
by `ramideltoro/nutsnews-infra`.
The worker-uplift RabbitMQ alert and SLO ownership boundary stays in infra;
backend and worker repositories only emit telemetry.

Grafana objects:

- `NutsNews Worker-Uplift Pipeline SLOs`
  (`nutsnews-worker-uplift-slos`)
- `NutsNews Worker-Uplift RabbitMQ Guardrails`
  (`grafana_rule_group.worker_uplift_guardrails`)

The alert group covers broker down, private canary failure, Alloy scrape/write
loss, missing worker scrapes, zero consumers on any main queue even when that queue is empty, sustained
production-owned queue backlog or oldest-unconfirmed durable outbox age,
per-queue publish/ack imbalance across the same seven main worker queues,
unacked growth, DLQs, excessive retry/redelivery,
connection churn, memory/disk alarms, low disk, file descriptor pressure, stale
recovery proof, repeated restarts, and SLO burn-rate alerts. Consumer,
missing-series, scheduler-freshness, backlog, unacked, DLQ/retry,
stage-latency, retry/DLQ burn, and publication paging are
gated by the host-owned
`nutsnews_backend_worker_uplift_expected_active` signal. That durable protected
cutover signal remains present if every worker endpoint disappears, unlike a
worker-emitted series. Split-worker rules remain dashboard-only while the host
signal is `0`; durable public feed-freshness alerts protect the current
production feed independently of split-worker ownership.

Each notification includes:

- `deployment_environment`
- `service`
- `queue`
- `severity`
- `owner`
- `route`
- `threshold`
- `runbook_url`
- value from Grafana's alert evaluation output
- recovery window through `keep_firing_for`

The SLO dashboard exposes broker availability, stage success/latency,
end-to-end feed freshness, retry/DLQ rate, and final publication success. The
shared terminal ratio counts `success|duplicate` over
`success|duplicate|invalid|failure|dlq`; intermediate `retry` events are
excluded. Broker availability and
retry/DLQ SLO alerts use multi-window burn-rate expressions where live traffic
supports that pattern.

Use the backend `Backend RabbitMQ Canary` workflow to exercise alert firing and
recovery without exposing RabbitMQ publicly:

| Drill | Alert classes exercised |
| --- | --- |
| `network-interruption` | broker down, broker availability burn |
| `invalid-credentials` | canary failure, connection churn, descriptor-pressure triage |
| `consumer-loss` | zero consumers on a main queue, unacked growth |
| `disk-watermark` | memory/disk alarm, low disk |
| `full-queue` | sustained backlog/oldest age, stage latency/freshness warning |
| `poison-message` | DLQ, retry/DLQ burn, final publication warning |
| `grafana-connectivity-loss` | Alloy metrics write loss |
| `restart` | recovery proof and restart guardrails |

After each deliberate fixture, run a normal canary and wait through the
configured recovery window before evaluating recovery. Alert tests must not
publish production articles, expose AMQP/management endpoints, disable legacy
ingestion/failover, mutate contact points, or disable Alloy remote write.
Rollback is a Git revert of the infra PR followed by `Grafana Cloud Plan` and
`Grafana Cloud Apply`; the Grafana resources use `prevent_destroy`.

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

Before adding secrets, configure the `production-vps` GitHub Environment with
all protection controls below:

- Under **Deployment branches and tags**, choose **Selected branches and tags**
  and allow only the exact `main` branch. Do not allow every branch, tags, or a
  wildcard that can match a feature branch.
- Leave **Required reviewers** empty so exact-main protected workflows can run
  automatically. The policy audit fails closed if a manual reviewer gate is
  reintroduced.
- Disable **Allow administrators to bypass configured protection rules**. The
  policy audit treats a missing, malformed, or enabled `can_admins_bypass`
  response as a blocker.

These repository settings are a required manual control; this GitOps change
does not mutate them. Manual protected Grafana plan, apply, drill, canary, and
annotation jobs also require the exact `refs/heads/main` ref at job level, so a
feature-branch dispatch is skipped before GitHub evaluates the Environment or
releases its secrets. Confirm the environment rule and reviewers in repository
settings before the first protected run and during quarterly access review.
The `production-vps` Environment must report administrator bypass disabled
before protected Grafana plan/apply. In this solo-maintainer repository,
dispatch Grafana plan/apply, VPS check/apply, the notification canary, and
failure drills through `Dispatch Protected Observability Rollout` on exact
`main`. That secretless, fixed-purpose workflow uses `github.token` to create
the target run as `github-actions[bot]`. The dispatcher does not use the
production Environment or read production secrets; the exact-main environment
policy permits the target to continue automatically.
The unattended daily synthetic inventory audit uses the separate
`grafana-observability-readonly` Environment described in
`GRAFANA_OBSERVABILITY_READONLY_ENVIRONMENT.md`; it must not inherit deployment
credentials or a manual reviewer gate.

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
- `NUTSNEWS_EMAIL_TO`
- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN`
- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL`
- `NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON`
- `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON`

The service account token should be scoped to manage Grafana folders, dashboards, alert rules, and Synthetic Monitoring checks. Keep Terraform state remote; do not commit state, tfvars, backend coordinates, tenant IDs, endpoints, usernames, or tokens.

Do not store `GRAFANA_URL` or `GRAFANA_SERVICE_ACCOUNT_TOKEN` in `ramideltoro/nutsnews-backend` after the handoff. Backend telemetry write credentials such as `GRAFANA_CLOUD_PROMETHEUS_URL`, `GRAFANA_CLOUD_PROMETHEUS_USERNAME`, `GRAFANA_CLOUD_PROMETHEUS_PASSWORD`, `GRAFANA_CLOUD_LOKI_URL`, `GRAFANA_CLOUD_LOKI_USERNAME`, and `GRAFANA_CLOUD_LOKI_PASSWORD` remain backend-scoped because the backend host uses them to ship metrics and logs.

Required production Synthetic Monitoring secrets:

- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN`: Synthetic Monitoring API token.
- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL`: Regional Synthetic Monitoring API backend URL from the live Grafana plugin configuration; this is not the Grafana stack UI URL.
- `NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON`: JSON array of probe IDs.
- `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON`: JSON object of HTTP checks.

Keep real target URLs in the protected secret JSON or local untracked variables, not in Git.
The input schema preserves Grafana's 10-second through 60-minute API-check
bounds and one- through 60-second timeout bounds. Production policy is stricter:
exactly five enabled checks across two public probes every five minutes, a
hard failure at or above the lower of 90% of the configured allowance and the
absolute 90,000-execution monthly ceiling, and a protected
reviewed acknowledgment in the 85% `major` forecast band. The protected
preflight artifact is value-free: it contains counts, interval bounds,
projected executions, thresholds, and the decision state, never check names,
targets, probe IDs, or credentials.

Leave checks in Grafana's default Synthetic Monitoring folder. The general
NutsNews dashboard folder is outside the Synthetic Monitoring folder tree and
must not be assigned as a check `folderUid`.

The JSON must define exactly `canonical_homepage`, `canonical_readiness`,
`canonical_articles_api`, `vps_readiness`, and
`vercel_secondary_readiness`. Readiness checks must require compact JSON
`"ready":true`, reject `deploymentTarget:"unknown"`, assert a `Cache-Control`
header containing `no-store`, and validate the target identity: `production-vps` for direct VPS,
`vercel-production` for Vercel-secondary, and either value for canonical
failover. The homepage must require real NutsNews content and reject a
maintenance payload. The articles API must require article content and public
cache headers. Never target refresh, controller, ingestion, trigger, or
publication routes.

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
| `NUTSNEWS_EMAIL_TO` | Existing protected operations/report recipient list, comma-separated. | Managed operations-email contact point and firing/resolved notifications |
| `NUTSNEWS_GRAFANA_CLOUD_METRICS_URL` | Grafana Cloud portal -> your stack -> sending metrics / Prometheus remote_write endpoint. | Alloy metrics remote write |
| `NUTSNEWS_GRAFANA_CLOUD_METRICS_USERNAME` | Grafana Cloud portal -> your stack -> sending metrics / Prometheus username or instance ID. | Alloy metrics remote write |
| `NUTSNEWS_GRAFANA_CLOUD_LOGS_URL` | Grafana Cloud portal -> your stack -> sending logs / Loki endpoint. | Alloy Loki write |
| `NUTSNEWS_GRAFANA_CLOUD_LOGS_USERNAME` | Grafana Cloud portal -> your stack -> sending logs / Loki username or instance ID. | Alloy Loki write |
| `NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN` | Grafana Cloud portal -> Security -> Access Policies -> create token with `metrics:write` and `logs:write` scoped to this stack. | Alloy telemetry writes |
| `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_ACCESS_TOKEN` | Grafana Cloud Synthetic Monitoring -> Config/API keys -> create a Synthetic Monitoring API token. | Five production synthetic checks |
| `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL` | Grafana Cloud Synthetic Monitoring plugin configuration; copy the regional API backend URL, not the Grafana UI URL. | Synthetic provider and controlled status/body/header mismatch drill |
| `NUTSNEWS_GRAFANA_SYNTHETIC_PROBE_IDS_JSON` | Grafana Cloud Synthetic Monitoring -> probes; JSON array of exactly two public probe IDs. | Five production synthetic checks |
| `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON` | Hand-authored protected JSON object of the five approved public-safe endpoints and assertions. Keep URLs outside Git. | Five production synthetic checks |
| `NUTSNEWS_BACKEND_OBSERVABILITY_DRILL_TOKEN` | Fine-grained GitHub token scoped only to `ramideltoro/nutsnews-backend`, with repository metadata read and Actions read/write. Store it only in the infra `production-vps` Environment. | Dispatch, correlate, watch, and download evidence from the protected backend drill workflow |

## Free-Quota Guardrails

The current committed assumptions are:

- Metrics: 10,000 active series per month.
- Logs: 50 GB ingested per month with 14-day retention.
- Synthetic Monitoring API tests: 100,000 executions per month.
- Synthetic Monitoring browser tests: 10,000 executions per month.
- k6: 500 virtual user hours per month.

Grafana can change these limits. Check the live pricing page before enabling more telemetry: https://grafana.com/pricing/

Grafana Cloud publishes usage and limit metrics in the `grafanacloud-usage` datasource. The dashboard and alerts use those metrics where available: https://grafana.com/docs/grafana-cloud/cost-management-and-billing/manage-invoices/understand-your-invoice/usage-limits/

Metrics active-series usage must use the documented live join:

```promql
max(
  grafanacloud_instance_active_series
  / on(id)
  grafanacloud_instance_metrics_limits{limit_name="max_global_series_per_user"}
)
```

Do not use the obsolete `grafanacloud_instance_metrics_usage` series. Threshold
rules use warning at 70%, major at 85%, and critical at 95%, with
`NoData=OK`. A separate major rule pages when required usage numerator or
denominator telemetry is missing. Logs and traces continue to use their live
usage/limit series. Tempo, exemplars, profiling, and Faro remain deferred;
correlation IDs remain structured metadata rather than metric or Loki labels.

Synthetic Monitoring execution estimate:

```text
probes x tests x rounded-duration-minutes x (43200 / frequency-minutes)
```

Five checks, two probes, and a five-minute interval project to 86,400 API
executions in a 30-day month. OpenTofu blocks at the lower of the absolute
90,000-execution ceiling and 90% of the configured free allowance, and
provisions 70/85/95% forecast alerts. Because
Grafana does not document a Prometheus billing-usage series for synthetic API
executions, this is a configuration forecast; the post-apply verifier also
requires exactly five managed checks and two live probe series per check.
Browser checks and cloud k6 runs are not enabled.

The exact 5×2×5-minute topology consumes 86.4% of the 100,000-execution free
allowance and therefore enters the 85% `major` forecast band. Production
plan/apply is fail-closed until this contradiction receives an explicit
reviewed decision. To retain the requested topology, set the protected
environment variable
`NUTSNEWS_GRAFANA_SYNTHETIC_MAJOR_FORECAST_ACKNOWLEDGED=true`; that value means
the operator deliberately chose the standing-major option and does not silence
or downgrade the capacity signal. The other valid choices—changing cadence or
topology, or changing the threshold/allowance with quota evidence—require a
reviewed source change. Keep `enforce_rollout_decisions=true` for all production
plans/applies; its false value is only for non-mutating static CI fixtures. The
90,000 hard ceiling remains independent and prevents adding a check/probe or
shortening the interval without a reviewed source change.

The production Alloy configs enforce the active-series budget at the scrape
boundary. VPS Caddy drops every per-client rate-limit family plus request and
response size and duplicate response-duration histograms; the required
terminal request rate, status ratios, request-latency histogram, upstream
errors, upstream health, and TLS expiry signals remain. VPS and backend Alloy
self-scrapes retain only readiness and configuration, remote-write backlog and
failure, and Loki retry and drop families. Backend PostgreSQL and worker
filtering is owned by `ramideltoro/nutsnews-backend`. Revert these allowlists
only through protected check/apply and only after the Usage / Quota dashboard
proves sufficient headroom.

The VPS host and textfile contract uses `job="integrations/unix"`, matching the
embedded exporter target identity retained by Grafana Cloud. Do not remap these
custom families into the reserved `integrations/node_exporter` job: Grafana
Cloud retains the integration's native Node Exporter families there but not the
custom `nutsnews_*` textfile families. Dashboards, alerts, drill prechecks, and
post-apply verification must move with this job identity as one reviewed unit.

## Alert Delivery And Notification Canary

OpenTofu owns the `NutsNews operations email` contact point using the protected
`NUTSNEWS_EMAIL_TO` recipient list. Resolved notifications are enabled. The
global managed policy routes `critical|major` after a 30-second group wait,
updates every five minutes, and repeats hourly. `warning|minor|low` waits five
minutes, updates every 15 minutes, and repeats every six hours.

Run `Grafana Notification Canary` once during rollout and retain its unique
firing and resolved messages in the operations mailbox. The workflow also runs
quarterly. The workflow creates a uniquely named Grafana-managed alert rule,
observes it in the built-in Alertmanager, changes its bounded PromQL condition
to false, observes recovery, and deletes the temporary rule. This is required
because Grafana's built-in Alertmanager accepts Grafana-managed alerts rather
than direct external alert injection. The firing query automatically becomes
false after 15 minutes if the runner is interrupted, and the workflow also
attempts recovery and deletion in its final cleanup path. Its fire/resolve phase
succeeds only after both Alertmanager state transitions and temporary-rule
deletion, then records `pending_receipt`; it does not mark email delivery as
verified. Search the mailbox for the unique
`NutsNewsNotificationCanary-github-<run>` alert name recorded in the workflow
summary. Retain the API-transition record and matching firing/resolved receipt
evidence in the private operator-owned store. Compute a separate SHA-256 content
digest for each retained evidence object and format it as
`sha256:<64-lowercase-hex>`. Never paste an evidence URL, pre-signed locator, or
path-embedded share token into a workflow input or Actions artifact. GitHub's
supporting Actions artifacts are retained for only 90 days and are not the
quarterly system of record.

Run the workflow separately with `action=attest-receipt`, the same
`github-<run>` canary ID, the two distinct opaque SHA-256 references, and this
exact confirmation:

```text
human-attest-grafana-notification-canary:github-<run>:firing-and-resolved-received
```

GitHub supplies and records the human triggering actor plus the attestation
workflow run ID and run attempt. The resulting `receipt_human_attested` record
binds those identities, the original canary run ID, and both evidence digests
with a further SHA-256 binding. It records that the human attested both firing
and resolved receipts without posting to Alertmanager or sending another pair.
It does **not** fetch the private evidence store and therefore must be described
as `human_attested`, never as independently verified delivery. Only a future
implementation that fetches evidence from an explicitly allowlisted store and
validates its content may emit an independently verified status. Rerunning the
original fire/resolve run also never sends another pair. A quarterly canary
without a separately retained `receipt_human_attested` record must not be
reported as having human receipt evidence.

The Grafana Linux/node_exporter integration inventory classifies 24 alerts and
16 recording rules in
`terraform/grafana-cloud/catalog/non-terraform-alert-rules.json`. Eleven
node-exporter recording rules are long-term retained. Five
legacy Asserts recording rules remain live because the authenticated integration
API reports 1.6.2 installed, 1.6.2 available, and `has_update=false`. Never
delete those five UIDs directly. Re-review and use the supported integration
upgrade only after Grafana offers it. After alert migration, the verifier
accepts the exact 16-rule alerts-disabled shape or the exact 11-rule
post-upgrade shape. It rejects the initial 40-rule shape as an incomplete
migration and rejects partial or unknown inventories.

First run the protected Grafana apply to create the 24 exact source-reviewed
Terraform replacements. Then run `Grafana Linux Integration Alert Migration`
in apply mode with exact confirmation `linux-integration-alert-migration`.
The workflow proves every live vendor definition matches its replacement,
verifies all replacements and all 16 recording rules, then disables only the
vendor alert bundle through the integration's supported `configurable_alerts`
control. Logs and recording rules remain enabled. It fails closed before
mutation on rule identity, source-severity, provenance, query, context, or live
integration-version drift, and verifies the final state after mutation. The
standard replacement context is owner, route, service, environment, normalized
severity, dashboard, and runbook. The committed
fingerprint policy begins in `pending_authenticated_rollout`, so the first
authenticated verification exports each deterministic live definition hash and
intentionally fails. Review those live definitions, commit
`definitionFingerprintSha256` on all 11 long-term retained rules, and change
`baselineStatus` to `approved`; only then does the verifier compare definitions
and allow a drift-validation claim. Do not invent hashes or describe the
pending bootstrap report as fingerprint validation.

## Native SLOs

OpenTofu creates four 30-day Grafana SLOs: public availability at 99.5%, 95%
of successful articles API probes within 750 ms, feed freshness within 15
minutes at 99%, and worker terminal success at 99%. Failed article-API probes
trigger the all-check synthetic operational alert and are excluded from the
latency denominator; the latency good events are the successful probes that
also meet 750 ms.
Grafana fast/slow burn
alerts are enabled for the first three. Worker terminal burn alerts remain
disabled while worker uplift is shadow-only through the protected
`worker_terminal_slo_alerting_enabled` input; the dashboard still evaluates its
SLI. Enable that input only in the protected cutover that also makes the
host-owned worker-uplift mode `production` and expected-active value `1`.
Feed-freshness paging protects durable production content regardless of which
ingestion implementation owns it, and the separate critical guardrail fires at
three hours.

The worker telemetry rollout is intentionally staged behind publication and
adoption of `nutsnews-worker-contracts`/`nutsnews-worker-runtime` 1.0. In both
rollout modes, post-apply verification requires all eight worker endpoints to be up and fresh,
the host collector must verify version, revision, and running-image digest for
all eight deployed services, all eight services must emit deployment and immutable build identity from runtime, and the
seven delivery processors must expose producer-initialized stage-event and fixed-bucket latency families
before traffic. Verification validates the scheduler through its loop/cycle metrics
instead of pretending it is a delivery stage. Until telemetry-enabled images
are republished and repinned, the runtime identity and readiness panels expose
an explicit awaiting-republished-images rollout state. While the host-owned mode remains
`shadow`, each readiness family must still be present, but shadow readiness is an explicit disabled state
and an `outcome="ok"` result is not required. At production cutover, every
service with `nutsnews_worker_expected_active=1` must report readiness
`outcome="ok"`, agree with the durable host-owned production gate, and have
worker SLO burn alerting enabled. Do not enable the cutover input before the
contracts/runtime 1.0 releases are published, adopted, and deployed across
every worker image.

## Deployment Annotations

Every managed dashboard displays append-only annotations tagged
`nutsnews-deployment`. Promotion, rollback, failover, and database-provider
workflows call
`terraform/grafana-cloud/scripts/publish_deployment_annotation.py` with the
commit, image digest, version, target, final outcome, and a bounded evidence
identifier when one exists. Do not replace this with Terraform
`grafana_annotation`: that resource updates one stateful event rather than
preserving deployment history.

Database-provider start and outcome annotations run for every protected
provider-switch `apply`, even when its optional Vercel release dispatch is
disabled, so the environment mutation cannot disappear from the timeline.

Failover annotations are deliberately separate from deployment of the
Cloudflare failover controller. Controller deployment does not prove that
visitor traffic changed targets. After an actual transition completes, an
operator or a Worker-owned automation dispatches
`.github/workflows/grafana-failover-annotation.yml` on `main`. The call must
name the new authoritative target (`production-vps` or `vercel-production`),
use a completed `succeeded` or `rolled-back` outcome, provide either a full
source commit or bounded release identity, link durable transition evidence
under `https://github.com/ramideltoro/...`, and pass the exact confirmation
`record-confirmed-production-failover`. The unprotected validation job checks
that contract before the reusable publisher can attach the protected
`production-vps` Environment. External Worker automation should use the
GitHub Actions workflow-dispatch API; `workflow_call` is the same-repository
integration point. Never emit a failover annotation for a controller deploy,
a planned transition, or an attempt that did not actually change the public
target.

## Bounded Failure Drills

`.github/workflows/grafana-failure-drill.yml` is the only infra-owned entry
point for the eight approved observability drills. It is manual-only, defaults
to `dry-run`, validates its source-controlled contract before attaching the
protected `production-vps` Environment, serializes with the production Ansible
baseline, and never accepts a free-form command or endpoint. Execute mode
requires the exact contract target plus
`execute-grafana-failure-drill:<target>:<drill>`.

| Drill | Safe injection | Recovery and current execution status |
| --- | --- | --- |
| `alloy-stopped` | Stop only `alloy.service` on `vps.nutsnews.com`. | The host schedules an independent systemd recovery before the stop; the workflow also runs explicit recovery and verifies Alloy readiness. Executable after the new Ansible hook is deployed. |
| `textfile-stale` | Stop only the managed textfile timer and age only the collector-success timestamp. | The host preserves the exact prior file, schedules recovery before mutation, reruns the collector, restarts the timer, and requires freshness. Executable after the new Ansible hook is deployed. |
| `worker-unavailable` | Stop one exact verified-shadow worker while a bounded drill fixture opens only the shadow ownership gate. | The protected backend workflow restarts that same worker, requires readiness/scrape recovery, clears the fixture, and retains its own fail-safe evidence. The infra dispatcher fails closed until the backend workflow is merged and its host hook is deployed. |
| `rabbitmq-zero-consumer` | Emit a disposable telemetry-only textfile fixture; never call RabbitMQ, stop a production consumer, or mutate a production queue. | The protected backend workflow clears the fixture automatically and proves real consumer telemetry was left unchanged. The infra dispatcher fails closed until the backend workflow and host hook are available. |
| `rabbitmq-growing-dlq` | Emit a telemetry-only disposable rule-path fixture. This proves the nonempty/burn alert paths; it does **not** assert real queue growth. | The protected backend workflow clears the fixture automatically and leaves queues/messages untouched. Grafana recovery polling allows 1,200 seconds for the rules' 15-minute `keep_firing_for`. |
| `postgres-relay-lag` | Emit a telemetry-only disposable relay-lag rule-path fixture; never pause replication. | The protected backend workflow holds the fixture for a fixed 900 seconds so scrape/evaluation plus the ten-minute `for` window can complete, then clears it and proves configuration is unchanged. Real relay health is required only when the relay is configured; `not_configured` remains a valid unchanged state. |
| `backend-readiness-failed` | Emit a disposable readiness rule-path fixture; never change the compatibility API or database. | The protected backend workflow clears the fixture and proves process, PostgreSQL, and public readiness were left unchanged. The infra dispatcher fails closed until the backend workflow and host hook are available. |
| `synthetic-mismatch` | Select exactly one of the five approved checks, then change only its status, body, or header assertion family. | Before any API mutation, the parent dispatches a separate protected watchdog and requires its sanitized armed handshake. The watchdog verifies the live parent run/revision, retains the exact remote check only in a runner-local mode-`0600` snapshot, and HMAC-binds that snapshot to the handshake. The parent fails closed if the check changes before injection, self-restores in `finally`, publishes a sanitized release, then waits for the watchdog to exact-restore and verify. If release never arrives, the watchdog restores when the parent terminates or after its bounded 7,200-second hold. The watchdog uses a separate per-check concurrency group to avoid deadlocking the parent that holds `grafana-cloud-apply`; normal restoration remains transitively serialized while the parent waits. On an abnormal parent exit, the restore helper refuses to overwrite any state other than the saved configuration or this drill's exact one-field mutation. Alert proof remains bound to `nn-sm-probe-failure`, the selected job, and the same two probe labels. Repeat all five checks and three assertion families during acceptance. Target URLs, assertion values, and private snapshots are never uploaded. |

VPS recovery additionally requires
`recover-grafana-failure-drill:<target>:<drill>`; Synthetic Monitoring fallback
uses
`recover-grafana-failure-drill:<selected-check>:synthetic-mismatch`.
Backend hooks schedule host recovery before mutation and trap explicit cleanup
even when orchestration fails. Infra dispatch uses a fine-grained
`NUTSNEWS_BACKEND_OBSERVABILITY_DRILL_TOKEN` scoped to Actions in only
`ramideltoro/nutsnews-backend`; the remote workflow attaches its own protected
`production-backend` Environment. If any hook, protected credential,
precheck, exact target, restore payload, or remote API shape is unavailable,
the drill fails closed before mutation wherever possible and reports failure
rather than inventing a substitute action.

The dedicated backend drill token is not currently present in the infra
`production-vps` Environment, so all five backend execute paths are a protected
rollout blocker and fail before dispatch. Provision and review that
least-privilege token before the first approved drill. Do not silently reuse
`NUTSNEWS_INFRA_RELEASE_TOKEN`: its backend Actions authority has not been
proven and its broader release purpose is a different trust boundary.

Each run retains a sanitized, value-free supporting artifact for 90 days. Copy
required drill evidence to the operator-owned durable evidence store before
expiry. For a backend drill, infra binds the downloaded artifact to the exact
backend workflow path, run ID, run attempt, main revision, evidence ID, drill,
fixed 900-second duration, and `dry_run=false`. Every upstream report and check
object must match the recursive key allowlist. Infra then constructs a new
outcome-only summary containing the source run reference, provider artifact
digest (verified against the downloaded archive), and SHA-256 of the validated
evidence payload. The downloaded archive
and upstream `evidence.json` remain runner-private and are never re-uploaded.
The final record contains the drill and bounded target identifiers, start/end
time, mode, precheck, injection, expected rule UIDs observed, recovery,
postcheck, and result. It never includes tokens, remote target URLs, request
headers, assertion regexes, metric values, message IDs, upstream check details,
or the Synthetic Monitoring restore snapshot. No live failure drill was
executed as part of introducing this automation; a passing evidence record
exists only after an approved protected run observes both firing and recovery.

## Home-Server Database Backup Metric Status

`scripts/home_server_db_backup_metrics.py` is source-staged with tests for the
exact durable `nutsnews_db_backup_last_success_timestamp_seconds` contract. It
preserves the last successful timestamp across a later failed run, reports
last-run and availability separately, and atomically overwrites stale output
with an explicit unavailable state. The home-server exporter is not managed by
this repository, so this source file has **not** been installed or wired into
the live home-server backup job. Do not claim that the exact metric is live
until the home-server owner deploys the wrapper and Grafana confirms a fresh
series. The canonical backup-verification threshold is 30 hours.

## Intentionally Excluded

- Debug/trace logs.
- Log lines larger than 8 KB.
- Rotated compressed logs and logs older than the Alloy file discovery window.
- High-cardinality labels such as container IDs, image IDs, request IDs, user IDs, raw IP addresses, and full dynamic paths.
- cAdvisor/container metric collection by default. The current safer metrics model is host/systemd telemetry, Docker state through the root-run Ops Portal collector, and textfile metrics under `/var/lib/nutsnews/alloy/textfile`.
- Tempo traces, exemplars, profiling, Faro, browser Synthetic Monitoring, and Grafana Cloud k6 execution until explicitly approved.

## Container Metrics Strategy

Alloy intentionally leaves `vps_service_foundation_grafana_alloy_collect_docker` set to `false` by default. This disables the cAdvisor exporter that previously triggered repeated `containerd.sock: connect: permission denied` errors.

Container logs are collected separately with `vps_service_foundation_grafana_alloy_collect_docker_logs` set to `true`. That grants the non-root `alloy` user membership in the `docker` group so Alloy can read the Docker API socket at `/var/run/docker.sock` for containers labeled with the `nutsnews-service-foundation` or `nutsnews-app` Compose project. This is a reviewed Docker API privilege boundary, not a cAdvisor/containerd metrics path.

Do not chmod `/run/containerd/containerd.sock`, make it world-readable, or run
Alloy as root just to silence cAdvisor. The bounded root-run textfile collector
uses `docker stats --no-stream` only for declared NutsNews services and always
overwrites output with explicit availability/state samples. The Docker
dashboard uses those bounded CPU, memory, network, block-IO, PID, running,
health, and restart families. Enabling cAdvisor still requires a separate
privilege and cardinality review.

## Rollout Order

The post-apply verifier is deliberately strict and will fail until the source
telemetry exists. Roll out in this order:

Current-state note (2026-08-01): Grafana Cloud Apply run `30708192621` on
`main` revision `c23403e` successfully created check IDs `3997`–`4001`.
Protected input-validation logs and the OpenTofu apply/output show five checks,
two probes, a 300-second interval, and exactly 86,400 projected API executions
per 30-day month. The verifier at that revision did not inspect the Synthetic
Monitoring API or assert the synthetic Terraform state, so its post-apply
artifact is not evidence for that topology. The live baseline also does not
prove unapplied verifier sanitization, notification-policy, native-SLO, or
environment-policy hardening from a later change; those contracts become
authoritative only after their protected apply and post-apply verification.

1. Publish and deploy the backend API, worker telemetry, backend Alloy, PostgreSQL, relay, Caddy, and durable content/ownership exporters. Keep worker uplift shadow-owned.
2. Run the protected VPS apply with Alloy enabled, the bounded Docker/textfile collectors, normalized journal coverage, and the daily 30-hour backup-verification policy.
3. Run the backend and VPS health audits once and confirm their conclusion, last-success age, collector freshness, and unavailable/disabled states are truthful.
4. Apply and verify the 24 Terraform Linux alert replacements, run the protected integration alert migration, and approve the 11-rule recording baseline; do not waive any stage as vendor metadata.
5. Run Grafana Cloud Plan, review the live diff, then run the protected apply and require the complete post-apply verification artifact to pass.
6. Fire and resolve the notification canary once, retain its API transition record, then run the separate human receipt attestation with distinct SHA-256 references for retained firing and recovery evidence keyed to the same stable canary ID; do not claim independent verification because the workflow does not fetch the private store.
7. Run every failure drill in dry-run mode and review its exact target, alert UIDs, fixed hold period, recovery, and sanitized evidence plan.
8. Execute only individually approved drills from `main`, beginning with the telemetry-only fixtures; retain firing and recovery evidence before proceeding to the next drill.

Do not use a partial Grafana pass to reorder these stages. A missing source
series must remain an explicit rollout dependency or disabled-by-configuration
state, not be masked with a synthetic healthy value.

## Apply Grafana Assets

1. Let the PR-triggered `Grafana Cloud Plan` workflow run its unprivileged fmt,
   static tests, and backendless validation job. The protected plan job never
   receives Environment secrets from a PR branch.
2. Merge the PR after required checks pass and after resolving the documented
   production Environment, vendor-rule, and synthetic-forecast rollout
   decisions.
3. On exact `main`, dispatch `Dispatch Protected Observability Rollout` with
   `operation=grafana-plan` and an empty confirmation. Approve the resulting `Grafana Cloud
   Plan` run at the `production-vps` Environment gate.
4. Confirm the live OpenTofu plan, refresh-only drift check, and value-free
   `grafana-cloud-input-validation` artifact. This artifact proves the protected
   topology and quota decision without disclosing targets; it is not post-apply
   telemetry evidence and does not require the last-applied live stack to
   already match an unapplied desired change. Exact live convergence and query
   health verification is apply-only.
5. Run `Dispatch Protected Observability Rollout` again on exact `main` with
   `operation=grafana-apply` and `confirmation=grafana-cloud`.
6. Approve the resulting `Grafana Cloud Apply` run at the `production-vps`
   Environment gate.
7. Review the final OpenTofu apply output, dashboard URLs, the value-free input
   artifact, and the `grafana-cloud-post-apply-verification` artifact. The
   post-apply artifact is the authoritative dashboard, alert, Prometheus, Loki,
   Synthetic Monitoring, SLO, and rule-health evidence.

If the backend secret is missing, stop and configure remote state before applying. Do not use local state from a GitHub Actions runner for production Grafana assets.

### Backend Import Sequence

1. Confirm the backend folder UID is `nutsnews-backend-ops` and the alert group name is `NutsNews Backend Guardrails`.
2. Run `Grafana Cloud Plan`; the import blocks should map the existing backend folder, dashboards, and rule group to the infra OpenTofu addresses.
3. Because the protected plan runs only from exact `main`, merge the reviewed
   import PR before dispatching it. If the plan reports a duplicate UID, missing
   object, or refresh-only drift, stop and reconcile it in a follow-up reviewed
   PR, merge that correction, and rerun the protected plan. Never apply until
   the plan and drift check are clean.
4. Run `Grafana Cloud Apply` from exact `main`.
5. Verify the post-apply report shows backend dashboards, 20 backend alert rules, backend Prometheus query results, RabbitMQ metric-family query results, backend host/source Loki log lines, and worker-uplift RabbitMQ container log lines.
6. Only after that verification passes, retire backend direct provisioning and remove backend-scoped Grafana management credentials. Leave backend telemetry write credentials in place.

### Rollback

Rollback is a reviewed GitOps revert. Revert the infra PR on `main`, rerun
`Grafana Cloud Plan`, and inspect the normal plan, value-free input evidence,
and refresh-only drift result. The plan intentionally does not require the
current live stack to equal the reverted desired state before apply. Run
`Grafana Cloud Apply` only when the rollback delta is expected, then require the
new apply-only exact-state/query-health artifact to pass. The managed folders,
dashboards, and alert rule groups use `prevent_destroy`, so any destructive
rollback requires an explicit reviewed code change that removes that protection.

## Enable Alloy On The VPS

1. Open `Dispatch Protected Observability Rollout` on exact `main`.
2. Set `operation` to `vps-check` and leave `confirmation` blank. The fixed
   dispatcher sets `run_mode=check` and keeps the reviewed production defaults.
3. Keep the production default `enable_grafana_alloy=true`.
4. Keep `confirm_apply` blank.
5. Review the diff. Alloy should install from the Grafana apt repository, render `/etc/alloy/config.alloy`, render a root-only env file, create the textfile metrics timer, validate the Alloy config, keep cAdvisor/container metrics disabled by default, enable NutsNews Docker log discovery, and verify no recent containerd socket permission errors remain after the service restart.
6. Rerun the dispatcher with `operation=vps-apply` and
   `confirmation=vps.nutsnews.com`; it fixes `run_mode=apply` and retains
   `enable_grafana_alloy=true` from the protected workflow's reviewed default.
7. Approve the `production-vps` Environment gate.

The existing protected apply workflow still connects as `nutsnews_ops`, never root SSH, and applies only the declared Ansible baseline.

The protected proof normalizes Alloy's reader-level `debugInfo` list without
printing reader identifiers or labels. It retries until at least three readers
are running and the Caddy and web readers are present. Missing or malformed
component content produces only a bounded status, health category, counts, and
booleans in the failed assertion.

## Disable Alloy On The VPS

Set `enable_grafana_alloy=false` only when Alloy must be deliberately disabled,
and set `confirm_disable_grafana_alloy=disable-grafana-alloy`. The workflow and
role reject an unconfirmed disable. Production inventory persists Alloy enabled
as the desired state, so a later ordinary apply restores telemetry.

In check/apply mode Ansible stops and disables the observability textfile timer, stops/disables/masks `alloy.service` when the unit exists, removes the managed Alloy environment file, config file, systemd drop-in, and textfile unit files, and removes supplementary access from the `alloy` user when that user exists. The package and Grafana apt repository can remain installed so rollback is a normal GitOps re-enable, but the service has no managed credential/config artifact to run with while disabled.

Rollback is to rerun the protected workflow with `enable_grafana_alloy=true`; the enabled path un-masks `alloy.service`, recreates the managed config and root-only env file from protected Environment secrets, validates Alloy, starts the textfile timer, and performs the usual readiness and journal checks.

## Verify Telemetry

Use Grafana Explore after apply:

```promql
up{job="integrations/unix",service="host-exporter",deployment_environment="production"}
up{job="integrations/nutsnews-vps-alloy",instance="vps.nutsnews.com",service="alloy",deployment_environment="production"}
up{job="integrations/nutsnews-vps-caddy",instance="vps.nutsnews.com",service="caddy",deployment_environment="production"}
nutsnews_observability_textfile_last_success_timestamp_seconds{deployment_environment="production"}
nutsnews_docker_stats_available{deployment_environment="production"}
nutsnews_production_ownership_info{deployment_environment="production"}
nutsnews_production_ownership_available{deployment_environment="production"}
nutsnews_production_ownership_last_success_timestamp_seconds{deployment_environment="production"}
nutsnews_backend_worker_uplift_deployment_info{job="nutsnews-backend-host",deployment_environment="production"}
nutsnews_backend_worker_uplift_ownership_available{job="nutsnews-backend-host",deployment_environment="production"}
count by (service) (up{job="nutsnews-worker-uplift",environment="production"})
```

The VPS production-ownership textfile exporter does not copy desired Ansible
labels into Grafana. It performs a bounded request to the exact canonical
`https://www.nutsnews.com/readyz` URL, requires HTTP 200, `ready=true`,
`service=nutsnews-web`, `Cache-Control: no-store`, reviewed deployment/database
values, a full source commit, matching identity headers, and the local deployed
infrastructure commit receipt. A redirect, mismatch, malformed/oversized body,
or missing receipt exports availability `0` and no ownership info series.
Configured ingestion owner and worker mode/write gate come separately from the
backend protected deployment signal through
`nutsnews_backend_worker_uplift_deployment_info`; invalid mode/expected-active
pairs set `nutsnews_backend_worker_uplift_ownership_available` to `0` and are
filtered from the dashboard, as is a signal whose backend exporter timestamp is
older than ten minutes. This source is protected deployment configuration, not
a direct database read of `worker_uplift_final.cutover_control`; do not describe
it as database-confirmed cutover state until that source is instrumented.

Use Loki Explore:

```logql
{deployment_environment="production", source="journal"}
{deployment_environment="production", source="auth"}
{deployment_environment="production", source="docker", service=~"web|caddy"}
{deployment_environment="production", source="docker", service="caddy"} | json
{deployment_environment="production"} |~ "(?i)(error|critical|panic|failed|denied)"
{host="backend.nutsnews.com", source="container", service="rabbitmq"}
```

VPS Loki streams index exactly `deployment_environment`, `service`,
`service_version`, `host`, `source`, and `severity`. Request, message,
correlation, trace, article, feed, and idempotency identifiers remain parsed
structured metadata and must not appear as indexed labels.

Container log streams are present when `vps_service_foundation_grafana_alloy_collect_docker_logs` is enabled. Container metrics stay disabled until `vps_service_foundation_grafana_alloy_collect_docker` is explicitly enabled after a separate review.

Use Synthetic Monitoring metrics when checks are configured:

```promql
probe_success{job=~"canonical_homepage|canonical_readiness|canonical_articles_api|vps_readiness|vercel_secondary_readiness"}
  * on(job, instance, probe, config_version) group_left()
    sm_check_info{
      label_service_namespace="nutsnews",
      label_deployment_environment="production"
    }
```

Use quota metrics:

```promql
grafanacloud_instance_active_series
grafanacloud_instance_metrics_limits{limit_name="max_global_series_per_user"}
max(grafanacloud_instance_active_series / on(id) grafanacloud_instance_metrics_limits{limit_name="max_global_series_per_user"})
grafanacloud_instance_metrics_limits
grafanacloud_logs_instance_limits
grafanacloud_traces_instance_limits
```

Expected dashboards are in the `NutsNews Observability` folder:

- NutsNews VPS Overview
- NutsNews Logs Overview
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
- NutsNews Current Production Ownership

Expected worker-uplift backend dashboards are in the `NutsNews Backend Ops`
folder:

- NutsNews Worker-Uplift RabbitMQ Overview
- NutsNews Worker-Uplift Queue Drilldown
- NutsNews Worker-Uplift RabbitMQ Resources

The Docker dashboard uses bounded `docker stats --no-stream` textfile metrics;
it does not depend on cAdvisor. Every repaired Docker, Caddy, application,
ownership, and synthetic panel declares an explicit unavailable/disabled state
instead of silently treating source loss as zero.

The protected post-apply verifier fails on missing folders/dashboards/rules,
incomplete ownership labels or runbook/dashboard annotations, unhealthy rule
evaluation, active `DatasourceNoData`/`DatasourceError` instances, broken
contact policy timings, missing usage numerator/denominator/ratio series,
anything other than all eight fresh worker scrapes, fewer than two live probe
series per synthetic, or missing required Loki services.

After protected apply, also verify the deployed VPS state:

```bash
systemctl show alloy.service --property=ActiveState,SubState,User,SupplementaryGroups,DropInPaths --no-pager
curl -fsS http://127.0.0.1:12345/-/ready
sudo journalctl -u alloy.service --since "-30 min" --no-pager | grep -c "containerd.sock: connect: permission denied"
sudo find /var/lib/nutsnews/alloy/textfile -maxdepth 1 -type f -name '*.prom' -printf '%s %p\n'
```

## Follow-Up App Hooks

This repo can observe deployment-owned container state, health, logs, and Caddy routing. Deeper application metrics, tracing, or structured request telemetry belong in `ramideltoro/nutsnews` or `ramideltoro/nutsnews-worker`. Create a follow-up issue or prompt there before changing application code.
