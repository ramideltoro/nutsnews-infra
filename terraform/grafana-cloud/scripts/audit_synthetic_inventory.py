#!/usr/bin/env python3
"""Run the read-only scheduled Synthetic Monitoring inventory/quota audit."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import verify_post_apply as verifier


def output_value(outputs: dict[str, Any], name: str) -> Any:
    return verifier.terraform_output_value(outputs, name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-outputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    inventory: dict[str, Any] = {
        "enabled_api_check_count": 0,
        "enabled_browser_check_count": 0,
        "monthly_api_execution_estimate": 0,
        "monthly_api_execution_ceiling": verifier.SYNTHETIC_API_EXECUTION_CEILING_MONTHLY,
        "execution_estimate_complete": False,
        "checks": [],
    }
    required = {
        "GRAFANA_SM_URL": os.environ.get("GRAFANA_SM_URL", ""),
        "GRAFANA_SM_ACCESS_TOKEN": os.environ.get("GRAFANA_SM_ACCESS_TOKEN", "").strip(),
        "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON": os.environ.get(
            "NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON", ""
        ).strip(),
    }
    if any(not value for value in required.values()):
        errors.append("scheduled Synthetic Monitoring audit is missing a protected input")
    elif not args.terraform_outputs.is_file():
        errors.append("scheduled Synthetic Monitoring audit is missing Terraform outputs")
    else:
        try:
            outputs = verifier.load_json(args.terraform_outputs)
            desired = verifier.parse_desired_synthetic_checks(
                required["NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON"]
            )
            client = verifier.SyntheticMonitoringClient(
                verifier.validate_synthetic_monitoring_url(required["GRAFANA_SM_URL"]),
                required["GRAFANA_SM_ACCESS_TOKEN"],
            )
            inventory = verifier.remote_synthetic_inventory(
                client,
                output_value(outputs, "synthetic_check_ids"),
                output_value(outputs, "synthetic_probe_selection"),
                desired,
                errors,
            )
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, json.JSONDecodeError):
            # The public artifact must never contain remote response bodies or protected targets.
            errors.append("scheduled Synthetic Monitoring inventory request failed closed")

    report = {
        "schema_version": 1,
        "audit": "grafana-cloud-synthetic-inventory",
        "status": "pass" if not errors else "fail",
        "inventory": inventory,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
