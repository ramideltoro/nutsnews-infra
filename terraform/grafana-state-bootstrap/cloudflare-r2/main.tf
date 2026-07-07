resource "cloudflare_r2_bucket" "grafana_cloud_state" {
  account_id = var.cloudflare_account_id
  name       = var.bucket_name
  location   = upper(var.location_hint)
}
