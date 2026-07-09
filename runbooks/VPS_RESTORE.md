# VPS Restore

Use this when restoring files from encrypted VPS restic backups.

Long-form restore notes live in `ramideltoro/nutsnews-docs` as `NUTSNEWS_VPS_RESTORE.md`.

## Preconditions

- You have the restic repository password from `production-vps` Environment secrets or the offline password record.
- You have the rclone config for the `nutsnews-onedrive` remote.
- You are restoring onto a trusted host.
- You are not committing secrets or recovered private config files.

## Inspect Snapshots

On a trusted machine with restic and rclone:

```bash
export RCLONE_CONFIG=/path/to/rclone.conf
export RESTIC_REPOSITORY=rclone:nutsnews-onedrive:nutsnews-backups/vps
export RESTIC_PASSWORD_FILE=/path/to/restic-password
restic snapshots
restic ls latest
```

## Restore To A Staging Directory First

```bash
sudo install -m 0700 -d /tmp/nutsnews-restore
sudo -E restic restore latest --target /tmp/nutsnews-restore
```

Review before copying anything into place:

```bash
sudo find /tmp/nutsnews-restore -maxdepth 3 -type d | sort
sudo ls -la /tmp/nutsnews-restore/opt/nutsnews
sudo ls -la /tmp/nutsnews-restore/etc/nutsnews
```

## Restore Test Procedure

Backups without restore tests are just emotional support files.

The scheduled `nutsnews-restic-verify.timer` checks repository readability and the latest snapshot on the VPS. It is not a full restore drill; restore drills still require staging a restore on a trusted host and are tracked separately from routine verification.

1. Restore the latest snapshot to a staging directory.
2. Confirm `/opt/nutsnews/data` exists if app data has been created.
3. Confirm `/etc/nutsnews` exists and is readable only by root.
4. Confirm systemd unit files are present under `etc/systemd/system`.
5. Run `restic check --read-data-subset=5%`.
6. Document the test date, snapshot ID, and result in the docs repo or incident notes.

## Production Restore Shape

Prefer rebuilding the host with Ansible first, then restoring data:

1. Bootstrap or rebuild the VPS.
2. Run protected Ansible apply.
3. Stop affected services.
4. Copy restored `/opt/nutsnews` data into place.
5. Copy only needed `/etc/nutsnews` config files into place.
6. Run `systemctl daemon-reload`.
7. Start services.
8. Run health checks and the backup verification workflow.

Do not use restore as a way to bypass GitOps. If restored config differs from the repo, reconcile the repo.
