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
- Optional GitOps-controlled NutsNews app route on `https://vps.nutsnews.com` while `/health` remains the infrastructure health endpoint
- Small Ansible-managed zram fallback swap on `/dev/zram0` with low swappiness
- Local portal status collector managed by `nutsnews-ops-portal-collector.timer`
- Caddy rate limiting for public health, API, auth-sensitive, admin-sensitive, ops-sensitive, and general public paths

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
sudo ss -ltnp 'sport = :18080'
sudo ufw status verbose
free -h
swapon --show
cat /proc/sys/vm/swappiness
sudo journalctl -k --since "-7 days" --no-pager | grep -Ei "out of memory|oom-killer|killed process" || true
sudo /usr/local/bin/nutsnews-ops-portal-collector
sudo docker logs nutsnews-caddy --since 10m | grep -E '"status":429|rate'
```

From a workstation or external monitor after DNS is pointed at the VPS and ports `80` and `443` are reachable:

```bash
curl -i https://vps.nutsnews.com/health
curl --connect-timeout 3 --max-time 5 http://vps.nutsnews.com:18080/health
```

The HTTPS request must return HTTP `200`. The direct port-`18080` request must
not connect; it is an intentional private-only failure.

When `vps_service_foundation_nutsnews_app_public_route_enabled` is `true`, also
verify the app route after the approved protected apply:

```bash
curl -i https://vps.nutsnews.com/
curl -i https://vps.nutsnews.com/healthz
curl -i 'https://vps.nutsnews.com/api/articles?page=0'
curl -i https://vps.nutsnews.com/api/auth/signin/google
```

`/health` must keep returning the infrastructure health response. `/healthz`
must return the app health response with the reviewed source commit and build
identity. Record exact statuses, redirects, security headers, cookies,
CSRF/CORS behavior, asset loading, cache behavior, Turnstile/contact-form
origins, and Sentry identity before calling the rollout complete.

## Rate Limiting

Caddy is the public reverse proxy for the VPS. The protected workflow builds the repo-managed Caddy image with the pinned free `mholt/caddy-ratelimit` module, copies `/opt/nutsnews/config/caddy/rate-limits`, validates the Compose config, and recreates the Caddy service when the Caddyfile, Dockerfile, route file, or rate-limit config changes.

Default Caddy limits are keyed by `{remote_host}` with IPv6 clients grouped by `/64`:

| Route group | Paths | Limit |
| --- | --- | --- |
| Health-sensitive endpoints | `/health`, `/healthz` | 30 requests per minute |
| Auth and ops-sensitive routes | `/api/auth/*`, `/login*`, `/ops*` | 20 requests per minute |
| Admin UI navigation | `/admin*` | 120 requests per minute |
| API routes | `/api/*` | 60 requests per minute |
| Public/default content | `/*` | 600 requests per minute |

Requests over the limit return HTTP 429 with `Retry-After`. Caddy access logs are written as JSON to Docker stdout so Alloy can parse request metadata without extra host mounts. Rate-limit hits can be inspected with:

```bash
sudo docker logs nutsnews-caddy --since 30m | grep -E '"status":429|rate'
```

Tune limits in `ansible/roles/vps_service_foundation/defaults/main.yml` by changing `vps_service_foundation_caddy_rate_limit_zones`, then run the protected workflow in `check` mode before `apply`. To disable the limiter temporarily through GitOps, set `vps_service_foundation_caddy_rate_limits_enabled: false`, merge the PR, and apply through the same workflow.

The admin UI has a separate higher-capacity bucket because a normal Next.js
App Router navigation can issue multiple HTML, RSC, and route requests. Keep
the stricter `/api/auth/*` bucket separate so credential and callback abuse is
still throttled without turning ordinary `/admin/*` navigation into a 429.
Repeated 429s on `/admin/login` or an admin dashboard should first be checked
against the Caddy JSON logs and the `Retry-After` header; do not bypass the
limiter with manual host changes. A regression test locks the separate zones,
their paths, and their budgets in `ansible/tests/validate_caddy_rate_limits.py`.

Cloudflare is currently managed here only for DDNS records and defaults to DNS-only records. If Cloudflare proxying is enabled later, add complementary Cloudflare WAF/rate-limit rules there and review Caddy client IP handling before relying on `{remote_host}`.

Expected `/healthz` output:

```text
ok
```

Expected `/health` success output:

```json
{"ok":true,"service":"nutsnews-infra"}
```

## Infrastructure Health Check

The public `/health` endpoint is intended for Better Stack and other HTTP status-code monitors. Caddy proxies `/health` to the local `nutsnews-infra-health.service`, which listens only on the Docker host-gateway address `172.17.0.1:18080` and returns:

When the Ansible-managed systemd unit changes, or listener inspection finds a
stale bind, protected apply restarts this service and asserts the narrow
listener is active. Do not restart it manually to force a configuration change.

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
curl -i http://172.17.0.1:18080/health
sudo journalctl -u nutsnews-infra-health.service -n 80 --no-pager
sudo tail -n 40 /opt/nutsnews/logs/health/health-failures.jsonl
```

Do not simulate failures by editing or restarting the production service over
SSH. Exercise failure behavior in an isolated environment, then make any
production change through a reviewed GitOps update and protected apply.

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

Caddy publishes public ports `80` and `443` for `vps.nutsnews.com`. The public virtual host always keeps `/health` on the local `nutsnews-infra-health.service` through `host.docker.internal`, which resolves to the Docker host-gateway address `172.17.0.1`. When the reviewed app public route flag is disabled, all other `vps.nutsnews.com` paths return `404`; when it is enabled, all other paths proxy to the digest-pinned NutsNews app container.

The loopback staged route is a health-only gate. It proxies
`/app-stage/healthz` to the app, but it does not provide full authenticated
HTML, asset, API, Auth.js callback, Turnstile, contact-form, cookie, CSRF/CORS,
Sentry, or cache parity. Full app parity validation must happen immediately
after the reviewed public route is enabled through Protected Ansible Apply.

UFW allows only the Caddy Docker network (`172.18.0.0/16`) to reach the host health service on TCP port `18080`. Direct public access to TCP port 18080 is intentionally blocked. This internal rule is managed by Ansible so the public Better Stack endpoint works without manual firewall changes.

The operations portal is exposed publicly at `https://ops.nutsnews.com` through Caddy-managed TLS and the Ops Portal Google OAuth gateway. The host loopback listener at `127.0.0.1:8080` remains available for private health checks and SSH tunnel fallback. When Cloudflare DDNS is enabled, the protected apply workflow updates both `vps.nutsnews.com` and `ops.nutsnews.com` A records immediately and keeps the DDNS timer enabled for future VPS public IPv4 changes.

Do not make manual firewall, DNS, or reverse proxy changes on the VPS. Apply routing changes through the protected workflow after PR review and verify with:

```bash
curl -i http://127.0.0.1:8080/health
curl -i https://vps.nutsnews.com/health
curl -i https://ops.nutsnews.com/
```

Rate-limit verification after deployment:

```bash
for i in $(seq 1 35); do curl -sk -o /dev/null -w "%{http_code}\n" https://vps.nutsnews.com/health; done
sudo docker logs nutsnews-caddy --since 10m | grep -E '"status":429|rate'
```

## Recovery

If the service layer fails:

1. Rerun the protected workflow in check mode.
2. Check `sudo docker compose -f /opt/nutsnews/apps/caddy/compose.yml ps`.
3. Check `sudo docker logs nutsnews-caddy`.
4. Check `/opt/nutsnews/config/caddy/Caddyfile`.
5. Reconcile any manual repair through a PR.

The Ansible role prints Compose status and the last Caddy logs automatically if `/healthz` does not answer during apply.

If logs show `exec /usr/bin/caddy: operation not permitted`, check that the Compose file grants only `NET_BIND_SERVICE` and does not set `no-new-privileges`. The official Caddy image uses a file capability on the binary, and over-hardening can stop the process before it starts. App and staging-access containers may still use `no-new-privileges=true`, which is Docker's supported Compose form.
