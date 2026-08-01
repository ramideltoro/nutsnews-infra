#!/usr/bin/env python3
"""Atomically persist and export truthful home-server DB backup outcomes.

The backup runner should call this only after it knows whether dump, upload, and
remote verification succeeded. Every known attempt advances ``last_run``. Only
a verified success advances ``last_success``; failures preserve the durable
prior success timestamp. ``unavailable`` replaces stale exposition with
explicit availability gauges and never fabricates a timestamp.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn


DEFAULT_OUTPUT = Path("/var/lib/node_exporter/textfile_collector/nutsnews_db_backup.prom")
DEFAULT_STATE = Path("/var/lib/nutsnews-db-backup/metrics-state.json")
SCHEMA_VERSION = 1
RESULTS = {"success", "failure", "unavailable"}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def positive_timestamp(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        fail("timestamps must be integer Unix seconds")
    if parsed <= 0:
        fail("timestamps must be positive Unix seconds")
    return parsed


def nonnegative_number(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        fail("metric values must be numeric")
    if not math.isfinite(parsed) or parsed < 0:
        fail("metric values must be finite and non-negative")
    return parsed


def tri_state(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized == "unavailable":
        return None
    fail("boolean status values must be true, false, or unavailable")


def atomic_write(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_state(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, False
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return {}, False
    for key in ("last_run_timestamp_seconds", "last_success_timestamp_seconds"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            return {}, False
    if raw.get("last_result") not in {"success", "failure"}:
        return {}, False
    return raw, True


def next_state(previous: dict[str, Any], result: str, run_timestamp: int) -> dict[str, Any]:
    previous_run = previous.get("last_run_timestamp_seconds")
    previous_success = previous.get("last_success_timestamp_seconds")
    last_run = max(run_timestamp, previous_run) if isinstance(previous_run, int) else run_timestamp
    if result == "success":
        last_success = max(run_timestamp, previous_success) if isinstance(previous_success, int) else run_timestamp
    else:
        last_success = previous_success if isinstance(previous_success, int) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "last_result": result,
        "last_run_timestamp_seconds": last_run,
        "last_success_timestamp_seconds": last_success,
    }


def number(value: float | int) -> str:
    return f"{value:.15g}"


def metric(lines: list[str], name: str, help_text: str, value: float | int) -> None:
    lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {number(value)}", ""))


def optional_metric(
    lines: list[str],
    name: str,
    help_text: str,
    value: float | int | bool | None,
) -> None:
    metric(lines, f"{name}_available", f"Whether {name} has a trustworthy value.", 1 if value is not None else 0)
    if value is not None:
        metric(lines, name, help_text, int(value) if isinstance(value, bool) else value)


def render_metrics(
    *,
    result: str,
    state: dict[str, Any],
    generated_at: int,
    duration_seconds: float | None = None,
    size_bytes: float | None = None,
    file_count: float | None = None,
    cloud_available_count: float | None = None,
    latest_cloud_backup_timestamp: int | None = None,
    next_run_timestamp: int | None = None,
    timer_enabled: bool | None = None,
    timer_active: bool | None = None,
    service_active: bool | None = None,
) -> str:
    if result not in RESULTS:
        raise ValueError("unsupported backup result")
    known_outcome = result in {"success", "failure"}
    last_run = state.get("last_run_timestamp_seconds") if known_outcome else None
    last_success = state.get("last_success_timestamp_seconds") if known_outcome else None
    lines: list[str] = []
    metric(lines, "nutsnews_db_backup_status_available", "Whether the latest backup outcome is available.", int(known_outcome))
    metric(
        lines,
        "nutsnews_db_backup_metrics_collection_success",
        "Whether this textfile refresh collected a trustworthy backup outcome.",
        int(known_outcome),
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_success",
        "Whether the last database backup attempt succeeded.",
        (result == "success") if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_run_timestamp_seconds",
        "Unix timestamp of the most recent database backup attempt.",
        last_run if isinstance(last_run, int) else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_success_timestamp_seconds",
        "Unix timestamp of the most recent verified successful database backup.",
        last_success if isinstance(last_success, int) else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_duration_seconds",
        "Duration of the latest database backup attempt in seconds.",
        duration_seconds if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_size_bytes",
        "Size of the latest local database backup folder before cleanup.",
        size_bytes if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_last_file_count",
        "Number of files generated by the latest database backup attempt.",
        file_count if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_cloud_available_count",
        "Number of database backup folders visible through the encrypted remote.",
        cloud_available_count if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_latest_cloud_backup_timestamp_seconds",
        "Timestamp parsed from the newest encrypted remote database backup folder.",
        latest_cloud_backup_timestamp if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_next_run_timestamp_seconds",
        "Unix timestamp of the next scheduled database backup attempt.",
        next_run_timestamp if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_timer_enabled",
        "Whether the database backup systemd timer is enabled.",
        timer_enabled if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_timer_active",
        "Whether the database backup systemd timer is active.",
        timer_active if known_outcome else None,
    )
    optional_metric(
        lines,
        "nutsnews_db_backup_service_active",
        "Whether the database backup service is currently running.",
        service_active if known_outcome else None,
    )
    metric(
        lines,
        "nutsnews_db_backup_status_metrics_last_update_timestamp_seconds",
        "Unix timestamp when database backup metrics were last refreshed.",
        generated_at,
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, choices=sorted(RESULTS))
    parser.add_argument("--run-timestamp", type=positive_timestamp)
    parser.add_argument("--generated-at", type=positive_timestamp, default=int(time.time()))
    parser.add_argument("--duration-seconds", type=nonnegative_number)
    parser.add_argument("--size-bytes", type=nonnegative_number)
    parser.add_argument("--file-count", type=nonnegative_number)
    parser.add_argument("--cloud-available-count", type=nonnegative_number)
    parser.add_argument("--latest-cloud-backup-timestamp", type=positive_timestamp)
    parser.add_argument("--next-run-timestamp", type=positive_timestamp)
    parser.add_argument("--timer-enabled", type=tri_state, default=None)
    parser.add_argument("--timer-active", type=tri_state, default=None)
    parser.add_argument("--service-active", type=tri_state, default=None)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.result != "unavailable" and args.run_timestamp is None:
        parser.error("--run-timestamp is required for success and failure outcomes")
    if args.run_timestamp is not None and args.run_timestamp > args.generated_at + 300:
        parser.error("--run-timestamp cannot be more than five minutes in the future")
    return args


def run(args: argparse.Namespace) -> int:
    lock_path = args.state_file.with_name(f"{args.state_file.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous, previous_valid = load_state(args.state_file)
        if args.result == "unavailable":
            state: dict[str, Any] = {}
        else:
            state = next_state(previous if previous_valid else {}, args.result, args.run_timestamp)
            atomic_write(args.state_file, json.dumps(state, indent=2, sort_keys=True) + "\n", mode=0o600)
        rendered = render_metrics(
            result=args.result,
            state=state,
            generated_at=args.generated_at,
            duration_seconds=args.duration_seconds,
            size_bytes=args.size_bytes,
            file_count=args.file_count,
            cloud_available_count=args.cloud_available_count,
            latest_cloud_backup_timestamp=args.latest_cloud_backup_timestamp,
            next_run_timestamp=args.next_run_timestamp,
            timer_enabled=args.timer_enabled,
            timer_active=args.timer_active,
            service_active=args.service_active,
        )
        atomic_write(args.output, rendered, mode=0o644)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return 0 if args.result == "success" else 1


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except (OSError, ValueError, TypeError):
        unavailable = render_metrics(result="unavailable", state={}, generated_at=int(time.time()))
        try:
            atomic_write(args.output, unavailable, mode=0o644)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
