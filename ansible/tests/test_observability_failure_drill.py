#!/usr/bin/env python3
"""Regression tests for the bounded VPS observability failure-drill hook."""

from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock


MODULE_PATH = Path("ansible/roles/vps_service_foundation/files/observability_failure_drill.py")
SPEC = importlib.util.spec_from_file_location("observability_failure_drill", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load observability failure drill hook.")
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


class ObservabilityFailureDrillTests(unittest.TestCase):
    def test_plan_is_non_mutating_and_names_exact_confirmation(self) -> None:
        result = DRILL.plan("alloy-stopped", "123-1", 900)
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(result["target"], "vps.nutsnews.com")
        self.assertEqual(
            result["confirmation_required"],
            "execute-grafana-failure-drill:vps.nutsnews.com:alloy-stopped",
        )

    def test_replace_timestamp_requires_exactly_one_sample(self) -> None:
        payload = (
            b"nutsnews_observability_textfile_collector_success 1\n"
            b"nutsnews_observability_textfile_last_success_timestamp_seconds 500\n"
        )
        rendered = DRILL.replace_success_timestamp(payload, 100)
        self.assertIn(b"last_success_timestamp_seconds 100", rendered)
        with self.assertRaises(RuntimeError):
            DRILL.replace_success_timestamp(b"unrelated 1\n", 100)

    def test_textfile_injection_schedules_failsafe_before_mutation_and_recovers(self) -> None:
        commands: list[list[str]] = []
        timer_active = True

        def fixed_command(argv: list[str], *, check: bool = True) -> CompletedProcess[str]:
            nonlocal timer_active
            commands.append(argv)
            if argv[:3] == ["systemctl", "is-active", "--quiet"]:
                active = timer_active if argv[3] == DRILL.TEXTFILE_TIMER else False
                return CompletedProcess(argv, 0 if active else 3, "", "")
            if argv[:2] == ["systemctl", "stop"] and argv[2] == DRILL.TEXTFILE_TIMER:
                timer_active = False
            if argv[:2] == ["systemctl", "start"] and argv[2] == DRILL.TEXTFILE_TIMER:
                timer_active = True
            if argv[:2] == ["systemctl", "start"] and argv[2] == DRILL.TEXTFILE_SERVICE:
                textfile.write_text(
                    "nutsnews_observability_textfile_collector_success 1\n"
                    f"nutsnews_observability_textfile_last_success_timestamp_seconds {int(time.time())}\n",
                    encoding="utf-8",
                )
            return CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            root.mkdir(mode=0o700)
            textfile = Path(directory) / "nutsnews.prom"
            textfile.write_text(
                "nutsnews_observability_textfile_collector_success 1\n"
                "nutsnews_observability_textfile_last_success_timestamp_seconds 500\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(DRILL, "STATE_ROOT", root),
                mock.patch.object(DRILL, "TEXTFILE", textfile),
                mock.patch.object(DRILL, "require_safe_state_root"),
                mock.patch.object(DRILL, "safe_textfile", side_effect=textfile.lstat),
                mock.patch.object(DRILL, "run_command", side_effect=fixed_command),
                mock.patch.object(DRILL.os, "chown"),
            ):
                injected = DRILL.inject(
                    "textfile-stale",
                    "123-1",
                    "execute-grafana-failure-drill:vps.nutsnews.com:textfile-stale",
                    900,
                )
                recovered = DRILL.recover(
                    "123-1",
                    "recover-grafana-failure-drill:vps.nutsnews.com:textfile-stale",
                )

            state_value = json.loads((root / "123-1/state.json").read_text(encoding="utf-8"))
            mode = stat.S_IMODE((root / "123-1/state.json").stat().st_mode)

        scheduled_at = next(index for index, argv in enumerate(commands) if argv[0] == "systemd-run")
        stopped_at = next(
            index
            for index, argv in enumerate(commands)
            if argv[:3] == ["systemctl", "stop", DRILL.TEXTFILE_TIMER]
        )
        self.assertLess(scheduled_at, stopped_at)
        self.assertEqual(injected["status"], "injected")
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(state_value["status"], "recovered")
        self.assertEqual(mode, 0o600)

    def test_existing_run_id_is_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            (root / "same-run").mkdir()
            with (
                mock.patch.object(DRILL, "STATE_ROOT", root),
                mock.patch.object(DRILL, "require_safe_state_root"),
            ):
                with self.assertRaises(RuntimeError):
                    DRILL.inject(
                        "alloy-stopped",
                        "same-run",
                        "execute-grafana-failure-drill:vps.nutsnews.com:alloy-stopped",
                        900,
                    )

    def test_failed_explicit_recovery_keeps_independent_timer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            run_dir = root / "123-1"
            run_dir.mkdir(mode=0o700)
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "123-1",
                        "drill": "alloy-stopped",
                        "target": "vps.nutsnews.com",
                        "status": "injected",
                    }
                ),
                encoding="utf-8",
            )
            failed_start = CompletedProcess(
                ["systemctl", "start", DRILL.ALLOY_UNIT],
                1,
                "",
                "failed",
            )
            with (
                mock.patch.object(DRILL, "STATE_ROOT", root),
                mock.patch.object(DRILL, "require_safe_state_root"),
                mock.patch.object(DRILL, "run_command", return_value=failed_start),
                mock.patch.object(DRILL, "service_active", return_value=False),
                mock.patch.object(DRILL, "alloy_ready", return_value=False),
                mock.patch.object(DRILL.time, "monotonic", side_effect=[0, 61]),
                mock.patch.object(DRILL, "cancel_recovery_timer") as cancel,
            ):
                with self.assertRaises(RuntimeError):
                    DRILL.recover(
                        "123-1",
                        "recover-grafana-failure-drill:vps.nutsnews.com:alloy-stopped",
                    )

            cancel.assert_not_called()
            state_value = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_value["status"], "recovery-failed")


if __name__ == "__main__":
    unittest.main()
