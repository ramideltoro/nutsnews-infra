#!/usr/bin/env python3
"""Migrate Linux integration alerts to reviewed Terraform-owned equivalents."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTERNAL_CATALOG = ROOT / "catalog" / "non-terraform-alert-rules.json"
DEFAULT_REPLACEMENT_CATALOG = (
    ROOT / "catalog" / "linux-integration-alert-replacements.json"
)
INTEGRATION_PATH = (
    "/api/plugin-proxy/grafana-easystart-app/"
    "integrations-api-admin/integrations/linux-node"
)
INTEGRATION_INSTALL_PATH = f"{INTEGRATION_PATH}/install"
INTEGRATION_RULES_PATH = f"{INTEGRATION_PATH}/rules"
PROVISIONING_PATH = "/api/v1/provisioning/alert-rules"
CONVERTED_RULES_PATH = "/api/convert/prometheus/config/v1/rules"
CONVERTED_RULES_NAMESPACE = "Integration - Linux Node"
CONVERTED_RULES_NAMESPACE_PATH = (
    f"{CONVERTED_RULES_PATH}/"
    f"{urllib.parse.quote(CONVERTED_RULES_NAMESPACE, safe='')}"
)
PROMETHEUS_DATASOURCE_UID = "grafanacloud-prom"
RUNBOOK_URL = (
    "https://github.com/ramideltoro/nutsnews-infra/blob/main/"
    "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md"
)


class ContractError(RuntimeError):
    """Raised when live state no longer matches the reviewed migration contract."""


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
        allow_not_found: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self.ssl_context
            ) as response:
                raw = response.read()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise ContractError(
                f"Grafana API {method} {path} returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ContractError(f"Grafana API {method} {path} was unreachable") from exc


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"catalog is not an object: {path.name}")
    return value


def integration_state(client: GrafanaClient, external: dict[str, Any]) -> dict[str, Any]:
    if external.get("integrationUpgradeStatus") != "not_available_from_live_api":
        raise ContractError("Linux integration upgrade availability requires review")
    response = client.request("GET", INTEGRATION_PATH)
    data = response.get("data", {}) if isinstance(response, dict) else {}
    installation = data.get("installation", {}) if isinstance(data, dict) else {}
    configuration = installation.get("configuration", {})
    logs = configuration.get("configurable_logs", {})
    alerts = configuration.get("configurable_alerts", {})
    observed = str(installation.get("version", ""))
    available = str(data.get("version", ""))
    has_update = data.get("has_update")
    if (observed, available, has_update) != (
        str(external.get("integrationVersionObserved", "")),
        str(external.get("integrationVersionAvailable", "")),
        False,
    ):
        raise ContractError(
            "Grafana Linux integration version/update state drifted; refresh and "
            "review both catalogs before changing integration alerts"
        )
    if logs.get("logs_disabled") is not False:
        raise ContractError("Linux integration logs must remain enabled")
    alerts_disabled = alerts.get("alerts_disabled")
    if not isinstance(alerts_disabled, bool):
        raise ContractError("Linux integration configurable-alert state is missing")
    return {
        "installed_version": observed,
        "available_version": available,
        "has_update": False,
        "logs_disabled": False,
        "alerts_disabled": alerts_disabled,
    }


def catalog_contracts(
    external: dict[str, Any], replacements: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if external.get("schemaVersion") != 2 or replacements.get("schemaVersion") != 1:
        raise ContractError("Linux integration catalog schema drifted")
    if external.get("folderUid") != replacements.get("sourceFolderUid"):
        raise ContractError("Linux integration source folder catalog drifted")
    if replacements.get("destinationFolderUid") != "nutsnews-observability":
        raise ContractError("Linux replacement destination folder drifted")
    if replacements.get("groupName") != "NutsNews Linux integration alert replacements":
        raise ContractError("Linux replacement rule group drifted")
    source_alerts = [
        item
        for item in external.get("rules", [])
        if item.get("disposition")
        == "replaced_by_terraform_normalized_equivalent"
    ]
    recordings = [item for item in external.get("rules", []) if item.get("kind") == "recording"]
    replacement_rules = replacements.get("rules", [])
    if len(source_alerts) != 24 or len(recordings) != 16 or len(replacement_rules) != 24:
        raise ContractError("Linux integration migration inventory counts drifted")
    replacement_by_source = {
        str(item.get("sourceUid", "")): item for item in replacement_rules
    }
    if len(replacement_by_source) != 24:
        raise ContractError("Linux replacement source UIDs are missing or duplicated")
    for source in source_alerts:
        replacement = replacement_by_source.get(str(source.get("uid", "")))
        if not replacement or source.get("replacementUid") != replacement.get(
            "replacementUid"
        ):
            raise ContractError("Linux alert source-to-replacement mapping drifted")
    return source_alerts, recordings, replacement_rules


def get_rule(client: GrafanaClient, uid: str) -> dict[str, Any] | None:
    path = f"{PROVISIONING_PATH}/{urllib.parse.quote(uid, safe='')}"
    response = client.request("GET", path, allow_not_found=True)
    if response is not None and not isinstance(response, dict):
        raise ContractError(f"Grafana returned an invalid rule for UID {uid}")
    return response


def query_expr(rule: dict[str, Any]) -> str:
    data = rule.get("data")
    if not isinstance(data, list):
        return ""
    for item in data:
        if isinstance(item, dict) and item.get("refId") == "query":
            model = item.get("model")
            if isinstance(model, dict):
                return str(model.get("expr", ""))
    return ""


def verify_source_rule(
    live: dict[str, Any], source: dict[str, Any], replacement: dict[str, Any], folder: str
) -> None:
    uid = str(source["uid"])
    identity = (
        live.get("uid"),
        live.get("folderUID"),
        live.get("ruleGroup"),
        live.get("title"),
        live.get("provenance"),
    )
    wanted = (uid, folder, source["group"], source["title"], "converted_prometheus")
    labels = live.get("labels", {})
    annotations = live.get("annotations", {})
    if identity != wanted:
        raise ContractError(f"vendor Linux alert identity/provenance drifted for UID {uid}")
    if not isinstance(labels, dict) or labels.get("severity") != source.get("severity"):
        raise ContractError(f"vendor Linux alert severity drifted for UID {uid}")
    if not isinstance(annotations, dict):
        raise ContractError(f"vendor Linux alert annotations drifted for UID {uid}")
    expected = (
        live.get("condition"),
        live.get("for"),
        live.get("noDataState"),
        live.get("execErrState"),
        query_expr(live),
        annotations.get("summary"),
        annotations.get("description"),
    )
    reviewed = (
        replacement["condition"],
        replacement["for"],
        replacement["noDataState"],
        replacement["execErrState"],
        replacement["expr"],
        replacement["summary"],
        replacement["description"],
    )
    if expected != reviewed:
        raise ContractError(f"vendor Linux alert definition drifted for UID {uid}")


def verify_replacement_rule(live: dict[str, Any], replacement: dict[str, Any]) -> None:
    uid = str(replacement["replacementUid"])
    identity = (
        live.get("uid"),
        live.get("folderUID"),
        live.get("ruleGroup"),
        live.get("title"),
    )
    wanted = (
        uid,
        "nutsnews-observability",
        "NutsNews Linux integration alert replacements",
        replacement["title"],
    )
    if identity != wanted:
        raise ContractError(f"Terraform Linux replacement identity drifted for UID {uid}")
    labels = live.get("labels", {})
    annotations = live.get("annotations", {})
    expected_labels = {
        "service_namespace": "nutsnews",
        "deployment_environment": "production",
        "managed_by": "nutsnews-infra",
        "owner": "nutsnews-observability",
        "route": "operations-email",
        "service": "vps-host",
        "severity": replacement["normalizedSeverity"],
        "source_integration": "linux-node",
    }
    expected_annotations = {
        "summary": replacement["summary"],
        "description": replacement["description"],
        "dashboard_url": "/d/nutsnews-vps-overview",
        "runbook_url": RUNBOOK_URL,
    }
    if not isinstance(labels, dict) or any(
        labels.get(key) != value for key, value in expected_labels.items()
    ):
        raise ContractError(f"Terraform Linux replacement labels drifted for UID {uid}")
    if not isinstance(annotations, dict) or any(
        annotations.get(key) != value for key, value in expected_annotations.items()
    ):
        raise ContractError(f"Terraform Linux replacement annotations drifted for UID {uid}")
    definition = (
        live.get("condition"),
        live.get("for"),
        live.get("noDataState"),
        live.get("execErrState"),
        query_expr(live),
    )
    reviewed = (
        replacement["condition"],
        replacement["for"],
        replacement["noDataState"],
        replacement["execErrState"],
        replacement["expr"],
    )
    if definition != reviewed:
        raise ContractError(f"Terraform Linux replacement definition drifted for UID {uid}")


def verify_recordings(
    client: GrafanaClient, recordings: list[dict[str, Any]], folder: str
) -> int:
    verified = 0
    for expected in recordings:
        uid = str(expected["uid"])
        live = get_rule(client, uid)
        if live is None:
            raise ContractError(f"Linux integration recording rule is missing for UID {uid}")
        identity = (
            live.get("uid"),
            live.get("folderUID"),
            live.get("ruleGroup"),
            live.get("title"),
            bool(live.get("record")),
        )
        wanted = (uid, folder, expected["group"], expected["title"], True)
        if identity != wanted:
            raise ContractError(f"Linux integration recording rule drifted for UID {uid}")
        verified += 1
    return verified


def integration_rule_bundle(
    client: GrafanaClient,
    source_alerts: list[dict[str, Any]],
    recordings: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return a reviewed full snapshot and recording-only desired bundle."""
    response = client.request("GET", INTEGRATION_RULES_PATH)
    data = response.get("data", {}) if isinstance(response, dict) else {}
    recording_groups = data.get("recording_rules")
    alert_groups = data.get("alerting_rules")
    if not isinstance(recording_groups, list) or not isinstance(alert_groups, list):
        raise ContractError("Linux integration rule bundle is missing")
    if len(recording_groups) != 2 or len(alert_groups) != 2:
        raise ContractError("Linux integration rule-group inventory drifted")

    def validate_groups(groups: list[dict[str, Any]], key: str) -> Counter[tuple[str, str]]:
        identities: Counter[tuple[str, str]] = Counter()
        for group in groups:
            if not isinstance(group, dict) or set(group) != {"name", "rules"}:
                raise ContractError("Linux integration rule-group shape drifted")
            group_name = group.get("name")
            rules = group.get("rules")
            if not isinstance(group_name, str) or not isinstance(rules, list):
                raise ContractError("Linux integration rule-group content drifted")
            for rule in rules:
                if not isinstance(rule, dict) or not isinstance(rule.get(key), str):
                    raise ContractError("Linux integration rule definition shape drifted")
                identities[(group_name, str(rule[key]))] += 1
        return identities

    observed_recordings = validate_groups(recording_groups, "record")
    expected_recordings = Counter(
        (str(item["group"]), str(item["title"])) for item in recordings
    )
    if observed_recordings != expected_recordings or sum(observed_recordings.values()) != 16:
        raise ContractError("Linux integration recording-rule bundle drifted")

    observed_alerts = validate_groups(alert_groups, "alert")
    expected_alerts = Counter(
        (str(item["group"]), str(item["title"])) for item in source_alerts
    )
    if observed_alerts != expected_alerts or sum(observed_alerts.values()) != 24:
        raise ContractError("Linux integration alert-rule bundle drifted")

    source_by_uid = {str(item["uid"]): item for item in source_alerts}
    expected_definitions: Counter[str] = Counter()
    for replacement in replacements:
        source = source_by_uid[str(replacement["sourceUid"])]
        expected_definitions[
            json.dumps(
                {
                    "group": source["group"],
                    "alert": replacement["title"],
                    "expr": replacement["expr"],
                    "for": replacement["for"],
                    "severity": source["severity"],
                    "summary": replacement["summary"],
                    "description": replacement["description"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        ] += 1
    observed_definitions: Counter[str] = Counter()
    for group in alert_groups:
        for rule in group["rules"]:
            labels = rule.get("labels", {})
            annotations = rule.get("annotations", {})
            if not isinstance(labels, dict) or not isinstance(annotations, dict):
                raise ContractError("Linux integration alert context drifted")
            observed_definitions[
                json.dumps(
                    {
                        "group": group["name"],
                        "alert": rule["alert"],
                        "expr": rule.get("expr"),
                        # Prometheus omits a zero pending period; Grafana's
                        # converted provisioning model canonicalizes it to 0s.
                        "for": rule.get("for") or "0s",
                        "severity": labels.get("severity"),
                        "summary": annotations.get("summary"),
                        "description": annotations.get("description"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ] += 1
    if observed_definitions != expected_definitions:
        raise ContractError("Linux integration alert rollback snapshot drifted")

    # The integration endpoint returns fresh objects, but copying through JSON makes
    # the mutation/rollback payloads independent and guarantees JSON-safe content.
    recordings_only = json.loads(json.dumps(recording_groups))
    full_snapshot = json.loads(json.dumps(recording_groups + alert_groups))
    return full_snapshot, recordings_only


def set_integration_alerts_disabled(client: GrafanaClient, disabled: bool) -> None:
    client.request(
        "POST",
        INTEGRATION_INSTALL_PATH,
        {
            "configuration": {
                "configurable_logs": {"logs_disabled": False},
                "configurable_alerts": {"alerts_disabled": disabled},
            }
        },
    )


def replace_converted_namespace(
    client: GrafanaClient,
    groups: list[dict[str, Any]],
    *,
    allow_missing_delete: bool,
) -> None:
    headers = {"X-Grafana-Alerting-Datasource-UID": PROMETHEUS_DATASOURCE_UID}
    client.request(
        "DELETE",
        CONVERTED_RULES_NAMESPACE_PATH,
        allow_not_found=allow_missing_delete,
        extra_headers=headers,
    )
    client.request(
        "POST",
        CONVERTED_RULES_PATH,
        {CONVERTED_RULES_NAMESPACE: groups},
        extra_headers=headers,
    )


def source_presence(
    client: GrafanaClient, source_alerts: list[dict[str, Any]]
) -> tuple[int, dict[str, dict[str, Any]]]:
    live_by_uid: dict[str, dict[str, Any]] = {}
    for source in source_alerts:
        uid = str(source["uid"])
        live = get_rule(client, uid)
        if live is not None:
            live_by_uid[uid] = live
    return len(live_by_uid), live_by_uid


def verify_all_replacements(
    client: GrafanaClient, replacements: list[dict[str, Any]]
) -> int:
    verified = 0
    for replacement in replacements:
        live = get_rule(client, str(replacement["replacementUid"]))
        if live is None:
            raise ContractError("Terraform Linux replacement is missing")
        verify_replacement_rule(live, replacement)
        verified += 1
    return verified


def wait_for_rule_count(
    client: GrafanaClient,
    source_alerts: list[dict[str, Any]],
    recordings: list[dict[str, Any]],
    *,
    wanted_sources: int,
    settle_seconds: int,
) -> None:
    deadline = time.monotonic() + settle_seconds
    while True:
        sources, _ = source_presence(client, source_alerts)
        present_recordings = sum(
            get_rule(client, str(recording["uid"])) is not None
            for recording in recordings
        )
        if sources == wanted_sources and present_recordings == 16:
            return
        if time.monotonic() >= deadline:
            raise ContractError(
                "converted Linux rule namespace did not settle to the reviewed shape"
            )
        time.sleep(5)


def rollback_full_namespace(
    client: GrafanaClient,
    full_snapshot: list[dict[str, Any]],
    source_alerts: list[dict[str, Any]],
    recordings: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
    external: dict[str, Any],
    state_before: dict[str, Any],
    settle_seconds: int,
) -> None:
    replace_converted_namespace(client, full_snapshot, allow_missing_delete=True)
    if state_before["alerts_disabled"] is not True:
        set_integration_alerts_disabled(client, False)
    wait_for_rule_count(
        client,
        source_alerts,
        recordings,
        wanted_sources=24,
        settle_seconds=settle_seconds,
    )
    _, live_sources = source_presence(client, source_alerts)
    replacement_by_source = {
        str(item["sourceUid"]): item for item in replacements
    }
    for source in source_alerts:
        verify_source_rule(
            live_sources[str(source["uid"])],
            source,
            replacement_by_source[str(source["uid"])],
            str(external["folderUid"]),
        )
    verify_recordings(client, recordings, str(external["folderUid"]))
    verify_all_replacements(client, replacements)
    rolled_back_state = integration_state(client, external)
    if rolled_back_state["alerts_disabled"] != state_before["alerts_disabled"]:
        raise ContractError("Linux integration configuration rollback did not settle")


def run(
    client: GrafanaClient,
    external: dict[str, Any],
    replacements: dict[str, Any],
    *,
    mode: str,
    settle_seconds: int,
) -> dict[str, Any]:
    source_alerts, recordings, replacement_rules = catalog_contracts(
        external, replacements
    )
    replacement_by_source = {
        str(item["sourceUid"]): item for item in replacement_rules
    }
    state_before = integration_state(client, external)
    replacement_verified = verify_all_replacements(client, replacement_rules)
    source_count, live_sources = source_presence(client, source_alerts)
    if source_count not in (0, 24):
        raise ContractError("vendor Linux alert bundle is partially missing")
    source_verified = 0
    if source_count == 24:
        for source in source_alerts:
            verify_source_rule(
                live_sources[str(source["uid"])],
                source,
                replacement_by_source[str(source["uid"])],
                str(external["folderUid"]),
            )
            source_verified += 1
    elif not state_before["alerts_disabled"]:
        raise ContractError("vendor Linux alerts are absent while integration alerts are enabled")

    recording_verified = verify_recordings(client, recordings, str(external["folderUid"]))
    full_snapshot, recordings_only = integration_rule_bundle(
        client, source_alerts, recordings, replacement_rules
    )
    changed = False
    if mode == "apply" and source_count == 24:
        mutation_started = False
        try:
            if not state_before["alerts_disabled"]:
                set_integration_alerts_disabled(client, True)
            mutation_started = True
            replace_converted_namespace(
                client, recordings_only, allow_missing_delete=False
            )
            wait_for_rule_count(
                client,
                source_alerts,
                recordings,
                wanted_sources=0,
                settle_seconds=settle_seconds,
            )
            state_after = integration_state(client, external)
            if not state_after["alerts_disabled"] or state_after["logs_disabled"]:
                raise ContractError("Linux integration configuration changed during migration")
            replacement_verified = verify_all_replacements(client, replacement_rules)
            recording_verified = verify_recordings(
                client, recordings, str(external["folderUid"])
            )
            changed = True
        except Exception as exc:
            if not mutation_started:
                raise
            try:
                rollback_full_namespace(
                    client,
                    full_snapshot,
                    source_alerts,
                    recordings,
                    replacement_rules,
                    external,
                    state_before,
                    settle_seconds,
                )
            except Exception as rollback_exc:
                raise ContractError(
                    "Linux integration migration failed and automatic full-bundle "
                    "rollback could not be verified"
                ) from rollback_exc
            raise ContractError(
                "Linux integration migration failed; automatic full-bundle rollback verified"
            ) from exc
        remaining_sources = 0
    elif mode == "apply":
        state_after = integration_state(client, external)
        if not state_after["alerts_disabled"] or state_after["logs_disabled"]:
            raise ContractError("migrated Linux integration configuration drifted")
        remaining_sources = 0
    else:
        state_after = state_before
        remaining_sources = source_count

    return {
        "schema_version": 1,
        "status": "pass",
        "mode": mode,
        "changed": changed,
        "integration_before": state_before,
        "integration_after": state_after,
        "source_alerts_equivalence_verified": source_verified,
        "source_alerts_remaining": remaining_sources,
        "terraform_replacements_verified": replacement_verified,
        "integration_recording_rules_verified": recording_verified,
        "recording_rules_changed": 0,
        "recording_rules_reconciled": 16 if changed else 0,
        "converted_namespace": CONVERTED_RULES_NAMESPACE,
        "rollback_snapshot_rules_verified": sum(
            len(group["rules"]) for group in full_snapshot
        ),
        "logs_changed": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("plan", "apply"), required=True)
    parser.add_argument("--external-catalog", type=Path, default=DEFAULT_EXTERNAL_CATALOG)
    parser.add_argument(
        "--replacement-catalog", type=Path, default=DEFAULT_REPLACEMENT_CATALOG
    )
    parser.add_argument("--settle-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        base_url = os.environ.get("GRAFANA_URL", "")
        token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
        if not base_url or not token:
            raise ContractError("Grafana URL and service-account token are required")
        if args.settle_seconds < 0 or args.settle_seconds > 600:
            raise ContractError("settle deadline must be between zero and 600 seconds")
        report = run(
            GrafanaClient(base_url, token),
            load_json(args.external_catalog),
            load_json(args.replacement_catalog),
            mode=args.mode,
            settle_seconds=args.settle_seconds,
        )
    except (ContractError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": 1,
            "status": "fail",
            "mode": args.mode,
            "error": str(exc)[:240],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
