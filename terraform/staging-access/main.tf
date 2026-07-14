resource "cloudflare_dns_record" "staging" {
  zone_id = var.cloudflare_zone_id
  name    = "staging"
  type    = "A"
  content = var.origin_ipv4
  proxied = true
  ttl     = 1
  comment = "GitOps-managed protected NutsNews staging hostname"
}

resource "cloudflare_zero_trust_access_policy" "browser" {
  account_id = var.cloudflare_account_id
  name       = "NutsNews staging authorized browsers"
  decision   = "allow"
  include = [
    for email in var.authorized_browser_emails : {
      email = { email = email }
    }
  ]
  session_duration = "8h"
}

resource "cloudflare_zero_trust_access_policy" "qualifier" {
  account_id = var.cloudflare_account_id
  name       = "NutsNews staging independent qualifier"
  decision   = "non_identity"
  include = [{
    service_token = { token_id = var.qualifier_service_token_id }
  }]
  session_duration = "1h"
}

resource "cloudflare_zero_trust_access_policy" "acme_challenge" {
  account_id = var.cloudflare_account_id
  name       = "NutsNews staging ACME challenge"
  decision   = "bypass"
  include = [{
    everyone = {}
  }]
}

resource "cloudflare_zero_trust_access_application" "acme_challenge" {
  account_id           = var.cloudflare_account_id
  name                 = "NutsNews staging ACME challenge"
  type                 = "self_hosted"
  domain               = "staging.nutsnews.com/.well-known/acme-challenge/*"
  app_launcher_visible = false
  policies = [{
    id         = cloudflare_zero_trust_access_policy.acme_challenge.id
    precedence = 1
  }]
}

resource "cloudflare_zero_trust_access_application" "staging" {
  account_id                 = var.cloudflare_account_id
  name                       = "NutsNews staging"
  type                       = "self_hosted"
  domain                     = "staging.nutsnews.com"
  session_duration           = "8h"
  service_auth_401_redirect  = true
  http_only_cookie_attribute = true
  same_site_cookie_attribute = "strict"
  enable_binding_cookie      = true
  app_launcher_visible       = false
  policies = [
    {
      id         = cloudflare_zero_trust_access_policy.qualifier.id
      precedence = 1
    },
    {
      id         = cloudflare_zero_trust_access_policy.browser.id
      precedence = 2
    },
  ]
}
