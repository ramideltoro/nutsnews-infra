# Operations Charter

This charter defines the operating model for the NutsNews infrastructure repository and the VPS platform it manages.

## Mission

The NutsNews VPS must be extremely stable, lightweight, secure, observable, recoverable, and easy to move between providers. The repository is the source of truth for infrastructure, service configuration, scanners, operations workflows, reports, and the Ops Portal.

The platform should minimize routine manual maintenance after bootstrap. Day-to-day operations must be driven by committed changes, pull requests, CI scanners, merges, and automated apply workflows.

## GitOps Model

All changes follow this path:

```text
commit -> PR -> CI scanners -> merge -> automated apply
```

Manual server changes are drift. They are allowed only for break-glass recovery, must be documented afterward, and must be reconciled back into this repository.

Future apply workflows must be auditable, repeatable, and safe to rerun. They must not require production secrets for pull request validation.

## Primary Production Host

The VPS is the primary production host. It should run only the services, configuration, observability hooks, backup jobs, reporting jobs, and automation declared here.

Operational priorities:

- Security: least privilege, secret scanning, dependency scanning, infrastructure scanning, and minimal public exposure.
- Stability: conservative changes, validation before merge, simple runtime components, and predictable rollback paths.
- Performance: lightweight services that fit a cheap VPS without wasting CPU, memory, disk, or network.
- Resiliency: automated health checks, restart policies, backups, restore testing, and documented incident procedures.
- Observability: clear service state, logs, metrics, checks, alerts, and reporting surfaced through the Ops Portal.
- Recoverability: encrypted backups, tested restore paths, provider-agnostic rebuild steps, and documented break-glass access.
- Reporting: regular email reports for deploys, checks, alerts, backups, security scans, and operational summaries.

## Provider-Agnostic Design

The platform must be portable across VPS providers. Provider-specific details should be isolated behind Terraform/OpenTofu variables, Ansible inventory, documented assumptions, or small provider modules.

Avoid designs that depend on one provider's proprietary runtime features unless they are optional and replaceable.

## Ops Portal

The Ops Portal is the central dashboard and control plane for the VPS platform. It should eventually surface:

- VPS health, capacity, uptime, and security posture
- Service state, versions, logs, and restart history
- Deploy status, release history, and rollback options
- CI checks, scanner results, and policy failures
- Alerts, incidents, acknowledgements, and follow-up actions
- Backup status, restore-test status, and retention health
- Runbooks, maintenance windows, and break-glass notes
- Email reports and report delivery history

Portal actions must respect GitOps. Mutating actions should create or trigger auditable repository-backed workflows rather than making untracked server changes.

## Email Reporting

Constant email reporting is part of the platform plan. Reports should be useful, concise, and actionable.

Planned report categories:

- Daily platform health summary
- Deploy and rollback summary
- Backup and restore-test status
- Security scanner summary
- Capacity and performance trend summary
- Incident and alert digest
- Weekly maintenance summary

Reports must not contain secrets. Sensitive values should be redacted or summarized.

## Security Boundaries

Do not commit secrets, private keys, Terraform state, `.tfvars`, local environment files, production credentials, or SSH material.

Pull request checks must use least-privilege permissions and must not require production secrets. Production apply workflows, when introduced, must be gated, auditable, and limited to trusted branches or protected environments.

Manual SSH is break-glass only. Any manual session must result in a runbook note or incident note describing what changed, why it changed, how it was verified, and how the repository was reconciled.

## Lightweight Runtime

The platform should stay simple enough for a solo-maintained, low-cost VPS.

Avoid Kubernetes, service meshes, heavyweight self-hosted observability stacks, broad custom control planes, and large databases unless explicitly approved. Prefer simple services, Docker Compose, host-level automation, managed external services where appropriate, and clear runbooks.

## Home Server Support Node

The home server is an optional NutsNews support node. The VPS remains the primary production host, and the public website must remain online without the home server.

Allowed support-node uses:

- Encrypted offsite backups
- Restore testing
- Private monitoring
- Scheduled reporting
- Local AI or background jobs
- Non-public maintenance and analysis tasks

Connectivity rules:

- Prefer private networking, outbound-only tunnels, or provider-supported private links.
- Do not expose broad inbound ports.
- Do not require the home server in the production request path.
- Do not store unencrypted production secrets on the home server.
- Self-hosted GitHub Actions runners on the home server may run only trusted workflows unless explicitly approved later.

## External Systems

Supabase, Cloudflare Worker/KV, Sentry, and Better Stack remain managed outside this repository. This repository may document integrations and manage VPS-side configuration, but it must not become the primary source of truth for those external resources unless explicitly changed later.

## Current Scope

This charter is planning and governance. It does not add real VPS provisioning, SSH deploys, production secrets, destructive automation, or runtime services.
