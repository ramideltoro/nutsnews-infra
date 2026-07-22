# Vercel-to-VPS environment synchronization

The protected `Protected Ansible Apply` workflow can perform a one-way sync from
Vercel Production to the VPS production app. The workflow defaults to this sync
operation, but it still defaults to Ansible check mode. VPS-only values remain in
the existing protected `NUTSNEWS_APP_ENVS_JSON` input and are not removed by the
sync.

The reviewed allowlist and classification rules live in
`config/vercel-vps-env-sync.json`. The sync fails closed when a Vercel Production
variable is not classified, is marked for manual review, or cannot be decrypted.
Vercel system variables, deployment metadata, and preview/development values are
excluded. The sync reports only variable names and SHA-256 fingerprints; values
are never printed.

The current Production inventory has been reviewed against the web runtime. The
admin dashboards require the server-only `SUPABASE_SERVICE_ROLE_KEY`; the
mapping also exposes the same reviewed Supabase URL as `SUPABASE_URL` for
server-side consumers while retaining `NEXT_PUBLIC_SUPABASE_URL` for browser
code. `ACTIONS_READ_TOKEN` is synchronized because the production-readiness
dashboard can use it for GitHub Actions status. The Auth.js Google OAuth and
session secrets are synchronized as server-only values because Auth.js reads
the `AUTH_*` convention internally.

The read-only failover visibility dashboard also depends on this sync. The
VPS runtime receives `NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL` and the
server-only `NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET`, plus optional runbook and
Cloudflare dashboard links. Manual failover action URLs and
`NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET` are intentionally manual-review only;
their presence stops the sync until an operator deliberately decides to make
DNS-changing controls operable from the VPS admin UI.

If the live controller status secret is managed outside Vercel, keep the same
`NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET` value in the scoped `cloudflare-admin`
and `production-vps` GitHub Environment secrets. Run
`cloudflare-failover-status-secret-apply.yml` from `main` to store the Worker
secret on `nutsnews-controller`, then run Protected Ansible Apply with
`sync_vercel_production=true` to materialize the read-only status URL and HMAC
secret into the VPS app environment. This path does not configure
`NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET`, so manual dashboard controls remain
disabled.

Use the companion operating guide in `ramideltoro/nutsnews-docs` for credential
setup, classification policy, dry-run/apply commands, rollback, rotation, and
removal procedures.

## Protected workflow inputs

- `sync_vercel_production=true` selects Vercel Production and the VPS production
  env file. Do not use preview or development values.
- `run_mode=check` performs the read-only Ansible preview and is the required
  first step.
- `run_mode=apply` is production-changing and requires the existing
  `production-vps` Environment approval plus the exact confirmation string.

An enabled production render fails before materialization unless the merged
map contains the complete runtime-safety and public Supabase contract. This
guard also applies when `sync_vercel_production=false`, so that option cannot
silently replace a working production environment with an incomplete map. The
managed Compose health check uses `/readyz`; `/healthz` remains a liveness and
immutable-identity endpoint, not proof that runtime policy and data access are
usable.

The workflow is serialized with the existing production VPS concurrency group.
It reads the VPS env file only through a read-only SSH command that emits names
and hashes. Ansible remains the only path that writes the VPS file.

## Vercel retrieval and validation

The sync first retrieves Vercel Production metadata from
`GET /v10/projects/{idOrName}/env` with the protected team identifier. It then
retrieves each selected variable by ID through Vercel's documented
`GET /v1/projects/{idOrName}/env/{id}` endpoint. The older `decrypt=true` query
parameter on the list endpoint is deprecated and must not be treated as proof
that `value` is plaintext.

The per-variable response must identify a decrypted value for encrypted,
secret, or sensitive variables. The sync rejects missing values, undecrypted
metadata, structured encrypted envelopes, newlines, and invalid runtime shapes
before writing the private temporary selection file. Auth.js values are checked
semantically: `AUTH_GOOGLE_ID` must match a Google Web client ID, `AUTH_GOOGLE_SECRET`
must be nonempty, `AUTH_SECRET` must be at least 32 characters, and
`ADMIN_EMAILS` must be a comma-separated list of email addresses. Failures name
only the affected variables; response bodies and values are never printed. The
failover controller status URL must be the HTTPS
`nutsnews-controller.nutsnews.workers.dev/status` endpoint, and the status HMAC
secret must be present and look like usable plaintext whenever either failover
status variable is selected.

If Vercel returns HTTP 403 while retrieving a selected secret, the protected
token does not have access to decrypt that project variable. Create or rotate
the Vercel Access Token in the Vercel account/team token settings with access
to the owning team and project environment variables. Store only the token in
the `production-vps` GitHub Environment secret
`NUTSNEWS_VERCEL_TOKEN`; keep the project and team identifiers in
`NUTSNEWS_VERCEL_PROJECT_ID` and `NUTSNEWS_VERCEL_TEAM_ID` in the same protected
environment. The workflow must fail closed until the per-variable endpoint
returns usable values.

## Failure recovery and rollback

If the sync reports an undecrypted or semantically invalid variable, do not run
Ansible apply and do not edit `/etc/nutsnews/nutsnews-app.env` over SSH. Fix the
Vercel token access or the Vercel Production variable, then rerun check mode.
If a bad value was already applied, restore the last known-good value in Vercel
Production through the dashboard or secure stdin-based CLI/API flow, rerun
check mode, and use the protected apply only after the name-only diff is
reviewed. Verify the VPS over read-only SSH and confirm the application health
endpoint. The GitOps rollback source is Vercel, not a hand-edited VPS env file.

Official references:

- [Vercel REST API overview](https://vercel.com/docs/rest-api)
- [Vercel project environment variables API](https://vercel.com/docs/rest-api/projects/retrieve-the-environment-variables-of-a-project-by-id-or-name)
- [Vercel environment-variable management](https://vercel.com/docs/environment-variables/manage-across-environments)
