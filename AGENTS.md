# Agent Instructions

## Before Editing

- Read this file and the relevant README, docs, or runbook before making changes.
- Inspect the current git status before editing.
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

## Validation

- Infrastructure changes require validation before PR review.
- Terraform changes must be formatted and validated.
- Ansible changes must be syntax checked or otherwise validated.
- Compose changes must be configuration checked.
- Portal changes must pass the relevant build, lint, and test commands once they exist.

## Documentation

- Add or update docs and runbooks for operations, deployment, infrastructure, or security changes.
- Security-sensitive changes must explain the operational impact and rollback path.
- Deployment changes must document how they are applied and verified.
