#!/usr/bin/env python3
"""Normalize the reviewed Grafana Linux integration alerts in place.

The Linux integration owns its rule definitions, while nutsnews-infra owns the
operational routing contract layered onto the 24 alerting rules. This command
fails closed on inventory or identity drift, changes no recording rules, and
verifies every update by reading the rule back from Grafana.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "catalog" / "non-terraform-alert-rules.json"
INTEGRATION_PATH = (
    "/api/plugin-proxy/grafana-easystart-app/"
    "integrations-api-admin/integrations/linux-node"
)
PROVISIONING_PATH = "/api/v1/provisioning/alert-rules"
WRITABLE_RULE_FIELDS = {
    "uid",
    "title",
    "folderUID",
    "ruleGroup",
    "condition",
    "data",
    "noDataState",
    "execErrState",
    "for",
    "keep_firing_for",
    "annotations",
    "labels",
    "isPaused",
    "notification_settings",
    "record",
    "missingSeriesEvalsToResolve",
}


class ContractError(RuntimeError):
    """Raised when live state no longer matches the reviewed catalog contract."""


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: Any


class GrafanaClient:
    def __init__(self, base_url: str, token: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ContractError("GRAFANA_URL must be a query-free HTTPS Grafana origin")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ssl_context = ssl.create_default_context()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        disable_provenance: bool = False,
    ) -> ApiResponse:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if disable_provenance:
            headers["X-Disable-Provenance"] = "true"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self.ssl_context
            ) as response:
                raw = response.read()
                decoded = json.loads(raw) if raw else None
                return ApiResponse(status=response.status, payload=decoded)
        except urllib.error.HTTPError as exc:
            # Never include the provider response: it may contain rule expressions or
            # other operator-only context. Status and fixed path are sufficient.
            raise ContractError(
                f"Grafana API {method} {path} returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ContractError(f"Grafana API {method} {path} was unreachable") from exc


def load_catalog(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 2:
        raise ContractError("Linux integration catalog schemaVersion must be 2")
    if payload.get("folderUid") != "integration---linux-node":
        raise ContractError("Linux integration folder UID drifted")
    context = payload.get("contextPolicy")
    if not isinstance(context, dict) or context.get("normalizationStatus") != "approved":
        raise ContractError("Linux integration normalization is not source-approved")
    return payload


def integration_state(client: GrafanaClient, catalog: dict[str, Any]) -> dict[str, Any]:
    response = client.request("GET", INTEGRATION_PATH)
    data = response.payload.get("data", {}) if isinstance(response.payload, dict) else {}
    installation = data.get("installation", {}) if isinstance(data, dict) else {}
    observed = str(installation.get("version", ""))
    available = str(data.get("version", ""))
    has_update = data.get("has_update")
    expected_observed = str(catalog.get("integrationVersionObserved", ""))
    expected_available = str(catalog.get("integrationVersionAvailable", ""))
    expected_upgrade_status = catalog.get("integrationUpgradeStatus")
    if (observed, available, has_update) != (
        expected_observed,
        expected_available,
        False,
    ):
        raise ContractError(
            "Grafana Linux integration version/update state drifted; refresh and review "
            "the catalog before changing vendor rules"
        )
    if expected_upgrade_status != "not_available_from_live_api":
        raise ContractError("Linux integration upgrade status is not the reviewed value")
    return {
        "installed_version": observed,
        "available_version": available,
        "has_update": False,
    }


def expected_alerts(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rules = catalog.get("rules")
    if not isinstance(rules, list):
        raise ContractError("Linux integration rule catalog is invalid")
    alerts = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and rule.get("kind") == "alert"
        and rule.get("disposition") == "retain"
    ]
    if len(rules) != catalog.get("legacyObservedRuleCount") or len(alerts) != 24:
        raise ContractError("Linux integration catalog must contain 40 rules and 24 alerts")
    uids = [str(rule.get("uid", "")) for rule in rules]
    if any(not uid for uid in uids) or len(uids) != len(set(uids)):
        raise ContractError("Linux integration catalog UIDs are missing or duplicated")
    return alerts


def desired_rule(
    live: dict[str, Any], expected: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    uid = str(expected["uid"])
    identity = (
        str(live.get("uid", "")),
        str(live.get("folderUID", "")),
        str(live.get("ruleGroup", "")),
        str(live.get("title", "")),
    )
    wanted_identity = (
        uid,
        str(catalog["folderUid"]),
        str(expected["group"]),
        str(expected["title"]),
    )
    if identity != wanted_identity:
        raise ContractError(f"Linux integration alert identity drifted for UID {uid}")
    if live.get("record"):
        raise ContractError(f"Catalog alert UID {uid} is a recording rule")
    if live.get("provenance") != "converted_prometheus":
        raise ContractError(f"Linux integration alert provenance drifted for UID {uid}")
    if not isinstance(live.get("data"), list) or not live["data"]:
        raise ContractError(f"Linux integration alert query is missing for UID {uid}")
    if not live.get("noDataState") or not live.get("execErrState"):
        raise ContractError(f"Linux integration alert state behavior is missing for UID {uid}")

    context = catalog["contextPolicy"]
    current_labels = live.get("labels")
    current_annotations = live.get("annotations")
    if not isinstance(current_labels, dict) or not isinstance(current_annotations, dict):
        raise ContractError(f"Linux integration alert context is invalid for UID {uid}")
    if current_labels.get("__converted_prometheus_rule__") != "true":
        raise ContractError(f"Linux integration marker is missing for UID {uid}")
    if not current_annotations.get("summary") or not current_annotations.get("description"):
        raise ContractError(f"Linux integration annotations are incomplete for UID {uid}")

    source_severity = str(expected.get("severity", ""))
    severity_map = context["severityNormalization"]
    desired_severity = str(severity_map.get(source_severity, ""))
    if not desired_severity:
        raise ContractError(f"Linux integration severity mapping is missing for UID {uid}")
    asserts_severity = current_labels.get("asserts_severity")
    if asserts_severity is not None and asserts_severity != source_severity:
        raise ContractError(f"Linux integration source severity drifted for UID {uid}")
    if current_labels.get("severity") not in {source_severity, desired_severity}:
        raise ContractError(f"Linux integration routed severity drifted for UID {uid}")

    labels = dict(current_labels)
    labels.update(context["requiredAlertLabelValues"])
    labels["severity"] = desired_severity
    annotations = dict(current_annotations)
    annotations.update(context["requiredAlertAnnotationValues"])

    desired = {key: live[key] for key in WRITABLE_RULE_FIELDS if key in live}
    desired["labels"] = labels
    desired["annotations"] = annotations
    return desired


def normalized_context_matches(
    rule: dict[str, Any], expected: dict[str, Any], catalog: dict[str, Any]
) -> bool:
    context = catalog["contextPolicy"]
    labels = rule.get("labels")
    annotations = rule.get("annotations")
    if not isinstance(labels, dict) or not isinstance(annotations, dict):
        return False
    expected_labels = dict(context["requiredAlertLabelValues"])
    expected_labels["severity"] = context["severityNormalization"][expected["severity"]]
    return all(labels.get(key) == value for key, value in expected_labels.items()) and all(
        annotations.get(key) == value
        for key, value in context["requiredAlertAnnotationValues"].items()
    )


def run(
    client: GrafanaClient,
    catalog: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    versions = integration_state(client, catalog)
    alerts = expected_alerts(catalog)
    changed = 0
    unchanged = 0
    applied = 0
    verified = 0
    planned: list[
        tuple[dict[str, Any], str, str, dict[str, Any], bool]
    ] = []

    for expected in alerts:
        uid = str(expected["uid"])
        path = f"{PROVISIONING_PATH}/{urllib.parse.quote(uid, safe='')}"
        before = client.request("GET", path).payload
        if not isinstance(before, dict):
            raise ContractError(f"Grafana returned an invalid alert rule for UID {uid}")
        desired = desired_rule(before, expected, catalog)
        already_normalized = normalized_context_matches(before, expected, catalog)
        planned.append((expected, uid, path, desired, already_normalized))
        if already_normalized:
            unchanged += 1
        else:
            changed += 1

    # Complete all read-only inventory, identity, provenance, severity, and query
    # validation before the first write. A later provider/API failure may still
    # yield a partial idempotent application, which the failed workflow exposes
    # and a normal rerun safely finishes.
    for expected, uid, path, desired, already_normalized in planned:
        if mode == "apply" and not already_normalized:
            client.request("PUT", path, desired, disable_provenance=True)
            applied += 1
        if mode == "apply":
            after = client.request("GET", path).payload
            if not isinstance(after, dict):
                raise ContractError(f"Grafana returned an invalid verification rule for UID {uid}")
            # Re-run the complete identity/provenance/query contract, then verify context.
            desired_rule(after, expected, catalog)
            if not normalized_context_matches(after, expected, catalog):
                raise ContractError(f"Linux integration normalization did not persist for UID {uid}")
            verified += 1

    status = "pass"
    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "integration": versions,
        "expected_alert_count": len(alerts),
        "changed_count": changed,
        "unchanged_count": unchanged,
        "applied_count": applied,
        "verified_count": verified,
        "recording_rules_changed": 0,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("plan", "apply"), required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output: dict[str, Any]
    try:
        base_url = os.environ.get("GRAFANA_URL", "")
        token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
        if not base_url or not token:
            raise ContractError("Grafana URL and service-account token are required")
        catalog = load_catalog(args.catalog)
        output = run(GrafanaClient(base_url, token), catalog, mode=args.mode)
    except (ContractError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output = {
            "schema_version": 1,
            "status": "fail",
            "mode": args.mode,
            "error": str(exc)[:240],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
