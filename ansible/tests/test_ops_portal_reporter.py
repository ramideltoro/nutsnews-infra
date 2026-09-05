#!/usr/bin/env python3
"""Regression coverage for stable VPS email alert identity and cooldown state."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
REPORTER_PATH = ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_reporter.py"
PROTECTED_APPLY_PATH = ROOT / ".github/workflows/protected-ansible-apply.yml"
ROLE_DEFAULTS_PATH = ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml"
SPEC = importlib.util.spec_from_file_location("ops_portal_reporter_under_test", REPORTER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load ops portal reporter module.")
REPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORTER)


def alert(identity: str, level: str = "warning", message: str = "Changing value: 1") -> dict[str, str]:
    return {"id": identity, "level": level, "message": message}


def sent_state(alerts: list[dict[str, str]], now: int = 1_000) -> dict[str, object]:
    sendable, suppressed, state = REPORTER.alert_delivery_plan(
        alerts,
        {},
        now=now,
        cooldown_seconds=21_600,
    )
    if suppressed:
        raise AssertionError("First occurrence must not be suppressed.")
    return REPORTER.record_sent_alerts(state, sendable, now=now, sent_at="2026-07-12T00:00:00+00:00")


class ReporterCooldownTests(unittest.TestCase):
    def test_default_cooldown_is_24_hours_in_every_runtime_layer(self) -> None:
        workflow = PROTECTED_APPLY_PATH.read_text(encoding="utf-8")
        role_defaults = ROLE_DEFAULTS_PATH.read_text(encoding="utf-8")

        with mock.patch.dict(REPORTER.os.environ, {}, clear=True):
            self.assertEqual(REPORTER.email_config()["cooldown_seconds"], 86_400)

        self.assertIn(
            'env_int("NUTSNEWS_ALERT_COOLDOWN_SECONDS", 86400)',
            workflow,
        )
        self.assertIn(
            "vps_service_foundation_email_alert_cooldown_seconds: 86400",
            role_defaults,
        )

    def test_changing_message_uses_stable_identity_during_cooldown(self) -> None:
        state = sent_state([alert("free_tier.example.quota_risk", message="6.09 hours, 79.7% used")])
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            [alert("free_tier.example.quota_risk", message="5.99 hours, 80.0% used")],
            state,
            now=1_300,
            cooldown_seconds=21_600,
        )
        self.assertEqual(sendable, [])
        self.assertEqual(suppressed, 1)

    def test_email_body_keeps_current_human_readable_values(self) -> None:
        current = alert("free_tier.example.quota_risk", message="5.99 GiB remaining, 80.0% used")
        body = REPORTER.alert_body(
            {"generated_at": "2026-07-12T03:46:03+00:00", "host": {"fqdn": "vps.nutsnews.com"}},
            [current],
        )
        self.assertIn("5.99 GiB remaining, 80.0% used", body)
        self.assertNotIn("password=", body.lower())
        self.assertNotIn("token=", body.lower())

    def test_distinct_alerts_each_send_once(self) -> None:
        alerts = [alert("backup.snapshot_stale"), alert("backup.timer_inactive")]
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            alerts,
            {},
            now=1_000,
            cooldown_seconds=21_600,
        )
        self.assertEqual(len(sendable), 2)
        self.assertEqual(suppressed, 0)

    def test_warning_to_critical_escalation_sends_promptly(self) -> None:
        state = sent_state([alert("resource.swap_usage", "warning")])
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            [alert("resource.swap_usage", "critical")],
            state,
            now=1_300,
            cooldown_seconds=21_600,
        )
        self.assertEqual(len(sendable), 1)
        self.assertEqual(suppressed, 0)

    def test_critical_to_warning_deescalation_respects_cooldown(self) -> None:
        state = sent_state([alert("resource.swap_usage", "critical")])
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            [alert("resource.swap_usage", "warning")],
            state,
            now=1_300,
            cooldown_seconds=21_600,
        )
        self.assertEqual(sendable, [])
        self.assertEqual(suppressed, 1)

    def test_cooldown_expiry_allows_reminder(self) -> None:
        state = sent_state([alert("backup.verification_overdue")])
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            [alert("backup.verification_overdue", message="Still overdue after the policy deadline")],
            state,
            now=22_601,
            cooldown_seconds=21_600,
        )
        self.assertEqual(len(sendable), 1)
        self.assertEqual(suppressed, 0)

    def test_clear_then_recurrence_starts_new_incident(self) -> None:
        state = sent_state([alert("backup.timer_inactive")])
        cleared = REPORTER.active_alert_state(state, [])
        self.assertEqual(cleared["alerts"], {})
        sendable, suppressed, _ = REPORTER.alert_delivery_plan(
            [alert("backup.timer_inactive")],
            cleared,
            now=1_300,
            cooldown_seconds=21_600,
        )
        self.assertEqual(len(sendable), 1)
        self.assertEqual(suppressed, 0)

    def test_state_is_bounded_and_does_not_store_messages_or_secrets(self) -> None:
        alerts = [
            alert(f"test.alert_{index}", message=f"volatile message {index} token=do-not-store")
            for index in range(REPORTER.ALERT_STATE_MAX_ENTRIES + 50)
        ]
        sendable, _, state = REPORTER.alert_delivery_plan(
            alerts,
            {},
            now=1_000,
            cooldown_seconds=21_600,
        )
        state = REPORTER.record_sent_alerts(
            state,
            sendable,
            now=1_000,
            sent_at="2026-07-12T00:00:00+00:00",
        )
        rendered = json.dumps(state, sort_keys=True).lower()
        self.assertEqual(state["schema_version"], REPORTER.ALERT_STATE_SCHEMA_VERSION)
        self.assertLessEqual(len(state["alerts"]), REPORTER.ALERT_STATE_MAX_ENTRIES)
        self.assertNotIn("volatile message", rendered)
        self.assertNotIn("do-not-store", rendered)
        self.assertNotIn("token=", rendered)


class ReporterCriticalHealthTests(unittest.TestCase):
    @staticmethod
    def configured_email() -> dict[str, object]:
        return {
            "enabled": True,
            "host": "smtp.example.invalid",
            "port": 587,
            "username": "",
            "password": "",
            "starttls": True,
            "sender": "ops@example.invalid",
            "recipients": ["recipient@example.invalid"],
            "cooldown_seconds": 21_600,
            "subject_prefix": "NutsNews VPS",
            "auth_complete": True,
        }

    @staticmethod
    def status(level: str) -> dict[str, object]:
        return {
            "generated_at": "2026-07-31T00:00:00+00:00",
            "alerts": {
                "items": [
                    {
                        "id": "test.health",
                        "level": level,
                        "message": f"{level} fixture",
                    }
                ]
            },
        }

    def test_critical_report_sends_and_writes_evidence_before_nonzero_exit(self) -> None:
        events: list[str] = []
        status_updates: list[dict[str, object]] = []

        def record_status(**kwargs: object) -> dict[str, object]:
            events.append("status")
            status_updates.append(kwargs)
            return kwargs

        with (
            mock.patch.object(REPORTER, "read_json", return_value=self.status("critical")),
            mock.patch.object(REPORTER, "send_email", side_effect=lambda *_: events.append("email")),
            mock.patch.object(REPORTER, "public_status_update", side_effect=record_status),
        ):
            result = REPORTER.handle_report(
                self.configured_email(),
                True,
                False,
                fail_on_critical=True,
            )

        self.assertEqual(result, REPORTER.CRITICAL_HEALTH_EXIT_CODE)
        self.assertEqual(events, ["email", "status"])
        self.assertEqual(status_updates[0]["status"], "sent")
        self.assertEqual(status_updates[0]["pending_alerts"], 1)
        self.assertTrue(status_updates[0]["sent"])
        self.assertEqual(status_updates[0]["report_conclusion"], "critical")
        self.assertEqual(status_updates[0]["report_exit_code"], REPORTER.CRITICAL_HEALTH_EXIT_CODE)

    def test_warning_report_remains_successful(self) -> None:
        with (
            mock.patch.object(REPORTER, "read_json", return_value=self.status("warning")),
            mock.patch.object(REPORTER, "send_email"),
            mock.patch.object(REPORTER, "public_status_update"),
        ):
            result = REPORTER.handle_report(
                self.configured_email(),
                True,
                False,
                fail_on_critical=True,
            )

        self.assertEqual(result, 0)

    def test_delivery_failure_takes_precedence_over_critical_exit(self) -> None:
        status_updates: list[dict[str, object]] = []
        with (
            mock.patch.object(REPORTER, "read_json", return_value=self.status("critical")),
            mock.patch.object(REPORTER, "send_email", side_effect=RuntimeError("smtp unavailable")),
            mock.patch.object(
                REPORTER,
                "public_status_update",
                side_effect=lambda **kwargs: status_updates.append(kwargs) or kwargs,
            ),
        ):
            result = REPORTER.handle_report(
                self.configured_email(),
                True,
                False,
                fail_on_critical=True,
            )

        self.assertEqual(result, 1)
        self.assertEqual(status_updates[0]["status"], "send failed")
        self.assertEqual(status_updates[0]["pending_alerts"], 1)
        self.assertIn("smtp unavailable", str(status_updates[0]["error"]))


if __name__ == "__main__":
    unittest.main()
