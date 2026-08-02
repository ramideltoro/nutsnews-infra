locals {
  linux_integration_alert_replacement_catalog = jsondecode(
    file("${path.module}/catalog/linux-integration-alert-replacements.json")
  )
}

resource "grafana_rule_group" "linux_integration_alert_replacements" {
  name             = local.linux_integration_alert_replacement_catalog.groupName
  folder_uid       = grafana_folder.observability.uid
  interval_seconds = local.linux_integration_alert_replacement_catalog.intervalSeconds

  lifecycle {
    prevent_destroy = true
  }

  dynamic "rule" {
    for_each = {
      for item in local.linux_integration_alert_replacement_catalog.rules :
      item.replacementUid => item
    }

    content {
      uid            = rule.value.replacementUid
      name           = rule.value.title
      for            = rule.value.for
      condition      = rule.value.condition
      no_data_state  = rule.value.noDataState
      exec_err_state = rule.value.execErrState
      is_paused      = false

      annotations = {
        summary       = rule.value.summary
        description   = rule.value.description
        dashboard_url = "/d/nutsnews-vps-overview"
        runbook_url   = local.grafana_observability_runbook_url
      }

      labels = {
        service_namespace      = "nutsnews"
        deployment_environment = var.deployment_environment
        managed_by             = "nutsnews-infra"
        owner                  = "nutsnews-observability"
        route                  = var.quota_alert_contact_route
        service                = "vps-host"
        severity               = rule.value.normalizedSeverity
        source_integration     = "linux-node"
      }

      data {
        ref_id         = "query"
        query_type     = "prometheus"
        datasource_uid = var.prometheus_datasource_uid

        relative_time_range {
          from = rule.value.queryFrom
          to   = rule.value.queryTo
        }

        model = jsonencode({
          datasource = {
            type = "prometheus"
            uid  = var.prometheus_datasource_uid
          }
          expr          = rule.value.expr
          instant       = true
          intervalMs    = 1000
          maxDataPoints = 43200
          range         = false
          refId         = "query"
        })
      }

      data {
        ref_id         = "prometheus_math"
        query_type     = "math"
        datasource_uid = "__expr__"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          datasource = {
            type = "__expr__"
            uid  = "__expr__"
          }
          expression    = "is_number($query) || is_nan($query) || is_inf($query)"
          intervalMs    = 1000
          maxDataPoints = 43200
          refId         = "prometheus_math"
          type          = "math"
        })
      }

      data {
        ref_id         = "threshold"
        query_type     = "threshold"
        datasource_uid = "__expr__"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [{
            evaluator = {
              params = [0]
              type   = "gt"
            }
          }]
          datasource = {
            type = "__expr__"
            uid  = "__expr__"
          }
          expression    = "prometheus_math"
          intervalMs    = 1000
          maxDataPoints = 43200
          refId         = "threshold"
          type          = "threshold"
        })
      }
    }
  }
}

output "linux_integration_alert_replacement_uids" {
  description = "Source-owned normalized replacements for the disabled vendor Linux integration alert bundle."
  value = {
    for item in local.linux_integration_alert_replacement_catalog.rules :
    item.sourceUid => item.replacementUid
  }
}
