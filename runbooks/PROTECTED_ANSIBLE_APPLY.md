# Protected Ansible Apply Runbook

Use this runbook for the manual GitHub Actions workflow that applies the Ansible baseline through the protected `production-vps` Environment.

The workflow is manual-only for now. It does not run on merge, does not use root SSH, and does not store secrets in the repository.

Production app releases normally enter this workflow through
`nutsnews-release-promotion.yml` after a successful staging qualification. The
promotion workflow verifies the signed staging qualification, confirms the
candidate is still the current successful staging deployment, waits for Vercel
Production to expose the same source commit, verifies the production Supabase
schema contract, creates or reuses the GitOps manifest PR, waits for checks,
merges it, and then dispatches this protected apply with the complete release
identity bundle. The old direct `nutsnews-production-release` dispatch is not a
valid entry point.

The promotion workflow locates the Vercel production workflow by the current or
legacy Vercel production run name for the dispatched source commit, workflow
file, event, branch, and dispatch timestamp. GitHub may record
repository-dispatch `headSha` as the app repository's current default-branch
commit rather than the dispatched release commit, so `headSha` is not used as
the run identity. A missing Vercel run is treated as an orchestration failure,
not proof of a bad deploy. Automated rollback is allowed only after the located
Vercel production run is rechecked and confirmed completed with a non-success
conclusion for the same source commit run name.

Before any job can enter the `production-vps` Environment, the workflow runs a
no-secret production eligibility check. Baseline-only changes may proceed when
the reviewed production release identity is unchanged. Any production app
release change must match a current, unexpired staging qualification
attestation for the exact image digest, source commit, build ID, source
workflow run, staging deployment ID, infra config generation, and test-suite
revision.

## Add Required Environment Secrets

Add these secrets to the existing `production-vps` GitHub Environment:

1. Open the `ramideltoro/nutsnews-infra` repository on GitHub.
2. Go to Settings.
3. Go to Environments.
4. Select `production-vps`.
5. Add each value under Environment secrets.
6. Keep the Environment protection rules enabled.

- `NUTSNEWS_VPS_SSH_PRIVATE_KEY`: private key allowed to SSH as `nutsnews_ops`
- `NUTSNEWS_VPS_KNOWN_HOSTS`: verified `known_hosts` entry for `65.75.202.112`
- `NUTSNEWS_VPS_ADMIN_AUTHORIZED_KEYS_JSON`: JSON array of approved public keys for `nutsnews_ops`
- `NUTSNEWS_CLOUDFLARE_DDNS_API_TOKEN`: optional Cloudflare token for `vps.nutsnews.com` DDNS, required only when `enable_cloudflare_ddns` is `true`
- `NUTSNEWS_GRAFANA_CLOUD_METRICS_URL`: optional Grafana Cloud Prometheus remote write endpoint, required only when `enable_grafana_alloy` is `true`
- `NUTSNEWS_GRAFANA_CLOUD_METRICS_USERNAME`: optional Grafana Cloud Prometheus username, required only when `enable_grafana_alloy` is `true`
- `NUTSNEWS_GRAFANA_CLOUD_LOGS_URL`: optional Grafana Cloud Loki push endpoint, required only when `enable_grafana_alloy` is `true`
- `NUTSNEWS_GRAFANA_CLOUD_LOGS_USERNAME`: optional Grafana Cloud Loki username, required only when `enable_grafana_alloy` is `true`
- `NUTSNEWS_GRAFANA_CLOUD_ACCESS_POLICY_TOKEN`: optional Grafana Cloud Access Policy token for telemetry writes, required only when `enable_grafana_alloy` is `true`

Example shape for `NUTSNEWS_VPS_ADMIN_AUTHORIZED_KEYS_JSON`:

```json
["ssh-ed25519 AAAA... operator@example"]
```

Do not commit any of these values. Verify host keys before adding `NUTSNEWS_VPS_KNOWN_HOSTS`; do not blindly trust a fresh network scan if something looks wrong.

## Add Optional Backup Environment Secrets

Add these to the same `production-vps` Environment before enabling encrypted VPS backups:

| Secret | Purpose |
| --- | --- |
| `NUTSNEWS_BACKUP_ENABLED` | Set to `true` to enable the restic backup timer |
| `NUTSNEWS_BACKUP_RESTIC_PASSWORD` | Restic repository password; keep a separate offline copy for disaster recovery |
| `NUTSNEWS_BACKUP_RCLONE_CONFIG` | Complete rclone config for the dedicated `nutsnews-onedrive` OneDrive remote |

Optional backup tuning secrets:

| Secret | Default |
| --- | --- |
| `NUTSNEWS_BACKUP_REPOSITORY` | `rclone:nutsnews-onedrive:nutsnews-backups/vps` |
| `NUTSNEWS_BACKUP_STALE_AFTER_HOURS` | `30` |
| `NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS` | `192` |
| `NUTSNEWS_BACKUP_CHECK_READ_DATA_SUBSET` | `5%` |
| `NUTSNEWS_BACKUP_KEEP_DAILY` | `14` |
| `NUTSNEWS_BACKUP_KEEP_WEEKLY` | `8` |
| `NUTSNEWS_BACKUP_KEEP_MONTHLY` | `12` |
| `NUTSNEWS_BACKUP_KEEP_YEARLY` | `2` |

The protected workflow rejects enabled backups unless the restic password and rclone config are present. It also rejects backup repositories that do not use the dedicated `nutsnews-onedrive` rclone remote. When backups are enabled, Ansible enables both the backup timer and the weekly latest-snapshot verification timer.

## Run Check Mode

1. Open GitHub Actions.
2. Select `Protected Ansible Apply`.
3. Select `Run workflow`.
4. Leave `run_mode` as `check`.
5. Set `enable_cloudflare_ddns` to `false` unless you are intentionally testing [Cloudflare DDNS](CLOUDFLARE_DDNS.md).
6. Set `enable_grafana_alloy` to `true` only after Grafana Cloud write secrets are configured and [Grafana Cloud Observability](GRAFANA_CLOUD_OBSERVABILITY.md) has been reviewed.
7. Leave `confirm_apply` blank.
8. Approve the `production-vps` Environment gate if prompted.
9. Review the Ansible diff and recap.

Check mode is the default because surprise infrastructure changes are how simple systems become weekend projects with invoices.

For an app release rehearsal, include the complete release identity bundle:

- `release_source_commit`
- `release_image_digest`
- `release_build_id`
- `release_source_workflow_run_id`
- `release_migration_head`
- `release_schema_version`
- `release_supabase_project_ref`

Do not manually dispatch this workflow for a new app digest until
`nutsnews-release-promotion.yml` has created and merged the reviewed production
manifest. If the production Supabase schema contract is behind, run the
protected `production-supabase-migration.yml` workflow in `ramideltoro/nutsnews`
for the same source commit and migration head, then rerun promotion.

The no-secret verifier rejects missing, expired, tampered, stale, superseded,
or mismatched staging qualification evidence before SSH keys, production app
secrets, deploy secrets, or the `production-vps` Environment are available.
The gate rehearsal and bypass inventory are covered by
`ansible/tests/validate_gate_rehearsal.py`; see the canonical operator guide in
`ramideltoro/nutsnews-docs` for the full Simple/Intermediate/Expert flow.

## Run Apply Mode

1. Run check mode first and review the output.
2. Select `Protected Ansible Apply`.
3. Set `run_mode` to `apply`.
4. Set `confirm_apply` to `vps.nutsnews.com`.
5. Set `enable_cloudflare_ddns` to `true` only after the DNS record state has been reviewed and approved.
6. Set `enable_grafana_alloy` to `true` only after check mode validates the Alloy package, config, and telemetry inputs.
7. Approve the `production-vps` Environment gate.
8. Review the final `PLAY RECAP`.

Apply mode connects as `nutsnews_ops` with sudo. It must never use root SSH.

Apply mode uses the same eligibility gate as check mode. Do not approve apply
for a production app digest unless the verifier accepted the exact staging
qualification record for that digest and deployment.

After an app release apply, the workflow verifies Docker is running the exact
reviewed image, checks the public `/healthz` identity, checks out the exact app
source/test revision, and runs the safe production smoke surfaces against
`https://vps.nutsnews.com/`. The smoke covers health, readiness, runtime public
config, homepage, public API shape, a static asset, cache/security headers,
contact validation failure, and auth session reachability without printing
secrets or submitting a real contact message.

Automated production release, pre-merge production, and fixed rollback dispatches
also set `enable_staging_access=true`. That keeps the root-owned staging deploy
bundle on the VPS bound to the currently reviewed infra commit, so later staging
deployments are not rejected by the server-side fixed command as
`unreviewed_infra_commit`.

The public app `/healthz` route is a static identity check for the shared VPS
web image and should report the image build target, `vps`. Runtime readiness
and public config should report the production VPS runtime identity,
`production-vps`.

The Ops Portal reads only the reviewed manifest, sanitized Docker identity, and
last app apply marker for release-gate status. Treat `unknown`, `not configured`,
`failed`, `expired`, or `superseded` as not eligible for promotion.

## Run Fixed Rollback

Use `Protected NutsNews Rollback` only for a critical app or route failure after
the recorded production release is known bad.

1. Open GitHub Actions.
2. Select `Protected NutsNews Rollback`.
3. Set `failed_image_digest` to the current failed production digest.
4. Enter a sanitized operator reason.
5. Set `rollback_confirmation` to `rollback-recorded-last-known-good`.
6. Approve the `production-vps` Environment gate.
7. Let the workflow create and merge the rollback PR, then dispatch protected
   apply with the restored release identity.

The rollback workflow can select only the current manifest's recorded
last-known-good digest as found in reviewed manifest history. It does not
accept an arbitrary restored digest, SSH command, mutable tag, or database down
migration.

## Read The Output

The workflow prints the normal Ansible output and then repeats everything from `PLAY RECAP` onward.

- `failed=0` means Ansible did not report failed tasks.
- `unreachable=0` means SSH and privilege escalation worked.
- `changed=0` is ideal for a stable baseline.
- `changed=1` may be normal when only the local server facts snapshot changes.

Any non-zero Ansible exit code fails the workflow.

## If The Workflow Cannot Connect

1. Confirm the `production-vps` Environment secrets exist and were not pasted with broken line endings.
2. Confirm `NUTSNEWS_VPS_KNOWN_HOSTS` contains a verified entry for `65.75.202.112`.
3. Confirm the private key matches an authorized public key for `nutsnews_ops`.
4. Confirm the VPS allows SSH on port `22`.
5. Retry check mode before apply mode.
6. If `nutsnews_ops` access is broken, use provider console or root SSH only as break-glass recovery.

Root SSH was only for first bootstrap. From here on, root access is an emergency ladder: use it only when the stairs are on fire, then write down exactly what happened and reconcile the repo afterward.

## After Break-Glass

If manual recovery was required:

- Record the incident using [BREAK_GLASS_SSH.md](BREAK_GLASS_SSH.md).
- Reconcile configuration drift through a PR.
- Update `ramideltoro/nutsnews-docs` with the lesson learned.
- Run the protected workflow in check mode after recovery.
