#!/usr/bin/env python3
"""Validate Ansible-managed zram fallback and portal swap/OOM reporting."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(".")
DEFAULTS = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
GROUP_VARS = (ROOT / "ansible/inventories/production/group_vars/nutsnews_vps.yml").read_text(encoding="utf-8")
TASKS = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
ZRAM_CONFIG = (
    ROOT / "ansible/roles/vps_service_foundation/templates/zram-generator.conf.j2"
).read_text(encoding="utf-8")
COLLECTOR_UNIT = (
    ROOT / "ansible/roles/vps_service_foundation/templates/nutsnews-ops-portal-collector.service.j2"
).read_text(encoding="utf-8")
COLLECTOR_TEXT = (
    ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py"
).read_text(encoding="utf-8")
REPORTER = (ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_reporter.py").read_text(encoding="utf-8")
APP_JS = (ROOT / "portal/assets/app.js").read_text(encoding="utf-8")
STATUS = json.loads((ROOT / "portal/data/status.example.json").read_text(encoding="utf-8"))
CADDY_COMPOSE = (ROOT / "compose/caddy/compose.yml").read_text(encoding="utf-8")
APP_COMPOSE = (ROOT / "compose/nutsnews/compose.yml").read_text(encoding="utf-8")

sys.dont_write_bytecode = True
COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "ops_portal_collector_for_zram_validation",
    ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py",
)
if COLLECTOR_SPEC is None or COLLECTOR_SPEC.loader is None:
    raise SystemExit("Could not load collector module for zram validation.")
COLLECTOR_MODULE = importlib.util.module_from_spec(COLLECTOR_SPEC)
COLLECTOR_SPEC.loader.exec_module(COLLECTOR_MODULE)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "vps_service_foundation_zram_enabled: true",
    "systemd-zram-generator",
    "vps_service_foundation_zram_size_mib: 1536",
    "vps_service_foundation_zram_swappiness: 10",
    "vps_service_foundation_zram_notes:",
    "/etc/systemd/zram-generator.conf",
    "/etc/sysctl.d/90-nutsnews-zram.conf",
):
    require(token in DEFAULTS, f"zram defaults missing {token}.")

for token in (
    "vps_service_foundation_zram_enabled: true",
    "vps_service_foundation_zram_size_mib: 1536",
    "vps_service_foundation_zram_swappiness: 10",
):
    require(token in GROUP_VARS, f"production group vars missing {token}.")

for token in (
    "Manage zram fallback configuration",
    "Validate zram fallback settings",
    "Install zram generator configuration",
    "ansible.posix.sysctl",
    "vm.swappiness",
    "systemctl",
    "/dev/{{ vps_service_foundation_zram_device }}",
    "Validate zram fallback swap is active",
    "swapon",
    "Assert zram fallback runtime state",
    "(/dev/)?",
    "[ \\t]+[0-9]+[ \\t]+[0-9]+[ \\t]+(partition|zram)[ \\t]+",
    "vps_service_foundation_zram_swap_priority | string",
):
    require(token in TASKS, f"zram Ansible task flow missing {token}.")

require(
    "{{" not in TASKS[TASKS.index("Assert zram fallback runtime state") : TASKS.index("- name: Manage Docker service")],
    "zram runtime assertion must use plain Ansible expressions, not deprecated templating delimiters.",
)

for token in (
    "[{{ vps_service_foundation_zram_device }}]",
    "zram-size = {{ vps_service_foundation_zram_size_mib }}",
    "compression-algorithm = {{ vps_service_foundation_zram_compression_algorithm }}",
    "swap-priority = {{ vps_service_foundation_zram_swap_priority }}",
):
    require(token in ZRAM_CONFIG, f"zram generator template missing {token}.")

for token in (
    "NUTSNEWS_SWAP_USAGE_CACHE_FILE",
    "NUTSNEWS_SWAP_NON_TRIVIAL_BYTES",
    "NUTSNEWS_SWAP_WARNING_PERCENT",
    "NUTSNEWS_SWAP_CRITICAL_PERCENT",
    "NUTSNEWS_SWAP_SUSTAINED_SECONDS",
    "NUTSNEWS_OOM_EVIDENCE_WINDOW",
):
    require(token in COLLECTOR_UNIT, f"collector unit must pass {token}.")

for token in (
    "swap_state",
    "swap_usage_history",
    "oom_evidence_state",
    "out of memory|oom-killer|killed process",
    "Swap usage is sustained or non-trivial",
    "Recent kernel OOM evidence was found",
):
    require(token in COLLECTOR_TEXT, f"collector missing {token}.")

require("Kernel OOM evidence" in REPORTER, "reporter must include kernel OOM evidence.")
require("Swap State" in APP_JS and "Kernel OOM" in APP_JS, "portal UI must render swap state and OOM evidence.")
require("mem_limit: 128m" in CADDY_COMPOSE, "Caddy/Ops Auth memory limits must remain in place.")
require("mem_limit: 768m" in APP_COMPOSE, "NutsNews app memory limit must remain in place.")

swap_fixture = STATUS.get("resources", {}).get("swap", {})
require(swap_fixture.get("status") == "enabled", "fixture must show zram swap enabled.")
require(swap_fixture.get("total_bytes") == 1610612736, "fixture zram size must be 1.5 GiB.")
require(swap_fixture.get("usage_state") == "unused", "fixture normal swap state should be unused.")
require(swap_fixture.get("warning") is False, "fixture normal swap state should not warn.")
oom_fixture = STATUS.get("resources", {}).get("oom_evidence", {})
require(oom_fixture.get("status") == "clear", "fixture OOM evidence should be clear.")
require(oom_fixture.get("count") == 0, "fixture OOM evidence should show zero real matches.")

with tempfile.TemporaryDirectory() as tmpdir:
    COLLECTOR_MODULE.SWAP_USAGE_CACHE_FILE = Path(tmpdir) / "swap-usage-cache.json"
    total = 1536 * 1024 * 1024
    disabled = COLLECTOR_MODULE.swap_state({"SwapTotal": 0, "SwapFree": 0})
    require(disabled["status"] == "disabled", "zero swap must be explicit disabled state.")
    require(disabled["used_percent"] is None, "disabled swap must not report zero percent usage.")

    unused = COLLECTOR_MODULE.swap_state({"SwapTotal": total, "SwapFree": total})
    require(unused["status"] == "enabled", "configured swap must be enabled.")
    require(unused["usage_state"] == "unused", "unused swap should be explicit.")
    require(unused["warning"] is False, "unused swap must not warn.")

    non_trivial = COLLECTOR_MODULE.swap_state({"SwapTotal": total, "SwapFree": total - (128 * 1024 * 1024)})
    require(non_trivial["usage_state"] == "non_trivial", "128 MiB swap use should be non-trivial.")
    require(non_trivial["warning"] is True, "non-trivial swap use should warn.")

    alerts = COLLECTOR_MODULE.alert_state(
        {"disk": {}, "memory": {}, "swap": non_trivial, "oom_evidence": {"available": True, "count": 0}},
        {},
        [],
        {},
        {},
        {},
    )
    require(
        any(alert.get("level") == "warning" and "Swap usage is sustained or non-trivial" in alert.get("message", "") for alert in alerts),
        "non-trivial swap use must emit a warning alert.",
    )

    oom_alerts = COLLECTOR_MODULE.alert_state(
        {"disk": {}, "memory": {}, "swap": unused, "oom_evidence": {"available": True, "count": 1}},
        {},
        [],
        {},
        {},
        {},
    )
    require(
        any(alert.get("level") == "critical" and "kernel OOM" in alert.get("message", "") for alert in oom_alerts),
        "recent kernel OOM evidence must emit a critical alert.",
    )

print("zram fallback and swap/OOM portal guardrails passed.")
