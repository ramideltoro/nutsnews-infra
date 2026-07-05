# NutsNews Infrastructure

This repository is the source of truth for the NutsNews VPS deployment.

## Strict GitOps Rule

After the initial bootstrap, no server changes should be made manually. All infrastructure, service configuration, deployment logic, scanners, operations portal changes, and runbook updates must flow through:

```text
commit -> PR -> checks -> merge -> pipeline apply
```

PR validation checks must pass before infrastructure changes are merged or applied.

Manual changes on the VPS create drift and must be avoided. Emergency manual intervention, if ever required, must be documented afterward and reconciled back into this repository.

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

## External Systems

The following systems remain managed outside this repository:

- Supabase
- Cloudflare Worker and KV
- Sentry
- Better Stack

Integrations with these systems may be documented or configured here when needed, but their primary resources and secrets remain external.
