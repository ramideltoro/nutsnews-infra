#!/usr/bin/env python3
"""Validate fail-safe GitHub Actions concurrency queue semantics."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONLINT_CONFIG = ROOT / ".github" / "actionlint.yaml"


def concurrency_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "concurrency:":
            continue
        indentation = len(line) - len(line.lstrip())
        body = [line]
        for candidate in lines[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indentation:
                break
            body.append(candidate)
        blocks.append("\n".join(body))
    return blocks


def validate_text(name: str, text: str) -> list[str]:
    errors: list[str] = []
    for index, block in enumerate(concurrency_blocks(text), start=1):
        cancel_false = re.search(r"(?m)^\s+cancel-in-progress:\s*false\s*$", block)
        cancel_true = re.search(r"(?m)^\s+cancel-in-progress:\s*true\s*$", block)
        queue_max = re.search(r"(?m)^\s+queue:\s*max\s*$", block)
        if cancel_false and not queue_max:
            errors.append(
                f"{name} concurrency block {index} is non-canceling but lacks queue: max"
            )
        if cancel_true and queue_max:
            errors.append(
                f"{name} concurrency block {index} combines queue: max with cancel-in-progress: true"
            )
        queue_lines = re.findall(r"(?m)^\s+queue:\s*(\S+)\s*$", block)
        if any(value != "max" for value in queue_lines):
            errors.append(f"{name} concurrency block {index} uses an unsupported queue value")
    return errors


def self_test() -> None:
    valid_queued = """concurrency:
  group: production
  queue: max
  cancel-in-progress: false
"""
    valid_canceling = """concurrency:
  group: ci
  cancel-in-progress: true
"""
    missing_queue = """concurrency:
  group: production
  cancel-in-progress: false
"""
    conflicting = """concurrency:
  group: production
  queue: max
  cancel-in-progress: true
"""
    assert validate_text("valid-queued", valid_queued) == []
    assert validate_text("valid-canceling", valid_canceling) == []
    assert any("lacks queue: max" in error for error in validate_text("missing", missing_queue))
    assert any("combines queue: max" in error for error in validate_text("conflict", conflicting))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("Workflow concurrency validator self-tests passed.")
        return 0

    errors: list[str] = []
    workflow_count = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow_count += 1
        errors.extend(validate_text(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")))
    if workflow_count == 0:
        errors.append("no GitHub Actions workflows were found")

    config = ACTIONLINT_CONFIG.read_text(encoding="utf-8") if ACTIONLINT_CONFIG.exists() else ""
    expected_ignore = 'unexpected key "queue" for "concurrency" section'
    if config.count(expected_ignore) != 1:
        errors.append("actionlint must ignore exactly its one unsupported concurrency queue parser error")

    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Workflow concurrency queue guardrails passed for {workflow_count} workflows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
