#!/usr/bin/env python3
"""Validate the reviewed production release and isolated runtime model."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HOST_VARS = ROOT / "inventories/production/host_vars/vps.nutsnews.com.yml"
DEFAULTS = ROOT / "roles/vps_service_foundation/defaults/main.yml"
TASKS = ROOT / "roles/vps_service_foundation/tasks/main.yml"
ENV_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment.yml"
ENVIRONMENT_VALIDATION_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment_validate.yml"
PRODUCTION_RUNTIME_CONTRACT_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_production_runtime_contract.yml"
STATE_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment_apply_state.yml"
STAGED_ROUTE = ROOT / "roles/vps_service_foundation/templates/nutsnews-app.routes.j2"
PUBLIC_ROUTE = ROOT / "roles/vps_service_foundation/templates/nutsnews-app.public.routes.j2"
APP_ENV_TEMPLATE = ROOT / "roles/vps_service_foundation/templates/nutsnews-app.env.j2"
COLLECTOR = ROOT / "roles/vps_service_foundation/files/ops_portal_collector.py"
APP_COMPOSE = REPO / "compose/nutsnews/compose.yml"
CADDY_COMPOSE = REPO / "compose/caddy/compose.yml"
CADDYFILE = REPO / "compose/caddy/Caddyfile"
PROTECTED_APPLY = REPO / ".github/workflows/protected-ansible-apply.yml"


def value(name: str) -> str:
    prefix = f"{name}:"
    for line in HOST_VARS.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"\'')
    return ""


assert HOST_VARS.is_file(), f"Missing reviewed app release manifest: {HOST_VARS}"
assert value("vps_service_foundation_nutsnews_app_enabled") == "true"
assert value("vps_service_foundation_nutsnews_app_staged_route_enabled") == "true"
assert value("vps_service_foundation_nutsnews_app_public_route_enabled") == "true"
assert value("vps_service_foundation_nutsnews_app_image_repo") == "ghcr.io/ramideltoro/nutsnews"
assert value("vps_service_foundation_nutsnews_app_image_review_status") == "reviewed"
assert re.fullmatch(r"sha256:[0-9a-f]{64}", value("vps_service_foundation_nutsnews_app_image_digest"))
assert re.fullmatch(r"[0-9a-f]{40}", value("vps_service_foundation_nutsnews_app_source_commit"))
assert value("vps_service_foundation_nutsnews_app_build_id")
assert value("vps_service_foundation_nutsnews_app_deployment_target") == "production-vps"
assert re.fullmatch(r"production-[1-9][0-9]*-[1-9][0-9]*-[0-9]{14}", value("vps_service_foundation_nutsnews_app_config_generation"))
assert re.fullmatch(r"[0-9]{14}", value("vps_service_foundation_nutsnews_app_migration_head"))
assert re.fullmatch(r"[0-9]{14}", value("vps_service_foundation_nutsnews_app_schema_version"))

for name in (
    "vps_service_foundation_nutsnews_app_image_digest",
    "vps_service_foundation_nutsnews_app_last_known_good_digest",
):
    candidate = value(name)
    assert not candidate or re.fullmatch(r"sha256:[0-9a-f]{64}", candidate), f"Invalid {name}"

defaults = DEFAULTS.read_text(encoding="utf-8")
tasks = TASKS.read_text(encoding="utf-8")
environment_tasks = ENV_TASKS.read_text(encoding="utf-8")
environment_validation_tasks = ENVIRONMENT_VALIDATION_TASKS.read_text(encoding="utf-8")
production_runtime_contract_tasks = PRODUCTION_RUNTIME_CONTRACT_TASKS.read_text(encoding="utf-8")
state_tasks = STATE_TASKS.read_text(encoding="utf-8")
app_compose = APP_COMPOSE.read_text(encoding="utf-8")
caddy_compose = CADDY_COMPOSE.read_text(encoding="utf-8")
staged_route = STAGED_ROUTE.read_text(encoding="utf-8")
public_route = PUBLIC_ROUTE.read_text(encoding="utf-8")
app_env_template = APP_ENV_TEMPLATE.read_text(encoding="utf-8")
caddyfile = CADDYFILE.read_text(encoding="utf-8")
protected_apply = PROTECTED_APPLY.read_text(encoding="utf-8")
collector = COLLECTOR.read_text(encoding="utf-8")

assert ":latest" not in "\n".join((defaults, app_compose, protected_apply)).lower()
assert "no-new-privileges=true" in app_compose
assert "no-new-privileges:true" not in app_compose
assert "NUTSNEWS_APP_IMAGE_TAG" not in protected_apply
assert "RELEASE_IMAGE_DEPLOYMENT_TARGET" in protected_apply
assert "RELEASE_HEALTH_DEPLOYMENT_TARGET" in protected_apply
assert "payload?.deploymentTarget === healthDeploymentTarget" in protected_apply
assert "vps_service_foundation_nutsnews_environment_names:" in defaults
assert "  - production\n  - staging" in defaults
assert "vps_service_foundation_nutsnews_environments:" in defaults
assert "  production:" in defaults and "  staging:" in defaults
assert "enabled: false" in defaults
assert "network_name: nutsnews-edge-staging" in defaults
assert "cache_volume_name: nutsnews-app-staging-cache" in defaults
assert "vps_service_foundation_nutsnews_app_image_review_status" in defaults

for required in (
    "NUTSNEWS_APP_PROJECT_NAME",
    "NUTSNEWS_APP_NETWORK_NAME",
    "NUTSNEWS_APP_NETWORK_ALIAS",
    "NUTSNEWS_APP_CACHE_VOLUME_NAME",
    "NUTSNEWS_APP_CPU_LIMIT",
    "NUTSNEWS_APP_CPU_SHARES",
    "NUTSNEWS_APP_MEMORY_LIMIT_MIB",
    "NUTSNEWS_APP_MEMORY_RESERVATION_MIB",
    "NUTSNEWS_APP_PIDS_LIMIT",
    "NUTSNEWS_APP_LOG_MAX_SIZE",
    "NUTSNEWS_APP_LOG_MAX_FILE",
    "external: true",
    "app-cache:",
):
    assert required in app_compose, f"Missing runtime-isolation Compose field: {required}"
assert "ports:" not in app_compose
assert "--project-name" in environment_tasks
assert "--remove-orphans" in environment_tasks
assert "nutsnews_environment.name == 'staging'" in environment_validation_tasks
assert "image_review_status == 'reviewed'" in environment_validation_tasks
assert "last_known_good_state_file" in state_tasks
assert "nutsnews_production_runtime_contract.yml" in environment_tasks
assert "Inspect existing non-selected NutsNews runtime directories" in tasks
assert "Reconcile shared Compose source for existing non-selected runtimes" in tasks
assert "source_compose_file" in tasks
assert "item.stat.isdir | default(false)" in tasks
assert "vps_service_foundation_nutsnews_production_required_runtime_env_keys" in production_runtime_contract_tasks
assert "NUTSNEWS_PUBLIC_SUPABASE_URL" in production_runtime_contract_tasks
assert "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY" in production_runtime_contract_tasks
assert "nutsnews_environment.health_path == '/readyz'" in environment_validation_tasks

assert caddy_compose.count("networks:\n      - edge") == 2
assert "com.docker.compose.network=edge" in caddy_compose
assert "\n  edge:\n    name: nutsnews-edge-v6" in caddy_compose
assert "name: nutsnews-edge-v6" in caddy_compose
assert "nutsnews-edge-staging" not in caddy_compose
assert "vps_service_foundation_nutsnews_environments.production" in staged_route
assert "uri strip_prefix" in staged_route
assert "handle_path" not in staged_route
assert "vps_service_foundation_nutsnews_environments.production" in public_route
assert "header_up Host" in public_route
assert "header_up X-Forwarded-Proto" in public_route
assert "flush_interval -1" in public_route
assert "nutsnews_environment.envs | dictsort" in app_env_template
assert "NUTSNEWS_APP_ENVIRONMENT" in app_env_template
for identity_key in (
    "NUTSNEWS_DEPLOYED_IMAGE_DIGEST",
    "NUTSNEWS_EXPECTED_SOURCE_COMMIT",
    "NUTSNEWS_EXPECTED_BUILD_ID",
    "NUTSNEWS_CONFIG_GENERATION",
    "NUTSNEWS_EXPECTED_SCHEMA_VERSION",
):
    assert identity_key in app_env_template
assert "nutsnews_environment.name == 'staging'" not in app_env_template

public_site = caddyfile.split("vps.nutsnews.com {", 1)[1].split("ops.nutsnews.com {", 1)[0]
assert "handle /health" in public_site
assert "import /etc/nutsnews/caddy/app.public.routes" in public_site
assert public_site.index("import /etc/nutsnews/caddy/app.public.routes") < public_site.rindex("handle {")
assert "Content-Security-Policy" not in public_site.split("handle /health", 1)[0]

assert "Render and apply selected NutsNews runtime environments" in tasks
assert "Verify selected NutsNews runtime environment state" in tasks
assert "'production' in vps_service_foundation_nutsnews_deployment_environments" in tasks
assert "True\\|none" not in state_tasks

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

print("Reviewed immutable app deployment and runtime-isolation guardrails passed.")
