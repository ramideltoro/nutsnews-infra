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
PROVISIONING_PATH = "/api/v1/provisioning/alert-rules"
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
    ) -> Any:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self.ssl_context
            ) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
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

    replacement_verified = 0
    for replacement in replacement_rules:
        live = get_rule(client, str(replacement["replacementUid"]))
        if live is None:
            raise ContractError(
                "Terraform Linux alert replacements must be applied before migration"
            )
        verify_replacement_rule(live, replacement)
        replacement_verified += 1

    source_verified = 0
    if not state_before["alerts_disabled"]:
        # Prove all live vendor definitions match their committed replacements before
        # changing the integration configuration.
        for source in source_alerts:
            live = get_rule(client, str(source["uid"]))
            if live is None:
                raise ContractError("vendor Linux alert bundle is partially missing")
            verify_source_rule(
                live,
                source,
                replacement_by_source[str(source["uid"])],
                str(external["folderUid"]),
            )
            source_verified += 1
    elif any(get_rule(client, str(source["uid"])) is not None for source in source_alerts):
        raise ContractError("Linux integration reports alerts disabled but vendor alerts remain")

    recording_verified = verify_recordings(
        client, recordings, str(external["folderUid"])
    )
    changed = False
    if mode == "apply" and not state_before["alerts_disabled"]:
        client.request(
            "POST",
            INTEGRATION_INSTALL_PATH,
            {
                "configuration": {
                    "configurable_logs": {"logs_disabled": False},
                    "configurable_alerts": {"alerts_disabled": True},
                }
            },
        )
        changed = True

    if mode == "apply":
        deadline = time.monotonic() + settle_seconds
        while True:
            state_after = integration_state(client, external)
            remaining_sources = sum(
                get_rule(client, str(source["uid"])) is not None
                for source in source_alerts
            )
            if state_after["alerts_disabled"] and remaining_sources == 0:
                break
            if time.monotonic() >= deadline:
                raise ContractError(
                    "Linux integration alert disable did not settle before the deadline"
                )
            time.sleep(5)
        replacement_verified = 0
        for replacement in replacement_rules:
            live = get_rule(client, str(replacement["replacementUid"]))
            if live is None:
                raise ContractError("Terraform Linux replacement disappeared during migration")
            verify_replacement_rule(live, replacement)
            replacement_verified += 1
        recording_verified = verify_recordings(
            client, recordings, str(external["folderUid"])
        )
    else:
        state_after = state_before
        remaining_sources = 0 if state_before["alerts_disabled"] else 24

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
