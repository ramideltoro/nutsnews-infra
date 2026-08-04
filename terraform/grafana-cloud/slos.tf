locals {
  production_legacy_ingestion_age_selector = "nutsnews_backend_legacy_worker_last_scheduled_success_age_seconds{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",host=\"backend.nutsnews.com\"}"
  production_legacy_owner_selector         = "nutsnews_backend_worker_uplift_deployment_info{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",host=\"backend.nutsnews.com\",ingestion_owner=\"legacy_shards\"}"
  production_uplift_scheduler_selector     = "nutsnews_worker_scheduler_loop_fresh{job=\"nutsnews-worker-uplift\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",service=\"scheduler\"}"
  production_uplift_owner_selector         = "nutsnews_backend_worker_uplift_expected_active{job=\"nutsnews-backend-host\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",service=\"host\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",host=\"backend.nutsnews.com\"}"
  production_ingestion_fresh_good          = "((max(${local.production_legacy_ingestion_age_selector}) <= 900) and on() (max(${local.production_legacy_ingestion_age_selector}) >= 0) and on() (max(${local.production_legacy_owner_selector}) == 1)) or ((max(${local.production_uplift_scheduler_selector}) == 1) and on() (max(${local.production_uplift_owner_selector}) == 1))"
  production_ingestion_fresh_valid         = "((max(${local.production_legacy_ingestion_age_selector}) >= 0) and on() (max(${local.production_legacy_owner_selector}) == 1)) or ((count(${local.production_uplift_scheduler_selector}) > 0) and on() (max(${local.production_uplift_owner_selector}) == 1))"

  service_level_objectives = {
    public_availability = {
      name             = "NutsNews public availability"
      description      = "Canonical homepage availability from two independent public probes."
      objective        = 0.995
      service          = "web"
      alerting_enabled = true
      query            = "sum(sum_over_time(probe_success{job=\"canonical_homepage\"}[$__interval])) / sum(count_over_time(probe_success{job=\"canonical_homepage\"}[$__interval]))"
      dashboard_url    = "/d/nutsnews-synthetic-uptime-api-checks"
    }
    api_latency = {
      name             = "NutsNews API latency"
      description      = "At least 95% of successful read-only article API checks complete within 750 milliseconds; failed checks are availability failures and are excluded from this latency denominator."
      objective        = 0.95
      service          = "web-api"
      alerting_enabled = true
      query            = "(sum(count_over_time(((probe_duration_seconds{job=\"canonical_articles_api\"} <= 0.75) and on(job, instance, probe, config_version) (probe_success{job=\"canonical_articles_api\"} == 1))[$__interval:])) or 0 * sum(count_over_time((probe_success{job=\"canonical_articles_api\"} == 1)[$__interval:]))) / sum(count_over_time((probe_success{job=\"canonical_articles_api\"} == 1)[$__interval:]))"
      dashboard_url    = "/d/nutsnews-synthetic-uptime-api-checks"
    }
    feed_freshness = {
      name             = "NutsNews ingestion freshness"
      description      = "At least 99% of valid observations report a successful scheduled ingestion cycle within 15 minutes for the active production owner. Source publishers are not required to create new content during every cycle."
      objective        = 0.99
      service          = "publication"
      alerting_enabled = true
      query            = "(sum(count_over_time((${local.production_ingestion_fresh_good})[$__interval:])) or 0 * sum(count_over_time((${local.production_ingestion_fresh_valid})[$__interval:]))) / sum(count_over_time((${local.production_ingestion_fresh_valid})[$__interval:]))"
      dashboard_url    = "/d/nutsnews-worker-uplift-slos"
    }
    worker_terminal_success = {
      name             = "NutsNews worker terminal success"
      description      = "Terminal worker events complete successfully; generated burn alerts remain disabled while worker uplift is shadow-only."
      objective        = 0.99
      service          = "worker"
      alerting_enabled = var.worker_terminal_slo_alerting_enabled
      query            = "sum(rate(nutsnews_worker_uplift_stage_events_total{job=\"nutsnews-worker-uplift\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",service=~\"fetcher|canonicalizer|enrichment|approval|translation|persistence|publication\",outcome=~\"success|duplicate\"}[$__rate_interval])) / sum(rate(nutsnews_worker_uplift_stage_events_total{job=\"nutsnews-worker-uplift\",instance=\"backend.nutsnews.com\",service_namespace=\"nutsnews\",host=\"backend.nutsnews.com\",environment=\"${var.deployment_environment}\",deployment_environment=\"${var.deployment_environment}\",service=~\"fetcher|canonicalizer|enrichment|approval|translation|persistence|publication\",outcome=~\"success|duplicate|invalid|failure|dlq\"}[$__rate_interval]))"
      dashboard_url    = "/d/nutsnews-worker-uplift-slos"
    }
  }
}

resource "grafana_slo" "nutsnews" {
  for_each = local.service_level_objectives

  name        = each.value.name
  description = each.value.description
  folder_uid  = grafana_folder.observability.uid

  destination_datasource {
    uid = var.prometheus_datasource_uid
  }

  objectives {
    value  = each.value.objective
    window = "30d"
  }

  query {
    type = "freeform"

    freeform {
      query = each.value.query
    }
  }

  label {
    key   = "deployment_environment"
    value = var.deployment_environment
  }

  label {
    key   = "owner"
    value = "nutsnews-observability"
  }

  label {
    key   = "service"
    value = each.value.service
  }

  dynamic "alerting" {
    for_each = each.value.alerting_enabled ? [true] : []

    content {
      annotation {
        key   = "summary"
        value = "${each.value.name} error budget burn requires operator attention."
      }

      annotation {
        key   = "dashboard_url"
        value = each.value.dashboard_url
      }

      annotation {
        key   = "runbook_url"
        value = local.grafana_observability_runbook_url
      }

      label {
        key   = "deployment_environment"
        value = var.deployment_environment
      }

      label {
        key   = "owner"
        value = "nutsnews-observability"
      }

      label {
        key   = "route"
        value = var.quota_alert_contact_route
      }

      label {
        key   = "service"
        value = each.value.service
      }

      fastburn {
        label {
          key   = "severity"
          value = "critical"
        }
      }

      slowburn {
        label {
          key   = "severity"
          value = "warning"
        }
      }
    }
  }
}
