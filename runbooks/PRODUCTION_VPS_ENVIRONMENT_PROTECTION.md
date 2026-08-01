# Production VPS Environment Protection

Use this checklist before the first protected rollout and whenever the
`production-vps` GitHub Environment policy changes. The repository audit is
read-only; it does not create or modify reviewers, branch policies, or secrets.

## Configure the GitHub Environment

In repository **Settings → Environments → production-vps**:

1. Configure at least one required reviewer, enable **Prevent self-review**, and
   save the protection rule.
2. Restrict deployment branches and tags to custom policies.
3. Remove wildcard, tag, protected-branch, and other branch entries. Keep
   exactly one policy: a **branch** policy (not a tag policy) named `main`.
4. Do not copy reviewer identities or IDs into workflow output, artifacts, or
   repository files.

## Verify Before Rollout

1. Merge the source checks to `main`.
2. From `main`, manually run **Production VPS environment policy audit**.
3. Require the audit to report that exact-main policy, a required reviewer, and
   self-review prevention are present before running any protected plan, apply,
   drill, backup, or operational workflow.
4. Run
   `PYTHONDONTWRITEBYTECODE=1 python3 ansible/tests/validate_production_vps_environment_policy.py`
   locally when changing workflows. This validator inventories every job that
   attaches `production-vps` and requires an exact-main job guard plus the
   unprotected audit prerequisite.

Every protected caller runs the same prerequisite without attaching an
Environment, so it cannot read Environment secrets. A failed or skipped audit
prevents the downstream job from requesting approval or receiving secrets.

## API Permission Caveat

The audit uses the run-scoped `GITHUB_TOKEN` with `actions: read` and performs
only authenticated `GET` requests for the Environment and its deployment branch
policies. GitHub documents Actions read access for these endpoints. If repository
or enterprise policy returns `403`, omits protection rules, or makes reviewer
configuration unavailable for the repository plan, the audit fails closed.
Correct the GitHub policy or plan limitation; do not bypass the prerequisite,
attach the Environment to it, or replace the token with a broad PAT.
