variable "grafana_url" {
  description = "Grafana Cloud stack URL. Supply through TF_VAR_grafana_url or the protected GitHub environment."
  type        = string
  sensitive   = true

  validation {
    condition     = startswith(var.grafana_url, "https://")
    error_message = "grafana_url must start with https://."
  }
}

variable "grafana_service_account_token" {
  description = "Grafana service account token with permissions to manage folders, dashboards, alert rules, and synthetic checks."
  type        = string
  sensitive   = true

  validation {
    condition     = length(trimspace(var.grafana_service_account_token)) > 0
    error_message = "grafana_service_account_token must be set from a secret."
  }
}

variable "prometheus_datasource_uid" {
  description = "UID of the Grafana Cloud Prometheus/Mimir datasource that receives Alloy metrics."
  type        = string

  validation {
    condition     = length(trimspace(var.prometheus_datasource_uid)) > 0
    error_message = "prometheus_datasource_uid must be set."
  }
}

variable "loki_datasource_uid" {
  description = "UID of the Grafana Cloud Loki datasource that receives Alloy logs."
  type        = string

  validation {
    condition     = length(trimspace(var.loki_datasource_uid)) > 0
    error_message = "loki_datasource_uid must be set."
  }
}

variable "usage_datasource_uid" {
  description = "UID of the Grafana Cloud usage datasource, usually named grafanacloud-usage."
  type        = string

  validation {
    condition     = length(trimspace(var.usage_datasource_uid)) > 0
    error_message = "usage_datasource_uid must be set."
  }
}

variable "folder_title" {
  description = "Grafana folder title for NutsNews observability assets."
  type        = string
  default     = "NutsNews Observability"
}

variable "deployment_environment" {
  description = "Default deployment_environment label used by Alloy external labels."
  type        = string
  default     = "production"
}

variable "quota_alert_contact_route" {
  description = "Optional routing label value for existing Grafana notification policies. This module does not create contact points because they often contain secrets."
  type        = string
  default     = "default"
}

variable "free_synthetic_api_executions_monthly" {
  description = "Current Grafana Cloud Free Synthetic Monitoring API execution assumption."
  type        = number
  default     = 100000
}

variable "free_synthetic_browser_executions_monthly" {
  description = "Current Grafana Cloud Free Synthetic Monitoring browser execution assumption."
  type        = number
  default     = 10000
}

variable "free_k6_vuh_monthly" {
  description = "Current Grafana Cloud Free k6 virtual user hour assumption."
  type        = number
  default     = 500
}

variable "synthetic_monitoring_probe_ids" {
  description = "Synthetic Monitoring probe location IDs. Empty disables synthetic check creation."
  type        = list(number)
  default     = []
}

variable "synthetic_http_checks" {
  description = "HTTP Synthetic Monitoring checks. Keep targets out of Git and supply through protected variables or tfvars outside version control."
  type = map(object({
    target                          = string
    enabled                         = optional(bool, true)
    frequency_ms                    = optional(number, 1800000)
    timeout_ms                      = optional(number, 5000)
    valid_status_codes              = optional(list(number), [200])
    fail_if_body_matches_regexp     = optional(list(string), [])
    fail_if_body_not_matches_regexp = optional(list(string), [])
  }))
  default = {}

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) : startswith(check.target, "https://")
    ])
    error_message = "Every synthetic HTTP check target must start with https://."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) : check.frequency_ms >= 900000
    ])
    error_message = "Synthetic checks must run no more frequently than every 15 minutes."
  }
}
