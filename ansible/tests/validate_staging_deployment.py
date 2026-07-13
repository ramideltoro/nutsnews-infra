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
WORKFLOW = REPO / ".github/workflows/nutsnews-staging-deploy.yml"
PLAYBOOK = ROOT / "playbooks/deploy-staging.yml"
INVENTORY = ROOT / "inventories/staging/hosts.yml"
DEFAULTS = ROOT / "roles/vps_service_foundation/defaults/main.yml"


spec = importlib.util.spec_from_file_location("validate_staging_candidate", VALIDATOR)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


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
        return {"status": "identical"}
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
        return {"status": "ahead"}
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

for required in (
    "nutsnews-staging-release",
    "workflow_dispatch:",
    "rehearse-staging-candidate",
    "cancel-in-progress: false",
    "environment: staging-vps",
    "Verify trusted source commit and OCI provenance",
    "ansible-playbook",
    "--check",
    "verify_staging_runtime.py",
    "staging_deployment_audit.py",
    "always() && !cancelled()",
):
    assert required in workflow, f"Staging workflow is missing required guardrail: {required}"

assert "environment: staging-vps" not in workflow.split("jobs:", 1)[1].split("deploy:", 1)[0]
assert "production-vps" not in workflow
assert "nutsnews-production-release" not in workflow
assert "group: nutsnews-staging-deploy" in workflow
assert workflow.index("Verify trusted source commit and OCI provenance") < workflow.index("environment: staging-vps")

for required in (
    "hosts: nutsnews_staging_vps",
    "vps_service_foundation_nutsnews_deployment_environments:",
    "- staging",
    "nutsnews-staging-deploy",
    "nutsnews-app-staging",
    "nutsnews-edge-staging",
    "Acquire the staging host mutation lock",
    "Release the staging host mutation lock",
):
    assert required in playbook, f"Staging-only play is missing {required}"

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

print("Immutable staging candidate and deployment guardrails passed.")
