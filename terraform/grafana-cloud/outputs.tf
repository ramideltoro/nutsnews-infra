output "folder_uid" {
  description = "Grafana folder UID for NutsNews observability assets."
  value       = grafana_folder.observability.uid
}

output "dashboard_urls" {
  description = "Managed dashboard URLs keyed by dashboard name."
  value = {
    for key, dashboard in grafana_dashboard.observability : key => dashboard.url
  }
}

output "synthetic_monthly_api_execution_estimate" {
  description = "Projected monthly Synthetic Monitoring API executions using probes x tests x rounded duration x (43200 / frequency)."
  value       = local.synthetic_monthly_api_executions
}

output "synthetic_monthly_api_execution_guardrail" {
  description = "70% guardrail of the configured free Synthetic Monitoring API execution assumption."
  value       = local.synthetic_monthly_api_guardrail
}

output "free_synthetic_browser_execution_assumption" {
  description = "Configured Grafana Cloud Free Synthetic Monitoring browser execution assumption."
  value       = var.free_synthetic_browser_executions_monthly
}

output "free_k6_vuh_assumption" {
  description = "Configured Grafana Cloud Free k6 virtual user hour assumption."
  value       = var.free_k6_vuh_monthly
}
