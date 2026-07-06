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
- Caddy serving the portal on host loopback only at `127.0.0.1:8080`

## Security Model

The portal is read-only for v1. It does not mount the Docker socket into the web app, does not expose a shell, and does not include install, uninstall, restart, or reconfigure buttons.

Caddy remains bound to host loopback only. Do not expose the portal publicly until a later PR adds reviewed authentication and TLS routing.

Email reporting is opt-in. SMTP host, credentials, sender, recipients, and cooldown values come from the protected `production-vps` GitHub Environment and are rendered into a root-only env file during protected apply. If email is disabled or incomplete, the reporter exits cleanly and the portal shows reporting as disabled or misconfigured.

SSH hardening allows `nutsnews_ops` to create only local TCP forwards to `127.0.0.1:8080` or `localhost:8080` for portal access. Remote forwarding, gateway exposure, stream-local forwarding, tunnel devices, and broad forwarding stay disabled.

The portal forwarding policy is intentionally modeled with explicit SSH `Match` blocks. Do not put `AllowTcpForwarding no` or `PermitOpen none` back into the global baseline drop-in; those global directives can block the operator exception and bring back the `administratively prohibited` tunnel failure.

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

## Run Email Checks

Check mode should remain safe even if no SMTP secrets are configured:

```bash
gh workflow run protected-ansible-apply.yml -f run_mode=check
```

After reviewing check output and environment approval, apply with:

```bash
gh workflow run protected-ansible-apply.yml -f run_mode=apply -f confirm_apply=vps.nutsnews.com
```

To send no email but refresh the public reporting status on the VPS:

```bash
sudo /usr/local/bin/nutsnews-ops-portal-reporter --mode alert --dry-run
sudo /usr/local/bin/nutsnews-ops-portal-reporter --mode report --dry-run
sudo /usr/local/bin/nutsnews-ops-portal-collector
```

## Send A Manual Health Report

Use the `Send VPS Health Report` workflow in GitHub Actions for an on-demand report email. The workflow is manual-only, uses the protected `production-vps` Environment, connects as `nutsnews_ops`, and starts only `nutsnews-ops-health-report.service`.

The workflow has no dispatch inputs and does not accept remote commands. If email is disabled, SMTP is incomplete, or the report fails to send, the workflow prints the safe reporting status fields and exits failed.

## Backups In The Portal

The portal shows encrypted VPS backup status from the local restic runner: enabled/configured state, repository path, latest snapshot freshness, last backup, last prune, last verify, next timer run, and protected path count.

Backup failures, stale snapshots, prune failures, verification failures, and inactive backup timers are emitted as warning or critical alerts. The existing email alert timer sends those alerts when email reporting is enabled.

Manual backup workflows stay narrow:

- `Run VPS Backup` starts only `nutsnews-restic-backup.service`
- `Verify VPS Backup` starts only `nutsnews-restic-verify.service`
- neither workflow accepts dispatch inputs or arbitrary remote commands

## Verify After Apply

From an approved administrative session on the VPS:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-ops-portal-collector.timer
systemctl status nutsnews-ops-alert-check.timer
systemctl status nutsnews-ops-health-report.timer
systemctl status nutsnews-restic-backup.timer
sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps
```

Expected `/healthz` output:

```text
ok
```

Expected portal status data:

```text
generated_at
```

## Troubleshooting

If the portal loads but shows stale data, check the timer:

```bash
systemctl list-timers nutsnews-ops-portal-collector.timer
journalctl -u nutsnews-ops-portal-collector.service -n 80 --no-pager
```

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
