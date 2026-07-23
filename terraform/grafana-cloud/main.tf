resource "grafana_folder" "observability" {
  title = var.folder_title
  uid   = "nutsnews-observability"

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = local.synthetic_monthly_api_executions <= local.synthetic_monthly_api_guardrail
      error_message = "Configured Synthetic Monitoring checks exceed 70% of the current free API execution assumption. Reduce checks, probes, or frequency before applying."
    }
  }
}

resource "grafana_dashboard" "observability" {
  for_each = local.dashboard_specs

  folder    = grafana_folder.observability.uid
  overwrite = true
  message   = "Managed by nutsnews-infra OpenTofu."

  config_json = templatefile("${path.module}/dashboards/nutsnews-dashboard.json.tftpl", {
    description               = each.value.description
    panels_json               = jsonencode(local.dashboard_panels[each.key])
    prometheus_datasource_uid = var.prometheus_datasource_uid
    tags_json                 = jsonencode(local.dashboard_tags)
    title                     = each.value.title
    uid                       = each.value.uid
  })

  lifecycle {
    prevent_destroy = true
  }
}
