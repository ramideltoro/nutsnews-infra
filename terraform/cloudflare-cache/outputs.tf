output "cache_ruleset_id" {
  description = "Managed Cloudflare cache settings entrypoint ruleset id."
  value       = cloudflare_ruleset.nutsnews_public_cache.id
}
