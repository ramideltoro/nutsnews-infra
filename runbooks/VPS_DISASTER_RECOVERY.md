# VPS Disaster Recovery

Use this when rebuilding NutsNews on another VPS provider.

Long-form disaster recovery notes live in `ramideltoro/nutsnews-docs` as `NUTSNEWS_VPS_DISASTER_RECOVERY.md`.

## Provider-Agnostic Rebuild Flow

1. Provision a replacement Ubuntu VPS.
2. Point DNS only after validation, not before.
3. Restore SSH access for `nutsnews_ops`.
4. Add or update `production-vps` Environment secrets if host keys or IPs changed.
5. Run `Protected Ansible Apply` in check mode.
6. Run `Protected Ansible Apply` in apply mode.
7. Restore encrypted restic backup data to staging.
8. Copy required `/opt/nutsnews` and `/etc/nutsnews` content into place.
9. Run `Run VPS Backup` against the new host.
10. Run `Verify VPS Backup`.
11. Check the Ops Portal through the SSH tunnel.
12. Cut over DNS.
13. Keep the old VPS or snapshot available until rollback risk is low.

## Minimum Verification

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/data/status.json
systemctl status nutsnews-restic-backup.timer --no-pager
systemctl status nutsnews-ops-portal-collector.timer --no-pager
```

## Rollback

If validation fails before DNS cutover, keep traffic on the old VPS. If validation fails after cutover, revert DNS to the old VPS and document what broke before trying again.

Manual SSH is break-glass only. Any manual fix must be reconciled back into this repo afterward.
