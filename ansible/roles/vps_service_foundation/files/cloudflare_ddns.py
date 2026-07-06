#!/usr/bin/env python3
"""Update the NutsNews Cloudflare A record when the VPS IPv4 changes."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
)


class DdnsError(RuntimeError):
    pass


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def bool_env(name: str, default: bool = False) -> bool:
    value = env(name)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise DdnsError(f"Missing required environment variable: {name}")
    return value


def api_request(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise DdnsError(f"Cloudflare API returned HTTP {error.code}: {detail}") from error
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise DdnsError(f"Cloudflare API request failed: {error}") from error

    if not data.get("success"):
        raise DdnsError(f"Cloudflare API reported failure: {data.get('errors', [])}")

    return data


def get_public_ipv4(endpoints: tuple[str, ...]) -> str:
    failures: list[str] = []

    for endpoint in endpoints:
        try:
            with urllib.request.urlopen(endpoint, timeout=15) as response:
                candidate = response.read().decode("utf-8").strip()
        except (TimeoutError, urllib.error.URLError) as error:
            failures.append(f"{endpoint}: {error}")
            continue

        parts = candidate.split(".")
        if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
            return candidate

        failures.append(f"{endpoint}: invalid IPv4 response")

    raise DdnsError("Could not determine public IPv4. " + " | ".join(failures))


def get_zone_id(token: str, zone_name: str) -> str:
    query = urllib.parse.urlencode({"name": zone_name, "status": "active", "per_page": "50"})
    data = api_request(token, "GET", f"/zones?{query}")
    zones = data.get("result", [])

    if len(zones) != 1:
        raise DdnsError(f"Expected exactly one active Cloudflare zone named {zone_name}, found {len(zones)}.")

    return zones[0]["id"]


def get_a_records(token: str, zone_id: str, record_name: str) -> list[dict]:
    query = urllib.parse.urlencode({"type": "A", "name": record_name, "per_page": "100"})
    data = api_request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    return data.get("result", [])


def create_record(token: str, zone_id: str, record_name: str, ip: str, ttl: int, proxied: bool) -> None:
    api_request(
        token,
        "POST",
        f"/zones/{zone_id}/dns_records",
        {
            "type": "A",
            "name": record_name,
            "content": ip,
            "ttl": ttl,
            "proxied": proxied,
            "comment": "Managed by NutsNews VPS Cloudflare DDNS",
        },
    )
    print(f"created {record_name} A record -> {ip} proxied={proxied} ttl={ttl}")


def update_record(token: str, zone_id: str, record: dict, record_name: str, ip: str, ttl: int, proxied: bool) -> None:
    api_request(
        token,
        "PATCH",
        f"/zones/{zone_id}/dns_records/{record['id']}",
        {
            "type": "A",
            "name": record_name,
            "content": ip,
            "ttl": ttl,
            "proxied": proxied,
            "comment": "Managed by NutsNews VPS Cloudflare DDNS",
        },
    )
    print(f"updated {record_name} A record {record.get('content')} -> {ip} proxied={proxied} ttl={ttl}")


def sanitized_record(record: dict) -> dict:
    return {
        "id": record.get("id", ""),
        "type": record.get("type", ""),
        "name": record.get("name", ""),
        "content": record.get("content", ""),
        "ttl": record.get("ttl", ""),
        "proxied": record.get("proxied", ""),
        "comment": record.get("comment", ""),
        "created_on": record.get("created_on", ""),
        "modified_on": record.get("modified_on", ""),
    }


def main() -> int:
    token = require_env("CLOUDFLARE_API_TOKEN")
    zone_name = require_env("CLOUDFLARE_ZONE_NAME")
    record_name = require_env("CLOUDFLARE_RECORD_NAME")
    ttl = int(env("CLOUDFLARE_RECORD_TTL", "60"))
    proxied = bool_env("CLOUDFLARE_RECORD_PROXIED", False)
    dry_run = bool_env("CLOUDFLARE_DDNS_DRY_RUN", False)
    inspect_only = bool_env("CLOUDFLARE_DDNS_INSPECT_ONLY", False)

    endpoints = tuple(
        item.strip()
        for item in env("CLOUDFLARE_DDNS_IP_ENDPOINTS", ",".join(DEFAULT_IP_ENDPOINTS)).split(",")
        if item.strip()
    )

    zone_id = get_zone_id(token, zone_name)
    records = get_a_records(token, zone_id, record_name)

    if inspect_only:
        print(
            json.dumps(
                {
                    "record_name": record_name,
                    "records": [sanitized_record(record) for record in records],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    public_ip = get_public_ipv4(endpoints)

    if len(records) > 1:
        raise DdnsError(f"Refusing to manage {record_name}: found {len(records)} A records.")

    if not records:
        if dry_run:
            print(f"would create {record_name} A record -> {public_ip} proxied={proxied} ttl={ttl}")
            return 0
        create_record(token, zone_id, record_name, public_ip, ttl, proxied)
        return 0

    record = records[0]
    current_ip = record.get("content", "")

    if current_ip == public_ip:
        print(f"unchanged {record_name} A record -> {public_ip}")
        return 0

    if dry_run:
        print(f"would update {record_name} A record {current_ip} -> {public_ip} proxied={proxied} ttl={ttl}")
        return 0

    update_record(token, zone_id, record, record_name, public_ip, ttl, proxied)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DdnsError as error:
        print(f"cloudflare-ddns error: {error}", file=sys.stderr)
        raise SystemExit(1)
