#!/usr/bin/env python3
"""Fetch, classify, fingerprint, and diff the reviewed Vercel-to-VPS env set.

Secret values are read only in memory or in caller-created mode-0600 temporary
files. This program never prints values; its human-readable output contains
only variable names, categories, and counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NoReturn


KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SYNC_CATEGORIES = {"safe_to_synchronize", "server_side_secret"}
EXCLUDED_CATEGORIES = {"vercel_platform_only", "preview_development_only"}
REVIEW_CATEGORY = "manual_review"
SECRET_TYPES = {"encrypted", "secret", "sensitive"}
ENVELOPE_KEYS = {"encrypted", "ciphertext", "encryptedvalue", "vsmvalue", "keyid"}
GOOGLE_CLIENT_ID_RE = re.compile(r"^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RUNTIME_AUTH_SECRET_LENGTH = 512
BACKEND_POSTGRES_PRIMARY_CONFIRMATION = "enable-backend-postgres-primary"
PROVIDER_SWITCH_CONFIRMATIONS = {
    "deploy-supabase-primary",
    BACKEND_POSTGRES_PRIMARY_CONFIRMATION,
}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Unable to read valid JSON from {path}: {exc.__class__.__name__}.")


def load_mapping(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if not isinstance(data, dict):
        fail("Environment sync mapping must be a JSON object.")
    source = data.get("source")
    target = data.get("target")
    if not isinstance(source, dict) or source.get("provider") != "vercel" or source.get("environment") != "production":
        fail("Environment sync mapping must select Vercel Production as its source.")
    if not isinstance(target, dict) or target.get("environment") != "production":
        fail("Environment sync mapping must select VPS production as its target.")

    variables = data.get("variables")
    patterns = data.get("patterns")
    if not isinstance(variables, dict) or not isinstance(patterns, list):
        fail("Environment sync mapping requires variables and patterns.")

    destinations: set[str] = set()
    for name, rule in variables.items():
        if not KEY_RE.fullmatch(name) or not isinstance(rule, dict):
            fail(f"Invalid exact variable rule: {name!r}.")
        validate_rule(name, rule)
        if rule.get("sync"):
            for destination in rule_destinations(rule):
                if destination in destinations:
                    fail(f"Duplicate synchronization destination: {destination}.")
                destinations.add(destination)

    for index, rule in enumerate(patterns):
        if not isinstance(rule, dict):
            fail(f"Invalid variable pattern at index {index}.")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            fail(f"Variable pattern {index} is missing a string pattern.")
        try:
            re.compile(pattern)
        except re.error as exc:
            fail(f"Invalid variable pattern {index}: {exc}.")
        validate_rule(f"pattern {index}", rule, require_destination=False)
    return data


def validate_rule(label: str, rule: dict[str, Any], *, require_destination: bool = True) -> None:
    category = rule.get("category")
    if category not in SYNC_CATEGORIES | EXCLUDED_CATEGORIES | {REVIEW_CATEGORY}:
        fail(f"{label} has an unsupported classification.")
    if not isinstance(rule.get("sync"), bool):
        fail(f"{label} must explicitly set sync true or false.")
    if rule["sync"]:
        if require_destination and not rule_destinations(rule):
            fail(f"{label} requires a valid destination when sync is true.")
        if category not in SYNC_CATEGORIES:
            fail(f"{label} cannot synchronize under category {category}.")
    elif "destination" in rule and rule.get("destination") is not None:
        if not isinstance(rule["destination"], str) or not KEY_RE.fullmatch(rule["destination"]):
            fail(f"{label} has an invalid destination.")
    if "destinations" in rule:
        destinations = rule["destinations"]
        if not isinstance(destinations, list) or not destinations or not all(
            isinstance(destination, str) and KEY_RE.fullmatch(destination) for destination in destinations
        ):
            fail(f"{label} has an invalid destinations list.")
        if "destination" in rule:
            fail(f"{label} cannot define both destination and destinations.")
    if not isinstance(rule.get("reason"), str) or not rule["reason"].strip():
        fail(f"{label} must include a non-empty reason.")


def rule_destinations(rule: dict[str, Any]) -> list[str]:
    destinations = rule.get("destinations")
    if destinations is not None:
        return list(destinations) if isinstance(destinations, list) else []
    destination = rule.get("destination")
    return [destination] if isinstance(destination, str) else []


def rule_for(mapping: dict[str, Any], key: str) -> dict[str, Any] | None:
    exact = mapping["variables"].get(key)
    if exact is not None:
        return exact
    for rule in mapping["patterns"]:
        if re.search(rule["pattern"], key):
            return rule
    return None


def looks_like_encrypted_envelope(value: str) -> bool:
    """Recognize structured Vercel ciphertext without inspecting or printing it."""
    stripped = value.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and bool({str(key).lower() for key in parsed} & ENVELOPE_KEYS)


def usable_record_value(record: dict[str, Any]) -> str:
    key = record["key"]
    value = record.get("value")
    variable_type = record.get("type")
    decrypted = record.get("decrypted")
    if not isinstance(value, str) or not value.strip():
        fail(f"Vercel Production variable {key} has no usable decrypted value available to the sync credential.")
    if decrypted is False or (variable_type in SECRET_TYPES and decrypted is not True):
        fail(f"Vercel Production variable {key} was not returned as a decrypted value.")
    if looks_like_encrypted_envelope(value):
        fail(f"Vercel Production variable {key} contains an encrypted envelope, not plaintext.")
    if "\n" in value or "\r" in value:
        fail(f"Vercel Production variable {key} contains a newline and cannot be represented safely in the VPS env file.")
    return value


def validate_selected_values(selected: dict[str, str]) -> None:
    invalid: set[str] = set()
    for key in ("AUTH_GOOGLE_ID", "AUTH_GOOGLE_SECRET", "AUTH_SECRET"):
        value = selected.get(key, "")
        if not isinstance(value, str) or not value.strip():
            invalid.add(key)

    google_id = selected.get("AUTH_GOOGLE_ID", "")
    if google_id and not GOOGLE_CLIENT_ID_RE.fullmatch(google_id):
        invalid.add("AUTH_GOOGLE_ID")

    google_secret = selected.get("AUTH_GOOGLE_SECRET", "")
    if google_secret and (
        looks_like_encrypted_envelope(google_secret)
        or len(google_secret) < 8
        or len(google_secret) > MAX_RUNTIME_AUTH_SECRET_LENGTH
    ):
        invalid.add("AUTH_GOOGLE_SECRET")

    auth_secret = selected.get("AUTH_SECRET", "")
    if auth_secret and (
        looks_like_encrypted_envelope(auth_secret)
        or len(auth_secret) < 32
        or len(auth_secret) > MAX_RUNTIME_AUTH_SECRET_LENGTH
    ):
        invalid.add("AUTH_SECRET")

    admin_emails = selected.get("ADMIN_EMAILS", "")
    if admin_emails and not all(EMAIL_RE.fullmatch(email.strip()) for email in admin_emails.split(",")):
        invalid.add("ADMIN_EMAILS")

    database_provider_mode = selected.get("NUTSNEWS_DATABASE_PROVIDER_MODE", "")
    if database_provider_mode and database_provider_mode not in {"supabase_primary", "backend_postgres_primary"}:
        invalid.add("NUTSNEWS_DATABASE_PROVIDER_MODE")

    backend_api_url = selected.get("NUTSNEWS_BACKEND_API_URL", "")
    if backend_api_url:
        parsed_backend_url = urllib.parse.urlparse(backend_api_url)
        if (
            parsed_backend_url.scheme != "https"
            or parsed_backend_url.netloc != "backend.nutsnews.com"
            or parsed_backend_url.path.rstrip("/") != "/api/app/db"
        ):
            invalid.add("NUTSNEWS_BACKEND_API_URL")
    backend_api_token = selected.get("NUTSNEWS_BACKEND_API_TOKEN", "")
    backend_primary_confirmation = selected.get("NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION", "")
    if backend_primary_confirmation and backend_primary_confirmation not in PROVIDER_SWITCH_CONFIRMATIONS:
        invalid.add("NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION")
    if database_provider_mode == "backend_postgres_primary" and not backend_api_url:
        invalid.add("NUTSNEWS_BACKEND_API_URL")
    if database_provider_mode == "backend_postgres_primary" and not backend_api_token:
        invalid.add("NUTSNEWS_BACKEND_API_TOKEN")
    if (
        database_provider_mode == "backend_postgres_primary"
        and backend_primary_confirmation != BACKEND_POSTGRES_PRIMARY_CONFIRMATION
    ):
        invalid.add("NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION")

    if invalid:
        fail("Invalid synchronized Vercel Production values for: " + ", ".join(sorted(invalid)) + ".")


def api_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("envs", payload.get("data"))
    else:
        records = None
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        fail("Vercel environment response did not contain a variable list.")
    return records


def production_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("key")
        target = record.get("target", [])
        targets = [target] if isinstance(target, str) else target
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            fail("Vercel returned an environment variable with an invalid name.")
        if not isinstance(targets, list):
            fail(f"Vercel variable {key} has an invalid target classification.")
        if "production" not in targets:
            continue
        if key in selected:
            fail(f"Vercel returned duplicate Production records for {key}.")
        selected[key] = record
    return list(selected.values())


def classify_records(records: list[dict[str, Any]], mapping: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[str]]]:
    selected: dict[str, str] = {}
    report = {"safe_to_synchronize": [], "server_side_secret": [], "vercel_platform_only": [], "preview_development_only": [], "manual_review": []}
    unclassified: list[str] = []
    manual_review: list[str] = []
    for record in production_records(records):
        key = record["key"]
        rule = rule_for(mapping, key)
        if rule is None:
            unclassified.append(key)
            continue
        category = rule["category"]
        report[category].append(key)
        if category == REVIEW_CATEGORY:
            manual_review.append(key)
            continue
        if not rule["sync"]:
            continue
        value = usable_record_value(record)
        for destination in rule_destinations(rule):
            if destination in selected:
                fail(f"Multiple Vercel variables map to VPS destination {destination}.")
            selected[destination] = value
    if unclassified:
        fail(
            "Unclassified Vercel Production variables: "
            + ", ".join(sorted(unclassified))
            + "; add explicit mapping rules before syncing."
        )
    if manual_review:
        fail(
            "Vercel Production variables require manual review: "
            + ", ".join(sorted(manual_review))
            + "; sync is stopped safely."
        )
    return selected, report


def fetch_json(url: str, token: str, variable_name: str | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if variable_name and exc.code == 403:
            fail(
                "Vercel denied decrypted access for Production variable "
                f"{variable_name}; the protected Vercel token needs project/team "
                "permission to read environment variable secrets."
            )
        if variable_name:
            fail(
                f"Vercel could not retrieve decrypted Production variable {variable_name} "
                f"(HTTP {exc.code}); no response body was printed."
            )
        fail(f"Vercel environment API request failed with HTTP {exc.code}; no response body was printed.")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        if variable_name:
            fail(
                f"Vercel could not retrieve decrypted Production variable {variable_name}; "
                "no response body was printed."
            )
        fail("Vercel environment API request failed; no response body was printed.")


def fetch_payload(mapping: dict[str, Any]) -> Any:
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    project_id = os.environ.get("VERCEL_PROJECT_ID", "").strip()
    team_id = os.environ.get("VERCEL_TEAM_ID", "").strip()
    missing = [name for name, value in (("VERCEL_TOKEN", token), ("VERCEL_PROJECT_ID", project_id), ("VERCEL_TEAM_ID", team_id)) if not value]
    if missing:
        fail("Missing required Vercel sync credential or identifier: " + ", ".join(missing) + ".")
    project_path = urllib.parse.quote(project_id, safe="")
    list_query = urllib.parse.urlencode({"teamId": team_id, "source": "vercel-vps-env-sync"})
    list_url = f"https://api.vercel.com/v10/projects/{project_path}/env?{list_query}"
    metadata = production_records(api_records(fetch_json(list_url, token)))

    decrypted_records: list[dict[str, Any]] = []
    for record in metadata:
        key = record["key"]
        rule = rule_for(mapping, key)
        # Do not request secret material for excluded, manual-review, or
        # unclassified variables. Classification will report those names
        # without allowing their values to enter the sync selection.
        if rule is None or not rule["sync"]:
            decrypted_records.append(
                {
                    "key": key,
                    "target": record.get("target", []),
                    "type": record.get("type"),
                    "decrypted": record.get("decrypted"),
                }
            )
            continue
        environment_id = record.get("id")
        if not isinstance(environment_id, str) or not environment_id:
            fail(f"Vercel Production variable {key} has no environment-variable ID for decrypted retrieval.")
        detail_query = urllib.parse.urlencode({"teamId": team_id})
        detail_url = (
            f"https://api.vercel.com/v1/projects/{project_path}/env/"
            f"{urllib.parse.quote(environment_id, safe='')}?{detail_query}"
        )
        detail = fetch_json(detail_url, token, key)
        if not isinstance(detail, dict):
            fail(f"Vercel decrypted response for Production variable {key} was not an object.")
        decrypted_records.append(
            {
                "key": key,
                "target": record.get("target", []),
                "type": detail.get("type", record.get("type")),
                "decrypted": detail.get("decrypted"),
                # Do not fall back to the list response: it may contain ciphertext.
                "value": detail.get("value"),
            }
        )
    return decrypted_records


def write_private_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True) + "\n")
    except OSError as exc:
        fail(f"Unable to write the private temporary sync file: {exc.__class__.__name__}.")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_fingerprints(path: Path) -> dict[str, str]:
    data = load_json(path)
    variables = data.get("variables") if isinstance(data, dict) else None
    if not isinstance(variables, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in variables.items()):
        fail("VPS fingerprint input is not a name-to-hash JSON object.")
    return variables


def print_diff(selected: dict[str, str], target: dict[str, str], mapping: dict[str, Any], report: dict[str, list[str]]) -> bool:
    managed = {
        destination
        for rule in mapping["variables"].values()
        if rule.get("sync")
        for destination in rule_destinations(rule)
    }
    desired_hashes = {key: sha256(value) for key, value in selected.items()}
    added = sorted(key for key in desired_hashes if key not in target)
    changed = sorted(key for key in desired_hashes if key in target and target[key] != desired_hashes[key])
    removed = sorted(key for key in target if key in managed and key not in desired_hashes)
    for label, names in (("added", added), ("changed", changed), ("removed", removed)):
        for name in names:
            print(f"{label}: {name}")
    for category in ("vercel_platform_only", "preview_development_only", "server_side_secret"):
        for name in sorted(report[category]):
            rule = rule_for(mapping, name)
            if rule is not None and not rule["sync"]:
                print(f"excluded ({category}): {name}")
    for name in sorted(report[REVIEW_CATEGORY]):
        print(f"manual-review: {name}")
    if not (added or changed or removed):
        print("No synchronized variable changes detected.")
    return bool(added or changed or removed)


def parse_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        fail(f"Unable to read the VPS env file: {exc.__class__.__name__}.")
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            fail(f"Invalid VPS env file line {line_number}.")
        key, raw = line.split("=", 1)
        key = key.strip()
        if not KEY_RE.fullmatch(key):
            fail(f"Invalid VPS env variable name on line {line_number}.")
        try:
            parsed = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            fail(f"Invalid VPS env value quoting on line {line_number}.")
        values[key] = parsed[0] if len(parsed) == 1 else raw.strip().strip('"')
    return values


def command_fetch(args: argparse.Namespace) -> None:
    mapping = load_mapping(Path(args.mapping))
    selected, report = classify_records(api_records(fetch_payload(mapping)), mapping)
    validate_selected_values(selected)
    write_private_json(Path(args.output), selected)
    write_private_json(Path(args.report_output), report)
    print(
        "Fetched and classified Vercel Production variables: "
        f"selected={len(selected)} "
        f"excluded={len(report['vercel_platform_only']) + len(report['preview_development_only']) + len(report['server_side_secret'])} "
        f"manual_review={len(report['manual_review'])}."
    )
    for category in ("vercel_platform_only", "preview_development_only", "server_side_secret"):
        for name in sorted(report[category]):
            rule = rule_for(mapping, name)
            if rule is not None and not rule["sync"]:
                print(f"excluded ({category}): {name}")


def command_diff(args: argparse.Namespace) -> None:
    mapping = load_mapping(Path(args.mapping))
    selected = load_json(Path(args.selected))
    if not isinstance(selected, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in selected.items()):
        fail("Selected sync input is not a name-to-value JSON object.")
    target = read_fingerprints(Path(args.target))
    report = load_json(Path(args.report))
    if not isinstance(report, dict):
        fail("Classification report is not a JSON object.")
    for category in ("safe_to_synchronize", "server_side_secret", "vercel_platform_only", "preview_development_only", "manual_review"):
        if not isinstance(report.get(category), list):
            fail("Classification report is missing a category.")
    print_diff(selected, target, mapping, report)


def command_fingerprint(args: argparse.Namespace) -> None:
    values = parse_env_file(Path(args.path))
    print(json.dumps({"variables": {key: sha256(value) for key, value in sorted(values.items())}}))


def command_validate_credentials(_: argparse.Namespace) -> None:
    for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID"):
        if not os.environ.get(name, "").strip():
            fail(f"Missing required Vercel sync credential or identifier: {name}.")
    print("Required Vercel sync credentials and identifiers are present.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--mapping", required=True)
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--report-output", required=True)
    fetch.set_defaults(function=command_fetch)
    diff = subparsers.add_parser("diff")
    diff.add_argument("--mapping", required=True)
    diff.add_argument("--selected", required=True)
    diff.add_argument("--target", required=True)
    diff.add_argument("--report", required=True)
    diff.set_defaults(function=command_diff)
    fingerprint = subparsers.add_parser("fingerprint")
    fingerprint.add_argument("--path", required=True)
    fingerprint.set_defaults(function=command_fingerprint)
    credentials = subparsers.add_parser("validate-credentials")
    credentials.set_defaults(function=command_validate_credentials)
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    arguments.function(arguments)
