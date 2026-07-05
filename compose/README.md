# Compose

Docker Compose service definitions for the VPS live here.

## Current Services

- `caddy/compose.yml`: local-only Caddy placeholder service for the VPS service foundation.
- `caddy/Caddyfile`: conservative Caddy config with admin API and automatic HTTPS disabled for now.
- `health/index.html`: static local placeholder page used to verify the service layer.

The Caddy service binds to `127.0.0.1:8080` on the host. It is intentionally not public yet; public routing belongs in a later reviewed PR.

Do not commit environment files, tokens, credentials, or generated runtime artifacts.
