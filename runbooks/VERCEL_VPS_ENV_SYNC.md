# Vercel-to-VPS environment synchronization

The protected `Protected Ansible Apply` workflow can perform a one-way sync from
Vercel Production to the VPS production app. The workflow defaults to this sync
operation, but it still defaults to Ansible check mode. VPS-only values remain in
the existing protected `NUTSNEWS_APP_ENVS_JSON` input and are not removed by the
sync.

The reviewed allowlist and classification rules live in
`config/vercel-vps-env-sync.json`. The sync fails closed when a Vercel Production
variable is not classified, is marked for manual review, or cannot be decrypted.
Vercel system variables, deployment metadata, and preview/development values are
excluded. The sync reports only variable names and SHA-256 fingerprints; values
are never printed.

Use the companion operating guide in `ramideltoro/nutsnews-docs` for credential
setup, classification policy, dry-run/apply commands, rollback, rotation, and
removal procedures.

## Protected workflow inputs

- `sync_vercel_production=true` selects Vercel Production and the VPS production
  env file. Do not use preview or development values.
- `run_mode=check` performs the read-only Ansible preview and is the required
  first step.
- `run_mode=apply` is production-changing and requires the existing
  `production-vps` Environment approval plus the exact confirmation string.

The workflow is serialized with the existing production VPS concurrency group.
It reads the VPS env file only through a read-only SSH command that emits names
and hashes. Ansible remains the only path that writes the VPS file.
