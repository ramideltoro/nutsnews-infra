#!/usr/bin/env python3
"""Validate the repo-managed, disabled-by-default app release manifest."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HOST_VARS = ROOT / "inventories/production/host_vars/vps.nutsnews.com.yml"
DEFAULTS = ROOT / "roles/vps_service_foundation/defaults/main.yml"
TASKS = ROOT / "roles/vps_service_foundation/tasks/main.yml"
STAGED_ROUTE = ROOT / "roles/vps_service_foundation/templates/nutsnews-app.routes.j2"
PUBLIC_ROUTE = ROOT / "roles/vps_service_foundation/templates/nutsnews-app.public.routes.j2"
COLLECTOR = ROOT / "roles/vps_service_foundation/files/ops_portal_collector.py"
APP_COMPOSE = REPO / "compose/nutsnews/compose.yml"
CADDYFILE = REPO / "compose/caddy/Caddyfile"
PROTECTED_APPLY = REPO / ".github/workflows/protected-ansible-apply.yml"


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
assert re.fullmatch(
    r"sha256:[0-9a-f]{64}",
    value("vps_service_foundation_nutsnews_app_image_digest"),
)
assert re.fullmatch(r"[0-9a-f]{40}", value("vps_service_foundation_nutsnews_app_source_commit"))
assert value("vps_service_foundation_nutsnews_app_build_id")
assert value("vps_service_foundation_nutsnews_app_deployment_target") == "production-vps"
assert value("vps_service_foundation_nutsnews_app_last_known_good_digest") == ""

for name in (
    "vps_service_foundation_nutsnews_app_image_digest",
    "vps_service_foundation_nutsnews_app_last_known_good_digest",
):
    candidate = value(name)
    assert not candidate or re.fullmatch(r"sha256:[0-9a-f]{64}", candidate), f"Invalid {name}"

assert "latest" not in HOST_VARS.read_text(encoding="utf-8").lower()

defaults = DEFAULTS.read_text(encoding="utf-8")
tasks = TASKS.read_text(encoding="utf-8")
app_compose = APP_COMPOSE.read_text(encoding="utf-8")
staged_route = STAGED_ROUTE.read_text(encoding="utf-8")
public_route = PUBLIC_ROUTE.read_text(encoding="utf-8")
caddyfile = CADDYFILE.read_text(encoding="utf-8")
protected_apply = PROTECTED_APPLY.read_text(encoding="utf-8")
collector = COLLECTOR.read_text(encoding="utf-8")

assert ":latest" not in "\n".join((defaults, app_compose, protected_apply)).lower()
assert "NUTSNEWS_APP_IMAGE_TAG" not in protected_apply
assert "vps_service_foundation_nutsnews_app_image_tag" not in defaults
assert "${NUTSNEWS_APP_IMAGE:?" in app_compose
assert "@sha256" in app_compose
assert "- node" in app_compose
assert "exit 0" not in app_compose
assert "external: true" in app_compose
assert "ports:" not in app_compose

assert "nutsnews_app_staged_route_enabled" in staged_route
assert "uri strip_prefix" in staged_route
assert "handle_path" not in staged_route
assert "route_path }}*" not in staged_route
assert "nutsnews_app_public_route_enabled" in public_route
assert "header_up Host" in public_route
assert "header_up X-Forwarded-Proto" in public_route
assert "flush_interval -1" in public_route
assert "header_down" not in public_route

public_site = caddyfile.split("vps.nutsnews.com {", 1)[1].split("ops.nutsnews.com {", 1)[0]
assert "handle /health" in public_site
assert "import /etc/nutsnews/caddy/app.public.routes" in public_site
assert public_site.index("import /etc/nutsnews/caddy/app.public.routes") < public_site.rindex("handle {")
assert "Content-Security-Policy" not in public_site.split("handle /health", 1)[0]

assert "^sha256:[0-9a-f]{64}$" in tasks
assert "True\\|healthy" in tasks
assert "True\\|none" not in tasks
assert "nutsnews_app_public_route_enabled" in tasks
assert "nutsnews_app_staged_route_enabled" in tasks

app_secret_references = set(re.findall(r"secrets\.(NUTSNEWS_APP_[A-Z0-9_]+)", protected_apply))
assert app_secret_references == {"NUTSNEWS_APP_ENVS_JSON"}

for field in (
    '"running_repo_digest"',
    '"source_commit"',
    '"build_id"',
    '"last_deployment_result"',
    '"last_known_good_digest"',
):
    assert field in collector, f"Missing sanitized app status field: {field}"
assert 'run(["docker", "image", "inspect"' in collector

print("Reviewed immutable app deployment guardrails passed.")
