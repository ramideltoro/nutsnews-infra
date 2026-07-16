# VPS Maintenance

Use the `Protected VPS Maintenance` workflow for routine package maintenance
and controlled reboots. Do not run `apt upgrade` or `systemctl reboot` over SSH
as routine maintenance.

## Workflow Modes

- `preflight`: reads system state, reboot-required state, package update counts,
  backup freshness, Docker container health, local Caddy health, Ops Portal auth
  redirect, and public `/health`.
- `package-maintenance`: requires `confirm_package_maintenance` set to
  `apply-package-maintenance`, then runs fixed `apt-get update` and
  `apt-get upgrade` commands after preflight passes.
- `reboot`: requires `confirm_reboot` set to `reboot-vps.nutsnews.com`, records
  the boot ID, reboots through systemd, waits for SSH, and runs post-reboot
  validation.
- `post-reboot`: validates SSH, systemd, Docker, Caddy, Ops Portal auth redirect,
  backup status, public `/health`, and absence of `/var/run/reboot-required`.

The workflow attaches to the `production-vps` GitHub Environment and uses only
the existing VPS SSH and known-hosts secrets. It does not accept arbitrary SSH
commands.

## Normal Procedure

1. Run `Protected VPS Maintenance` with `maintenance_mode=preflight`.
2. If package updates should be applied, run `maintenance_mode=package-maintenance`
   with `confirm_package_maintenance=apply-package-maintenance`.
3. If the workflow or Ops Portal reports `reboot_required=true`, run
   `maintenance_mode=reboot` with `confirm_reboot=reboot-vps.nutsnews.com`.
4. Review the workflow summary and the Ops Portal after the run.

## Rollback

Package rollback is not automated. If maintenance breaks production health,
use the existing GitOps rollback/app release path for application regressions,
or follow the VPS disaster recovery runbook for host-level failures. Do not
reverse database migrations as part of host package maintenance.
