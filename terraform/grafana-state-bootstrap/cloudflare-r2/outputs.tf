output "bucket_name" {
  description = "R2 bucket name to reference from the Grafana Cloud backend config secret."
  value       = cloudflare_r2_bucket.grafana_cloud_state.name
}

output "state_key" {
  description = "Recommended object key for the Grafana Cloud OpenTofu state file."
  value       = "grafana-cloud/terraform.tfstate"
}

output "backend_config_template" {
  description = "Template for NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG. Replace placeholders outside Git."
  value       = <<-EOT
    bucket                      = "${cloudflare_r2_bucket.grafana_cloud_state.name}"
    key                         = "grafana-cloud/terraform.tfstate"
    region                      = "auto"
    endpoints                   = { s3 = "https://<cloudflare-account-id>.r2.cloudflarestorage.com" }
    access_key                  = "<r2-access-key-id>"
    secret_key                  = "<r2-secret-access-key>"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
    use_lockfile                = true
  EOT
}
