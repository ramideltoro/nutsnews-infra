locals {
  operations_email_addresses = compact([
    for address in split(",", var.operations_email_recipients) : trimspace(address)
  ])
}

resource "grafana_contact_point" "operations_email" {
  name = "NutsNews operations email"

  email {
    addresses               = local.operations_email_addresses
    disable_resolve_message = false
    single_email            = true
    subject                 = "[{{ .Status | toUpper }}] NutsNews {{ .CommonLabels.severity }}: {{ .CommonLabels.alertname }}"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "grafana_notification_policy" "operations_email" {
  contact_point   = grafana_contact_point.operations_email.name
  group_by        = ["alertname", "service", "deployment_environment"]
  group_wait      = "5m"
  group_interval  = "15m"
  repeat_interval = "6h"

  policy {
    contact_point   = grafana_contact_point.operations_email.name
    group_by        = ["alertname", "service", "deployment_environment"]
    group_wait      = "30s"
    group_interval  = "5m"
    repeat_interval = "1h"

    matcher {
      label = "severity"
      match = "=~"
      value = "critical|major"
    }
  }

  policy {
    contact_point   = grafana_contact_point.operations_email.name
    group_by        = ["alertname", "service", "deployment_environment"]
    group_wait      = "5m"
    group_interval  = "15m"
    repeat_interval = "6h"

    matcher {
      label = "severity"
      match = "=~"
      value = "warning|minor|low"
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}
