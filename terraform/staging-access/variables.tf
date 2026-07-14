variable "cloudflare_api_token" {
  description = "Protected API token limited to DNS and Access application/policy management."
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account that owns the NutsNews Zero Trust organization."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for nutsnews.com."
  type        = string
  sensitive   = true
}

variable "origin_ipv4" {
  description = "Existing VPS IPv4 address used by the proxied staging record."
  type        = string
  sensitive   = true
}

variable "authorized_browser_emails" {
  description = "Exact browser identities allowed through Cloudflare Access."
  type        = set(string)
  sensitive   = true
  validation {
    condition     = length(var.authorized_browser_emails) > 0
    error_message = "At least one exact browser email must be configured."
  }
}

variable "qualifier_service_token_id" {
  description = "ID (not secret) of the Cloudflare Access service token whose credential pair lives only in staging-tests."
  type        = string
  sensitive   = true
}
