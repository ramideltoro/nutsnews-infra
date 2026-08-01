variable "cloudflare_api_token" {
  description = "Zone-scoped token used only by the protected Cloudflare cache workflow."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone id for nutsnews.com."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cloudflare_zone_id))
    error_message = "cloudflare_zone_id must be a 32-character lowercase hexadecimal id."
  }
}

variable "cache_ruleset_id" {
  description = "Existing zone entrypoint ruleset imported before its first managed plan."
  type        = string
  default     = "865f91ce8f6d4ddf84c66401137a3a28"

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cache_ruleset_id))
    error_message = "cache_ruleset_id must be a 32-character lowercase hexadecimal id."
  }
}

variable "cache_policy_mode" {
  description = "baseline imports the active two-rule policy without drift; coordinated enables the reviewed long-lived cache backlog."
  type        = string
  default     = "baseline"

  validation {
    condition     = contains(["baseline", "coordinated"], var.cache_policy_mode)
    error_message = "cache_policy_mode must be baseline or coordinated."
  }
}
