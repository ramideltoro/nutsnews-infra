variable "grafana_url" {
  description = "Grafana Cloud stack URL. Supply through TF_VAR_grafana_url or the protected GitHub environment."
  type        = string
  sensitive   = true

  validation {
    condition = can(regex(
      "(?i)^https://kindcantaloupe2036\\.grafana\\.net(:443)?/?\\z",
      var.grafana_url,
    ))
    error_message = "grafana_url must be the exact query-free https://kindcantaloupe2036.grafana.net origin using implicit or explicit port 443."
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

variable "operations_email_recipients" {
  description = "Comma-separated operations email recipients sourced from the protected NUTSNEWS_EMAIL_TO secret."
  type        = string
  sensitive   = true

  validation {
    condition = length(compact([
      for address in split(",", var.operations_email_recipients) : trimspace(address)
      ])) > 0 && alltrue([
      for address in compact([
        for candidate in split(",", var.operations_email_recipients) : trimspace(candidate)
      ]) : can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", address))
    ])
    error_message = "operations_email_recipients must contain one or more comma-separated email addresses from the protected NUTSNEWS_EMAIL_TO secret."
  }
}

variable "quota_alert_contact_route" {
  description = "Routing label applied to all Terraform-managed NutsNews alert rules."
  type        = string
  default     = "operations-email"
}

variable "free_synthetic_api_executions_monthly" {
  description = "Current Grafana Cloud Free Synthetic Monitoring API execution assumption."
  type        = number
  default     = 100000

  validation {
    condition     = var.free_synthetic_api_executions_monthly > 0
    error_message = "free_synthetic_api_executions_monthly must be positive."
  }
}

variable "free_synthetic_browser_executions_monthly" {
  description = "Current Grafana Cloud Free Synthetic Monitoring browser execution assumption."
  type        = number
  default     = 10000
}

variable "enforce_rollout_decisions" {
  description = "Fail-closed switch for unresolved rollout decisions. Keep true for every production plan/apply; false is allowed only in explicitly non-mutating static CI fixtures."
  type        = bool
  default     = true
}

variable "synthetic_major_forecast_acknowledged" {
  description = "Protected explicit acknowledgment that the reviewed rollout choice is to retain five checks, two probes, and five-minute cadence despite the standing >=85% major forecast. False blocks production plan/apply; other choices require source changes."
  type        = bool
  default     = false
}

variable "free_k6_vuh_monthly" {
  description = "Current Grafana Cloud Free k6 virtual user hour assumption."
  type        = number
  default     = 500
}

variable "synthetic_monitoring_probe_ids" {
  description = "Exactly two public Synthetic Monitoring probe IDs. Empty is retained only for backendless validate; production plan/apply preconditions reject it."
  type        = list(number)
  default     = []

  validation {
    condition     = length(var.synthetic_monitoring_probe_ids) == 0 || length(var.synthetic_monitoring_probe_ids) == 2
    error_message = "Synthetic Monitoring accepts only an empty backendless-validation default or exactly two public probes; production requires the latter."
  }
}

variable "worker_terminal_slo_alerting_enabled" {
  description = "Protected cutover switch for Grafana-generated worker terminal-success fast/slow burn alerts. Keep false while worker uplift is shadow-only."
  type        = bool
  default     = false
}

variable "synthetic_http_checks" {
  description = "The five approved read-only HTTP Synthetic Monitoring checks. Keep targets out of Git and supply through protected variables or tfvars outside version control."
  sensitive   = true
  type = map(object({
    target                          = string
    enabled                         = optional(bool, true)
    frequency_ms                    = optional(number, 300000)
    timeout_ms                      = optional(number, 5000)
    valid_status_codes              = optional(list(number), [200])
    fail_if_body_matches_regexp     = optional(list(string), [])
    fail_if_body_not_matches_regexp = optional(list(string), [])
    fail_if_header_matches_regexp = optional(list(object({
      allow_missing = optional(bool, false)
      header        = string
      regexp        = string
    })), [])
    fail_if_header_not_matches_regexp = optional(list(object({
      allow_missing = optional(bool, false)
      header        = string
      regexp        = string
    })), [])
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
      for check in values(var.synthetic_http_checks) :
      check.frequency_ms >= 10000 && check.frequency_ms <= 3600000
    ])
    error_message = "Synthetic checks must run between every 10 seconds and every 60 minutes."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) :
      can(regex("^https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?(?::443)?(?:/[^?#]*)?$", lower(check.target)))
    ])
    error_message = "Synthetic targets must use public DNS hostnames and credential-free, query-free HTTPS URLs on the default TLS port or 443."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) : check.frequency_ms == 300000
    ])
    error_message = "Every approved Synthetic Monitoring check must run every five minutes (300000 ms)."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) :
      check.timeout_ms >= 1000 && check.timeout_ms <= 60000
    ])
    error_message = "Synthetic check timeouts must be between 1 and 60 seconds."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) :
      check.enabled && length(check.valid_status_codes) == 1 && check.valid_status_codes[0] == 200
    ])
    error_message = "Every approved Synthetic Monitoring check must be enabled and require HTTP 200 so controlled status mismatches fail."
  }

  validation {
    condition = length(var.synthetic_http_checks) == 0 || toset(keys(var.synthetic_http_checks)) == toset([
      "canonical_articles_api",
      "canonical_homepage",
      "canonical_readiness",
      "vercel_secondary_readiness",
      "vps_readiness",
    ])
    error_message = "Synthetic Monitoring must configure exactly the five approved read-only check names."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) : !can(regex(
        "/(refresh|controller|ingest|trigger|publish)(/|[?]|$)",
        lower(check.target),
      ))
    ])
    error_message = "Synthetic Monitoring targets must not call refresh, controller, ingestion, trigger, or publication routes."
  }

  validation {
    condition = alltrue([
      for name, check in var.synthetic_http_checks :
      name == "canonical_homepage" ? can(regex("^https://[^/]+/?$", lower(check.target))) :
      name == "canonical_articles_api" ? can(regex("/api/articles/?$", lower(check.target))) :
      can(regex("/readyz/?$", lower(check.target)))
    ])
    error_message = "Synthetic targets must map to the approved canonical homepage, read-only /api/articles, or /readyz routes."
  }

  validation {
    condition = alltrue([
      for check in values(var.synthetic_http_checks) : (
        length(check.fail_if_body_matches_regexp) +
        length(check.fail_if_body_not_matches_regexp) +
        length(check.fail_if_header_matches_regexp) +
        length(check.fail_if_header_not_matches_regexp)
      ) > 0
    ])
    error_message = "Every Synthetic Monitoring check must validate at least one expected body or header assertion in addition to its status code."
  }

  validation {
    condition = alltrue([
      for name, check in var.synthetic_http_checks :
      name == "canonical_homepage" ? (
        jsonencode(check.fail_if_body_matches_regexp) == jsonencode(["maintenance"]) &&
        jsonencode(check.fail_if_body_not_matches_regexp) == jsonencode(["NutsNews"]) &&
        length(check.fail_if_header_matches_regexp) == 0 &&
        length(check.fail_if_header_not_matches_regexp) == 0
        ) : name == "canonical_articles_api" ? (
        length(check.fail_if_body_matches_regexp) == 0 &&
        jsonencode(check.fail_if_body_not_matches_regexp) == jsonencode(["articles"]) &&
        length(check.fail_if_header_matches_regexp) == 0 &&
        jsonencode(check.fail_if_header_not_matches_regexp) == jsonencode([{
          allow_missing = false
          header        = "Cache-Control"
          regexp        = "public|max-age|s-maxage"
        }])
        ) : name == "canonical_readiness" ? (
        jsonencode(check.fail_if_body_matches_regexp) == jsonencode(["deploymentTarget.*unknown"]) &&
        jsonencode(check.fail_if_body_not_matches_regexp) == jsonencode([
          "ready.*true",
          "deploymentTarget.*(production-vps|vercel-production)",
        ]) &&
        length(check.fail_if_header_matches_regexp) == 0 &&
        jsonencode(check.fail_if_header_not_matches_regexp) == jsonencode([{
          allow_missing = false
          header        = "Cache-Control"
          regexp        = "no-store"
        }])
        ) : name == "vps_readiness" ? (
        jsonencode(check.fail_if_body_matches_regexp) == jsonencode(["deploymentTarget.*unknown"]) &&
        jsonencode(check.fail_if_body_not_matches_regexp) == jsonencode([
          "ready.*true",
          "deploymentTarget.*production-vps",
        ]) &&
        length(check.fail_if_header_matches_regexp) == 0 &&
        jsonencode(check.fail_if_header_not_matches_regexp) == jsonencode([{
          allow_missing = false
          header        = "Cache-Control"
          regexp        = "no-store"
        }])
        ) : name == "vercel_secondary_readiness" ? (
        jsonencode(check.fail_if_body_matches_regexp) == jsonencode(["deploymentTarget.*unknown"]) &&
        jsonencode(check.fail_if_body_not_matches_regexp) == jsonencode([
          "ready.*true",
          "deploymentTarget.*vercel-production",
        ]) &&
        length(check.fail_if_header_matches_regexp) == 0 &&
        jsonencode(check.fail_if_header_not_matches_regexp) == jsonencode([{
          allow_missing = false
          header        = "Cache-Control"
          regexp        = "no-store"
        }])
      ) : false
    ])
    error_message = "Every synthetic check must use the exact approved behavioral assertion patterns; merely token-containing or non-matching regexps are rejected."
  }
}
