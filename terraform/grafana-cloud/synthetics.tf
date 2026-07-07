resource "grafana_synthetic_monitoring_check" "http" {
  for_each = local.enabled_synthetic_http_checks

  job                = each.key
  target             = each.value.target
  enabled            = true
  probes             = var.synthetic_monitoring_probe_ids
  frequency          = each.value.frequency_ms
  timeout            = each.value.timeout_ms
  basic_metrics_only = true
  alert_sensitivity  = "none"
  folder_uid         = grafana_folder.observability.uid

  labels = {
    service_namespace      = "nutsnews"
    deployment_environment = var.deployment_environment
    check                  = substr(each.key, 0, 32)
    cost_guardrail         = "free"
  }

  settings {
    http {
      method                          = "GET"
      fail_if_not_ssl                 = true
      valid_status_codes              = each.value.valid_status_codes
      fail_if_body_matches_regexp     = each.value.fail_if_body_matches_regexp
      fail_if_body_not_matches_regexp = each.value.fail_if_body_not_matches_regexp
    }
  }
}
