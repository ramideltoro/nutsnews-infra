output "access_application_audience" {
  description = "Access application audience to configure in the VPS-side JWT verifier."
  value       = cloudflare_zero_trust_access_application.staging.aud
  sensitive   = true
}

output "staging_hostname" {
  description = "Protected staging hostname."
  value       = cloudflare_dns_record.staging.name
}
