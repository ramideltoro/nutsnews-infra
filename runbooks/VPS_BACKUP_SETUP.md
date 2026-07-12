# VPS Backup Setup

Use this after the VPS backup PR is merged and before enabling scheduled encrypted backups.

Long-form setup notes live in `ramideltoro/nutsnews-docs` as `NUTSNEWS_VPS_BACKUPS.md`.

## What This Adds

- `restic` encrypted backups
- `rclone` transport to the dedicated OneDrive remote `nutsnews-onedrive`
- Restic repository `rclone:nutsnews-onedrive:nutsnews-backups/vps`
- Root-only config under `/etc/nutsnews`
- Backup service and timer:
  - `nutsnews-restic-backup.service`
  - `nutsnews-restic-backup.timer`
- Verification service and timer:
  - `nutsnews-restic-verify.service`
  - `nutsnews-restic-verify.timer`
- Portal status at `/opt/nutsnews/portal-assets/data/backup-status.json`
- Manual GitHub Actions workflows:
  - `Run VPS Backup`
  - `Verify VPS Backup`

## Required `production-vps` Environment Secrets

Add these to `ramideltoro/nutsnews-infra` -> Settings -> Environments -> `production-vps` -> Environment secrets:

| Secret | Value |
| --- | --- |
| `NUTSNEWS_BACKUP_ENABLED` | `true` |
| `NUTSNEWS_BACKUP_RESTIC_PASSWORD` | A long unique restic repository password |
| `NUTSNEWS_BACKUP_RCLONE_CONFIG` | The complete rclone config for the `nutsnews-onedrive` remote |

Optional overrides:

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

Do not commit restic passwords, rclone configs, OAuth tokens, private keys, or credentials.

## Generate The rclone Config Safely

Do this on your machine, not in chat:

```bash
rclone config
```

Create a OneDrive remote named exactly:

```text
nutsnews-onedrive
```

Then inspect only the config locally:

```bash
rclone config file
rclone lsd nutsnews-onedrive:
```

Copy the full config text into the GitHub Environment secret `NUTSNEWS_BACKUP_RCLONE_CONFIG`. Do not paste it into issues, PRs, docs, terminal transcripts, or chat.

## Apply

1. Run `Protected Ansible Apply` in `check` mode.
2. Review the Ansible diff.
3. Run `Protected Ansible Apply` in `apply` mode with `confirm_apply=vps.nutsnews.com`.
4. Run `Run VPS Backup`.
5. Run `Verify VPS Backup`.
6. Confirm `nutsnews-restic-verify.timer` is enabled for the weekly scheduled check.
7. Open the Ops Portal and confirm the latest snapshot shows recent successful verification.

## Manual VPS Checks

```bash
systemctl list-timers nutsnews-restic-backup.timer nutsnews-restic-verify.timer
systemctl status nutsnews-restic-backup.service --no-pager
systemctl status nutsnews-restic-verify.service --no-pager
sudo journalctl -u nutsnews-restic-backup.service -n 120 --no-pager
sudo cat /opt/nutsnews/portal-assets/data/backup-status.json
```

The scheduled verify timer is conservative by default: weekly, randomized by several hours, and stale after 192 hours. It verifies the latest snapshot with the same lock-protected runner as the manual workflow, so backup and verification jobs do not run restic against each other.

Daily backups normally create a newer snapshot than the last weekly verification. The portal shows that mismatch as `latest_unverified` with `policy_status=pending` and a policy deadline; it is status information, not an immediate email condition. Alerting remains active for failed verification, verification beyond 192 hours, an inactive verify timer, failed backup/prune work, or a backup snapshot older than the 30-hour freshness threshold. Full restore drills remain separate under issue #24.

`Backup Local Cache` in the Free Tier section measures only local GiB against the VPS root filesystem. Snapshot age remains in the backup panel, and remote OneDrive capacity is reported as unmeasured unless a real read-only quota source is added.

## Rollback

Revert the infra PR, merge it, and run protected apply. If backup secrets were added, leave them in GitHub until restore confidence is no longer needed; deleting secrets does not delete existing encrypted OneDrive snapshots.
