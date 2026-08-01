locals {
  production_host_expression  = "http.host in {\"nutsnews.com\" \"www.nutsnews.com\"}"
  cacheable_method_expression = "http.request.method in {\"GET\" \"HEAD\"}"
  protected_path_expression = join(" or ", [
    "starts_with(http.request.uri.path, \"/monitoring\")",
    "starts_with(http.request.uri.path, \"/admin\")",
    "starts_with(http.request.uri.path, \"/api/auth\")",
    "starts_with(http.request.uri.path, \"/api/internal\")",
    "starts_with(http.request.uri.path, \"/api/log-test\")",
    "http.request.uri.path eq \"/api/contact\"",
    "http.request.uri.path eq \"/api/engagement\"",
    "http.request.uri.path eq \"/api/runtime-config\"",
    "http.request.uri.path eq \"/readyz\"",
  ])
  router_variant_expression = join(" or ", [
    "http.request.headers[\"rsc\"][0] eq \"1\"",
    "http.request.headers[\"next-router-prefetch\"][0] eq \"1\"",
    "http.request.headers[\"next-router-state-tree\"][0] ne \"\"",
    "http.request.headers[\"purpose\"][0] eq \"prefetch\"",
  ])
  baseline_cache_rules = [
    {
      ref         = "c98bc1f5ad7940d69a365ea7c9f2d6d0"
      action      = "set_cache_settings"
      description = "Cache NutsNews public content"
      expression  = "(http.host in {\"nutsnews.com\" \"www.nutsnews.com\"} and (http.request.uri.path eq \"/\" or http.request.uri.path eq \"/about\" or starts_with(http.request.uri.path, \"/articles/\") or starts_with(http.request.uri.path, \"/api/articles\")))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 7200
        }
      }
    },
    {
      ref         = "7e4dc1716e4143689d81a3563754456d"
      action      = "set_cache_settings"
      description = "Bypass monitoring routes"
      expression  = "(http.host in {\"nutsnews.com\" \"www.nutsnews.com\"} and starts_with(http.request.uri.path, \"/monitoring\"))"
      enabled     = true
      action_parameters = {
        cache = false
      }
    },
  ]

  coordinated_cache_rules = [
    {
      ref         = "bypass-protected-and-next-router-variants"
      action      = "set_cache_settings"
      description = "Bypass protected routes and Next.js router variants"
      expression  = "(${local.production_host_expression} and (${local.protected_path_expression} or ${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache = false
      }
    },
    {
      ref         = "cache-next-optimized-images"
      action      = "set_cache_settings"
      description = "Cache Next.js optimized images for 30 days"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and http.request.uri.path eq \"/_next/image\")"
      enabled     = true
      action_parameters = {
        cache = true
        browser_ttl = {
          mode = "respect_origin"
        }
        edge_ttl = {
          mode    = "override_origin"
          default = 2592000
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            header = {
              include        = ["accept"]
              exclude_origin = false
            }
            query_string = {
              include = {
                list = ["q", "url", "w"]
              }
            }
          }
        }
        serve_stale = {
          disable_stale_while_updating = false
        }
      }
    },
    {
      ref         = "cache-homepage"
      action      = "set_cache_settings"
      description = "Cache the homepage with the two-hour publication fallback"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and http.request.uri.path eq \"/\" and not (${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache = true
        browser_ttl = {
          mode = "respect_origin"
        }
        edge_ttl = {
          mode    = "override_origin"
          default = 7200
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = {
              exclude = { all = true }
            }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
    {
      ref         = "cache-public-feed-apis"
      action      = "set_cache_settings"
      description = "Cache public feed APIs with functional parameters only"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and http.request.uri.path in {\"/api/articles\" \"/api/home-feed\"} and not (${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 7200
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = {
              include = { list = ["category", "cursor", "home", "lang", "page"] }
            }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
    {
      ref         = "cache-public-search"
      action      = "set_cache_settings"
      description = "Cache normalized public search combinations for six hours"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and http.request.uri.path eq \"/api/search\" and not (${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 21600
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = {
              include = { list = ["lang", "limit", "page", "q"] }
            }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
    {
      ref         = "cache-sitemaps"
      action      = "set_cache_settings"
      description = "Cache sitemap index, root sitemap, shards, and robots for two hours"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and (http.request.uri.path in {\"/robots.txt\" \"/sitemap.xml\" \"/sitemap-index.xml\"} or starts_with(http.request.uri.path, \"/articles/sitemap/\")))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 7200
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = { exclude = { all = true } }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
    {
      ref         = "cache-article-pages"
      action      = "set_cache_settings"
      description = "Cache article pages and detail APIs for 30 days"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and ((starts_with(http.request.uri.path, \"/articles/\") and not starts_with(http.request.uri.path, \"/articles/sitemap/\")) or (starts_with(http.request.uri.path, \"/api/articles/\") and http.request.uri.path ne \"/api/articles\")) and not (${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 2592000
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = { include = { list = ["lang"] } }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
    {
      ref         = "cache-informational-pages"
      action      = "set_cache_settings"
      description = "Cache public informational pages for 30 days"
      expression  = "(${local.production_host_expression} and ${local.cacheable_method_expression} and http.request.uri.path in {\"/about\" \"/apps\" \"/contact\" \"/privacy\" \"/privacy/android\" \"/privacy/ios\" \"/saved\"} and not (${local.router_variant_expression}))"
      enabled     = true
      action_parameters = {
        cache       = true
        browser_ttl = { mode = "respect_origin" }
        edge_ttl = {
          mode    = "override_origin"
          default = 2592000
        }
        cache_key = {
          cache_deception_armor      = true
          ignore_query_strings_order = true
          custom_key = {
            query_string = { exclude = { all = true } }
          }
        }
        serve_stale = { disable_stale_while_updating = false }
      }
    },
  ]
}

resource "cloudflare_ruleset" "nutsnews_public_cache" {
  zone_id = var.cloudflare_zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = jsondecode(
    var.cache_policy_mode == "coordinated"
    ? jsonencode(local.coordinated_cache_rules)
    : jsonencode(local.baseline_cache_rules)
  )
}
