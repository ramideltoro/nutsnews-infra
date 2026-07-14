#!/usr/bin/env python3
"""Server-side forced command for the staging-only deployment identity."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


BUNDLE = Path("/opt/nutsnews/staging-deploy-bundle")
MARKER = BUNDLE / "infra-commit"
MAX_REQUEST_BYTES = 1_048_576
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_LINE = re.compile(r"^TASK \[([A-Za-z0-9 _./:()'=-]{1,200})\]", re.MULTILINE)


def fail(message: str, *, task: str = "") -> None:
    response = {"ok": False, "code": message}
    if task:
        response["task"] = task
    print(json.dumps(response, separators=(",", ":")))
    raise SystemExit(1)


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
        if operation == "check":
            command.extend(["--check", "--diff"])
        result = subprocess.run(
            command,
            cwd=BUNDLE / "ansible",
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
            fail(f"staging_{operation}_failed", task=tasks[-1] if tasks else "")
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
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Image}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
            "nutsnews-app-staging",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    expected = f"ghcr.io/ramideltoro/nutsnews@{digest}|true|healthy"
    if inspect.returncode or inspect.stdout.strip() != expected:
        fail("staging_runtime_mismatch")
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
    print(
        json.dumps(
            {"ok": True, "operation": "verify", "actual_digest": digest, "config_generation": config_generation},
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
