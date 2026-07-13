# Runbooks

Operational runbooks will live here.

Add runbooks for deployment, rollback, incident response, security changes, backup and restore, and recurring maintenance as those workflows are introduced.

## Current Runbooks

- [First Bootstrap](FIRST_BOOTSTRAP.md)
- [Break-Glass SSH Notes](BREAK_GLASS_SSH.md)
- [Bootstrap Rollback And Recovery Notes](BOOTSTRAP_ROLLBACK_RECOVERY.md)
- [Protected Ansible Apply](PROTECTED_ANSIBLE_APPLY.md)
- [VPS Service Foundation](VPS_SERVICE_FOUNDATION.md)
- [NutsNews Runtime Environments](NUTSNEWS_RUNTIME_ENVIRONMENTS.md)
- [NutsNews Staging Deployment](NUTSNEWS_STAGING_DEPLOY.md)
- [Operations Portal](OPS_PORTAL.md)
- [VPS Backup Setup](VPS_BACKUP_SETUP.md)
- [VPS Restore](VPS_RESTORE.md)
- [VPS Disaster Recovery](VPS_DISASTER_RECOVERY.md)
- [Cloudflare DDNS](CLOUDFLARE_DDNS.md)
- [Grafana Cloud Observability](GRAFANA_CLOUD_OBSERVABILITY.md)
- [Vercel-to-VPS environment synchronization](VERCEL_VPS_ENV_SYNC.md)

The `Send VPS Health Report` GitHub Actions workflow manually triggers the existing VPS report email without an interactive SSH session.
