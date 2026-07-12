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
only the affected variables; response bodies and values are never printed.

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
