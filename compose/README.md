# Compose

Docker Compose service definitions for the VPS live here.

## Current Services

- `caddy/compose.yml`: Caddy edge service for the VPS service foundation.
- `caddy/Dockerfile`: reproducible Caddy build with the pinned free `mholt/caddy-ratelimit` module.
- `caddy/Caddyfile`: Caddy config with admin API disabled, public TLS for `vps.nutsnews.com` and `ops.nutsnews.com`, Caddy rate-limit imports, plus loopback portal access for health checks and SSH tunnel fallback.
- `health/index.html`: static local placeholder page used to verify the service layer.
- `staging-access/compose.yml`: bounded Cloudflare Access JWT verifier attached only to the staging network when the protected staging boundary is explicitly enabled.

The Caddy service publishes ports `80` and `443` for the public Better Stack `/health` endpoint and the Google OAuth-protected operations portal at `https://ops.nutsnews.com`. The operations portal also remains reachable on host loopback at `127.0.0.1:8080` for private health checks and SSH tunnel fallback.

Do not commit environment files, tokens, credentials, or generated runtime artifacts.
