#!/usr/bin/env python3
"""Validate exact backend drill evidence and emit a value-free local summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path
from typing import Any, NoReturn


SCHEMA_VERSION = 1
BACKEND_REPOSITORY = "ramideltoro/nutsnews-backend"
BACKEND_WORKFLOW = "backend-observability-failure-drills"
BACKEND_WORKFLOW_FILE = f"{BACKEND_WORKFLOW}.yml"
BACKEND_ARTIFACT_NAME = "backend-observability-failure-drill-evidence"
BACKEND_DURATION_SECONDS = 900
BACKEND_DRILLS = {
    "worker-unavailable",
    "rabbitmq-zero-consumer",
    "rabbitmq-growing-dlq",
    "postgres-relay-lag",
    "backend-readiness-failed",
}
REPORT_ACTIONS = ("plan", "inject", "status", "recover")
ROOT_KEYS = {
    "schema_version",
    "safe_metadata_only",
    "workflow",
    "run_id",
    "run_attempt",
    "revision",
    "evidence_id",
    "drill_id",
    "drill",
    "duration_seconds",
    "dry_run",
    "reports",
}
REPORT_KEYS = {
    "schema_version",
    "safe_metadata_only",
    "action",
    "drill",
    "drill_id",
    "status",
    "dry_run",
    "recovery_scheduled",
    "recovery_required",
    "recovered",
    "injected_at_utc",
    "recovery_deadline_utc",
    "duration_seconds",
    "checks",
}
CHECK_KEYS = {"name", "status"}
EVIDENCE_ID = re.compile(r"^nnobs-[0-9]{10,20}-[a-f0-9]{8}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
CHECK_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
MAX_ARCHIVE_BYTES = 1_000_000
MAX_EVIDENCE_BYTES = 262_144


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def exact_keys(value: Any, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        fail(f"{context} does not match the exact allowlisted schema")
    return value


def positive_integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        fail(f"{context} must be a positive integer")
    return value


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("backend evidence contains a duplicate JSON object key")
        result[key] = value
    return result


def read_single_evidence_member(archive_path: Path) -> bytes:
    if (
        archive_path.is_symlink()
        or not archive_path.is_file()
        or archive_path.stat().st_size > MAX_ARCHIVE_BYTES
    ):
        fail("backend evidence artifact must be one bounded regular ZIP file")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "evidence.json":
                fail("backend evidence artifact must contain only evidence.json")
            member = members[0]
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if member.is_dir() or file_type not in {0, stat.S_IFREG}:
                fail("backend evidence artifact member must be a regular file")
            if member.flag_bits & 0x1:
                fail("backend evidence artifact member must not be encrypted")
            if (
                member.file_size > MAX_EVIDENCE_BYTES
                or member.compress_size > MAX_EVIDENCE_BYTES
            ):
                fail("backend evidence payload exceeds the bounded size")
            raw = archive.read(member)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        fail(f"backend evidence artifact is not a readable ZIP file: {exc}")
    if len(raw) > MAX_EVIDENCE_BYTES:
        fail("backend evidence payload exceeds the bounded size")
    return raw


def decode_evidence(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"backend evidence payload is not strict UTF-8 JSON: {exc}")
    return exact_keys(value, ROOT_KEYS, "backend evidence root")


def validate_report(
    report: Any,
    *,
    action: str,
    drill: str,
    evidence_id: str,
) -> None:
    value = exact_keys(report, REPORT_KEYS, f"backend {action} report")
    expected_flags = {
        "plan": (True, False, False, False),
        "inject": (False, True, True, False),
        "status": (False, True, True, False),
        "recover": (False, False, False, True),
    }[action]
    actual_flags = (
        value.get("dry_run"),
        value.get("recovery_scheduled"),
        value.get("recovery_required"),
        value.get("recovered"),
    )
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("safe_metadata_only") is not True
        or value.get("action") != action
        or value.get("drill") != drill
        or value.get("drill_id") != evidence_id
        or value.get("status") != "pass"
        or value.get("duration_seconds") != BACKEND_DURATION_SECONDS
        or actual_flags != expected_flags
    ):
        fail(f"backend {action} report does not match its exact drill binding")

    injected_at = value.get("injected_at_utc")
    recovery_deadline = value.get("recovery_deadline_utc")
    if action == "plan":
        if injected_at is not None or recovery_deadline is not None:
            fail("backend plan report must not contain execution timestamps")
    elif not (
        isinstance(injected_at, str)
        and UTC_TIMESTAMP.fullmatch(injected_at)
        and isinstance(recovery_deadline, str)
        and UTC_TIMESTAMP.fullmatch(recovery_deadline)
    ):
        fail(f"backend {action} report must contain bounded UTC timestamps")

    checks = value.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        fail(f"backend {action} checks must contain 1 to 32 entries")
    seen: set[str] = set()
    for check in checks:
        item = exact_keys(check, CHECK_KEYS, f"backend {action} check")
        name = item.get("name")
        if not isinstance(name, str) or not CHECK_NAME.fullmatch(name) or name in seen:
            fail(f"backend {action} check names must be unique bounded identifiers")
        seen.add(name)
        if item.get("status") not in {"pass", "not_applicable"}:
            fail(f"backend {action} check status is not an allowed successful outcome")


def validate_evidence(
    evidence: dict[str, Any],
    *,
    run_id: int,
    run_attempt: int,
    revision: str,
    evidence_id: str,
    drill: str,
) -> None:
    if (
        evidence.get("schema_version") != SCHEMA_VERSION
        or evidence.get("safe_metadata_only") is not True
        or evidence.get("workflow") != BACKEND_WORKFLOW
        or evidence.get("run_id") != run_id
        or evidence.get("run_attempt") != run_attempt
        or evidence.get("revision") != revision
        or evidence.get("evidence_id") != evidence_id
        or evidence.get("drill_id") != evidence_id
        or evidence.get("drill") != drill
        or evidence.get("duration_seconds") != BACKEND_DURATION_SECONDS
        or evidence.get("dry_run") is not False
    ):
        fail("backend evidence does not match the exact protected-run binding")
    reports = exact_keys(
        evidence.get("reports"), set(REPORT_ACTIONS), "backend evidence reports"
    )
    for action in REPORT_ACTIONS:
        validate_report(
            reports[action], action=action, drill=drill, evidence_id=evidence_id
        )


def build_summary(
    *,
    run_id: int,
    run_attempt: int,
    revision: str,
    evidence_id: str,
    drill: str,
    artifact_id: int,
    artifact_digest: str,
    evidence_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "safe_metadata_only": True,
        "source": {
            "repository": BACKEND_REPOSITORY,
            "workflow": BACKEND_WORKFLOW_FILE,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "revision": revision,
        },
        "binding": {
            "evidence_id": evidence_id,
            "drill": drill,
            "duration_seconds": BACKEND_DURATION_SECONDS,
            "dry_run": False,
        },
        "artifact": {
            "id": artifact_id,
            "name": BACKEND_ARTIFACT_NAME,
            "provider_digest": artifact_digest,
            "evidence_sha256": evidence_sha256,
            "source_run_url": (
                f"https://github.com/{BACKEND_REPOSITORY}/actions/runs/{run_id}"
            ),
        },
        "reports": {action: "pass" for action in REPORT_ACTIONS},
        "result": "pass",
    }


def validate_artifact(
    archive_path: Path,
    *,
    run_id: int,
    run_attempt: int,
    revision: str,
    evidence_id: str,
    drill: str,
    artifact_id: int,
    artifact_digest: str,
) -> dict[str, Any]:
    positive_integer(run_id, "expected backend run_id")
    positive_integer(run_attempt, "expected backend run_attempt")
    positive_integer(artifact_id, "expected backend artifact_id")
    if not COMMIT_SHA.fullmatch(revision):
        fail("expected backend revision must be a full lowercase commit SHA")
    if not EVIDENCE_ID.fullmatch(evidence_id):
        fail("expected backend evidence_id is invalid")
    if drill not in BACKEND_DRILLS:
        fail("expected backend drill is not allowlisted")
    if not SHA256.fullmatch(artifact_digest):
        fail("expected backend artifact digest must be a SHA-256 digest")

    raw = read_single_evidence_member(archive_path)
    downloaded_digest = f"sha256:{hashlib.sha256(archive_path.read_bytes()).hexdigest()}"
    if downloaded_digest != artifact_digest:
        fail("downloaded backend artifact does not match GitHub's SHA-256 digest")
    evidence = decode_evidence(raw)
    validate_evidence(
        evidence,
        run_id=run_id,
        run_attempt=run_attempt,
        revision=revision,
        evidence_id=evidence_id,
        drill=drill,
    )
    return build_summary(
        run_id=run_id,
        run_attempt=run_attempt,
        revision=revision,
        evidence_id=evidence_id,
        drill=drill,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        evidence_sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-zip", required=True, type=Path)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--drill", required=True, choices=sorted(BACKEND_DRILLS))
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = validate_artifact(
        args.artifact_zip,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        revision=args.revision,
        evidence_id=args.evidence_id,
        drill=args.drill,
        artifact_id=args.artifact_id,
        artifact_digest=args.artifact_digest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
