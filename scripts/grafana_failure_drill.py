#!/usr/bin/env python3
"""Validate failure-drill scope, observe alerts, and build value-free evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NoReturn


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config/grafana-failure-drills.json"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PHASES = ("precheck", "injection", "observation", "recovery", "postcheck")
PHASE_STATUSES = {"pass", "fail", "planned", "not-run"}
GRAFANA_UI_ORIGIN = "https://kindcantaloupe2036.grafana.net"
GRAFANA_UI_ORIGIN_SPELLINGS = frozenset(
    {
        GRAFANA_UI_ORIGIN,
        f"{GRAFANA_UI_ORIGIN}/",
        f"{GRAFANA_UI_ORIGIN}:443",
        f"{GRAFANA_UI_ORIGIN}:443/",
    }
)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every HTTP redirect into an error before bearer credentials can move."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def validate_api_url(value: str, name: str = "GRAFANA_URL") -> str:
    """Return the exact query-free NutsNews Grafana HTTPS origin."""
    invalid_origin = f"{name} must be a query-free HTTPS Grafana Cloud API origin"
    if value not in GRAFANA_UI_ORIGIN_SPELLINGS:
        fail(invalid_origin)
    return GRAFANA_UI_ORIGIN


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_contract(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"failure-drill contract is unavailable: {exc}")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        fail("failure-drill contract schema_version must be 1")
    drills = data.get("drills")
    if not isinstance(drills, list) or len(drills) != 8:
        fail("failure-drill contract must define exactly eight drills")
    seen: set[str] = set()
    for item in drills:
        if not isinstance(item, dict):
            fail("failure-drill contract entries must be objects")
        drill_id = item.get("id")
        if not isinstance(drill_id, str) or not SAFE_ID.fullmatch(drill_id) or drill_id in seen:
            fail("failure-drill IDs must be unique bounded identifiers")
        seen.add(drill_id)
        if item.get("executor") not in {"vps-hook", "backend-workflow", "synthetic-api"}:
            fail(f"{drill_id}: unsupported executor")
        for key in ("target", "injection", "recovery"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                fail(f"{drill_id}: {key} is required")
        if not SAFE_ID.fullmatch(item["target"]):
            fail(f"{drill_id}: target must be a bounded exact identifier")
        variants = item.get("variants", [])
        if not isinstance(variants, list) or any(
            not isinstance(value, str) or not SAFE_ID.fullmatch(value) for value in variants
        ):
            fail(f"{drill_id}: variants must be bounded identifiers")
        targets = item.get("targets", [])
        if targets and (
            item.get("executor") != "synthetic-api"
            or not isinstance(targets, list)
            or item["target"] not in targets
            or len(set(targets)) != 5
            or any(
                not isinstance(value, str) or not SAFE_ID.fullmatch(value)
                for value in targets
            )
        ):
            fail(f"{drill_id}: synthetic targets must be five unique bounded identifiers")
        alert_uids = item.get("expected_alert_uids")
        if not isinstance(alert_uids, list) or any(
            not isinstance(value, str) or not SAFE_ID.fullmatch(value) for value in alert_uids
        ):
            fail(f"{drill_id}: expected_alert_uids must be bounded")
    if data.get("default_mode") != "dry-run":
        fail("failure-drill contract must default to dry-run")
    retention = data.get("artifact_retention_days")
    if retention != 90:
        fail("public-repository failure-drill artifacts must use the 90-day maximum")
    return data


def select_drill(contract: dict[str, Any], drill_id: str, variant: str) -> dict[str, Any]:
    item = next((value for value in contract["drills"] if value["id"] == drill_id), None)
    if item is None:
        fail("unsupported failure drill")
    variants = item.get("variants", [])
    if variants:
        if variant not in variants:
            fail(f"{drill_id} requires one of these variants: {', '.join(variants)}")
    elif variant != "not-applicable":
        fail(f"{drill_id} does not accept a variant")
    return item


def select_synthetic_target(item: dict[str, Any], target: str) -> dict[str, Any]:
    targets = item.get("targets", [])
    if item.get("executor") != "synthetic-api":
        if target:
            fail("--synthetic-target is valid only for the synthetic drill")
        return item
    selected = target or item["target"]
    if selected not in targets:
        fail("synthetic target is not in the source-controlled five-check allowlist")
    return {**item, "target": selected}


def expected_confirmation(item: dict[str, Any]) -> str:
    return f"execute-grafana-failure-drill:{item['target']}:{item['id']}"


def validate_execute(item: dict[str, Any], target_confirmation: str, execute_confirmation: str) -> None:
    if target_confirmation != item["target"]:
        fail("target confirmation does not match the contract's exact target")
    if execute_confirmation != expected_confirmation(item):
        fail("execute confirmation does not match the contract's exact target and drill")


def planned_report(item: dict[str, Any], variant: str, run_id: str) -> dict[str, Any]:
    variant_value = variant if item.get("variants") else "not-applicable"
    return {
        "schema_version": 1,
        "drill": item["id"],
        "target": item["target"],
        "variant": variant_value,
        "run_id": run_id,
        "started_at": utc_now(),
        "mode": "dry-run",
        "precheck": {"status": "planned"},
        "injection": {"status": "planned", "summary": item["injection"]},
        "observed_alert_uids": item["expected_alert_uids"],
        "recovery": {"status": "planned", "summary": item["recovery"]},
        "postcheck": {"status": "planned"},
        "result": "dry-run",
        "mutation_performed": False,
        "confirmation_required": expected_confirmation(item),
    }


class GrafanaClient:
    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = validate_api_url(url)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def request(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.url}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise RuntimeError(f"Grafana alert query failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Grafana alert query failed") from exc
        return json.loads(raw) if raw else []


def active_rule_uids(client: GrafanaClient) -> set[str]:
    query = urllib.parse.urlencode({"active": "true", "silenced": "false", "inhibited": "false"})
    response = client.request(f"/api/alertmanager/grafana/api/v2/alerts?{query}")
    if not isinstance(response, list):
        return set()
    observed: set[str] = set()
    for alert in response:
        labels = alert.get("labels") if isinstance(alert, dict) else None
        if not isinstance(labels, dict):
            continue
        # Grafana's Alertmanager API uses __alert_rule_uid__ for rule-specific
        # silences and active alert instances. Keep the older aliases because
        # hosted versions and externally forwarded alerts can expose them too.
        for key in ("__alert_rule_uid__", "grafana_rule_uid", "rule_uid", "uid"):
            value = labels.get(key)
            if isinstance(value, str) and SAFE_ID.fullmatch(value):
                observed.add(value)
    return observed


def observe_alerts(expected_uids: list[str], state: str, timeout_seconds: int) -> dict[str, Any]:
    if not expected_uids:
        return {"status": "pass", "state": state, "observed_alert_uids": []}
    url = validate_api_url(os.environ.get("GRAFANA_URL", ""))
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "").strip()
    if not token:
        fail("GRAFANA_SERVICE_ACCOUNT_TOKEN is required for alert observation")
    client = GrafanaClient(url, token)
    expected = set(expected_uids)
    deadline = time.monotonic() + timeout_seconds
    last_observed: set[str] = set()
    while True:
        last_observed = active_rule_uids(client)
        satisfied = expected <= last_observed if state == "firing" else expected.isdisjoint(last_observed)
        if satisfied:
            return {
                "status": "pass",
                "state": state,
                "observed_alert_uids": sorted(expected & last_observed) if state == "firing" else [],
            }
        if time.monotonic() >= deadline:
            return {
                "status": "fail",
                "state": state,
                "observed_alert_uids": sorted(expected & last_observed),
            }
        time.sleep(10)


def read_phase(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        fail(f"{name} phase evidence is missing or too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail(f"{name} phase evidence is invalid JSON")
    if not isinstance(value, dict) or value.get("status") not in PHASE_STATUSES:
        fail(f"{name} phase evidence has an invalid status")
    report: dict[str, Any] = {"status": value["status"]}
    checks = value.get("checks", [])
    if checks:
        if not isinstance(checks, list) or len(checks) > 32:
            fail(f"{name} phase checks are invalid")
        clean_checks = []
        for check in checks:
            if not isinstance(check, dict):
                fail(f"{name} phase check must be an object")
            check_name = check.get("name")
            check_status = check.get("status")
            if not isinstance(check_name, str) or not SAFE_ID.fullmatch(check_name):
                fail(f"{name} phase check name is unbounded")
            if check_status not in PHASE_STATUSES:
                fail(f"{name} phase check status is invalid")
            clean_checks.append({"name": check_name, "status": check_status})
        report["checks"] = clean_checks
    if name == "observation":
        uids = value.get("observed_alert_uids", [])
        if not isinstance(uids, list) or any(
            not isinstance(uid, str) or not SAFE_ID.fullmatch(uid) for uid in uids
        ):
            fail("observation alert UIDs are invalid")
        report["observed_alert_uids"] = sorted(set(uids))
    return report


def finalized_report(
    item: dict[str, Any],
    variant: str,
    run_id: str,
    phase_paths: dict[str, Path],
    started_at: str | None = None,
) -> dict[str, Any]:
    phases = {name: read_phase(phase_paths[name], name) for name in PHASES}
    result = "pass" if all(value["status"] == "pass" for value in phases.values()) else "fail"
    return {
        "schema_version": 1,
        "drill": item["id"],
        "target": item["target"],
        "variant": variant if item.get("variants") else "not-applicable",
        "run_id": run_id,
        "started_at": started_at or utc_now(),
        "mode": "execute",
        "precheck": phases["precheck"],
        "injection": phases["injection"],
        "observed_alert_uids": phases["observation"].get("observed_alert_uids", []),
        "recovery": phases["recovery"],
        "postcheck": phases["postcheck"],
        "result": result,
        "mutation_performed": phases["injection"]["status"] == "pass",
        "finished_at": utc_now(),
    }


def initialization_started_at(path: Path, item: dict[str, Any], run_id: str) -> str:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        fail("initialization evidence is missing or too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail("initialization evidence is invalid JSON")
    started_at = value.get("started_at") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("drill") != item["id"]
        or value.get("target") != item["target"]
        or value.get("run_id") != run_id
        or value.get("mode") != "execute"
        or not isinstance(started_at, str)
        or not UTC_TIMESTAMP.fullmatch(started_at)
    ):
        fail("initialization evidence does not match this exact drill run")
    return started_at


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "initialize", "observe", "finalize"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--drill", required=True)
    parser.add_argument("--variant", default="not-applicable")
    parser.add_argument("--synthetic-target", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-confirmation", default="")
    parser.add_argument("--execute-confirmation", default="")
    parser.add_argument("--state", choices=("firing", "resolved"))
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initialization-file", type=Path)
    for phase in PHASES:
        parser.add_argument(f"--{phase}-file", type=Path)
    args = parser.parse_args()
    if not SAFE_ID.fullmatch(args.run_id):
        parser.error("--run-id must be a bounded identifier")
    if args.timeout_seconds < 30 or args.timeout_seconds > 1200:
        parser.error("--timeout-seconds must be between 30 and 1200")
    if args.action == "observe" and not args.state:
        parser.error("--state is required for observe")
    if args.action == "finalize" and any(getattr(args, f"{phase}_file") is None for phase in PHASES):
        parser.error("all five phase files are required for finalize")
    return args


def main() -> int:
    args = parse_args()
    contract = load_contract(args.contract)
    item = select_drill(contract, args.drill, args.variant)
    item = select_synthetic_target(item, args.synthetic_target)
    if args.action == "plan":
        report = planned_report(item, args.variant, args.run_id)
    elif args.action == "initialize":
        validate_execute(item, args.target_confirmation, args.execute_confirmation)
        report = planned_report(item, args.variant, args.run_id)
        report.update({"mode": "execute", "result": "initialized"})
    elif args.action == "observe":
        report = observe_alerts(item["expected_alert_uids"], args.state, args.timeout_seconds)
    else:
        phase_paths = {phase: getattr(args, f"{phase}_file") for phase in PHASES}
        started_at = (
            initialization_started_at(args.initialization_file, item, args.run_id)
            if args.initialization_file
            else None
        )
        report = finalized_report(item, args.variant, args.run_id, phase_paths, started_at)
    write_report(args.output, report)
    return 0 if report.get("status", report.get("result")) not in {"fail", "failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
