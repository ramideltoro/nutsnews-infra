#!/usr/bin/env python3
"""Validate manual VPS backup workflows stay narrow."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


WORKFLOWS = [
    {
        "path": Path(".github/workflows/run-vps-backup.yml"),
        "name": "Run VPS Backup",
        "service_env": "BACKUP_SERVICE",
        "service_name": "nutsnews-restic-backup.service",
        "start_line": "sudo -n /bin/systemctl start ${BACKUP_SERVICE}",
        "status_file_env": "BACKUP_STATUS_FILE",
        "success_field": "last_backup.status",
    },
    {
        "path": Path(".github/workflows/verify-vps-backup.yml"),
        "name": "Verify VPS Backup",
        "service_env": "VERIFY_SERVICE",
        "service_name": "nutsnews-restic-verify.service",
        "start_line": "sudo -n /bin/systemctl start ${VERIFY_SERVICE}",
        "status_file_env": "BACKUP_STATUS_FILE",
        "success_field": "last_check.status",
    },
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def load_collector():
    path = Path("ansible/roles/vps_service_foundation/files/ops_portal_collector.py").resolve()
    spec = importlib.util.spec_from_file_location("ops_portal_collector_under_test", path)
    require(spec is not None and spec.loader is not None, "Could not load ops portal collector module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_backup_runner():
    path = Path("ansible/roles/vps_service_foundation/files/vps_restic_backup.py").resolve()
    spec = importlib.util.spec_from_file_location("vps_restic_backup_under_test", path)
    require(spec is not None and spec.loader is not None, "Could not load VPS restic backup module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_collector_run(argv: list[str], timeout: int = 8) -> dict[str, object]:
    if argv[:1] == ["du"]:
        return {"ok": True, "stdout": "0\t/tmp/nutsnews-backups\n", "stderr": "", "returncode": 0}
    if argv[:2] == ["systemctl", "show"]:
        return {
            "ok": True,
            "stdout": "\n".join(
                [
                    "NextElapseUSecRealtime=2026-07-09 03:25:00 UTC",
                    "LastTriggerUSec=2026-07-08 03:25:00 UTC",
                    "Result=success",
                    "ActiveState=active",
                    "SubState=waiting",
                ]
            ),
            "stderr": "",
            "returncode": 0,
        }
    if argv[:2] == ["systemctl", "is-active"]:
        return {"ok": True, "stdout": "inactive\n", "stderr": "", "returncode": 0}
    if argv[:2] == ["systemctl", "is-enabled"]:
        return {"ok": True, "stdout": "static\n", "stderr": "", "returncode": 0}
    return {"ok": False, "stdout": "", "stderr": f"Unexpected command: {argv}", "returncode": 1}


def stale_restic_timestamp() -> str:
    value = datetime.now(timezone.utc) - timedelta(seconds=120)
    return value.strftime("%Y-%m-%dT%H:%M:%S") + ".162710227Z"


def backup_status_fixture(snapshot_time: str) -> dict[str, object]:
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "schema_version": 1,
        "updated_at": checked_at,
        "enabled": True,
        "configured": True,
        "status": "success",
        "repository": "rclone:nutsnews-onedrive:nutsnews-backups/vps",
        "rclone_remote": "nutsnews-onedrive",
        "latest_snapshot": {
            "id": "c53dda9300000000000000000000000000000000000000000000000000000000",
            "short_id": "c53dda93",
            "time": snapshot_time,
            "hostname": "vps.nutsnews.com",
            "paths": ["/opt/nutsnews", "/etc/nutsnews"],
        },
        "latest_snapshot_age_seconds": 38,
        "latest_status": "fresh",
        "last_backup": {"status": "success", "finished_at": checked_at, "error": ""},
        "last_prune": {"status": "success", "finished_at": checked_at, "error": ""},
        "last_check": {
            "status": "success",
            "finished_at": checked_at,
            "latest_snapshot_id": "c53dda93",
            "latest_snapshot_time": snapshot_time,
            "error": "",
        },
        "stale_after_seconds": 60,
        "stale_after_hours": 1,
        "verify_stale_after_seconds": 691200,
        "verify_stale_after_hours": 192,
        "timer_active": "active",
        "verify_timer_active": "active",
        "missing_configuration": [],
        "backup_paths": ["/opt/nutsnews", "/etc/nutsnews"],
        "missing_paths": [],
    }


def validate_collector_recomputes_backup_freshness() -> None:
    collector = load_collector()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        status_file = tmp_path / "backup-status.json"
        status_file.write_text(json.dumps(backup_status_fixture(stale_restic_timestamp())), encoding="utf-8")

        collector.BACKUP_STATUS_FILE = status_file
        collector.BACKUPS_DIR = tmp_path / "backups"
        collector.BACKUPS_DIR.mkdir()
        collector.run = fake_collector_run

        backups = collector.backup_state()

    require(backups.get("latest_status") == "stale", "Collector must recompute frozen backup age as stale.")
    require(backups.get("latest_snapshot_age_seconds") != 38, "Collector must not preserve runner-written snapshot age.")
    require(
        int(backups.get("latest_snapshot_age_seconds") or 0) > int(backups.get("stale_after_seconds") or 0),
        "Recomputed backup age must exceed stale_after_seconds.",
    )

    alerts = collector.alert_state({}, {}, [], backups, {})
    require(
        any(alert.get("level") == "critical" and "snapshot is stale" in alert.get("message", "") for alert in alerts),
        "Stale recomputed backup age must create a critical alert for email reporting.",
    )

    rendered = json.dumps(backups, sort_keys=True)
    require(backups.get("status") == "success", "Successful backup status must remain visible.")
    for forbidden in ("RESTIC_PASSWORD", "RCLONE_CONFIG", "password=", "token=", "authorization="):
        require(forbidden not in rendered, f"Backup status must not expose {forbidden}.")


def validate_backup_alert_semantics() -> None:
    collector = load_collector()
    backup_runner = load_backup_runner()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    latest_time = now.isoformat()
    backups = backup_status_fixture(latest_time)
    backups["latest_snapshot_age_seconds"] = int(30 * 3600 * 0.797)
    backups["last_check"] = {
        "status": "success",
        "finished_at": (now - timedelta(days=1)).isoformat(),
        "latest_snapshot_id": "older123",
        "latest_snapshot_time": (now - timedelta(days=1, hours=1)).isoformat(),
        "error": "",
    }

    verification = collector.backup_verification_status(backups)
    require(
        verification.get("status") == "latest_unverified",
        "A new daily snapshot must remain visibly different from the last verified snapshot.",
    )
    require(verification.get("policy_status") == "pending", "A new daily snapshot must be pending within policy.")
    require(verification.get("pending") is True, "Pending verification must stay visible in portal status.")
    require(verification.get("overdue") is False, "Expected weekly verification wait must not be overdue.")
    require(verification.get("deadline_at") != "unknown", "Pending verification must expose its policy deadline.")
    runner_verification = backup_runner.verification_status(backups)
    require(
        runner_verification.get("status") == "latest_unverified"
        and runner_verification.get("policy_status") == "pending",
        "Backup runner and collector must agree on the visible mismatch and pending policy.",
    )
    backups["latest_snapshot_verification"] = verification

    alerts = collector.alert_state({}, {}, [], backups, {})
    alert_ids = {item.get("id") for item in alerts}
    require("backup.verification_overdue" not in alert_ids, "Expected weekly verification wait must not alert.")
    require(
        not any("has not been verified" in item.get("message", "") for item in alerts),
        "A newer daily snapshot must not immediately emit repetitive unverified email noise.",
    )

    gibibyte = 1024**3
    collector.directory_size_bytes = lambda _path: gibibyte
    providers = collector.local_usage_providers(
        {
            "cpu_percent": 5,
            "memory": {"used_bytes": 2 * gibibyte, "total_bytes": 8 * gibibyte},
            "swap": {"status": "enabled", "used_bytes": 0, "total_bytes": 2 * gibibyte},
            "disk": {"used_bytes": 20 * gibibyte, "total_bytes": 80 * gibibyte},
        },
        {"available": True},
        {**backups, "enabled": True, "size_bytes": gibibyte},
    )
    backup_provider = next(item for item in providers if item.get("key") == "backup_storage")
    require(
        backup_provider.get("platform") == "Backup Local Cache",
        "Backup provider must describe measurable local cache.",
    )
    require(
        {metric.get("unit") for metric in backup_provider.get("metrics", [])} == {"GiB"},
        "Backup free-tier metrics must use measurable GiB capacity only.",
    )
    require(
        not any(metric.get("key") == "latest_snapshot_age_hours" for metric in backup_provider.get("metrics", [])),
        "Snapshot age must not be treated as quota consumption.",
    )
    require(
        not collector.free_tier_alerts({"providers": [backup_provider]}),
        "A fresh snapshot at 79.7% of its freshness window must not create a storage-quota warning.",
    )

    stale_case = backup_status_fixture(latest_time)
    stale_case["last_check"] = {
        "status": "success",
        "finished_at": (now - timedelta(days=9)).isoformat(),
        "latest_snapshot_id": "older123",
        "latest_snapshot_time": (now - timedelta(days=9)).isoformat(),
        "error": "",
    }
    stale_verification = collector.backup_verification_status(stale_case)
    require(
        stale_verification.get("status") == "latest_unverified"
        and stale_verification.get("policy_status") == "overdue",
        "An older checked snapshot beyond 192 hours must be visibly unverified and overdue.",
    )
    stale_case["latest_snapshot_verification"] = stale_verification
    stale_alerts = collector.alert_state({}, {}, [], stale_case, {})
    require(
        any(item.get("id") == "backup.verification_overdue" for item in stale_alerts),
        "Overdue verification must keep its warning alert.",
    )

    never_checked_case = backup_status_fixture((now - timedelta(days=9)).isoformat())
    never_checked_case["last_check"] = {"status": "never"}
    never_checked_verification = collector.backup_verification_status(never_checked_case)
    require(
        never_checked_verification.get("status") == "latest_unverified"
        and never_checked_verification.get("policy_status") == "overdue"
        and never_checked_verification.get("overdue") is True,
        "A snapshot that has never been checked by the policy deadline must be overdue.",
    )
    never_checked_case["latest_snapshot_verification"] = never_checked_verification
    never_checked_alerts = collector.alert_state({}, {}, [], never_checked_case, {})
    require(
        any(item.get("id") == "backup.verification_overdue" for item in never_checked_alerts),
        "Never-checked overdue verification must alert.",
    )

    failed_case = backup_status_fixture(latest_time)
    failed_case["last_check"] = {"status": "failed", "finished_at": now.isoformat(), "error": "safe failure"}
    failed_case["latest_snapshot_verification"] = collector.backup_verification_status(failed_case)
    failed_alerts = collector.alert_state({}, {}, [], failed_case, {})
    require(
        any(item.get("id") == "backup.verification_failed" for item in failed_alerts),
        "Failed verification must keep its warning alert.",
    )

    inactive_case = backup_status_fixture(latest_time)
    inactive_case["verify_timer_active"] = "inactive"
    inactive_case["latest_snapshot_verification"] = collector.backup_verification_status(inactive_case)
    inactive_alerts = collector.alert_state({}, {}, [], inactive_case, {})
    require(
        any(item.get("id") == "backup.verification_timer_inactive" for item in inactive_alerts),
        "Inactive verification timer must keep its warning alert.",
    )


def validate_backup_runner_error_lifecycle() -> None:
    backup_runner = load_backup_runner()
    env_keys = [
        "NUTSNEWS_BACKUP_ENABLED",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD_FILE",
        "RCLONE_CONFIG",
        "NUTSNEWS_BACKUP_RCLONE_REMOTE",
        "NUTSNEWS_BACKUP_STALE_AFTER_HOURS",
        "NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS",
    ]
    previous_env = {key: os.environ.get(key) for key in env_keys}

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        protected_path = tmp_path / "protected"
        protected_path.mkdir()
        password_file = tmp_path / "restic-password"
        password_file.write_text("safe-test-password\n", encoding="utf-8")
        rclone_config = tmp_path / "rclone.conf"
        rclone_config.write_text("[safe-test-remote]\n", encoding="utf-8")
        paths_file = tmp_path / "paths.txt"
        paths_file.write_text(f"{protected_path}\n", encoding="utf-8")

        os.environ.update(
            {
                "NUTSNEWS_BACKUP_ENABLED": "true",
                "RESTIC_REPOSITORY": "rclone:nutsnews-onedrive:nutsnews-backups/vps",
                "RESTIC_PASSWORD_FILE": str(password_file),
                "RCLONE_CONFIG": str(rclone_config),
                "NUTSNEWS_BACKUP_RCLONE_REMOTE": "nutsnews-onedrive",
                "NUTSNEWS_BACKUP_STALE_AFTER_HOURS": "30",
                "NUTSNEWS_BACKUP_VERIFY_STALE_AFTER_HOURS": "192",
            }
        )

        backup_runner.STATUS_FILE = tmp_path / "backup-status.json"
        backup_runner.STATE_DIR = tmp_path / "state"
        backup_runner.STATE_DIR.mkdir()
        backup_runner.LOG_FILE = tmp_path / "restic-backup.log"
        backup_runner.PATHS_FILE = paths_file
        backup_runner.EXCLUDES_FILE = tmp_path / "excludes.txt"

        snapshot_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        snapshots = [
            {
                "id": "c53dda9300000000000000000000000000000000000000000000000000000000",
                "short_id": "c53dda93",
                "time": snapshot_time,
                "hostname": "vps.nutsnews.com",
                "paths": [str(protected_path)],
            }
        ]
        state = {"backup_failures": 1, "verify_failures": 1}

        def fake_restic_run(argv: list[str], *, env: dict[str, str], timeout: int | None = None) -> dict[str, object]:
            del env, timeout
            if argv[:2] == ["restic", "snapshots"]:
                return {
                    "ok": True,
                    "stdout": json.dumps(snapshots),
                    "stderr": "",
                    "returncode": 0,
                    "duration_seconds": 0.01,
                }
            if argv[:2] == ["restic", "backup"]:
                if state["backup_failures"]:
                    state["backup_failures"] -= 1
                    return {
                        "ok": False,
                        "stdout": "",
                        "stderr": "repository token=sensitive-test-value temporarily unavailable",
                        "returncode": 1,
                        "duration_seconds": 0.02,
                    }
                return {
                    "ok": True,
                    "stdout": (
                        '{"message_type":"summary","snapshot_id":"c53dda93",'
                        '"files_new":1,"files_changed":0,"files_unmodified":2,'
                        '"dirs_new":0,"data_added":512,"total_bytes_processed":1024}\n'
                    ),
                    "stderr": "",
                    "returncode": 0,
                    "duration_seconds": 0.03,
                }
            if argv[:2] == ["restic", "forget"]:
                return {"ok": True, "stdout": "[]", "stderr": "", "returncode": 0, "duration_seconds": 0.01}
            if argv[:2] == ["restic", "ls"]:
                return {
                    "ok": True,
                    "stdout": '{"struct_type":"snapshot"}\n{"name":"safe-file"}\n',
                    "stderr": "",
                    "returncode": 0,
                    "duration_seconds": 0.01,
                }
            if argv[:2] == ["restic", "check"]:
                if state["verify_failures"]:
                    state["verify_failures"] -= 1
                    return {
                        "ok": False,
                        "stdout": "",
                        "stderr": "check failed with password: sensitive-test-value",
                        "returncode": 1,
                        "duration_seconds": 0.02,
                    }
                return {"ok": True, "stdout": "no errors were found", "stderr": "", "returncode": 0, "duration_seconds": 0.02}
            return {"ok": False, "stdout": "", "stderr": f"unexpected command {argv}", "returncode": 99, "duration_seconds": 0}

        backup_runner.run = fake_restic_run

        require(backup_runner.handle_backup() == 1, "Initial failed backup should fail.")
        failed_backup_status = json.loads(backup_runner.STATUS_FILE.read_text(encoding="utf-8"))
        require(failed_backup_status.get("last_error"), "Failed backup must set an active last_error.")
        require(failed_backup_status.get("last_error_at"), "Failed backup must timestamp the active last_error.")
        require(
            failed_backup_status.get("last_error_source") == "restic backup",
            "Failed backup must identify the active error source.",
        )
        rendered_failure = json.dumps(failed_backup_status)
        require("sensitive-test-value" not in rendered_failure, "Backup status must redact sensitive failure text.")
        require(failed_backup_status.get("last_backup", {}).get("error"), "Per-run backup failure detail must remain.")

        require(backup_runner.handle_backup() == 0, "Successful backup after failure should pass.")
        successful_backup_status = json.loads(backup_runner.STATUS_FILE.read_text(encoding="utf-8"))
        require(successful_backup_status.get("last_error") == "", "Successful backup must clear active last_error.")
        require("last_error_at" not in successful_backup_status, "Successful backup must clear active error timestamp.")
        require(
            successful_backup_status.get("last_backup", {}).get("status") == "success",
            "Successful backup result must remain visible.",
        )
        resolved_errors = successful_backup_status.get("resolved_errors")
        require(isinstance(resolved_errors, list) and resolved_errors, "Successful backup must preserve resolved history.")
        require(
            resolved_errors[-1].get("resolved_by") == "successful backup and prune",
            "Resolved backup error must record how it was cleared.",
        )
        require(
            "sensitive-test-value" not in json.dumps(successful_backup_status),
            "Resolved backup history must remain redacted.",
        )

        require(backup_runner.handle_verify_latest() == 1, "Initial failed verification should fail.")
        failed_verify_status = json.loads(backup_runner.STATUS_FILE.read_text(encoding="utf-8"))
        require(failed_verify_status.get("last_error_source") == "restic check", "Verify failure source must be active.")
        require(failed_verify_status.get("last_check", {}).get("error"), "Per-run verify failure detail must remain.")

        require(backup_runner.handle_verify_latest() == 0, "Successful verification after failure should pass.")
        successful_verify_status = json.loads(backup_runner.STATUS_FILE.read_text(encoding="utf-8"))
        require(successful_verify_status.get("last_error") == "", "Successful verify must clear active last_error.")
        require(
            successful_verify_status.get("last_check", {}).get("status") == "success",
            "Successful verify result must remain visible.",
        )
        verify_resolved = successful_verify_status.get("resolved_errors")
        require(isinstance(verify_resolved, list) and len(verify_resolved) >= 2, "Verify success must append history.")
        require(
            verify_resolved[-1].get("resolved_by") == "successful latest snapshot verification",
            "Resolved verify error must record how it was cleared.",
        )
        require(
            "sensitive-test-value" not in json.dumps(successful_verify_status),
            "Resolved verify history must remain redacted.",
        )

    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def validate_workflow(item: dict[str, object]) -> None:
    path = item["path"]
    assert isinstance(path, Path)
    text = path.read_text(encoding="utf-8")
    name = str(item["name"])
    service_env = str(item["service_env"])
    service_name = str(item["service_name"])
    start_line = str(item["start_line"])

    require(f"name: {name}" in text, f"{path}: unexpected workflow name.")
    require(re.search(r"(?m)^  workflow_dispatch:\s*$", text) is not None, f"{path}: must be manual-only.")
    require("pull_request:" not in text, f"{path}: must not run on pull_request.")
    require("push:" not in text, f"{path}: must not run on push.")
    require("schedule:" not in text, f"{path}: must not run on a schedule.")
    require("inputs:" not in text, f"{path}: must not accept dispatch inputs.")
    require("environment: production-vps" in text, f"{path}: must use the production-vps environment.")
    require("NUTSNEWS_VPS_SSH_PRIVATE_KEY" in text, f"{path}: must use the existing SSH private key secret.")
    require("NUTSNEWS_VPS_KNOWN_HOSTS" in text, f"{path}: must use the existing known_hosts secret.")
    require("VPS_USER: nutsnews_ops" in text, f"{path}: must connect as nutsnews_ops.")
    require(f"{service_env}: {service_name}" in text, f"{path}: unexpected systemd service target.")
    require(start_line in text, f"{path}: must start only the fixed systemd unit.")
    require(str(item["status_file_env"]) in text, f"{path}: must read the fixed backup status file.")
    require(str(item["success_field"]) in text, f"{path}: must validate the expected fixed status field.")
    require("${{ inputs." not in text, f"{path}: must not interpolate dispatch inputs.")
    require("bash -s" not in text, f"{path}: must not stream arbitrary shell over SSH.")
    require("sudo -n /bin/bash" not in text, f"{path}: must not start a remote shell with sudo.")
    require("sudo -n bash" not in text, f"{path}: must not start a remote shell with sudo.")
    require("set -x" not in text, f"{path}: must not enable shell tracing around secrets.")
    require("cat \"$HOME/.ssh/nutsnews_vps\"" not in text, f"{path}: must not print the private key.")
    require("cat \"$HOME/.ssh/known_hosts\"" not in text, f"{path}: must not print known_hosts.")

    remote_commands = re.findall(r'ssh "\$\{ssh_args\[@\]\}" "\$target" "([^"]+)', text)
    require(remote_commands, f"{path}: could not find fixed SSH commands.")
    for command in remote_commands:
        require("${{" not in command, f"{path}: remote command must not use GitHub expressions: {command}")


for workflow in WORKFLOWS:
    validate_workflow(workflow)

validate_collector_recomputes_backup_freshness()
validate_backup_alert_semantics()
validate_backup_runner_error_lifecycle()

print("Backup workflow guardrails passed.")
