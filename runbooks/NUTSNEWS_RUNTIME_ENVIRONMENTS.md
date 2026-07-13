# NutsNews Runtime Environments

NutsNews has exactly two runtime identities: `production` and `staging`.
Production preserves the existing `nutsnews-app` Compose project, route, image
digest, and `nutsnews-edge` Caddy network. Staging has separate Compose,
application, environment, manifest, marker, last-known-good, cache-volume, and
network identities.

Staging remains disabled by default because issue #118 has not supplied the
measured VPS resource budget required to qualify a second runtime. Do not
enable it or add a staging deploy workflow, credentials, route, TLS boundary,
or promotion process through this configuration.

Every configured image must be a reviewed
`ghcr.io/ramideltoro/nutsnews@sha256:<digest>` reference. Mutable tags fail
before Compose is invoked. Caddy joins only the production app network, so a
staging container cannot become a production upstream.

For local verification, run the rendered Compose configurations and
`ansible/tests/validate_nutsnews_environment_isolation.py`. A staging-only
Ansible check must leave the production application paths and container outside
its plan.
