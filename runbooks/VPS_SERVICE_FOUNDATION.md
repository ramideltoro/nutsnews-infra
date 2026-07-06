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
- Better Stack-compatible infrastructure health endpoint at `https://vps.nutsnews.com/health`
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
curl -i http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/
curl -fsS http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-infra-health.service
systemctl status nutsnews-ops-portal-collector.timer
```

From a workstation or external monitor after DNS is pointed at the VPS and ports `80` and `443` are reachable:

```bash
curl -i https://vps.nutsnews.com/health
```

Expected `/healthz` output:

```text
ok
```

Expected `/health` success output:

```json
{"ok":true,"service":"nutsnews-infra"}
```

## Infrastructure Health Check

The public `/health` endpoint is intended for Better Stack and other HTTP status-code monitors. Caddy proxies `/health` to the local `nutsnews-infra-health.service`, which listens on the host health port and returns:

- HTTP `200` only when all required checks pass
- HTTP `503` when any required check fails

The response body is intentionally minimal. It does not expose secrets, environment values, hostnames, tokens, database URLs, stack traces, or detailed diagnostics.

Default required checks:

- CPU usage below `60%`
- memory usage below `60%`
- disk usage below `60%` for `/` and `/opt/nutsnews`
- active systemd units: `ssh.service`, `docker.service`, `unattended-upgrades.service`, `ufw.service`, `fail2ban.service`, `nutsnews-infra-health.service`, `nutsnews-ops-portal-collector.timer`, `nutsnews-ops-alert-check.timer`, and `nutsnews-ops-health-report.timer`
- running and healthy Docker containers: `nutsnews-caddy`, plus `nutsnews-app` when the app layer is enabled

Failure details are written to journald for `nutsnews-infra-health.service` and to:

```text
/opt/nutsnews/logs/health/health-failures.jsonl
```

Each failure log row includes timestamp, failed check, measured value, threshold, relevant service/container/path, and a short reason. The public HTTP response only includes generic failed check groups.

Safe local test commands after apply:

```bash
curl -i http://127.0.0.1:8080/health
sudo journalctl -u nutsnews-infra-health.service -n 80 --no-pager
sudo tail -n 40 /opt/nutsnews/logs/health/health-failures.jsonl
```

Safe failure simulation without stopping services:

```bash
sudo systemctl edit nutsnews-infra-health.service
```

Add this temporary override:

```ini
[Service]
Environment="NUTSNEWS_INFRA_HEALTH_SIMULATE_FAILURES=manual-test"
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart nutsnews-infra-health.service
curl -i http://127.0.0.1:8080/health
sudo journalctl -u nutsnews-infra-health.service -n 80 --no-pager
```

Remove the override and restart the service to restore normal checks:

```bash
sudo systemctl revert nutsnews-infra-health.service
sudo systemctl daemon-reload
sudo systemctl restart nutsnews-infra-health.service
```

Better Stack settings:

```text
Monitor type: HTTP status code
URL: https://vps.nutsnews.com/health
Expected status: 2xx
Check frequency: 1 minute
Alert after: 2-3 failed checks
Suggested monitor name: NutsNews Infra Health
Recommended regions: US East, US West, EU West
```

## Public Exposure

Caddy publishes public ports `80` and `443` for `vps.nutsnews.com`. The public virtual host exposes only `/health` and returns `404` for other paths. Caddy proxies `/health` to the local `nutsnews-infra-health.service` through the host gateway.

The operations portal remains bound to `127.0.0.1:8080` on the host and is not exposed publicly. Keep Cloudflare DNS-only unless a later approved change explicitly enables proxying.

Do not make manual firewall, DNS, or reverse proxy changes on the VPS. Apply routing changes through the protected workflow after PR review and verify with:

```bash
curl -i http://127.0.0.1:8080/health
curl -i https://vps.nutsnews.com/health
```

## Recovery

If the service layer fails:

1. Rerun the protected workflow in check mode.
2. Check `sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps`.
3. Check `sudo docker logs nutsnews-caddy`.
4. Check `/opt/nutsnews/config/caddy/Caddyfile`.
5. Reconcile any manual repair through a PR.

The Ansible role prints Compose status and the last Caddy logs automatically if `/healthz` does not answer during apply.

If logs show `exec /usr/bin/caddy: operation not permitted`, check that the Compose file grants only `NET_BIND_SERVICE` and does not set `no-new-privileges:true`. The official Caddy image uses a file capability on the binary, and over-hardening can stop the process before it starts.
