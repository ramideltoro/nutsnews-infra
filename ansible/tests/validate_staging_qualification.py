#!/usr/bin/env python3
"""Regression coverage for the off-VPS staging qualification boundary."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT = ROOT / "scripts/staging_qualification.py"
WORKFLOW = REPO / ".github/workflows/nutsnews-staging-qualification.yml"
DEPLOY_AUDIT = ROOT / "scripts/staging_deployment_audit.py"

spec = importlib.util.spec_from_file_location("staging_qualification", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


COMMIT = "a" * 40
INFRA_COMMIT = "b" * 40
DIGEST = f"sha256:{'c' * 64}"
DEPLOYMENT_ID = "stg-" + "d" * 24
CONFIG_GENERATION = f"staging-{DEPLOYMENT_ID}-eeeeeeeeeeee"
RUN_ID = "123456789"
SOURCE_RUN_ID = "987654321"
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def deployment_payload() -> dict[str, object]:
    return {
        "id": 42,
        "url": "https://api.github.com/repos/ramideltoro/nutsnews-infra/deployments/42",
        "sha": INFRA_COMMIT,
        "environment": "staging",
        "production_environment": False,
        "transient_environment": True,
        "task": "nutsnews-staging-deploy",
        "payload": {
            "schema_version": "20260715000100",
            "migration_head": "20260713000000",
            "supabase_project_ref": "mpqfulvvagyzqneiaqky",
            "source_repository": "ramideltoro/nutsnews",
            "source_commit": COMMIT,
            "image_repository": "ghcr.io/ramideltoro/nutsnews",
            "requested_digest": DIGEST,
            "build_id": f"{SOURCE_RUN_ID}-1",
            "source_workflow_run_id": SOURCE_RUN_ID,
            "infra_commit": INFRA_COMMIT,
            "config_generation": CONFIG_GENERATION,
            "deployment_id": DEPLOYMENT_ID,
            "target_hostname": "staging.nutsnews.com",
            "github_run_id": RUN_ID,
        },
    }


def status_payload(state: str = "success", description: str | None = None) -> dict[str, object]:
    return {
        "state": state,
        "description": description or f"{DEPLOYMENT_ID} status=success actual={DIGEST}",
        "log_url": f"https://github.com/ramideltoro/nutsnews-infra/actions/runs/{RUN_ID}",
        "url": "https://api.github.com/repos/ramideltoro/nutsnews-infra/deployments/42/statuses/1",
        "created_at": "2026-07-15T11:59:00Z",
    }


def fetch_json(url: str) -> object:
    if url.endswith("/deployments?environment=staging&per_page=100"):
        return [deployment_payload()]
    if url.endswith("/deployments/42/statuses"):
        return [status_payload()]
    raise AssertionError(f"unexpected URL {url}")


def evidence() -> module.DeploymentEvidence:
    return module.fetch_deployment_evidence(RUN_ID, DEPLOYMENT_ID, fetch_json)


def identity(override: dict[str, str] | None = None) -> module.RuntimeIdentity:
    values = {
        "checked_at": "2026-07-15T12:00:00Z",
        "target_hostname": "staging.nutsnews.com",
        "health_status": 200,
        "ready_status": 200,
        "source_commit": COMMIT,
        "build_id": f"{SOURCE_RUN_ID}-1",
        "image_digest": DIGEST,
        "runtime_environment": "staging",
        "deployment_target": "vps-staging",
        "config_generation": CONFIG_GENERATION,
        "ready_code": "ready",
    }
    values.update(override or {})
    return module.RuntimeIdentity(**values)


def app_report(overrides: dict[str, object] | None = None) -> dict[str, object]:
    report: dict[str, object] = {
        "schemaVersion": 1,
        "suiteRevision": COMMIT,
        "target": "https://staging.nutsnews.com/",
        "stagingDeploymentId": DEPLOYMENT_ID,
        "result": "pass",
        "results": [
            {"name": "cloudflare-access-and-runtime-identity", "required": True, "status": "pass"},
            {"name": "github-staging-deployment-identity", "required": True, "status": "pass"},
            {"name": "existing-deployment-smoke", "required": True, "status": "pass"},
            {"name": "isolated-staging-synthetic-write", "required": True, "status": "pass"},
            {"name": "bounded-http-auth-contact-security", "required": True, "status": "pass"},
            {"name": "bounded-chromium-accessibility", "required": True, "status": "pass"},
            {"name": "unconditional-fixture-cleanup", "required": True, "status": "pass"},
        ],
    }
    report.update(overrides or {})
    return report


def qualifier(overrides: dict[str, str] | None = None) -> dict[str, str]:
    values = {
        "commit": INFRA_COMMIT,
        "ref": "refs/heads/main",
        "workflow_ref": (
            "ramideltoro/nutsnews-infra/.github/workflows/"
            "nutsnews-staging-qualification.yml@refs/heads/main"
        ),
        "run_id": "111222333",
        "run_attempt": "1",
    }
    values.update(overrides or {})
    return values


def record(
    *,
    post_identity: module.RuntimeIdentity | None = None,
    report: dict[str, object] | None = None,
    qualifier_overrides: dict[str, str] | None = None,
    completed_at: str = "2026-07-15T12:10:00Z",
) -> dict[str, object]:
    return module.build_record(
        evidence(),
        identity(),
        post_identity or identity({"checked_at": "2026-07-15T12:09:00Z"}),
        report or app_report(),
        qualifier(qualifier_overrides),
        "2026-07-15T12:00:00Z",
        completed_at,
    )


def expect_reject(label: str, operation) -> None:
    try:
        operation()
    except module.QualificationError:
        return
    raise AssertionError(f"{label} must be rejected")


def require_pinned_action(workflow: str, action: str, label: str) -> None:
    pattern = rf"uses:\s+{re.escape(action)}@[0-9a-fA-F]{{40}}"
    assert re.search(pattern, workflow), (
        f"{label} must use {action} pinned to a full commit SHA; "
        "the generic workflow action pin validator rejects mutable refs."
    )


resolved = evidence()
assert resolved.image_repository == "ghcr.io/ramideltoro/nutsnews"
assert resolved.image_digest == DIGEST
assert resolved.staging_deployment_id == DEPLOYMENT_ID
assert resolved.migration_head == "20260713000000"
assert resolved.supabase_project_ref == "mpqfulvvagyzqneiaqky"

passing_record = record()
assert passing_record["source"]["migration_head"] == "20260713000000"
assert passing_record["source"]["schema_version"] == "20260715000100"
assert passing_record["source"]["supabase_project_ref"] == "mpqfulvvagyzqneiaqky"
module.validate_record(
    passing_record,
    now=NOW,
    expected_image_digest=DIGEST,
    expected_staging_deployment_id=DEPLOYMENT_ID,
)

verified_attestation = {
    "verificationResult": {
        "statement": {
            "subject": [
                {
                    "name": "ghcr.io/ramideltoro/nutsnews",
                    "digest": {"sha256": DIGEST.removeprefix("sha256:")},
                }
            ],
            "predicate": passing_record,
        }
    }
}
module.validate_record(passing_record, now=NOW, verified_attestation=verified_attestation)

expect_reject(
    "changed digest",
    lambda: module.validate_record(passing_record, now=NOW, expected_image_digest=f"sha256:{'f' * 64}"),
)
wrong_issuer_record = json.loads(json.dumps(passing_record))
wrong_issuer_record["qualifier"]["repository"] = "ramideltoro/nutsnews"
expect_reject("wrong issuer", lambda: module.validate_record(wrong_issuer_record, now=NOW))
wrong_ref_record = record(qualifier_overrides={"ref": "refs/heads/feature"})
expect_reject("wrong protected ref", lambda: module.validate_record(wrong_ref_record, now=NOW))
expect_reject(
    "expired timestamp",
    lambda: module.validate_record(passing_record, now=NOW + timedelta(hours=25)),
)
expect_reject(
    "changed staging deployment ID",
    lambda: module.validate_record(passing_record, now=NOW, expected_staging_deployment_id="stg-" + "f" * 24),
)
missing_suite = app_report({"results": []})
expect_reject("missing suite", lambda: record(report=missing_suite))
for status in ("skip", "cancelled", "timeout", "fail"):
    failed = app_report({"result": "pass", "results": [{"name": f"required-{status}", "required": True, "status": status}]})
    expect_reject(f"{status} suite", lambda failed=failed: record(report=failed))
expect_reject(
    "pre/post identity mismatch",
    lambda: record(post_identity=identity({"image_digest": f"sha256:{'f' * 64}"})),
)
tampered_attestation = {
    "verificationResult": {
        "statement": {
            "subject": [
                {
                    "name": "ghcr.io/ramideltoro/nutsnews",
                    "digest": {"sha256": DIGEST.removeprefix("sha256:")},
                }
            ],
            "predicate": {**passing_record, "result": "fail"},
        }
    }
}
expect_reject(
    "tampered JSON",
    lambda: module.validate_record(passing_record, now=NOW, verified_attestation=tampered_attestation),
)


def fake_staging_fetch(path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], dict[str, object]]:
    assert headers["CF-Access-Client-Id"] == "client-id"
    assert headers["CF-Access-Client-Secret"] == "client-secret"
    if path.startswith("healthz"):
        return 200, {
            "x-nutsnews-source-commit": COMMIT,
            "x-nutsnews-build-id": f"{SOURCE_RUN_ID}-1",
        }, {"ok": True}
    if path.startswith("readyz"):
        return 200, {
            "x-nutsnews-expected-image-digest": DIGEST,
            "x-nutsnews-runtime-environment": "staging",
            "x-nutsnews-deployment-target": "vps-staging",
            "x-nutsnews-config-generation": CONFIG_GENERATION,
        }, {"ok": True, "code": "ready"}
    raise AssertionError(path)


old_env = dict(os.environ)
try:
    os.environ["CF_ACCESS_CLIENT_ID"] = "client-id"
    os.environ["CF_ACCESS_CLIENT_SECRET"] = "client-secret"
    live_identity = module.read_runtime_identity(resolved, fake_staging_fetch)
    assert live_identity.image_digest == DIGEST
finally:
    os.environ.clear()
    os.environ.update(old_env)

assert module._curl_config_quote('client"secret\\value') == 'client\\"secret\\\\value'
with tempfile.TemporaryDirectory() as tempdir:
    headers_path = Path(tempdir) / "headers.txt"
    headers_path.write_text(
        "HTTP/2 302\r\nlocation: https://example.invalid\r\n\r\n"
        "HTTP/2 200\r\nX-NutsNews-Source-Commit: abc\r\n"
        "X-NutsNews-Config-Generation: staging\r\n\r\n",
        encoding="utf-8",
    )
    parsed_headers = module._parse_curl_headers(headers_path)
    assert parsed_headers == {
        "x-nutsnews-source-commit": "abc",
        "x-nutsnews-config-generation": "staging",
    }

workflow = WORKFLOW.read_text(encoding="utf-8")
deploy_audit = DEPLOY_AUDIT.read_text(encoding="utf-8")

for required in (
    "workflow_run:",
    "Deploy Verified NutsNews Staging Candidate",
    "runs-on: ubuntu-latest",
    "environment: staging-tests",
    "predicate-type: https://nutsnews.com/attestations/staging-qualification/v1",
    "push-to-registry: false",
    "gh attestation verify",
    "npm run test:staging-qualification",
    "Check staging identity before tests",
    "Check staging identity after tests",
    "persist-credentials: false",
    "overwrite: false",
    "Record coupled VPS and Vercel production release request",
    "Coupled production release queued",
    "Promotion workflow will start from this successful qualification run.",
):
    assert required in workflow, f"Qualification workflow missing guardrail: {required}"

require_pinned_action(workflow, "actions/attest", "Qualification attestation step")

for forbidden in (
    "repository_dispatch:",
    "repos/ramideltoro/nutsnews-infra/dispatches",
    "nutsnews-production-release",
    "production-vps",
    "NUTSNEWS_STAGING_VPS_SSH_PRIVATE_KEY",
    "NUTSNEWS_INFRA_RELEASE_TOKEN",
    "packages: write",
    "nutsnews_staging_deploy@",
    "ssh ",
):
    assert forbidden not in workflow, f"Qualification workflow must not reference {forbidden}"

assert "target_hostname" in deploy_audit
assert "image_repository" in deploy_audit
assert "migration_head" in deploy_audit
assert "supabase_project_ref" in deploy_audit
assert "environment_url" in deploy_audit

print("Off-VPS staging qualification guardrails passed.")
