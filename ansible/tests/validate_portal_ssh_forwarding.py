#!/usr/bin/env python3
"""Validate the SSH portal forwarding policy and effective OpenSSH config."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_TEMPLATE = REPO_ROOT / "ansible/roles/vps_baseline/templates/sshd_nutsnews.conf.j2"
FORWARDING_TEMPLATE = REPO_ROOT / "ansible/roles/vps_baseline/templates/sshd_portal_forwarding.conf.j2"

ADMIN_USER = "nutsnews_ops"
PORTAL_TARGETS = "127.0.0.1:8080 localhost:8080"


def find_executable(name: str, extra_paths: list[str] | None = None) -> str:
    found = shutil.which(name)
    if found:
        return found

    for path in extra_paths or []:
        if Path(path).exists():
            return path

    raise SystemExit(f"{name} is required for SSH forwarding validation.")


def require_line_absent(pattern: str, text: str, description: str) -> None:
    if re.search(pattern, text, flags=re.MULTILINE):
        raise SystemExit(f"Unexpected {description}: {pattern}")


def require_contains(needle: str, text: str, description: str) -> None:
    if needle not in text:
        raise SystemExit(f"Missing {description}: {needle}")


def render_template(text: str, host_key: Path) -> str:
    replacements = {
        "{{ vps_baseline_ssh_port }}": "22",
        "{{ vps_baseline_ssh_password_authentication }}": "no",
        "{{ vps_baseline_ssh_kbd_interactive_authentication }}": "no",
        "{{ vps_baseline_ssh_permit_root_login }}": "prohibit-password",
        "{{ vps_baseline_ssh_client_alive_interval }}": "300",
        "{{ vps_baseline_ssh_client_alive_count_max }}": "2",
        "{{ vps_baseline_ssh_portal_forward_user }}": ADMIN_USER,
        "{{ vps_baseline_ssh_portal_permit_open | join(' ') }}": PORTAL_TARGETS,
    }

    rendered = text
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)

    return f"HostKey {host_key}\n{rendered}"


def parse_effective_config(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if " " not in line:
            continue
        key, value = line.split(" ", 1)
        values[key] = value
    return values


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def validate_static_policy(baseline: str, forwarding: str) -> None:
    require_line_absent(r"^\s*AllowTcpForwarding\s+no\s*$", baseline, "global TCP forwarding deny")
    require_line_absent(r"^\s*PermitOpen\s+none\s*$", baseline, "global PermitOpen deny")
    require_line_absent(r"^\s*AllowTcpForwarding\s+yes\s*$", baseline + "\n" + forwarding, "broad TCP forwarding")

    require_contains("GatewayPorts no", baseline, "GatewayPorts hardening")
    require_contains("AllowStreamLocalForwarding no", baseline, "stream-local forwarding hardening")
    require_contains("PermitTunnel no", baseline, "tunnel device hardening")

    admin_match = f"Match User {ADMIN_USER}"
    all_match = "Match all"
    require_contains("Match User {{ vps_baseline_ssh_portal_forward_user }}", forwarding, "admin Match block")
    require_contains("AllowTcpForwarding local", forwarding, "admin local forwarding")
    require_contains("PermitOpen {{ vps_baseline_ssh_portal_permit_open | join(' ') }}", forwarding, "portal PermitOpen")
    require_contains(all_match, forwarding, "fallback Match all block")
    require_contains("PermitOpen none", forwarding, "fallback PermitOpen deny")

    rendered_forwarding = render_template(forwarding, Path("/tmp/unused-host-key"))
    if rendered_forwarding.index(admin_match) > rendered_forwarding.index(all_match):
        raise SystemExit("The admin Match block must appear before Match all.")


def validate_effective_policy(sshd: str, ssh_keygen: str, baseline: str, forwarding: str) -> None:
    with tempfile.TemporaryDirectory(prefix="nutsnews-sshd-") as tmpdir:
        tmp = Path(tmpdir)
        host_key = tmp / "host_key"
        run([ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)])

        config = tmp / "sshd_config"
        config.write_text(
            render_template(baseline + "\n" + forwarding, host_key),
            encoding="utf-8",
        )

        run([sshd, "-t", "-f", str(config)])

        admin = parse_effective_config(
            run(
                [
                    sshd,
                    "-T",
                    "-f",
                    str(config),
                    "-C",
                    f"user={ADMIN_USER},host=vps.nutsnews.com,addr=127.0.0.1",
                ]
            ).stdout
        )
        other = parse_effective_config(
            run(
                [
                    sshd,
                    "-T",
                    "-f",
                    str(config),
                    "-C",
                    "user=not_nutsnews_ops,host=vps.nutsnews.com,addr=127.0.0.1",
                ]
            ).stdout
        )

    expected_admin = {
        "allowtcpforwarding": "local",
        "permitopen": PORTAL_TARGETS,
        "gatewayports": "no",
        "allowstreamlocalforwarding": "no",
        "permittunnel": "no",
    }
    expected_other = {
        "allowtcpforwarding": "no",
        "permitopen": "none",
        "gatewayports": "no",
        "allowstreamlocalforwarding": "no",
        "permittunnel": "no",
    }

    for key, expected in expected_admin.items():
        actual = admin.get(key)
        if actual != expected:
            raise SystemExit(f"{ADMIN_USER} {key} expected {expected!r}, got {actual!r}")

    for key, expected in expected_other.items():
        actual = other.get(key)
        if actual != expected:
            raise SystemExit(f"fallback user {key} expected {expected!r}, got {actual!r}")


def main() -> None:
    baseline = BASELINE_TEMPLATE.read_text(encoding="utf-8")
    forwarding = FORWARDING_TEMPLATE.read_text(encoding="utf-8")
    validate_static_policy(baseline, forwarding)

    sshd = find_executable("sshd", ["/usr/sbin/sshd", "/usr/local/sbin/sshd"])
    ssh_keygen = find_executable("ssh-keygen", ["/usr/bin/ssh-keygen", "/usr/local/bin/ssh-keygen"])
    validate_effective_policy(sshd, ssh_keygen, baseline, forwarding)


if __name__ == "__main__":
    main()
