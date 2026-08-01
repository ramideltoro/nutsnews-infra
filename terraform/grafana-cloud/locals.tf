locals {
  dashboard_tags                    = ["nutsnews", "vps", "grafana-cloud", "gitops"]
  grafana_observability_runbook_url = "https://github.com/ramideltoro/nutsnews-infra/blob/main/runbooks/GRAFANA_CLOUD_OBSERVABILITY.md"

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
  base_log_filter             = "deployment_environment=~\"$environment\""
  node_exporter_metric_filter = "job=~\"integrations/node_exporter\", instance=~\"$instance\""

  dashboard_no_value = {
    vps_overview                 = "Unavailable — required VPS overview telemetry is missing or stale"
    logs_overview                = "Unavailable — required normalized Loki stream is missing for the selected range"
    cpu_load_processes           = "Unavailable — node_exporter CPU/process telemetry is missing for the selected instance"
    memory_swap                  = "Unavailable — node_exporter memory telemetry is missing for the selected instance"
    disk_filesystem_io           = "Unavailable — node_exporter disk/filesystem telemetry is missing for the selected instance"
    network_caddy_edge           = "Unavailable — required network or Caddy telemetry is missing"
    docker_compose_containers    = "Unavailable — bounded Docker stats export is missing or stale"
    systemd_services_timers      = "Unavailable — systemd status export is missing or stale"
    logs_security_auth           = "No matching security or authentication events in the selected time range"
    backups_restore_verification = "Unavailable — backup verification status export is missing or stale"
    ops_portal_reporting         = "Unavailable — Ops Portal reporting status export is missing or stale"
    application_service_health   = "Unavailable — application health status export is missing or stale"
    synthetic_uptime_api_checks  = "Unavailable — required Synthetic Monitoring samples are missing"
    grafana_cloud_usage_quota    = "Unavailable — Grafana Cloud usage or limit telemetry is missing"
    production_ownership         = "Unavailable — production ownership telemetry is missing or stale"
  }

  dashboard_specs = {
    vps_overview = {
      uid         = "nutsnews-vps-overview"
      title       = "NutsNews VPS Overview"
      description = "High-level host, service, backup, app, and log health for the NutsNews VPS."
      panels = [
        { title = "Host scrape availability", type = "stat", datasource = "prometheus", unit = "percentunit", width = 6, height = 8, description = "Authoritative scrape state from the Linux node_exporter integration.", noValue = "Unavailable — host exporter scrape telemetry is missing", expr = "avg(up{${local.node_exporter_metric_filter}})" },
        { title = "Ops Portal status age", type = "stat", datasource = "prometheus", unit = "s", width = 6, height = 8, description = "Freshness of the durable Ops Portal status textfile export.", noValue = "Unavailable — Ops Portal status telemetry is missing or stale", expr = "max(nutsnews_ops_portal_status_generated_age_seconds{${local.base_metric_filter}})" },
        { title = "Active alerts by level", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Alert totals emitted by the durable Ops Portal status export.", noValue = "Unavailable — alert summary telemetry is missing or stale", expr = "sum by (level) (nutsnews_alerts_total{${local.base_metric_filter}}) and on() (max(nutsnews_alert_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Recent warning and error logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, description = "Recent normalized Loki streams from every production source.", noValue = "Unavailable — normalized production log streams are missing", expr = "{${local.base_log_filter}} |~ \"(?i)(warn|warning|error|critical|failed)\"" },
      ]
    }

    logs_overview = {
      uid         = "nutsnews-logs-overview"
      title       = "NutsNews Logs Overview"
      description = "Centralized Loki log volume, levels, systemd units, Docker containers, Caddy status classes, and recent errors."
      panels = [
        { title = "Log volume by source", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (source) (count_over_time({${local.base_log_filter}}[$__interval])) or vector(0)" },
        { title = "Log volume by service", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (service) (count_over_time({${local.base_log_filter}}[$__interval])) or vector(0)" },
        { title = "Log volume by severity", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (severity) (count_over_time({${local.base_log_filter},severity!=\"\"}[$__interval])) or vector(0)" },
        { title = "Systemd journal by service", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (service) (count_over_time({${local.base_log_filter},source=\"journal\"}[$__interval])) or vector(0)" },
        { title = "Docker logs by service", type = "timeseries", datasource = "loki", unit = "short", width = 12, height = 8, expr = "sum by (service) (count_over_time({${local.base_log_filter},source=\"docker\"}[$__interval])) or vector(0)" },
        {
          title      = "Caddy status classes"
          type       = "timeseries"
          datasource = "loki"
          unit       = "short"
          width      = 12
          height     = 8
          targets = [
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",service=\"caddy\"} | json | status >= 200 | status < 300 [$__interval])) or vector(0)", legend = "2xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",service=\"caddy\"} | json | status >= 300 | status < 400 [$__interval])) or vector(0)", legend = "3xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",service=\"caddy\"} | json | status >= 400 | status < 500 [$__interval])) or vector(0)", legend = "4xx" },
            { expr = "sum(count_over_time({${local.base_log_filter},source=\"docker\",service=\"caddy\"} | json | status >= 500 | status < 600 [$__interval])) or vector(0)", legend = "5xx" },
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
        { title = "Ops snapshot memory", type = "timeseries", datasource = "prometheus", unit = "percent", width = 12, height = 8, noValue = "Unavailable — Ops resource status source is missing", expr = "nutsnews_resource_memory_used_percent{${local.base_metric_filter}} and on() (max(nutsnews_resource_status_available{${local.base_metric_filter}}) == 1)" },
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
        { title = "Caddy request rate", type = "timeseries", datasource = "prometheus", unit = "reqps", width = 12, height = 8, noValue = "Unavailable — terminal Caddy reverse-proxy metrics are missing", expr = "sum(rate(caddy_http_requests_total{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval]))" },
        {
          title      = "Caddy error and throttle ratios"
          type       = "timeseries"
          datasource = "prometheus"
          unit       = "percentunit"
          width      = 12
          height     = 8
          noValue    = "No Caddy requests in selected time range — error ratios undefined"
          targets = [
            { expr = "(sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\",code=~\"4..\"}[$__rate_interval])) / sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval]))) and on() (sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval])) > 0)", legend = "4xx" },
            { expr = "(sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\",code=\"429\"}[$__rate_interval])) / sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval]))) and on() (sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval])) > 0)", legend = "429" },
            { expr = "(sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\",code=~\"5..\"}[$__rate_interval])) / sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval]))) and on() (sum(rate(caddy_http_request_duration_seconds_count{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval])) > 0)", legend = "5xx" },
          ]
        },
        {
          title      = "Caddy request latency"
          type       = "timeseries"
          datasource = "prometheus"
          unit       = "s"
          width      = 12
          height     = 8
          noValue    = "Unavailable — Caddy duration histogram is missing"
          targets = [
            { expr = "histogram_quantile(0.95, sum by (le) (rate(caddy_http_request_duration_seconds_bucket{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval])))", legend = "p95" },
            { expr = "histogram_quantile(0.99, sum by (le) (rate(caddy_http_request_duration_seconds_bucket{deployment_environment=~\"$environment\",service=\"caddy\",handler=\"reverse_proxy\"}[$__rate_interval])))", legend = "p99" },
          ]
        },
        { title = "Caddy upstream health", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — Caddy upstream health telemetry is missing", expr = "min by (upstream) (caddy_reverse_proxy_upstreams_healthy{deployment_environment=~\"$environment\",service=\"caddy\"})" },
        { title = "TLS certificate expiry", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, noValue = "Unavailable — TLS certificate probe telemetry is missing", expr = "min(nutsnews_caddy_tls_certificate_expiry_seconds{${local.base_metric_filter},service=\"caddy\"})" },
        { title = "Caddy warnings and errors", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\",service=\"caddy\"} |~ \"(?i)(warn|error|failed|panic|tls|reverse_proxy)\"" },
      ]
    }

    docker_compose_containers = {
      uid         = "nutsnews-docker-compose-containers"
      title       = "NutsNews Docker Compose Containers"
      description = "Bounded Docker stats textfile metrics for expected services, plus container state, restarts, network, block IO, and logs."
      panels = [
        { title = "Docker stats availability", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_stats_available{${local.base_metric_filter}}" },
        { title = "Container CPU", type = "timeseries", datasource = "prometheus", unit = "percent", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_container_cpu_percent{${local.base_metric_filter}}" },
        { title = "Container memory", type = "timeseries", datasource = "prometheus", unit = "bytes", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_container_memory_used_bytes{${local.base_metric_filter}}" },
        { title = "Container network IO", type = "timeseries", datasource = "prometheus", unit = "bytes", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_container_network_receive_bytes{${local.base_metric_filter}} or nutsnews_docker_container_network_transmit_bytes{${local.base_metric_filter}}" },
        { title = "Container block IO", type = "timeseries", datasource = "prometheus", unit = "bytes", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_container_block_read_bytes{${local.base_metric_filter}} or nutsnews_docker_container_block_write_bytes{${local.base_metric_filter}}" },
        { title = "Container restarts and health", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — container state collection is stale", expr = "nutsnews_docker_container_restart_count{${local.base_metric_filter}} or nutsnews_docker_container_healthy{${local.base_metric_filter}} or nutsnews_docker_container_running{${local.base_metric_filter}}" },
        { title = "Container logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\"}" },
      ]
    }

    systemd_services_timers = {
      uid         = "nutsnews-systemd-services-timers"
      title       = "NutsNews Systemd Services Timers"
      description = "Systemd service and timer state, restart counters, and service task pressure."
      panels = [
        { title = "Systemd active state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "node_systemd_unit_state{${local.node_exporter_metric_filter},state=\"active\"}" },
        { title = "NutsNews service active", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — Ops systemd service source is missing", expr = "nutsnews_systemd_service_active{${local.base_metric_filter}} and on() (max(nutsnews_systemd_service_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Systemd restarts", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "node_systemd_service_restart_total{${local.node_exporter_metric_filter}}" },
        { title = "Systemd warnings and failures", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"journal\"} |~ \"(?i)(failed|failure|warning|timeout|dependency)\"" },
      ]
    }

    logs_security_auth = {
      uid         = "nutsnews-logs-security-auth"
      title       = "NutsNews Logs Security Auth"
      description = "Authentication and security logs with redacted secrets and IP addresses."
      panels = [
        { title = "Recent failed logins", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — Ops security status source is missing", expr = "(nutsnews_security_failed_logins_recent{${local.base_metric_filter}} or nutsnews_security_failed_logins_invalid_user{${local.base_metric_filter}}) and on() (max(nutsnews_security_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Auth log stream", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"auth\"}" },
        { title = "High-priority journal", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"journal\",severity=~\"critical|error|warning\"}" },
        { title = "Dropped log guardrail counters", type = "timeseries", datasource = "prometheus", unit = "ops", width = 12, height = 8, expr = "sum by (reason) (rate(loki_process_dropped_lines_total{${local.base_metric_filter}}[5m]))" },
      ]
    }

    backups_restore_verification = {
      uid         = "nutsnews-backups-restore-verification"
      title       = "NutsNews Backups Restore Verification"
      description = "Restic backup freshness, prune/check status, missing paths, and backup logs."
      panels = [
        { title = "Backup status source", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, noValue = "Unavailable — backup status telemetry is missing", expr = "max(nutsnews_backup_status_available{${local.base_metric_filter}})" },
        { title = "Backup latest snapshot age", type = "timeseries", datasource = "prometheus", unit = "s", width = 18, height = 8, noValue = "Unavailable — backup status export is missing or unreadable", expr = "nutsnews_backup_latest_snapshot_age_seconds{${local.base_metric_filter}} and on() (max(nutsnews_backup_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Last successful verification age", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, noValue = "Unavailable — no completed backup verification timestamp is available", expr = "nutsnews_backup_last_verify_finished_age_seconds{${local.base_metric_filter}} and on() (max(nutsnews_backup_last_verify_finished_age_seconds{${local.base_metric_filter}}) >= 0) and on() (max(nutsnews_backup_last_verify_success{${local.base_metric_filter}}) == 1) and on() (max(nutsnews_backup_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Backup success state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — backup status export is missing or unreadable", expr = "(nutsnews_backup_last_success{${local.base_metric_filter}} or nutsnews_backup_last_prune_success{${local.base_metric_filter}} or nutsnews_backup_last_verify_success{${local.base_metric_filter}}) and on() (max(nutsnews_backup_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Backup config and missing paths", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — backup status export is missing or unreadable", expr = "(nutsnews_backup_configured{${local.base_metric_filter}} or nutsnews_backup_missing_configuration_total{${local.base_metric_filter}} or nutsnews_backup_missing_paths_total{${local.base_metric_filter}}) and on() (max(nutsnews_backup_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Backup logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"file\"} |~ \"(?i)(backup|restic|rclone|snapshot|verify|prune)\"" },
      ]
    }

    ops_portal_reporting = {
      uid         = "nutsnews-ops-portal-reporting"
      title       = "NutsNews Ops Portal Reporting"
      description = "Ops Portal collector, status feed, email reporting, and alert delivery state."
      panels = [
        { title = "Ops Portal status readable", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, expr = "max(nutsnews_ops_portal_status_available{${local.base_metric_filter}})" },
        { title = "Status snapshot age", type = "stat", datasource = "prometheus", unit = "s", width = 6, height = 8, expr = "max(nutsnews_ops_portal_status_generated_age_seconds{${local.base_metric_filter}})" },
        { title = "Email reporting state", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — email reporting status source is missing", expr = "(nutsnews_email_reporting_enabled{${local.base_metric_filter}} or nutsnews_email_reporting_configured{${local.base_metric_filter}} or nutsnews_email_reporting_pending_alerts{${local.base_metric_filter}} or nutsnews_email_reporting_suppressed_alerts{${local.base_metric_filter}}) and on() (max(nutsnews_email_reporting_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Latest health-audit conclusion", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — scheduled health-audit conclusion telemetry is missing", expr = "max by (outcome) (nutsnews_email_reporting_last_report_conclusion{${local.base_metric_filter}}) and on() (max(nutsnews_email_reporting_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Last critical-free health-audit success age", type = "stat", datasource = "prometheus", unit = "s", width = 12, height = 8, noValue = "Unavailable — scheduled health-audit success telemetry is missing", expr = "max(nutsnews_email_reporting_last_report_success_age_seconds{${local.base_metric_filter}}) and on() (max(nutsnews_email_reporting_last_report_success_age_seconds{${local.base_metric_filter}}) >= 0) and on() (max(nutsnews_email_reporting_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Last report-delivery success age", type = "stat", datasource = "prometheus", unit = "s", width = 12, height = 8, noValue = "Unavailable — report-delivery success telemetry is missing", expr = "max(nutsnews_email_reporting_last_report_delivery_success_age_seconds{${local.base_metric_filter}}) and on() (max(nutsnews_email_reporting_last_report_delivery_success_age_seconds{${local.base_metric_filter}}) >= 0) and on() (max(nutsnews_email_reporting_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Latest health-audit exit code", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — scheduled health-audit exit-code telemetry is missing", expr = "max(nutsnews_email_reporting_last_report_exit_code{${local.base_metric_filter}}) and on() (max(nutsnews_email_reporting_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "Ops Portal logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"file\"} |~ \"(?i)(ops|portal|collector|report|alert)\"" },
      ]
    }

    application_service_health = {
      uid         = "nutsnews-application-service-health"
      title       = "NutsNews Application Service Health"
      description = "Deployment-owned app/service health from Compose, Caddy routing, and the Ops Portal status feed."
      panels = [
        { title = "Application status source", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, noValue = "Unavailable — application status telemetry is missing", expr = "max(nutsnews_app_status_available{${local.base_metric_filter}})" },
        { title = "App deployment and route state", type = "timeseries", datasource = "prometheus", unit = "short", width = 18, height = 8, noValue = "Unavailable — application status export is missing or unreadable", expr = "(nutsnews_app_enabled{${local.base_metric_filter}} or nutsnews_app_container_running{${local.base_metric_filter}} or nutsnews_app_container_healthy{${local.base_metric_filter}} or nutsnews_app_public_route_enabled{${local.base_metric_filter}} or nutsnews_app_public_route_healthy{${local.base_metric_filter}} or nutsnews_app_staged_route_enabled{${local.base_metric_filter}} or nutsnews_app_staged_route_healthy{${local.base_metric_filter}}) and on() (max(nutsnews_app_status_available{${local.base_metric_filter}}) == 1)" },
        { title = "App container resource usage", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, noValue = "Unavailable — bounded Docker stats export is missing or stale", expr = "nutsnews_docker_container_memory_used_bytes{${local.base_metric_filter},service=~\"web|caddy\"} or nutsnews_docker_container_cpu_percent{${local.base_metric_filter},service=~\"web|caddy\"}" },
        { title = "Application route logs", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter},source=\"docker\",service=~\"web|caddy\"} |~ \"(?i)(app-stage|healthz|readyz|api)\"" },
        { title = "Service health endpoint failures", type = "logs", datasource = "loki", unit = "short", width = 12, height = 8, expr = "{${local.base_log_filter}} |~ \"(?i)(health|ready|route|upstream)\" |~ \"(?i)(fail|error|timeout|unhealthy)\"" },
      ]
    }

    synthetic_uptime_api_checks = {
      uid         = "nutsnews-synthetic-uptime-api-checks"
      title       = "NutsNews Synthetic Uptime API Checks"
      description = "Five required public endpoint checks managed by the Grafana provider across two probes every five minutes."
      panels = [
        { title = "Synthetic success", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, expr = "avg by (job, probe) (probe_success * on(job, instance, probe, config_version) group_left() sm_check_info{label_service_namespace=\"nutsnews\",label_deployment_environment=~\"$environment\"})" },
        { title = "Synthetic duration", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, expr = "avg by (job, probe) (probe_duration_seconds * on(job, instance, probe, config_version) group_left() sm_check_info{label_service_namespace=\"nutsnews\",label_deployment_environment=~\"$environment\"})" },
        { title = "HTTP status code", type = "timeseries", datasource = "prometheus", unit = "short", width = 12, height = 8, expr = "max by (job, probe) (probe_http_status_code * on(job, instance, probe, config_version) group_left() sm_check_info{label_service_namespace=\"nutsnews\",label_deployment_environment=~\"$environment\"})" },
        { title = "Synthetic HTTP phase duration", type = "timeseries", datasource = "prometheus", unit = "s", width = 12, height = 8, noValue = "Unavailable — required Synthetic Monitoring samples are missing", expr = "avg by (job, probe, phase) (probe_http_duration_seconds * on(job, instance, probe, config_version) group_left() sm_check_info{label_service_namespace=\"nutsnews\",label_deployment_environment=~\"$environment\"})" },
        { title = "Scheduled Inventory Audit Conclusion", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 6, noValue = "Unavailable — scheduled audit status export is missing", expr = "max by (outcome) (nutsnews_synthetic_inventory_audit_conclusion{deployment_environment=~\"$environment\"})" },
        { title = "Scheduled Inventory Audit Last Success Age", type = "stat", datasource = "prometheus", unit = "s", width = 12, height = 6, noValue = "Unavailable — no scheduled audit success is known", expr = "max(nutsnews_synthetic_inventory_audit_last_success_age_seconds{deployment_environment=~\"$environment\"})" },
      ]
    }

    grafana_cloud_usage_quota = {
      uid         = "nutsnews-grafana-cloud-usage-quota"
      title       = "NutsNews Grafana Cloud Usage Quota"
      description = "Current Grafana Cloud usage and live platform-limit guardrails."
      panels = [
        { title = "Metrics usage versus live active-series limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_instance_active_series / on(id) grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})" },
        { title = "Projected Synthetic Monitoring API executions", type = "timeseries", datasource = "prometheus", unit = "percentunit", width = 12, height = 8, noValue = "Unavailable — managed synthetic configuration is missing", expr = "vector(${local.synthetic_monthly_api_executions / var.free_synthetic_api_executions_monthly})" },
        { title = "Logs active streams versus live stream limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_logs_instance_active_streams) / max(grafanacloud_logs_instance_limits{limit_name=\"max_global_streams_per_user\"})" },
        { title = "Logs ingest rate versus live rate limit", type = "timeseries", datasource = "usage", unit = "percentunit", width = 12, height = 8, expr = "max(grafanacloud_logs_instance_bytes_received_per_second) / (max(grafanacloud_logs_instance_limits{limit_name=\"ingestion_rate_mb\"}) * 1024 * 1024)" },
        { title = "Traces ingest (Tempo deferred)", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Tempo, exemplars, and profiling are disabled by the approved telemetry policy; trace usage limits remain guarded independently by alerts.", noValue = "Disabled by configuration — Tempo is deferred", mappings = [{ type = "value", options = { "-1" = { text = "Disabled by configuration — Tempo deferred" } } }], expr = "vector(-1)" },
        { title = "Published Grafana Cloud limits", type = "timeseries", datasource = "usage", unit = "short", width = 12, height = 8, expr = "grafanacloud_instance_metrics_limits or grafanacloud_logs_instance_limits or grafanacloud_traces_instance_limits" },
      ]
    }

    production_ownership = {
      uid         = "nutsnews-production-ownership"
      title       = "NutsNews Current Production Ownership"
      description = "Current routed web/database identity observed from canonical readiness plus the backend protected deployment ownership signal."
      panels = [
        { title = "Web target", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, description = "Routed production ownership observed from the validated canonical /readyz response.", noValue = "Unavailable — canonical production ownership observation is missing or stale", expr = "max by (web_target) (nutsnews_production_ownership_info{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) and on() (max(nutsnews_production_ownership_available{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) == 1) and on() ((time() - max(nutsnews_production_ownership_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"})) < 300)" },
        { title = "Database provider", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, description = "Routed database ownership observed from the validated canonical /readyz response.", noValue = "Unavailable — canonical production ownership observation is missing or stale", expr = "max by (database_provider) (nutsnews_production_ownership_info{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) and on() (max(nutsnews_production_ownership_available{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) == 1) and on() ((time() - max(nutsnews_production_ownership_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"})) < 300)" },
        { title = "Ingestion owner", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, description = "Configured production ingestion ownership derived fail closed from the backend protected deployment mode and expected-active pair.", noValue = "Unavailable — backend protected ownership signal is missing, stale, or invalid", expr = "max by (ingestion_owner) (nutsnews_backend_worker_uplift_deployment_info{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) and on() (max(nutsnews_backend_worker_uplift_ownership_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) == 1) and on() ((time() - max(nutsnews_backend_metric_scrape_timestamp_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"})) < 600) and on() (max(nutsnews_backend_metric_exporter_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) == 1)" },
        { title = "Worker-uplift mode and write gate", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, description = "Configured worker-uplift ownership mode and write gate derived fail closed from the backend protected deployment signal.", noValue = "Unavailable — backend protected ownership signal is missing, stale, or invalid", expr = "max by (mode, write_gate) (nutsnews_backend_worker_uplift_deployment_info{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) and on() (max(nutsnews_backend_worker_uplift_ownership_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) == 1) and on() ((time() - max(nutsnews_backend_metric_scrape_timestamp_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"})) < 600) and on() (max(nutsnews_backend_metric_exporter_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) == 1)" },
        { title = "Web and infrastructure revisions", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Web revision from canonical readiness and infrastructure revision from the host deployment receipt, joined by the production ownership collector.", noValue = "Unavailable — validated web or infrastructure ownership revision is missing", expr = "max by (web_revision, infra_revision) (nutsnews_production_ownership_info{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) and on() (max(nutsnews_production_ownership_available{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) == 1) and on() ((time() - max(nutsnews_production_ownership_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"})) < 300)" },
        { title = "Backend API revision", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Backend compatibility API version and immutable revision from its deployment ownership metric.", noValue = "Unavailable — backend deployment identity telemetry is missing", expr = "max by (service, service_version, revision) (nutsnews_backend_api_build_info{job=\"nutsnews-backend-api\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\"})" },
        { title = "Deployed worker release and image identities", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Host-verified ownership evidence with version, revision, and immutable running-image digest for each of the eight deployed worker services.", noValue = "Unavailable — running worker images do not match the complete verified deployment manifest", expr = "max by (worker_service, service_version, revision, image_digest) (nutsnews_backend_worker_uplift_deployed_service_info{job=\"nutsnews-backend-host\",service=\"host\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\",host=\"backend.nutsnews.com\"}) and on() (max(nutsnews_backend_worker_uplift_deployed_identity_available{job=\"nutsnews-backend-host\",service=\"host\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\",host=\"backend.nutsnews.com\"}) == 1)" },
        { title = "Worker runtime deployment identities", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Runtime-emitted deployment and production-adapter ownership identity for all eight worker services after telemetry-enabled images are published and repinned.", noValue = "Awaiting republished worker images — runtime deployment identity telemetry is not deployed", expr = "max by (service, deployment, adapter) (nutsnews_worker_deployment_info{job=\"nutsnews-worker-uplift\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\"})" },
        { title = "Worker readiness by ownership mode", type = "stat", datasource = "prometheus", unit = "short", width = 12, height = 8, description = "Production-owned workers show their real readiness outcome; shadow workers use an explicit disabled state instead of being treated as unready.", noValue = "Awaiting republished worker images — readiness or ownership telemetry is not deployed", mappings = [{ type = "value", options = { "-1" = { text = "Disabled by configuration — shadow" }, "0" = { text = "Unready" }, "1" = { text = "Ready" } } }], expr = "(max by (service) (nutsnews_worker_health_probe{job=\"nutsnews-worker-uplift\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\",probe=\"readiness\",outcome=\"ok\"}) and on(service) (max by (service) (nutsnews_worker_expected_active{job=\"nutsnews-worker-uplift\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\"}) == 1)) or (((0 * max by (service) (nutsnews_worker_expected_active{job=\"nutsnews-worker-uplift\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\"})) - 1) and on(service) (max by (service) (nutsnews_worker_expected_active{job=\"nutsnews-worker-uplift\",deployment_environment=\"${var.deployment_environment}\",instance=\"backend.nutsnews.com\"}) == 0))" },
        { title = "Ownership telemetry available", type = "stat", datasource = "prometheus", unit = "short", width = 6, height = 8, description = "Combined ownership-source validity and freshness: canonical routed readiness and the backend protected deployment signal must both be current and valid.", noValue = "Unavailable — ownership source availability telemetry is missing", expr = "(max(nutsnews_production_ownership_available{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)) * (max(nutsnews_backend_worker_uplift_ownership_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)) * (max(nutsnews_backend_metric_exporter_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)) * (((time() - max(nutsnews_production_ownership_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"})) < bool 300) or vector(0)) * (((time() - max(nutsnews_backend_metric_scrape_timestamp_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\"})) < bool 600) or vector(0))" },
        { title = "Ownership telemetry freshness", type = "stat", datasource = "prometheus", unit = "s", width = 6, height = 8, description = "Age of the latest successful canonical readiness ownership observation; failed validation is unavailable, never a stale desired-state claim.", noValue = "Unavailable — canonical ownership observation has no valid success timestamp", expr = "time() - max(nutsnews_production_ownership_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host-exporter\",host=\"vps.nutsnews.com\",deployment_environment=\"${var.deployment_environment}\"} > 0)" },
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
            mappings = try(panel.mappings, [])
            noValue  = try(panel.noValue, local.dashboard_no_value[dashboard_key])
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

  # The protected check map is sensitive because it contains endpoint targets
  # and response assertions. Resource identity must not be derived from a
  # sensitive value, so declassify only its fixed, validated names. Individual
  # check values remain sensitive everywhere they are consumed.
  synthetic_http_check_names = nonsensitive(toset(keys(var.synthetic_http_checks)))
  enabled_synthetic_http_checks = length(var.synthetic_monitoring_probe_ids) > 0 ? toset([
    for name in local.synthetic_http_check_names : name
    if nonsensitive(var.synthetic_http_checks[name].enabled)
  ]) : toset([])

  synthetic_target_hosts = {
    for name in local.enabled_synthetic_http_checks :
    name => trimsuffix(lower(split("/", trimprefix(var.synthetic_http_checks[name].target, "https://"))[0]), ":443")
  }
  synthetic_target_role_contract = length(local.enabled_synthetic_http_checks) == 5 && (
    lookup(local.synthetic_target_hosts, "canonical_homepage", "") != "" &&
    lookup(local.synthetic_target_hosts, "canonical_homepage", "") == lookup(local.synthetic_target_hosts, "canonical_readiness", "") &&
    lookup(local.synthetic_target_hosts, "canonical_homepage", "") == lookup(local.synthetic_target_hosts, "canonical_articles_api", "") &&
    lookup(local.synthetic_target_hosts, "vps_readiness", "") != lookup(local.synthetic_target_hosts, "canonical_homepage", "") &&
    lookup(local.synthetic_target_hosts, "vercel_secondary_readiness", "") != lookup(local.synthetic_target_hosts, "canonical_homepage", "") &&
    lookup(local.synthetic_target_hosts, "vps_readiness", "") != lookup(local.synthetic_target_hosts, "vercel_secondary_readiness", "")
  )

  synthetic_approved_job_regex  = "canonical_articles_api|canonical_homepage|canonical_readiness|vercel_secondary_readiness|vps_readiness"
  synthetic_joined_probe_series = "probe_success{job=~\"^(${local.synthetic_approved_job_regex})$\"} * on(job, instance, probe, config_version) group_left() sm_check_info{job=~\"^(${local.synthetic_approved_job_regex})$\",label_service_namespace=\"nutsnews\",label_deployment_environment=\"${var.deployment_environment}\"}"

  synthetic_monthly_api_executions = length(local.enabled_synthetic_http_checks) == 0 ? 0 : sum([
    for name in local.enabled_synthetic_http_checks :
    length(var.synthetic_monitoring_probe_ids) * 1 * (43200 / (nonsensitive(var.synthetic_http_checks[name].frequency_ms) / 60000))
  ])

  synthetic_monthly_api_hard_ceiling    = 90000
  synthetic_monthly_api_guardrail       = min(local.synthetic_monthly_api_hard_ceiling, var.free_synthetic_api_executions_monthly * 0.90)
  synthetic_monthly_api_major_threshold = var.free_synthetic_api_executions_monthly * 0.85

  quota_alert_thresholds = {
    "70" = 0.70
    "85" = 0.85
    "95" = 0.95
  }

  quota_alert_sources = {
    metrics_active_series = {
      uid           = "nn-gc-metrics-series"
      title         = "Grafana Cloud metrics active-series usage"
      expr          = "max(grafanacloud_instance_active_series / on(id) grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})"
      no_data_state = "OK"
      description   = "Grafana Cloud metrics active-series usage is above the live max_global_series_per_user limit guardrail."
    }
    logs_active_streams = {
      uid           = "nn-gc-logs-streams"
      title         = "Grafana Cloud logs active streams"
      expr          = "max(grafanacloud_logs_instance_active_streams) / max(grafanacloud_logs_instance_limits{limit_name=\"max_global_streams_per_user\"})"
      no_data_state = "OK"
      description   = "Grafana Cloud Logs active streams are above the live max_global_streams_per_user guardrail."
    }
    logs_ingestion_rate = {
      uid           = "nn-gc-logs-ingest"
      title         = "Grafana Cloud logs ingestion rate"
      expr          = "max(grafanacloud_logs_instance_bytes_received_per_second) / (max(grafanacloud_logs_instance_limits{limit_name=\"ingestion_rate_mb\"}) * 1024 * 1024)"
      no_data_state = "OK"
      description   = "Grafana Cloud Logs ingestion rate is above the live ingestion_rate_mb guardrail."
    }
    traces_ingestion_rate = {
      uid           = "nn-gc-traces-ingest"
      title         = "Grafana Cloud traces ingestion rate"
      expr          = "max(grafanacloud_traces_instance_bytes_received_per_second) / max(grafanacloud_traces_instance_limits{limit_name=\"ingestion_rate_limit_bytes\"})"
      no_data_state = "OK"
      description   = "Grafana Cloud Traces ingestion appeared even though worker-uplift trace export is deferred; keep traces disabled unless separately approved."
    }
    synthetic_api_execution_projection = {
      uid           = "nn-gc-synthetic-api-executions"
      title         = "Grafana Cloud projected synthetic API executions"
      expr          = "vector(${local.synthetic_monthly_api_executions / var.free_synthetic_api_executions_monthly})"
      no_data_state = "OK"
      description   = "Terraform-managed Synthetic Monitoring checks project above the configured monthly free API-execution allowance guardrail. This is a configuration forecast; verify the remote check inventory for unmanaged checks."
    }
  }

  quota_alert_rules = concat(
    flatten([
      for source_key, source in local.quota_alert_sources : [
        for threshold_name, threshold in local.quota_alert_thresholds : {
          key           = "${source_key}_${threshold_name}"
          uid           = "${source.uid}-${threshold_name}"
          title         = "${source.title} above ${threshold_name}%"
          expr          = source.expr
          threshold     = threshold
          severity      = threshold >= 0.95 ? "critical" : threshold >= 0.85 ? "major" : "warning"
          for_period    = threshold >= 0.95 ? "5m" : "15m"
          no_data_state = source.no_data_state
          description   = source.description
        }
      ]
    ]),
    [local.usage_telemetry_missing_rule],
  )

  log_pipeline_alert_rules = {
    synthetic_inventory_audit_failed = {
      uid           = "nn-sm-inventory-audit-failed"
      title         = "NutsNews synthetic inventory audit failed"
      datasource    = "prometheus"
      expr          = "1 - max(nutsnews_synthetic_inventory_audit_conclusion{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",outcome=\"success\"})"
      threshold     = 0
      for_period    = "15m"
      severity      = "major"
      service       = "synthetic-monitoring"
      no_data_state = "Alerting"
      dashboard_url = "/d/nutsnews-synthetic-uptime-api-checks"
      description   = "The latest completed scheduled read-only Synthetic Monitoring inventory/quota audit did not succeed, or its durable workflow-status export is unavailable."
    }
    synthetic_inventory_audit_overdue = {
      uid           = "nn-sm-inventory-audit-overdue"
      title         = "NutsNews synthetic inventory audit overdue"
      datasource    = "prometheus"
      expr          = "((max(nutsnews_synthetic_inventory_audit_last_run_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) > bool 108000) or vector(0)) + ((max(nutsnews_synthetic_inventory_audit_last_run_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) < bool 0) or vector(0)) + (max(absent(nutsnews_synthetic_inventory_audit_last_run_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0)) + ((max(nutsnews_synthetic_inventory_audit_last_success_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) > bool 108000) or vector(0)) + ((max(nutsnews_synthetic_inventory_audit_last_success_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) < bool 0) or vector(0)) + (max(absent(nutsnews_synthetic_inventory_audit_last_success_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0))"
      threshold     = 0
      for_period    = "15m"
      severity      = "major"
      service       = "synthetic-monitoring"
      no_data_state = "Alerting"
      dashboard_url = "/d/nutsnews-synthetic-uptime-api-checks"
      description   = "No scheduled synthetic inventory audit run or successful audit has been exported within the 30-hour daily dead-man window."
    }
    synthetic_probe_failure = {
      uid           = "nn-sm-probe-failure"
      title         = "NutsNews synthetic probe failed"
      datasource    = "prometheus"
      expr          = "max by (job, probe) (1 - (${local.synthetic_joined_probe_series}))"
      threshold     = 0
      for_period    = "10m"
      severity      = "major"
      service       = "synthetic-monitoring"
      no_data_state = "OK"
      dashboard_url = "/d/nutsnews-synthetic-uptime-api-checks"
      description   = "At least one current public probe for an approved homepage, readiness, or article-API check is failing. Missing probe-series telemetry is alerted separately."
    }
    synthetic_probe_series_contract = {
      uid           = "nn-sm-probe-series-contract"
      title         = "NutsNews synthetic probe series contract failed"
      datasource    = "prometheus"
      expr          = "(5 - (count(count by (job) (${local.synthetic_joined_probe_series})) or vector(0))) + (sum(count by (job) (${local.synthetic_joined_probe_series}) != bool 2) or vector(0)) + (sum(count by (job) (count by (job, probe) (${local.synthetic_joined_probe_series})) != bool 2) or vector(0)) + (sum(count by (job) (count by (job, config_version) (${local.synthetic_joined_probe_series})) != bool 1) or vector(0))"
      threshold     = 0
      for_period    = "10m"
      severity      = "major"
      service       = "synthetic-monitoring"
      no_data_state = "Alerting"
      dashboard_url = "/d/nutsnews-synthetic-uptime-api-checks"
      description   = "Each of the five approved Synthetic Monitoring jobs must continuously expose exactly two current probe series joined to bounded check metadata. Missing, duplicate, stale-config, or unexpected-version series violate the contract."
    }
    alloy_readiness = {
      uid           = "nn-alloy-readiness"
      title         = "Grafana Alloy readiness failed"
      datasource    = "prometheus"
      expr          = "(1 - (max(nutsnews_alloy_ready{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0))) + (1 - (max(nutsnews_alloy_readiness_probe_success{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0))) + (1 - (max(nutsnews_alloy_ready{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0))) + (1 - (max(nutsnews_alloy_readiness_probe_success{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "The VPS or backend Alloy readiness endpoint is unready, its bounded probe failed, or an expected per-host readiness series is absent."
    }
    alloy_self_metrics_missing = {
      uid           = "nn-alloy-self-metrics-missing"
      title         = "Grafana Alloy self metrics missing"
      datasource    = "prometheus"
      expr          = "((1 - min(up{job=\"integrations/nutsnews-vps-alloy\",instance=\"vps.nutsnews.com\"})) or max(absent(up{job=\"integrations/nutsnews-vps-alloy\",instance=\"vps.nutsnews.com\"})) or vector(0)) + ((1 - min(up{job=\"nutsnews-backend-alloy\",instance=\"backend.nutsnews.com\"})) or max(absent(up{job=\"nutsnews-backend-alloy\",instance=\"backend.nutsnews.com\"})) or vector(0))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "One or more independently scraped Alloy self targets are down or missing from Grafana Cloud."
    }
    alloy_internal_metrics_contract = {
      uid           = "nn-alloy-internal-metrics-missing"
      title         = "Grafana Alloy internal metric families missing"
      datasource    = "prometheus"
      expr          = "abs(2 - (count(count by (instance, job) (prometheus_remote_storage_samples_pending{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"})) or vector(0))) + abs(2 - (count(count by (instance, job) (prometheus_remote_storage_samples_failed_total{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"})) or vector(0))) + abs(2 - (count(count by (instance, job) (loki_write_dropped_entries_total{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"})) or vector(0))) + abs(2 - (count(count by (instance, job) (loki_write_batch_retries_total{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"})) or vector(0)))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "Each independently scraped VPS/backend Alloy target must continuously export remote-write pending/failure and Loki drop/retry families, including zero-valued counters."
    }
    alloy_remote_write_failures = {
      uid           = "nn-alloy-remote-write-failures"
      title         = "Grafana Alloy remote-write failures"
      datasource    = "prometheus"
      expr          = "sum by (instance, job) (rate(prometheus_remote_storage_samples_failed_total{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"}[5m]))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "The independently scraped VPS or backend Alloy collector reports failed Prometheus remote-write samples. Check the affected job/instance, credentials, connectivity, and Grafana Cloud quota state."
    }
    alloy_remote_write_backlog = {
      uid           = "nn-alloy-remote-write-backlog"
      title         = "Grafana Alloy remote-write backlog"
      datasource    = "prometheus"
      expr          = "max by (instance, job) (prometheus_remote_storage_samples_pending{job=~\"integrations/nutsnews-vps-alloy|nutsnews-backend-alloy\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"})"
      threshold     = 1000
      for_period    = "10m"
      severity      = "major"
      no_data_state = "OK"
      description   = "The VPS or backend Alloy remote-write path has retained a sustained pending-sample backlog; the job/instance labels identify the affected collector."
    }
    observability_collector_stale = {
      uid           = "nn-observability-collector-stale"
      title         = "NutsNews observability collector stale"
      datasource    = "prometheus"
      expr          = "(((time() - max(nutsnews_observability_textfile_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) > bool 300) or max(absent(nutsnews_observability_textfile_last_success_timestamp_seconds{job=\"integrations/node_exporter\",instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0)) + (((time() - max(nutsnews_backend_metric_scrape_timestamp_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) > bool 600) or max(absent(nutsnews_backend_metric_scrape_timestamp_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0)) + (1 - (max(nutsnews_backend_metric_exporter_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)))"
      threshold     = 0
      for_period    = "5m"
      severity      = "major"
      no_data_state = "Alerting"
      description   = "An expected VPS/backend textfile collector is absent, its oldest per-target snapshot is stale, or the backend collector explicitly reports unavailable."
    }
    caddy_tls_expiry = {
      uid           = "nn-caddy-tls-expiry"
      title         = "NutsNews Caddy TLS certificate expiring"
      datasource    = "prometheus"
      expr          = "max((nutsnews_caddy_tls_certificate_expiry_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"caddy\"} < bool 604800) and on() (nutsnews_caddy_tls_certificate_probe_success{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"caddy\"} == 1))"
      threshold     = 0
      for_period    = "15m"
      severity      = "critical"
      no_data_state = "OK"
      description   = "A successfully probed public Caddy TLS certificate expires in less than seven days. Probe failures are alerted separately."
    }
    caddy_tls_probe_missing = {
      uid           = "nn-caddy-tls-probe-missing"
      title         = "NutsNews Caddy TLS certificate probe failed"
      datasource    = "prometheus"
      expr          = "1 - min(nutsnews_caddy_tls_certificate_probe_success{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"caddy\"})"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "The bounded public Caddy TLS certificate probe failed or its telemetry is missing."
    }
    alloy_loki_dropped_entries = {
      uid           = "nn-alloy-loki-dropped"
      title         = "Grafana Alloy Loki dropped log entries"
      datasource    = "prometheus"
      expr          = "sum by (instance, job) (rate(loki_write_dropped_entries_total{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"}[5m]))"
      threshold     = 0
      for_period    = "5m"
      severity      = "critical"
      no_data_state = "Alerting"
      description   = "Alloy reports dropped Loki entries after exhausting retries, which means log shipping is losing data."
    }
    alloy_loki_batch_retries = {
      uid           = "nn-alloy-loki-retries"
      title         = "Grafana Alloy Loki write retries"
      datasource    = "prometheus"
      expr          = "sum by (instance, job) (rate(loki_write_batch_retries_total{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",service=\"alloy\"}[5m]))"
      threshold     = 0
      for_period    = "10m"
      severity      = "warning"
      no_data_state = "OK"
      description   = "Alloy is retrying Loki writes. Check Grafana Cloud Logs credentials, endpoint reachability, and quota state."
    }
    high_error_log_volume = {
      uid           = "nn-logs-high-error-volume"
      title         = "NutsNews high error log volume"
      datasource    = "loki"
      expr          = "sum(count_over_time({deployment_environment=\"${var.deployment_environment}\",severity=~\"error|critical\"}[5m]))"
      threshold     = 20
      for_period    = "10m"
      severity      = "warning"
      no_data_state = "OK"
      description   = "Recent normalized production streams contain repeated error or critical entries."
    }
    health_audit_non_success = {
      uid           = "nn-health-audit-non-success"
      title         = "NutsNews scheduled health audit non-success"
      datasource    = "prometheus"
      expr          = "max(nutsnews_email_reporting_last_report_conclusion{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\",outcome=~\"critical|delivery_failed|disabled|misconfigured|dry_run|unknown\"}) and on() (max(nutsnews_email_reporting_status_available{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) == 1)"
      threshold     = 0
      for_period    = "5m"
      severity      = "major"
      service       = "health-audit"
      no_data_state = "OK"
      description   = "The latest scheduled health-audit conclusion was not a critical-free success. Delivery failure remains a distinct bounded outcome."
    }
    health_audit_success_missed = {
      uid           = "nn-health-audit-success-missed"
      title         = "NutsNews scheduled health audit success overdue"
      datasource    = "prometheus"
      expr          = "((max(nutsnews_email_reporting_last_report_success_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) > bool 108000) or vector(0)) + ((max(nutsnews_email_reporting_last_report_success_age_seconds{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) < bool 0) or vector(0)) + ((1 - max(nutsnews_email_reporting_status_available{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or max(absent(nutsnews_email_reporting_status_available{service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0))"
      threshold     = 0
      for_period    = "15m"
      severity      = "major"
      service       = "health-audit"
      no_data_state = "Alerting"
      description   = "No critical-free scheduled health-audit success has been published within the standardized 30-hour cadence."
    }
    backup_verification_overdue = {
      uid           = "nn-backup-verification-overdue"
      title         = "NutsNews backup verification overdue"
      datasource    = "prometheus"
      expr          = "((((max(nutsnews_backup_last_verify_finished_age_seconds{instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) > bool 108000) + (max(nutsnews_backup_last_verify_finished_age_seconds{instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) < bool 0)) or max(absent(nutsnews_backup_last_verify_finished_age_seconds{instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"})) or vector(0)) + (1 - (max(nutsnews_backup_last_verify_success{instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0))) + (1 - (max(nutsnews_backup_status_available{instance=\"vps.nutsnews.com\",service_namespace=\"nutsnews\",deployment_environment=\"${var.deployment_environment}\"}) or vector(0)))) + ((((max(nutsnews_backend_backup_last_success_age_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\"}) > bool 108000) + (max(nutsnews_backend_backup_last_success_age_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\"}) < bool 0)) or max(absent(nutsnews_backend_backup_last_success_age_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\"})) or vector(0)) + (1 - (max(nutsnews_backend_backup_status_available{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\"}) or vector(0))))"
      threshold     = 0
      for_period    = "15m"
      severity      = "major"
      service       = "backup"
      no_data_state = "Alerting"
      description   = "A required VPS or backend backup has no successfully verified snapshot within the standardized 30-hour freshness window."
    }
  }

  usage_telemetry_missing_rule = {
    key           = "usage_telemetry_missing"
    uid           = "nn-gc-usage-telemetry-missing"
    title         = "Grafana Cloud usage telemetry missing"
    expr          = "(max(absent(grafanacloud_instance_active_series)) or vector(0)) + (max(absent(grafanacloud_instance_metrics_limits{limit_name=\"max_global_series_per_user\"})) or vector(0)) + (max(absent(grafanacloud_logs_instance_active_streams)) or vector(0)) + (max(absent(grafanacloud_logs_instance_limits{limit_name=\"max_global_streams_per_user\"})) or vector(0)) + (max(absent(grafanacloud_logs_instance_bytes_received_per_second)) or vector(0)) + (max(absent(grafanacloud_logs_instance_limits{limit_name=\"ingestion_rate_mb\"})) or vector(0))"
    threshold     = 0
    severity      = "major"
    for_period    = "10m"
    no_data_state = "Alerting"
    description   = "One or more required Grafana Cloud usage numerator or denominator series are missing; quota percentage alerts remain NoData=OK to avoid false threshold pages."
  }
}
