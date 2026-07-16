#!/usr/bin/env python3
"""Validate the VPS runtime drift checker stays read-only and scoped."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(".")
SCRIPT = ROOT / "ansible/scripts/vps_runtime_drift_check.py"
TEXT = SCRIPT.read_text(encoding="utf-8")
MODULE = ast.parse(TEXT)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


for token in (
    "MANAGED_FILE_MAP",
    "REMOTE_METADATA_PATHS",
    "deployed-infra-commit",
    "last-apply.json",
    "expected_commit",
    "deployed_commit",
    "last_apply_commit",
    "expected_sha256",
    "actual_sha256",
):
    require(token in TEXT, f"Drift checker missing {token}.")

for token in (
    "compose/caddy/compose.yml",
    "compose/caddy/Dockerfile",
    "compose/nutsnews/compose.yml",
    "compose/staging-access/compose.yml",
    "staging-access/jwt_gateway.py",
    "/opt/nutsnews/apps/caddy/compose.yml",
    "/opt/nutsnews/apps/caddy/Dockerfile",
    "/opt/nutsnews/apps/nutsnews/compose.yml",
    "/opt/nutsnews/apps/nutsnews-staging/compose.yml",
    "/opt/nutsnews/staging-access/compose.yml",
    "/opt/nutsnews/staging-access/jwt_gateway.py",
):
    require(token in TEXT, f"Drift checker must compare {token}.")

for forbidden in (
    "/etc/nutsnews",
    "env_file",
    "password",
    "token",
    "secret",
    "cookie",
    "csrf",
    "cat ",
    "sed ",
):
    require(forbidden not in TEXT.lower(), f"Drift checker may expose sensitive data via {forbidden}.")

commands: list[list[str]] = []
for node in ast.walk(MODULE):
    if not isinstance(node, ast.Call):
        continue
    if not (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ):
        continue
    if not node.args or not isinstance(node.args[0], ast.List):
        continue
    values: list[str] = []
    for element in node.args[0].elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    commands.append(values)

flat_commands = {item for command in commands for item in command}
require("ssh" in flat_commands, "Drift checker must read live files through SSH.")
require("sudo" in flat_commands, "Drift checker must use sudo only for read-only file hashing.")
for forbidden_command in ("scp", "rsync", "tee", "cp", "mv", "rm", "docker"):
    require(
        forbidden_command not in flat_commands,
        f"Drift checker must not run mutating or broad command {forbidden_command}.",
    )

print("VPS runtime drift checker guardrails passed.")
