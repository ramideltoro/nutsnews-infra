# VPS Service Foundation Runbook

Use this runbook after the service foundation PR is merged and before applying the baseline through the protected workflow.

## What This Adds

- Docker Engine from Ubuntu packages
- Docker Compose v2 from Ubuntu packages
- `/opt/nutsnews` runtime layout:
  - `/opt/nutsnews/apps`
  - `/opt/nutsnews/config`
  - `/opt/nutsnews/data`
  - `/opt/nutsnews/logs`
- `/opt/nutsnews/backups`
- `/opt/nutsnews/portal-assets`
- `/opt/nutsnews/portal-assets/assets`
- `/opt/nutsnews/portal-assets/data`
- `/opt/nutsnews/health`
- `/opt/nutsnews/ops`
- Caddy managed by Compose at `/opt/nutsnews/apps/caddy/compose.yml`
- Read-only operations portal and `/healthz` endpoint on `127.0.0.1:8080`
- Local portal status collector managed by `nutsnews-ops-portal-collector.timer`

## Apply Safely

1. Open the `Protected Ansible Apply` workflow.
2. Run `check` mode first.
3. Review the Ansible recap and diff.
4. Run `apply` mode only after check mode looks safe.
5. Keep root SSH as break-glass only.

On a fresh VPS, check mode simulates Docker package installation but does not create the `docker` service, `docker` group, service users, or `/opt/nutsnews` directories. The role intentionally skips those runtime-dependent tasks in check mode and performs them during apply mode.

## Verify After Apply

From a break-glass-free SSH session as `nutsnews_ops`:

```bash
sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/
curl -fsS http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-ops-portal-collector.timer
```

Expected `/healthz` output:

```text
ok
```

## Public Exposure

Caddy is intentionally bound to `127.0.0.1:8080` only. Do not expose public routing until a later PR adds reviewed domain, TLS, and authentication rules.

## Recovery

If the service layer fails:

1. Rerun the protected workflow in check mode.
2. Check `sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps`.
3. Check `sudo docker logs nutsnews-caddy`.
4. Check `/opt/nutsnews/config/caddy/Caddyfile`.
5. Reconcile any manual repair through a PR.

The Ansible role prints Compose status and the last Caddy logs automatically if `/healthz` does not answer during apply.

If logs show `exec /usr/bin/caddy: operation not permitted`, check that the Compose file grants only `NET_BIND_SERVICE` and does not set `no-new-privileges:true`. The official Caddy image uses a file capability on the binary, and over-hardening can stop the process before it starts.
