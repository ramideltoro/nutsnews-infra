data "grafana_synthetic_monitoring_probes" "available" {
  count = length(var.synthetic_monitoring_probe_ids) > 0 ? 1 : 0
}

locals {
  available_synthetic_monitoring_probes = length(data.grafana_synthetic_monitoring_probes.available) == 0 ? {} : data.grafana_synthetic_monitoring_probes.available[0].probes
  selected_synthetic_monitoring_probe_names = toset([
    for name, id in local.available_synthetic_monitoring_probes : name
    if contains(var.synthetic_monitoring_probe_ids, id)
  ])
}

data "grafana_synthetic_monitoring_probe" "selected" {
  for_each = local.selected_synthetic_monitoring_probe_names

  name = each.key
}

resource "grafana_synthetic_monitoring_check" "http" {
  for_each = local.enabled_synthetic_http_checks

  job                = each.key
  target             = var.synthetic_http_checks[each.key].target
  enabled            = true
  probes             = var.synthetic_monitoring_probe_ids
  frequency          = local.synthetic_http_check_frequency_ms[each.key]
  timeout            = nonsensitive(var.synthetic_http_checks[each.key].timeout_ms)
  basic_metrics_only = true
  alert_sensitivity  = "none"

  lifecycle {
    precondition {
      condition = (
        length(local.selected_synthetic_monitoring_probe_names) == length(var.synthetic_monitoring_probe_ids) &&
        alltrue([for probe in data.grafana_synthetic_monitoring_probe.selected : probe.public])
      )
      error_message = "Every configured Synthetic Monitoring probe ID must resolve to a public probe; private probes are not permitted for this free-tier design."
    }
  }

  labels = {
    service_namespace      = "nutsnews"
    deployment_environment = var.deployment_environment
    check                  = substr(each.key, 0, 32)
    owner                  = "nutsnews-observability"
    service                = "synthetic-monitoring"
  }

  settings {
    http {
      method                          = "GET"
      fail_if_not_ssl                 = true
      no_follow_redirects             = true
      valid_status_codes              = nonsensitive(var.synthetic_http_checks[each.key].valid_status_codes)
      fail_if_body_matches_regexp     = var.synthetic_http_checks[each.key].fail_if_body_matches_regexp
      fail_if_body_not_matches_regexp = var.synthetic_http_checks[each.key].fail_if_body_not_matches_regexp

      dynamic "fail_if_header_matches_regexp" {
        # Dynamic-block identity cannot be sensitive. Reveal only the bounded
        # assertion count; header names and regexps stay sensitive attributes.
        for_each = toset([
          for index in range(nonsensitive(length(var.synthetic_http_checks[each.key].fail_if_header_matches_regexp))) : tostring(index)
        ])

        content {
          allow_missing = var.synthetic_http_checks[each.key].fail_if_header_matches_regexp[tonumber(fail_if_header_matches_regexp.key)].allow_missing
          header        = var.synthetic_http_checks[each.key].fail_if_header_matches_regexp[tonumber(fail_if_header_matches_regexp.key)].header
          regexp        = var.synthetic_http_checks[each.key].fail_if_header_matches_regexp[tonumber(fail_if_header_matches_regexp.key)].regexp
        }
      }

      dynamic "fail_if_header_not_matches_regexp" {
        for_each = toset([
          for index in range(nonsensitive(length(var.synthetic_http_checks[each.key].fail_if_header_not_matches_regexp))) : tostring(index)
        ])

        content {
          allow_missing = var.synthetic_http_checks[each.key].fail_if_header_not_matches_regexp[tonumber(fail_if_header_not_matches_regexp.key)].allow_missing
          header        = var.synthetic_http_checks[each.key].fail_if_header_not_matches_regexp[tonumber(fail_if_header_not_matches_regexp.key)].header
          regexp        = var.synthetic_http_checks[each.key].fail_if_header_not_matches_regexp[tonumber(fail_if_header_not_matches_regexp.key)].regexp
        }
      }
    }
  }
}
