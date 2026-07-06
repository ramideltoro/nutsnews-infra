#!/usr/bin/env python3
"""Block committed backup secrets and raw rclone OAuth material."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


FORBIDDEN = [
    re.compile(r"(?im)^token\s*=\s*\{"),
    re.compile(r"(?im)^refresh_token\s*="),
    re.compile(r"(?im)^client_secret\s*=\s*\S+"),
    re.compile(r"(?im)^RESTIC_PASSWORD\s*="),
    re.compile(r"(?im)^NUTSNEWS_BACKUP_RESTIC_PASSWORD\s*=\s*\S+"),
]


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files"], text=True)
    return [Path(line) for line in output.splitlines() if line.strip()]


def is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


violations: list[str] = []
for path in tracked_files():
    if not path.exists() or not is_text(path):
        continue
    text = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        if pattern.search(text):
            violations.append(f"{path}: matched {pattern.pattern}")

if violations:
    raise SystemExit("Committed backup secret material found:\n" + "\n".join(violations))

print("Backup secret safety check passed.")
