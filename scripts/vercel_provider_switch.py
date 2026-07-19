#!/usr/bin/env python3
"""Plan or apply the Vercel Production app DB provider switch.

The script intentionally manages only non-secret switch variables. Backend API
tokens remain in Vercel/Cloudflare secret stores and are not read or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn


PROVIDER_MODES = {"supabase_primary", "backend_postgres_primary"}
CONFIRMATIONS = {
    "supabase_primary": "deploy-supabase-primary",
    "backend_postgres_primary": "enable-backend-postgres-primary",
}
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD_ID_RE = re.compile(r"^[1-9][0-9]{0,19}-[1-9][0-9]{0,5}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_https_url(value: str, label: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        fail(f"{label} must be an https URL without credentials.")


def desired_variables(provider_mode: str, production_writes_paused: str, backend_api_url: str) -> dict[str, str]:
    return {
        "NUTSNEWS_DATABASE_PROVIDER_MODE": provider_mode,
        "NUTSNEWS_PRODUCTION_WRITES_PAUSED": production_writes_paused,
        "NUTSNEWS_BACKEND_API_URL": backend_api_url,
    }


def vercel_request(url: str, token: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        fail(f"Vercel provider switch API request failed with HTTP {exc.code}; response body was not printed.")
    except (TimeoutError, urllib.error.URLError):
        fail("Vercel provider switch API request failed; response body was not printed.")
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        fail("Vercel provider switch API returned non-JSON; response body was not printed.")


def apply_variables(variables: dict[str, str]) -> dict[str, Any]:
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    project_id = os.environ.get("VERCEL_PROJECT_ID", "").strip()
    team_id = os.environ.get("VERCEL_TEAM_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("VERCEL_TOKEN", token),
            ("VERCEL_PROJECT_ID", project_id),
            ("VERCEL_TEAM_ID", team_id),
        )
        if not value
    ]
    if missing:
        fail("Missing required Vercel provider switch credential or identifier: " + ", ".join(missing) + ".")

    project_path = urllib.parse.quote(project_id, safe="")
    query = urllib.parse.urlencode({"teamId": team_id, "upsert": "true", "source": "nutsnews-db-provider-cutover"})
    url = f"https://api.vercel.com/v10/projects/{project_path}/env?{query}"
    updated: list[str] = []
    for key, value in variables.items():
        if not KEY_RE.fullmatch(key):
            fail(f"Invalid Vercel environment variable name: {key}.")
        payload = {
            "key": key,
            "value": value,
            "type": "plain",
            "target": ["production"],
            "comment": "NutsNews backend PostgreSQL primary cutover provider switch.",
            "customEnvironmentIds": [],
        }
        response = vercel_request(url, token, method="POST", body=payload)
        failed = response.get("failed") if isinstance(response, dict) else None
        if failed:
            failed_keys = sorted(
                str(item.get("error", {}).get("key") or item.get("error", {}).get("envVarKey") or "unknown")
                for item in failed
                if isinstance(item, dict)
            )
            fail("Vercel rejected provider switch variables: " + ", ".join(failed_keys) + ".")
        updated.append(key)
    return {"updated_keys": sorted(updated)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=("plan", "apply"), default="plan")
    parser.add_argument("--database-provider-mode", required=True, choices=sorted(PROVIDER_MODES))
    parser.add_argument("--production-writes-paused", required=True, choices=("true", "false"))
    parser.add_argument("--backend-api-url", default="https://backend.nutsnews.com/api/app/db")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--image-digest", default="")
    parser.add_argument("--build-id", default="")
    parser.add_argument("--vps-apply-run-id", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    expected_confirmation = CONFIRMATIONS[args.database_provider_mode]
    blockers: list[str] = []
    if args.confirmation != expected_confirmation:
        blockers.append("missing_or_invalid_provider_switch_confirmation")
    validate_https_url(args.backend_api_url, "backend API URL")
    if args.operation == "apply":
        if not SHA_RE.fullmatch(args.source_commit):
            blockers.append("missing_or_invalid_source_commit")
        if not IMAGE_DIGEST_RE.fullmatch(args.image_digest):
            blockers.append("missing_or_invalid_image_digest")
        if not BUILD_ID_RE.fullmatch(args.build_id):
            blockers.append("missing_or_invalid_build_id")
        if not RUN_ID_RE.fullmatch(args.vps_apply_run_id):
            blockers.append("missing_or_invalid_vps_apply_run_id")

    variables = desired_variables(args.database_provider_mode, args.production_writes_paused, args.backend_api_url)
    apply_report: dict[str, Any] = {}
    if args.operation == "apply" and not blockers:
        apply_report = apply_variables(variables)

    report = {
        "status": "blocked" if blockers else "ready" if args.operation == "plan" else "applied",
        "checked_at_utc": utc_now(),
        "operation": args.operation,
        "database_provider_mode": args.database_provider_mode,
        "production_writes_paused": args.production_writes_paused == "true",
        "target": "vercel_production",
        "managed_keys": sorted(variables),
        "source_commit": args.source_commit or None,
        "image_digest_present": bool(args.image_digest),
        "build_id": args.build_id or None,
        "vps_apply_run_id": args.vps_apply_run_id or None,
        "mutation_performed": args.operation == "apply" and not blockers,
        "blockers": blockers,
        "safe_metadata_only": True,
        **apply_report,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
