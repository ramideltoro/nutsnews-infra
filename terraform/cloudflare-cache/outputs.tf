output "cache_ruleset_id" {
  description = "Managed Cloudflare cache settings entrypoint ruleset id."
  value       = cloudflare_ruleset.nutsnews_public_cache.id
}

output "request_transform_entrypoints" {
  description = "Existing zone URL-rewrite entrypoints, inspected before managing tracking-query normalization."
  value       = local.request_transform_entrypoints
}
