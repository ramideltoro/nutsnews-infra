# Grafana State Bootstrap: Cloudflare R2

This bootstrap module creates the private Cloudflare R2 bucket used by the `terraform/grafana-cloud` partial `s3` backend.

The repo did not previously have remote Terraform/OpenTofu state. This module is intentionally separate from the Grafana Cloud module because the state bucket must exist before the Grafana Cloud module can initialize its remote backend.

## State

This bootstrap is a one-time prerequisite. It runs with local state through the protected `Grafana State Bootstrap` workflow and should only be used to create the bucket. Do not commit local bootstrap state, backend config, tfvars, Cloudflare account IDs, R2 endpoints, access keys, or secrets.

After the bucket exists, create a bucket-scoped R2 S3 API token and store the rendered backend config in the `production-vps` GitHub Environment secret `NUTSNEWS_GRAFANA_CLOUD_TOFU_BACKEND_CONFIG`.

## Required Inputs

Supply through protected GitHub environment secrets:

- `TF_VAR_cloudflare_account_id`
- `TF_VAR_cloudflare_api_token`

The Cloudflare API token is for bucket creation and should have account-level R2 bucket management permission. It is separate from the R2 S3 API token used by OpenTofu's backend.

## Free-Tier Assumption

Cloudflare R2 currently includes a free monthly allowance for Standard storage and operations. Terraform state is expected to be tiny, but R2 can bill above the included usage. Check Cloudflare R2 pricing before enabling or reusing the account for additional storage.

## Local Validation

```bash
tofu fmt -recursive terraform/grafana-state-bootstrap/cloudflare-r2
tofu -chdir=terraform/grafana-state-bootstrap/cloudflare-r2 init -backend=false -input=false
tofu -chdir=terraform/grafana-state-bootstrap/cloudflare-r2 validate -no-color
```
