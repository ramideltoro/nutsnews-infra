#!/usr/bin/env python3
"""Static safety tests for the independent synthetic recovery watchdog."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PARENT = (ROOT / ".github/workflows/grafana-failure-drill.yml").read_text(encoding="utf-8")
WATCHDOG = (
    ROOT / ".github/workflows/grafana-synthetic-recovery-watchdog.yml"
).read_text(encoding="utf-8")
SCRIPT = (
    ROOT / "terraform/grafana-cloud/scripts/exercise_synthetic_failure_drill.py"
).read_text(encoding="utf-8")


class SyntheticRecoveryWatchdogWorkflowTests(unittest.TestCase):
    def test_parent_dispatches_and_confirms_independent_arm_before_mutation(self) -> None:
        dispatch = PARENT.index("Dispatch independent synthetic recovery watchdog")
        arm = PARENT.index("Require sanitized armed watchdog handshake before mutation")
        execute = PARENT.index(
            "exercise_synthetic_failure_drill.py execute", arm
        )
        self.assertLess(dispatch, arm)
        self.assertLess(arm, execute)
        synthetic_job = PARENT[PARENT.index("  execute-synthetic:") : PARENT.index("  execute-backend:")]
        for token in (
            "actions: write",
            "gh workflow run grafana-synthetic-recovery-watchdog.yml",
            "validate-watchdog-arm",
            '--watchdog-handshake "$RUNNER_TEMP/synthetic-watchdog/armed.json"',
            '.status == "in_progress" and .conclusion == null',
            "watchdog_snapshot_matches",
        ):
            self.assertIn(token, synthetic_job)

    def test_helper_rechecks_exact_armed_snapshot_before_first_update(self) -> None:
        execute_start = SCRIPT.index("def run_execute(")
        execute_end = SCRIPT.index("def synthetic_client_from_environment", execute_start)
        execute = SCRIPT[execute_start:execute_end]
        self.assertLess(
            execute.index("validate_watchdog_handshake("),
            execute.index("remote = resolve_check(sm_client)"),
        )
        self.assertLess(
            execute.index("watchdog_payload_hmac(base, watchdog_nonce)"),
            execute.index("sm_client.update_check(check_id, changed)"),
        )
        for token in (
            "watchdog_confirmed_armed",
            "watchdog_snapshot_matches",
            "snapshot_payload_hmac_sha256",
            "hmac.compare_digest",
        ):
            self.assertIn(token, SCRIPT)

    def test_watchdog_arm_is_read_only_and_private_snapshot_never_uploads(self) -> None:
        arm_start = SCRIPT.index("def run_watchdog_arm(")
        arm_end = SCRIPT.index("def validate_watchdog_handshake(", arm_start)
        arm = SCRIPT[arm_start:arm_end]
        self.assertIn("private_snapshot_write(snapshot, remote, restore)", arm)
        self.assertNotIn("update_check(", arm)

        upload_paths = re.findall(r"(?m)^\s+path:\s*(.+)$", WATCHDOG)
        self.assertTrue(upload_paths)
        self.assertTrue(all("synthetic-watchdog-private" not in path for path in upload_paths))
        self.assertNotIn("path: ${{ runner.temp }}/synthetic-watchdog-private", WATCHDOG)
        self.assertIn("private_snapshot_uploaded: false", WATCHDOG)
        self.assertIn("exact-remote-check.json", WATCHDOG)

    def test_watchdog_wait_is_bounded_and_always_exact_restores(self) -> None:
        arm = WATCHDOG.index("Fetch exact remote check and arm private recovery snapshot")
        publish = WATCHDOG.index("Publish sanitized armed recovery handshake")
        wait = WATCHDOG.index("Wait bounded time for parent release or termination")
        restore = WATCHDOG.index("Exact-restore and verify after release or bounded wait")
        final = WATCHDOG.index("Finalize sanitized independent recovery evidence")
        self.assertLess(arm, publish)
        self.assertLess(publish, wait)
        self.assertLess(wait, restore)
        self.assertLess(restore, final)
        for token in (
            "deadline=$((SECONDS + 7200))",
            'wait_status="timeout"',
            'wait_status="parent-terminated"',
            "if: always() && steps.arm.outcome == 'success'",
            "exercise_synthetic_failure_drill.py restore",
            "--timeout-seconds 1200",
            "timeout-minutes: 170",
        ):
            self.assertIn(token, WATCHDOG)

    def test_watchdog_validates_exact_parent_and_sanitized_release(self) -> None:
        for token in (
            '(.path == ".github/workflows/grafana-failure-drill.yml")',
            '(.status == "in_progress")',
            '(.head_sha == $head_sha)',
            "request_exact_restore",
            "watchdog_nonce_sha256",
            "parent_head_sha",
            "safe_metadata_only",
            "((keys | sort) == [",
        ):
            self.assertIn(token, WATCHDOG)

    def test_watchdog_does_not_deadlock_parent_apply_concurrency(self) -> None:
        concurrency = WATCHDOG[
            WATCHDOG.index("concurrency:") : WATCHDOG.index("jobs:")
        ]
        self.assertIn("grafana-synthetic-recovery-${{ inputs.synthetic_check }}", concurrency)
        self.assertIn("queue: max", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)
        self.assertNotIn("grafana-cloud-apply", concurrency)
        self.assertIn("Require independent watchdog exact restoration evidence", PARENT)
        self.assertIn('timeout 2700 gh run watch "$WATCHDOG_RUN_ID"', PARENT)

    def test_exact_restore_refuses_unrelated_configuration_drift(self) -> None:
        restore_start = SCRIPT.index("def restore_exact_if_owned(")
        restore_end = SCRIPT.index("def safe_report(", restore_start)
        restore = SCRIPT[restore_start:restore_end]
        self.assertIn("payloads_equal(current, restore)", restore)
        self.assertIn("payloads_equal(current, expected_mutation)", restore)
        self.assertIn("refusing to overwrite", restore)


if __name__ == "__main__":
    unittest.main()
