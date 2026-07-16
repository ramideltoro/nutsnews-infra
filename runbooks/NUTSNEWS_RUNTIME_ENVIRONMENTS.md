# NutsNews Runtime Environments

NutsNews has exactly two runtime identities: `production` and `staging`.
Production preserves the existing `nutsnews-app` Compose project, route, image
digest, and `nutsnews-edge-v6` Caddy network. Staging has separate Compose,
application, environment, manifest, marker, last-known-good, cache-volume, and
network identities.

Staging remains disabled by default. Issue #118 records the measured same-host
capacity contract, but it does not authorize this configuration to enable a
staging deploy workflow, credentials, route, TLS boundary, or promotion
process. See `ramideltoro/nutsnews-docs`:
`NUTSNEWS_VPS_STAGING_CAPACITY.md`.

The shared app Compose contract renders per-runtime CPU, memory reservation and
limit, PID, and `json-file` log caps. Staging is fixed at one CPU, 512 MiB
memory maximum, 256 MiB reservation, 128 PIDs, and 10 MiB x three logs. Keep
the later staging qualifier within its documented test budget and scale it down
after the 24-hour qualification expiry.

Every configured image must be a reviewed
`ghcr.io/ramideltoro/nutsnews@sha256:<digest>` reference. Mutable tags fail
before Compose is invoked. Caddy joins only the production app network, so a
staging container cannot become a production upstream.

For local verification, run the rendered Compose configurations and
`ansible/tests/validate_nutsnews_environment_isolation.py`. A staging-only
Ansible check must leave the production application paths and container outside
its plan.
