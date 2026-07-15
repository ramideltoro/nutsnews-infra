# NutsNews Infrastructure

This repository is the source of truth for the NutsNews VPS deployment.

## Strict GitOps Rule

After the initial bootstrap, no server changes should be made manually. All infrastructure, service configuration, deployment logic, scanners, operations portal changes, and runbook updates must flow through:

```text
commit -> PR -> checks -> merge -> pipeline apply
```

PR validation checks must pass before infrastructure changes are merged or applied.

Manual changes on the VPS create drift and must be avoided. Emergency manual intervention, if ever required, must be documented afterward and reconciled back into this repository.

## Operating Model

NutsNews runs as a strict GitOps-managed, provider-agnostic VPS platform. The VPS is the primary production host; manual SSH is break-glass only; and the Ops Portal is the planned dashboard and control plane for state, services, deploys, checks, alerts, backups, runbooks, and reports.

The platform should stay lightweight for solo maintenance on a cheap VPS. Security, performance, resiliency, observability, recoverability, and regular email reporting are first-class requirements. The optional home server support node may help with encrypted offsite backups, restore testing, private monitoring, scheduled reporting, and background jobs, but it must never be required for the public website to stay online.

See [docs/OPERATIONS_CHARTER.md](docs/OPERATIONS_CHARTER.md) for the full operating charter.

Detailed learning and explanation docs for this repository live in [`ramideltoro/nutsnews-docs`](https://github.com/ramideltoro/nutsnews-docs). Every infra change must include a matching docs update there, pushed directly to that repo's `main` branch unless GitHub blocks the push. Local docs in this repo should stay short and operational.

## VPS Purpose

The VPS hosts the self-managed NutsNews runtime and operational tooling that belongs outside the managed external platforms. It is intended to run only the services, configuration, observability hooks, and automation declared in this repository.

The first Ansible bootstrap layer defines a lightweight Ubuntu baseline for `vps.nutsnews.com`: an automation admin user, SSH hardening with lockout protections, UFW, unattended security updates, fail2ban, time sync, persistent journald, log rotation, and local server facts output.

Ansible baseline applies are now designed to run through the protected `production-vps` GitHub Environment. The apply workflow is manual-only, defaults to check mode, connects as `nutsnews_ops`, and requires environment secrets for SSH material. It is not automatic on merge yet.

The next service foundation layer adds Ansible-managed Docker Engine and Docker Compose support, a standard `/opt/nutsnews` runtime layout, and a local-only Caddy placeholder service for verifying the container layer before public routing or heavier tooling is introduced.

The Ops Portal layer adds a read-only dashboard served publicly at `https://ops.nutsnews.com` through Caddy-managed TLS and protected by Google OAuth for `rami.deltoro@gmail.com` only. The local `127.0.0.1:8080` listener remains available for health checks and SSH tunnel fallback. A local systemd timer collects sanitized host, Docker, service, log, security, backup, alert, GitOps, and free-tier or usage-limited service status into `/opt/nutsnews/portal-assets/data/status.json`. The portal has no direct management buttons; future actions must still go through PR review and protected apply workflows.

The `Send VPS Health Report` workflow can be triggered manually through the same protected `production-vps` Environment. It connects as `nutsnews_ops` with the existing SSH secret and known-hosts pattern, then starts only the existing `nutsnews-ops-health-report.service` unit on the VPS.

The encrypted VPS backup layer uses restic with the rclone backend to write ciphertext to the dedicated OneDrive remote `nutsnews-onedrive` at `rclone:nutsnews-onedrive:nutsnews-backups/vps`. Backup secrets come only from the protected `production-vps` Environment, Ansible writes root-only config on the VPS, and scheduled/manual backup verification starts only fixed systemd units. The Ops Portal reports whether the newest snapshot has a recent successful verification.

The observability layer adds optional Grafana Alloy telemetry on the VPS and OpenTofu-managed Grafana Cloud folders, dashboards, quota guardrails, and Synthetic Monitoring checks. Grafana Cloud URLs, usernames, tenant-specific IDs, service account tokens, Access Policy tokens, synthetic targets, and Terraform backend coordinates are supplied through the protected environment, never committed.

## Repo Layout

```text
.
├── .github/workflows/   # CI, validation, and deployment workflows
├── ansible/             # Host configuration and operational automation
├── compose/             # Docker Compose service definitions
├── docs/                # Architecture, security, and operational documentation
├── portal/              # Operations portal source
├── runbooks/            # Step-by-step operational procedures
└── terraform/           # Infrastructure definitions
```

## Managed Here

- VPS infrastructure definitions
- Host configuration and hardening automation
- Docker Compose service manifests
- Deployment and validation pipelines
- Security and dependency scanning configuration
- Operations portal code and configuration
- Runbooks for deployment, operations, incident response, and security changes

## Required CI Gates

- Repository Hygiene: verifies the expected scaffold folders, PR template, CODEOWNERS, and naming guardrails.
- Workflow Safety: checks GitHub Actions syntax, scans workflow security, and blocks restricted trigger usage without approval.
- Secrets Scan: runs Gitleaks on pull requests, main branch pushes, manual runs, and a nightly schedule.
- Supply Chain: runs Dependency Review for pull requests and OSV-Scanner across the repository.
- Infrastructure Checks: runs YAML linting, OpenTofu formatting and validation, TFLint, Checkov, and Ansible linting when relevant files exist.
- Runtime Checks: validates Compose files when present, runs Hadolint for Dockerfiles, and scans filesystem/config risk with Trivy.
- Portal Checks: skips cleanly for the scaffold, then runs install, lint, test, and build scripts when `portal/package.json` exists.
- Nightly Audit: runs deeper workflow, dependency, and configuration scans on a schedule.

## Protected Manual Applies

Use [runbooks/PROTECTED_ANSIBLE_APPLY.md](runbooks/PROTECTED_ANSIBLE_APPLY.md) before running the manual Ansible workflow. Root SSH was only for the initial bootstrap and is now break-glass only.

Use [runbooks/VERCEL_VPS_ENV_SYNC.md](runbooks/VERCEL_VPS_ENV_SYNC.md) when synchronizing the reviewed Vercel Production environment variables to the VPS app.

Use [runbooks/VPS_SERVICE_FOUNDATION.md](runbooks/VPS_SERVICE_FOUNDATION.md) before applying or verifying the Docker and Caddy service foundation.

Use [runbooks/NUTSNEWS_STAGING_ACCESS.md](runbooks/NUTSNEWS_STAGING_ACCESS.md) before onboarding or applying the isolated staging hostname, Access boundary, or restricted deployment identity.

Use [runbooks/NUTSNEWS_STAGING_QUALIFICATION.md](runbooks/NUTSNEWS_STAGING_QUALIFICATION.md) before interpreting or rerunning the independent off-VPS staging qualifier.

Use [runbooks/OPS_PORTAL.md](runbooks/OPS_PORTAL.md) before applying or verifying the read-only operations portal.

Use the manual `Send VPS Health Report` workflow when you need an on-demand email report without opening an interactive SSH session.

Use [runbooks/VPS_BACKUP_SETUP.md](runbooks/VPS_BACKUP_SETUP.md), [runbooks/VPS_RESTORE.md](runbooks/VPS_RESTORE.md), and [runbooks/VPS_DISASTER_RECOVERY.md](runbooks/VPS_DISASTER_RECOVERY.md) before enabling backups, restoring files, or rebuilding on another VPS provider.

Use [runbooks/GRAFANA_CLOUD_OBSERVABILITY.md](runbooks/GRAFANA_CLOUD_OBSERVABILITY.md) before applying Grafana Cloud dashboards, quota guardrails, Synthetic Monitoring checks, or enabling Alloy telemetry writes on the VPS.

## External Systems

The following systems remain managed outside this repository:

- Supabase
- Cloudflare Worker and KV
- Sentry
- Better Stack

Integrations with these systems may be documented or configured here when needed, but their primary resources and secrets remain external.
