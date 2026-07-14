# Staging Access

This OpenTofu root owns only the proxied `staging.nutsnews.com` DNS record and
its Cloudflare Access application/policies. It does not own or import any
production DNS record, production Access application, or application secret.

The qualifier service token must be created separately because Cloudflare
reveals its client secret only once. Supply only its non-secret provider ID to
OpenTofu; store the client ID and client secret only in the `staging-tests`
GitHub Environment. Use a partial S3 backend and protected inputs. Never commit
state, backend coordinates, account/zone identifiers, IP addresses, email
addresses, API tokens, or service-token credentials.

Offline validation:

```bash
tofu fmt -check -recursive terraform/staging-access
tofu -chdir=terraform/staging-access init -backend=false -input=false
tofu -chdir=terraform/staging-access validate -no-color
```
