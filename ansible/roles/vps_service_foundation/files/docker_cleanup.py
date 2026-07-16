#!/usr/bin/env python3
"""Conservative Docker image and build-cache cleanup for the VPS."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FILE = Path(os.environ.get("NUTSNEWS_DOCKER_CLEANUP_STATUS_FILE", "/opt/nutsnews/portal-assets/data/docker-cleanup-status.json"))
LOG_FILE = Path(os.environ.get("NUTSNEWS_DOCKER_CLEANUP_LOG_FILE", "/opt/nutsnews/logs/docker-cleanup/cleanup.jsonl"))
IMAGE_UNTIL = os.environ.get("NUTSNEWS_DOCKER_CLEANUP_IMAGE_UNTIL", "168h").strip() or "168h"
BUILD_CACHE_UNTIL = os.environ.get("NUTSNEWS_DOCKER_CLEANUP_BUILD_CACHE_UNTIL", "168h").strip() or "168h"
PROTECTED_REFS = [
    item.strip()
    for item in os.environ.get("NUTSNEWS_DOCKER_CLEANUP_PROTECTED_IMAGE_REFS", "").split(",")
    if item.strip()
]
AGE_RE = re.compile(r"^[0-9]+(h|m|s)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(argv: list[str], timeout: int = 120, output_limit: int = 2000) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "stdout": "", "stderr": "command timed out"}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[:output_limit],
        "stderr": result.stderr.strip()[:output_limit],
    }


def inspect_images(refs: list[str]) -> list[dict[str, Any]]:
    if not refs:
        return []
    result = run(["docker", "image", "inspect", *refs], timeout=30, output_limit=200000)
    if not result["ok"]:
        return []
    try:
        data = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def running_image_ids() -> set[str]:
    result = run(["docker", "ps", "--format", "{{.Image}}"], timeout=20)
    refs = [line.strip() for line in result["stdout"].splitlines() if line.strip()] if result["ok"] else []
    return {item.get("Id", "") for item in inspect_images(refs) if item.get("Id")}


def protected_image_state(running_ids: set[str]) -> dict[str, Any]:
    inspected = inspect_images(PROTECTED_REFS)
    missing = sorted(set(PROTECTED_REFS) - {tag for item in inspected for tag in item.get("RepoTags", []) + item.get("RepoDigests", [])})
    unsafe: list[str] = []
    protected = []
    for item in inspected:
        image_id = str(item.get("Id", ""))
        repo_tags = [tag for tag in item.get("RepoTags", []) if tag and tag != "<none>:<none>"]
        repo_digests = [digest for digest in item.get("RepoDigests", []) if digest and not digest.startswith("<none>")]
        is_running = image_id in running_ids
        is_untagged = not repo_tags
        if image_id and is_untagged and not is_running:
            unsafe.append(image_id)
        protected.append(
            {
                "id": image_id,
                "running": is_running,
                "tagged": bool(repo_tags),
                "has_digest": bool(repo_digests),
            }
        )
    return {"protected": protected, "missing_refs": missing, "unsafe_untagged_ids": unsafe}


def docker_df() -> str:
    result = run(["docker", "system", "df"], timeout=30)
    return result["stdout"] if result["ok"] else ""


def write_status(status: dict[str, Any]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(status, sort_keys=True) + "\n")


def main() -> int:
    started_at = utc_now()
    if not AGE_RE.match(IMAGE_UNTIL) or not AGE_RE.match(BUILD_CACHE_UNTIL):
        status = {
            "schema_version": 1,
            "status": "failed",
            "ok": False,
            "started_at": started_at,
            "completed_at": utc_now(),
            "error": "invalid_age_filter",
        }
        write_status(status)
        return 2

    before = docker_df()
    running_ids = running_image_ids()
    protection = protected_image_state(running_ids)

    build_prune = run(["docker", "builder", "prune", "--force", "--filter", f"until={BUILD_CACHE_UNTIL}"])
    image_prune: dict[str, Any]
    if protection["unsafe_untagged_ids"]:
        image_prune = {
            "ok": True,
            "skipped": True,
            "reason": "protected_untagged_image_present",
            "protected_count": len(protection["unsafe_untagged_ids"]),
        }
    else:
        image_prune = run(["docker", "image", "prune", "--force", "--filter", f"until={IMAGE_UNTIL}"])
        image_prune["skipped"] = False

    status = {
        "schema_version": 1,
        "ok": bool(build_prune.get("ok")) and bool(image_prune.get("ok")),
        "started_at": started_at,
        "completed_at": utc_now(),
        "filters": {"build_cache_until": BUILD_CACHE_UNTIL, "image_until": IMAGE_UNTIL},
        "before": before,
        "after": docker_df(),
        "protected_images": protection,
        "build_cache_prune": build_prune,
        "image_prune": image_prune,
    }
    status["status"] = "success" if status["ok"] else "failed"
    write_status(status)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
