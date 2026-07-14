#!/usr/bin/env python3
"""Strictly validate and verify a NutsNews staging deployment candidate.

This module intentionally performs all untrusted-event handling before the
workflow can attach the ``staging-vps`` GitHub Environment.  It accepts only
the fixed candidate shape emitted by the trusted NutsNews build pipeline and
then binds that candidate to both the completed source workflow run and the
OCI BuildKit SLSA provenance stored beside the immutable image digest.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE_REPOSITORY = "ramideltoro/nutsnews"
IMAGE_REPOSITORY = "ghcr.io/ramideltoro/nutsnews"
SCHEMA_VERSION_PATTERN = re.compile(r"^[0-9]{14}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^([1-9][0-9]{0,19})-([1-9][0-9]{0,5})$")
RUN_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")
EXPECTED_KEYS = {
    "schema_version",
    "source_repository",
    "source_commit",
    "image_repository",
    "image_digest",
    "build_id",
    "source_workflow_run_id",
}
OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"


class CandidateError(ValueError):
    """Raised when a candidate cannot safely reach staging credentials."""


@dataclass(frozen=True)
class Candidate:
    schema_version: str
    source_repository: str
    source_commit: str
    image_repository: str
    image_digest: str
    build_id: str
    source_workflow_run_id: str

    @property
    def deployment_id(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"stg-{hashlib.sha256(encoded).hexdigest()[:24]}"

    @property
    def workflow_attempt(self) -> int:
        match = BUILD_ID_PATTERN.fullmatch(self.build_id)
        assert match is not None  # validated in validate_candidate
        return int(match.group(2))

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _require_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise CandidateError(f"Candidate field {name!r} must be a string.")
    if value != value.strip() or not value:
        raise CandidateError(f"Candidate field {name!r} must be a non-empty, trimmed string.")
    return value


def validate_candidate(payload: Any) -> Candidate:
    """Return a canonical candidate or reject every malformed/mutable shape."""

    if not isinstance(payload, dict):
        raise CandidateError("Candidate payload must be a JSON object.")
    keys = set(payload)
    if keys != EXPECTED_KEYS:
        missing = sorted(EXPECTED_KEYS - keys)
        unexpected = sorted(keys - EXPECTED_KEYS)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise CandidateError("Candidate payload must contain exactly the approved fields (" + "; ".join(details) + ").")

    candidate = Candidate(**{name: _require_string(payload, name) for name in EXPECTED_KEYS})
    if not SCHEMA_VERSION_PATTERN.fullmatch(candidate.schema_version):
        raise CandidateError("schema_version must be the expected 14-digit migration schema version.")
    if candidate.source_repository != SOURCE_REPOSITORY:
        raise CandidateError("Only ramideltoro/nutsnews may request a staging deployment.")
    if not COMMIT_PATTERN.fullmatch(candidate.source_commit):
        raise CandidateError("source_commit must be a full lowercase 40-character SHA.")
    if candidate.image_repository != IMAGE_REPOSITORY:
        raise CandidateError("image_repository is not in the staging image allowlist.")
    if not DIGEST_PATTERN.fullmatch(candidate.image_digest):
        raise CandidateError("image_digest must be an immutable sha256 digest; tags such as latest are rejected.")
    if not BUILD_ID_PATTERN.fullmatch(candidate.build_id):
        raise CandidateError("build_id must be the source GitHub workflow run ID and positive attempt (run-attempt).")
    if not RUN_ID_PATTERN.fullmatch(candidate.source_workflow_run_id):
        raise CandidateError("source_workflow_run_id must be a positive numeric GitHub workflow run ID.")
    if candidate.build_id.split("-", 1)[0] != candidate.source_workflow_run_id:
        raise CandidateError("build_id must belong to source_workflow_run_id.")
    return candidate


def _request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nutsnews-staging-candidate-validator",
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub/GHCR hosts only
            return json.load(response)
    except Exception as error:  # pragma: no cover - exact urllib errors vary by runner image
        raise CandidateError(f"Trusted source/provenance lookup failed: {error}") from error


def verify_source(candidate: Candidate, fetch_json: Callable[[str], Any] = _request_json) -> None:
    """Require a successful owned main build and a commit reachable from main."""

    run_url = f"https://api.github.com/repos/{SOURCE_REPOSITORY}/actions/runs/{candidate.source_workflow_run_id}"
    run = fetch_json(run_url)
    if not isinstance(run, dict):
        raise CandidateError("Source workflow run response was not an object.")

    head_repository = run.get("head_repository")
    if not isinstance(head_repository, dict):
        head_repository = {}
    expected = {
        "id": int(candidate.source_workflow_run_id),
        "name": "Container Image",
        "path": ".github/workflows/container-image.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": candidate.source_commit,
    }
    for key, value in expected.items():
        if run.get(key) != value:
            raise CandidateError(f"Source workflow run did not satisfy trusted {key}={value!r}.")
    if head_repository.get("full_name") != SOURCE_REPOSITORY:
        raise CandidateError("Source workflow run was not executed from the trusted NutsNews repository.")
    if run.get("run_attempt") != candidate.workflow_attempt:
        raise CandidateError("Source workflow run attempt does not match build_id.")

    comparison_url = (
        f"https://api.github.com/repos/{SOURCE_REPOSITORY}/compare/"
        f"{candidate.source_commit}...main"
    )
    comparison = fetch_json(comparison_url)
    # GitHub reports `ahead` when the compare head (`main`) contains the base
    # candidate commit. This is the only non-identical relationship that proves
    # the candidate remains reachable from trusted main.
    if not isinstance(comparison, dict) or comparison.get("status") not in {"ahead", "identical"}:
        raise CandidateError("Requested source commit is not reachable from ramideltoro/nutsnews main.")


def _registry_token(fetch_json: Callable[[str], Any]) -> str:
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{IMAGE_REPOSITORY.removeprefix('ghcr.io/')}:pull"})
    token_response = fetch_json(f"https://ghcr.io/token?{query}")
    if not isinstance(token_response, dict) or not isinstance(token_response.get("token"), str):
        raise CandidateError("GHCR did not provide a pull token for the allowlisted image repository.")
    return token_response["token"]


def _provenance_value(provenance: dict[str, Any], *path: str) -> Any:
    value: Any = provenance
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def verify_oci_provenance(candidate: Candidate, fetch_json: Callable[[str, dict[str, str] | None], Any] = _request_json) -> None:
    """Verify the attached immutable OCI SLSA provenance binds all candidate IDs."""

    def registry_fetch(url: str, headers: dict[str, str] | None = None) -> Any:
        return fetch_json(url, headers)

    token = _registry_token(lambda url: registry_fetch(url))
    repository = IMAGE_REPOSITORY.removeprefix("ghcr.io/")
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": ", ".join((OCI_INDEX_MEDIA_TYPE, OCI_MANIFEST_MEDIA_TYPE, "application/json")),
    }
    index_url = f"https://ghcr.io/v2/{repository}/manifests/{candidate.image_digest}"
    index = registry_fetch(index_url, auth_headers)
    if not isinstance(index, dict) or index.get("mediaType") != OCI_INDEX_MEDIA_TYPE:
        raise CandidateError("Requested image digest is not an OCI index with verifiable provenance.")
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list):
        raise CandidateError("OCI index did not contain manifest descriptors.")

    platform_descriptors = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and descriptor.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
        and descriptor.get("platform") == {"architecture": "amd64", "os": "linux"}
        and isinstance(descriptor.get("digest"), str)
        and DIGEST_PATTERN.fullmatch(descriptor["digest"])
    ]
    if len(platform_descriptors) != 1:
        raise CandidateError("OCI index must contain exactly one linux/amd64 application manifest.")
    platform_digest = platform_descriptors[0]["digest"]

    attestation_descriptors = [
        descriptor
        for descriptor in descriptors
        if isinstance(descriptor, dict)
        and descriptor.get("mediaType") == OCI_MANIFEST_MEDIA_TYPE
        and isinstance(descriptor.get("annotations"), dict)
        and descriptor["annotations"].get("vnd.docker.reference.type") == "attestation-manifest"
        and descriptor["annotations"].get("vnd.docker.reference.digest") == platform_digest
        and isinstance(descriptor.get("digest"), str)
        and DIGEST_PATTERN.fullmatch(descriptor["digest"])
    ]
    if not attestation_descriptors:
        raise CandidateError("OCI image is missing a provenance attestation bound to its linux/amd64 manifest.")

    provenance: dict[str, Any] | None = None
    for descriptor in attestation_descriptors:
        manifest_url = f"https://ghcr.io/v2/{repository}/manifests/{descriptor['digest']}"
        manifest = registry_fetch(manifest_url, auth_headers)
        if not isinstance(manifest, dict) or manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE:
            continue
        for layer in manifest.get("layers", []):
            if not isinstance(layer, dict):
                continue
            if layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
                continue
            if layer.get("annotations", {}).get("in-toto.io/predicate-type") != SLSA_PREDICATE_TYPE:
                continue
            layer_digest = layer.get("digest")
            if not isinstance(layer_digest, str) or not DIGEST_PATTERN.fullmatch(layer_digest):
                continue
            document = registry_fetch(f"https://ghcr.io/v2/{repository}/blobs/{layer_digest}", auth_headers)
            if isinstance(document, dict):
                provenance = document
                break
        if provenance is not None:
            break
    if provenance is None:
        raise CandidateError("OCI image is missing an SLSA v1 provenance document.")

    subject = provenance.get("subject")
    if not isinstance(subject, list) or not any(
        isinstance(item, dict)
        and isinstance(item.get("digest"), dict)
        and item["digest"].get("sha256") == platform_digest.removeprefix("sha256:")
        for item in subject
    ):
        raise CandidateError("OCI provenance subject is not bound to the pulled linux/amd64 image manifest.")
    if provenance.get("predicateType") != SLSA_PREDICATE_TYPE:
        raise CandidateError("OCI provenance does not use the required SLSA v1 predicate.")

    args = _provenance_value(provenance, "predicate", "buildDefinition", "externalParameters", "request", "args")
    if not isinstance(args, dict):
        raise CandidateError("OCI provenance is missing immutable build arguments.")
    required_args = {
        "build-arg:SOURCE_REPOSITORY": f"https://github.com/{SOURCE_REPOSITORY}",
        "build-arg:NUTSNEWS_SOURCE_COMMIT": candidate.source_commit,
        "build-arg:NUTSNEWS_BUILD_ID": candidate.build_id,
        "build-arg:NUTSNEWS_DEPLOYMENT_TARGET": "vps",
    }
    for key, value in required_args.items():
        if args.get(key) != value:
            raise CandidateError(f"OCI provenance did not bind {key} to the approved candidate value.")

    builder_id = _provenance_value(provenance, "predicate", "runDetails", "builder", "id")
    expected_builder = (
        f"https://github.com/{SOURCE_REPOSITORY}/actions/runs/"
        f"{candidate.source_workflow_run_id}/attempts/{candidate.workflow_attempt}"
    )
    if builder_id != expected_builder:
        raise CandidateError("OCI provenance builder identity does not match the trusted source workflow run.")


def load_event_candidate(event_path: Path, event_name: str, candidate_env: str, confirmation_env: str) -> Candidate:
    if event_name == "repository_dispatch":
        try:
            event = json.loads(event_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CandidateError("Could not parse the repository_dispatch event payload.") from error
        if not isinstance(event, dict) or event.get("action") != "nutsnews-staging-release":
            raise CandidateError("Only the nutsnews-staging-release dispatch event is accepted.")
        return validate_candidate(event.get("client_payload"))
    if event_name == "workflow_dispatch":
        if os.environ.get(confirmation_env) != "rehearse-staging-candidate":
            raise CandidateError("Manual rehearsal requires the exact rehearsal confirmation.")
        try:
            payload = json.loads(os.environ.get(candidate_env, ""))
        except json.JSONDecodeError as error:
            raise CandidateError("Manual rehearsal candidate_json must be valid JSON.") from error
        return validate_candidate(payload)
    raise CandidateError("This workflow accepts only repository_dispatch or controlled workflow_dispatch rehearsal events.")


def write_candidate(candidate: Candidate, output: Path, github_output: Path | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(candidate.as_dict(), sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o600)
    if github_output:
        lines = [
            f"deployment_id={candidate.deployment_id}",
            f"source_commit={candidate.source_commit}",
            f"image_repository={candidate.image_repository}",
            f"image_digest={candidate.image_digest}",
            f"build_id={candidate.build_id}",
            f"source_workflow_run_id={candidate.source_workflow_run_id}",
            f"schema_version={candidate.schema_version}",
        ]
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--candidate-env", default="NUTSNEWS_STAGING_CANDIDATE_JSON")
    parser.add_argument("--confirmation-env", default="NUTSNEWS_STAGING_REHEARSAL_CONFIRMATION")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    arguments = parser.parse_args()

    try:
        candidate = load_event_candidate(
            arguments.event_path,
            arguments.event_name,
            arguments.candidate_env,
            arguments.confirmation_env,
        )
        verify_source(candidate)
        verify_oci_provenance(candidate)
        write_candidate(candidate, arguments.output, arguments.github_output)
    except CandidateError as error:
        raise SystemExit(f"Staging candidate rejected: {error}") from error

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Validated immutable staging candidate {candidate.deployment_id} at {timestamp}.")


if __name__ == "__main__":
    main()
