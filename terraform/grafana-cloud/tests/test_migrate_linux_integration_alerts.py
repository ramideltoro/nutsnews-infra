#!/usr/bin/env python3
"""Tests for the fail-closed Linux integration alert migration."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_linux_integration_alerts.py"
SPEC = importlib.util.spec_from_file_location("migrate_linux_integration_alerts", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(
        self,
        rules: dict[str, dict[str, Any]],
        source_uids: set[str],
        *,
        alerts_disabled: bool = False,
    ) -> None:
        self.rules = copy.deepcopy(rules)
        self.source_uids = source_uids
        self.alerts_disabled = alerts_disabled
        self.posts = 0

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        if path == MODULE.INTEGRATION_PATH:
            return {
                "data": {
                    "version": "1.6.2",
                    "has_update": False,
                    "installation": {
                        "version": "1.6.2",
                        "configuration": {
                            "configurable_logs": {"logs_disabled": False},
                            "configurable_alerts": {
                                "alerts_disabled": self.alerts_disabled
                            },
                        },
                    },
                }
            }
        if path == MODULE.INTEGRATION_INSTALL_PATH and method == "POST":
            expected = {
                "configuration": {
                    "configurable_logs": {"logs_disabled": False},
                    "configurable_alerts": {"alerts_disabled": True},
                }
            }
            if payload != expected:
                raise AssertionError("integration mutation escaped the fixed contract")
            self.posts += 1
            self.alerts_disabled = True
            for uid in self.source_uids:
                self.rules.pop(uid, None)
            return {"status": "ok"}
        if path.startswith(MODULE.PROVISIONING_PATH + "/") and method == "GET":
            uid = path.rsplit("/", 1)[-1]
            value = self.rules.get(uid)
            if value is None and not allow_not_found:
                raise MODULE.ContractError("not found")
            return copy.deepcopy(value)
        raise AssertionError(f"unsupported fake request: {method} {path}")


def fixture() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, dict[str, Any]], set[str]
]:
    external = json.loads(
        (ROOT / "catalog" / "non-terraform-alert-rules.json").read_text(
            encoding="utf-8"
        )
    )
    replacements = json.loads(
        (ROOT / "catalog" / "linux-integration-alert-replacements.json").read_text(
            encoding="utf-8"
        )
    )
    replacement_by_source = {
        item["sourceUid"]: item for item in replacements["rules"]
    }
    rules: dict[str, dict[str, Any]] = {}
    source_uids: set[str] = set()
    for item in external["rules"]:
        uid = item["uid"]
        if item["kind"] == "recording":
            rules[uid] = {
                "uid": uid,
                "folderUID": external["folderUid"],
                "ruleGroup": item["group"],
                "title": item["title"],
                "record": {"metric": item["title"]},
            }
            continue
        source_uids.add(uid)
        reviewed = replacement_by_source[uid]
        rules[uid] = {
            "uid": uid,
            "folderUID": external["folderUid"],
            "ruleGroup": item["group"],
            "title": item["title"],
            "provenance": "converted_prometheus",
            "condition": reviewed["condition"],
            "for": reviewed["for"],
            "noDataState": reviewed["noDataState"],
            "execErrState": reviewed["execErrState"],
            "labels": {"severity": item["severity"]},
            "annotations": {
                "summary": reviewed["summary"],
                "description": reviewed["description"],
            },
            "data": [
                {"refId": "query", "model": {"expr": reviewed["expr"]}}
            ],
        }
    for reviewed in replacements["rules"]:
        uid = reviewed["replacementUid"]
        rules[uid] = {
            "uid": uid,
            "folderUID": replacements["destinationFolderUid"],
            "ruleGroup": replacements["groupName"],
            "title": reviewed["title"],
            "condition": reviewed["condition"],
            "for": reviewed["for"],
            "noDataState": reviewed["noDataState"],
            "execErrState": reviewed["execErrState"],
            "labels": {
                "service_namespace": "nutsnews",
                "deployment_environment": "production",
                "managed_by": "nutsnews-infra",
                "owner": "nutsnews-observability",
                "route": "operations-email",
                "service": "vps-host",
                "severity": reviewed["normalizedSeverity"],
                "source_integration": "linux-node",
            },
            "annotations": {
                "summary": reviewed["summary"],
                "description": reviewed["description"],
                "dashboard_url": "/d/nutsnews-vps-overview",
                "runbook_url": MODULE.RUNBOOK_URL,
            },
            "data": [
                {"refId": "query", "model": {"expr": reviewed["expr"]}}
            ],
        }
    return external, replacements, rules, source_uids


class MigrateLinuxIntegrationAlertsTest(unittest.TestCase):
    def test_plan_proves_equivalence_without_mutation(self) -> None:
        external, replacements, rules, source_uids = fixture()
        client = FakeClient(rules, source_uids)
        report = MODULE.run(
            client, external, replacements, mode="plan", settle_seconds=0
        )
        self.assertEqual(report["source_alerts_equivalence_verified"], 24)
        self.assertEqual(report["terraform_replacements_verified"], 24)
        self.assertEqual(report["integration_recording_rules_verified"], 16)
        self.assertEqual(report["source_alerts_remaining"], 24)
        self.assertEqual(client.posts, 0)

    def test_apply_disables_only_vendor_alerts_and_is_idempotent(self) -> None:
        external, replacements, rules, source_uids = fixture()
        client = FakeClient(rules, source_uids)
        report = MODULE.run(
            client, external, replacements, mode="apply", settle_seconds=0
        )
        self.assertTrue(report["changed"])
        self.assertEqual(report["source_alerts_remaining"], 0)
        self.assertEqual(report["terraform_replacements_verified"], 24)
        self.assertEqual(report["integration_recording_rules_verified"], 16)
        self.assertEqual(report["recording_rules_changed"], 0)
        self.assertEqual(client.posts, 1)

        second = MODULE.run(
            client, external, replacements, mode="apply", settle_seconds=0
        )
        self.assertFalse(second["changed"])
        self.assertEqual(client.posts, 1)

    def test_source_drift_fails_before_configuration_mutation(self) -> None:
        external, replacements, rules, source_uids = fixture()
        drifted_uid = sorted(source_uids)[-1]
        rules[drifted_uid]["data"][0]["model"]["expr"] = "vector(0)"
        client = FakeClient(rules, source_uids)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.posts, 0)

    def test_missing_replacement_fails_before_configuration_mutation(self) -> None:
        external, replacements, rules, source_uids = fixture()
        rules.pop(replacements["rules"][0]["replacementUid"])
        client = FakeClient(rules, source_uids)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.posts, 0)


if __name__ == "__main__":
    unittest.main()
