#!/usr/bin/env python3
"""Fail only when an OpenTofu refresh-only plan contains resource drift.

Root output changes are configuration/state bookkeeping, not evidence that a
remote Grafana resource changed outside OpenTofu. The plan JSON exposes actual
remote changes separately in ``resource_drift``. Grafana's notification policy
API also normalizes omitted timing lists to empty lists; that exact semantic
no-op is ignored while every non-empty or otherwise changed policy still fails.
"""

from __future__ import annotations

import json
import sys
from typing import Any


class RefreshPlanError(ValueError):
    """Raised when the refresh-only plan JSON cannot be trusted."""


NOTIFICATION_POLICY_ADDRESS = "grafana_notification_policy.operations_email"
EMPTY_TIMING_KEYS = {"active_timings", "mute_timings"}


def _without_empty_timing_defaults(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_empty_timing_defaults(item)
            for key, item in value.items()
            if not (key in EMPTY_TIMING_KEYS and item == [])
        }
    if isinstance(value, list):
        return [_without_empty_timing_defaults(item) for item in value]
    return value


def _contains_empty_timing_default(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key in EMPTY_TIMING_KEYS and item == [])
            or _contains_empty_timing_default(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_empty_timing_default(item) for item in value)
    return False


def _is_notification_policy_empty_timing_normalization(
    address: str, change: dict[str, Any], actions: list[str]
) -> bool:
    before = change.get("before")
    after = change.get("after")
    return (
        address == NOTIFICATION_POLICY_ADDRESS
        and actions == ["update"]
        and isinstance(before, dict)
        and isinstance(after, dict)
        and before != after
        and _contains_empty_timing_default(after)
        and _without_empty_timing_defaults(after) == before
    )


def resource_drift_findings(payload: Any) -> list[tuple[str, tuple[str, ...]]]:
    if not isinstance(payload, dict):
        raise RefreshPlanError("Refresh-only plan JSON must be an object.")

    format_version = payload.get("format_version")
    if not isinstance(format_version, str) or format_version.split(".", 1)[0] != "1":
        raise RefreshPlanError("Refresh-only plan JSON has an unsupported format version.")
    if payload.get("errored") is not False:
        raise RefreshPlanError("Refresh-only plan JSON reports an errored plan.")

    resource_drift = payload.get("resource_drift", [])
    if not isinstance(resource_drift, list):
        raise RefreshPlanError("Refresh-only plan resource_drift must be a list.")

    findings: list[tuple[str, tuple[str, ...]]] = []
    for item in resource_drift:
        if not isinstance(item, dict):
            raise RefreshPlanError("Refresh-only plan contains malformed resource drift.")
        address = item.get("address")
        change = item.get("change")
        if not isinstance(address, str) or not address or not isinstance(change, dict):
            raise RefreshPlanError("Refresh-only plan contains malformed resource drift.")
        actions = change.get("actions")
        if (
            not isinstance(actions, list)
            or not actions
            or not all(isinstance(action, str) and action for action in actions)
        ):
            raise RefreshPlanError("Refresh-only plan contains malformed drift actions.")
        if actions != ["no-op"] and not _is_notification_policy_empty_timing_normalization(
            address, change, actions
        ):
            findings.append((address, tuple(actions)))
    return findings


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        findings = resource_drift_findings(payload)
    except (json.JSONDecodeError, RefreshPlanError) as error:
        raise SystemExit(f"Could not validate Grafana Cloud resource drift: {error}") from error

    if findings:
        for address, actions in findings[:100]:
            print(f"Grafana Cloud resource drift: {address} ({','.join(actions)})", file=sys.stderr)
        if len(findings) > 100:
            print(f"Grafana Cloud resource drift: {len(findings) - 100} additional resources", file=sys.stderr)
        raise SystemExit(2)

    print("No Grafana Cloud resource drift detected; root output-only refresh changes are ignored.")


if __name__ == "__main__":
    main()
