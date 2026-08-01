output "folder_uids" {
  description = "Grafana folder UIDs keyed by host ownership scope."
  value = {
    vps     = grafana_folder.observability.uid
    backend = grafana_folder.backend_observability.uid
  }
}

output "folder_uid" {
  description = "Backward-compatible Grafana folder UID for NutsNews VPS observability assets."
  value       = grafana_folder.observability.uid
}

output "dashboard_urls" {
  description = "Managed dashboard URLs keyed by host ownership scope and dashboard name."
  value = {
    vps = {
      for key, dashboard in grafana_dashboard.observability : key => dashboard.url
    }
    backend = {
      for key, dashboard in grafana_dashboard.backend_observability : key => dashboard.url
    }
  }
}

output "synthetic_monthly_api_execution_estimate" {
  description = "Projected monthly Synthetic Monitoring API executions using probes x tests x rounded duration x (43200 / frequency)."
  value       = local.synthetic_monthly_api_executions
}

output "synthetic_monthly_api_execution_guardrail" {
  description = "90% guardrail of the configured free Synthetic Monitoring API execution assumption."
  value       = local.synthetic_monthly_api_guardrail
}

output "synthetic_monthly_api_major_threshold" {
  description = "85% major threshold of the configured free Synthetic Monitoring API execution assumption."
  value       = local.synthetic_monthly_api_major_threshold
}

output "synthetic_major_forecast_acknowledged" {
  description = "Protected reviewed decision state for retaining the standing-major five-check/two-probe/five-minute topology."
  value       = var.synthetic_major_forecast_acknowledged
}

output "enforce_rollout_decisions" {
  description = "Whether unresolved rollout decisions are fail-closed; production plans and applies must report true."
  value       = var.enforce_rollout_decisions
}

output "synthetic_check_ids" {
  description = "Grafana Synthetic Monitoring check IDs keyed by approved check name."
  value = {
    for key, check in grafana_synthetic_monitoring_check.http : key => check.id
  }
}

output "synthetic_probe_selection" {
  description = "Resolved Synthetic Monitoring probe IDs and public/private status for the protected two-probe selection."
  value = {
    for name, probe in data.grafana_synthetic_monitoring_probe.selected : name => {
      id     = local.available_synthetic_monitoring_probes[name]
      public = probe.public
    }
  }
}

output "slo_uuids" {
  description = "Grafana SLO UUIDs keyed by service-level objective name."
  value = {
    for key, slo in grafana_slo.nutsnews : key => slo.uuid
  }
}

output "worker_terminal_slo_alerting_enabled" {
  description = "Protected worker cutover state controlling Grafana-generated terminal-success burn alerts."
  value       = var.worker_terminal_slo_alerting_enabled
}

output "operations_contact_point" {
  description = "Name of the managed operations contact point."
  value       = grafana_contact_point.operations_email.name
}

output "free_synthetic_browser_execution_assumption" {
  description = "Configured Grafana Cloud Free Synthetic Monitoring browser execution assumption."
  value       = var.free_synthetic_browser_executions_monthly
}

output "free_k6_vuh_assumption" {
  description = "Configured Grafana Cloud Free k6 virtual user hour assumption."
  value       = var.free_k6_vuh_monthly
}
