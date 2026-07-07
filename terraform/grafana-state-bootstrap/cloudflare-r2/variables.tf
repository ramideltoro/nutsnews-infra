variable "cloudflare_account_id" {
  description = "Cloudflare account ID that owns the R2 bucket. Supply from the protected GitHub environment."
  type        = string
  sensitive   = true

  validation {
    condition     = length(trimspace(var.cloudflare_account_id)) > 0
    error_message = "cloudflare_account_id must be set from a protected secret."
  }
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with account-level R2 bucket management permission. Supply from the protected GitHub environment."
  type        = string
  sensitive   = true

  validation {
    condition     = length(trimspace(var.cloudflare_api_token)) > 0
    error_message = "cloudflare_api_token must be set from a protected secret."
  }
}

variable "bucket_name" {
  description = "Private Cloudflare R2 bucket name for the Grafana Cloud OpenTofu state object."
  type        = string
  default     = "nutsnews-grafana-cloud-tofu-state"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3-63 characters using lowercase letters, numbers, and hyphens, and it cannot start or end with a hyphen."
  }
}

variable "location_hint" {
  description = "Cloudflare R2 bucket location hint. Use a standard R2 location hint such as WNAM, ENAM, WEUR, EEUR, or APAC."
  type        = string
  default     = "WNAM"

  validation {
    condition     = contains(["WNAM", "ENAM", "WEUR", "EEUR", "APAC"], upper(var.location_hint))
    error_message = "location_hint must be one of WNAM, ENAM, WEUR, EEUR, or APAC."
  }
}
