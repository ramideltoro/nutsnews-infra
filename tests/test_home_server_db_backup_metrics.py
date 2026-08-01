#!/usr/bin/env python3
"""Regression coverage for truthful home-server database backup metrics."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/home_server_db_backup_metrics.py"
SPEC = importlib.util.spec_from_file_location("home_server_db_backup_metrics", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the home-server DB backup metrics module.")
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


def arguments(
    directory: str,
    *,
    result: str,
    run_timestamp: int | None,
    generated_at: int,
) -> argparse.Namespace:
    return argparse.Namespace(
        result=result,
        run_timestamp=run_timestamp,
        generated_at=generated_at,
        duration_seconds=7.0,
        size_bytes=1024.0,
        file_count=3.0,
        cloud_available_count=4.0,
        latest_cloud_backup_timestamp=run_timestamp,
        next_run_timestamp=generated_at + 3600,
        timer_enabled=True,
        timer_active=True,
        service_active=False,
        state_file=Path(directory) / "state.json",
        output=Path(directory) / "nutsnews_db_backup.prom",
    )


def sample(rendered: str, name: str) -> str | None:
    prefix = f"{name} "
    return next((line for line in rendered.splitlines() if line.startswith(prefix)), None)


class HomeServerDbBackupMetricsTests(unittest.TestCase):
    def test_success_then_failure_preserves_success_and_advances_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            success = arguments(directory, result="success", run_timestamp=100, generated_at=110)
            self.assertEqual(METRICS.run(success), 0)
            first = success.output.read_text(encoding="utf-8")
            self.assertEqual(sample(first, "nutsnews_db_backup_last_success"), "nutsnews_db_backup_last_success 1")
            self.assertEqual(
                sample(first, "nutsnews_db_backup_last_success_timestamp_seconds"),
                "nutsnews_db_backup_last_success_timestamp_seconds 100",
            )

            failure = arguments(directory, result="failure", run_timestamp=200, generated_at=210)
            self.assertEqual(METRICS.run(failure), 1)
            second = failure.output.read_text(encoding="utf-8")
            state = json.loads(failure.state_file.read_text(encoding="utf-8"))

        self.assertEqual(sample(second, "nutsnews_db_backup_last_success"), "nutsnews_db_backup_last_success 0")
        self.assertEqual(
            sample(second, "nutsnews_db_backup_last_run_timestamp_seconds"),
            "nutsnews_db_backup_last_run_timestamp_seconds 200",
        )
        self.assertEqual(
            sample(second, "nutsnews_db_backup_last_success_timestamp_seconds"),
            "nutsnews_db_backup_last_success_timestamp_seconds 100",
        )
        self.assertEqual(state["last_run_timestamp_seconds"], 200)
        self.assertEqual(state["last_success_timestamp_seconds"], 100)
        self.assertEqual(state["last_result"], "failure")

    def test_first_failure_exposes_success_timestamp_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failure = arguments(directory, result="failure", run_timestamp=300, generated_at=310)
            self.assertEqual(METRICS.run(failure), 1)
            rendered = failure.output.read_text(encoding="utf-8")
            state = json.loads(failure.state_file.read_text(encoding="utf-8"))

        self.assertEqual(
            sample(rendered, "nutsnews_db_backup_last_success_timestamp_seconds_available"),
            "nutsnews_db_backup_last_success_timestamp_seconds_available 0",
        )
        self.assertIsNone(sample(rendered, "nutsnews_db_backup_last_success_timestamp_seconds"))
        self.assertEqual(sample(rendered, "nutsnews_db_backup_last_run_timestamp_seconds"), "nutsnews_db_backup_last_run_timestamp_seconds 300")
        self.assertIsNone(state["last_success_timestamp_seconds"])

    def test_corrupt_state_unavailable_refresh_overwrites_stale_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unavailable = arguments(directory, result="unavailable", run_timestamp=None, generated_at=410)
            unavailable.state_file.write_text("not-json\n", encoding="utf-8")
            unavailable.output.write_text(
                "nutsnews_db_backup_last_success_timestamp_seconds 123\n",
                encoding="utf-8",
            )
            self.assertEqual(METRICS.run(unavailable), 1)
            rendered = unavailable.output.read_text(encoding="utf-8")

        self.assertNotIn(" 123", rendered)
        self.assertEqual(sample(rendered, "nutsnews_db_backup_status_available"), "nutsnews_db_backup_status_available 0")
        self.assertEqual(
            sample(rendered, "nutsnews_db_backup_last_run_timestamp_seconds_available"),
            "nutsnews_db_backup_last_run_timestamp_seconds_available 0",
        )
        self.assertEqual(
            sample(rendered, "nutsnews_db_backup_last_success_timestamp_seconds_available"),
            "nutsnews_db_backup_last_success_timestamp_seconds_available 0",
        )
        self.assertIsNone(sample(rendered, "nutsnews_db_backup_last_run_timestamp_seconds"))
        self.assertIsNone(sample(rendered, "nutsnews_db_backup_last_success_timestamp_seconds"))

    def test_atomic_files_have_bounded_permissions_and_no_environment_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            success = arguments(directory, result="success", run_timestamp=500, generated_at=510)
            previous_secret = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = "postgresql://secret-user:secret-password@example.invalid/db"
            try:
                self.assertEqual(METRICS.run(success), 0)
            finally:
                if previous_secret is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = previous_secret
            rendered = success.output.read_text(encoding="utf-8")
            state = success.state_file.read_text(encoding="utf-8")
            output_mode = success.output.stat().st_mode & 0o777
            state_mode = success.state_file.stat().st_mode & 0o777

        self.assertNotIn("secret-user", rendered + state)
        self.assertNotIn("secret-password", rendered + state)
        self.assertEqual(output_mode, 0o644)
        self.assertEqual(state_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
