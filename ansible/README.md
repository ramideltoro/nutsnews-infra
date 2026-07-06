# Ansible

Ansible inventory, playbooks, roles, and host configuration automation live here.

This directory contains the first bootstrap baseline for `vps.nutsnews.com`. It does not include secrets, private keys, passwords, or root credentials.

## Layout

- `inventories/production/hosts.yml`: production inventory for the primary VPS
- `inventories/production/group_vars/nutsnews_vps.yml`: non-secret host defaults
- `playbooks/bootstrap.yml`: baseline bootstrap entry point
- `roles/vps_baseline/`: lightweight Ubuntu baseline role
- `roles/vps_service_foundation/`: Docker, Compose, `/opt/nutsnews`, local Caddy, Ops Portal, email reporting, and restic/rclone VPS backups
- `facts/`: ignored local output directory for generated server fact snapshots

## Validation

Install required Ansible collections:

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
```

Run syntax checks without connecting to the VPS:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --syntax-check
```

Run linting:

```bash
cd ansible
ansible-lint .
```

Do not run the playbook against the VPS outside an approved local check or the protected manual workflow.

## Dry Run

After approval and once a public key is supplied outside the repo, use check mode first:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff \
  --extra-vars '{"vps_baseline_admin_authorized_keys":["ssh-ed25519 AAAA... operator@example"]}'
```

Then run the real bootstrap only after the check-mode output is reviewed.

The service foundation role installs Docker Engine and Compose, creates the `/opt/nutsnews` layout, copies the Caddy Compose bundle, and starts the local-only placeholder service during real apply mode. It skips Docker Compose mutation in Ansible check mode.

The same role installs restic and rclone, writes root-only backup config under `/etc/nutsnews`, installs `nutsnews-restic-backup.service`, `nutsnews-restic-backup.timer`, and `nutsnews-restic-verify.service`, and enables the timer only when backup secrets are supplied through the protected `production-vps` Environment.

## Protected Manual Workflow

GitHub Actions can run the baseline through the `production-vps` Environment using `.github/workflows/protected-ansible-apply.yml`.

- The workflow is `workflow_dispatch` only.
- The default run mode is `check`.
- Real apply mode requires Environment approval and the `confirm_apply` input.
- The workflow connects as `nutsnews_ops`, never root.
- Required secrets are documented in [../runbooks/PROTECTED_ANSIBLE_APPLY.md](../runbooks/PROTECTED_ANSIBLE_APPLY.md).

## Lockout Safety

The playbook fails before making changes unless `vps_baseline_admin_authorized_keys` contains at least one public SSH key. UFW allows SSH before enabling the firewall, SSH stays on port `22`, and the SSH daemon is syntax-checked before reload through the handler.
