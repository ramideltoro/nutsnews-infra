# Runbooks

Operational runbooks will live here.

Add runbooks for deployment, rollback, incident response, security changes, backup and restore, and recurring maintenance as those workflows are introduced.

## Current Runbooks

- [First Bootstrap](FIRST_BOOTSTRAP.md)
- [Break-Glass SSH Notes](BREAK_GLASS_SSH.md)
- [Bootstrap Rollback And Recovery Notes](BOOTSTRAP_ROLLBACK_RECOVERY.md)
- [Protected Ansible Apply](PROTECTED_ANSIBLE_APPLY.md)
- [VPS Service Foundation](VPS_SERVICE_FOUNDATION.md)
- [Operations Portal](OPS_PORTAL.md)
- [Cloudflare DDNS](CLOUDFLARE_DDNS.md)

The `Send VPS Health Report` GitHub Actions workflow manually triggers the existing VPS report email without an interactive SSH session.
