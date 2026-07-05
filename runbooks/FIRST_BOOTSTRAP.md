# First Bootstrap Runbook

Use this only after the Ansible bootstrap PR is merged and the VPS bootstrap is explicitly approved.

## Preconditions

- VPS hostname: `vps.nutsnews.com`
- IPv4: `65.75.202.112`
- IPv6: `2606:cc0:11:23ae::1`
- OS: Ubuntu 26.04 LTS
- Specs: 4 vCPU, 10240 MiB RAM, 80 GiB disk
- You have provider console access or another break-glass path.
- You have an approved public SSH key for `nutsnews_ops`.
- No private keys, passwords, tokens, or root credentials are committed.

## Validate Locally

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --syntax-check
ansible-lint ansible
```

## Dry Run

Do not run this from CI yet. From a trusted operator machine:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml --check --diff \
  --extra-vars '{"vps_baseline_admin_authorized_keys":["ssh-ed25519 AAAA... operator@example"]}'
```

Review the output before applying.

## Apply

Run only after the dry run looks correct:

```bash
cd ansible
ansible-playbook playbooks/bootstrap.yml \
  --extra-vars '{"vps_baseline_admin_authorized_keys":["ssh-ed25519 AAAA... operator@example"]}'
```

## Verify

- Open a second terminal before closing the first SSH session.
- Confirm `nutsnews_ops` can connect with the supplied key.
- Confirm password login is disabled only after key login works.
- Confirm UFW is enabled and allows SSH, HTTP, and HTTPS.
- Confirm fail2ban is running.
- Confirm unattended upgrades are configured.
- Confirm journald is persistent.
- Confirm `ansible/facts/vps.nutsnews.com.json` was generated locally and is not committed.

## Afterward

- Commit any required reconciliation changes through a PR.
- Update `ramideltoro/nutsnews-docs` if the run revealed new facts or recovery notes.
