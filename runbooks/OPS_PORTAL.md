# Operations Portal Runbook

Use this runbook after the Ops Portal v1 PR is merged and before applying the service foundation through the protected workflow.

## What This Adds

- Static portal assets under `/opt/nutsnews/portal-assets`
- A local JSON status feed at `/opt/nutsnews/portal-assets/data/status.json`
- A root-run, read-only local collector at `/usr/local/bin/nutsnews-ops-portal-collector`
- A systemd timer named `nutsnews-ops-portal-collector.timer`
- A root-run email reporter at `/usr/local/bin/nutsnews-ops-portal-reporter`
- Root-only reporter configuration at `/etc/nutsnews/ops-reporter.env`
- Alert and daily report timers named `nutsnews-ops-alert-check.timer` and `nutsnews-ops-health-report.timer`
- Backup status from `/opt/nutsnews/portal-assets/data/backup-status.json`
- Free-tier usage and remaining quota status for local VPS resources, Docker storage, backup storage, Vercel, Sentry, Cloudflare, Better Stack, Supabase, Grafana Cloud, and GitHub Actions
- Grafana Alloy service readiness, Docker log shipping state, textfile metrics file visibility, and recent cAdvisor/containerd exporter permission-error counts
- A Google OAuth gateway in front of every portal route and data endpoint
- Caddy serving the protected portal publicly at `https://ops.nutsnews.com`
- Caddy keeping host loopback access at `127.0.0.1:8080` for health checks and SSH tunnel fallback

## Security Model

The portal is read-only for v1. It does not mount the Docker socket into the web app, does not expose a shell, and does not include install, uninstall, restart, or reconfigure buttons.

Caddy terminates public HTTPS for `ops.nutsnews.com` and routes all dashboard routes, static assets, and `/data/*` status endpoints through the Ops Portal auth gateway. The only Google account allowed through the dashboard is `rami.deltoro@gmail.com`; every other Google user receives a clear access-denied response. The host loopback listener remains available for health checks and SSH tunnel fallback.

The OAuth route path is fixed at:

```text
/api/auth/callback/google
```

Google OAuth authorized redirect URIs must match the app callback URL exactly. The configured callback is `https://<dashboard-domain>/api/auth/callback/google`. The documented environment URLs are:

- Production: `https://ops.nutsnews.com/api/auth/callback/google`
- Staging: `https://staging.ops.nutsnews.com/api/auth/callback/google`
- Dev: `https://dev.ops.nutsnews.com/api/auth/callback/google`

The shorthand `https:///api/auth/callback/google` is not a valid runtime URL because it has no host. If a concrete URL is required, use the dashboard domain form above consistently in the app config and the Google OAuth authorized redirect URI list.

Email reporting is opt-in. SMTP host, credentials, sender, recipients, and cooldown values come from the protected `production-vps` GitHub Environment and are rendered into a root-only env file during protected apply. If email is disabled or incomplete, the reporter exits cleanly and the portal shows reporting as disabled or misconfigured.

Alert cooldown uses each alert's stable machine ID plus its severity, not the rendered message. Numeric details may change in email without bypassing cooldown. Warning-to-critical escalation sends promptly; cleared alerts are removed from state so a later recurrence is a new incident. The root-only state file stores no rendered alert body and is capped at 256 active identities.

Free Tier Usage is read-only. The quota catalog lives in `vps_service_foundation_free_tier_quotas`; recheck official provider docs before changing those values. Local VPS, Docker, and backup entries come from live read-only collector data. Provider credentials are optional and only support read-only collection. The dashboard groups rows by service and labels each metric as `measured`, `missing credential`, `unavailable`, `unsupported`, or `unknown`. If a token, usage endpoint, or normalized snapshot is missing, malformed, or stale, the portal shows an honest unavailable state for that metric or provider instead of failing the whole dashboard.

Collector refresh stays on a one-minute systemd timer for critical health, but slower sections use `/opt/nutsnews/portal-assets/data/collector-slow-cache.json`. Docker inspect/image metadata, Compose project listings, process rankings, log excerpts, security/update scans, backup filesystem metadata, local free-tier storage rows, OOM journal evidence, and Alloy visibility all expose `_collector_cache` metadata with `live`, `fresh_cache`, `stale_cache`, or `unavailable` state. Treat `stale_cache` as preserved last-known-safe portal data and inspect `journalctl -u nutsnews-ops-portal-collector.service` before changing cadences.

Alloy telemetry visibility is read-only. The collector checks `alloy.service`, the local readiness URL, whether Docker log shipping is enabled separately from cAdvisor/container metrics, `.prom` files under the configured textfile directory, and recent journal matches for `containerd.sock: connect: permission denied`. A nonzero recent match count raises a portal alert because it means the broken cAdvisor/container path has returned or the post-apply validation window needs investigation. The portal does not read Alloy secrets or expose any control that can restart or reconfigure Alloy.

SSH hardening allows `nutsnews_ops` to create only local TCP forwards to `127.0.0.1:8080` or `localhost:8080` for portal access. Remote forwarding, gateway exposure, stream-local forwarding, tunnel devices, and broad forwarding stay disabled.

The portal forwarding policy is intentionally modeled with explicit SSH `Match` blocks. Do not put `AllowTcpForwarding no` or `PermitOpen none` back into the global baseline drop-in; those global directives can block the operator exception and bring back the `administratively prohibited` tunnel failure.

## Public Access

Open:

```text
https://ops.nutsnews.com/
```

Sign in with the allowlisted Google account, `rami.deltoro@gmail.com`. Caddy manages TLS for this hostname. The protected apply workflow can keep the `ops.nutsnews.com` Cloudflare A record aligned with the VPS IP when Cloudflare DDNS is enabled.

## Access Through SSH

Use an approved key for `nutsnews_ops` and forward the local browser port to the portal loopback listener:

```bash
ssh -N -L 8080:127.0.0.1:8080 nutsnews_ops@vps.nutsnews.com
```

Then open:

```text
http://127.0.0.1:8080/
```

If your local `8080` is already busy, use a different left-side port while keeping the right-side target restricted:

```bash
ssh -N -L 18080:127.0.0.1:8080 nutsnews_ops@vps.nutsnews.com
```

Then open `http://127.0.0.1:18080/`.

## Apply Safely

1. Open the `Protected Ansible Apply` workflow.
2. Run `check` mode first.
3. Confirm the role plans portal assets, collector units, Caddy config, and status data without production secrets.
4. Run `apply` mode only after check mode looks safe.
5. Keep any manual SSH inspection break-glass only and document it afterward.

## Configure Email Reporting

Add these optional secrets to the existing `production-vps` GitHub Environment before running protected apply in `apply` mode:

- `NUTSNEWS_EMAIL_ENABLED`: set to `true` to enable sending
- `NUTSNEWS_SMTP_HOST`: SMTP server hostname
- `NUTSNEWS_SMTP_PORT`: SMTP port, usually `587`
- `NUTSNEWS_SMTP_USERNAME`: SMTP username if required
- `NUTSNEWS_SMTP_PASSWORD`: SMTP password or app password if required
- `NUTSNEWS_SMTP_STARTTLS`: `true` unless the provider explicitly says otherwise
- `NUTSNEWS_EMAIL_FROM`: sender address
- `NUTSNEWS_EMAIL_TO`: comma-separated recipient list
- `NUTSNEWS_ALERT_COOLDOWN_SECONDS`: duplicate-alert cooldown, default `21600`
- `NUTSNEWS_REPORT_SUBJECT_PREFIX`: optional subject prefix, default `NutsNews VPS`

Do not commit SMTP values. Do not paste them into committed vars files. The protected apply workflow passes them as runtime Ansible extra vars, and the env file task is `no_log` so diffs do not leak them.

## Configure Google OAuth

Add these required secrets to the existing `production-vps` GitHub Environment before running protected apply:

- `NUTSNEWS_GOOGLE_CLIENT_ID`: Google OAuth web client ID
- `NUTSNEWS_GOOGLE_CLIENT_SECRET`: Google OAuth web client secret
- `NUTSNEWS_OPS_PORTAL_SESSION_SECRET`: random 32+ character session signing secret
- `NUTSNEWS_OPS_PORTAL_CALLBACK_URL`: one of the documented callback URLs, usually `https://ops.nutsnews.com/api/auth/callback/google`
- `NUTSNEWS_OPS_PORTAL_DOMAIN`: optional host used to derive the callback URL when the explicit callback URL is not set, default `ops.nutsnews.com`

Do not commit Google OAuth values. Ansible renders them into `/etc/nutsnews/ops-portal-auth.env` with mode `0600`, and the task is `no_log`.

## Configure Free Tier Usage

The dashboard always shows the configured free-tier quota catalog. Live usage is best effort and must stay read-only.

Optional protected `production-vps` Environment values:

- `NUTSNEWS_FREE_TIER_USAGE_JSON`: normalized usage snapshot JSON for providers without a live collector
- `NUTSNEWS_VERCEL_API_TOKEN` and `NUTSNEWS_VERCEL_USAGE_API_URL`
- `NUTSNEWS_SENTRY_AUTH_TOKEN`, `NUTSNEWS_SENTRY_ORG`, and optionally `NUTSNEWS_SENTRY_BASE_URL`
- `NUTSNEWS_CLOUDFLARE_USAGE_API_TOKEN`, `NUTSNEWS_CLOUDFLARE_USAGE_API_URL`, and `NUTSNEWS_CLOUDFLARE_ACCOUNT_ID`
- `NUTSNEWS_BETTER_STACK_API_TOKEN` and `NUTSNEWS_BETTER_STACK_USAGE_API_URL`
- `NUTSNEWS_SUPABASE_ACCESS_TOKEN` and `NUTSNEWS_SUPABASE_USAGE_API_URL`
- `NUTSNEWS_GRAFANA_CLOUD_USAGE_API_TOKEN` and `NUTSNEWS_GRAFANA_CLOUD_USAGE_API_URL`
- `NUTSNEWS_GITHUB_USAGE_API_TOKEN` and `NUTSNEWS_GITHUB_ACTIONS_USAGE_API_URL`

`NUTSNEWS_FREE_TIER_USAGE_JSON` must be a JSON object. The collector accepts provider-keyed snapshots such as:

```json
{
  "vercel": {
    "last_checked_at": "2026-07-05T00:00:00+00:00",
    "usage": {
      "fast_data_transfer_gb": 32,
      "function_invocations": 1200
    }
  }
}
```

It also accepts provider metric values under `providers.<key>.metrics`, `providers.<key>.usage`, top-level provider metric values, and metric-list entries like `{"key":"logs_gb","usage":1.2}`. Snapshot values are fallback data only; prefer live collectors when the provider exposes read-only quota usage.

Generic `*_USAGE_API_URL` endpoints must be HTTPS GET endpoints and return read-only normalized JSON with metric values under `usage`, for example `{"usage":{"logs_gb":1.2}}`. Provider-specific collectors may add safe query parameters or parse documented read-only response shapes, but they must still avoid paid APIs, mutating endpoints, automatic upgrade flows, and tokens with write/admin scopes.

Current provider-specific notes:

- Vercel usage is read from the Billing Charges FOCUS JSONL endpoint. Configure `NUTSNEWS_VERCEL_USAGE_API_URL` as the HTTPS billing charges URL, including the correct `teamId` or `slug` query parameter when the account is team-owned; the collector adds ISO 8601 `from` and `to` parameters and aggregates documented FOCUS quantity fields by configured service/unit matchers. Vercel does not use generic snapshot/cache fallback because placeholder zeroes are unsafe for billing usage. If the live API fails or omits a metric, the portal must show `unavailable` with the safe backend reason instead of generic `unknown` or zero-like placeholders. A `costs_not_found` response usually means the configured team identifier, account access, token role, or billing endpoint does not expose the desired Hobby quota metrics. Vercel monthly Hobby quota rows reset at the next UTC month boundary in the portal output.
- Sentry accepts either `https://sentry.io` or `https://sentry.io/api/0` as `NUTSNEWS_SENTRY_BASE_URL`; the collector normalizes the API root before calling Stats v2. `401 Invalid token` means `NUTSNEWS_SENTRY_AUTH_TOKEN` must be replaced with a token that can read organization stats for `NUTSNEWS_SENTRY_ORG`.
- Cloudflare Workers request usage is read with a POST to the GraphQL Analytics API using `NUTSNEWS_CLOUDFLARE_ACCOUNT_ID`. Workers KV, Pages, and R2 quota metrics still need a normalized snapshot or a dedicated collector.
- Better Stack monitor usage is read from the configured normalized endpoint by counting the returned `data` list. Telemetry volume, web event, status page subscriber, and session replay metrics still need normalized usage fields or a dedicated read-only usage endpoint.
- Supabase analytics endpoints return `result` rows for a specific metric. If the portal reports missing quota metrics, configure a normalized snapshot or add a collector for the specific Supabase quota metric; do not map unrelated API-request counts to storage, egress, auth, edge function, or realtime quotas.
- Grafana Cloud billed usage requires numeric `month` and `year` parameters. A `403` response means `NUTSNEWS_GRAFANA_CLOUD_USAGE_API_TOKEN` does not have permission for billed usage on the configured org.
- GitHub Actions reads public repository cache and artifact usage without a token when the configured repository API URL is public. Set `NUTSNEWS_GITHUB_USAGE_API_TOKEN` only when private repository access or authenticated REST rate-limit telemetry is needed. Use a fine-grained read-only token for repository Actions metadata; do not create custom secrets whose names begin with `GITHUB_`.

Ansible renders these values into `/etc/nutsnews/free-tier-usage.env` with mode `0600`, and the collector keeps only sanitized status in `/opt/nutsnews/portal-assets/data/status.json`.

To verify the deployed status snapshot without a browser OAuth session or local SSH access, run the manual `Verify Ops Portal Status` workflow. It uses the protected `production-vps` SSH key, reads only `/opt/nutsnews/portal-assets/data/status.json`, and prints sanitized Vercel free-tier status fields and metric states. It also checks masked `/etc/nutsnews/free-tier-usage.env` key presence, collector timer state, and recent collector journal lines, then calls the configured Vercel Billing Charges endpoint and prints sanitized response shape, service/unit samples, tag keys, and aggregate matches without printing tokens, URLs, team IDs, project IDs, or tag values. The workflow fails if Vercel disappears, falls back to cached placeholders, emits zero-like displays for null usage, or renders a known unavailable/unsupported row as generic `unknown`.

## Run Email Checks

Check mode should remain safe even if no SMTP secrets are configured:

```bash
gh workflow run protected-ansible-apply.yml -f run_mode=check
```

After reviewing check output and environment approval, apply with:

```bash
gh workflow run protected-ansible-apply.yml -f run_mode=apply -f confirm_apply=vps.nutsnews.com
```

Protected apply refreshes the public reporting status with `--dry-run` and the same managed email environment used by the systemd reporter units. Do not invoke the reporter binary bare: a direct process does not load `/etc/nutsnews/ops-reporter.env` and can temporarily misreport configured email as disabled. For routine refreshes, let the managed alert timer run through its systemd service.

Use the protected apply refresh task for portal status refreshes. It pauses
`nutsnews-ops-portal-collector.timer`, waits for any active collector run to
finish, starts `nutsnews-ops-portal-collector.service` through systemd, and
then resumes the timer. That avoids recording intentional apply refreshes as
`status=15/TERM` signal failures while still failing the apply for genuine
collector errors or timeouts. The collector also falls back to
`/etc/nutsnews/free-tier-usage.env` when its process environment is missing
free-tier settings, so external providers such as Vercel stay visible instead
of disappearing from the generated snapshot.

## Send A Manual Health Report

Use the `Send VPS Health Report` workflow in GitHub Actions for an on-demand report email. The workflow is manual-only, uses the protected `production-vps` Environment, connects as `nutsnews_ops`, and starts only `nutsnews-ops-health-report.service`.

The workflow has no dispatch inputs and does not accept remote commands. If email is disabled, SMTP is incomplete, or the report fails to send, the workflow prints the safe reporting status fields and exits failed.

## Backups In The Portal

The portal shows encrypted VPS backup status from the local restic runner: enabled/configured state, repository path, latest snapshot freshness, whether the latest snapshot has been verified, last backup, last prune, next backup run, next verify run, and protected path count. Raw backup path lists stay in root-only config and are not copied into public status JSON.

Backup failures, stale snapshots, prune failures, verification failures, overdue verification, and inactive backup or verify timers are emitted as warning or critical alerts. A newer daily snapshot remains visibly `latest_unverified` with `policy_status=pending` until the scheduled weekly verification and does not alert while it is inside the 192-hour policy window.

The runner treats top-level `last_error` as the active unresolved backup error. A later successful backup plus prune, or a later successful latest-snapshot verification, clears that active field and moves the previous value into bounded `resolved_errors` history with occurrence and resolution timestamps. Per-run failure details remain on `last_backup.error`, `last_prune.error`, or `last_check.error`.

The Free Tier section labels measurable backup capacity as `Backup Local Cache` in GiB against the VPS root filesystem. Snapshot age is backup freshness, not storage consumption. Remote OneDrive quota remains unmeasured unless a real read-only source is added; do not infer it from snapshot age or display invented zero usage.

Manual backup workflows stay narrow:

- `Run VPS Backup` starts only `nutsnews-restic-backup.service`
- `Verify VPS Backup` starts only `nutsnews-restic-verify.service`
- neither workflow accepts dispatch inputs or arbitrary remote commands

## Verify After Apply

From an approved administrative session on the VPS:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -i http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-ops-portal-collector.timer
systemctl status nutsnews-ops-alert-check.timer
systemctl status nutsnews-ops-health-report.timer
systemctl status nutsnews-restic-backup.timer
systemctl status nutsnews-restic-verify.timer
systemctl status nutsnews-docker-cleanup.timer
sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps
```

Expected `/healthz` output:

```text
ok
```

Expected unauthenticated portal status response:

```text
HTTP/1.1 302 Found
Location: /api/auth/signin/google
```

After signing in as `rami.deltoro@gmail.com`, `/data/status.json` should return the JSON feed. Signing in as any other Google account should return `403` with the access-denied message.

## Troubleshooting

If the portal loads but shows stale data, check the timer:

```bash
systemctl list-timers nutsnews-ops-portal-collector.timer
journalctl -u nutsnews-ops-portal-collector.service -n 80 --no-pager
python3 -m json.tool /opt/nutsnews/portal-assets/data/status.json | grep -A6 '"slow_sections"'
```

Docker cleanup status is collected from
`/opt/nutsnews/portal-assets/data/docker-cleanup-status.json` into the
`docker_cleanup` section of the portal feed. The public status summarizes
cadence, filters, latest result, prune return codes, Docker storage summary, and
protected-image counts without publishing protected refs or secrets.

If email reports do not arrive:

```bash
systemctl list-timers nutsnews-ops-alert-check.timer nutsnews-ops-health-report.timer
journalctl -u nutsnews-ops-alert-check.service -n 80 --no-pager
journalctl -u nutsnews-ops-health-report.service -n 80 --no-pager
sudo /usr/local/bin/nutsnews-ops-portal-reporter --mode report --dry-run
```

Confirm the portal shows `email_reporting.configured: true`. If it is disabled or misconfigured, fix the `production-vps` Environment secrets and run protected apply again. Do not edit `/etc/nutsnews/ops-reporter.env` by hand except for break-glass diagnosis, and document that afterward.

If the portal does not answer, check Caddy:

```bash
sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps
sudo docker logs --tail 200 nutsnews-caddy
```

If status data contains something sensitive, treat it as an incident, remove public access if any exists, rotate affected credentials, and fix the collector redaction through a PR before reapplying.

## Rollback

Revert the portal PR, merge it, and run the protected apply workflow. The previous Caddy health endpoint should remain the minimum verification target.

Do not manually delete portal files as routine cleanup. Manual repair is break-glass only and must be reconciled back into Git.
