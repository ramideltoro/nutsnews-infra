locals {
  backend_catalog        = jsondecode(file("${path.module}/catalog/backend-observability.json"))
  worker_uplift_catalog  = jsondecode(file("${path.module}/catalog/worker-uplift-rabbitmq-alerts.json"))
  backend_dashboard_tags = ["nutsnews", "backend", "grafana-cloud", "gitops"]
  backend_dashboard_catalog = concat(
    local.backend_catalog.dashboards,
    try(local.worker_uplift_catalog.dashboards, []),
  )

  backend_dashboard_specs = {
    for dashboard in local.backend_dashboard_catalog : dashboard.uid => {
      uid         = dashboard.uid
      title       = dashboard.title
      description = try(dashboard.description, "Backend host, runtime, service, backup, database, quota, alert, synthetic, and log observability imported from nutsnews-backend.")
      panels = [
        for panel in dashboard.panels : {
          title       = panel.title
          type        = try(panel.type, "timeseries")
          datasource  = try(panel.datasource, "prometheus")
          unit        = try(panel.unit, "short")
          width       = try(panel.width, 12)
          height      = try(panel.height, 8)
          expr        = panel.expr
          description = try(panel.description, "")
          links = [
            for link in try(panel.links, []) : merge(link, {
              url = replace(try(link.url, ""), "%24%24%7Bloki_datasource_uid%7D", urlencode(var.loki_datasource_uid))
            })
          ]
          noValue = try(panel.noValue, "No data")
        }
      ]
    }
  }

  backend_dashboard_panels = {
    for dashboard_key, spec in local.backend_dashboard_specs : dashboard_key => [
      for index, panel in spec.panels : {
        datasource = {
          type = local.datasource_types[panel.datasource]
          uid  = local.datasource_uids[panel.datasource]
        }
        description = panel.description
        fieldConfig = {
          defaults = {
            color = {
              mode = "palette-classic"
            }
            custom = {
              axisCenteredZero  = false
              axisLabel         = ""
              axisPlacement     = "auto"
              barAlignment      = 0
              drawStyle         = "line"
              fillOpacity       = 10
              gradientMode      = "none"
              lineInterpolation = "linear"
              lineWidth         = 1
              pointSize         = 5
              showPoints        = "never"
              spanNulls         = false
              stacking = {
                group = "A"
                mode  = "none"
              }
              thresholdsStyle = {
                mode = "off"
              }
            }
            mappings = []
            noValue  = panel.noValue
            unit     = panel.unit
          }
          overrides = []
        }
        gridPos = {
          h = panel.height
          w = panel.width
          x = index % 2 == 0 ? 0 : 12
          y = floor(index / 2) * 8
        }
        id    = index + 1
        links = panel.links
        title = panel.title
        type  = panel.type
        options = merge(
          {
            legend = {
              calcs       = []
              displayMode = "list"
              placement   = "bottom"
              showLegend  = true
            }
            tooltip = {
              mode = "single"
              sort = "none"
            }
          },
          panel.type == "logs" ? {
            dedupStrategy      = "none"
            enableLogDetails   = true
            prettifyLogMessage = false
            showCommonLabels   = false
            showLabels         = false
            showTime           = true
            sortOrder          = "Descending"
            wrapLogMessage     = false
          } : {}
        )
        targets = [
          {
            datasource = {
              type = local.datasource_types[panel.datasource]
              uid  = local.datasource_uids[panel.datasource]
            }
            editorMode   = "code"
            expr         = panel.expr
            instant      = false
            interval     = ""
            legendFormat = panel.datasource == "prometheus" ? "{{unit}}{{stage}}{{device}}{{name}}{{__name__}}" : "__auto"
            queryType    = panel.datasource == "loki" ? "range" : ""
            range        = true
            refId        = "A"
            useBackend   = false
          }
        ]
      }
    ]
  }

  backend_alert_rules = {
    for alert in local.backend_catalog.alerts : alert.uid => {
      uid             = alert.uid
      title           = alert.title
      expr            = alert.expr
      datasource      = try(alert.datasource, "prometheus")
      threshold       = try(alert.threshold, 0)
      evaluator       = try(alert.evaluator, "gt")
      for_period      = try(alert["for"], "5m")
      range_seconds   = try(alert.range_seconds, 600)
      no_data_state   = try(alert.no_data_state, "OK")
      exec_err_state  = try(alert.exec_err_state, "Error")
      keep_firing_for = try(alert.keep_firing_for, "5m")
      reducer         = try(alert.reducer, "last")
      severity        = try(alert.severity, "warning")
      service         = try(alert.service, "backend")
      runbook_url     = try(alert.runbook_url, "https://github.com/ramideltoro/nutsnews-infra/blob/main/runbooks/GRAFANA_CLOUD_OBSERVABILITY.md")
      summary         = try(alert.summary, alert.title)
      description     = try(alert.description, "")
      labels          = try(alert.labels, {})
    }
  }

  worker_uplift_alert_rules = {
    for alert in local.worker_uplift_catalog.alerts : alert.uid => {
      uid                     = alert.uid
      title                   = alert.title
      expr                    = alert.expr
      datasource              = try(alert.datasource, "prometheus")
      threshold               = try(alert.threshold, 0)
      threshold_label         = try(alert.threshold_description, tostring(try(alert.threshold, 0)))
      evaluator               = try(alert.evaluator, "gt")
      for_period              = try(alert["for"], "5m")
      range_seconds           = try(alert.range_seconds, 600)
      no_data_state           = try(alert.no_data_state, "OK")
      exec_err_state          = try(alert.exec_err_state, "Error")
      keep_firing_for         = try(alert.keep_firing_for, "5m")
      reducer                 = try(alert.reducer, "last")
      severity                = try(alert.severity, "warning")
      service                 = try(alert.service, "worker")
      queue                   = try(alert.queue, "all")
      owner                   = try(alert.owner, local.worker_uplift_catalog.owner)
      route                   = try(alert.route, local.worker_uplift_catalog.contact_route)
      runbook_url             = try(alert.runbook_url, local.worker_uplift_catalog.runbook_url)
      summary                 = try(alert.summary, alert.title)
      description             = try(alert.description, "")
      slo_id                  = try(alert.slo_id, "none")
      alert_category          = try(alert.alert_category, "worker-uplift")
      test_drill              = try(alert.test_drill, "manual")
      maintenance_suppression = try(alert.maintenance_suppression, local.worker_uplift_catalog.maintenance_suppression)
      labels                  = try(alert.labels, {})
    }
  }
}

resource "grafana_folder" "backend_observability" {
  title                        = local.backend_catalog.folder.title
  uid                          = local.backend_catalog.folder.uid
  prevent_destroy_if_not_empty = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "grafana_dashboard" "backend_observability" {
  for_each = local.backend_dashboard_specs

  folder    = grafana_folder.backend_observability.uid
  overwrite = true
  message   = "Managed by nutsnews-infra OpenTofu after backend provisioning handoff."

  config_json = templatefile("${path.module}/dashboards/nutsnews-dashboard.json.tftpl", {
    description               = each.value.description
    panels_json               = jsonencode(local.backend_dashboard_panels[each.key])
    prometheus_datasource_uid = var.prometheus_datasource_uid
    tags_json                 = jsonencode(local.backend_dashboard_tags)
    title                     = each.value.title
    uid                       = each.value.uid
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "grafana_rule_group" "backend_guardrails" {
  name             = local.backend_catalog.alert_group.name
  folder_uid       = grafana_folder.backend_observability.uid
  interval_seconds = local.backend_catalog.alert_group.interval_seconds

  lifecycle {
    prevent_destroy = true
  }

  dynamic "rule" {
    for_each = local.backend_alert_rules

    content {
      uid             = rule.value.uid
      name            = rule.value.title
      for             = rule.value.for_period
      keep_firing_for = rule.value.keep_firing_for
      condition       = "C"
      no_data_state   = rule.value.no_data_state
      exec_err_state  = rule.value.exec_err_state
      is_paused       = false

      annotations = merge(
        {
          summary     = rule.value.summary
          runbook_url = rule.value.runbook_url
        },
        rule.value.description != "" ? { description = rule.value.description } : {}
      )

      labels = merge(
        {
          component              = "backend-observability"
          deployment_environment = var.deployment_environment
          managed_by             = "nutsnews-infra"
          service                = rule.value.service
          service_namespace      = "nutsnews"
          severity               = rule.value.severity
        },
        rule.value.labels
      )

      data {
        ref_id         = "A"
        datasource_uid = local.datasource_uids[rule.value.datasource]
        query_type     = rule.value.datasource == "loki" ? "range" : ""

        relative_time_range {
          from = rule.value.range_seconds
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
                type   = rule.value.reducer
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "A"
          reducer    = rule.value.reducer
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
                type   = rule.value.evaluator
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

resource "grafana_rule_group" "worker_uplift_guardrails" {
  name             = local.worker_uplift_catalog.alert_group.name
  folder_uid       = grafana_folder.backend_observability.uid
  interval_seconds = local.worker_uplift_catalog.alert_group.interval_seconds

  lifecycle {
    prevent_destroy = true
  }

  dynamic "rule" {
    for_each = local.worker_uplift_alert_rules

    content {
      uid             = rule.value.uid
      name            = rule.value.title
      for             = rule.value.for_period
      keep_firing_for = rule.value.keep_firing_for
      condition       = "C"
      no_data_state   = rule.value.no_data_state
      exec_err_state  = rule.value.exec_err_state
      is_paused       = false

      annotations = {
        summary                 = rule.value.summary
        description             = "${rule.value.description} value={{ $values.B.Value }} threshold=${rule.value.threshold_label} owner=${rule.value.owner} route=${rule.value.route} recovery_window=${rule.value.keep_firing_for} maintenance=${rule.value.maintenance_suppression}"
        runbook_url             = rule.value.runbook_url
        threshold               = rule.value.threshold_label
        recovery_window         = rule.value.keep_firing_for
        test_drill              = rule.value.test_drill
        maintenance_suppression = rule.value.maintenance_suppression
      }

      labels = merge(
        {
          alert_category         = rule.value.alert_category
          component              = "worker-uplift-rabbitmq"
          deployment_environment = var.deployment_environment
          managed_by             = "nutsnews-infra"
          owner                  = rule.value.owner
          queue                  = rule.value.queue
          route                  = rule.value.route
          service                = rule.value.service
          service_namespace      = "nutsnews"
          severity               = rule.value.severity
          slo_id                 = rule.value.slo_id
          threshold              = rule.value.threshold_label
        },
        rule.value.labels
      )

      data {
        ref_id         = "A"
        datasource_uid = local.datasource_uids[rule.value.datasource]
        query_type     = rule.value.datasource == "loki" ? "range" : ""

        relative_time_range {
          from = rule.value.range_seconds
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
                type   = rule.value.reducer
              }
              type = "query"
            }
          ]
          datasource = {
            type = "__expr__"
            uid  = "-100"
          }
          expression = "A"
          reducer    = rule.value.reducer
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
                type   = rule.value.evaluator
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
