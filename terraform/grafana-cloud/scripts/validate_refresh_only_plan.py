#!/usr/bin/env python3
"""Fail only when an OpenTofu refresh-only plan contains resource drift.

Root output changes are configuration/state bookkeeping, not evidence that a
remote Grafana resource changed outside OpenTofu. The plan JSON exposes actual
remote changes separately in ``resource_drift``.
"""

from __future__ import annotations

import json
import sys
from typing import Any


class RefreshPlanError(ValueError):
    """Raised when the refresh-only plan JSON cannot be trusted."""


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
        if actions != ["no-op"]:
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
