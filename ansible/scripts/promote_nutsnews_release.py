#!/usr/bin/env python3
"""Validate and update the reviewed immutable NutsNews VPS release manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


IMAGE_REPOSITORY = "ghcr.io/ramideltoro/nutsnews"
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
BUILD_ID_RE = re.compile(r"[0-9]+-[0-9]+\Z")


class PromotionError(ValueError):
    """Raised when a release cannot safely become reviewed manifest state."""


def require_match(pattern: re.Pattern[str], value: str, label: str) -> str:
    if not pattern.fullmatch(value):
        raise PromotionError(f"{label} is invalid.")
    return value


def manifest_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_]+):\s*(.*?)\s*", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip("\"'")
    return values


def required_value(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value:
        raise PromotionError(f"Manifest is missing required value {name}.")
    return value


def validate_release(
    image_repository: str,
    image_digest: str,
    source_commit: str,
    build_id: str,
) -> dict[str, str]:
    if image_repository != IMAGE_REPOSITORY:
        raise PromotionError("Image repository is not the approved NutsNews GHCR repository.")
    return {
        "image_repository": image_repository,
        "image_digest": require_match(SHA256_RE, image_digest, "Image digest"),
        "source_commit": require_match(COMMIT_RE, source_commit, "Source commit"),
        "build_id": require_match(BUILD_ID_RE, build_id, "Build ID"),
    }


def validate_manifest(values: dict[str, str]) -> dict[str, str]:
    repository = required_value(values, "vps_service_foundation_nutsnews_app_image_repo")
    digest = required_value(values, "vps_service_foundation_nutsnews_app_image_digest")
    source_commit = required_value(values, "vps_service_foundation_nutsnews_app_source_commit")
    build_id = required_value(values, "vps_service_foundation_nutsnews_app_build_id")
    deployment_target = required_value(values, "vps_service_foundation_nutsnews_app_deployment_target")
    last_known_good = values.get("vps_service_foundation_nutsnews_app_last_known_good_digest", "")

    release = validate_release(repository, digest, source_commit, build_id)
    if deployment_target != "production-vps":
        raise PromotionError("Manifest deployment target must be production-vps.")
    if last_known_good:
        require_match(SHA256_RE, last_known_good, "Last-known-good image digest")
    release["deployment_target"] = deployment_target
    release["last_known_good_digest"] = last_known_good
    return release


def replace_value(text: str, name: str, value: str) -> str:
    replacement = f"{name}: {json.dumps(value)}"
    updated, count = re.subn(rf"(?m)^{re.escape(name)}:\s*.*$", replacement, text)
    if count != 1:
        raise PromotionError(f"Manifest must contain exactly one {name} entry.")
    return updated


def promote_manifest(
    manifest_path: Path,
    image_repository: str,
    image_digest: str,
    source_commit: str,
    build_id: str,
    *,
    write: bool,
) -> dict[str, str]:
    release = validate_release(image_repository, image_digest, source_commit, build_id)
    original = manifest_path.read_text(encoding="utf-8")
    current = validate_manifest(manifest_values(original))

    next_last_known_good = current["last_known_good_digest"]
    if current["image_digest"] != release["image_digest"]:
        next_last_known_good = current["image_digest"]

    updated = original
    for name, value in (
        ("vps_service_foundation_nutsnews_app_image_repo", release["image_repository"]),
        ("vps_service_foundation_nutsnews_app_image_digest", release["image_digest"]),
        ("vps_service_foundation_nutsnews_app_source_commit", release["source_commit"]),
        ("vps_service_foundation_nutsnews_app_build_id", release["build_id"]),
        ("vps_service_foundation_nutsnews_app_deployment_target", "production-vps"),
        ("vps_service_foundation_nutsnews_app_last_known_good_digest", next_last_known_good),
    ):
        updated = replace_value(updated, name, value)

    if write and updated != original:
        manifest_path.write_text(updated, encoding="utf-8")

    return {
        **release,
        "previous_digest": current["image_digest"],
        "last_known_good_digest": next_last_known_good,
        "changed": str(updated != original).lower(),
    }


def verify_manifest(
    manifest_path: Path,
    image_repository: str,
    image_digest: str,
    source_commit: str,
    build_id: str,
) -> dict[str, str]:
    expected = validate_release(image_repository, image_digest, source_commit, build_id)
    actual = validate_manifest(manifest_values(manifest_path.read_text(encoding="utf-8")))
    for name, expected_value in expected.items():
        if actual[name] != expected_value:
            raise PromotionError(f"Manifest {name} does not match the requested automated release.")
    return actual


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-repository", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--manifest", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if (args.verify or args.write) and args.manifest is None:
        raise PromotionError("--manifest is required for --verify and --write.")

    if args.validate_only:
        result = validate_release(
            args.image_repository,
            args.image_digest,
            args.source_commit,
            args.build_id,
        )
    elif args.verify:
        result = verify_manifest(
            args.manifest,
            args.image_repository,
            args.image_digest,
            args.source_commit,
            args.build_id,
        )
    else:
        result = promote_manifest(
            args.manifest,
            args.image_repository,
            args.image_digest,
            args.source_commit,
            args.build_id,
            write=True,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
