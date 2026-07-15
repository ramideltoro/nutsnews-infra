# NutsNews Staging Qualification

`Qualify Verified NutsNews Staging Candidate` runs only after
`Deploy Verified NutsNews Staging Candidate` completes successfully, or from a
manual dispatch that resolves an existing successful staging deploy run.

The qualifier runs on GitHub-hosted `ubuntu-latest` with only the
`staging-tests` Environment attached. It does not attach production deployment
authority, production app secrets, deploy SSH, or release-promotion credentials.
Before and after the app qualification suite, it reads the GitHub staging
Deployment record and the live `https://staging.nutsnews.com/healthz` and
`/readyz` identity through Cloudflare Access service-token material.

If `ramideltoro/nutsnews` is private, `staging-tests` must provide
`NUTSNEWS_STAGING_TESTS_SOURCE_TOKEN` with read-only source checkout access.
For public checkout, the workflow falls back to the default token.

On full success only, the workflow writes `staging-qualification.json`, attests
it with GitHub OIDC-backed artifact attestations, and verifies the created
attestation with `gh attestation verify`. The predicate is short lived: 24
hours, and invalidated earlier by a staging redeploy or relevant infra/config or
test-suite revision.

The retained evidence artifact is named with the staging deployment ID, workflow
run ID, and attempt so reruns create separate immutable history. Evidence must
remain sanitized: no cookies, CSRF tokens, Access tokens, test-user credentials,
or full sensitive response bodies.

See the matching operator guide in `ramideltoro/nutsnews-docs` for the
Simple/Intermediate/Expert flow, Mermaid diagram, verification command, and
failure-mode checklist.
