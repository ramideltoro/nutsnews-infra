# Protected Ansible Apply Runbook

Use this runbook for the manual GitHub Actions workflow that applies the Ansible baseline through the protected `production-vps` Environment.

The workflow is manual-only for now. It does not run on merge, does not use root SSH, and does not store secrets in the repository.

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

Example shape for `NUTSNEWS_VPS_ADMIN_AUTHORIZED_KEYS_JSON`:

```json
["ssh-ed25519 AAAA... operator@example"]
```

Do not commit any of these values. Verify host keys before adding `NUTSNEWS_VPS_KNOWN_HOSTS`; do not blindly trust a fresh network scan if something looks wrong.

## Run Check Mode

1. Open GitHub Actions.
2. Select `Protected Ansible Apply`.
3. Select `Run workflow`.
4. Leave `run_mode` as `check`.
5. Set `enable_cloudflare_ddns` to `false` unless you are intentionally testing [Cloudflare DDNS](CLOUDFLARE_DDNS.md).
6. Leave `confirm_apply` blank.
7. Approve the `production-vps` Environment gate if prompted.
8. Review the Ansible diff and recap.

Check mode is the default because surprise infrastructure changes are how simple systems become weekend projects with invoices.

## Run Apply Mode

1. Run check mode first and review the output.
2. Select `Protected Ansible Apply`.
3. Set `run_mode` to `apply`.
4. Set `confirm_apply` to `vps.nutsnews.com`.
5. Set `enable_cloudflare_ddns` to `true` only after the DNS record state has been reviewed and approved.
6. Approve the `production-vps` Environment gate.
7. Review the final `PLAY RECAP`.

Apply mode connects as `nutsnews_ops` with sudo. It must never use root SSH.

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
