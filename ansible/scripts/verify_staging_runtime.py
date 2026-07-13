#!/usr/bin/env python3
"""Verify staging /readyz identity and Docker's resolved immutable digest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import time

from validate_staging_candidate import CandidateError, validate_candidate


STAGING_HOST = "65.75.202.112"
STAGING_USER = "nutsnews_ops"
STAGING_CONTAINER = "nutsnews-app-staging"
STAGING_TARGET = "vps-staging"
READY_ATTEMPTS = 30
READY_DELAY_SECONDS = 3
SSH_TIMEOUT_SECONDS = 20
READY_NODE_PROGRAM = r"""
const response = await fetch('http://127.0.0.1:3000/readyz', { signal: AbortSignal.timeout(5000) });
const body = await response.json().catch(() => ({}));
const headers = Object.fromEntries([
  'x-nutsnews-source-commit',
  'x-nutsnews-build-id',
  'x-nutsnews-deployment-target',
  'x-nutsnews-config-generation',
  'x-nutsnews-expected-image-digest',
].map((name) => [name, response.headers.get(name) || '']));
console.log(JSON.stringify({ status: response.status, body, headers }));
""".strip()


def ssh_command(key: Path, known_hosts: Path, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=10",
        f"{STAGING_USER}@{STAGING_HOST}",
        remote_command,
    ]


def run_ssh(key: Path, known_hosts: Path, remote_command: str) -> str:
    completed = subprocess.run(
        ssh_command(key, known_hosts, remote_command),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=SSH_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise CandidateError("Staging runtime SSH verification command failed.")
    return completed.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--config-generation", required=True)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    arguments = parser.parse_args()

    try:
        candidate = validate_candidate(json.loads(arguments.candidate_file.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, CandidateError) as error:
        raise SystemExit(f"Cannot verify staging runtime: {error}") from error

    expected_image = f"{candidate.image_repository}@{candidate.image_digest}"
    readiness: dict[str, object] | None = None
    for _ in range(READY_ATTEMPTS):
        try:
            response = run_ssh(
                arguments.ssh_key,
                arguments.known_hosts,
                f"sudo docker exec {STAGING_CONTAINER} node -e {shlex.quote(READY_NODE_PROGRAM)}",
            )
            parsed = json.loads(response)
            if not isinstance(parsed, dict):
                raise CandidateError("Staging /readyz response was not an object.")
            body = parsed.get("body")
            headers = parsed.get("headers")
            if not isinstance(body, dict) or not isinstance(headers, dict):
                raise CandidateError("Staging /readyz response had an invalid shape.")
            expected_headers = {
                "x-nutsnews-source-commit": candidate.source_commit,
                "x-nutsnews-build-id": candidate.build_id,
                "x-nutsnews-deployment-target": STAGING_TARGET,
                "x-nutsnews-config-generation": arguments.config_generation,
                "x-nutsnews-expected-image-digest": candidate.image_digest,
            }
            if parsed.get("status") == 200 and body.get("ok") is True and all(
                headers.get(key) == value for key, value in expected_headers.items()
            ):
                readiness = parsed
                break
        except (CandidateError, json.JSONDecodeError, subprocess.TimeoutExpired):
            pass
        time.sleep(READY_DELAY_SECONDS)
    if readiness is None:
        raise SystemExit("Staging /readyz did not return the approved identity before the bounded timeout.")

    try:
        configured_image = run_ssh(
            arguments.ssh_key,
            arguments.known_hosts,
            f"sudo docker inspect --format '{{{{.Config.Image}}}}' {STAGING_CONTAINER}",
        )
        image_id = run_ssh(
            arguments.ssh_key,
            arguments.known_hosts,
            f"sudo docker inspect --format '{{{{.Image}}}}' {STAGING_CONTAINER}",
        )
        repo_digests = run_ssh(
            arguments.ssh_key,
            arguments.known_hosts,
            f"sudo docker image inspect --format '{{{{range .RepoDigests}}}}{{{{println .}}}}{{{{end}}}}' {image_id}",
        ).splitlines()
    except CandidateError as error:
        raise SystemExit(f"Could not read Docker's staging image identity: {error}") from error
    if configured_image != expected_image or expected_image not in repo_digests:
        raise SystemExit("Docker's actual running staging image digest does not equal the requested immutable digest.")

    result = {
        "actual_digest": candidate.image_digest,
        "deployment_id": candidate.deployment_id,
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    arguments.result_file.parent.mkdir(parents=True, exist_ok=True)
    arguments.result_file.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    arguments.result_file.chmod(0o600)
    print(f"Staging readiness and Docker digest verified for {candidate.deployment_id}.")


if __name__ == "__main__":
    main()
