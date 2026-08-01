# Cloudflare cache settings

This isolated stack owns the existing `http_request_cache_settings` zone entrypoint for `nutsnews.com`. The import block adopts ruleset `865f91ce8f6d4ddf84c66401137a3a28`; do not create a second zone entrypoint.

Use the protected `Cloudflare Cache Rules Apply` workflow. Run `cache_policy_mode=baseline` first to import and verify the active two-rule ruleset without intended drift. After that plan is clean, use `cache_policy_mode=coordinated` to review or apply the long-lived policy; apply is gated by an exact confirmation. Cache keys retain only functional parameters, Next.js router variants bypass edge storage, optimized-image format negotiation includes `Accept`, and protected/mutating routes remain uncacheable.

Rollback: revert the reviewed rule change and run a protected apply. The previous two-rule production baseline is recorded in `active-ruleset-baseline.json` for comparison; it is not an apply target.
