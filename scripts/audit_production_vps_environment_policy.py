#!/usr/bin/env python3
"""Fail closed unless the production-vps GitHub Environment is protected.

The audit is intentionally read-only and reports no reviewer identities.  It is
designed to run before a job attaches the protected environment, so a missing
or weakened policy cannot unlock environment secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


EXPECTED_REPOSITORY = "ramideltoro/nutsnews-infra"
EXPECTED_API_ORIGIN = "https://api.github.com"
ENVIRONMENT_NAME = "production-vps"
API_VERSION = "2022-11-28"


class PolicyAuditError(RuntimeError):
    """Raised when the remote policy cannot be proven safe."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the workflow token through an unexpected redirect."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _require_mapping(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyAuditError(f"{description} is missing or malformed")
    return value


def _require_list(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise PolicyAuditError(f"{description} is missing or malformed")
    return value


def validate_api_origin(api_url: str) -> str:
    """Allow the workflow token to be sent only to GitHub's exact API origin."""

    try:
        parsed = urllib.parse.urlsplit(api_url)
        parsed_port = parsed.port
    except ValueError as exc:
        raise PolicyAuditError(
            f"GITHUB_API_URL must be the exact {EXPECTED_API_ORIGIN} origin"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or parsed_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise PolicyAuditError(
            f"GITHUB_API_URL must be the exact {EXPECTED_API_ORIGIN} origin"
        )
    return EXPECTED_API_ORIGIN


def validate_policy(
    environment: dict[str, Any], branch_policy_response: dict[str, Any]
) -> None:
    """Validate custom exact-main deployment policy and reviewer protection."""

    deployment_policy = _require_mapping(
        environment.get("deployment_branch_policy"),
        "deployment branch policy",
    )
    if deployment_policy.get("protected_branches") is not False:
        raise PolicyAuditError(
            "production-vps must not use the broad protected-branches policy"
        )
    if deployment_policy.get("custom_branch_policies") is not True:
        raise PolicyAuditError(
            "production-vps must enable custom deployment branch policies"
        )

    protection_rules = _require_list(
        environment.get("protection_rules"), "environment protection rules"
    )
    reviewer_rules = [
        rule
        for rule in protection_rules
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    if len(reviewer_rules) != 1:
        raise PolicyAuditError(
            "production-vps must have exactly one required-reviewers rule"
        )
    reviewers = _require_list(
        reviewer_rules[0].get("reviewers"), "required reviewer configuration"
    )
    if len(reviewers) < 1:
        raise PolicyAuditError(
            "production-vps must configure at least one required reviewer"
        )
    if reviewer_rules[0].get("prevent_self_review") is not True:
        raise PolicyAuditError(
            "production-vps must prevent deployment initiators from self-reviewing"
        )

    policies = _require_list(
        branch_policy_response.get("branch_policies"),
        "custom deployment branch policies",
    )
    total_count = branch_policy_response.get("total_count")
    if not isinstance(total_count, int) or total_count != len(policies):
        raise PolicyAuditError(
            "custom deployment branch policy response is incomplete or malformed"
        )
    if len(policies) != 1 or not isinstance(policies[0], dict):
        raise PolicyAuditError(
            "production-vps must have exactly one custom deployment branch policy"
        )
    policy = policies[0]
    if policy.get("name") != "main":
        raise PolicyAuditError(
            "production-vps custom deployment branch policy must be exactly main"
        )
    if policy.get("type") != "branch":
        raise PolicyAuditError(
            "production-vps main deployment policy must target a branch"
        )


def api_get_json(api_origin: str, path: str, token: str) -> dict[str, Any]:
    url = f"{api_origin}{path}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "nutsnews-production-vps-policy-audit",
        },
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise PolicyAuditError(
            f"GitHub policy API returned HTTP {exc.code}; read access is required"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PolicyAuditError("GitHub policy API could not be verified") from exc
    return _require_mapping(payload, "GitHub policy API response")


def audit(repository: str, api_url: str, token: str) -> None:
    if repository != EXPECTED_REPOSITORY:
        raise PolicyAuditError(
            f"policy audit is restricted to {EXPECTED_REPOSITORY}"
        )
    if not token:
        raise PolicyAuditError("GITHUB_TOKEN is required for the policy audit")
    api_origin = validate_api_origin(api_url)
    owner, repo = repository.split("/", 1)
    base_path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/environments/{ENVIRONMENT_NAME}"
    )
    environment = api_get_json(api_origin, base_path, token)
    branch_policies = api_get_json(
        api_origin,
        f"{base_path}/deployment-branch-policies?per_page=100",
        token,
    )
    validate_policy(environment, branch_policies)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL", EXPECTED_API_ORIGIN)
    )
    args = parser.parse_args()
    try:
        audit(args.repository, args.api_url, os.environ.get("GITHUB_TOKEN", ""))
    except PolicyAuditError as exc:
        print(f"production-vps environment policy audit failed: {exc}", file=sys.stderr)
        return 1
    print(
        "production-vps environment policy audit passed: "
        "exact-main custom branch policy, required reviewer, and self-review "
        "prevention are present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
