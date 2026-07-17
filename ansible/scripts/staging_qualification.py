#!/usr/bin/env python3
"""Resolve, verify, and write NutsNews staging qualification evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SOURCE_REPOSITORY = "ramideltoro/nutsnews"
INFRA_REPOSITORY = "ramideltoro/nutsnews-infra"
IMAGE_REPOSITORY = "ghcr.io/ramideltoro/nutsnews"
QUALIFIER_WORKFLOW = ".github/workflows/nutsnews-staging-qualification.yml"
QUALIFICATION_PREDICATE_TYPE = "https://nutsnews.com/attestations/staging-qualification/v1"
TARGET_HOSTNAME = "staging.nutsnews.com"
TARGET_BASE_URL = f"https://{TARGET_HOSTNAME}/"
TRUSTED_REF = "refs/heads/main"
EXPECTED_DEPLOYMENT_TARGET = "vps-staging"
EXPECTED_RUNTIME_ENVIRONMENT = "staging"
QUALIFICATION_TTL_HOURS = 24

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")
RUN_ATTEMPT_PATTERN = re.compile(r"^[1-9][0-9]{0,5}$")
STAGING_DEPLOYMENT_ID_PATTERN = re.compile(r"^stg-[0-9a-f]{24}$")
CONFIG_GENERATION_PATTERN = re.compile(r"^staging-stg-[0-9a-f]{24}-[0-9a-f]{12}$")
SCHEMA_VERSION_PATTERN = re.compile(r"^[0-9]{14}$")


class QualificationError(ValueError):
    """Raised when a qualification input cannot produce a trusted pass."""


@dataclass(frozen=True)
class DeploymentEvidence:
    schema_version: str
    migration_head: str
    supabase_project_ref: str
    source_repository: str
    source_commit: str
    image_repository: str
    image_digest: str
    build_id: str
    source_workflow_run_id: str
    infra_commit: str
    config_generation: str
    staging_deployment_id: str
    github_deployment_id: int
    target_hostname: str
    staging_deploy_workflow_run_id: str
    staging_deploy_evidence_url: str
    github_deployment_url: str
    github_deployment_status_url: str
    deployed_at: str


@dataclass(frozen=True)
class RuntimeIdentity:
    checked_at: str
    target_hostname: str
    health_status: int
    ready_status: int
    source_commit: str
    build_id: str
    image_digest: str
    runtime_environment: str
    deployment_target: str
    config_generation: str
    ready_code: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise QualificationError(f"{label} timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QualificationError(f"{label} timestamp is invalid.") from error
    if parsed.tzinfo is None:
        raise QualificationError(f"{label} timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def require_string(payload: dict[str, Any], name: str, pattern: re.Pattern[str] | None = None) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or value != value.strip() or not value:
        raise QualificationError(f"{name} must be a non-empty trimmed string.")
    if pattern and not pattern.fullmatch(value):
        raise QualificationError(f"{name} has an invalid format.")
    return value


def request_json(url: str, token: str | None = None, headers: dict[str, str] | None = None) -> Any:
    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nutsnews-staging-qualification",
    }
    if headers:
        request_headers.update(headers)
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
        request_headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub/staging URLs only
        return json.load(response)


def resolve_staging_run_id(event_path: Path, event_name: str, manual_run_id: str) -> str:
    if event_name == "workflow_run":
        try:
            event = json.loads(event_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise QualificationError("Could not parse workflow_run event payload.") from error
        workflow_run = event.get("workflow_run") if isinstance(event, dict) else None
        if not isinstance(workflow_run, dict):
            raise QualificationError("workflow_run event payload is missing workflow_run.")
        if workflow_run.get("conclusion") != "success":
            raise QualificationError("Only a successful staging deploy workflow can trigger qualification.")
        run_id = workflow_run.get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            raise QualificationError("workflow_run event is missing a numeric staging run ID.")
        return str(run_id)
    if event_name == "workflow_dispatch":
        if not RUN_ID_PATTERN.fullmatch(manual_run_id):
            raise QualificationError("Manual qualification requires a numeric staging_deploy_run_id.")
        return manual_run_id
    raise QualificationError("Qualification accepts only workflow_run or controlled workflow_dispatch events.")


def load_latest_success_status(
    deployment_id: int,
    fetch_json: Callable[[str], Any],
) -> dict[str, Any]:
    statuses = fetch_json(
        f"https://api.github.com/repos/{INFRA_REPOSITORY}/deployments/{deployment_id}/statuses"
    )
    if not isinstance(statuses, list) or not statuses:
        raise QualificationError("GitHub deployment has no statuses.")
    latest = statuses[0]
    if not isinstance(latest, dict) or latest.get("state") != "success":
        raise QualificationError("Latest GitHub deployment status is not success.")
    return latest


def deployment_from_payload(deployment: dict[str, Any], status: dict[str, Any]) -> DeploymentEvidence:
    payload = deployment.get("payload")
    if not isinstance(payload, dict):
        raise QualificationError("GitHub deployment payload is missing.")
    if deployment.get("environment") != "staging":
        raise QualificationError("Deployment environment is not staging.")
    if deployment.get("production_environment") is not False:
        raise QualificationError("Deployment must be explicitly non-production.")
    if deployment.get("transient_environment") is not True:
        raise QualificationError("Deployment must be a transient staging environment.")
    if deployment.get("task") != "nutsnews-staging-deploy":
        raise QualificationError("Deployment task is not the fixed staging deploy task.")
    deployment_id = deployment.get("id")
    if not isinstance(deployment_id, int) or deployment_id <= 0:
        raise QualificationError("Deployment database ID is invalid.")
    description = str(status.get("description") or "")

    evidence = DeploymentEvidence(
        schema_version=require_string(payload, "schema_version", SCHEMA_VERSION_PATTERN),
        migration_head=require_string(payload, "migration_head", SCHEMA_VERSION_PATTERN),
        supabase_project_ref=require_string(payload, "supabase_project_ref", re.compile(r"^[a-z0-9]{20}$")),
        source_repository=require_string(payload, "source_repository"),
        source_commit=require_string(payload, "source_commit", COMMIT_PATTERN),
        image_repository=require_string(payload, "image_repository"),
        image_digest=require_string(payload, "requested_digest", DIGEST_PATTERN),
        build_id=require_string(payload, "build_id"),
        source_workflow_run_id=require_string(payload, "source_workflow_run_id", RUN_ID_PATTERN),
        infra_commit=require_string(payload, "infra_commit", COMMIT_PATTERN),
        config_generation=require_string(payload, "config_generation", CONFIG_GENERATION_PATTERN),
        staging_deployment_id=require_string(payload, "deployment_id", STAGING_DEPLOYMENT_ID_PATTERN),
        github_deployment_id=deployment_id,
        target_hostname=require_string(payload, "target_hostname"),
        staging_deploy_workflow_run_id=require_string(payload, "github_run_id", RUN_ID_PATTERN),
        staging_deploy_evidence_url=str(status.get("log_url") or ""),
        github_deployment_url=str(deployment.get("url") or ""),
        github_deployment_status_url=str(status.get("url") or ""),
        deployed_at=str(status.get("created_at") or ""),
    )
    if evidence.source_repository != SOURCE_REPOSITORY:
        raise QualificationError("Deployment source repository is not trusted.")
    if evidence.image_repository != IMAGE_REPOSITORY:
        raise QualificationError("Deployment image repository is not trusted.")
    if evidence.target_hostname != TARGET_HOSTNAME:
        raise QualificationError("Deployment target hostname is not staging.")
    if f"actual={evidence.image_digest}" not in description:
        raise QualificationError("Successful deployment status is not bound to the actual digest.")
    parse_time(evidence.deployed_at, "Deployment status")
    parsed_log = urlparse(evidence.staging_deploy_evidence_url)
    if parsed_log.scheme != "https" or parsed_log.netloc != "github.com":
        raise QualificationError("Deployment evidence URL is not a GitHub HTTPS URL.")
    return evidence


def fetch_deployment_evidence(
    staging_run_id: str,
    staging_deployment_id: str,
    fetch_json: Callable[[str], Any],
) -> DeploymentEvidence:
    query = urlencode({"environment": "staging", "per_page": "100"})
    deployments = fetch_json(f"https://api.github.com/repos/{INFRA_REPOSITORY}/deployments?{query}")
    if not isinstance(deployments, list):
        raise QualificationError("GitHub deployments response was not a list.")
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for deployment in deployments:
        if not isinstance(deployment, dict) or not isinstance(deployment.get("payload"), dict):
            continue
        payload = deployment["payload"]
        if str(payload.get("github_run_id")) != staging_run_id:
            continue
        if staging_deployment_id and payload.get("deployment_id") != staging_deployment_id:
            continue
        status = load_latest_success_status(int(deployment["id"]), fetch_json)
        matches.append((deployment, status))
    if len(matches) != 1:
        raise QualificationError(f"Expected exactly one successful staging deployment for run {staging_run_id}.")
    return deployment_from_payload(matches[0][0], matches[0][1])


def write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def load_deployment(path: Path) -> DeploymentEvidence:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("Deployment evidence file is invalid.") from error
    if not isinstance(payload, dict):
        raise QualificationError("Deployment evidence file must be an object.")
    return DeploymentEvidence(**payload)


def load_identity(path: Path) -> RuntimeIdentity:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("Runtime identity file is invalid.") from error
    if not isinstance(payload, dict):
        raise QualificationError("Runtime identity file must be an object.")
    return RuntimeIdentity(**payload)


def access_headers_from_env() -> dict[str, str]:
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise QualificationError("staging-tests Cloudflare Access client ID and secret are required.")
    return {
        "Accept": "application/json",
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }


def _curl_config_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_curl_headers(path: Path) -> dict[str, str]:
    sections = [section for section in re.split(r"\r?\n\r?\n", path.read_text(encoding="utf-8")) if section.strip()]
    if not sections:
        return {}
    headers: dict[str, str] = {}
    for line in sections[-1].splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def fetch_staging_json(path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], dict[str, Any]]:
    url = f"{TARGET_BASE_URL}{path.lstrip('/')}"
    with tempfile.TemporaryDirectory(prefix="nutsnews-staging-identity-") as tempdir:
        temp = Path(tempdir)
        config_path = temp / "curl.conf"
        headers_path = temp / "headers.txt"
        body_path = temp / "body.json"
        config_lines = [
            "silent",
            "show-error",
            "max-time = 20",
            f'dump-header = "{_curl_config_quote(str(headers_path))}"',
            f'output = "{_curl_config_quote(str(body_path))}"',
            'write-out = "%{http_code}"',
        ]
        for key, value in headers.items():
            config_lines.append(f'header = "{_curl_config_quote(key)}: {_curl_config_quote(value)}"')
        config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
        config_path.chmod(0o600)
        completed = subprocess.run(  # noqa: S603 - fixed executable and URL, secrets isolated in private config.
            ["curl", "--config", str(config_path), url],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        status_text = completed.stdout.strip()[-3:]
        if not status_text.isdigit():
            raise QualificationError("Staging identity request did not return an HTTP status.")
        status = int(status_text)
        if completed.returncode != 0:
            raise QualificationError(f"Staging identity request failed before HTTP completion with curl exit {completed.returncode}.")
        response_headers = _parse_curl_headers(headers_path) if headers_path.exists() else {}
        body: dict[str, Any] = {}
        if body_path.exists() and body_path.stat().st_size > 0:
            try:
                parsed = json.loads(body_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                body = parsed
        return status, response_headers, body


def read_runtime_identity(
    deployment: DeploymentEvidence,
    fetch_pair: Callable[[str, dict[str, str]], tuple[int, dict[str, str], dict[str, Any]]] = fetch_staging_json,
) -> RuntimeIdentity:
    headers = access_headers_from_env()
    health_status, health_headers, health_body = fetch_pair("healthz", headers)
    ready_status, ready_headers, ready_body = fetch_pair(
        f"readyz?qualification={deployment.config_generation}", headers
    )
    if health_status != 200 or ready_status != 200:
        raise QualificationError("Staging health/ready endpoints did not both return HTTP 200.")
    if not isinstance(health_body, dict) or health_body.get("ok") is not True:
        raise QualificationError("Staging /healthz did not return ok=true.")
    if not isinstance(ready_body, dict) or ready_body.get("ok") is not True or ready_body.get("code") != "ready":
        raise QualificationError("Staging /readyz did not return the ready identity.")
    identity = RuntimeIdentity(
        checked_at=isoformat(utc_now()),
        target_hostname=TARGET_HOSTNAME,
        health_status=health_status,
        ready_status=ready_status,
        source_commit=health_headers.get("x-nutsnews-source-commit", ""),
        build_id=health_headers.get("x-nutsnews-build-id", ""),
        image_digest=ready_headers.get("x-nutsnews-expected-image-digest", ""),
        runtime_environment=ready_headers.get("x-nutsnews-runtime-environment", ""),
        deployment_target=ready_headers.get("x-nutsnews-deployment-target", ""),
        config_generation=ready_headers.get("x-nutsnews-config-generation", ""),
        ready_code=str(ready_body.get("code", "")),
    )
    assert_identity_matches_deployment(identity, deployment)
    return identity


def assert_identity_matches_deployment(identity: RuntimeIdentity, deployment: DeploymentEvidence) -> None:
    expected = {
        "target_hostname": deployment.target_hostname,
        "source_commit": deployment.source_commit,
        "build_id": deployment.build_id,
        "image_digest": deployment.image_digest,
        "runtime_environment": EXPECTED_RUNTIME_ENVIRONMENT,
        "deployment_target": EXPECTED_DEPLOYMENT_TARGET,
        "config_generation": deployment.config_generation,
        "ready_code": "ready",
    }
    actual = asdict(identity)
    for key, value in expected.items():
        if actual.get(key) != value:
            raise QualificationError(f"Runtime identity mismatch for {key}.")


def required_suite_results(app_report: dict[str, Any]) -> list[dict[str, Any]]:
    results = app_report.get("results")
    if not isinstance(results, list) or not results:
        raise QualificationError("App qualification report has no required suite results.")
    required: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise QualificationError("App qualification suite result has an invalid shape.")
        name = result.get("name")
        status = result.get("status")
        if not isinstance(name, str) or not name:
            raise QualificationError("App qualification suite name is missing.")
        if result.get("required") is not True:
            raise QualificationError(f"Required suite {name} is not marked required.")
        if status != "pass":
            raise QualificationError(f"Required suite {name} was not passing.")
        required.append(
            {
                "name": name,
                "required": True,
                "result": status,
                "duration_seconds": result.get("durationSeconds"),
            }
        )
    return required


def load_app_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("App qualification report is missing or invalid JSON.") from error
    if not isinstance(report, dict):
        raise QualificationError("App qualification report must be an object.")
    if report.get("result") != "pass":
        raise QualificationError("App qualification report is not a pass.")
    return report


def build_record(
    deployment: DeploymentEvidence,
    pre_identity: RuntimeIdentity,
    post_identity: RuntimeIdentity,
    app_report: dict[str, Any],
    qualifier: dict[str, str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    assert_identity_matches_deployment(pre_identity, deployment)
    assert_identity_matches_deployment(post_identity, deployment)
    comparable_pre = {key: value for key, value in asdict(pre_identity).items() if key != "checked_at"}
    comparable_post = {key: value for key, value in asdict(post_identity).items() if key != "checked_at"}
    if comparable_pre != comparable_post:
        raise QualificationError("Pre/post staging identity mismatch.")
    suite_commit = str(app_report.get("suiteRevision") or "")
    if suite_commit != deployment.source_commit:
        raise QualificationError("Test-suite commit does not match the trusted app source commit.")
    started = parse_time(started_at, "Qualification start")
    completed = parse_time(completed_at, "Qualification completion")
    if completed < started:
        raise QualificationError("Qualification completion cannot be before start.")
    expires = completed + timedelta(hours=QUALIFICATION_TTL_HOURS)
    run_id = require_string(qualifier, "run_id", RUN_ID_PATTERN)
    run_attempt = require_string(qualifier, "run_attempt", RUN_ATTEMPT_PATTERN)
    qualifier_commit = require_string(qualifier, "commit", COMMIT_PATTERN)
    ref = require_string(qualifier, "ref")
    workflow_ref = require_string(qualifier, "workflow_ref")
    return {
        "schema_version": "nutsnews.staging_qualification.v1",
        "predicate_type": QUALIFICATION_PREDICATE_TYPE,
        "result": "pass",
        "image": {
            "repository": deployment.image_repository,
            "digest": deployment.image_digest,
        },
        "source": {
            "repository": deployment.source_repository,
            "commit": deployment.source_commit,
            "build_id": deployment.build_id,
            "workflow_run_id": deployment.source_workflow_run_id,
            "workflow_run_url": (
                f"https://github.com/{deployment.source_repository}/actions/runs/"
                f"{deployment.source_workflow_run_id}"
            ),
            "migration_head": deployment.migration_head,
            "schema_version": deployment.schema_version,
            "supabase_project_ref": deployment.supabase_project_ref,
        },
        "infra": {
            "repository": INFRA_REPOSITORY,
            "commit": deployment.infra_commit,
            "config_generation": deployment.config_generation,
        },
        "staging": {
            "deployment_id": deployment.staging_deployment_id,
            "github_deployment_id": deployment.github_deployment_id,
            "target_hostname": deployment.target_hostname,
            "deploy_workflow_run_id": deployment.staging_deploy_workflow_run_id,
            "deploy_workflow_run_url": deployment.staging_deploy_evidence_url,
            "github_deployment_url": deployment.github_deployment_url,
            "github_deployment_status_url": deployment.github_deployment_status_url,
            "deployed_at": deployment.deployed_at,
            "pre_test_identity": asdict(pre_identity),
            "post_test_identity": asdict(post_identity),
        },
        "test_suite": {
            "repository": SOURCE_REPOSITORY,
            "commit": suite_commit,
        },
        "qualifier": {
            "repository": INFRA_REPOSITORY,
            "workflow": QUALIFIER_WORKFLOW,
            "commit": qualifier_commit,
            "ref": ref,
            "workflow_ref": workflow_ref,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "run_url": f"https://github.com/{INFRA_REPOSITORY}/actions/runs/{run_id}",
        },
        "evidence_urls": {
            "source_workflow_run": (
                f"https://github.com/{deployment.source_repository}/actions/runs/"
                f"{deployment.source_workflow_run_id}"
            ),
            "staging_deploy_workflow_run": deployment.staging_deploy_evidence_url,
            "qualifier_workflow_run": f"https://github.com/{INFRA_REPOSITORY}/actions/runs/{run_id}",
        },
        "timing": {
            "started_at": isoformat(started),
            "completed_at": isoformat(completed),
            "expires_at": isoformat(expires),
            "ttl_hours": QUALIFICATION_TTL_HOURS,
        },
        "invalidated_by": [
            "staging redeploy",
            "infra config revision",
            "required test-suite revision",
            "qualification expiration",
        ],
        "required_suites": required_suite_results(app_report),
    }


def validate_record(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
    expected_image_digest: str | None = None,
    expected_staging_deployment_id: str | None = None,
    verified_attestation: dict[str, Any] | None = None,
) -> None:
    if record.get("schema_version") != "nutsnews.staging_qualification.v1":
        raise QualificationError("Qualification schema version is not trusted.")
    if record.get("predicate_type") != QUALIFICATION_PREDICATE_TYPE:
        raise QualificationError("Qualification predicate type is not trusted.")
    if record.get("result") != "pass":
        raise QualificationError("Qualification result is not pass.")
    image = record.get("image")
    source = record.get("source")
    staging = record.get("staging")
    timing = record.get("timing")
    suites = record.get("required_suites")
    qualifier = record.get("qualifier")
    if not isinstance(image, dict) or image.get("repository") != IMAGE_REPOSITORY:
        raise QualificationError("Qualification image repository is not trusted.")
    digest = str(image.get("digest") or "")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise QualificationError("Qualification image digest is invalid.")
    if expected_image_digest and digest != expected_image_digest:
        raise QualificationError("Qualification digest does not match the expected candidate.")
    if not isinstance(source, dict):
        raise QualificationError("Qualification source section is missing.")
    if not SCHEMA_VERSION_PATTERN.fullmatch(str(source.get("schema_version") or "")):
        raise QualificationError("Qualification source schema version is invalid.")
    if not SCHEMA_VERSION_PATTERN.fullmatch(str(source.get("migration_head") or "")):
        raise QualificationError("Qualification source migration head is invalid.")
    if not re.fullmatch(r"^[a-z0-9]{20}$", str(source.get("supabase_project_ref") or "")):
        raise QualificationError("Qualification source Supabase project ref is invalid.")
    if not isinstance(staging, dict):
        raise QualificationError("Qualification staging section is missing.")
    deployment_id = str(staging.get("deployment_id") or "")
    if not STAGING_DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id):
        raise QualificationError("Qualification staging deployment ID is invalid.")
    if expected_staging_deployment_id and deployment_id != expected_staging_deployment_id:
        raise QualificationError("Qualification staging deployment ID changed.")
    if staging.get("target_hostname") != TARGET_HOSTNAME:
        raise QualificationError("Qualification target hostname is not staging.")
    pre = staging.get("pre_test_identity")
    post = staging.get("post_test_identity")
    if not isinstance(pre, dict) or not isinstance(post, dict):
        raise QualificationError("Qualification pre/post identities are required.")
    comparable_pre = {key: value for key, value in pre.items() if key != "checked_at"}
    comparable_post = {key: value for key, value in post.items() if key != "checked_at"}
    if comparable_pre != comparable_post:
        raise QualificationError("Qualification pre/post identities do not match.")
    for identity in (pre, post):
        if identity.get("image_digest") != digest:
            raise QualificationError("Qualification identity digest does not match image digest.")
        if identity.get("deployment_target") != EXPECTED_DEPLOYMENT_TARGET:
            raise QualificationError("Qualification identity target is not staging.")
        if identity.get("runtime_environment") != EXPECTED_RUNTIME_ENVIRONMENT:
            raise QualificationError("Qualification identity environment is not staging.")
    if not isinstance(timing, dict):
        raise QualificationError("Qualification timing is missing.")
    expires_at = parse_time(str(timing.get("expires_at") or ""), "Qualification expiration")
    if (now or utc_now()) >= expires_at:
        raise QualificationError("Qualification is expired.")
    if not isinstance(suites, list) or not suites:
        raise QualificationError("Qualification has no required suites.")
    for suite in suites:
        if not isinstance(suite, dict) or suite.get("required") is not True or suite.get("result") != "pass":
            raise QualificationError("Qualification contains a missing, skipped, cancelled, timed-out, or failed suite.")
    if not isinstance(qualifier, dict):
        raise QualificationError("Qualification issuer metadata is missing.")
    if qualifier.get("repository") != INFRA_REPOSITORY or qualifier.get("workflow") != QUALIFIER_WORKFLOW:
        raise QualificationError("Qualification issuer repository/workflow is not trusted.")
    if qualifier.get("ref") != TRUSTED_REF:
        raise QualificationError("Qualification issuer ref is not the protected main ref.")
    if qualifier.get("workflow_ref") != f"{INFRA_REPOSITORY}/{QUALIFIER_WORKFLOW}@{TRUSTED_REF}":
        raise QualificationError("Qualification workflow ref is not bound to the protected main ref.")
    if verified_attestation is not None:
        validate_verified_attestation(record, verified_attestation)


def validate_verified_attestation(record: dict[str, Any], verified: dict[str, Any]) -> None:
    bundle = verified.get("verificationResult") if isinstance(verified, dict) else None
    statement = bundle.get("statement") if isinstance(bundle, dict) else None
    if not isinstance(statement, dict):
        raise QualificationError("Verified attestation statement is missing.")
    subject = statement.get("subject")
    if not isinstance(subject, list) or not any(
        isinstance(item, dict)
        and item.get("name") == IMAGE_REPOSITORY
        and isinstance(item.get("digest"), dict)
        and item["digest"].get("sha256") == str(record["image"]["digest"]).removeprefix("sha256:")
        for item in subject
    ):
        raise QualificationError("Attestation subject digest is not the qualified image.")
    predicate = statement.get("predicate")
    if predicate != record:
        raise QualificationError("Attestation predicate does not exactly match the qualification record.")


def command_resolve(arguments: argparse.Namespace) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    staging_run_id = resolve_staging_run_id(
        arguments.event_path,
        arguments.event_name,
        arguments.staging_deploy_run_id,
    )
    fetch_json = lambda url: request_json(url, token=token)
    evidence = fetch_deployment_evidence(
        staging_run_id,
        arguments.staging_deployment_id,
        fetch_json,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(asdict(evidence), sort_keys=True) + "\n", encoding="utf-8")
    write_github_output(
        arguments.github_output,
        {
            "staging_deployment_id": evidence.staging_deployment_id,
            "github_deployment_id": str(evidence.github_deployment_id),
            "source_commit": evidence.source_commit,
            "image_repository": evidence.image_repository,
            "image_digest": evidence.image_digest,
            "build_id": evidence.build_id,
            "source_workflow_run_id": evidence.source_workflow_run_id,
            "schema_version": evidence.schema_version,
            "migration_head": evidence.migration_head,
            "supabase_project_ref": evidence.supabase_project_ref,
            "infra_commit": evidence.infra_commit,
            "config_generation": evidence.config_generation,
            "target_hostname": evidence.target_hostname,
        },
    )
    print(f"Resolved staging deployment {evidence.staging_deployment_id}.")


def command_identity(arguments: argparse.Namespace) -> None:
    deployment = load_deployment(arguments.deployment_evidence)
    identity = read_runtime_identity(deployment)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(asdict(identity), sort_keys=True) + "\n", encoding="utf-8")
    print(f"Verified staging identity for {deployment.staging_deployment_id}.")


def command_record(arguments: argparse.Namespace) -> None:
    deployment = load_deployment(arguments.deployment_evidence)
    pre_identity = load_identity(arguments.pre_identity)
    post_identity = load_identity(arguments.post_identity)
    app_report = load_app_report(arguments.app_report)
    qualifier = {
        "commit": arguments.qualifier_commit,
        "ref": arguments.qualifier_ref,
        "workflow_ref": arguments.qualifier_workflow_ref,
        "run_id": arguments.qualifier_run_id,
        "run_attempt": arguments.qualifier_run_attempt,
    }
    record = build_record(
        deployment,
        pre_identity,
        post_identity,
        app_report,
        qualifier,
        arguments.started_at,
        arguments.completed_at,
    )
    validate_record(record, expected_image_digest=deployment.image_digest, expected_staging_deployment_id=deployment.staging_deployment_id)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote passing staging qualification record for {deployment.staging_deployment_id}.")


def command_validate(arguments: argparse.Namespace) -> None:
    try:
        record = json.loads(arguments.record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("Qualification record is missing or invalid.") from error
    verified_items: list[dict[str, Any]] = []
    if arguments.verified_attestation:
        try:
            parsed = json.loads(arguments.verified_attestation.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise QualificationError("Verified attestation JSON is missing or invalid.") from error
        if not isinstance(parsed, list) or not parsed:
            raise QualificationError("Verified attestation JSON must be a non-empty gh result list.")
        verified_items = [item for item in parsed if isinstance(item, dict)]
        if not verified_items:
            raise QualificationError("Verified attestation JSON did not contain result objects.")
    if not verified_items:
        validate_record(
            record,
            expected_image_digest=arguments.expected_image_digest,
            expected_staging_deployment_id=arguments.expected_staging_deployment_id,
        )
    else:
        errors: list[str] = []
        for item in verified_items:
            try:
                validate_record(
                    record,
                    expected_image_digest=arguments.expected_image_digest,
                    expected_staging_deployment_id=arguments.expected_staging_deployment_id,
                    verified_attestation=item,
                )
                break
            except QualificationError as error:
                errors.append(str(error))
        else:
            raise QualificationError(
                "No verified attestation exactly matched this qualification record: " + "; ".join(errors[:3])
            )
    print("Staging qualification record is valid.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve-deployment")
    resolve.add_argument("--event-path", type=Path, required=True)
    resolve.add_argument("--event-name", required=True)
    resolve.add_argument("--staging-deploy-run-id", default="")
    resolve.add_argument("--staging-deployment-id", default="")
    resolve.add_argument("--output", type=Path, required=True)
    resolve.add_argument("--github-output", type=Path)
    resolve.set_defaults(func=command_resolve)

    identity = subparsers.add_parser("check-identity")
    identity.add_argument("--deployment-evidence", type=Path, required=True)
    identity.add_argument("--output", type=Path, required=True)
    identity.set_defaults(func=command_identity)

    record = subparsers.add_parser("write-record")
    record.add_argument("--deployment-evidence", type=Path, required=True)
    record.add_argument("--pre-identity", type=Path, required=True)
    record.add_argument("--post-identity", type=Path, required=True)
    record.add_argument("--app-report", type=Path, required=True)
    record.add_argument("--started-at", required=True)
    record.add_argument("--completed-at", required=True)
    record.add_argument("--qualifier-commit", required=True)
    record.add_argument("--qualifier-ref", required=True)
    record.add_argument("--qualifier-workflow-ref", required=True)
    record.add_argument("--qualifier-run-id", required=True)
    record.add_argument("--qualifier-run-attempt", required=True)
    record.add_argument("--output", type=Path, required=True)
    record.set_defaults(func=command_record)

    validate = subparsers.add_parser("validate-record")
    validate.add_argument("--record", type=Path, required=True)
    validate.add_argument("--expected-image-digest")
    validate.add_argument("--expected-staging-deployment-id")
    validate.add_argument("--verified-attestation", type=Path)
    validate.set_defaults(func=command_validate)

    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        arguments.func(arguments)
    except QualificationError as error:
        raise SystemExit(f"Staging qualification rejected: {error}") from error


if __name__ == "__main__":
    main()
