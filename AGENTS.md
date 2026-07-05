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
