#!/usr/bin/env python3
"""Validate workflow action references use immutable pins."""

from __future__ import annotations

import argparse
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
FULL_GIT_SHA = re.compile(r"[0-9a-fA-F]{40}")
SHA256_DIGEST = re.compile(r"sha256:[0-9a-fA-F]{64}")

# Every exception must be narrow, deliberate, and documented with a reason.
ACTION_PIN_ALLOWLIST: dict[str, str] = {}


@dataclass(frozen=True)
class UsesReference:
    path: Path
    line_number: int
    value: str
    job: str | None
    step: str | None


def strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.rstrip()


def parse_scalar(value: str) -> str:
    value = strip_inline_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def iter_workflow_files(workflow_dir: Path) -> Iterable[Path]:
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(workflow_dir.glob(pattern))


def iter_uses_references(path: Path) -> Iterable[UsesReference]:
    current_job: str | None = None
    current_step: str | None = None
    in_jobs = False

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if indent == 0:
            in_jobs = stripped == "jobs:"
            current_job = None
            current_step = None
            continue

        if in_jobs and indent == 2:
            match = re.match(r"([A-Za-z0-9_-]+):\s*(?:#.*)?$", stripped)
            if match:
                current_job = match.group(1)
                current_step = None

        if stripped.startswith("- "):
            current_step = None

        step_match = re.match(r"-\s+name:\s*(.+)$", stripped)
        if step_match:
            current_step = parse_scalar(step_match.group(1))
            continue

        uses_match = re.match(r"(?:-\s+)?uses:\s*(.+)$", stripped)
        if uses_match:
            yield UsesReference(
                path=path,
                line_number=line_number,
                value=parse_scalar(uses_match.group(1)),
                job=current_job,
                step=current_step,
            )


def is_local_reference(value: str) -> bool:
    return value.startswith("./")


def is_pinned_docker_reference(value: str) -> bool:
    if not value.startswith("docker://"):
        return False
    return bool(SHA256_DIGEST.search(value))


def is_pinned_action_reference(value: str) -> bool:
    if value.startswith("docker://"):
        return is_pinned_docker_reference(value)
    if "@" not in value:
        return False
    _, ref = value.rsplit("@", 1)
    return bool(FULL_GIT_SHA.fullmatch(ref))


def validate_allowlist() -> list[str]:
    failures: list[str] = []
    for value, reason in ACTION_PIN_ALLOWLIST.items():
        if not value.strip():
            failures.append("Action pin allowlist contains an empty uses value.")
        if not reason.strip():
            failures.append(f"Action pin allowlist entry for {value!r} must include a reason.")
    return failures


def format_context(reference: UsesReference) -> str:
    parts = []
    if reference.job:
        parts.append(f"job {reference.job!r}")
    if reference.step:
        parts.append(f"step {reference.step!r}")
    return ", ".join(parts) if parts else "workflow"


def validate_workflow_dir(workflow_dir: Path) -> list[str]:
    failures = validate_allowlist()
    for workflow in iter_workflow_files(workflow_dir):
        for reference in iter_uses_references(workflow):
            if is_local_reference(reference.value):
                continue
            if reference.value in ACTION_PIN_ALLOWLIST:
                continue
            if is_pinned_action_reference(reference.value):
                continue
            relative_path = reference.path.relative_to(ROOT) if reference.path.is_relative_to(ROOT) else reference.path
            failures.append(
                f"{relative_path}:{reference.line_number}: {format_context(reference)} uses "
                f"{reference.value!r}, which must be pinned to a full 40-character commit SHA. "
                "Use a SHA pin or add a narrow ACTION_PIN_ALLOWLIST entry with a reason."
            )
    return failures


def write_fixture(path: Path, uses_value: str) -> None:
    path.write_text(
        "\n".join(
            [
                "name: fixture",
                "on: workflow_dispatch",
                "jobs:",
                "  test:",
                "    runs-on: ubuntu-latest",
                "    steps:",
                "      - name: Exercise fixture action",
                f"        uses: {uses_value}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        workflow_dir = Path(temp_dir)
        write_fixture(workflow_dir / "unpinned.yml", "actions/checkout@v7")
        failures = validate_workflow_dir(workflow_dir)
        assert failures and "actions/checkout@v7" in failures[0], failures

    with tempfile.TemporaryDirectory() as temp_dir:
        workflow_dir = Path(temp_dir)
        write_fixture(workflow_dir / "pinned.yml", "actions/checkout@" + "a" * 40)
        failures = validate_workflow_dir(workflow_dir)
        assert not failures, failures

    with tempfile.TemporaryDirectory() as temp_dir:
        workflow_dir = Path(temp_dir)
        write_fixture(workflow_dir / "local.yml", "./.github/actions/example")
        failures = validate_workflow_dir(workflow_dir)
        assert not failures, failures

    print("Workflow action pin validator self-test passed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run built-in validator fixture checks.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return

    failures = validate_workflow_dir(WORKFLOWS)
    if failures:
        raise SystemExit("\n".join(failures))
    print("Workflow action pins validated.")


if __name__ == "__main__":
    main()
