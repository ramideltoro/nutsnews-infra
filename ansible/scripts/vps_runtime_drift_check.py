#!/usr/bin/env python3
"""Compare reviewed runtime source files with live VPS managed files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

MANAGED_FILE_MAP = (
    {
        "name": "caddy-compose",
        "local": "compose/caddy/compose.yml",
        "remote": "/opt/nutsnews/apps/caddy/compose.yml",
    },
    {
        "name": "caddy-dockerfile",
        "local": "compose/caddy/Dockerfile",
        "remote": "/opt/nutsnews/apps/caddy/Dockerfile",
    },
    {
        "name": "production-app-compose",
        "local": "compose/nutsnews/compose.yml",
        "remote": "/opt/nutsnews/apps/nutsnews/compose.yml",
    },
    {
        "name": "staging-app-compose",
        "local": "compose/nutsnews/compose.yml",
        "remote": "/opt/nutsnews/apps/nutsnews-staging/compose.yml",
    },
    {
        "name": "staging-access-compose",
        "local": "compose/staging-access/compose.yml",
        "remote": "/opt/nutsnews/staging-access/compose.yml",
    },
    {
        "name": "staging-access-gateway",
        "local": "staging-access/jwt_gateway.py",
        "remote": "/opt/nutsnews/staging-access/jwt_gateway.py",
    },
)

REMOTE_METADATA_PATHS = {
    "deployed_commit": "/opt/nutsnews/ops/deployed-infra-commit",
    "last_apply": "/opt/nutsnews/ops/last-apply.json",
}

REMOTE_SCRIPT = r"""
import hashlib
import json
from pathlib import Path

paths = json.loads({paths_json!r})
metadata_paths = json.loads({metadata_json!r})
files = {{}}
for path_text in paths:
    path = Path(path_text)
    if not path.exists():
        files[path_text] = {{"present": False}}
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files[path_text] = {{"present": True, "sha256": digest}}

metadata = {{}}
deployed_path = Path(metadata_paths["deployed_commit"])
if deployed_path.exists():
    metadata["deployed_commit"] = deployed_path.read_text(encoding="utf-8").strip()
last_apply_path = Path(metadata_paths["last_apply"])
if last_apply_path.exists():
    try:
        last_apply = json.loads(last_apply_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        metadata["last_apply_error"] = "invalid_json"
    else:
        metadata["last_apply_commit"] = str(last_apply.get("commit", ""))
        metadata["last_apply_status"] = str(last_apply.get("status", ""))
        metadata["last_apply_run_id"] = str(last_apply.get("run_id", ""))

print(json.dumps({{"files": files, "metadata": metadata}}, sort_keys=True))
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def read_remote(target: str) -> dict[str, Any]:
    remote_paths = [entry["remote"] for entry in MANAGED_FILE_MAP]
    script = REMOTE_SCRIPT.format(
        paths_json=json.dumps(remote_paths),
        metadata_json=json.dumps(REMOTE_METADATA_PATHS),
    )
    result = subprocess.run(
        ["ssh", target, "sudo", "python3", "-"],
        check=True,
        text=True,
        input=script,
        capture_output=True,
    )
    return json.loads(result.stdout)


def build_report(target: str, expected_commit: str) -> dict[str, Any]:
    remote = read_remote(target)
    remote_files = remote.get("files", {})
    files: list[dict[str, Any]] = []
    ok = True
    for entry in MANAGED_FILE_MAP:
        local_path = REPO_ROOT / entry["local"]
        expected_sha = sha256(local_path)
        remote_state = remote_files.get(entry["remote"], {})
        actual_sha = remote_state.get("sha256") if remote_state.get("present") else None
        status = "match" if actual_sha == expected_sha else "drift"
        if status != "match":
            ok = False
        files.append(
            {
                "name": entry["name"],
                "local": entry["local"],
                "remote": entry["remote"],
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "status": status,
            }
        )

    metadata = remote.get("metadata", {})
    metadata_status = {
        "expected_commit": expected_commit,
        "deployed_commit": metadata.get("deployed_commit", ""),
        "last_apply_commit": metadata.get("last_apply_commit", ""),
        "last_apply_status": metadata.get("last_apply_status", ""),
        "last_apply_run_id": metadata.get("last_apply_run_id", ""),
    }
    for key in ("deployed_commit", "last_apply_commit"):
        if metadata_status[key] != expected_commit:
            ok = False

    return {
        "ok": ok,
        "target": target,
        "metadata": metadata_status,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="nutsnews-vps", help="SSH target alias")
    parser.add_argument(
        "--expected-commit",
        default=current_commit(),
        help="reviewed infra commit expected in live apply metadata",
    )
    args = parser.parse_args()

    report = build_report(args.target, args.expected_commit)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
