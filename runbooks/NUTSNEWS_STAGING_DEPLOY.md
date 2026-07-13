# NutsNews Staging Deployment

`Deploy Verified NutsNews Staging Candidate` accepts only the
`nutsnews-staging-release` repository-dispatch candidate schema. Its preflight
has no `staging-vps` Environment attachment, deploy secret, or SSH credential:
it validates the exact payload, source build on `ramideltoro/nutsnews` `main`,
and the immutable OCI SLSA provenance first.

The manual dispatch path is rehearsal-only. Enter the exact candidate JSON and
`rehearse-staging-candidate`; it validates the same boundary but cannot attach
staging credentials, run Ansible, or mutate a host.

An approved dispatch uses only the `staging-vps` GitHub Environment and
`ansible/playbooks/deploy-staging.yml` with the `nutsnews-staging-deploy` tag.
It serializes deployments, takes the staging host mutation lock, runs check
mode before apply, waits for `/readyz`, verifies Docker's resolved digest, and
writes a GitHub Deployment audit record. It never calls a production workflow
or targets the production runtime.

See the matching operational guide in `ramideltoro/nutsnews-docs` for the
candidate schema, Environment setup, approval procedure, evidence, and
rollback guidance. A live staging apply remains separately approved work.
