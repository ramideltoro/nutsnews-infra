locals {
  dashboard_tags = ["nutsnews", "vps", "grafana-cloud", "gitops"]

  datasource_uids = {
    prometheus = var.prometheus_datasource_uid
    loki       = var.loki_datasource_uid
    usage      = var.usage_datasource_uid
  }

  datasource_types = {
    prometheus = "prometheus"
    loki       = "loki"
    usage      = "prometheus"
  }

  base_metric_filter          = "service_namespace=\"nutsnews\", deployment_environment=~\"$environment\", instance=~\"$instance\""
  base_log_filter             = "service_namespace=\"nutsnews\", deployment_environment=~\"$environment\", instance=~\"$instance\""
  node_exporter_metric_filter = "job=~\"integrations/node_exporter\", instance=~\"$instance\""

  dashboard_specs = {
    vps_overview = {
      uid         = "nutsnews-vps-overview"
      title       = "NutsNews VPS Overview"
      description = "High-level host, service, backup, app, and log health for the NutsNews VPS."
      panels = [
        { title = "Host scrape availability", type = "stat", datasource = "prometheus", unit = "percentunit", width = 6, height = 8, expr = "avg(up{${local.node_exporter_metric_filter}})" },
        { title = "Ops Portal status age", type = "stat", datasource = "prometheus", unit = "s", width = 6, height = 8, expr = "max(nutsnews_ops_portal_status_generated_age_seconds{${local.base_metric_filter}})" },
        { title = "Active alerts by level", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "sum by (level) (nutsnews_alerts_total{${local.base_metric_filter}})" },
        { title = "Recent warning and error logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter}} |~ \"(?i)(warn|warning|error|critical|failed)\"" },
      ]
    }

    logs_overview = {
      uid         = "nutsnews-logs-overview"
      title       = "NutsNews Logs Overview"
      description = "Centralized Loki log volume, levels, systemd units, Docker containers, Caddy status classes, and recent errors."
      panels = [
        { title = "Log volume by source", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (source) (count_over_time({${local.base_log_filter}}[$__interval]))" },
        { title = "Log volume by service", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (service) (count_over_time({${local.base_log_filter}}[$__interval]))" },
        { title = "Log volume by level", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (level) (count_over_time({${local.base_log_filter},level!=\"\"}[$__interval]))" },
        { title = "Systemd journal by unit", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (unit) (count_over_time({${local.base_log_filter},source=\"journal\",unit!=\"\"}[$__interval]))" },
        { title = "Docker logs by container", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (compose_project, container) (count_over_time({${local.base_log_filter},source=\"docker\"}[$__interval]))" },
        {
          title      = "Caddy status classes"
          type       = "timeseries"
          datasource = "loki"
          unit       = "short"
          width      = 12
          height     = 8
          targets = [
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",container=\"nutsnews-caddy\"} | json | status >= 200 | status < 300 [$__interval]))", legend = "2xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",container=\"nutsnews-caddy\"} | json | status >= 300 | status < 400 [$__interval]))", legend = "3xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",container=\"nutsnews-caddy\"} | json | status >= 400 | status < 500 [$__interval]))", legend = "4xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",container=\"nutsnews-caddy\"} | json | status >= 500 | status < 600 [$__interval]))", legend = "5xx" },
          ]
        },
        { title = "Recent errors", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter}} |~ \"(?i)(error|critical|panic|failed|denied)\"" },
        { title = "Dropped log guardrails", type = "timeseries", datasource = "prometheus", unit = "ops", width = 12, height = 8, expr = "sum by (reason) (rate(loki_process_dropped_lines_total{${local.base_metric_filter}}[$__rate_interval]))" },
      ]
    }

    cpu_load_processes = {
      uid         = "nutsnews-cpu-load-processes"
      title       = "NutsNews CPU Load Processes"
      description = "CPU saturation, load average, process count, file descriptors, conntrack, and clock health."
      panels = [
        { title = "CPU busy", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "1 - avg by (instance) (rate(node_cpu_seconds_total{${local.node_exporter_metric_filter},mode=\"idle\"}[$__rate_interval]))" },
        {
          title      = "Load averages"
          type       = "timeseries"
          datasource = "prometheus"
          unit       = "short"
          width      = 12
          height     = 8
          targets = [
            { expr = "node_load1{${local.node_exporter_metric_filter}}", legend = "1m" },
            { expr = "node_load5{${local.node_exporter_metric_filter}}", legend = "5m" },
            { expr = "node_load15{${local.node_exporter_metric_filter}}", legend = "15m" },
          ]
        },
        { title = "Process counts", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "node_processes_state{${local.node_exporter_metric_filter}}" },
        { title = "Clock offset", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, expr = "node_timex_offset_seconds{${local.node_exporter_metric_filter}}" },
      ]
    }

    memory_swap = {
      uid         = "nutsnews-memory-swap"
      title       = "NutsNews Memory Swap"
      description = "Host memory and swap pressure from Alloy's Linux exporter."
      panels = [
        { title = "Memory used", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "1 - (node_memory_MemAvailable_bytes{${local.node_exporter_metric_filter}} / node_memory_MemTotal_bytes{${local.node_exporter_metric_filter}})" },
        { title = "Memory available", type = "timeseries", datasource = "prometheus", unit = "bytes", width = 12, height = 8, expr = "node_memory_MemAvailable_bytes{${local.node_exporter_metric_filter}}" },
        { title = "Swap used", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "1 - ((node_memory_SwapFree_bytes{${local.node_exporter_metric_filter}} + node_memory_SwapCached_bytes{${local.node_exporter_metric_filter}}) / node_memory_SwapTotal_bytes{${local.node_exporter_metric_filter}})" },
        { title = "Ops snapshot memory", type = "timeseries", datasource = "prometheus", unit = "percent", width = 12, height = 8, expr = "nutsnews_resource_memory_used_percent{${local.base_metric_filter}}" },
      ]
    }

    disk_filesystem_io = {
      uid         = "nutsnews-disk-filesystem-io"
      title       = "NutsNews Disk Filesystem IO"
      description = "Filesystem capacity, inode pressure, and block IO."
      panels = [
        { title = "Filesystem used", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "1 - (node_filesystem_avail_bytes{${local.node_exporter_metric_filter},fstype!=\"\"} / node_filesystem_size_bytes{${local.node_exporter_metric_filter},fstype!=\"\"})" },
        { title = "Inodes used", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "1 - (node_filesystem_files_free{${local.node_exporter_metric_filter},fstype!=\"\"} / node_filesystem_files{${local.node_exporter_metric_filter},fstype!=\"\"})" },
        { title = "Disk read/write throughput", type = "timeseries", datasource = "prometheus", unit = "Bps", width = 12, height = 8, expr = "sum by (instance, device) (rate(node_disk_read_bytes_total{${local.node_exporter_metric_filter}}[5m]) + rate(node_disk_written_bytes_total{${local.node_exporter_metric_filter}}[5m]))" },
        { title = "Disk IO time", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "rate(node_disk_io_time_seconds_total{${local.node_exporter_metric_filter}}[5m])" },
      ]
    }

    network_caddy_edge = {
      uid         = "nutsnews-network-caddy-edge"
      title       = "NutsNews Network Caddy Edge"
      description = "Network IO/errors and edge-service logs."
      panels = [
        { title = "Network receive", type = "timeseries", datasource = "prometheus", unit = "Bps", width = 12, height = 8, expr = "sum by (instance, device) (rate(node_network_receive_bytes_total{${local.node_exporter_metric_filter}}[5m]))" },
        { title = "Network transmit", type = "timeseries", datasource = "prometheus", unit = "Bps", width = 12, height = 8, expr = "sum by (instance, device) (rate(node_network_transmit_bytes_total{${local.node_exporter_metric_filter}}[5m]))" },
        { title = "Network errors", type = "timeseries", datasource = "prometheus", unit = "ops", width = 12, height = 8, expr = "sum by (instance, device) (rate(node_network_receive_errs_total{${local.node_exporter_metric_filter}}[5m]) + rate(node_network_transmit_errs_total{${local.node_exporter_metric_filter}}[5m]))" },
        { title = "Caddy warnings and errors", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\",container=\"nutsnews-caddy\"} |~ \"(?i)(warn|error|failed|panic|tls|reverse_proxy)\"" },
      ]
    }

    docker_compose_containers = {
      uid         = "nutsnews-docker-compose-containers"
      title       = "NutsNews Docker Compose Containers"
      description = "Docker and Compose container health, restarts, CPU, memory, network, block IO, and logs."
      panels = [
        { title = "Container CPU", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "sum by (container, compose_project) (rate(container_cpu_usage_seconds_total{${local.base_metric_filter},container!=\"\"}[5m]))" },
        { title = "Container memory", type = "timeseries", datasource = "prometheus", unit = "bytes", width = 12, height = 8, expr = "sum by (container, compose_project) (container_memory_working_set_bytes{${local.base_metric_filter},container!=\"\"})" },
        { title = "Container restarts and health", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_docker_container_restart_count{${local.base_metric_filter}} or nutsnews_docker_container_healthy{${local.base_metric_filter}}" },
        { title = "Container logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\"}" },
      ]
    }

    systemd_services_timers = {
      uid         = "nutsnews-systemd-services-timers"
      title       = "NutsNews Systemd Services Timers"
      description = "Systemd service and timer state, restart counters, and service task pressure."
      panels = [
        { title = "Systemd active state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "node_systemd_unit_state{${local.node_exporter_metric_filter},state=\"active\"}" },
        { title = "NutsNews service active", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_systemd_service_active{${local.base_metric_filter}}" },
        { title = "Systemd restarts", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "node_systemd_service_restart_total{${local.node_exporter_metric_filter}}" },
        { title = "Systemd warnings and failures", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},log_source=\"journal\"} |~ \"(?i)(failed|failure|warning|timeout|dependency)\"" },
      ]
    }

    logs_security_auth = {
      uid         = "nutsnews-logs-security-auth"
      title       = "NutsNews Logs Security Auth"
      description = "Authentication and security logs with redacted secrets and IP addresses."
      panels = [
        { title = "Recent failed logins", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_security_failed_logins_recent{${local.base_metric_filter}} or nutsnews_security_failed_logins_invalid_user{${local.base_metric_filter}}" },
        { title = "Auth log stream", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"auth\"}" },
        { title = "High-priority journal", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"journal\",level=~\"emerg|alert|crit|err|warning\"}" },
        { title = "Dropped log guardrail counters", type = "timeseries", datasource = "prometheus", unit = "ops", width = 12, height = 8, expr = "sum by (reason) (rate(loki_process_dropped_lines_total{${local.base_metric_filter}}[5m]))" },
      ]
    }

    backups_restore_verification = {
      uid         = "nutsnews-backups-restore-verification"
      title       = "NutsNews Backups Restore Verification"
      description = "Restic backup freshness, prune/check status, missing paths, and backup logs."
      panels = [
        { title = "Backup latest snapshot age", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, expr = "nutsnews_backup_latest_snapshot_age_seconds{${local.base_metric_filter}}" },
        { title = "Backup success state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_backup_last_success{${local.base_metric_filter}} or nutsnews_backup_last_prune_success{${local.base_metric_filter}} or nutsnews_backup_last_verify_success{${local.base_metric_filter}}" },
        { title = "Backup config and missing paths", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_backup_configured{${local.base_metric_filter}} or nutsnews_backup_missing_configuration_total{${local.base_metric_filter}} or nutsnews_backup_missing_paths_total{${local.base_metric_filter}}" },
        { title = "Backup logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},log_source=\"nutsnews-service\"} |~ \"(?i)(backup|restic|rclone|snapshot|verify|prune)\"" },
      ]
    }

    ops_portal_reporting = {
      uid         = "nutsnews-ops-portal-reporting"
      title       = "NutsNews Ops Portal Reporting"
      description = "Ops Portal collector, status feed, email reporting, and alert delivery state."
      panels = [
        { title = "Ops Portal status readable", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, expr = "max(nutsnews_ops_portal_status_available{${local.base_metric_filter}})" },
        { title = "Status snapshot age", type = "stat", datasource = "prometheus", unit = "s", width = 6, height = 8, expr = "max(nutsnews_ops_portal_status_generated_age_seconds{${local.base_metric_filter}})" },
        { title = "Email reporting state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_email_reporting_enabled{${local.base_metric_filter}} or nutsnews_email_reporting_configured{${local.base_metric_filter}} or nutsnews_email_reporting_pending_alerts{${local.base_metric_filter}} or nutsnews_email_reporting_suppressed_alerts{${local.base_metric_filter}}" },
        { title = "Ops Portal logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},log_source=\"nutsnews-service\"} |~ \"(?i)(ops|portal|collector|report|alert)\"" },
      ]
    }

    application_service_health = {
      uid         = "nutsnews-application-service-health"
      title       = "NutsNews Application Service Health"
      description = "Deployment-owned app/service health from Compose, Caddy routing, and the Ops Portal status feed."
      panels = [
        { title = "App deployment state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "nutsnews_app_enabled{${local.base_metric_filter}} or nutsnews_app_container_running{${local.base_metric_filter}} or nutsnews_app_container_healthy{${local.base_metric_filter}} or nutsnews_app_route_ready{${local.base_metric_filter}}" },
        { title = "App container resource usage", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "sum by (container) (container_memory_working_set_bytes{${local.base_metric_filter},container=~\"nutsnews.*\"}) or sum by (container) (rate(container_cpu_usage_seconds_total{${local.base_metric_filter},container=~\"nutsnews.*\"}[5m]))" },
        { title = "Application route logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\",container=~\"nutsnews-app|nutsnews-caddy\"} |~ \"(?i)(app-stage|healthz|api)\"" },
        { title = "Service health endpoint failures", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},log_source=\"nutsnews-service\"} |~ \"(?i)(health|route|upstream)\" |~ \"(?i)(fail|error|timeout|unhealthy)\"" },
      ]
    }

    synthetic_uptime_api_checks = {
      uid         = "nutsnews-synthetic-uptime-api-checks"
      title       = "NutsNews Synthetic Uptime API Checks"
      description = "Low-frequency public endpoint checks managed by the Grafana provider when probe IDs and targets are supplied."
      panels = [
        { title = "Synthetic success", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "avg by (job, probe) (probe_success{service_namespace=\"nutsnews\", deployment_environment=~\"$environment\"})" },
        { title = "Synthetic duration", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, expr = "avg by (job, probe) (probe_duration_seconds{service_namespace=\"nutsnews\", deployment_environment=~\"$environment\"})" },
        { title = "HTTP status code", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "max by (job, probe) (probe_http_status_code{service_namespace=\"nutsnews\", deployment_environment=~\"$environment\"})" },
        { title = "Synthetic logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{service_namespace=\"nutsnews\", deployment_environment=~\"$environment\"} |~ \"(?i)(synthetic|probe|http)\"" },
      ]
    }

    grafana_cloud_usage_quota = {
      uid         = "nutsnews-grafana-cloud-usage-quota"
      title       = "NutsNews Grafana Cloud Usage Quota"
      description = "Current Grafana Cloud usage and live platform-limit guardrails."
      panels = [
        { title = "Metrics usage versus live active-series limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_instance_metrics_usage) / max(grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})" },
        { title = "Logs active streams versus live stream limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_logs_instance_active_streams) / max(grafanacloud_logs_instance_limits{limit_name=\"max_global_streams_per_user\"})" },
        { title = "Logs ingest rate versus live rate limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_logs_instance_bytes_received_per_second) / (max(grafanacloud_logs_instance_limits{limit_name=\"ingestion_rate_mb\"}) * 1024 * 1024)" },
        { title = "Traces ingest rate versus live rate limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_traces_instance_bytes_received_per_second) / max(grafanacloud_traces_instance_limits{limit_name=\"ingestion_rate_limit_bytes\"})" },
        { title = "Published Grafana Cloud limits", type = "timeseries", datasource = "usage", unit = "short", width = 12, height = 8, expr = "grafanacloud_instance_metrics_limits or grafanacloud_logs_instance_limits or grafanacloud_traces_instance_limits" },
      ]
    }
  }

  dashboard_panels = {
    for dashboard_key, spec in local.dashboard_specs : dashboard_key => [
      for index, panel in spec.panels : {
        datasource = {
          type = local.datasource_types[panel.datasource]
          uid  = local.datasource_uids[panel.datasource]
        }
        description = try(panel.description, "")
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
            noValue  = try(panel.noValue, "No data")
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
        links = try(panel.links, [])
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
        targets = lookup(panel, "targets", null) != null ? [
          for target_index, target in panel.targets : {
            datasource = {
              type = local.datasource_types[panel.datasource]
              uid  = local.datasource_uids[panel.datasource]
            }
            editorMode   = "code"
            expr         = target.expr
            instant      = false
            interval     = ""
            legendFormat = target.legend
            queryType    = panel.datasource == "loki" ? "range" : ""
            range        = true
            refId        = ["A", "B", "C", "D", "E", "F"][target_index]
            useBackend   = false
          }
          ] : panel.datasource == "loki" ? [
          {
            datasource = {
              type = local.datasource_types[panel.datasource]
              uid  = local.datasource_uids[panel.datasource]
            }
            editorMode   = "code"
            expr         = panel.expr
            instant      = false
            interval     = ""
            legendFormat = "__auto"
            queryType    = "range"
            range        = true
            refId        = "A"
            useBackend   = false
          }
          ] : [
          {
            datasource = {
              type = local.datasource_types[panel.datasource]
              uid  = local.datasource_uids[panel.datasource]
            }
            editorMode   = "code"
            expr         = panel.expr
            instant      = false
            interval     = ""
            legendFormat = "__auto"
            queryType    = ""
            range        = true
            refId        = "A"
            useBackend   = false
          }
        ]
      }
    ]
  }

  enabled_synthetic_http_checks = {
    for name, check in var.synthetic_http_checks : name => check
    if check.enabled && length(var.synthetic_monitoring_probe_ids) > 0
  }

  synthetic_monthly_api_executions = length(local.enabled_synthetic_http_checks) == 0 ? 0 : sum([
    for check in values(local.enabled_synthetic_http_checks) :
    length(var.synthetic_monitoring_probe_ids) * 1 * (43200 / (check.frequency_ms / 60000))
  ])

  synthetic_monthly_api_guardrail = var.free_synthetic_api_executions_monthly * 0.70

  quota_alert_thresholds = {
    "70" = 0.70
    "85" = 0.85
    "95" = 0.95
  }

  quota_alert_sources = {
    metrics_active_series = {
      title         = "Grafana Cloud metrics active-series usage"
      expr          = "max(grafanacloud_instance_metrics_usage) / max(grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})"
      no_data_state = "NoData"
      description   = "Grafana Cloud metrics active-series usage is above the live max_global_series_per_user limit guardrail."
    }
    logs_active_streams = {
      title         = "Grafana Cloud logs active streams"
      expr          = "max(grafanacloud_logs_instance_active_streams) / max(grafanacloud_logs_instance_limits{limit_name=\"max_global_streams_per_user\"})"
      no_data_state = "NoData"
      description   = "Grafana Cloud Logs active streams are above the live max_global_streams_per_user guardrail."
    }
    logs_ingestion_rate = {
      title         = "Grafana Cloud logs ingestion rate"
      expr          = "max(grafanacloud_logs_instance_bytes_received_per_second) / (max(grafanacloud_logs_instance_limits{limit_name=\"ingestion_rate_mb\"}) * 1024 * 1024)"
      no_data_state = "NoData"
      description   = "Grafana Cloud Logs ingestion rate is above the live ingestion_rate_mb guardrail."
    }
    traces_ingestion_rate = {
      title         = "Grafana Cloud traces ingestion rate"
      expr          = "max(grafanacloud_traces_instance_bytes_received_per_second) / max(grafanacloud_traces_instance_limits{limit_name=\"ingestion_rate_limit_bytes\"})"
      no_data_state = "OK"
      description   = "Grafana Cloud Traces ingestion appeared even though worker-uplift trace export is deferred; keep traces disabled unless separately approved."
    }
  }

  quota_alert_rules = flatten([
    for source_key, source in local.quota_alert_sources : [
      for threshold_name, threshold in local.quota_alert_thresholds : {
        key           = "${source_key}_${threshold_name}"
        title         = "${source.title} above ${threshold_name}%"
        expr          = source.expr
        threshold     = threshold
        severity      = threshold >= 0.95 ? "critical" : threshold >= 0.85 ? "warning" : "info"
        for_period    = threshold >= 0.95 ? "5m" : "15m"
        no_data_state = source.no_data_state
        description   = source.description
      }
    ]
  ])

  log_pipeline_alert_rules = {
    alloy_loki_dropped_entries = {
      title         = "Grafana Alloy Loki dropped log entries"
      datasource    = "prometheus"
      expr          = "sum(rate(loki_write_dropped_entries_total{service_namespace=\"nutsnews\", deployment_environment=\"${var.deployment_environment}\"}[5m]))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "Alloy reports dropped Loki entries after exhausting retries, which means log shipping is losing data."
    }
    alloy_loki_batch_retries = {
      title         = "Grafana Alloy Loki write retries"
      datasource    = "prometheus"
      expr          = "sum(rate(loki_write_batch_retries_total{service_namespace=\"nutsnews\", deployment_environment=\"${var.deployment_environment}\"}[5m]))"
      threshold     = 0
      for_period    = "10m"
      severity      = "warning"
      no_data_state = "OK"
      description   = "Alloy is retrying Loki writes. Check Grafana Cloud Logs credentials, endpoint reachability, and quota state."
    }
    high_error_log_volume = {
      title         = "NutsNews high error log volume"
      datasource    = "loki"
      expr          = "sum(count_over_time({service_namespace=\"nutsnews\", deployment_environment=\"${var.deployment_environment}\"} |~ \"(?i)(error|critical|panic|failed|denied)\" [5m]))"
      threshold     = 20
      for_period    = "10m"
      severity      = "warning"
      no_data_state = "OK"
      description   = "Recent log volume contains repeated error, critical, panic, failed, or denied entries."
    }
  }
}
