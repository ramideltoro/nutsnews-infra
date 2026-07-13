#!/usr/bin/env python3
"""Block committed NutsNews app secret material and common app secret placeholders."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ALLOWED_PREFIXES = (
    "NUTSNEWS_APP_ENABLED",
    "NUTSNEWS_APP_ENVIRONMENT",
    "NUTSNEWS_APP_PROJECT_NAME",
    "NUTSNEWS_APP_STAGED_ROUTE_ENABLED",
    "NUTSNEWS_APP_PUBLIC_ROUTE_ENABLED",
    "NUTSNEWS_APP_PUBLIC_DOMAIN",
    "NUTSNEWS_APP_CONTAINER_NAME",
    "NUTSNEWS_APP_CONTAINER_PORT",
    "NUTSNEWS_APP_IMAGE",
    "NUTSNEWS_APP_IMAGE_REPO",
    "NUTSNEWS_APP_IMAGE_DIGEST",
    "NUTSNEWS_APP_IMAGE_REVIEW_STATUS",
    "NUTSNEWS_APP_SOURCE_COMMIT",
    "NUTSNEWS_APP_BUILD_ID",
    "NUTSNEWS_APP_DEPLOYMENT_TARGET",
    "NUTSNEWS_APP_LAST_KNOWN_GOOD_DIGEST",
    "NUTSNEWS_APP_HEALTH_PATH",
    "NUTSNEWS_APP_ROUTE_PATH",
    "NUTSNEWS_APP_ENV_FILE",
    "NUTSNEWS_APP_NETWORK_NAME",
    "NUTSNEWS_APP_NETWORK_ALIAS",
    "NUTSNEWS_APP_CACHE_VOLUME_NAME",
    "NUTSNEWS_APP_SECRET_ENV_KEYS",
    "NUTSNEWS_APP_REQUIRED_SECRET_KEYS",
)


TRACKED_FILE_PATTERNS = (
    re.compile(r"^ansible/roles/.+"),
    re.compile(r"^compose/.+"),
    re.compile(r"^portal/.+"),
)

FORBIDDEN_ASSIGNMENT = re.compile(
    r"""(?im)^
        (?P<key>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<value>.+)$
    """.replace(" ", ""),
    re.VERBOSE,
)

SUSPICIOUS_KEY_HINTS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PRIVATE_KEY",
    "API_KEY",
)


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files"], text=True)
    return [Path(line) for line in output.splitlines() if line.strip()]


def is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def is_allowed_prefix(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def value_is_placeholder(value: str) -> bool:
    value = value.strip().strip("'\"")
    lowered = value.lower()
    return (
        not value
        or value.startswith("${")
        or value.startswith("{{")
        or value in {"true", "false", "yes", "no", "0", "1"}
        or "CHANGE_ME" in value
        or "placeholder" in lowered
        or value == "NUTSNEWS_APP_ENV_FILE"
    )


def is_suspicious_key(key: str) -> bool:
    return key.startswith("NUTSNEWS_APP_") and (key not in ALLOWED_PREFIXES) and any(
        hint in key for hint in SUSPICIOUS_KEY_HINTS
    )


def main() -> None:
    violations: list[str] = []

    for path in tracked_files():
        if not path.exists() or not is_text(path):
            continue
        path_str = str(path)
        if not any(pattern.search(path_str) for pattern in TRACKED_FILE_PATTERNS):
            continue

        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            match = FORBIDDEN_ASSIGNMENT.search(line)
            if not match:
                continue

            key = match.group("key")
            value = match.group("value").strip()
            if not key.startswith("NUTSNEWS_APP_"):
                continue
            if is_allowed_prefix(key) or not is_suspicious_key(key):
                continue
            if value_is_placeholder(value):
                continue
            violations.append(f"{path_str}: {line}")

    if violations:
        raise SystemExit("Committed NutsNews app secret-like values found:\n" + "\n".join(violations))

    print("NutsNews app secret guard check passed.")


if __name__ == "__main__":
    main()
