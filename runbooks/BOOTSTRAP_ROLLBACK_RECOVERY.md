# Bootstrap Rollback And Recovery Notes

The bootstrap baseline is designed to avoid lockout, but recovery still needs a plan.

## If SSH Login Fails

1. Keep any existing working SSH session open.
2. Try a second SSH session as `nutsnews_ops`.
3. If the new user cannot log in, inspect `/etc/ssh/sshd_config.d/20-nutsnews-baseline.conf`.
4. Run `sudo sshd -t`.
5. If needed, move the drop-in aside and reload SSH:

```bash
sudo mv /etc/ssh/sshd_config.d/20-nutsnews-baseline.conf /root/20-nutsnews-baseline.conf.disabled
sudo systemctl reload ssh
```

## If UFW Blocks Access

Use an existing session or provider console:

```bash
sudo ufw status verbose
sudo ufw allow 22/tcp
sudo ufw reload
```

Disable UFW only if access is still blocked:

```bash
sudo ufw disable
```

## If fail2ban Blocks An Operator

```bash
sudo fail2ban-client status sshd
sudo fail2ban-client set sshd unbanip <operator-ip>
```

## Reconcile

Any manual recovery change must become a repo change or an explicit runbook note. The next automated apply should not have to guess what happened.
