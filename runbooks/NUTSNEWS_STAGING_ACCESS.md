# NutsNews Staging Access Boundary

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

Before any apply, use the canonical onboarding, check/apply sequence, rollback
and live read-only checklist in `ramideltoro/nutsnews-docs`:
`NUTSNEWS_VPS_STAGING_ACCESS_BOUNDARY.md`. Protected provider apply and
Protected Ansible Apply both require separate explicit approval. Do not perform
DNS cutover, host mutation or public verification from this runbook alone.
