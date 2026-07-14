# NutsNews Staging Access Boundary

The staging Caddy route sends a cloned request to the local Access JWT verifier.
Its verifier URI is `/verify?`: the explicit empty query prevents OAuth callback
codes and other application query parameters from reaching the verifier while
Caddy preserves the original request for the staging application after access is
granted. Staging access logs omit request URIs, Cloudflare Access JWT and
service-token headers, cookies, and redirect locations so OAuth codes, CSRF
state, Access tokens, and response navigation material cannot be retained in
Docker logs. These protections are scoped to `staging.nutsnews.com`; production
and operations virtual hosts retain their existing routing and logging behavior.

The reviewed staging boundary is opt-in and is not deployed by merging its PR.
It adds `staging.nutsnews.com` behind Cloudflare Access, validates the signed
Access JWT again at the VPS, and routes only to `nutsnews-app-staging` on
`nutsnews-edge-staging`. With the option disabled, the rendered Caddyfile is
byte-for-byte the existing production configuration.

Use three separate trust boundaries:

- `staging-vps`: forced staging deploy key, known hosts and staging app env JSON;
- `staging-tests`: Access client ID/secret and future test-user material only;
- `cloudflare-admin`: fixed staging DNS/Access OpenTofu inputs only.

Never put deploy SSH or app runtime secrets in `staging-tests`, provider-admin
credentials in either staging environment, or test users in `staging-vps`.

The browser authorization cookie uses `SameSite=Lax`, the narrowest Cloudflare
Access setting compatible with the top-level cross-site return from the
staging application's Google OAuth flow. `SameSite=Strict` is not permitted:
Cloudflare documents that it can cause `ERR_TOO_MANY_REDIRECTS`. HttpOnly and
the binding cookie remain enabled, and the application and VPS verifier still
enforce the existing Access identity and audience boundaries.

Before any apply, use the canonical onboarding, check/apply sequence, rollback
and live read-only checklist in `ramideltoro/nutsnews-docs`:
`NUTSNEWS_VPS_STAGING_ACCESS_BOUNDARY.md`. Protected provider apply and
Protected Ansible Apply both require separate explicit approval. Do not perform
DNS cutover, host mutation or public verification from this runbook alone.
