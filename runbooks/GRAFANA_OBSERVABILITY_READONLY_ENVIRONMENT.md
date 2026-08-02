# Grafana Observability Read-Only Environment

The daily `Grafana Cloud Synthetic Inventory Audit` uses the dedicated
`grafana-observability-readonly` GitHub Environment. It must not attach the
reviewer-gated `production-vps` Environment: scheduled workflows cannot satisfy
a manual reviewer gate and the audit does not need any deployment authority.

## Environment Protection

In **Settings -> Environments -> grafana-observability-readonly**:

1. Under **Deployment branches and tags**, choose **Selected branches and
   tags** and allow only the exact `main` branch. Do not allow tags, every
   branch, or a wildcard.
2. Leave **Required reviewers** empty. This environment is intentionally able
   to run on schedule without manual approval.
3. Keep the workflow's exact repository and exact `refs/heads/main` job guard.
   The job guard and Environment branch policy are independent controls.

## Read-Only Inputs

Create these Environment variables (they are configuration, not credentials):

- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_URL`: the exact regional Synthetic
  Monitoring API origin.
- `NUTSNEWS_GRAFANA_SYNTHETIC_EXPECTED_INVENTORY_JSON`: the sanitized output
  identity containing exactly `synthetic_check_ids` and
  `synthetic_probe_selection`. Refresh it after an approved Grafana apply if a
  check or probe ID changes. The value contains no target URLs or credentials.

Use this shape for the expected inventory variable:

```json
{
  "synthetic_check_ids": {
    "canonical_articles_api": 1,
    "canonical_homepage": 2,
    "canonical_readiness": 3,
    "vercel_secondary_readiness": 4,
    "vps_readiness": 5
  },
  "synthetic_probe_selection": {
    "public-probe-a": {"id": 11, "public": true},
    "public-probe-b": {"id": 22, "public": true}
  }
}
```

Replace the example IDs with the corresponding sanitized OpenTofu outputs from
the last approved apply. Store these Environment secrets:

- `NUTSNEWS_GRAFANA_SYNTHETIC_MONITORING_READONLY_ACCESS_TOKEN`: a dedicated,
  least-privilege Synthetic Monitoring identity that can read checks and probes only.
  Grant reader roles only; do not grant checks, probes, alerts,
  thresholds, access-token, or secure-value writer roles.
- `NUTSNEWS_GRAFANA_SYNTHETIC_HTTP_CHECKS_JSON`: the five approved targets and
  assertions. It is protected because it contains target configuration, but it
  contains no credential and grants no mutation capability.

Do not copy deployment or mutation secrets into this Environment. In
particular, do not add the OpenTofu backend configuration, Grafana service
account token, telemetry-write access policy token, the write-capable Synthetic
Monitoring token used by plan/apply and drills, operations email, Cloudflare
tokens, SSH keys, or backend drill tokens. If Grafana cannot issue an
enforceably read-only identity for this API, leave the scheduled audit blocked
instead of reusing a writer token.

The workflow grants only `contents: read`, checks out without persisted Git
credentials, issues only Synthetic Monitoring `GET` requests, and uploads a
sanitized audit artifact. Changes to checks remain confined to the manual,
reviewer-gated production workflows.
