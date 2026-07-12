#!/usr/bin/env python3
"""Regression coverage for stable VPS email alert identity and cooldown state."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORTER_PATH = ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_reporter.py"
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


if __name__ == "__main__":
    unittest.main()
