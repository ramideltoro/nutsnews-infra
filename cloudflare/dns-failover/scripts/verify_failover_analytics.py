#!/usr/bin/env python3
"""Produce value-free deployed FAILOVER_ANALYTICS evidence."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


DATASET = "nutsnews_dns_failover_v1"
SCRIPT_NAME = "nutsnews-dns-failover"
GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def summarize_bindings(settings: dict) -> dict:
    bindings = settings.get("result", {}).get("bindings", [])
    by_name = {
        str(item.get("name", "")): str(item.get("type", ""))
        for item in bindings
        if isinstance(item, dict)
    }
    return {
        "query_succeeded": settings.get("success") is True,
        "failover_analytics": {
            "name": "FAILOVER_ANALYTICS",
            "type": by_name.get("FAILOVER_ANALYTICS"),
            "present": by_name.get("FAILOVER_ANALYTICS") == "analytics_engine",
        },
        "dns_failover": {
            "name": "DNS_FAILOVER",
            "type": by_name.get("DNS_FAILOVER"),
            "present": by_name.get("DNS_FAILOVER") == "durable_object_namespace",
        },
        "binding_names_and_types": [
            {"name": name, "type": by_name[name]} for name in sorted(by_name)
        ],
    }


def summarize_schedules(schedules: dict) -> dict:
    values = [
        str(item.get("cron", ""))
        for item in schedules.get("result", [])
        if isinstance(item, dict)
    ]
    return {
        "query_succeeded": schedules.get("success") is True,
        "crons": values,
        "minute_watchdog_present": "* * * * *" in values,
    }


def graphql_payload(account_id: str, window_start: datetime) -> dict:
    return {
        "query": """
query FailoverAnalytics($accountTag: string!, $start: Time!, $dataset: string!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      workersAnalyticsEngineAdaptiveGroups(
        limit: 20
        filter: {datetime_geq: $start, dataset: $dataset}
      ) {
        count
        dimensions { dataset datetimeMinute }
      }
    }
  }
}
""".strip(),
        "variables": {
            "accountTag": account_id,
            "start": isoformat(window_start),
            "dataset": DATASET,
        },
    }


def summarize_graphql(response: dict) -> dict:
    errors = response.get("errors") or []
    accounts = (
        ((response.get("data") or {}).get("viewer") or {}).get("accounts") or []
    )
    groups: list[dict] = []
    for account in accounts:
        if isinstance(account, dict):
            groups.extend(account.get("workersAnalyticsEngineAdaptiveGroups") or [])
    counts = [
        float(item.get("count") or 0)
        for item in groups
        if isinstance(item, dict)
    ]
    latest = sorted(
        str((item.get("dimensions") or {}).get("datetimeMinute") or "")
        for item in groups
        if isinstance(item, dict)
    )
    return {
        "query_succeeded": not errors and isinstance(accounts, list),
        "error_count": len(errors),
        "positive_event_count": sum(counts) > 0,
        "sampled_event_count": sum(counts),
        "latest_event_minute_utc": latest[-1] if latest and latest[-1] else None,
    }


def write_proof(path: Path, proof: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    deploy_token = os.environ.get("CLOUDFLARE_DEPLOY_API_TOKEN", "")
    analytics_token = os.environ.get("CLOUDFLARE_ANALYTICS_API_TOKEN", "")
    if not account_id or not deploy_token or not analytics_token:
        print("Required Cloudflare proof credentials are missing.", flush=True)
        return 2

    api_base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
    checked_at = utc_now()
    try:
        settings = request_json(
            f"{api_base}/workers/scripts/{SCRIPT_NAME}/settings",
            token=deploy_token,
        )
        schedules = request_json(
            f"{api_base}/workers/scripts/{SCRIPT_NAME}/schedules",
            token=deploy_token,
        )
    except urllib.error.HTTPError as error:
        proof = {
            "schema_version": 1,
            "status": "fail",
            "checked_at_utc": isoformat(checked_at),
            "http_status": error.code,
            "state_changes_performed_by_verification": False,
            "secret_values_recorded": False,
        }
        write_proof(args.output, proof)
        print(f"Cloudflare deployed-settings proof failed with HTTP {error.code}.")
        return 1

    binding_summary = summarize_bindings(settings)
    schedule_summary = summarize_schedules(schedules)
    window_start = checked_at - timedelta(minutes=20)
    query_summary = {
        "query_succeeded": False,
        "error_count": 0,
        "positive_event_count": False,
        "sampled_event_count": 0,
        "latest_event_minute_utc": None,
    }
    deadline = time.monotonic() + max(0, args.wait_seconds)
    while True:
        try:
            response = request_json(
                GRAPHQL_URL,
                token=analytics_token,
                method="POST",
                payload=graphql_payload(account_id, window_start),
            )
            query_summary = summarize_graphql(response)
        except urllib.error.HTTPError as error:
            query_summary = {
                "query_succeeded": False,
                "error_count": 1,
                "positive_event_count": False,
                "sampled_event_count": 0,
                "latest_event_minute_utc": None,
                "http_status": error.code,
            }
        if query_summary["positive_event_count"] or time.monotonic() >= deadline:
            break
        print("Analytics event not visible yet; retrying.", flush=True)
        time.sleep(max(1, args.poll_seconds))

    passed = all(
        (
            binding_summary["query_succeeded"],
            binding_summary["failover_analytics"]["present"],
            binding_summary["dns_failover"]["present"],
            schedule_summary["query_succeeded"],
            schedule_summary["minute_watchdog_present"],
            query_summary["query_succeeded"],
            query_summary["positive_event_count"],
        )
    )
    proof = {
        "schema_version": 1,
        "status": "pass" if passed else "fail",
        "checked_at_utc": isoformat(utc_now()),
        "source_commit": os.environ.get("GITHUB_SHA") or None,
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "script_name": SCRIPT_NAME,
        "dataset": DATASET,
        "bindings": binding_summary,
        "schedules": schedule_summary,
        "analytics_query": {
            **query_summary,
            "method": "cloudflare_graphql_workers_analytics_engine",
            "window_start_utc": isoformat(window_start),
        },
        "value_policy": {
            "binding_names_and_types_only": True,
            "account_identifiers_recorded": False,
            "zone_identifiers_recorded": False,
            "dns_record_data_recorded": False,
            "secret_values_recorded": False,
        },
        "state_changes_performed_by_verification": False,
    }
    write_proof(args.output, proof)
    if not passed:
        print("FAILOVER_ANALYTICS deployed proof did not pass.")
        return 1
    print("FAILOVER_ANALYTICS deployed binding and query proof passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
