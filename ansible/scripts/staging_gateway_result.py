#!/usr/bin/env python3
"""Validate sanitized staging gateway responses."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re


RETRY_EXIT_CODE = 75
CODE_PATTERN = re.compile(r"[a-z_]{1,80}")
TASK_PATTERN = re.compile(r"[A-Za-z0-9 _./:()'=-]{1,200}")
CONTROLLER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class GatewayResultError(ValueError):
    """A gateway response is malformed or reports a sanitized failure."""


@dataclass(frozen=True)
class GatewayOutcome:
    ok: bool
    operation: str
    code: str = ""
    message: str = ""


def validate_failure_response(result: object) -> tuple[str, str, str, str]:
    if not isinstance(result, dict):
        raise GatewayResultError("Staging gateway returned an invalid failure response.")
    code = result.get("code")
    task = result.get("task", "")
    diagnostic = result.get("diagnostic", "")
    controller = result.get("controller", "")
    if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
        raise GatewayResultError("Staging gateway returned an invalid failure response.")
    if not isinstance(task, str) or (task and not TASK_PATTERN.fullmatch(task)):
        raise GatewayResultError("Staging gateway returned an invalid task label.")
    if not isinstance(diagnostic, str) or (diagnostic and not CODE_PATTERN.fullmatch(diagnostic)):
        raise GatewayResultError("Staging gateway returned an invalid diagnostic class.")
    if not isinstance(controller, str) or (
        controller not in {"", "unknown"} and not CONTROLLER_PATTERN.fullmatch(controller)
    ):
        raise GatewayResultError("Staging gateway returned an invalid controller version.")
    return code, task, diagnostic, controller


def failure_message(code: str, task: str = "", diagnostic: str = "", controller: str = "") -> str:
    task_suffix = f" at reviewed task: {task}" if task else ""
    diagnostic_suffix = f"; diagnostic={diagnostic}" if diagnostic else ""
    controller_suffix = f"; controller={controller}" if controller else ""
    return f"Staging gateway failed with {code}{task_suffix}{diagnostic_suffix}{controller_suffix}"


def evaluate_gateway_result(result: object, status: int, operation: str) -> GatewayOutcome:
    if status == 0 and result == {"ok": True, "operation": operation}:
        return GatewayOutcome(ok=True, operation=operation)
    code, task, diagnostic, controller = validate_failure_response(result)
    return GatewayOutcome(
        ok=False,
        operation=operation,
        code=code,
        message=failure_message(code, task, diagnostic, controller),
    )


def read_result(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GatewayResultError("Staging gateway returned an invalid failure response.") from error


def append_summary(summary_file: Path | None, line: str) -> None:
    if summary_file is None:
        return
    with summary_file.open("a", encoding="utf-8") as output:
        output.write(line.rstrip() + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("check", "apply"), required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--status", type=int, required=True)
    parser.add_argument("--retry-code", default="")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--retry-delay-seconds", type=int, default=0)
    parser.add_argument("--summary-file", type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    try:
        outcome = evaluate_gateway_result(read_result(arguments.result), arguments.status, arguments.operation)
    except GatewayResultError as error:
        raise SystemExit(str(error)) from error

    if outcome.ok:
        if arguments.attempt > 1:
            append_summary(
                arguments.summary_file,
                f"- Staging gateway {arguments.operation} succeeded on attempt "
                f"{arguments.attempt}/{arguments.max_attempts}.",
            )
        return

    retryable = bool(arguments.retry_code) and outcome.code == arguments.retry_code
    if retryable and arguments.attempt < arguments.max_attempts:
        append_summary(
            arguments.summary_file,
            f"- Staging gateway {arguments.operation} attempt "
            f"{arguments.attempt}/{arguments.max_attempts} returned `{outcome.code}`; "
            f"retrying after {arguments.retry_delay_seconds}s for reviewed infra commit propagation.",
        )
        print(
            f"{outcome.message}; retrying attempt {arguments.attempt + 1}/"
            f"{arguments.max_attempts} after {arguments.retry_delay_seconds}s.",
        )
        raise SystemExit(RETRY_EXIT_CODE)

    if retryable:
        append_summary(
            arguments.summary_file,
            f"- Staging gateway {arguments.operation} attempt "
            f"{arguments.attempt}/{arguments.max_attempts} still returned `{outcome.code}`; "
            "reviewed infra commit propagation did not complete in the retry window.",
        )
        raise SystemExit(
            f"{outcome.message} after {arguments.attempt}/{arguments.max_attempts} attempts; "
            "reviewed infra commit propagation did not complete in the retry window."
        )
    raise SystemExit(outcome.message)


if __name__ == "__main__":
    main()
