provider "grafana" {
  url  = var.grafana_url
  auth = var.grafana_service_account_token
}
