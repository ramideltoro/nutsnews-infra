resource "grafana_folder" "observability" {
  title = var.folder_title
  uid   = "nutsnews-observability"

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = local.synthetic_monthly_api_executions < local.synthetic_monthly_api_guardrail
      error_message = "Configured Synthetic Monitoring checks must remain below 90% of the current free API execution assumption. Reduce checks or probes before applying."
    }

    precondition {
      condition = (
        !var.enforce_rollout_decisions ||
        length(local.enabled_synthetic_http_checks) == 0 ||
        local.synthetic_monthly_api_executions < local.synthetic_monthly_api_major_threshold ||
        var.synthetic_major_forecast_acknowledged
      )
      error_message = "Synthetic Monitoring projects into the >=85% major forecast band. Production plan/apply is blocked until a reviewed decision is made: set the protected synthetic_major_forecast_acknowledged=true only to retain five checks x two probes x five-minute cadence; a different cadence, topology, or threshold requires a source change."
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
