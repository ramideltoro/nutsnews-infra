# Operations Portal Runbook

Use this runbook after the Ops Portal v1 PR is merged and before applying the service foundation through the protected workflow.

## What This Adds

- Static portal assets under `/opt/nutsnews/portal-assets`
- A local JSON status feed at `/opt/nutsnews/portal-assets/data/status.json`
- A root-run, read-only local collector at `/usr/local/bin/nutsnews-ops-portal-collector`
- A systemd timer named `nutsnews-ops-portal-collector.timer`
- Caddy serving the portal on host loopback only at `127.0.0.1:8080`

## Security Model

The portal is read-only for v1. It does not mount the Docker socket into the web app, does not expose a shell, and does not include install, uninstall, restart, or reconfigure buttons.

Caddy remains bound to host loopback only. Do not expose the portal publicly until a later PR adds reviewed authentication and TLS routing.

## Apply Safely

1. Open the `Protected Ansible Apply` workflow.
2. Run `check` mode first.
3. Confirm the role plans portal assets, collector units, Caddy config, and status data without production secrets.
4. Run `apply` mode only after check mode looks safe.
5. Keep any manual SSH inspection break-glass only and document it afterward.

## Verify After Apply

From an approved administrative session on the VPS:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-ops-portal-collector.timer
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

If the portal does not answer, check Caddy:

```bash
sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps
sudo docker logs --tail 200 nutsnews-caddy
```

If status data contains something sensitive, treat it as an incident, remove public access if any exists, rotate affected credentials, and fix the collector redaction through a PR before reapplying.

## Rollback

Revert the portal PR, merge it, and run the protected apply workflow. The previous Caddy health endpoint should remain the minimum verification target.

Do not manually delete portal files as routine cleanup. Manual repair is break-glass only and must be reconciled back into Git.
