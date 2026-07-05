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

## External Systems

The following systems remain managed outside this repository:

- Supabase
- Cloudflare Worker and KV
- Sentry
- Better Stack

Integrations with these systems may be documented or configured here when needed, but their primary resources and secrets remain external.
