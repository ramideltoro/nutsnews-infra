#!/usr/bin/env python3
"""Render and assert strict NutsNews production/staging runtime isolation."""

from __future__ import annotations

import grp
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RENDER_PLAYBOOK = ROOT / "tests/render_nutsnews_environments.yml"
STAGING_ONLY_PLAYBOOK = ROOT / "tests/staging_only_nutsnews_environment.yml"
INVALID_INPUT_PLAYBOOK = ROOT / "tests/validate_nutsnews_environment_input.yml"
PRODUCTION_CONTRACT_PLAYBOOK = ROOT / "tests/validate_production_runtime_contract.yml"
DEFAULT_MODEL_PLAYBOOK = ROOT / "tests/validate_nutsnews_environment_defaults.yml"
ENVIRONMENT_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment.yml"
ENVIRONMENT_VALIDATION_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment_validate.yml"
MAIN_TASKS = ROOT / "roles/vps_service_foundation/tasks/main.yml"
APP_COMPOSE = REPO / "compose/nutsnews/compose.yml"
CADDY_COMPOSE = REPO / "compose/caddy/compose.yml"


def run(command: list[str], environment: dict[str, str], *, expect: int = 0) -> str:
    base_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("NUTSNEWS_APP_")
    }
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**base_environment, **environment},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"Command returned {result.returncode}, expected {expect}: {' '.join(command)}\n{result.stdout}"
        )
    return result.stdout


def compose_config(environment_file: Path, compose_file: Path) -> dict:
    output = run(
        [
            "docker",
            "compose",
            "--env-file",
            str(environment_file),
            "-f",
            str(compose_file),
            "config",
            "--format",
            "json",
        ],
        {},
    )
    return json.loads(output)


def runtime_paths(root: Path, environment: str, suffix: str = "") -> dict[str, Path]:
    if environment == "production":
        return {
            "compose": root / "apps/nutsnews/compose.yml",
            "env": root / "etc/nutsnews-app.env",
            "manifest": root / "ops/apps/production/release.json",
        }
    return {
        "compose": root / f"apps/nutsnews-staging-{suffix}/compose.yml",
        "env": root / f"etc/nutsnews-staging-{suffix}.env",
        "manifest": root / f"ops/apps/staging-{suffix}/release.json",
    }


assert shutil.which("ansible-playbook"), "ansible-playbook is required for runtime-isolation regression coverage."
assert shutil.which("docker"), "Docker Compose is required for runtime-isolation regression coverage."

run(["ansible-playbook", "--check", str(DEFAULT_MODEL_PLAYBOOK)], {})

valid_production_runtime = {
    "NUTSNEWS_RUNTIME_ENV": "production",
    "NUTSNEWS_SIDE_EFFECTS_MODE": "live",
    "NUTSNEWS_DATA_ENVIRONMENT": "production",
    "NUTSNEWS_SUPABASE_CREDENTIALS_ENV": "production",
    "NUTSNEWS_SUPABASE_PROJECT_REF": "fixtureproductionref",
    "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF": "fixtureproductionref",
    "NUTSNEWS_PUBLIC_SUPABASE_URL": "https://fixtureproductionref.supabase.co",
    "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY": "fixture-anon-key",
}
run(
    [
        "ansible-playbook",
        "--check",
        str(PRODUCTION_CONTRACT_PLAYBOOK),
        "-e",
        json.dumps({"test_runtime_envs": valid_production_runtime}),
    ],
    {},
)
run(
    [
        "ansible-playbook",
        "--check",
        str(PRODUCTION_CONTRACT_PLAYBOOK),
        "-e",
        json.dumps(
            {
                "test_runtime_envs": valid_production_runtime,
                "test_release_project_ref": "different-production",
            }
        ),
    ],
    {},
    expect=2,
)

invalid_production_runtimes = [
    {key: value for key, value in valid_production_runtime.items() if key != "NUTSNEWS_RUNTIME_ENV"},
    {**valid_production_runtime, "NUTSNEWS_RUNTIME_ENV": "staging"},
    {**valid_production_runtime, "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF": "different-production"},
]
for invalid_runtime in invalid_production_runtimes:
    run(
        [
            "ansible-playbook",
            "--check",
            str(PRODUCTION_CONTRACT_PLAYBOOK),
            "-e",
            json.dumps({"test_runtime_envs": invalid_runtime}),
        ],
        {},
        expect=2,
    )

with tempfile.TemporaryDirectory(prefix="nutsnews-runtime-isolation-") as temporary_directory:
    root = Path(temporary_directory)
    render_environment = {"NUTSNEWS_RENDER_ROOT": str(root), "NUTSNEWS_STAGING_SUFFIX": "base"}
    run(["ansible-playbook", str(RENDER_PLAYBOOK)], render_environment)

    production = runtime_paths(root, "production")
    staging = runtime_paths(root, "staging", "base")
    production_before = {name: path.read_bytes() for name, path in production.items()}

    production_config = compose_config(production["env"], production["compose"])
    staging_config = compose_config(staging["env"], staging["compose"])
    production_manifest = json.loads(production["manifest"].read_text(encoding="utf-8"))
    staging_manifest = json.loads(staging["manifest"].read_text(encoding="utf-8"))

    production_service = production_config["services"]["nutsnews-app"]
    staging_service = staging_config["services"]["nutsnews-app"]
    assert production_config["name"] != staging_config["name"]
    assert production_service["container_name"] != staging_service["container_name"]
    assert production_service["networks"]["environment"]["aliases"] != staging_service["networks"]["environment"]["aliases"]
    assert production_config["networks"]["environment"]["name"] != staging_config["networks"]["environment"]["name"]
    assert production_config["volumes"]["app-cache"]["name"] != staging_config["volumes"]["app-cache"]["name"]
    assert not production_service.get("ports") and not staging_service.get("ports")
    assert production_service["cpus"] == 4.0
    assert production_service["cpu_shares"] == 1024
    assert production_service["mem_limit"] == "805306368"
    assert production_service["mem_reservation"] == "536870912"
    assert production_service["pids_limit"] == 256
    assert production_service["logging"]["driver"] == "json-file"
    assert production_service["logging"]["options"] == {"max-file": "3", "max-size": "10m"}
    assert "/readyz" in " ".join(production_service["healthcheck"]["test"])
    assert staging_service["cpus"] == 1.0
    assert staging_service["cpu_shares"] == 256
    assert staging_service["mem_limit"] == "536870912"
    assert staging_service["mem_reservation"] == "268435456"
    assert staging_service["pids_limit"] == 128
    assert staging_service["logging"]["driver"] == "json-file"
    assert staging_service["logging"]["options"] == {"max-file": "3", "max-size": "10m"}
    assert "/readyz" in " ".join(staging_service["healthcheck"]["test"])
    assert production["env"] != staging["env"]
    for key in (
        "app_dir",
        "env_file",
        "state_dir",
        "release_manifest_file",
        "apply_marker_file",
        "last_known_good_state_file",
    ):
        assert production_manifest[key] != staging_manifest[key], f"Runtime state collision: {key}"

    # Only the staging fixture changes; production artifacts must remain byte-identical.
    run(
        ["ansible-playbook", str(RENDER_PLAYBOOK)],
        {"NUTSNEWS_RENDER_ROOT": str(root), "NUTSNEWS_STAGING_SUFFIX": "changed"},
    )
    assert production_before == {name: path.read_bytes() for name, path in production.items()}

    staging_check_root = root / "staging-only"
    (staging_check_root / "apps/nutsnews-staging").mkdir(parents=True)
    (staging_check_root / "ops/apps/staging").mkdir(parents=True)
    (staging_check_root / "etc").mkdir(parents=True)
    staging_check_output = run(
        ["ansible-playbook", "--check", "--diff", str(STAGING_ONLY_PLAYBOOK)],
        {
            "NUTSNEWS_STAGING_ONLY_ROOT": str(staging_check_root),
            "NUTSNEWS_STAGING_ONLY_GROUP": grp.getgrgid(os.getgid()).gr_name,
        },
    )
    assert "/apps/nutsnews/" not in staging_check_output
    assert "/etc/nutsnews-app.env" not in staging_check_output
    assert not re.search(r'container_name:\s*nutsnews-app(?:\s|$)', staging_check_output)

    for environment_name in ("production", "staging"):
        output = run(
            ["ansible-playbook", "--check", str(INVALID_INPUT_PLAYBOOK)],
            {"NUTSNEWS_TEST_ENVIRONMENT": environment_name},
            expect=2,
        )
        assert "FAILED" in output

environment_tasks = ENVIRONMENT_TASKS.read_text(encoding="utf-8")
environment_validation_tasks = ENVIRONMENT_VALIDATION_TASKS.read_text(encoding="utf-8")
main_tasks = MAIN_TASKS.read_text(encoding="utf-8")
app_compose = APP_COMPOSE.read_text(encoding="utf-8")
caddy_compose = CADDY_COMPOSE.read_text(encoding="utf-8")

assert "--project-name" in environment_tasks
assert "--remove-orphans" in environment_tasks
assert not re.search(r"\b(docker\s+(?:compose\s+)?(?:down|stop|rm))\b", environment_tasks)
assert "Mutable tags" in environment_validation_tasks
assert "nutsnews_production_runtime_contract.yml" in environment_tasks
assert "vps_service_foundation_nutsnews_deployment_environments" in main_tasks
assert "NUTSNEWS_APP_PROJECT_NAME" in app_compose
assert "NUTSNEWS_APP_NETWORK_NAME" in app_compose
assert "NUTSNEWS_APP_CACHE_VOLUME_NAME" in app_compose
assert "NUTSNEWS_APP_CPU_LIMIT" in app_compose
assert "NUTSNEWS_APP_MEMORY_LIMIT_MIB" in app_compose
assert "NUTSNEWS_APP_LOG_MAX_SIZE" in app_compose
assert "nutsnews-edge-staging" not in caddy_compose
assert caddy_compose.count("networks:\n      - edge") == 2
assert "com.docker.compose.network=edge" in caddy_compose
assert "\n  edge:\n    name: nutsnews-edge" in caddy_compose

print("NutsNews production/staging runtime isolation regression coverage passed.")
