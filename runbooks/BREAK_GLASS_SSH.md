# Break-Glass SSH Notes

Manual SSH is for break-glass recovery only.

## When It Is Allowed

- GitOps automation cannot reach the VPS.
- Public service recovery needs immediate manual action.
- SSH access itself is broken and must be restored.
- Provider console access is required to recover the host.

## Rules

- Use the narrowest access path available.
- Do not copy secrets into this repository.
- Do not make unrelated changes while connected.
- Record every command that changes system state.
- Reconcile the final state back into this repository through a PR.
- Update `ramideltoro/nutsnews-docs` with the lesson learned.

## Minimum Incident Note

Record:

- Date and time
- Operator
- Why break-glass was needed
- Access path used
- Commands or files changed
- Verification performed
- Follow-up PR link
- Docs update link or commit SHA

## Recovery Priority

1. Restore safe access.
2. Restore service health.
3. Preserve logs and evidence.
4. Reconcile repo state.
5. Document what happened.
