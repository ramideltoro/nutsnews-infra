#!/usr/bin/env python3
"""Unit tests for the read-only production-vps environment policy audit."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts/audit_production_vps_environment_policy.py"
SPEC = importlib.util.spec_from_file_location("production_vps_policy_audit", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

VALIDATOR_PATH = REPO / "ansible/tests/validate_production_vps_environment_policy.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "production_vps_policy_source_validator", VALIDATOR_PATH
)
assert VALIDATOR_SPEC and VALIDATOR_SPEC.loader
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)


VALID_ENVIRONMENT = {
    "can_admins_bypass": True,
    "protection_rules": [],
    "deployment_branch_policy": {
        "protected_branches": False,
        "custom_branch_policies": False,
    },
}
VALID_BRANCH_POLICIES = {
    "total_count": 0,
    "branch_policies": [],
}


class PolicyAuditTests(unittest.TestCase):
    def test_unrestricted_automatic_policy_passes(self) -> None:
        MODULE.validate_policy(VALID_ENVIRONMENT, VALID_BRANCH_POLICIES)

    def test_null_deployment_branch_policy_is_unrestricted(self) -> None:
        environment = copy.deepcopy(VALID_ENVIRONMENT)
        environment["deployment_branch_policy"] = None
        MODULE.validate_policy(environment, VALID_BRANCH_POLICIES)

    def test_admin_bypass_setting_does_not_create_a_deployment_gate(self) -> None:
        for can_admins_bypass in (True, False, None):
            with self.subTest(can_admins_bypass=can_admins_bypass):
                environment = copy.deepcopy(VALID_ENVIRONMENT)
                if can_admins_bypass is None:
                    environment.pop("can_admins_bypass")
                else:
                    environment["can_admins_bypass"] = can_admins_bypass
                MODULE.validate_policy(environment, VALID_BRANCH_POLICIES)

    def test_required_reviewer_fails_automatic_deployment_contract(self) -> None:
        environment = copy.deepcopy(VALID_ENVIRONMENT)
        environment["protection_rules"].append(
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "User", "reviewer": {}}],
            },
        )
        with self.assertRaisesRegex(
            MODULE.PolicyAuditError, "must not require manual reviewers"
        ):
            MODULE.validate_policy(environment, VALID_BRANCH_POLICIES)

    def test_protected_branches_mode_fails(self) -> None:
        environment = copy.deepcopy(VALID_ENVIRONMENT)
        environment["deployment_branch_policy"]["protected_branches"] = True
        with self.assertRaisesRegex(MODULE.PolicyAuditError, "protected-branches"):
            MODULE.validate_policy(environment, VALID_BRANCH_POLICIES)

    def test_custom_branch_policy_mode_fails(self) -> None:
        environment = copy.deepcopy(VALID_ENVIRONMENT)
        environment["deployment_branch_policy"]["custom_branch_policies"] = True
        with self.assertRaisesRegex(MODULE.PolicyAuditError, "custom deployment"):
            MODULE.validate_policy(environment, VALID_BRANCH_POLICIES)

    def test_any_branch_or_tag_restriction_fails(self) -> None:
        policies = {
            "total_count": 1,
            "branch_policies": [{"name": "main", "type": "branch"}],
        }
        with self.assertRaisesRegex(MODULE.PolicyAuditError, "restrict deployments"):
            MODULE.validate_policy(VALID_ENVIRONMENT, policies)

    def test_api_origin_is_exact_and_query_free(self) -> None:
        self.assertEqual(
            MODULE.validate_api_origin("https://api.github.com"),
            "https://api.github.com",
        )
        for invalid in (
            "http://api.github.com",
            "https://api.github.com.evil.example",
            "https://api.github.com/path",
            "https://api.github.com?token=leak",
            "https://user@api.github.com",
            "https://api.github.com:not-a-port",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(MODULE.PolicyAuditError):
                    MODULE.validate_api_origin(invalid)


class PolicySourceValidatorTests(unittest.TestCase):
    def test_exact_literal_is_the_only_accepted_production_syntax(self) -> None:
        self.assertTrue(
            VALIDATOR.classify_environment_entry(
                "    environment: production-vps", "fixture:canonical"
            )
        )
        for name, fixture in {
            "multiline-map": "    environment:\n      name: production-vps",
            "flow-map": "    environment: {name: production-vps}",
            "quoted": '    environment: "production-vps"',
            "case-variant": "    environment: Production-VPS",
        }.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AssertionError, "canonical literal"):
                    VALIDATOR.classify_environment_entry(fixture, f"fixture:{name}")

    def test_dynamic_environment_name_forms_fail_closed(self) -> None:
        for name, fixture in {
            "direct-expression": "    environment: ${{ inputs.environment }}",
            "map-expression": (
                "    environment:\n      name: ${{ inputs.environment }}"
            ),
            "yaml-alias": "    environment: *deployment_environment",
        }.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AssertionError, "dynamic"):
                    VALIDATOR.classify_environment_entry(fixture, f"fixture:{name}")

    def test_static_nonproduction_map_can_use_expression_for_url_only(self) -> None:
        self.assertFalse(
            VALIDATOR.classify_environment_entry(
                "    environment:\n"
                "      name: staging-vps\n"
                "      url: ${{ steps.deploy.outputs.url }}",
                "fixture:staging-map",
            )
        )

    def test_built_in_syntax_fixture_self_test_passes(self) -> None:
        VALIDATOR.run_self_test()


if __name__ == "__main__":
    unittest.main()
