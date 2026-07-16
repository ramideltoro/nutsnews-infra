#!/usr/bin/env python3
"""Server-side forced command for the staging-only deployment identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


BUNDLE = Path("/opt/nutsnews/staging-deploy-bundle")
MARKER = BUNDLE / "infra-commit"
MAX_REQUEST_BYTES = 1_048_576
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_LINE = re.compile(r"TASK \[([A-Za-z0-9 _./:()'=-]{1,200})\]")
CONTROLLER_VERSION = re.compile(r"\[core ([0-9]+\.[0-9]+\.[0-9]+)\]")
ERROR_CLASSES = (
    ("invalid_yaml", ("unable to read either as json nor yaml", "syntax error while loading yaml")),
    ("unsupported_option", ("invalid options for", "unsupported parameters for")),
    ("missing_module", ("couldn't resolve module/action", "could not find the requested service")),
    ("missing_role", ("the role ", " was not found")),
    ("missing_file", ("could not find or access", "unable to retrieve file contents")),
    ("undefined_variable", ("undefined variable", " is undefined")),
    ("invalid_play_attribute", ("is not a valid attribute for a play",)),
    ("conflicting_action", ("conflicting action statements",)),
    ("callback_error", ("callback", "failed to load")),
    ("controller_exception", ("unexpected exception", "traceback (most recent call last)")),
)


def fail(message: str, *, task: str = "", diagnostic: str = "", controller: str = "") -> None:
    response = {"ok": False, "code": message}
    if task:
        response["task"] = task
    if diagnostic:
        response["diagnostic"] = diagnostic
    if controller:
        response["controller"] = controller
    print(json.dumps(response, separators=(",", ":")))
    raise SystemExit(1)


def classify_controller_output(output: str) -> str:
    lowered = output.lower()
    for name, fragments in ERROR_CLASSES:
        if any(fragment in lowered for fragment in fragments):
            return name
    if not output.strip():
        return "empty_controller_output"
    if "error!" in lowered:
        return "unclassified_controller_error"
    return "unclassified_controller_failure"


def docker_inspect(container: str) -> dict[str, object]:
    inspected = subprocess.run(
        ["docker", "inspect", container],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        payload = []
    if inspected.returncode or not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        fail("staging_boundary_inspect_failed")
    return payload[0]


def secure_root_file(path: Path) -> bool:
    try:
        metadata = path.stat()
    except OSError:
        return False
    return metadata.st_uid == 0 and metadata.st_gid == 0 and stat.S_IMODE(metadata.st_mode) == 0o600


def root_directory(path: Path) -> bool:
    try:
        metadata = path.stat()
    except OSError:
        return False
    return path.is_dir() and metadata.st_uid == 0 and metadata.st_gid == 0


def read_request() -> dict[str, object]:
    if os.environ.get("SSH_ORIGINAL_COMMAND", "").strip():
        fail("arbitrary_command_rejected")
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        fail("invalid_request_size")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("invalid_json")
    if not isinstance(request, dict):
        fail("invalid_request")
    return request


def run_deploy(request: dict[str, object], operation: str) -> None:
    infra_commit = request.get("infra_commit")
    candidate = request.get("candidate")
    staging_envs = request.get("staging_app_envs")
    if not isinstance(infra_commit, str) or not SHA.fullmatch(infra_commit):
        fail("invalid_infra_commit")
    if MARKER.read_text(encoding="utf-8").strip() != infra_commit:
        fail("unreviewed_infra_commit")
    if not isinstance(candidate, dict) or not isinstance(staging_envs, dict):
        fail("invalid_deploy_payload")

    with tempfile.TemporaryDirectory(prefix="nutsnews-staging-deploy-") as temporary:
        root = Path(temporary)
        candidate_file = root / "candidate.json"
        vars_file = root / "vars.json"
        candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
        candidate_file.chmod(0o600)
        environment = {
            **os.environ,
            "NUTSNEWS_STAGING_APP_ENVS_JSON": json.dumps(staging_envs, separators=(",", ":")),
        }
        render = subprocess.run(
            [
                "python3",
                str(BUNDLE / "ansible/scripts/write_staging_ansible_vars.py"),
                "--candidate-file",
                str(candidate_file),
                "--infra-commit",
                infra_commit,
                "--output",
                str(vars_file),
            ],
            cwd=BUNDLE,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if render.returncode:
            fail("staging_payload_rejected")
        command = [
            "ansible-playbook",
            "playbooks/deploy-staging.yml",
            "--inventory",
            "inventories/staging/hosts.yml",
            "--limit",
            "staging-vps",
            "--tags",
            "nutsnews-staging-deploy",
            "--extra-vars",
            "ansible_connection=local",
            "--extra-vars",
            f"@{vars_file}",
        ]
        controller_environment = {
            **os.environ,
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_ROLES_PATH": str(BUNDLE / "ansible/roles"),
            "ANSIBLE_STDOUT_CALLBACK": "default",
            # Older controllers expose import_role defaults by default;
            # this keeps the equivalent behavior explicit on 2.17+.
            "ANSIBLE_PRIVATE_ROLE_VARS": "false",
        }
        version_result = subprocess.run(
            ["ansible-playbook", "--version"],
            cwd=BUNDLE / "ansible",
            env=controller_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        version_match = CONTROLLER_VERSION.search(version_result.stdout)
        controller = version_match.group(1) if version_match else "unknown"
        syntax = subprocess.run(
            [*command, "--syntax-check"],
            cwd=BUNDLE / "ansible",
            env=controller_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if syntax.returncode:
            fail(
                "staging_syntax_failed",
                diagnostic=classify_controller_output(syntax.stdout),
                controller=controller,
            )
        if operation == "check":
            command.extend(["--check", "--diff"])
        result = subprocess.run(
            command,
            cwd=BUNDLE / "ansible",
            env=controller_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode:
            # Ansible output can contain rendered diffs and must never cross
            # the forced-command boundary. Return only the last reviewed task
            # label so an operator can diagnose a failure without secrets.
            tasks = TASK_LINE.findall(result.stdout)
            fail(
                f"staging_{operation}_failed",
                task=tasks[-1] if tasks else "",
                diagnostic=classify_controller_output(result.stdout),
                controller=controller,
            )
    print(json.dumps({"ok": True, "operation": operation}, separators=(",", ":")))


def verify(request: dict[str, object]) -> None:
    digest = request.get("image_digest")
    config_generation = request.get("config_generation")
    candidate = request.get("candidate")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        fail("invalid_verify_digest")
    if not isinstance(config_generation, str) or not config_generation.startswith("staging-stg-"):
        fail("invalid_config_generation")
    if not isinstance(candidate, dict):
        fail("invalid_verify_candidate")
    source_commit = candidate.get("source_commit")
    build_id = candidate.get("build_id")
    if not isinstance(source_commit, str) or not SHA.fullmatch(source_commit) or not isinstance(build_id, str):
        fail("invalid_verify_identity")
    expected_image = f"ghcr.io/ramideltoro/nutsnews@{digest}"
    staging = docker_inspect("nutsnews-app-staging")
    staging_config = staging.get("Config", {})
    staging_state = staging.get("State", {})
    if not isinstance(staging_config, dict) or not isinstance(staging_state, dict):
        fail("staging_runtime_mismatch")
    staging_health = staging_state.get("Health", {})
    if (
        staging_config.get("Image") != expected_image
        or staging_state.get("Running") is not True
        or not isinstance(staging_health, dict)
        or staging_health.get("Status") != "healthy"
    ):
        fail("staging_runtime_mismatch")
    image_identity = staging.get("Image")
    image_inspect = subprocess.run(
        ["docker", "image", "inspect", str(image_identity)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        image_payload = json.loads(image_inspect.stdout)
    except json.JSONDecodeError:
        image_payload = []
    if (
        image_inspect.returncode
        or not isinstance(image_payload, list)
        or len(image_payload) != 1
        or not isinstance(image_payload[0], dict)
        or expected_image not in image_payload[0].get("RepoDigests", [])
    ):
        fail("staging_image_digest_mismatch")
    probe_program = (
        "fetch('http://127.0.0.1:3000/readyz',{signal:AbortSignal.timeout(5000)})"
        ".then(async r=>{const b=await r.json().catch(()=>({}));console.log(JSON.stringify({"
        "status:r.status,ok:b.ok===true,source:r.headers.get('x-nutsnews-source-commit'),"
        "build:r.headers.get('x-nutsnews-build-id'),target:r.headers.get('x-nutsnews-deployment-target'),"
        "generation:r.headers.get('x-nutsnews-config-generation'),digest:r.headers.get('x-nutsnews-expected-image-digest')}))})"
        ".catch(()=>process.exit(1))"
    )
    probe = subprocess.run(
        [
            "docker",
            "exec",
            "nutsnews-app-staging",
            "node",
            "-e",
            probe_program,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        readiness = json.loads(probe.stdout)
    except json.JSONDecodeError:
        readiness = {}
    if probe.returncode or readiness != {
        "status": 200,
        "ok": True,
        "source": source_commit,
        "build": build_id,
        "target": "vps-staging",
        "generation": config_generation,
        "digest": digest,
    }:
        fail("staging_readiness_failed")

    production = docker_inspect("nutsnews-app")
    access = docker_inspect("nutsnews-staging-access-verifier")
    caddy = docker_inspect("nutsnews-caddy")

    def container_metadata(value: dict[str, object]) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
        config = value.get("Config", {})
        state = value.get("State", {})
        host = value.get("HostConfig", {})
        network_settings = value.get("NetworkSettings", {})
        if not all(isinstance(item, dict) for item in (config, state, host, network_settings)):
            fail("staging_boundary_inspect_failed")
        return config, state, host, network_settings

    staging_config, staging_state, staging_host, staging_network_settings = container_metadata(staging)
    production_config, production_state, _, production_network_settings = container_metadata(production)
    access_config, access_state, access_host, access_network_settings = container_metadata(access)
    caddy_config, caddy_state, _, caddy_network_settings = container_metadata(caddy)

    def labels(config: dict[str, object]) -> dict[str, object]:
        value = config.get("Labels", {})
        return value if isinstance(value, dict) else {}

    def networks(settings: dict[str, object]) -> set[str]:
        value = settings.get("Networks", {})
        return set(value) if isinstance(value, dict) else set()

    def healthy(state: dict[str, object]) -> bool:
        health = state.get("Health", {})
        return state.get("Running") is True and isinstance(health, dict) and health.get("Status") == "healthy"

    def health_status(state: dict[str, object]) -> str:
        health = state.get("Health", {})
        if isinstance(health, dict):
            status = health.get("Status")
            if isinstance(status, str) and status:
                return status
        return "none"

    staging_labels = labels(staging_config)
    production_labels = labels(production_config)
    access_labels = labels(access_config)
    caddy_labels = labels(caddy_config)
    staging_networks = networks(staging_network_settings)
    production_networks = networks(production_network_settings)
    access_networks = networks(access_network_settings)
    caddy_networks = networks(caddy_network_settings)
    staging_log = staging_host.get("LogConfig", {})
    staging_log_config = staging_log.get("Config", {}) if isinstance(staging_log, dict) else {}
    staging_ports = staging_host.get("PortBindings")
    access_ports = access_host.get("PortBindings")

    staging_app_dir = Path("/opt/nutsnews/apps/nutsnews-staging")
    staging_state_dir = Path("/opt/nutsnews/ops/apps/staging")
    production_app_dir = Path("/opt/nutsnews/apps/nutsnews")
    production_state_dir = Path("/opt/nutsnews/ops/apps/production")
    staging_env = Path("/etc/nutsnews/nutsnews-staging-app.env")
    access_env = Path("/etc/nutsnews/nutsnews-staging-access.env")
    production_env = Path("/etc/nutsnews/nutsnews-app.env")
    caddy_file = Path("/opt/nutsnews/config/caddy/Caddyfile")
    try:
        caddy_text = caddy_file.read_text(encoding="utf-8")
    except OSError:
        caddy_text = ""
    caddy_mounts = caddy.get("Mounts", [])
    caddyfile_mounted = any(
        isinstance(mount, dict)
        and mount.get("Source") == str(caddy_file)
        and mount.get("Destination") == "/etc/caddy/Caddyfile"
        and mount.get("RW") is False
        for mount in caddy_mounts
    ) if isinstance(caddy_mounts, list) else False

    production_observation = {
        "running": production_state.get("Running") is True,
        "healthy": healthy(production_state),
        "health_status": health_status(production_state),
    }
    boundary = {
        "staging_unpublished": staging_ports in (None, {}) and access_ports in (None, {}),
        "compose_projects": (
            staging_labels.get("com.docker.compose.project") == "nutsnews-staging"
            and production_labels.get("com.docker.compose.project") == "nutsnews-app"
            and access_labels.get("com.docker.compose.project") == "nutsnews-staging-access"
            and caddy_labels.get("com.docker.compose.project") == "nutsnews-service-foundation"
        ),
        "immutable_digest": staging_config.get("Image") == expected_image,
        "network_separation": (
            staging_networks == {"nutsnews-edge-staging"}
            and access_networks == {"nutsnews-edge-staging"}
            and "nutsnews-edge-v6" in production_networks
            and "nutsnews-edge-staging" not in production_networks
            and {"nutsnews-edge-v6", "nutsnews-edge-staging"}.issubset(caddy_networks)
        ),
        "resource_limits": (
            staging_host.get("NanoCpus") == 1_000_000_000
            and staging_host.get("CpuShares") == 256
            and staging_host.get("Memory") == 512 * 1024 * 1024
            and staging_host.get("MemoryReservation") == 256 * 1024 * 1024
            and staging_host.get("PidsLimit") == 128
        ),
        "log_limits": (
            isinstance(staging_log_config, dict)
            and staging_log_config.get("max-size") == "10m"
            and staging_log_config.get("max-file") == "3"
        ),
        "directory_separation": (
            all(root_directory(path) for path in (staging_app_dir, staging_state_dir, production_app_dir, production_state_dir))
            and len({str(path.resolve()) for path in (staging_app_dir, staging_state_dir, production_app_dir, production_state_dir)}) == 4
        ),
        "env_file_permissions": all(secure_root_file(path) for path in (staging_env, access_env, production_env)),
        "caddy_route": (
            healthy(caddy_state)
            and caddyfile_mounted
            and "staging.nutsnews.com {" in caddy_text
            and "forward_auth nutsnews-staging-access:8091" in caddy_text
            and "uri /verify?" in caddy_text
            and "request>uri delete" in caddy_text
            and "request>headers>Cf-Access-Jwt-Assertion delete" in caddy_text
            and "resp_headers>Location delete" in caddy_text
            and "reverse_proxy nutsnews-app-staging:3000" in caddy_text
        ),
        "access_verifier_healthy": healthy(access_state),
    }
    if not all(boundary.values()):
        fail("staging_boundary_failed")
    print(
        json.dumps(
            {
                "ok": True,
                "operation": "verify",
                "actual_digest": digest,
                "config_generation": config_generation,
                "boundary": boundary,
                "production": production_observation,
            },
            separators=(",", ":"),
        )
    )


def main() -> None:
    request = read_request()
    operation = request.get("operation")
    if operation in {"check", "apply"}:
        run_deploy(request, str(operation))
    elif operation == "verify":
        verify(request)
    else:
        fail("operation_rejected")


if __name__ == "__main__":
    main()
