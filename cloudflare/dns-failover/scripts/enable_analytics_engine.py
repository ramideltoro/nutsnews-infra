#!/usr/bin/env python3
"""Enable Workers Analytics Engine through the protected infra workflow."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


RATE_PLAN_ID = "beta_analytics_engine_api"


class ActivationError(RuntimeError):
    """A value-free activation validation failure."""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def analytics_subscription(subscriptions: dict) -> dict | None:
    for item in subscriptions.get("result") or []:
        if not isinstance(item, dict):
            continue
        rate_plan = item.get("rate_plan") or {}
        if str(rate_plan.get("id") or "").lower() == RATE_PLAN_ID:
            return item
    return None


def safe_subscription_summary(subscription: dict | None) -> dict:
    rate_plan = (subscription or {}).get("rate_plan") or {}
    return {
        "present": subscription is not None,
        "rate_plan": str(rate_plan.get("id") or "").lower() or None,
        "state": str((subscription or {}).get("state") or "") or None,
        "price_usd": (subscription or {}).get("price"),
    }


def write_proof(path: Path, proof: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def activate(api_base: str, token: str) -> tuple[dict, int]:
    subscriptions_url = f"{api_base}/subscriptions"
    before = request_json(subscriptions_url, token=token)
    if before.get("success") is not True:
        raise ActivationError("Subscription inventory query did not succeed.")
    existing = analytics_subscription(before)
    activation_performed = existing is None
    if existing is None:
        response = request_json(
            subscriptions_url,
            token=token,
            method="POST",
            payload={
                "frequency": "monthly",
                "price": 0,
                "rate_plan": {"id": RATE_PLAN_ID},
            },
        )
        if response.get("success") is not True:
            raise ActivationError("Subscription activation did not succeed.")
        candidate = response.get("result")
        if isinstance(candidate, dict):
            existing = candidate

    after = request_json(subscriptions_url, token=token)
    if after.get("success") is not True:
        raise ActivationError("Post-activation inventory query did not succeed.")
    active = analytics_subscription(after) or existing
    summary = safe_subscription_summary(active)
    passed = all(
        (
            summary["present"],
            summary["rate_plan"] == RATE_PLAN_ID,
            summary["state"] in {"Paid", "Provisioned", "Trial"},
            summary["price_usd"] == 0,
        )
    )
    return (
        {
            "schema_version": 1,
            "status": "pass" if passed else "fail",
            "checked_at_utc": iso_now(),
            "source_commit": os.environ.get("GITHUB_SHA") or None,
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID") or None,
            "activation_requested": True,
            "activation_performed": activation_performed,
            "subscription": summary,
            "value_policy": {
                "subscription_identifier_recorded": False,
                "account_identifier_recorded": False,
                "billing_profile_recorded": False,
                "payment_data_recorded": False,
                "secret_values_recorded": False,
            },
            "dns_or_worker_state_changed": False,
        },
        0 if passed else 1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    if args.confirm != "enable-analytics-engine-for-nutsnews":
        print("Analytics Engine activation confirmation did not match.", flush=True)
        return 2

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    billing_token = os.environ.get("CLOUDFLARE_BILLING_API_TOKEN", "")
    if not account_id or not billing_token:
        print("Required Cloudflare activation credentials are missing.", flush=True)
        return 2

    api_base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
    try:
        proof, result = activate(api_base, billing_token)
    except (urllib.error.HTTPError, ActivationError) as error:
        proof = {
            "schema_version": 1,
            "status": "fail",
            "checked_at_utc": iso_now(),
            "activation_requested": True,
            "activation_performed": False,
            "value_policy": {
                "subscription_identifier_recorded": False,
                "account_identifier_recorded": False,
                "billing_profile_recorded": False,
                "payment_data_recorded": False,
                "secret_values_recorded": False,
            },
            "dns_or_worker_state_changed": False,
        }
        if isinstance(error, urllib.error.HTTPError):
            proof["http_status"] = error.code
        result = 1

    write_proof(args.output, proof)
    if result:
        print("Protected Analytics Engine activation proof did not pass.", flush=True)
    else:
        print("Protected Analytics Engine activation proof passed.", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
