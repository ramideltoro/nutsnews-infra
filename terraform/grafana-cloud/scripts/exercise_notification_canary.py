#!/usr/bin/env python3
"""Fire and resolve a uniquely named Grafana Alertmanager notification canary."""

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
from typing import Any


REQUIRED_LABELS = {
    "alertname",
    "deployment_environment",
    "owner",
    "route",
    "service",
    "severity",
}
GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"
GRAFANA_CANARY_DASHBOARD_URL = (
    f"https://{GRAFANA_UI_HOSTNAME}/d/nutsnews-vps-overview"
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


def validate_api_url(value: str, name: str = "GRAFANA_URL") -> str:
    """Return the exact query-free NutsNews Grafana HTTPS origin."""
    invalid_origin = f"{name} must be a query-free HTTPS Grafana Cloud API origin"
    if value != value.strip():
        raise ValueError(invalid_origin)
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        raise ValueError(invalid_origin) from None
    if (
        parsed.scheme != "https"
        or hostname != GRAFANA_UI_HOSTNAME
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower()
        not in {GRAFANA_UI_HOSTNAME, f"{GRAFANA_UI_HOSTNAME}:443"}
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(invalid_origin)
    return f"https://{GRAFANA_UI_HOSTNAME}"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso8601(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def safe_canary_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    if not normalized or len(normalized) > 80:
        raise ValueError("canary ID must contain 1-80 safe characters")
    return normalized


def build_alert(
    canary_id: str,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
) -> dict[str, Any]:
    canary_id = safe_canary_id(canary_id)
    labels = {
        "alertname": f"NutsNewsNotificationCanary-{canary_id}",
        "deployment_environment": "production",
        "owner": "nutsnews-observability",
        "route": "operations-email",
        "service": "grafana-notification-canary",
        "severity": "critical",
    }
    if set(labels) != REQUIRED_LABELS:
        raise ValueError("notification canary labels drifted from the routing contract")
    return {
        "labels": labels,
        "annotations": {
            "canary_id": canary_id,
            "dashboard_url": GRAFANA_CANARY_DASHBOARD_URL,
            "description": (
                "Deliberate Grafana notification-path test. Confirm the firing and resolved "
                "email pair is retained under this unique alert name."
            ),
            "runbook_url": (
                "https://github.com/ramideltoro/nutsnews-infra/blob/main/"
                "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md#alert-delivery-and-notification-canary"
            ),
            "summary": f"Grafana notification canary {canary_id}",
        },
        "startsAt": iso8601(starts_at),
        "endsAt": iso8601(ends_at),
        "generatorURL": GRAFANA_CANARY_DASHBOARD_URL,
    }


class AlertmanagerClient:
    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = validate_api_url(url)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            exc.close()
            raise RuntimeError(
                f"Grafana Alertmanager {method} {path} failed with {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Grafana Alertmanager {method} {path} failed: {exc.reason}"
            ) from exc
        return json.loads(raw) if raw else {}


def matching_active_alerts(response: Any, alert_name: str) -> list[dict[str, Any]]:
    if not isinstance(response, list):
        return []
    return [
        item
        for item in response
        if isinstance(item, dict)
        and isinstance(item.get("labels"), dict)
        and item["labels"].get("alertname") == alert_name
    ]


def active_alerts(client: AlertmanagerClient, alert_name: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "active": "true",
            "silenced": "false",
            "inhibited": "false",
            "filter": f'alertname="{alert_name}"',
        }
    )
    response = client.request(
        "GET", f"/api/alertmanager/grafana/api/v2/alerts?{query}"
    )
    return matching_active_alerts(response, alert_name)


def wait_for_state(
    client: AlertmanagerClient,
    alert_name: str,
    expected_active: bool,
    timeout_seconds: int,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        observed_active = bool(active_alerts(client, alert_name))
        if observed_active is expected_active:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canary-id", required=True)
    parser.add_argument("--hold-seconds", type=int, default=45)
    parser.add_argument("--state-timeout-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.hold_seconds < 35:
        raise SystemExit("--hold-seconds must exceed the critical route's 30-second group wait")
    if args.state_timeout_seconds < 30:
        raise SystemExit("--state-timeout-seconds must be at least 30 seconds")

    raw_url = os.environ.get("GRAFANA_URL", "")
    try:
        url = validate_api_url(raw_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "").strip()
    if not token:
        print("GRAFANA_SERVICE_ACCOUNT_TOKEN is required", file=sys.stderr)
        return 1

    canary_id = safe_canary_id(args.canary_id)
    client = AlertmanagerClient(url, token)
    starts_at = utc_now()
    firing = build_alert(canary_id, starts_at, starts_at + dt.timedelta(minutes=15))
    alert_name = firing["labels"]["alertname"]
    report: dict[str, Any] = {
        "alertname": alert_name,
        "canary_id": canary_id,
        "fired_at": iso8601(starts_at),
        "firing_state_observed": False,
        "held_seconds": args.hold_seconds,
        "resolved_at": None,
        "resolved_state_observed": False,
        "status": "fail",
    }

    try:
        client.request("POST", "/api/alertmanager/grafana/api/v2/alerts", [firing])
        report["firing_state_observed"] = wait_for_state(
            client, alert_name, True, args.state_timeout_seconds
        )
        if not report["firing_state_observed"]:
            raise RuntimeError("canary did not become active before timeout")
        time.sleep(args.hold_seconds)

        resolved_at = utc_now()
        resolved = build_alert(canary_id, starts_at, resolved_at)
        client.request("POST", "/api/alertmanager/grafana/api/v2/alerts", [resolved])
        report["resolved_at"] = iso8601(resolved_at)
        report["resolved_state_observed"] = wait_for_state(
            client, alert_name, False, args.state_timeout_seconds
        )
        if not report["resolved_state_observed"]:
            raise RuntimeError("canary did not resolve before timeout")
        report["status"] = "pass"
    except (RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
