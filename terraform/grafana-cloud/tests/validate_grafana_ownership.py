#!/usr/bin/env python3
"""Validate centralized Grafana Cloud ownership and backend import guardrails."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
CATALOG = json.loads((ROOT / "catalog/backend-observability.json").read_text(encoding="utf-8"))
BACKEND_TF = (ROOT / "backend.tf").read_text(encoding="utf-8")
IMPORTS_TF = (ROOT / "imports.tf").read_text(encoding="utf-8")
MAIN_TF = (ROOT / "main.tf").read_text(encoding="utf-8")
ALERTS_TF = (ROOT / "alerts.tf").read_text(encoding="utf-8")
PLAN_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-plan.yml").read_text(encoding="utf-8")
APPLY_WORKFLOW = (REPO / ".github/workflows/grafana-cloud-apply.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RUNBOOK = (REPO / "runbooks/GRAFANA_CLOUD_OBSERVABILITY.md").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


folder = CATALOG["folder"]
dashboards = CATALOG["dashboards"]
alerts = CATALOG["alerts"]
dashboard_uids = [dashboard["uid"] for dashboard in dashboards]
alert_uids = [alert["uid"] for alert in alerts]

require(folder == {"title": "NutsNews Backend Ops", "uid": "nutsnews-backend-ops"}, "backend folder UID/title changed")
require(len(dashboards) == 10, "backend catalog must preserve all 10 backend dashboards")
require(len(alerts) == 11, "backend catalog must preserve all 11 backend alerts")
require(len(dashboard_uids) == len(set(dashboard_uids)), "backend dashboard UIDs must be unique")
require(len(alert_uids) == len(set(alert_uids)), "backend alert UIDs must be unique")
require("nutsnews-observability" not in dashboard_uids, "backend dashboard UIDs must not collide with VPS folder UID")

for token in (
    'resource "grafana_folder" "backend_observability"',
    'resource "grafana_dashboard" "backend_observability"',
    'resource "grafana_rule_group" "backend_guardrails"',
    'message   = "Managed by nutsnews-infra OpenTofu after backend provisioning handoff."',
    'managed_by             = "nutsnews-infra"',
    "prevent_destroy = true",
):
    require(token in BACKEND_TF, f"backend OpenTofu ownership config missing {token}")

for uid in dashboard_uids:
    require(uid in json.dumps(CATALOG), f"backend catalog missing dashboard UID {uid}")

source_created_dashboards = [
    dashboard for dashboard in dashboards if dashboard.get("importExisting") is False
]
require(len(source_created_dashboards) == 1, "exactly one backend dashboard may be source-created instead of imported")
require(
    [dashboard["uid"] for dashboard in source_created_dashboards] == ["nutsnews-backend-postgres-failover"],
    "only the missing backend PostgreSQL failover dashboard may be source-created instead of imported",
)
require(
    "Grafana Cloud Apply run 29984664724" in source_created_dashboards[0].get("missingRemoteObjectReason", ""),
    "source-created dashboard must document the apply evidence for the missing remote object",
)

for uid in alert_uids:
    require(uid in json.dumps(CATALOG), f"backend catalog missing alert UID {uid}")

require('to = grafana_folder.backend_observability' in IMPORTS_TF, "backend folder import block missing")
require('id = "nutsnews-backend-ops"' in IMPORTS_TF, "backend folder import id missing")
require('for_each = local.backend_dashboard_import_ids' in IMPORTS_TF, "dashboard import must use catalog-driven for_each")
require('if try(dashboard.importExisting, true)' in IMPORTS_TF, "dashboard import ids must skip explicitly source-created missing dashboards")
require('to       = grafana_dashboard.backend_observability[each.key]' in IMPORTS_TF, "dashboard import target missing")
require('id       = each.key' in IMPORTS_TF, "dashboard import ids must be dashboard UIDs")
require(
    'id = "nutsnews-backend-ops:NutsNews Backend Guardrails"' in IMPORTS_TF,
    "backend alert rule group import id missing",
)

for resource_text, name in ((MAIN_TF, "VPS dashboards"), (ALERTS_TF, "VPS alert groups")):
    require("prevent_destroy = true" in resource_text, f"{name} must be lifecycle-protected")

require("plan -refresh-only -detailed-exitcode" in PLAN_WORKFLOW, "Grafana plan workflow must run refresh-only drift detection")
require("Review and reconcile before apply" in PLAN_WORKFLOW, "drift workflow failure must explain reconciliation")
require("verify_post_apply.py" in APPLY_WORKFLOW, "Grafana apply workflow must run post-apply verification")
require("--require-query-data" in APPLY_WORKFLOW, "post-apply verification must require live query data")
require("grafana-cloud-post-apply-verification" in APPLY_WORKFLOW, "verification report artifact missing")

for text, name in ((README, "module README"), (RUNBOOK, "runbook")):
    require("nutsnews-backend is a telemetry producer" in text, f"{name} must record backend producer ownership")
    require("Grafana management/service-account credentials stay only in ramideltoro/nutsnews-infra" in text, f"{name} must document credential boundary")
    require("Do not remove existing backend Grafana resources until import and query/alert verification pass" in text, f"{name} must preserve backend deletion guardrail")

require(
    re.search(r"backend.*nutsnews-backend-ops.*grafana_folder\.backend_observability", README, re.DOTALL) is not None,
    "README must map backend folder UID to its OpenTofu address",
)

print("Grafana Cloud ownership guardrails passed.")
