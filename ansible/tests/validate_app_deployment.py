#!/usr/bin/env python3
"""Validate the repo-managed, disabled-by-default app release manifest."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HOST_VARS = ROOT / "inventories/production/host_vars/vps.nutsnews.com.yml"


def value(name: str) -> str:
    prefix = f"{name}:"
    for line in HOST_VARS.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"\'')
    return ""


assert HOST_VARS.is_file(), f"Missing reviewed app release manifest: {HOST_VARS}"
assert value("vps_service_foundation_nutsnews_app_enabled") == "false"
assert value("vps_service_foundation_nutsnews_app_staged_route_enabled") == "false"
assert value("vps_service_foundation_nutsnews_app_public_route_enabled") == "false"
assert value("vps_service_foundation_nutsnews_app_image_repo") == "ghcr.io/ramideltoro/nutsnews"
assert value("vps_service_foundation_nutsnews_app_image_digest") == ""
assert value("vps_service_foundation_nutsnews_app_source_commit") == ""
assert value("vps_service_foundation_nutsnews_app_last_known_good_digest") == ""

for name in (
    "vps_service_foundation_nutsnews_app_image_digest",
    "vps_service_foundation_nutsnews_app_last_known_good_digest",
):
    candidate = value(name)
    assert not candidate or re.fullmatch(r"sha256:[0-9a-f]{64}", candidate), f"Invalid {name}"

assert "latest" not in HOST_VARS.read_text(encoding="utf-8").lower()
print("Reviewed app release manifest guardrails passed.")
