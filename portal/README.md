# Operations Portal

This directory contains the static NutsNews Operations Portal v1 shell.

The portal is read-only, has no local management buttons, and reads only the sanitized JSON snapshot generated on the VPS at `/opt/nutsnews/portal-assets/data/status.json`.

The Free Tier Usage section is populated by the read-only collector module installed as `/usr/local/bin/ops_free_tier_usage.py` plus local VPS resource readings from the main status collector. Quotas are supplied by Ansible configuration or live local limits, not by hard-coded browser values. The UI groups rows by service and shows per-metric measurement state: `measured`, `missing credential`, `unavailable`, `unsupported`, or `unknown`. Optional provider tokens or normalized usage snapshots can improve freshness, but missing or unsupported sources must render honestly rather than breaking the dashboard.

The main status feed also reports swap capacity, swap usage state, sustained/non-trivial swap warnings, and recent kernel OOM evidence. If swap or kernel-log data cannot be read, the feed must use explicit disabled, unavailable, or unsupported states rather than zero-like placeholders.

All dashboard routes are served through `auth_gateway.py`, which uses Google OAuth and allows only `rami.deltoro@gmail.com`. The callback route path is fixed at `/api/auth/callback/google`; the configured redirect URI must be `https://<dashboard-domain>/api/auth/callback/google`.

Do not commit secrets, local environment files, generated build artifacts, private keys, tokens, or runtime status snapshots.
