# Ansible

Ansible inventory, playbooks, roles, and host configuration automation live here.

This directory contains the first bootstrap baseline for `vps.nutsnews.com`. It does not include secrets, private keys, passwords, root credentials, or production apply workflows.

## Layout

- `inventories/production/hosts.yml`: production inventory for the primary VPS
- `inventories/production/group_vars/vps_baseline_vps.yml`: non-secret host defaults
- `playbooks/bootstrap.yml`: baseline bootstrap entry point
- `roles/vps_baseline/`: lightweight Ubuntu baseline role
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

Do not run the playbook against the VPS until bootstrap is intentionally approved.

## Dry Run

After approval and once a public key is supplied outside the repo, use check mode first:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff \
  --extra-vars '{"vps_baseline_admin_authorized_keys":["ssh-ed25519 AAAA... operator@example"]}'
```

Then run the real bootstrap only after the check-mode output is reviewed.

## Lockout Safety

The playbook fails before making changes unless `vps_baseline_admin_authorized_keys` contains at least one public SSH key. UFW allows SSH before enabling the firewall, SSH stays on port `22`, and the SSH daemon is syntax-checked before reload through the handler.
