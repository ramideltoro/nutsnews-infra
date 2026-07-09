resource "grafana_rule_group" "quota_guardrails" {
  name             = "NutsNews Grafana Cloud quota guardrails"
  folder_uid       = grafana_folder.observability.uid
  interval_seconds = 300

  dynamic "rule" {
    for_each = {
      for item in local.quota_alert_rules : item.key => item
    }

    content {
      name           = rule.value.title
      for            = rule.value.for_period
      condition      = "C"
      no_data_state  = "NoData"
      exec_err_state = "Error"
      is_paused      = false

      annotations = {
        summary     = rule.value.title
        description = "Grafana Cloud usage is above the configured free-tier guardrail assumption. Check the Usage / Quota dashboard before enabling additional telemetry."
      }

      labels = {
        service_namespace      = "nutsnews"
        deployment_environment = var.deployment_environment
        managed_by             = "nutsnews-infra"
        route                  = var.quota_alert_contact_route
        severity               = rule.value.severity
      }

      data {
        ref_id         = "A"
        datasource_uid = var.usage_datasource_uid

        relative_time_range {
          from = 1800
          to   = 0
        }

        model = jsonencode({
          datasource = {
            type = "prometheus"
            uid  = var.usage_datasource_uid
          }
          editorMode    = "code"
          expr          = rule.value.expr
          instant       = false
          intervalMs    = 1000
          legendFormat  = rule.value.title
          maxDataPoints = 43200
          range         = true
          refId         = "A"
        })
      }

      data {
        ref_id         = "B"
        datasource_uid = "-100"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [
            {
              evaluator = {
                params = []
                type   = "gt"
              }
              operator = {
                type = "and"
              }
              query = {
                params = ["B"]
              }
              reducer = {
                params = []
                type   = "last"
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "A"
          reducer    = "last"
          refId      = "B"
          type       = "reduce"
        })
      }

      data {
        ref_id         = "C"
        datasource_uid = "-100"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [
            {
              evaluator = {
                params = [rule.value.threshold]
                type   = "gt"
              }
              operator = {
                type = "and"
              }
              query = {
                params = ["C"]
              }
              reducer = {
                params = []
                type   = "last"
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "B"
          refId      = "C"
          type       = "threshold"
        })
      }
    }
  }
}

resource "grafana_rule_group" "log_pipeline" {
  name             = "NutsNews log pipeline health"
  folder_uid       = grafana_folder.observability.uid
  interval_seconds = 300

  dynamic "rule" {
    for_each = local.log_pipeline_alert_rules

    content {
      name           = rule.value.title
      for            = rule.value.for_period
      condition      = "C"
      no_data_state  = rule.value.no_data_state
      exec_err_state = "Error"
      is_paused      = false

      annotations = {
        summary     = rule.value.title
        description = rule.value.description
      }

      labels = {
        service_namespace      = "nutsnews"
        deployment_environment = var.deployment_environment
        managed_by             = "nutsnews-infra"
        route                  = var.quota_alert_contact_route
        severity               = rule.value.severity
      }

      data {
        ref_id         = "A"
        datasource_uid = local.datasource_uids[rule.value.datasource]

        relative_time_range {
          from = 1800
          to   = 0
        }

        model = jsonencode(merge(
          {
            datasource = {
              type = local.datasource_types[rule.value.datasource]
              uid  = local.datasource_uids[rule.value.datasource]
            }
            editorMode    = "code"
            expr          = rule.value.expr
            instant       = false
            intervalMs    = 1000
            legendFormat  = rule.value.title
            maxDataPoints = 43200
            range         = true
            refId         = "A"
          },
          rule.value.datasource == "loki" ? {
            queryType  = "range"
            useBackend = false
          } : {}
        ))
      }

      data {
        ref_id         = "B"
        datasource_uid = "-100"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [
            {
              evaluator = {
                params = []
                type   = "gt"
              }
              operator = {
                type = "and"
              }
              query = {
                params = ["B"]
              }
              reducer = {
                params = []
                type   = "last"
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "A"
          reducer    = "last"
          refId      = "B"
          type       = "reduce"
        })
      }

      data {
        ref_id         = "C"
        datasource_uid = "-100"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [
            {
              evaluator = {
                params = [rule.value.threshold]
                type   = "gt"
              }
              operator = {
                type = "and"
              }
              query = {
                params = ["C"]
              }
              reducer = {
                params = []
                type   = "last"
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "B"
          refId      = "C"
          type       = "threshold"
        })
      }
    }
  }
}
