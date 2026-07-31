# Worker-Uplift Backup And Isolated Restore Readiness

This runbook owns the infrastructure-facing readiness record for
`ramideltoro/nutsnews-worker#162`. The machine-readable record is
`config/worker-uplift-backup-restore-readiness.json`.

This evidence does not authorize cutover, production writes, a DNS or failover
change, a legacy-ingestion change, or a restore over production. Legacy
ingestion remains the production owner. A real incident restore requires
separate authorization and the applicable protected workflow.

## Recovery strategy

PostgreSQL uses an encrypted Restic logical snapshot and an isolated database
restore with integrity and critical-query validation.

RabbitMQ uses the pinned image, source-controlled topology, protected
credentials, and PostgreSQL outbox reconciliation as the preferred clean
rebuild strategy. Sanitized definition exports retain topology metadata without
credential values. A stopped-volume restore is supported only for a deliberately
quiesced snapshot using the matching node identity and Erlang cookie.

A hot copy of the running RabbitMQ message store is explicitly unsupported by
the backend recovery policy because it can be inconsistent. Normal backups do
not retain it. This is not a waiver: the tested clean-rebuild path and
authoritative PostgreSQL reconciliation replace that method.

## Read-only checks

The following actions inspect retained status and artifacts without changing a
runtime. The workflow name is `Backend RabbitMQ Recovery`:

```bash
gh workflow run backend-rabbitmq-recovery.yml \
  -R ramideltoro/nutsnews-backend \
  --ref main \
  -f action=status
```

After the run finishes, download `backend-rabbitmq-recovery-report` and inspect
the JSON. A GitHub Actions conclusion alone is insufficient. Confirm that
definition export, scheduled check, clean rebuild, and stopped-volume restore
all report `healthy`, then verify the artifact digest and each retained file
SHA-256.

The Grafana-owned `NutsNews worker-uplift RabbitMQ recovery proof stale` rule
uses the backend definition-export age metric. No-data alerts, and a proof older
than its threshold alerts. Do not modify the rule or suppress it during routine
evidence collection.

## Isolated drills

These protected workflows perform mutations only inside disposable recovery
targets. They do not authorize or perform a production restore.

PostgreSQL:

The workflow name is `Backend PostgreSQL Primary Shadow Restore`.

```bash
gh workflow run backend-postgres-primary-shadow-restore.yml \
  -R ramideltoro/nutsnews-backend \
  --ref main
```

Inspect `backend-postgres-backup-restore-proof.json` and require:

- encrypted Restic snapshot metadata;
- an isolated restore scope;
- freshness, integrity, critical-query, and restore-health status `pass`;
- measured RPO and RTO;
- safe metadata only.

RabbitMQ scheduled definition export and clean rebuild:

```bash
gh workflow run backend-rabbitmq-recovery.yml \
  -R ramideltoro/nutsnews-backend \
  --ref main \
  -f action=scheduled-check \
  -f confirm_target=backend.nutsnews.com
```

RabbitMQ stopped-volume restore:

```bash
gh workflow run backend-rabbitmq-recovery.yml \
  -R ramideltoro/nutsnews-backend \
  --ref main \
  -f action=stopped-volume-restore-drill \
  -f confirm_target=backend.nutsnews.com
```

For both RabbitMQ drills, inspect the report and status artifacts. Require the
topology and permissions checks to pass. Require `topology probe-transfers` to
cover fetch, canonicalization, enrichment, approval, translation, persistence,
and publication with no skipped stage. The stopped-volume report must also prove
that the disposable source broker was stopped before copying, the running broker
was untouched, and no production message-store snapshot was created.

## Protected mutations

Deploying a changed recovery helper is a protected host mutation. It is not part
of a routine evidence refresh. Use `Protected Backend Ansible Apply` with the
`rabbitmq-recovery-helper` deployment scope, run check mode first, inspect the
artifact, then run the exact apply only if check mode is clean. The scope must
not run the fixed one-shot or the full baseline.

Do not use direct host commands to replace protected automation. Do not approve
an unrelated run. An exact workflow run dispatched for an authorized issue can
be approved through the GitHub API and must be monitored through artifact
validation.

## Recovery and rollback boundaries

For PostgreSQL, a readiness drill stops after isolated validation. It must not
promote the restored database, change the writer, or alter replication.

For RabbitMQ, prefer clean rebuild. Recreate the pinned broker and declarative
topology, provision protected credentials, validate transfers, then reconcile
the PostgreSQL outbox before publishers resume. A stopped-volume restore requires
a known quiesced snapshot and matching identity. Never copy a running message
store.

If a helper deployment fails, revert its backend PR and use the same narrow
protected deployment scope. If a drill fails, leave production untouched,
retain the failed value-free artifact, and treat the corresponding readiness
control as unhealthy until a fresh isolated drill passes.

## Validation

```bash
python3 scripts/validate_worker_uplift_backup_restore_readiness.py
python3 -m unittest tests.test_worker_uplift_backup_restore_readiness
```

The record deliberately excludes credential values, cloud account identifiers,
host-private paths, record data, and backup contents.
