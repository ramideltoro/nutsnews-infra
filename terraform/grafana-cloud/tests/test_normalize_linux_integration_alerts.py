#!/usr/bin/env python3
"""Tests for fail-closed Linux integration alert normalization."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "normalize_linux_integration_alerts.py"
SPEC = importlib.util.spec_from_file_location("normalize_linux_integration_alerts", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self, rules: dict[str, dict[str, Any]]) -> None:
        self.rules = copy.deepcopy(rules)
        self.put_uids: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        disable_provenance: bool = False,
    ) -> Any:
        if path == MODULE.INTEGRATION_PATH:
            return MODULE.ApiResponse(
                200,
                {
                    "data": {
                        "version": "1.6.2",
                        "has_update": False,
                        "installation": {"version": "1.6.2"},
                    }
                },
            )
        uid = path.rsplit("/", 1)[-1]
        if method == "GET":
            return MODULE.ApiResponse(200, copy.deepcopy(self.rules[uid]))
        if method == "PUT":
            if not disable_provenance or payload is None:
                raise AssertionError("writes must explicitly preserve provisioned-rule ownership")
            preserved = {
                "provenance": self.rules[uid]["provenance"],
            }
            self.rules[uid] = {**copy.deepcopy(payload), **preserved}
            self.put_uids.append(uid)
            return MODULE.ApiResponse(200, {"message": "updated"})
        raise AssertionError(f"unsupported fake request: {method} {path}")


def fixture() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    catalog = json.loads(
        (ROOT / "catalog" / "non-terraform-alert-rules.json").read_text(
            encoding="utf-8"
        )
    )
    rules: dict[str, dict[str, Any]] = {}
    for item in catalog["rules"]:
        if item["kind"] != "alert":
            continue
        uid = item["uid"]
        rules[uid] = {
            "uid": uid,
            "title": item["title"],
            "folderUID": catalog["folderUid"],
            "ruleGroup": item["group"],
            "condition": "threshold",
            "data": [{"refId": "query", "model": {"expr": "up == 0"}}],
            "noDataState": "OK",
            "execErrState": "OK",
            "for": "5m",
            "keep_firing_for": "0s",
            "isPaused": False,
            "record": None,
            "missingSeriesEvalsToResolve": 1,
            "provenance": "converted_prometheus",
            "labels": {
                "__converted_prometheus_rule__": "true",
                "asserts_alert_category": "failure",
                "asserts_entity_type": "Node",
                "asserts_severity": item["severity"],
                "severity": item["severity"],
            },
            "annotations": {
                "summary": "summary",
                "description": "description",
            },
        }
    return catalog, rules


class NormalizeLinuxIntegrationAlertsTest(unittest.TestCase):
    def test_plan_is_read_only_and_reports_all_changes(self) -> None:
        catalog, rules = fixture()
        client = FakeClient(rules)
        report = MODULE.run(client, catalog, mode="plan")
        self.assertEqual(report["expected_alert_count"], 24)
        self.assertEqual(report["changed_count"], 24)
        self.assertEqual(report["applied_count"], 0)
        self.assertEqual(client.put_uids, [])

    def test_apply_updates_only_alerts_and_is_idempotent(self) -> None:
        catalog, rules = fixture()
        client = FakeClient(rules)
        report = MODULE.run(client, catalog, mode="apply")
        self.assertEqual(report["applied_count"], 24)
        self.assertEqual(report["verified_count"], 24)
        self.assertEqual(len(set(client.put_uids)), 24)
        self.assertTrue(all(uid in rules for uid in client.put_uids))

        client.put_uids.clear()
        second = MODULE.run(client, catalog, mode="apply")
        self.assertEqual(second["changed_count"], 0)
        self.assertEqual(second["unchanged_count"], 24)
        self.assertEqual(client.put_uids, [])

    def test_identity_drift_fails_before_that_rule_is_written(self) -> None:
        catalog, rules = fixture()
        drifted_uid = list(rules)[-1]
        rules[drifted_uid]["ruleGroup"] = "unreviewed-group"
        client = FakeClient(rules)
        with self.assertRaises(MODULE.ContractError):
            MODULE.run(client, catalog, mode="apply")
        self.assertEqual(client.put_uids, [])

    def test_integration_update_state_drift_fails_closed(self) -> None:
        catalog, rules = fixture()

        class UpdatedClient(FakeClient):
            def request(self, method: str, path: str, payload=None, **kwargs: Any) -> Any:
                if path == MODULE.INTEGRATION_PATH:
                    return MODULE.ApiResponse(
                        200,
                        {
                            "data": {
                                "version": "1.6.3",
                                "has_update": True,
                                "installation": {"version": "1.6.2"},
                            }
                        },
                    )
                return super().request(method, path, payload, **kwargs)

        with self.assertRaises(MODULE.ContractError):
            MODULE.run(UpdatedClient(rules), catalog, mode="plan")


if __name__ == "__main__":
    unittest.main()
