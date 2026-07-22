# Cloudflare DNS Failover

Use this runbook to prepare and operate the `nutsnews-dns-failover` Cloudflare Worker. It manages DNS failover for `nutsnews.com` and `www.nutsnews.com` without paid Cloudflare Load Balancing and without putting apex/www visitor traffic through a Worker.

During normal operation, normal visitor requests do not execute this Worker. The Worker has no `nutsnews.com/*` or `www.nutsnews.com/*` route. Cloudflare DNS/proxying sends visitors to whichever DNS target is active.

## Controller Model

- Worker: `nutsnews-dns-failover`
- Durable Object: `DnsFailoverController`
- Durable Object instance name: `nutsnews-production-vps-primary`
- Retired Durable Object instance names: `nutsnews-production`
- Cron Trigger: `* * * * *`
- Durable Object alarm cadence: 15 seconds
- VPS readiness check: `https://vps.nutsnews.com/readyz`
- Expected readiness target: `production-vps`
- Failure threshold: 3 consecutive VPS readiness failures
- Recovery threshold: 1 successful VPS readiness check while DNS is on Vercel

The Cron Trigger is only a minute-level watchdog/bootstrap. It forwards to the named Durable Object, which calls `setAlarm()` and owns the recurring 15-second loop. The Durable Object alarm performs the readiness check, reads the current DNS state, updates counters, writes DNS only when allowed, persists state, logs a sanitized event, and schedules the next alarm.

If Durable Object Alarms are unavailable on the Cloudflare plan, do not cut over in #396. Pause, document the missing capability, and choose a reviewed alternate cadence instead of silently falling back to minute-level cron.

## DNS Topology

After cutover, keep both managed production records as proxied CNAME records:

```text
nutsnews.com      CNAME vps.nutsnews.com         Proxied Auto TTL
www.nutsnews.com  CNAME vps.nutsnews.com         Proxied Auto TTL
```

The Vercel fallback target is:

```text
cname.vercel-dns.com
```

The controller keeps record types stable after cutover by changing only CNAME content between `vps.nutsnews.com` and `cname.vercel-dns.com`. If the pre-cutover Cloudflare records are still A records, #396 must replace them with the reviewed proxied CNAME records during the cutover window before relying on automatic failover.

Cloudflare proxied records use Auto TTL, currently 300 seconds. DNS failover is therefore not instant; recursive resolvers or local client caches can take longer than 300 seconds to reflect a change.

## Protected Secrets

Store these only in the `cloudflare-admin` GitHub Environment:

```text
NUTSNEWS_CLOUDFLARE_ACCOUNT_ID
NUTSNEWS_DNS_FAILOVER_DEPLOY_API_TOKEN
NUTSNEWS_DNS_FAILOVER_DNS_API_TOKEN
NUTSNEWS_DNS_FAILOVER_ZONE_ID
NUTSNEWS_DNS_FAILOVER_RECORDS_JSON
NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN
```

Use separate tokens when possible:

- Deploy token: Workers Scripts edit permission for the target account.
- Runtime DNS token: minimum Cloudflare DNS edit scope for the `nutsnews.com` zone.

`NUTSNEWS_DNS_FAILOVER_RECORDS_JSON` stores the apex and www DNS record ids without printing them in logs:

```json
[
  {"id":"<apex-record-id>","name":"nutsnews.com","type":"CNAME"},
  {"id":"<www-record-id>","name":"www.nutsnews.com","type":"CNAME"}
]
```

Do not paste tokens, private headers, or sensitive origin details into issues, logs, docs, or shell history.

## Plan And Apply

Use the manual `Cloudflare DNS Failover Apply` workflow from `main`.

Plan mode:

```text
run_mode: plan
dns_writes_enabled: false
confirm_apply:
confirm_dns_writes:
```

Apply the Worker without enabling automatic DNS writes:

```text
run_mode: apply
dns_writes_enabled: false
confirm_apply: dns-failover.nutsnews.com
confirm_dns_writes:
```

Enable or refresh automatic DNS writes only through the protected apply
workflow from `main`:

```text
run_mode: apply
dns_writes_enabled: true
confirm_apply: dns-failover.nutsnews.com
confirm_dns_writes: enable-dns-writes-for-nutsnews.com
```

When writes are disabled, the Durable Object can still schedule checks and report state, but automatic failover and failback DNS writes are suppressed. Manual failover and manual failback endpoints still require the admin bearer token and explicit confirmation bodies.

## Admin Operations

The Worker exposes protected admin endpoints on its workers.dev hostname only. Every endpoint except `/healthz` requires:

```text
Authorization: Bearer <NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN>
```

Safe status check:

```bash
curl -fsS -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  https://<worker-subdomain>.workers.dev/status
```

Manual lock or unlock:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"locked":true,"reason":"incident hold"}' \
  https://<worker-subdomain>.workers.dev/manual-lock
```

Manual failover:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"confirm":"failover-to-vercel"}' \
  https://<worker-subdomain>.workers.dev/manual-failover
```

Manual failback:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"confirm":"failback-to-vps"}' \
  https://<worker-subdomain>.workers.dev/manual-failback
```

Expiring VPS health failure override for a controlled failover drill:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"confirm":"force-vps-health-failure","failureCount":3,"ttlSeconds":120,"reason":"issue-397 failover drill"}' \
  https://<worker-subdomain>.workers.dev/test-health-override
```

Clear the override immediately after the drill if any forced failures remain:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"confirm":"clear-vps-health-override"}' \
  https://<worker-subdomain>.workers.dev/test-health-override
```

Use `/test-health-override` only for a bounded operator drill. It is
admin-token protected, requires an explicit confirmation phrase, accepts at
most 20 forced failures, expires after at most 300 seconds, and stores no
secret material. It does not change DNS directly; it only makes the next
controller checks see synthetic VPS readiness failures so the normal threshold
and DNS-state gates decide whether to fail over.

Retire a previously used Durable Object instance after a controller-name
rotation:

```bash
curl -fsS -X POST -H "Authorization: Bearer $NUTSNEWS_DNS_FAILOVER_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"confirm":"retire-dns-failover-controller","controllerName":"nutsnews-production","reason":"controller name rotated to nutsnews-production-vps-primary"}' \
  https://<worker-subdomain>.workers.dev/retire-controller
```

The retire endpoint can target only names listed in `RETIRED_CONTROLLER_NAMES`
and refuses the active `CONTROLLER_NAME`. Retirement sets a manual lock on the
old Durable Object, clears any test override, and calls `deleteAlarm()` so the
old instance cannot keep running a stale 15-second loop against the managed DNS
records.

## Verification

Before #396, verify that the Worker deploys and the status endpoint reports:

- `checkIntervalSeconds: 15`
- `failureThreshold: 3`
- `dnsWritesEnabled: false`
- no manual lock unless an incident requires it
- last check timestamps updating after cron and Durable Object alarm propagation

After #396, with writes enabled and VPS primary active:

- `https://nutsnews.com/readyz` and `https://www.nutsnews.com/readyz` report `production-vps` while VPS is healthy.
- The controller status reports DNS on `vps` and no pending or failed DNS action.
- The first and second consecutive VPS failures do not update DNS.
- The third consecutive VPS failure updates apex and www to `cname.vercel-dns.com`.
- While DNS points to Vercel, checks continue every 15 seconds.
- Once VPS is reachable and current DNS is Vercel, failback updates apex and www to `vps.nutsnews.com`.

Controlled drill sequence:

1. Verify direct Vercel secondary health/readiness and confirm the expected
   source commit before mutating DNS.
2. Set the expiring health override with `failureCount:3`.
3. Call `/check-now` once and verify `consecutiveFailureCount: 1`,
   `lastDnsAction: none:failure_threshold_not_met`, and Cloudflare DNS still
   points apex and `www` to `vps.nutsnews.com`.
4. Call `/check-now` a second time and verify `consecutiveFailureCount: 2`,
   `lastDnsAction: none:failure_threshold_not_met`, and Cloudflare DNS still
   points apex and `www` to `vps.nutsnews.com`.
5. Call `/check-now` a third time and verify `lastDnsAction:
   updated:vps_failure_threshold:vercel`, Cloudflare DNS points both records to
   `cname.vercel-dns.com`, and public apex/`www` health reports
   `x-nutsnews-deployment-target: vercel-production` after propagation.
6. Confirm `/status` continues to update `lastCheckTimestamp` while DNS points
   to Vercel.
7. Clear the test override if needed. With real VPS readiness healthy and DNS
   currently on Vercel, wait through the DNS update cooldown and verify
   failback updates both records to `vps.nutsnews.com`.
8. Finish only after apex/`www` again report
   `x-nutsnews-deployment-target: production-vps` and controller status is
   healthy on `activeDnsTarget: vps`.

If the controller or Cloudflare API is unavailable, use the Cloudflare dashboard or API directly to perform manual failover or manual failback using the same record names, CNAME targets, proxied status, and Auto TTL above. Reconcile any emergency change back into this repository afterward.
