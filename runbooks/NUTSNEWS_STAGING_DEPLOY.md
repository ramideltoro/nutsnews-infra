# NutsNews Staging Deployment

`Deploy Verified NutsNews Staging Candidate` accepts only the
`nutsnews-staging-release` repository-dispatch candidate schema. Its preflight
has no `staging-vps` Environment attachment, deploy secret, or SSH credential:
it validates the exact payload, source build on `ramideltoro/nutsnews` `main`,
and the immutable OCI SLSA provenance first.

For source reachability, GitHub's fixed compare request is
`<candidate-commit>...main`; only `ahead` (the current `main` contains that
candidate) or `identical` is trusted. Any other relationship fails before the
`staging-vps` Environment can expose deployment credentials.

The manual dispatch path is rehearsal-only. Enter the exact candidate JSON and
`rehearse-staging-candidate`; it validates the same boundary but cannot attach
staging credentials, run Ansible, or mutate a host.

An approved dispatch uses only the `staging-vps` GitHub Environment and
`ansible/playbooks/deploy-staging.yml` with the `nutsnews-staging-deploy` tag.
The protected deploy job requires `NUTSNEWS_STAGING_AUTH_GOOGLE_ID` and
`NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET` together. It overlays them in memory onto
the existing write-only `NUTSNEWS_STAGING_APP_ENVS_JSON` bundle and forces
`AUTH_URL=https://staging.nutsnews.com` plus
`NUTSNEWS_OAUTH_CREDENTIALS_ENV=staging`; neither credential is logged or attached
to preflight, rehearsal, summaries, deployment records, or artifacts.
It serializes deployments, takes the staging host mutation lock, runs check
mode before apply, waits for `/readyz`, verifies Docker's resolved digest, and
writes a GitHub Deployment audit record. It never calls a production workflow
or targets the production runtime.

If the server-side fixed command rejects the apply, the workflow reports only
the sanitized gateway code, reviewed Ansible task label, diagnostic class, and
controller version. It must not print Ansible output, rendered diffs, request
JSON, environment values, or secrets. Fix the underlying reviewed automation or
host bundle through GitOps and rerun the same immutable candidate. When an
Ansible `always` cleanup task runs after a failure, the reported task label is
the reviewed task that emitted the failure, not the cleanup task that happened
to run last.

The server-side staging verifier still inspects production container identity,
network separation, and root-only production env-file permissions as part of
the isolation boundary. Current production container health is recorded in the
sanitized runtime result, but it is not a required staging deploy gate; an
already-unhealthy production app must be recovered through the protected
production eligibility and apply path after staging has independently qualified.

See the matching operational guide in `ramideltoro/nutsnews-docs` for the
candidate schema, Environment setup, approval procedure, evidence, and
rollback guidance. A live staging apply remains separately approved work.
