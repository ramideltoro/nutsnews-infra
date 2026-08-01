# Cloudflare cache settings

This isolated stack owns the existing `http_request_cache_settings` zone entrypoint for `nutsnews.com`. The import block adopts ruleset `865f91ce8f6d4ddf84c66401137a3a28`; do not create a second zone entrypoint.

Use the protected `Cloudflare Cache Rules Apply` workflow. Run `cache_policy_mode=baseline` first to import and verify the active two-rule ruleset without intended drift. After that plan is clean, use `cache_policy_mode=coordinated` to review or apply the long-lived policy; apply is gated by an exact confirmation. The zone's current plan supports ignore-all query keys but not selective custom keys, so functional API and optimized-image routes use Cloudflare's default full-query key while query normalization and cardinality bounds remain enforced by the application. Next.js router variants bypass edge storage, optimized-image format negotiation uses the all-plan `Vary` setting to normalize `Accept` to AVIF and WebP, and protected/mutating routes remain uncacheable.

The stack also discovers existing zone URL-rewrite entrypoints before tracking-query normalization is introduced. Review `request_transform_entrypoints` from a protected plan; never create a second `http_request_transform` zone entrypoint or overwrite unmanaged rewrite rules.

Rollback: revert the reviewed rule change and run a protected apply. The previous two-rule production baseline is recorded in `active-ruleset-baseline.json` for comparison; it is not an apply target.
