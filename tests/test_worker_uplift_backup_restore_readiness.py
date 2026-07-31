"""Focused tests for worker-uplift backup and restore readiness evidence."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_worker_uplift_backup_restore_readiness.py"
SPEC = importlib.util.spec_from_file_location("backup_restore_readiness_validator", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class BackupRestoreReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(VALIDATOR.READINESS_PATH.read_text(encoding="utf-8"))

    def errors_for(self, mutate) -> list[str]:
        document = copy.deepcopy(self.document)
        mutate(document)
        return VALIDATOR.validate(document)

    def test_checked_in_evidence_passes(self) -> None:
        self.assertEqual(VALIDATOR.validate(self.document), [])

    def test_rejects_production_restore_target(self) -> None:
        errors = self.errors_for(
            lambda data: data["guardrails"].__setitem__("production_data_path_used_as_restore_target", True)
        )
        self.assertTrue(any("production_data_path_used_as_restore_target" in error for error in errors))

    def test_rejects_missing_inventory_disposition(self) -> None:
        errors = self.errors_for(lambda data: data["inventory"].pop())
        self.assertTrue(any("inventory is incomplete" in error for error in errors))

    def test_rejects_not_configured_control(self) -> None:
        errors = self.errors_for(
            lambda data: data["control_status"].__setitem__("definition_export", "not_configured")
        )
        self.assertTrue(any("recovery controls" in error for error in errors))

    def test_rejects_unverified_message_transfer(self) -> None:
        errors = self.errors_for(
            lambda data: data["evidence"]["rabbitmq_stopped_volume_restore"]["result"].__setitem__(
                "representative_message_transfer_status", "skipped"
            )
        )
        self.assertTrue(any("message_transfer_status" in error for error in errors))

    def test_rejects_private_runtime_path(self) -> None:
        errors = self.errors_for(
            lambda data: data["evidence"]["postgresql"]["result"].__setitem__(
                "unsafe_location", "/var/lib/example"
            )
        )
        self.assertTrue(any("forbidden private" in error for error in errors))

    def test_rejects_non_isolated_postgresql_target(self) -> None:
        errors = self.errors_for(
            lambda data: data["evidence"]["postgresql"]["result"].__setitem__("isolated_target", False)
        )
        self.assertTrue(any("must be isolated" in error for error in errors))

    def test_rejects_skipped_stage(self) -> None:
        def mutate(data):
            result = data["evidence"]["rabbitmq_stopped_volume_restore"]["result"]
            result["probed_stages"].remove("publication")
            result["skipped_stages"] = ["publication"]

        errors = self.errors_for(mutate)
        self.assertTrue(any("stage coverage incomplete" in error for error in errors))
        self.assertTrue(any("must not skip stages" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
