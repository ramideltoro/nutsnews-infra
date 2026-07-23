locals {
  backend_dashboard_import_ids = toset([
    for dashboard in local.backend_catalog.dashboards : dashboard.uid
  ])
}

import {
  to = grafana_folder.backend_observability
  id = "nutsnews-backend-ops"
}

import {
  for_each = local.backend_dashboard_import_ids
  to       = grafana_dashboard.backend_observability[each.key]
  id       = each.key
}

import {
  to = grafana_rule_group.backend_guardrails
  id = "nutsnews-backend-ops:NutsNews Backend Guardrails"
}
