#!/usr/bin/env python3
"""Classify GitHub PR check output without mistaking transient API failures for failed checks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TRANSIENT_ERROR_PATTERNS = (
    re.compile(r"\b(?:408|425|429|5[0-9]{2})\b"),
    re.compile(r"\bbad gateway\b"),
    re.compile(r"\bservice unavailable\b"),
    re.compile(r"\bgateway timeout\b"),
    re.compile(r"\binternal server error\b"),
    re.compile(r"\brequest timeout\b"),
    re.compile(r"\btimed out\b"),
    re.compile(r"\btimeout\b.*\b(?:awaiting|waiting|response|handshake)\b"),
    re.compile(r"\b(?:tls|ssl)\b.*\bhandshake\b"),
    re.compile(r"\bconnection\b.*\b(?:reset|refused|closed|aborted)\b"),
    re.compile(r"\btemporary failure\b"),
    re.compile(r"\bnetwork is unreachable\b"),
    re.compile(r"\bunexpected eof\b"),
    re.compile(r"\bstream error\b"),
    re.compile(r"\bhttp/2\b.*\b(?:error|goaway)\b"),
)


class CheckClassificationError(ValueError):
    """Raised when a check poll failed for a non-transient reason."""


def is_transient_error(message: str) -> bool:
    normalized = message.casefold()
    return any(pattern.search(normalized) for pattern in TRANSIENT_ERROR_PATTERNS)


def classify_checks(checks_text: str, error_text: str, exit_code: int) -> str:
    """Return passed, failed, waiting, or retry for one `gh pr checks` poll."""

    if checks_text.strip():
        try:
            checks: Any = json.loads(checks_text)
        except json.JSONDecodeError as error:
            raise CheckClassificationError(f"GitHub check output was not valid JSON: {error}") from error

        if not isinstance(checks, list):
            raise CheckClassificationError("GitHub check output must be a JSON list.")
        if not checks:
            return "waiting"
        if not all(isinstance(check, dict) for check in checks):
            raise CheckClassificationError("Every GitHub check result must be a JSON object.")

        buckets = {str(check.get("bucket", "")).casefold() for check in checks}
        if buckets & {"fail", "cancel"}:
            return "failed"
        if buckets <= {"pass", "skipping"}:
            return "passed"
        return "waiting"

    normalized_error = error_text.casefold()
    if "no checks reported" in normalized_error:
        return "waiting"
    if exit_code != 0 and is_transient_error(error_text):
        return "retry"
    if exit_code == 0 and not error_text.strip():
        return "waiting"

    detail = error_text.strip() or f"`gh pr checks` exited {exit_code} without output."
    raise CheckClassificationError(detail)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks-file", required=True, type=Path)
    parser.add_argument("--error-file", required=True, type=Path)
    parser.add_argument("--exit-code", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state = classify_checks(
            args.checks_file.read_text(encoding="utf-8"),
            args.error_file.read_text(encoding="utf-8"),
            args.exit_code,
        )
    except CheckClassificationError as error:
        print(error, file=sys.stderr)
        return 2

    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
