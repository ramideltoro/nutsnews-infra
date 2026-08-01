#!/usr/bin/env python3
"""Tests for the infra-side backend failure-drill evidence trust boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/validate_backend_drill_evidence.py"
SPEC = importlib.util.spec_from_file_location("validate_backend_drill_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load backend drill evidence validator.")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

RUN_ID = 12345678901
RUN_ATTEMPT = 2
REVISION = "a" * 40
EVIDENCE_ID = "nnobs-12345678901-00000002"
DRILL = "worker-unavailable"
ARTIFACT_ID = 987654321
TIMESTAMP = "2026-08-01T12:34:56Z"


def report(action: str) -> dict[str, object]:
    flags = {
        "plan": (True, False, False, False),
        "inject": (False, True, True, False),
        "status": (False, True, True, False),
        "recover": (False, False, False, True),
    }[action]
    return {
        "schema_version": 1,
        "safe_metadata_only": True,
        "action": action,
        "drill": DRILL,
        "drill_id": EVIDENCE_ID,
        "status": "pass",
        "dry_run": flags[0],
        "recovery_scheduled": flags[1],
        "recovery_required": flags[2],
        "recovered": flags[3],
        "injected_at_utc": None if action == "plan" else TIMESTAMP,
        "recovery_deadline_utc": None if action == "plan" else TIMESTAMP,
        "duration_seconds": 900,
        "checks": [{"name": f"{action}_verified", "status": "pass"}],
    }


def evidence() -> dict[str, object]:
    return {
        "schema_version": 1,
        "safe_metadata_only": True,
        "workflow": "backend-observability-failure-drills",
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "revision": REVISION,
        "evidence_id": EVIDENCE_ID,
        "drill_id": EVIDENCE_ID,
        "drill": DRILL,
        "duration_seconds": 900,
        "dry_run": False,
        "reports": {action: report(action) for action in VALIDATOR.REPORT_ACTIONS},
    }


def write_archive(path: Path, payload: bytes, *, extra_member: bool = False) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evidence.json", payload)
        if extra_member:
            archive.writestr("unapproved.json", b"{}")


def validate(path: Path, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "revision": REVISION,
        "evidence_id": EVIDENCE_ID,
        "drill": DRILL,
        "artifact_id": ARTIFACT_ID,
        "artifact_digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
    }
    arguments.update(overrides)
    return VALIDATOR.validate_artifact(path, **arguments)


class BackendDrillEvidenceValidationTests(unittest.TestCase):
    def archive(self, root: Path, value: dict[str, object]) -> tuple[Path, bytes]:
        raw = (json.dumps(value, sort_keys=True) + "\n").encode()
        path = root / "artifact.zip"
        write_archive(path, raw)
        return path, raw

    def test_exact_evidence_emits_only_reconstructed_value_free_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, raw = self.archive(Path(directory), evidence())
            archive_digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            summary = validate(path)

        self.assertEqual(summary["result"], "pass")
        self.assertEqual(summary["reports"], {action: "pass" for action in VALIDATOR.REPORT_ACTIONS})
        self.assertEqual(summary["source"]["run_id"], RUN_ID)
        self.assertEqual(summary["source"]["run_attempt"], RUN_ATTEMPT)
        self.assertEqual(summary["source"]["revision"], REVISION)
        self.assertEqual(summary["artifact"]["id"], ARTIFACT_ID)
        self.assertEqual(
            summary["artifact"]["provider_digest"],
            archive_digest,
        )
        self.assertEqual(
            summary["artifact"]["evidence_sha256"],
            f"sha256:{hashlib.sha256(raw).hexdigest()}",
        )
        encoded = json.dumps(summary)
        for upstream_detail in (
            "injected_at_utc",
            "recovery_deadline_utc",
            "plan_verified",
            "inject_verified",
            "status_verified",
            "recover_verified",
        ):
            self.assertNotIn(upstream_detail, encoded)

    def test_every_top_level_binding_is_exact(self) -> None:
        mutations = {
            "schema_version": 2,
            "workflow": "other-workflow",
            "run_id": RUN_ID + 1,
            "run_attempt": RUN_ATTEMPT + 1,
            "revision": "c" * 40,
            "evidence_id": "nnobs-12345678901-ffffffff",
            "drill_id": "nnobs-12345678901-ffffffff",
            "drill": "rabbitmq-zero-consumer",
            "duration_seconds": 899,
            "dry_run": True,
        }
        for key, changed in mutations.items():
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                value = evidence()
                value[key] = changed
                path, _ = self.archive(Path(directory), value)
                with self.assertRaises(SystemExit):
                    validate(path)

    def test_root_report_and_check_keys_are_recursively_allowlisted(self) -> None:
        mutations = []
        root_extra = evidence()
        root_extra["unexpected"] = "do-not-copy"
        mutations.append(root_extra)

        report_extra = evidence()
        report_extra["reports"]["inject"]["unexpected"] = "do-not-copy"
        mutations.append(report_extra)

        check_extra = evidence()
        check_extra["reports"]["status"]["checks"][0]["unexpected"] = "do-not-copy"
        mutations.append(check_extra)

        reports_extra = evidence()
        reports_extra["reports"]["unexpected"] = report("plan")
        mutations.append(reports_extra)

        for index, value in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                path, _ = self.archive(Path(directory), value)
                with self.assertRaises(SystemExit):
                    validate(path)

    def test_report_identity_flags_and_outcomes_are_exact(self) -> None:
        for field, changed in (
            ("action", "status"),
            ("drill", "rabbitmq-zero-consumer"),
            ("drill_id", "nnobs-12345678901-ffffffff"),
            ("status", "fail"),
            ("dry_run", True),
            ("recovered", True),
            ("duration_seconds", 60),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                value = evidence()
                value["reports"]["inject"][field] = changed
                path, _ = self.archive(Path(directory), value)
                with self.assertRaises(SystemExit):
                    validate(path)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        value = json.dumps(evidence(), sort_keys=True)
        raw = value.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.zip"
            write_archive(path, raw)
            with self.assertRaises(SystemExit):
                validate(path)

    def test_artifact_must_contain_one_exact_regular_member(self) -> None:
        raw = json.dumps(evidence()).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.zip"
            write_archive(path, raw, extra_member=True)
            with self.assertRaises(SystemExit):
                validate(path)

    def test_downloaded_archive_must_match_provider_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.archive(Path(directory), evidence())
            with self.assertRaises(SystemExit):
                validate(path, artifact_digest="sha256:" + "f" * 64)

    def test_expected_api_metadata_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.archive(Path(directory), evidence())
            for field, value in (
                ("run_id", True),
                ("run_attempt", 0),
                ("revision", "main"),
                ("evidence_id", "bad"),
                ("drill", "arbitrary"),
                ("artifact_id", -1),
                ("artifact_digest", "sha256:bad"),
            ):
                with self.subTest(field=field), self.assertRaises(SystemExit):
                    validate(path, **{field: value})


if __name__ == "__main__":
    unittest.main()
