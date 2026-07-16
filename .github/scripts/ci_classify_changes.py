#!/usr/bin/env python3
"""Classify changed paths for CI cost controls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ZERO_SHA = "0" * 40


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line for line in result.stdout.splitlines() if line]


def changed_paths() -> tuple[list[str], str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}

    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
        return sorted(git_lines("diff", "--name-only", f"{base}...{head}")), f"{base[:12]}...{head[:12]}"

    if event_name == "push":
        before = event.get("before") or ""
        after = event.get("after") or os.environ.get("GITHUB_SHA", "")
        if before and before != ZERO_SHA and after:
            return sorted(git_lines("diff", "--name-only", before, after)), f"{before[:12]}..{after[:12]}"

    return sorted(git_lines("ls-files")), "full repository"


def starts(path: str, *prefixes: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def is_markdown_doc(path: str) -> bool:
    return path.endswith(".md") or path in {"AGENTS.md"}


def classify(paths: list[str]) -> dict[str, bool]:
    docs = all(starts(path, "docs/", "runbooks/") or is_markdown_doc(path) for path in paths)
    workflows = any(starts(path, ".github/workflows/", ".github/actions/", ".github/scripts/") for path in paths)
    ci_config = any(starts(path, ".github/requirements/") for path in paths)
    terraform = any(starts(path, "terraform/") or path.endswith((".tf", ".tfvars")) for path in paths)
    ansible = any(starts(path, "ansible/") for path in paths)
    portal = any(starts(path, "portal/") for path in paths)
    runtime = any(
        starts(path, "compose/")
        or path.endswith(("Dockerfile", ".Dockerfile"))
        or path
        in {
            "ansible/inventories/production/host_vars/vps.nutsnews.com.yml",
            "ansible/roles/vps_service_foundation/templates/nutsnews-app.env.j2",
            "ansible/roles/vps_service_foundation/templates/nutsnews-app.routes.j2",
            "ansible/roles/vps_service_foundation/templates/nutsnews-app.public.routes.j2",
        }
        for path in paths
    )
    dependency = any(
        path.endswith(
            (
                "requirements.txt",
                "requirements.yml",
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "Dockerfile",
                ".Dockerfile",
            )
        )
        or starts(path, ".github/requirements/")
        for path in paths
    )
    config = any(path.endswith((".yml", ".yaml", ".json", ".toml", ".tf", ".tfvars")) for path in paths)

    return {
        "docs_only": bool(paths) and docs,
        "workflows": workflows,
        "ci_config": ci_config,
        "terraform": terraform,
        "ansible": ansible,
        "portal": portal,
        "runtime": runtime,
        "dependency": dependency,
        "config": config,
    }


def emit_output(name: str, value: object) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    text = "true" if value is True else "false" if value is False else str(value)
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={text}\n")
    else:
        print(f"{name}={text}")


def append_summary(paths: list[str], diff_ref: str, labels: dict[str, bool]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    changed_preview = "\n".join(f"- `{path}`" for path in paths[:25])
    if len(paths) > 25:
        changed_preview += f"\n- ... {len(paths) - 25} more"

    active = ", ".join(name for name, enabled in labels.items() if enabled) or "none"
    summary = [
        "## CI path classification",
        "",
        f"- Diff: `{diff_ref}`",
        f"- Changed files: `{len(paths)}`",
        f"- Active categories: {active}",
        f"- Docs/runbooks-only: `{str(labels['docs_only']).lower()}`",
        "",
        "Changed file sample:",
        changed_preview or "- No changed files detected; conservative full-run defaults apply.",
    ]
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(summary) + "\n")


def main() -> int:
    paths, diff_ref = changed_paths()
    labels = classify(paths)

    docs_only = labels["docs_only"]
    workflow_or_ci = labels["workflows"] or labels["ci_config"]
    run_yaml = (not docs_only) or labels["config"] or workflow_or_ci
    run_terraform = labels["terraform"] or workflow_or_ci
    run_ansible = labels["ansible"] or labels["runtime"] or labels["portal"] or workflow_or_ci
    run_checkov = not docs_only
    run_runtime = labels["runtime"] or labels["ansible"] or labels["portal"] or workflow_or_ci
    run_portal = labels["portal"] or labels["ansible"] or labels["runtime"] or workflow_or_ci
    run_supply_chain = not docs_only

    outputs = {
        **labels,
        "changed_count": len(paths),
        "run_yaml": run_yaml,
        "run_terraform": run_terraform,
        "run_ansible": run_ansible,
        "run_checkov": run_checkov,
        "run_runtime": run_runtime,
        "run_portal": run_portal,
        "run_supply_chain": run_supply_chain,
    }
    for name, value in outputs.items():
        emit_output(name, value)

    append_summary(paths, diff_ref, labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
