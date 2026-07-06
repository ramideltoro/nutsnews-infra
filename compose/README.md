# Compose

Docker Compose service definitions for the VPS live here.

## Current Services

- `caddy/compose.yml`: Caddy edge service for the VPS service foundation.
- `caddy/Caddyfile`: Caddy config with admin API disabled, public TLS for `vps.nutsnews.com`, and loopback-only portal access.
- `health/index.html`: static local placeholder page used to verify the service layer.

The Caddy service publishes ports `80` and `443` for the public Better Stack `/health` endpoint. The operations portal remains bound to `127.0.0.1:8080` on the host and is not exposed publicly.

Do not commit environment files, tokens, credentials, or generated runtime artifacts.
