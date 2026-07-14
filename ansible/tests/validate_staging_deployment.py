#!/usr/bin/env python3
"""Focused regression coverage for the immutable staging deployment boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FIXTURES = ROOT / "tests/fixtures/staging_candidate"
VALIDATOR = ROOT / "scripts/validate_staging_candidate.py"
WRITE_VARS = ROOT / "scripts/write_staging_ansible_vars.py"
GATEWAY = ROOT / "scripts/staging_gateway_request.py"
WORKFLOW = REPO / ".github/workflows/nutsnews-staging-deploy.yml"
PLAYBOOK = ROOT / "playbooks/deploy-staging.yml"
INVENTORY = ROOT / "inventories/staging/hosts.yml"
DEFAULTS = ROOT / "roles/vps_service_foundation/defaults/main.yml"
ENVIRONMENT_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment.yml"


spec = importlib.util.spec_from_file_location("validate_staging_candidate", VALIDATOR)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

write_vars_spec = importlib.util.spec_from_file_location("write_staging_ansible_vars", WRITE_VARS)
assert write_vars_spec and write_vars_spec.loader
write_vars_module = importlib.util.module_from_spec(write_vars_spec)
sys.modules[write_vars_spec.name] = write_vars_module
write_vars_spec.loader.exec_module(write_vars_module)

gateway_spec = importlib.util.spec_from_file_location("staging_gateway_request", GATEWAY)
assert gateway_spec and gateway_spec.loader
gateway_module = importlib.util.module_from_spec(gateway_spec)
gateway_spec.loader.exec_module(gateway_module)

minimal_staging_env = {
    "AUTH_GOOGLE_ID": "staging-google-client-id-fixture",
    "AUTH_GOOGLE_SECRET": "staging-google-client-secret-fixture",
    "AUTH_SECRET": "staging-auth-secret-fixture",
    "NEXTAUTH_URL": "https://staging.nutsnews.com",
    "NUTSNEWS_EMAIL_MODE": "disabled",
    "NUTSNEWS_OAUTH_CREDENTIALS_ENV": "staging",
    "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF": "production-project-ref",
    "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY": "staging-anon-key-fixture",
    "NUTSNEWS_PUBLIC_SUPABASE_URL": "https://staging-project-ref.supabase.co",
    "NUTSNEWS_SITE_URL": "https://staging.nutsnews.com",
    "NUTSNEWS_SUPABASE_PROJECT_REF": "staging-project-ref",
    "NUTSNEWS_SUPABASE_URL": "https://staging-project-ref.supabase.co",
    "NUTSNEWS_TELEMETRY_ENVIRONMENT": "staging",
    "SUPABASE_SERVICE_ROLE_KEY": "staging-service-role-fixture",
}
assert write_vars_module.parse_staging_envs(json.dumps(minimal_staging_env)) == minimal_staging_env
assert write_vars_module.STAGING_SECRET_ENV_KEYS & minimal_staging_env.keys() == {
    "AUTH_GOOGLE_SECRET",
    "AUTH_SECRET",
    "NUTSNEWS_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
}

base_staging_env = {
    key: value
    for key, value in minimal_staging_env.items()
    if key not in {"AUTH_GOOGLE_ID", "AUTH_GOOGLE_SECRET", "NUTSNEWS_OAUTH_CREDENTIALS_ENV"}
}
oauth_overrides = gateway_module.protected_staging_oauth_overrides(
    {
        "NUTSNEWS_STAGING_AUTH_GOOGLE_ID": "staging-google-client-id-fixture",
        "NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET": "staging-google-client-secret-fixture",
    }
)
assert (
    write_vars_module.parse_staging_envs(json.dumps(base_staging_env), oauth_overrides)
    == minimal_staging_env
)
stale_oauth_env = {
    **base_staging_env,
    "AUTH_GOOGLE_ID": "must-be-replaced",
    "AUTH_GOOGLE_SECRET": "must-be-replaced",
    "NUTSNEWS_OAUTH_CREDENTIALS_ENV": "production",
}
assert (
    write_vars_module.parse_staging_envs(json.dumps(stale_oauth_env), oauth_overrides)
    == minimal_staging_env
)
for incomplete_oauth in (
    {},
    {"NUTSNEWS_STAGING_AUTH_GOOGLE_ID": "staging-google-client-id-fixture"},
    {"NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET": "staging-google-client-secret-fixture"},
):
    try:
        gateway_module.protected_staging_oauth_overrides(incomplete_oauth)
    except module.CandidateError:
        pass
    else:
        raise AssertionError("Incomplete protected staging OAuth credentials must fail closed.")


def fixture(name: str) -> dict[str, str]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


valid = module.validate_candidate(fixture("valid.json"))
assert valid.deployment_id == module.validate_candidate(fixture("valid.json")).deployment_id
assert valid.deployment_id.startswith("stg-")
assert len(valid.deployment_id) == len("stg-") + 24

for name in ("mutable-tag.json", "invalid-digest.json", "wrong-repository.json", "partial.json"):
    try:
        module.validate_candidate(fixture(name))
    except module.CandidateError:
        pass
    else:
        raise AssertionError(f"{name} must be rejected before any staging environment access.")


def trusted_source_fetch(url: str) -> object:
    if "/actions/runs/" in url:
        return {
            "id": 123456789,
            "name": "Container Image",
            "path": ".github/workflows/container-image.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": valid.source_commit,
            "head_repository": {"full_name": "ramideltoro/nutsnews"},
            "run_attempt": 2,
        }
    if "/compare/" in url:
        return {"status": "ahead"}
    raise AssertionError(f"Unexpected trusted-source URL: {url}")


module.verify_source(valid, trusted_source_fetch)
untrusted = module.validate_candidate(fixture("untrusted-source.json"))


def untrusted_source_fetch(url: str) -> object:
    if "/actions/runs/" in url:
        return {
            "id": 123456789,
            "name": "Container Image",
            "path": ".github/workflows/container-image.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": untrusted.source_commit,
            "head_repository": {"full_name": "ramideltoro/nutsnews"},
            "run_attempt": 2,
        }
    if "/compare/" in url:
        return {"status": "diverged"}
    raise AssertionError(f"Unexpected untrusted-source URL: {url}")


try:
    module.verify_source(untrusted, untrusted_source_fetch)
except module.CandidateError:
    pass
else:
    raise AssertionError("A source commit not reachable from main must be rejected.")


platform_digest = "sha256:" + "d" * 64
attestation_digest = "sha256:" + "e" * 64
provenance_digest = "sha256:" + "f" * 64


def provenance_fetch(url: str, _headers: dict[str, str] | None = None) -> object:
    if url.startswith("https://ghcr.io/token?"):
        return {"token": "fixture-token"}
    if url.endswith(f"/manifests/{valid.image_digest}"):
        return {
            "mediaType": module.OCI_INDEX_MEDIA_TYPE,
            "manifests": [
                {
                    "mediaType": module.OCI_MANIFEST_MEDIA_TYPE,
                    "digest": platform_digest,
                    "platform": {"architecture": "amd64", "os": "linux"},
                },
                {
                    "mediaType": module.OCI_MANIFEST_MEDIA_TYPE,
                    "digest": attestation_digest,
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": platform_digest,
                    },
                },
            ],
        }
    if url.endswith(f"/manifests/{attestation_digest}"):
        return {
            "mediaType": module.OCI_MANIFEST_MEDIA_TYPE,
            "layers": [
                {
                    "mediaType": module.IN_TOTO_MEDIA_TYPE,
                    "digest": provenance_digest,
                    "annotations": {"in-toto.io/predicate-type": module.SLSA_PREDICATE_TYPE},
                }
            ],
        }
    if url.endswith(f"/blobs/{provenance_digest}"):
        return {
            "predicateType": module.SLSA_PREDICATE_TYPE,
            "subject": [{"digest": {"sha256": platform_digest.removeprefix("sha256:")}}],
            "predicate": {
                "buildDefinition": {
                    "externalParameters": {
                        "request": {
                            "args": {
                                "build-arg:SOURCE_REPOSITORY": "https://github.com/ramideltoro/nutsnews",
                                "build-arg:NUTSNEWS_SOURCE_COMMIT": valid.source_commit,
                                "build-arg:NUTSNEWS_BUILD_ID": valid.build_id,
                                "build-arg:NUTSNEWS_DEPLOYMENT_TARGET": "vps",
                            }
                        }
                    }
                },
                "runDetails": {
                    "builder": {
                        "id": "https://github.com/ramideltoro/nutsnews/actions/runs/123456789/attempts/2"
                    }
                },
            },
        }
    raise AssertionError(f"Unexpected provenance URL: {url}")


module.verify_oci_provenance(valid, provenance_fetch)

workflow = WORKFLOW.read_text(encoding="utf-8")
playbook = PLAYBOOK.read_text(encoding="utf-8")
inventory = INVENTORY.read_text(encoding="utf-8")
defaults = DEFAULTS.read_text(encoding="utf-8")
environment_tasks = ENVIRONMENT_TASKS.read_text(encoding="utf-8")

for required in (
    "nutsnews-staging-release",
    "workflow_dispatch:",
    "rehearse-staging-candidate",
    "cancel-in-progress: false",
    "environment: staging-vps",
    "NUTSNEWS_STAGING_AUTH_GOOGLE_ID",
    "NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET",
    "Verify trusted source commit and OCI provenance",
    "ansible-playbook",
    "for operation in check apply",
    "staging_gateway_request.py",
    "nutsnews_staging_deploy@65.75.202.112",
    "Prove the deployment key rejects arbitrary commands",
    "staging_deployment_audit.py",
    "always() && !cancelled()",
):
    assert required in workflow, f"Staging workflow is missing required guardrail: {required}"

assert "environment: staging-vps" not in workflow.split("jobs:", 1)[1].split("deploy:", 1)[0]
assert "production-vps" not in workflow
assert "nutsnews-production-release" not in workflow
assert "group: nutsnews-staging-deploy" in workflow
assert workflow.index("Verify trusted source commit and OCI provenance") < workflow.index("environment: staging-vps")
preflight_workflow = workflow.split("deploy:", 1)[0]
assert "NUTSNEWS_STAGING_AUTH_GOOGLE_ID" not in preflight_workflow
assert "NUTSNEWS_STAGING_AUTH_GOOGLE_SECRET" not in preflight_workflow

for required in (
    "hosts: nutsnews_staging_vps",
    "Load reviewed service-foundation defaults for staging safeguards",
    "tasks_from: staging_defaults.yml",
    "vps_service_foundation_nutsnews_deployment_environments:",
    "- staging",
    "nutsnews-staging-deploy",
    "nutsnews-app-staging",
    "nutsnews-edge-staging",
    "Acquire the staging host mutation lock",
    "Release the staging host mutation lock",
):
    assert required in playbook, f"Staging-only play is missing {required}"

assert "public: true" not in playbook

assert (
    playbook.index("tasks_from: staging_defaults.yml")
    < playbook.index("Refuse anything except the fixed staging inventory alias")
)
assert "nutsnews-app'" in playbook
assert "vps_baseline_vps" not in playbook
assert "production/hosts.yml" not in playbook
assert "staging-vps:" in inventory
assert "vps_baseline_vps" not in inventory

# A fixed global queue and a host-side lock make concurrent dispatches queue
# rather than cancel each other or mutate the staging runtime simultaneously.
assert "group: nutsnews-staging-deploy" in workflow
assert "cancel-in-progress: false" in workflow
assert "nutsnews-staging-deploy.lock" in defaults

# A first staging dry run must not require directories that check mode only
# predicts. Parent-dependent files are rendered by apply, or by later checks
# after those root-owned directories already exist.
assert "Inspect runtime directories after the non-mutating plan" in environment_tasks
assert environment_tasks.count("not ansible_check_mode or") == 2
assert "runtime_directories.results[0].stat.isdir" in environment_tasks
assert "runtime_directories.results[1].stat.isdir" in environment_tasks

print("Immutable staging candidate and deployment guardrails passed.")
