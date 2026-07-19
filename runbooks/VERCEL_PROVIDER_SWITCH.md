# Protected Vercel Provider Switch

Use this only for the NutsNews backend PostgreSQL primary cutover or rollback.
It updates non-secret Vercel Production switch variables and then dispatches the
existing app Vercel production release so the new environment is deployed.

## Backend PostgreSQL Primary

Prerequisites:

- backend #119 has current DB gate evidence;
- app and worker production writes are paused;
- Supabase no-new-write watermarks are attached to #119;
- rollback owner coverage is recorded for the rollback window.

Dispatch after the PR containing the workflow has merged:

```bash
gh workflow run protected-vercel-provider-switch.yml \
  --repo ramideltoro/nutsnews-infra \
  --ref main \
  -f operation=apply \
  -f database_provider_mode=backend_postgres_primary \
  -f production_writes_paused=false \
  -f backend_api_url=https://backend.nutsnews.com/api/app/db \
  -f provider_switch_confirmation=enable-backend-postgres-primary \
  -f source_commit=<app-source-sha> \
  -f image_digest=<sha256:image-digest> \
  -f build_id=<run-attempt> \
  -f vps_apply_run_id=<protected-vps-apply-run-id> \
  -f staging_deployment_id=<staging-deployment-id> \
  -f qualification_run_id=<staging-qualification-run-id> \
  -f dispatch_vercel_release=true
```

The job is behind the `production-vps` GitHub environment. The artifact contains
safe metadata only and never prints Vercel values or secrets.

## Rollback To Supabase Primary

Rollback before the forward-recovery boundary uses the same workflow:

```bash
gh workflow run protected-vercel-provider-switch.yml \
  --repo ramideltoro/nutsnews-infra \
  --ref main \
  -f operation=apply \
  -f database_provider_mode=supabase_primary \
  -f production_writes_paused=true \
  -f backend_api_url=https://backend.nutsnews.com/api/app/db \
  -f provider_switch_confirmation=deploy-supabase-primary \
  -f source_commit=<app-source-sha> \
  -f image_digest=<sha256:image-digest> \
  -f build_id=<run-attempt> \
  -f vps_apply_run_id=<protected-vps-apply-run-id> \
  -f staging_deployment_id=<staging-deployment-id> \
  -f qualification_run_id=<staging-qualification-run-id> \
  -f dispatch_vercel_release=true
```

Do not use this workflow for secret rotation. It manages only:

- `NUTSNEWS_DATABASE_PROVIDER_MODE`;
- `NUTSNEWS_PRODUCTION_WRITES_PAUSED`;
- `NUTSNEWS_BACKEND_API_URL`.
