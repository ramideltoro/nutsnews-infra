# Cloudflare DDNS

Use this runbook to manage the DNS-only `vps.nutsnews.com` A record for the NutsNews VPS.

Do not continue to TLS, Caddy public routing, or app deployment until DNS and the DDNS updater are verified.

## Record Target

```text
Zone: nutsnews.com
Record type: A
Name: vps
FQDN: vps.nutsnews.com
TTL: 60 seconds
Proxy status: DNS only
```

The current VPS IPv4 in inventory is `65.75.202.112`, but the updater discovers the active public IPv4 at runtime.

## Preferred Alternative

Use a provider-reserved/static IPv4 when the VPS provider offers one. A static IP is simpler than DDNS and avoids DNS churn for SSH, monitoring, and future app deployment.

The current IP WHOIS owner is Universal Layer LLC. Public Universal Layer docs do not clearly confirm a reserved/static IP product, so DDNS is acceptable if a static IP is unavailable or not worth using for this test deployment.

## Cloudflare API Token

Create a dedicated Cloudflare API token. Do not use the global API key.

Minimum permissions:

```text
Zone / Zone / Read
Zone / DNS / Edit
```

Scope:

```text
Include / Specific zone / nutsnews.com
```

Optional restrictions:

```text
Client IP filtering: VPS public IPv4, if you are comfortable updating this when the VPS IP changes
TTL: set an expiration if this is only for test deployment
```

Copy the token once and store it only as the `production-vps` GitHub Environment secret:

```text
NUTSNEWS_CLOUDFLARE_DDNS_API_TOKEN
```

Do not paste the token in chat, logs, committed files, shell history, or issue comments.

## Preflight Before Changing DNS

Before enabling the updater, inspect Cloudflare for existing `vps.nutsnews.com` A records.

If an existing `vps` A record exists, review:

```text
id
type
name
content
ttl
proxied
comment
modified_on
```

Do not overwrite an existing record until the change is approved.

If running the local helper directly, set the token only in the shell environment and use inspection mode:

```bash
read -rsp "Cloudflare token: " CLOUDFLARE_API_TOKEN
echo
export CLOUDFLARE_API_TOKEN
CLOUDFLARE_ZONE_NAME=nutsnews.com \
  CLOUDFLARE_RECORD_NAME=vps.nutsnews.com \
  CLOUDFLARE_DDNS_INSPECT_ONLY=1 \
  ansible/roles/vps_service_foundation/files/cloudflare_ddns.py
unset CLOUDFLARE_API_TOKEN
```

Do not paste the token into a committed file or a command transcript.

## Apply Through GitOps

Run the protected workflow in check mode first. With `enable_cloudflare_ddns` set to `true`, the workflow prints sanitized Cloudflare record metadata before Ansible runs.

```text
run_mode: check
enable_cloudflare_ddns: true
confirm_apply:
```

Then apply only after reviewing the output:

```text
run_mode: apply
enable_cloudflare_ddns: true
confirm_apply: vps.nutsnews.com
```

The Ansible role writes the token to `/etc/nutsnews/cloudflare-ddns.env` with `0600` permissions and installs:

```text
/usr/local/bin/nutsnews-cloudflare-ddns
/etc/systemd/system/nutsnews-cloudflare-ddns.service
/etc/systemd/system/nutsnews-cloudflare-ddns.timer
```

The timer runs every 1 minute.

## Verify

After apply, verify from the VPS without printing the token:

```bash
sudo systemctl status nutsnews-cloudflare-ddns.timer
sudo systemctl start nutsnews-cloudflare-ddns.service
sudo journalctl -u nutsnews-cloudflare-ddns.service -n 20 --no-pager
```

Expected no-change log shape:

```text
unchanged vps.nutsnews.com A record -> <current-ip>
```

Expected update log shape:

```text
updated vps.nutsnews.com A record <old-ip> -> <current-ip> proxied=False ttl=60
```

Verify DNS from a trusted machine:

```bash
dig +short vps.nutsnews.com A
```

The result should match the VPS public IPv4.

## Expected Delay

With a 60-second Cloudflare TTL and a 1-minute updater interval, a changed VPS IP usually reaches DNS clients within 1-10 minutes. Some recursive resolvers and clients cache longer, so occasional longer delays are possible.
