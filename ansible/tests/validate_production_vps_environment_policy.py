#!/usr/bin/env python3
"""Source checks for production-vps environment attachments and policy audit."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github/workflows"
AUDIT_WORKFLOW = WORKFLOWS / "production-vps-environment-policy-audit.yml"
AUDITOR = REPO / "scripts/audit_production_vps_environment_policy.py"
EXACT_REPOSITORY_GUARD = "github.repository == 'ramideltoro/nutsnews-infra'"
EXACT_MAIN_GUARD = "github.ref == 'refs/heads/main'"
AUDIT_JOB = "audit-production-vps-policy"
AUDIT_USE = "uses: ./.github/workflows/production-vps-environment-policy-audit.yml"
ENVIRONMENT_NAME = "production-vps"
CANONICAL_ENVIRONMENT_LINE = "    environment: production-vps"
JOB_ENVIRONMENT_KEY = re.compile(
    r"^    (?:(?:\"environment\"|'environment')|environment)\s*:(.*)$"
)
ENVIRONMENT_NAME_KEY = re.compile(
    r"^ {5,}(?:(?:\"name\"|'name')|name)\s*:(.*)$"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def job_blocks(text: str) -> dict[str, tuple[int, str]]:
    lines = text.splitlines(keepends=True)
    jobs_line = next((i for i, line in enumerate(lines) if line == "jobs:\n"), None)
    require(jobs_line is not None, "Workflow must contain a jobs mapping.")
    starts: list[tuple[str, int]] = []
    for index in range(jobs_line + 1, len(lines)):
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[index])
        if match:
            starts.append((match.group(1), index))
    blocks: dict[str, tuple[int, str]] = {}
    for offset, (name, start) in enumerate(starts):
        end = starts[offset + 1][1] if offset + 1 < len(starts) else len(lines)
        blocks[name] = (start, "".join(lines[start:end]))
    return blocks


def job_environment_entries(block: str) -> list[tuple[int, str]]:
    """Return job-level environment values, including nested mapping lines.

    This deliberately implements only the small indentation-aware subset needed
    to inventory a GitHub Actions job. It does not depend on PyYAML, and it
    treats quoted keys and whitespace before the colon as non-canonical forms
    rather than silently overlooking them.
    """

    lines = block.splitlines()
    entries: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if JOB_ENVIRONMENT_KEY.match(line) is None:
            continue
        entry_lines = [line]
        for continuation in lines[index + 1 :]:
            if not continuation.strip():
                break
            indentation = len(continuation) - len(continuation.lstrip(" "))
            if indentation <= 4:
                break
            entry_lines.append(continuation)
        entries.append((index, "\n".join(entry_lines)))
    return entries


def _dynamic_environment_name(value: str) -> bool:
    stripped = value.strip()
    return (
        not stripped
        or "${{" in stripped
        or stripped.startswith("*")
        or stripped.startswith("!")
    )


def classify_environment_entry(entry: str, source: str) -> bool:
    """Return True only for the exact canonical production attachment.

    Dynamic environment names cannot be proven not to resolve to production-vps,
    so they fail closed. Static non-production environments remain valid.
    """

    lines = entry.splitlines()
    require(lines, f"{source}: empty job environment declaration.")
    first_match = JOB_ENVIRONMENT_KEY.match(lines[0])
    require(first_match is not None, f"{source}: malformed job environment declaration.")

    if entry == CANONICAL_ENVIRONMENT_LINE:
        return True

    require(
        ENVIRONMENT_NAME not in entry.lower(),
        f"{source}: production-vps must use the exact canonical literal "
        f"`{CANONICAL_ENVIRONMENT_LINE.strip()}`.",
    )

    inline_value = first_match.group(1).strip()
    if inline_value:
        require(
            not _dynamic_environment_name(inline_value),
            f"{source}: dynamic job environment names are not allowed because "
            "they could bypass production-vps source auditing.",
        )
        require(
            not (inline_value.startswith("{") and "${{" in inline_value),
            f"{source}: dynamic flow-map environment names are not allowed.",
        )
        return False

    name_values = [
        match.group(1).strip()
        for line in lines[1:]
        if (match := ENVIRONMENT_NAME_KEY.match(line)) is not None
    ]
    require(
        len(name_values) == 1,
        f"{source}: environment mappings must contain exactly one static name.",
    )
    require(
        not _dynamic_environment_name(name_values[0]),
        f"{source}: dynamic environment mapping names are not allowed because "
        "they could bypass production-vps source auditing.",
    )
    return False


def classify_job_environment(job_name: str, block: str, source: str) -> tuple[bool, int]:
    entries = job_environment_entries(block)
    require(
        len(entries) <= 1,
        f"{source}:{job_name}: duplicate job environment declarations are not allowed.",
    )
    if not entries:
        return False, -1
    line_index, entry = entries[0]
    return classify_environment_entry(entry, f"{source}:{job_name}"), line_index


def validate_workflow_environment_attachments(source: str, text: str) -> int:
    """Validate all environment declarations and return protected-job count."""

    blocks = job_blocks(text)
    classified: dict[str, tuple[int, str, bool, int]] = {}
    protected_jobs = 0
    for job_name, (job_start, block) in blocks.items():
        is_protected, environment_line = classify_job_environment(
            job_name, block, source
        )
        classified[job_name] = (job_start, block, is_protected, environment_line)
        protected_jobs += int(is_protected)

    if protected_jobs == 0:
        return 0

    require(
        AUDIT_JOB in classified,
        f"{source}: missing unprotected policy-audit prerequisite job.",
    )
    audit_start, audit_block, audit_is_protected, _ = classified[AUDIT_JOB]
    require(
        not audit_is_protected and not job_environment_entries(audit_block),
        f"{source}: policy prerequisite must not attach any environment.",
    )
    require(
        AUDIT_USE in audit_block,
        f"{source}: policy prerequisite must call the canonical reusable audit.",
    )
    require(
        EXACT_REPOSITORY_GUARD in audit_block,
        f"{source}: policy prerequisite must pin the canonical repository.",
    )
    require(
        EXACT_MAIN_GUARD in audit_block,
        f"{source}: policy prerequisite must run only from exact main.",
    )
    require(
        "actions: read" in audit_block,
        f"{source}: policy prerequisite needs read-only Actions permission.",
    )

    for job_name, (job_start, block, is_protected, environment_line) in classified.items():
        if not is_protected:
            continue
        header = "\n".join(block.splitlines()[:environment_line])
        require(
            audit_start < job_start,
            f"{source}:{job_name}: audit prerequisite must be declared first.",
        )
        require(
            EXACT_REPOSITORY_GUARD in header,
            f"{source}:{job_name}: missing exact canonical-repository job guard.",
        )
        require(
            EXACT_MAIN_GUARD in header,
            f"{source}:{job_name}: missing exact-main job guard.",
        )
        require(
            re.search(
                r"^    needs:.*audit-production-vps-policy", header, re.MULTILINE
            )
            is not None,
            f"{source}:{job_name}: protected job must need the policy audit "
            "before environment attachment.",
        )
    return protected_jobs


def run_self_test() -> None:
    """Exercise syntax forms that previously evaded literal substring scans."""

    accepted_fixtures = {
        "canonical": "    environment: production-vps",
        "static-other": "    environment: staging-vps",
        "static-other-map-with-url-expression": (
            "    environment:\n"
            "      name: staging-vps\n"
            "      url: ${{ steps.deploy.outputs.url }}"
        ),
    }
    expected_results = {
        "canonical": True,
        "static-other": False,
        "static-other-map-with-url-expression": False,
    }
    for name, fixture in accepted_fixtures.items():
        actual = classify_environment_entry(fixture, f"self-test:{name}")
        require(actual is expected_results[name], f"self-test fixture failed: {name}")

    rejected_fixtures = {
        "multiline-map": "    environment:\n      name: production-vps",
        "flow-map": "    environment: {name: production-vps}",
        "quoted-scalar": '    environment: "production-vps"',
        "case-variant": "    environment: Production-VPS",
        "direct-expression": "    environment: ${{ inputs.environment }}",
        "map-name-expression": (
            "    environment:\n      name: ${{ inputs.environment }}"
        ),
        "yaml-alias": "    environment: *deployment_environment",
        "empty-map": "    environment:",
    }
    for name, fixture in rejected_fixtures.items():
        try:
            classify_environment_entry(fixture, f"self-test:{name}")
        except AssertionError:
            continue
        raise AssertionError(f"self-test fixture unexpectedly passed: {name}")

    duplicate_block = (
        "  deploy:\n"
        "    environment: staging-vps\n"
        "    environment: production-vps\n"
    )
    try:
        classify_job_environment("deploy", duplicate_block, "self-test:duplicate")
    except AssertionError:
        pass
    else:
        raise AssertionError("self-test duplicate environment fixture unexpectedly passed")
    print("production-vps environment syntax self-test passed (12 fixtures)")


def validate_repository() -> tuple[int, int]:
    audit_workflow_text = AUDIT_WORKFLOW.read_text(encoding="utf-8")
    audit_blocks = job_blocks(audit_workflow_text)
    require(
        all(not job_environment_entries(block) for _, block in audit_blocks.values()),
        "Policy audit must remain unprotected and must not attach any environment.",
    )
    require(
        "on:\n" in audit_workflow_text and "  workflow_call:" in audit_workflow_text,
        "Policy audit must be a local reusable workflow.",
    )
    require(
        "  workflow_dispatch:" in audit_workflow_text,
        "Operators must be able to run the read-only audit directly.",
    )
    require(
        EXACT_REPOSITORY_GUARD in audit_workflow_text,
        "Policy audit must pin the canonical repository.",
    )
    require(
        EXACT_MAIN_GUARD in audit_workflow_text,
        "Policy audit must run only from exact main.",
    )
    require(
        "actions: read" in audit_workflow_text,
        "Policy audit requires read-only Actions API permission.",
    )
    require(
        "GITHUB_TOKEN: ${{ github.token }}" in audit_workflow_text,
        "Policy audit must use the scoped workflow token.",
    )
    require(
        "audit_production_vps_environment_policy.py" in audit_workflow_text,
        "Reusable workflow must execute the policy auditor.",
    )

    auditor_text = AUDITOR.read_text(encoding="utf-8")
    for token in (
        'EXPECTED_REPOSITORY = "ramideltoro/nutsnews-infra"',
        'EXPECTED_API_ORIGIN = "https://api.github.com"',
        'ENVIRONMENT_NAME = "production-vps"',
        '"custom_branch_policies"',
        '"protected_branches"',
        '"required_reviewers"',
        'get("prevent_self_review") is not True',
        'policy.get("name") != "main"',
        'policy.get("type") != "branch"',
        "len(reviewers) < 1",
        'method="GET"',
    ):
        require(
            token in auditor_text,
            f"Policy auditor is missing required contract token: {token}",
        )
    for mutating_method in (
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
    ):
        require(
            mutating_method not in auditor_text,
            f"Policy auditor must remain read-only: {mutating_method}",
        )

    protected_workflows: set[str] = set()
    protected_jobs = 0
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        workflow_protected_jobs = validate_workflow_environment_attachments(
            path.name, text
        )
        if workflow_protected_jobs:
            protected_workflows.add(path.name)
            protected_jobs += workflow_protected_jobs

    require(protected_workflows, "Expected at least one production-vps protected workflow.")
    require(protected_jobs > 0, "Expected at least one production-vps protected job.")
    return len(protected_workflows), protected_jobs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run built-in environment syntax regression fixtures.",
    )
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return 0
    protected_workflows, protected_jobs = validate_repository()
    print(
        "production-vps environment policy source validation passed "
        f"({protected_workflows} workflows, {protected_jobs} protected jobs)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
