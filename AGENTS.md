# Agent Instructions

## Before Editing

- Read this file and the relevant README, docs, or runbook before making changes.
- Inspect the current git status before editing.
- Show me the plan before you begin edits.
- Preserve user changes. Do not overwrite, delete, or revert work you did not make unless explicitly instructed.

## Repository Rules

- Do not add secrets to this repository.
- Do not commit Terraform state, `.tfvars`, private keys, tokens, credentials, or local environment files.
- All changes must go through commit, pull request, checks, merge, and pipeline apply.
- Do not make manual server changes after bootstrap.
- Do not prefix PR titles, branch names, commit messages, docs, headings, or generated content with any agent branding. Use normal human-readable project language.
- Do not add real VPS provisioning, SSH deploys, production secrets, destructive automation, or production credentials unless explicitly requested and reviewed.
- Keep the platform lightweight enough for a cheap solo-maintained VPS. Avoid Kubernetes and heavyweight self-hosted observability unless explicitly approved.
- Design all infrastructure, operations, services, scanners, reports, and portal changes for GitOps: commit, PR, CI scanners, merge, then automated apply.
- Treat the VPS as the primary production host. Stability, security, performance, resiliency, observability, recoverability, and constant email reporting are platform priorities.
- Keep the setup provider-agnostic. Avoid provider-specific coupling unless isolated, documented, and replaceable.
- Treat manual SSH as break-glass only. Any manual intervention must be documented afterward and reconciled back into this repository.
- Treat the Ops Portal as the central dashboard and control plane for VPS state, services, deploys, checks, alerts, backups, runbooks, and reports.
- The optional home server support node must never be required for the public website to stay online.
- Use private networking or tunnels for support-node connectivity. Do not expose broad inbound ports.
- Self-hosted GitHub Actions runners on the home server may run only trusted workflows unless explicitly approved later.
- Every change in this repository must be documented in `ramideltoro/nutsnews-docs`.
- Push the matching `ramideltoro/nutsnews-docs` documentation update directly to its `main` branch with no pull request unless GitHub blocks the push.
- Keep only short operational pointers in this repository. Learning, explanation, diagrams, recovery context, and operating guides belong in `ramideltoro/nutsnews-docs`.
- Documentation-only changes must never trigger app, Worker, VPS, or deployment workflows.

## Production VPS Verification and Troubleshooting

- For changes affecting VPS services, networking, Docker, Caddy, systemd, UFW, health endpoints, monitoring, security, or availability, SSH verification is REQUIRED before reporting success.
- SSH is authorized for read-only verification and troubleshooting: inspect sockets, routes, service/unit state, logs, Docker network/configuration, UFW, and health responses as needed.
- If runtime verification exposes a problem, troubleshoot it over SSH, but make the permanent fix only in this repository through Ansible/GitOps.
- MUST NOT make persistent direct host edits, weaken the firewall, expose secrets, or restart/change production services manually to bypass the GitOps path.
- After an approved Protected Ansible Apply, SSH verification is REQUIRED again and must prove the intended runtime state.

## Validation

- Infrastructure changes require validation before PR review.
- Terraform changes must be formatted and validated.
- Ansible changes must be syntax checked or otherwise validated.
- Compose changes must be configuration checked.
- Portal changes must pass the relevant build, lint, and test commands once they exist.

## Documentation

- Add or update `ramideltoro/nutsnews-docs` for every change in this repository.
- Add or update local docs and runbooks only as short operational pointers for operations, deployment, infrastructure, or security changes.
- Security-sensitive changes must explain the operational impact and rollback path.
- Deployment changes must document how they are applied and verified.
