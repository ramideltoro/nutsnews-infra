resource "grafana_folder" "observability" {
  title = var.folder_title
  uid   = "nutsnews-observability"

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = local.synthetic_monthly_api_executions < local.synthetic_monthly_api_guardrail
      error_message = "Configured Synthetic Monitoring checks must remain below both 90% of the current free API execution assumption and the absolute 90,000-execution monthly ceiling. Reduce checks or probes before applying."
    }

    precondition {
      condition     = length(local.enabled_synthetic_http_checks) == 5 && length(var.synthetic_monitoring_probe_ids) == 2
      error_message = "Production Synthetic Monitoring requires all five approved enabled checks across exactly two public probes."
    }

    precondition {
      condition     = local.synthetic_target_role_contract
      error_message = "Synthetic Monitoring requires one shared canonical host plus distinct direct-VPS and Vercel-secondary readiness hosts."
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
    extra_variables           = []
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
