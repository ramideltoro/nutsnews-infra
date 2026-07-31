#!/usr/bin/env python3
"""Validate the value-free worker-uplift backup and restore readiness record."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
READINESS_PATH = ROOT / "config" / "worker-uplift-backup-restore-readiness.json"
RUNBOOK_PATH = ROOT / "runbooks" / "WORKER_UPLIFT_BACKUP_RESTORE_READINESS.md"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "infrastructure-checks.yml"

HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
RABBITMQ_IMAGE = re.compile(r"^rabbitmq@sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHORT_SNAPSHOT = re.compile(r"^[0-9a-f]{12}$")
WORKFLOW_URL = re.compile(
    r"^https://github\.com/ramideltoro/nutsnews-backend/actions/runs/(?P<run_id>[0-9]+)$"
)
FORBIDDEN_VALUE_MARKERS = (
    "/etc/",
    "/private/",
    "/users/",
    "/var/",
    "amqp://",
    "postgres://",
    "postgresql://",
    "password=",
    "secret=",
    "token=",
)
FORBIDDEN_KEYS = {
    "account_id",
    "account_identifier",
    "backup_contents",
    "database_url",
    "private_path",
    "record_data",
    "secret_value",
    "token_value",
}
REQUIRED_INVENTORY = {
    "postgresql_primary_shadow_logical_snapshot",
    "rabbitmq_topology_and_sanitized_definitions",
    "rabbitmq_clean_rebuild",
    "rabbitmq_quiesced_volume_restore",
    "rabbitmq_live_message_store_hot_copy",
}
REQUIRED_STAGES = {
    "approval",
    "canonicalization",
    "enrichment",
    "fetch",
    "persistence",
    "publication",
    "translation",
}
REQUIRED_CONTROLS = {
    "definition_export": "healthy",
    "scheduled_check": "healthy",
    "clean_rebuild_drill": "healthy",
    "stopped_volume_restore_drill": "healthy",
    "grafana_recovery_freshness_alert": "managed",
}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def parse_timestamp(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{field} must be an RFC3339 UTC string")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be an RFC3339 UTC string")
        return
    if parsed.utcoffset() is None or value.endswith("Z") is False:
        errors.append(f"{field} must use the Z UTC suffix")


def validate_artifact(artifact: Any, field: str, errors: list[str]) -> None:
    require(isinstance(artifact, dict), f"{field} must be an object", errors)
    if not isinstance(artifact, dict):
        return
    require(isinstance(artifact.get("id"), int) and artifact["id"] > 0, f"{field}.id invalid", errors)
    require(bool(artifact.get("name")), f"{field}.name missing", errors)
    require(bool(SHA256.fullmatch(str(artifact.get("digest", "")))), f"{field}.digest invalid", errors)
    hash_fields = [name for name in artifact if name.endswith("_sha256")]
    require(bool(hash_fields), f"{field} must retain at least one file SHA-256", errors)
    for name in hash_fields:
        require(bool(HEX_64.fullmatch(str(artifact[name]))), f"{field}.{name} invalid", errors)


def validate_workflow_evidence(item: Any, field: str, errors: list[str]) -> None:
    require(isinstance(item, dict), f"{field} must be an object", errors)
    if not isinstance(item, dict):
        return
    run_id = item.get("run_id", item.get("apply_run_id"))
    require(isinstance(run_id, int) and run_id > 0, f"{field} run id invalid", errors)
    match = WORKFLOW_URL.fullmatch(str(item.get("workflow_url", "")))
    require(bool(match), f"{field}.workflow_url invalid", errors)
    if match and isinstance(run_id, int):
        require(int(match.group("run_id")) == run_id, f"{field} run URL/id mismatch", errors)
    require(bool(COMMIT.fullmatch(str(item.get("source_commit", "")))), f"{field}.source_commit invalid", errors)
    validate_artifact(item.get("artifact"), f"{field}.artifact", errors)


def walk_for_hygiene(value: Any, errors: list[str], path: str = "document") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                errors.append(f"{path}.{key} is a forbidden evidence field")
            walk_for_hygiene(child, errors, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_for_hygiene(child, errors, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        for marker in FORBIDDEN_VALUE_MARKERS:
            if marker in lowered:
                errors.append(f"{path} contains forbidden private or sensitive material marker {marker}")


def validate(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    require(document.get("schema_version") == 1, "schema_version must be 1", errors)
    require(document.get("decision_id") == "worker-uplift-61g-backup-restore-readiness", "decision_id mismatch", errors)
    require(
        document.get("tracking_issue") == "https://github.com/ramideltoro/nutsnews-worker/issues/162",
        "tracking_issue mismatch",
        errors,
    )
    require(document.get("implementation_repository") == "ramideltoro/nutsnews-infra", "repository mismatch", errors)
    require(document.get("decision") == "pass", "readiness decision must be pass", errors)
    parse_timestamp(document.get("assessed_at_utc"), "assessed_at_utc", errors)

    owners = document.get("owners", {})
    require(
        set(owners) == {"readiness_and_alerts", "protected_backend_operations", "tracking"},
        "all ownership boundaries must be explicit",
        errors,
    )
    require(all(isinstance(value, str) and value for value in owners.values()), "owner values must be named", errors)

    guardrails = document.get("guardrails", {})
    expected_guardrails = {
        "cutover_performed",
        "dns_or_failover_changed",
        "legacy_ingestion_changed",
        "production_data_path_used_as_restore_target",
        "production_rabbitmq_broker_stopped",
        "production_writes_enabled",
    }
    require(set(guardrails) == expected_guardrails, "guardrail set is incomplete", errors)
    for name in expected_guardrails:
        require(guardrails.get(name) is False, f"guardrail {name} must remain false", errors)

    versions = document.get("versions", {})
    postgres_version = versions.get("postgresql", {})
    rabbitmq_version = versions.get("rabbitmq", {})
    require(postgres_version.get("major") == "18", "PostgreSQL major version must be exact", errors)
    require(bool(COMMIT.fullmatch(str(postgres_version.get("source_commit", "")))), "PostgreSQL source commit invalid", errors)
    require(
        bool(RABBITMQ_IMAGE.fullmatch(str(rabbitmq_version.get("image", "")))),
        "RabbitMQ image digest must be immutable",
        errors,
    )
    require(bool(COMMIT.fullmatch(str(rabbitmq_version.get("source_commit", "")))), "RabbitMQ source commit invalid", errors)

    inventory = document.get("inventory", [])
    require(isinstance(inventory, list), "inventory must be a list", errors)
    inventory_by_id = {
        entry.get("id"): entry for entry in inventory if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    require(set(inventory_by_id) == REQUIRED_INVENTORY, "backup inventory is incomplete or contains unknown entries", errors)
    for inventory_id, entry in inventory_by_id.items():
        require(entry.get("disposition") in {"ready", "retained", "replaced", "explicitly_unsupported"}, f"{inventory_id} disposition invalid", errors)
        for field in ("owner", "strategy", "retention", "rationale"):
            require(isinstance(entry.get(field), str) and bool(entry[field]), f"{inventory_id}.{field} missing", errors)
    require(
        inventory_by_id.get("rabbitmq_live_message_store_hot_copy", {}).get("disposition") == "explicitly_unsupported",
        "live RabbitMQ message-store hot copy must remain explicitly unsupported",
        errors,
    )

    evidence = document.get("evidence", {})
    expected_evidence = {
        "postgresql",
        "rabbitmq_scheduled_check",
        "rabbitmq_stopped_volume_restore",
        "helper_deployment",
    }
    require(set(evidence) == expected_evidence, "evidence set incomplete", errors)
    for field in expected_evidence:
        validate_workflow_evidence(evidence.get(field), f"evidence.{field}", errors)

    postgres = evidence.get("postgresql", {}).get("result", {})
    for field in ("status", "freshness_status", "integrity_status", "critical_query_status", "restore_health_status"):
        require(postgres.get(field) == "pass", f"PostgreSQL {field} must pass", errors)
    require(postgres.get("isolated_target") is True, "PostgreSQL restore target must be isolated", errors)
    require(postgres.get("encrypted_repository") is True, "PostgreSQL snapshot repository must be encrypted", errors)
    require(bool(SHORT_SNAPSHOT.fullmatch(str(postgres.get("snapshot_short_id", "")))), "PostgreSQL snapshot metadata invalid", errors)
    require(isinstance(postgres.get("rpo_seconds"), int) and postgres["rpo_seconds"] >= 0, "PostgreSQL RPO invalid", errors)
    require(isinstance(postgres.get("rto_seconds"), int) and postgres["rto_seconds"] >= 0, "PostgreSQL RTO invalid", errors)
    parse_timestamp(postgres.get("completed_at_utc"), "PostgreSQL completed_at_utc", errors)

    scheduled = evidence.get("rabbitmq_scheduled_check", {}).get("result", {})
    for field in ("status", "definition_export_status", "clean_rebuild_status"):
        require(scheduled.get(field) == "healthy", f"RabbitMQ scheduled {field} must be healthy", errors)
    require(scheduled.get("representative_message_transfer_status") == "pass", "clean rebuild message transfer must pass", errors)
    require(scheduled.get("raw_export_retained") is False, "raw RabbitMQ definition export must not be retained", errors)
    require(scheduled.get("sensitive_fields_redacted") == 16, "RabbitMQ definition redaction count mismatch", errors)
    require(
        scheduled.get("clean_rebuild_duration_seconds", 10**9) <= scheduled.get("clean_rebuild_target_rto_seconds", -1),
        "clean rebuild exceeded RTO",
        errors,
    )

    stopped = evidence.get("rabbitmq_stopped_volume_restore", {}).get("result", {})
    require(stopped.get("status") == "healthy", "stopped-volume restore must be healthy", errors)
    for field in ("source_broker_stopped_before_copy",):
        require(stopped.get(field) is True, f"{field} must be true", errors)
    for field in ("production_message_store_snapshot_created", "running_broker_touched"):
        require(stopped.get(field) is False, f"{field} must be false", errors)
    for field in ("topology_status", "permissions_status", "representative_message_transfer_status"):
        require(stopped.get(field) == "pass", f"stopped-volume {field} must pass", errors)
    require(set(stopped.get("probed_stages", [])) == REQUIRED_STAGES, "stopped-volume stage coverage incomplete", errors)
    require(stopped.get("skipped_stages") == [], "stopped-volume proof must not skip stages", errors)
    require(stopped.get("duration_seconds", 10**9) <= stopped.get("target_rto_seconds", -1), "stopped-volume restore exceeded RTO", errors)

    deployment = evidence.get("helper_deployment", {})
    require(deployment.get("deployment_scope") == "rabbitmq-recovery-helper", "helper deployment scope must remain narrow", errors)
    deployment_result = deployment.get("result", {})
    require(deployment_result.get("status") == "pass", "helper deployment must pass", errors)
    require(deployment_result.get("failed_tasks") == 0, "helper deployment failed tasks must be zero", errors)
    require(deployment_result.get("unreachable_hosts") == 0, "helper deployment unreachable hosts must be zero", errors)
    require(deployment_result.get("fixed_one_shot_executed") is False, "helper deployment must not execute one-shot", errors)
    require(deployment_result.get("full_baseline_executed") is False, "helper deployment must not execute full baseline", errors)

    require(document.get("control_status") == REQUIRED_CONTROLS, "required recovery controls must be healthy and managed", errors)
    require("not_configured" not in json.dumps(document.get("control_status")), "recovery control remains not_configured", errors)

    topology = document.get("topology", {})
    require(topology.get("expected", {}).get("routes") == 7, "expected topology route count mismatch", errors)
    require(topology.get("expected", {}).get("queues") == 36, "expected topology queue count mismatch", errors)
    require(topology.get("sanitized_export", {}).get("bindings") == 36, "exported binding count mismatch", errors)

    hygiene = document.get("evidence_hygiene", {})
    require(hygiene and all(value is True for value in hygiene.values()), "evidence hygiene flags must all pass", errors)
    walk_for_hygiene(document, errors)

    return errors


def main() -> int:
    try:
        document = json.loads(READINESS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load {READINESS_PATH}: {exc}", file=sys.stderr)
        return 1

    errors = validate(document)

    runbook = RUNBOOK_PATH.read_text(encoding="utf-8") if RUNBOOK_PATH.exists() else ""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8") if WORKFLOW_PATH.exists() else ""
    for token in (
        "Read-only checks",
        "Isolated drills",
        "Protected mutations",
        "does not authorize cutover",
        "Backend PostgreSQL Primary Shadow Restore",
        "Backend RabbitMQ Recovery",
    ):
        require(token in runbook, f"runbook missing required token: {token}", errors)
    for token in (
        "validate_worker_uplift_backup_restore_readiness.py",
        "tests.test_worker_uplift_backup_restore_readiness",
    ):
        require(token in workflow, f"infrastructure checks missing required token: {token}", errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Worker-uplift backup and isolated restore readiness evidence is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
