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
`NUTSNEWS_OAUTH_CREDENTIALS_ENV=staging`; neither value is logged or attached
to preflight, rehearsal, summaries, deployment records, or artifacts.
It serializes deployments, takes the staging host mutation lock, runs check
mode before apply, waits for `/readyz`, verifies Docker's resolved digest, and
writes a GitHub Deployment audit record. It never calls a production workflow
or targets the production runtime.

See the matching operational guide in `ramideltoro/nutsnews-docs` for the
candidate schema, Environment setup, approval procedure, evidence, and
rollback guidance. A live staging apply remains separately approved work.
