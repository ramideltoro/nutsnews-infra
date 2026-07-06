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
- Manual verification service:
  - `nutsnews-restic-verify.service`
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
6. Open the Ops Portal and confirm backups show fresh status.

## Manual VPS Checks

```bash
systemctl list-timers nutsnews-restic-backup.timer
systemctl status nutsnews-restic-backup.service --no-pager
systemctl status nutsnews-restic-verify.service --no-pager
sudo journalctl -u nutsnews-restic-backup.service -n 120 --no-pager
sudo cat /opt/nutsnews/portal-assets/data/backup-status.json
```

## Rollback

Revert the infra PR, merge it, and run protected apply. If backup secrets were added, leave them in GitHub until restore confidence is no longer needed; deleting secrets does not delete existing encrypted OneDrive snapshots.
