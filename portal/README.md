# Operations Portal

This directory contains the static NutsNews Operations Portal v1 shell.

The portal is read-only, has no local management buttons, and reads only the sanitized JSON snapshot generated on the VPS at `/opt/nutsnews/portal-assets/data/status.json`.

The Free Tier Usage section is populated by the read-only collector module installed as `/usr/local/bin/ops_free_tier_usage.py`. Quotas are supplied by Ansible configuration, not by hard-coded browser values. Optional provider tokens or normalized usage snapshots can improve freshness, but missing tokens must render as `not configured`, `cached`, `unavailable`, or `unknown` rather than breaking the dashboard.

All dashboard routes are served through `auth_gateway.py`, which uses Google OAuth and allows only `rami.deltoro@gmail.com`. The callback route path is fixed at `/api/auth/callback/google`; the configured redirect URI must be `https://<dashboard-domain>/api/auth/callback/google`.

Do not commit secrets, local environment files, generated build artifacts, private keys, tokens, or runtime status snapshots.
