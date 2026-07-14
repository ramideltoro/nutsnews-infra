# Terraform

Terraform/OpenTofu definitions live here.

- [`grafana-state-bootstrap/cloudflare-r2`](grafana-state-bootstrap/cloudflare-r2/README.md): one-time Cloudflare R2 bucket bootstrap for Grafana Cloud OpenTofu remote state.
- [`grafana-cloud`](grafana-cloud/README.md): Grafana Cloud folders, dashboards, quota guardrails, and optional Synthetic Monitoring checks for the NutsNews VPS.
- [`staging-access`](staging-access/README.md): the additive proxied staging DNS record and Cloudflare Access browser/service-token boundary.

Do not commit state files, `.tfvars`, provider credentials, backend coordinates, Grafana URLs, tenant IDs, usernames, tokens, or generated local artifacts.
