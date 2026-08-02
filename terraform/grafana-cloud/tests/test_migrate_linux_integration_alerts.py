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
        bundle: dict[str, list[dict[str, Any]]],
        *,
        alerts_disabled: bool = False,
        drop_recording_once: bool = False,
    ) -> None:
        self.rules = copy.deepcopy(rules)
        self.original_rules = copy.deepcopy(rules)
        self.source_uids = set(source_uids)
        self.recording_uids = {
            uid for uid, rule in rules.items() if bool(rule.get("record"))
        }
        self.bundle = copy.deepcopy(bundle)
        self.alerts_disabled = alerts_disabled
        self.drop_recording_once = drop_recording_once
        self.install_posts = 0
        self.convert_posts = 0
        self.convert_deletes = 0

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
        extra_headers: dict[str, str] | None = None,
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
        if path == MODULE.INTEGRATION_RULES_PATH and method == "GET":
            return {"data": copy.deepcopy(self.bundle)}
        if path == MODULE.INTEGRATION_INSTALL_PATH and method == "POST":
            configuration = payload.get("configuration", {}) if payload else {}
            logs = configuration.get("configurable_logs", {})
            alerts = configuration.get("configurable_alerts", {})
            if logs != {"logs_disabled": False} or not isinstance(
                alerts.get("alerts_disabled"), bool
            ):
                raise AssertionError("integration mutation escaped the fixed contract")
            self.install_posts += 1
            self.alerts_disabled = alerts["alerts_disabled"]
            return {"status": "ok"}
        converted_headers = {
            "X-Grafana-Alerting-Datasource-UID": MODULE.PROMETHEUS_DATASOURCE_UID
        }
        if path == MODULE.CONVERTED_RULES_NAMESPACE_PATH and method == "DELETE":
            if extra_headers != converted_headers:
                raise AssertionError("converted namespace delete lacks datasource identity")
            self.convert_deletes += 1
            for uid in self.source_uids | self.recording_uids:
                self.rules.pop(uid, None)
            return None
        if path == MODULE.CONVERTED_RULES_PATH and method == "POST":
            if extra_headers != converted_headers:
                raise AssertionError("converted namespace post lacks datasource identity")
            groups = payload.get(MODULE.CONVERTED_RULES_NAMESPACE) if payload else None
            if not isinstance(groups, list):
                raise AssertionError("converted namespace payload drifted")
            self.convert_posts += 1
            includes_alerts = any(
                "alert" in rule for group in groups for rule in group.get("rules", [])
            )
            restore_uids = set(self.recording_uids)
            if includes_alerts:
                restore_uids |= self.source_uids
            skipped = False
            for uid in sorted(restore_uids):
                if (
                    self.drop_recording_once
                    and not includes_alerts
                    and uid in self.recording_uids
                    and not skipped
                ):
                    skipped = True
                    continue
                self.rules[uid] = copy.deepcopy(self.original_rules[uid])
            if skipped:
                self.drop_recording_once = False
            return {"status": "ok"}
        if path.startswith(MODULE.PROVISIONING_PATH + "/") and method == "GET":
            uid = path.rsplit("/", 1)[-1]
            value = self.rules.get(uid)
            if value is None and not allow_not_found:
                raise MODULE.ContractError("not found")
            return copy.deepcopy(value)
        raise AssertionError(f"unsupported fake request: {method} {path}")


def fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
    set[str],
    dict[str, list[dict[str, Any]]],
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
    recording_groups: dict[str, list[dict[str, Any]]] = {}
    alert_groups: dict[str, list[dict[str, Any]]] = {}
    recording_index = 0
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
            recording_groups.setdefault(item["group"], []).append(
                {"record": item["title"], "expr": f"vector({recording_index})"}
            )
            recording_index += 1
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
        alert_groups.setdefault(item["group"], []).append(
            {
                "alert": reviewed["title"],
                "expr": reviewed["expr"],
                "for": reviewed["for"],
                "labels": {"severity": item["severity"]},
                "annotations": {
                    "summary": reviewed["summary"],
                    "description": reviewed["description"],
                },
            }
        )
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
    bundle = {
        "recording_rules": [
            {"name": name, "rules": group_rules}
            for name, group_rules in recording_groups.items()
        ],
        "alerting_rules": [
            {"name": name, "rules": group_rules}
            for name, group_rules in alert_groups.items()
        ],
    }
    return external, replacements, rules, source_uids, bundle


class MigrateLinuxIntegrationAlertsTest(unittest.TestCase):
    def test_plan_proves_equivalence_without_mutation(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        client = FakeClient(rules, source_uids, bundle, alerts_disabled=True)
        report = MODULE.run(
            client, external, replacements, mode="plan", settle_seconds=0
        )
        self.assertEqual(report["source_alerts_equivalence_verified"], 24)
        self.assertEqual(report["terraform_replacements_verified"], 24)
        self.assertEqual(report["integration_recording_rules_verified"], 16)
        self.assertEqual(report["source_alerts_remaining"], 24)
        self.assertEqual(report["rollback_snapshot_rules_verified"], 40)
        self.assertEqual(client.install_posts, 0)
        self.assertEqual(client.convert_posts, 0)
        self.assertEqual(client.convert_deletes, 0)

    def test_apply_reconciles_recording_only_namespace_and_is_idempotent(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        client = FakeClient(rules, source_uids, bundle)
        report = MODULE.run(
            client, external, replacements, mode="apply", settle_seconds=0
        )
        self.assertTrue(report["changed"])
        self.assertEqual(report["source_alerts_remaining"], 0)
        self.assertEqual(report["terraform_replacements_verified"], 24)
        self.assertEqual(report["integration_recording_rules_verified"], 16)
        self.assertEqual(report["recording_rules_changed"], 0)
        self.assertEqual(report["recording_rules_reconciled"], 16)
        self.assertEqual(client.install_posts, 1)
        self.assertEqual(client.convert_posts, 1)
        self.assertEqual(client.convert_deletes, 1)

        second = MODULE.run(
            client, external, replacements, mode="apply", settle_seconds=0
        )
        self.assertFalse(second["changed"])
        self.assertEqual(client.install_posts, 1)
        self.assertEqual(client.convert_posts, 1)
        self.assertEqual(client.convert_deletes, 1)

    def test_source_drift_fails_before_configuration_mutation(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        drifted_uid = sorted(source_uids)[-1]
        rules[drifted_uid]["data"][0]["model"]["expr"] = "vector(0)"
        client = FakeClient(rules, source_uids, bundle)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.install_posts, 0)
        self.assertEqual(client.convert_posts, 0)
        self.assertEqual(client.convert_deletes, 0)

    def test_missing_replacement_fails_before_configuration_mutation(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        rules.pop(replacements["rules"][0]["replacementUid"])
        client = FakeClient(rules, source_uids, bundle)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.install_posts, 0)
        self.assertEqual(client.convert_posts, 0)
        self.assertEqual(client.convert_deletes, 0)

    def test_bundle_drift_fails_before_configuration_mutation(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        bundle["alerting_rules"][0]["rules"][0]["expr"] = "vector(0)"
        client = FakeClient(rules, source_uids, bundle)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.install_posts, 0)
        self.assertEqual(client.convert_posts, 0)
        self.assertEqual(client.convert_deletes, 0)

    def test_failed_reduced_namespace_restores_full_bundle(self) -> None:
        external, replacements, rules, source_uids, bundle = fixture()
        client = FakeClient(
            rules, source_uids, bundle, drop_recording_once=True
        )
        with self.assertRaisesRegex(
            MODULE.ContractError, "automatic full-bundle rollback verified"
        ):
            MODULE.run(client, external, replacements, mode="apply", settle_seconds=0)
        self.assertEqual(client.install_posts, 2)
        self.assertEqual(client.convert_posts, 2)
        self.assertEqual(client.convert_deletes, 2)
        self.assertFalse(client.alerts_disabled)
        self.assertTrue(source_uids.issubset(client.rules))
        self.assertTrue(client.recording_uids.issubset(client.rules))


if __name__ == "__main__":
    unittest.main()
