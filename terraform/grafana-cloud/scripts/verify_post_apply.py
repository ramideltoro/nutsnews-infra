#!/usr/bin/env python3
"""Verify OpenTofu-managed Grafana Cloud resources after protected apply."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_CATALOG = ROOT / "catalog" / "backend-observability.json"

VPS_DASHBOARD_UIDS = {
    "nutsnews-vps-overview",
    "nutsnews-logs-overview",
    "nutsnews-cpu-load-processes",
    "nutsnews-memory-swap",
    "nutsnews-disk-filesystem-io",
    "nutsnews-network-caddy-edge",
    "nutsnews-docker-compose-containers",
    "nutsnews-systemd-services-timers",
    "nutsnews-logs-security-auth",
    "nutsnews-backups-restore-verification",
    "nutsnews-ops-portal-reporting",
    "nutsnews-application-service-health",
    "nutsnews-synthetic-uptime-api-checks",
    "nutsnews-grafana-cloud-usage-quota",
}

VPS_ALERT_RULE_GROUPS = {
    ("nutsnews-observability", "NutsNews Grafana Cloud quota guardrails"),
    ("nutsnews-observability", "NutsNews log pipeline health"),
}

PROMETHEUS_QUERIES = {
    "vps_node_exporter": 'up{job=~"integrations/node_exporter"}',
    "backend_host": 'up{job="nutsnews-backend-host"}',
    "backend_public_endpoint": 'nutsnews_backend_public_endpoint_healthy{job="nutsnews-backend-host"}',
}

LOKI_QUERIES = {
    "backend_host_logs": '{host="backend.nutsnews.com"}',
    "backend_journal": '{host="backend.nutsnews.com",source="journal"}',
}


class GrafanaClient:
    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Grafana API {method} {path} failed with {exc.code}: {detail}") from exc
        return json.loads(raw) if raw else {}


def env(name: str, fallback: str = "") -> str:
    return os.environ.get(name, os.environ.get(fallback, "")).strip()


def load_backend_catalog() -> dict[str, Any]:
    return json.loads(BACKEND_CATALOG.read_text(encoding="utf-8"))


def prometheus_query(client: GrafanaClient, datasource_uid: str, query: str) -> dict[str, Any]:
    encoded = urllib.parse.urlencode({"query": query})
    response = client.request(
        "GET",
        f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid)}/api/v1/query?{encoded}",
    )
    data = response.get("data", {})
    result = data.get("result", []) if isinstance(data, dict) else []
    return {
        "query": query,
        "status": response.get("status", "unknown"),
        "result_count": len(result) if isinstance(result, list) else 0,
    }


def loki_query_range(client: GrafanaClient, datasource_uid: str, query: str, hours: int) -> dict[str, Any]:
    end = int(time.time() * 1_000_000_000)
    start = end - (hours * 60 * 60 * 1_000_000_000)
    encoded = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start),
            "end": str(end),
            "limit": "20",
            "direction": "backward",
        }
    )
    response = client.request(
        "GET",
        f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid)}/loki/api/v1/query_range?{encoded}",
    )
    data = response.get("data", {})
    result = data.get("result", []) if isinstance(data, dict) else []
    line_count = 0
    if isinstance(result, list):
        for stream in result:
            values = stream.get("values", []) if isinstance(stream, dict) else []
            if isinstance(values, list):
                line_count += len(values)
    return {
        "query": query,
        "status": response.get("status", "unknown"),
        "result_count": len(result) if isinstance(result, list) else 0,
        "line_count": line_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-query-data", action="store_true")
    parser.add_argument("--loki-hours", type=int, default=6)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = env("TF_VAR_grafana_url", "GRAFANA_URL")
    token = env("TF_VAR_grafana_service_account_token", "GRAFANA_SERVICE_ACCOUNT_TOKEN")
    prometheus_uid = env("TF_VAR_prometheus_datasource_uid", "GRAFANA_PROMETHEUS_DATASOURCE_UID")
    loki_uid = env("TF_VAR_loki_datasource_uid", "GRAFANA_LOKI_DATASOURCE_UID")

    missing = [
        name
        for name, value in {
            "TF_VAR_grafana_url": url,
            "TF_VAR_grafana_service_account_token": token,
            "TF_VAR_prometheus_datasource_uid": prometheus_uid,
            "TF_VAR_loki_datasource_uid": loki_uid,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing required environment values: {', '.join(missing)}", file=sys.stderr)
        return 1

    catalog = load_backend_catalog()
    backend_dashboard_uids = {dashboard["uid"] for dashboard in catalog["dashboards"]}
    backend_alert_uids = {alert["uid"] for alert in catalog["alerts"]}
    client = GrafanaClient(url, token)
    errors: list[str] = []

    folders = {}
    for uid in ("nutsnews-observability", catalog["folder"]["uid"]):
        try:
            folders[uid] = client.request("GET", f"/api/folders/{urllib.parse.quote(uid)}").get("title")
        except RuntimeError as exc:
            errors.append(str(exc))

    dashboards = {}
    for uid in sorted(VPS_DASHBOARD_UIDS | backend_dashboard_uids):
        try:
            dashboard = client.request("GET", f"/api/dashboards/uid/{urllib.parse.quote(uid)}")
            dashboards[uid] = dashboard.get("dashboard", {}).get("title", "")
        except RuntimeError as exc:
            errors.append(str(exc))

    alerts = {}
    for uid in sorted(backend_alert_uids):
        try:
            alert = client.request("GET", f"/api/v1/provisioning/alert-rules/{urllib.parse.quote(uid)}")
            alerts[uid] = alert.get("title", "")
        except RuntimeError as exc:
            errors.append(str(exc))

    for folder_uid, group_name in sorted(VPS_ALERT_RULE_GROUPS | {(catalog["folder"]["uid"], catalog["alert_group"]["name"])}):
        group_alerts = [
            alert_uid
            for alert_uid, title in alerts.items()
            if folder_uid == catalog["folder"]["uid"] and title
        ]
        if folder_uid == catalog["folder"]["uid"] and not group_alerts:
            errors.append(f"missing alert rules for group {folder_uid}:{group_name}")

    prometheus = {
        name: prometheus_query(client, prometheus_uid, query)
        for name, query in PROMETHEUS_QUERIES.items()
    }
    loki = {
        name: loki_query_range(client, loki_uid, query, args.loki_hours)
        for name, query in LOKI_QUERIES.items()
    }

    if args.require_query_data:
        for name, item in prometheus.items():
            if item["result_count"] < 1:
                errors.append(f"Prometheus query returned no data: {name}")
        for name, item in loki.items():
            if item["line_count"] < 1:
                errors.append(f"Loki query returned no log lines: {name}")

    report = {
        "status": "pass" if not errors else "fail",
        "folders": folders,
        "dashboard_count": len(dashboards),
        "backend_alert_count": len(alerts),
        "prometheus_queries": prometheus,
        "loki_queries": loki,
        "errors": errors,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
