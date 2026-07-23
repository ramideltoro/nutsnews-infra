from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "vercel_vps_env_sync.py"
MAPPING = Path(__file__).parents[1] / "config" / "vercel-vps-env-sync.json"
SPEC = importlib.util.spec_from_file_location("vercel_vps_env_sync", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CURRENT_VERCEL_PRODUCTION_NAMES = {
    "ACTIONS_READ_TOKEN",
    "ADMIN_EMAILS",
    "ADMIN_SHARD_COUNT",
    "ADMIN_SHARD_SLOW_RUN_MS",
    "ADMIN_SHARD_STALE_MINUTES",
    "AUTH_GOOGLE_ID",
    "AUTH_GOOGLE_SECRET",
    "AUTH_SECRET",
    "AUTH_TRUST_HOST",
    "AUTH_URL",
    "BETTER_STACK_INGESTING_HOST",
    "BETTER_STACK_SOURCE_TOKEN",
    "CONTACT_FROM_EMAIL",
    "CONTACT_TO_EMAIL",
    "HOME_SERVER_STATS_API_KEY",
    "HOME_SERVER_STATS_URL",
    "NEXT_PUBLIC_APP_ENV",
    "NEXT_PUBLIC_GA_ID",
    "NEXT_PUBLIC_NUTSNEWS_IOS_APP_STORE_URL",
    "NEXT_PUBLIC_NUTSNEWS_RUNTIME_ENV",
    "NEXT_PUBLIC_NUTSNEWS_SIDE_EFFECTS_MODE",
    "NEXT_PUBLIC_SENTRY_DSN",
    "NEXT_PUBLIC_SITE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_TURNSTILE_SITE_KEY",
    "NEXT_PUBLIC_VERCEL_ENV",
    "NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA",
    "NEXTAUTH_URL",
    "NUTSNEWS_EDGE_FEED_SNAPSHOT_URL",
    "NUTSNEWS_ADMIN_CANONICAL_ORIGIN",
    "NUTSNEWS_ADMIN_DIRECT_ORIGIN",
    "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL",
    "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL",
    "NUTSNEWS_FAILOVER_RUNBOOK_URL",
    "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET",
    "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET",
    "NUTSNEWS_BACKEND_API_URL",
    "NUTSNEWS_BACKEND_API_TOKEN",
    "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION",
    "NUTSNEWS_DATABASE_PROVIDER_MODE",
    "NUTSNEWS_DATA_ENV",
    "NUTSNEWS_DATA_ENVIRONMENT",
    "NUTSNEWS_PRODUCTION_WRITES_PAUSED",
    "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF",
    "NUTSNEWS_RUNTIME_ENV",
    "NUTSNEWS_SIDE_EFFECTS_MODE",
    "NUTSNEWS_SUPABASE_CREDENTIALS_ENV",
    "NUTSNEWS_SUPABASE_PROJECT_REF",
    "OPENAI_API_KEY",
    "OPENAI_INPUT_COST_PER_1M_TOKENS",
    "OPENAI_INPUT_TOKENS_PER_REVIEW_ESTIMATE",
    "OPENAI_OUTPUT_COST_PER_1M_TOKENS",
    "OPENAI_OUTPUT_TOKENS_PER_REVIEW_ESTIMATE",
    "RESEND_API_KEY",
    "SENTRY_AUTH_TOKEN",
    "SENTRY_ORG",
    "SENTRY_PROJECT",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TURNSTILE_SECRET_KEY",
}

REQUIRED_AUTH_VALUES = {
    "AUTH_GOOGLE_ID": "1234567890-test-client.apps.googleusercontent.com",
    "AUTH_GOOGLE_SECRET": "valid-secret-fixture",
    "AUTH_SECRET": "s" * 64,
}

HOME_SERVER_STATS_VALUES = {
    "HOME_SERVER_STATS_URL": "https://ai.nutsnews.com/stats",
    "HOME_SERVER_STATS_API_KEY": "home-server-stats-key-fixture",
}


def valid_runtime_fixture(**overrides: str) -> dict[str, str]:
    values = {
        **REQUIRED_AUTH_VALUES,
        **HOME_SERVER_STATS_VALUES,
    }
    values.update(overrides)
    return values


class VercelVpsEnvSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = sync.load_mapping(MAPPING)
        self.report = {
            "safe_to_synchronize": [],
            "server_side_secret": [],
            "vercel_platform_only": [],
            "preview_development_only": [],
            "manual_review": [],
        }

    def test_empty_diff_is_idempotent(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = sync.print_diff({}, {}, self.mapping, self.report)
        self.assertFalse(changed)
        self.assertIn("No synchronized variable changes detected.", output.getvalue())

    def test_current_production_inventory_is_explicitly_classified(self) -> None:
        self.assertEqual(set(self.mapping["variables"]), CURRENT_VERCEL_PRODUCTION_NAMES)

    def test_added_variable_reports_name_only(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = sync.print_diff({"NEXT_PUBLIC_SITE_URL": "alpha"}, {}, self.mapping, self.report)
        self.assertTrue(changed)
        self.assertIn("added: NEXT_PUBLIC_SITE_URL", output.getvalue())
        self.assertNotIn("alpha", output.getvalue())

    def test_changed_variable_reports_name_only(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = sync.print_diff(
                {"NEXT_PUBLIC_SITE_URL": "new-value"},
                {"NEXT_PUBLIC_SITE_URL": digest("old-value")},
                self.mapping,
                self.report,
            )
        self.assertTrue(changed)
        self.assertIn("changed: NEXT_PUBLIC_SITE_URL", output.getvalue())
        self.assertNotIn("new-value", output.getvalue())
        self.assertNotIn("old-value", output.getvalue())

    def test_removed_variable_reports_name_only(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = sync.print_diff(
                {},
                {"NEXT_PUBLIC_SITE_URL": digest("old-value")},
                self.mapping,
                self.report,
            )
        self.assertTrue(changed)
        self.assertIn("removed: NEXT_PUBLIC_SITE_URL", output.getvalue())
        self.assertNotIn("old-value", output.getvalue())

    def test_excluded_platform_variable_is_not_selected(self) -> None:
        selected, report = sync.classify_records(
            [{"key": "VERCEL_URL", "target": ["production"], "value": "platform"}],
            self.mapping,
        )
        self.assertEqual(selected, {})
        self.assertEqual(report["vercel_platform_only"], ["VERCEL_URL"])

    def test_preview_only_variable_is_not_read_as_production(self) -> None:
        selected, _ = sync.classify_records(
            [{"key": "NEXT_PUBLIC_SITE_URL", "target": ["preview"], "value": "preview-only"}],
            self.mapping,
        )
        self.assertEqual(selected, {})

    def test_supabase_url_populates_browser_and_server_destinations(self) -> None:
        selected, _ = sync.classify_records(
            [
                {
                    "key": "NEXT_PUBLIC_SUPABASE_URL",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "https://example.supabase.co",
                }
            ],
            self.mapping,
        )
        self.assertEqual(
            selected,
            {
                "NUTSNEWS_PUBLIC_SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_URL": "https://example.supabase.co",
            },
        )

    def test_runtime_data_environment_is_synchronized(self) -> None:
        selected, _ = sync.classify_records(
            [
                {
                    "key": "NUTSNEWS_DATA_ENVIRONMENT",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "production",
                }
            ],
            self.mapping,
        )
        self.assertEqual(
            selected,
            {"NUTSNEWS_DATA_ENVIRONMENT": "production"},
        )

    def test_synchronized_server_secret_is_not_reported_as_excluded(self) -> None:
        selected, report = sync.classify_records(
            [
                {
                    "key": "SUPABASE_SERVICE_ROLE_KEY",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "secret",
                }
            ],
            self.mapping,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = sync.print_diff(selected, {}, self.mapping, report)
        self.assertTrue(changed)
        self.assertIn("added: SUPABASE_SERVICE_ROLE_KEY", output.getvalue())
        self.assertNotIn("excluded", output.getvalue())

    def test_backend_api_token_synchronizes_as_server_secret(self) -> None:
        selected, report = sync.classify_records(
            [
                {
                    "key": "NUTSNEWS_BACKEND_API_TOKEN",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "backend-token-fixture",
                }
            ],
            self.mapping,
        )
        self.assertEqual(selected, {"NUTSNEWS_BACKEND_API_TOKEN": "backend-token-fixture"})
        self.assertEqual(report["server_side_secret"], ["NUTSNEWS_BACKEND_API_TOKEN"])

    def test_home_server_stats_config_synchronizes_for_admin_dashboard(self) -> None:
        selected, report = sync.classify_records(
            [
                {
                    "key": "HOME_SERVER_STATS_URL",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "https://ai.nutsnews.com/stats",
                },
                {
                    "key": "HOME_SERVER_STATS_API_KEY",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "home-server-stats-key-fixture",
                },
            ],
            self.mapping,
        )

        self.assertEqual(selected, HOME_SERVER_STATS_VALUES)
        self.assertEqual(report["safe_to_synchronize"], ["HOME_SERVER_STATS_URL"])
        self.assertEqual(report["server_side_secret"], ["HOME_SERVER_STATS_API_KEY"])
        sync.validate_selected_values(valid_runtime_fixture())

    def test_home_server_stats_config_requires_canonical_url_and_api_key(self) -> None:
        sync.validate_selected_values(valid_runtime_fixture())

        invalid_cases = (
            valid_runtime_fixture(HOME_SERVER_STATS_URL="https://ai.nutsnews.com/api/stats"),
            valid_runtime_fixture(HOME_SERVER_STATS_URL="https://ai.nutsnews.com/health"),
            valid_runtime_fixture(HOME_SERVER_STATS_URL="http://ai.nutsnews.com/stats"),
            valid_runtime_fixture(HOME_SERVER_STATS_URL="https://example.com/stats"),
            valid_runtime_fixture(HOME_SERVER_STATS_URL="https://ai.nutsnews.com/stats?debug=true"),
            valid_runtime_fixture(HOME_SERVER_STATS_API_KEY="short"),
            valid_runtime_fixture(HOME_SERVER_STATS_API_KEY=json.dumps({"encryptedValue": "ciphertext-fixture"})),
            {key: value for key, value in valid_runtime_fixture().items() if key != "HOME_SERVER_STATS_URL"},
            {key: value for key, value in valid_runtime_fixture().items() if key != "HOME_SERVER_STATS_API_KEY"},
        )

        for invalid_values in invalid_cases:
            with self.subTest(invalid_values=sorted(invalid_values)):
                with self.assertRaisesRegex(SystemExit, "HOME_SERVER_STATS"):
                    sync.validate_selected_values(invalid_values)

    def test_failover_status_and_action_secrets_sync_for_admin_dashboard(self) -> None:
        selected, report = sync.classify_records(
            [
                {
                    "key": "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "https://nutsnews-controller.nutsnews.workers.dev/status?mode=dashboard",
                },
                {
                    "key": "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "x" * 64,
                },
                {
                    "key": "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "y" * 64,
                },
                {
                    "key": "NUTSNEWS_FAILOVER_RUNBOOK_URL",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "https://github.com/ramideltoro/nutsnews/blob/main/.github/deployment/failover-visibility-runbook.md",
                },
                {
                    "key": "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL",
                    "target": ["production"],
                    "type": "plain",
                    "decrypted": True,
                    "value": "https://dash.cloudflare.com/example/nutsnews.com/dns/records",
                },
            ],
            self.mapping,
        )

        self.assertEqual(
            selected,
            {
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://nutsnews-controller.nutsnews.workers.dev/status?mode=dashboard",
                "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": "x" * 64,
                "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET": "y" * 64,
                "NUTSNEWS_FAILOVER_RUNBOOK_URL": "https://github.com/ramideltoro/nutsnews/blob/main/.github/deployment/failover-visibility-runbook.md",
                "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL": "https://dash.cloudflare.com/example/nutsnews.com/dns/records",
            },
        )
        self.assertEqual(
            report["safe_to_synchronize"],
            [
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL",
                "NUTSNEWS_FAILOVER_RUNBOOK_URL",
                "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL",
            ],
        )
        self.assertEqual(
            report["server_side_secret"],
            ["NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET", "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET"],
        )
        sync.validate_selected_values(valid_runtime_fixture(**selected))

    def test_failover_status_config_requires_controller_url_and_hmac_secret(self) -> None:
        required_auth = valid_runtime_fixture()
        valid = {
            **required_auth,
            "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://nutsnews-controller.nutsnews.workers.dev/status",
            "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": "x" * 64,
        }
        sync.validate_selected_values(valid)

        for invalid_values in (
            {
                **valid,
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "http://nutsnews-controller.nutsnews.workers.dev/status",
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://example.com/status?mode=dashboard",
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": "https://nutsnews-controller.nutsnews.workers.dev/actions",
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": "too-short",
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET": "too-short",
            },
            {
                **required_auth,
                "NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL": valid["NUTSNEWS_FAILOVER_CONTROLLER_STATUS_URL"],
            },
            {
                **required_auth,
                "NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET": valid["NUTSNEWS_FAILOVER_STATUS_HMAC_SECRET"],
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_RUNBOOK_URL": "http://github.com/ramideltoro/nutsnews",
            },
            {
                **valid,
                "NUTSNEWS_FAILOVER_CLOUDFLARE_DASHBOARD_URL": "https://user:pass@dash.cloudflare.com/example",
            },
        ):
            with self.assertRaises(SystemExit):
                sync.validate_selected_values(invalid_values)

    def test_failover_action_hmac_secret_synchronizes_for_manual_controls(self) -> None:
        selected, report = sync.classify_records(
            [
                {
                    "key": "NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "z" * 64,
                }
            ],
            self.mapping,
        )
        self.assertEqual(selected, {"NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET": "z" * 64})
        self.assertEqual(report["server_side_secret"], ["NUTSNEWS_FAILOVER_ACTION_HMAC_SECRET"])
        sync.validate_selected_values(valid_runtime_fixture(**selected))

    def test_failover_action_urls_require_manual_review(self) -> None:
        for key in (
            "NUTSNEWS_FAILOVER_CONTROLLER_ACTION_URL",
            "NUTSNEWS_FAILOVER_CONTROLLER_AUDIT_URL",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(SystemExit, key):
                    sync.classify_records(
                        [
                            {
                                "key": key,
                                "target": ["production"],
                                "type": "encrypted",
                                "decrypted": True,
                                "value": "manual-control-fixture",
                            }
                        ],
                        self.mapping,
                    )

    def test_valid_plaintext_is_accepted(self) -> None:
        selected, _ = sync.classify_records(
            [
                {
                    "key": "AUTH_GOOGLE_ID",
                    "target": ["production"],
                    "type": "encrypted",
                    "decrypted": True,
                    "value": "1234567890-test-client.apps.googleusercontent.com",
                }
            ],
            self.mapping,
        )
        self.assertEqual(selected["AUTH_GOOGLE_ID"], "1234567890-test-client.apps.googleusercontent.com")

    def test_encrypted_envelope_is_rejected_without_echoing_ciphertext(self) -> None:
        ciphertext = "ciphertext-fixture-must-not-appear"
        envelope = json.dumps({"encryptedValue": ciphertext, "keyId": "fixture"})
        with self.assertRaisesRegex(SystemExit, "AUTH_GOOGLE_ID") as raised:
            sync.classify_records(
                [
                    {
                        "key": "AUTH_GOOGLE_ID",
                        "target": ["production"],
                        "type": "encrypted",
                        "decrypted": False,
                        "value": envelope,
                    }
                ],
                self.mapping,
            )
        self.assertNotIn(ciphertext, str(raised.exception))
        self.assertNotIn(envelope, str(raised.exception))

    def test_observed_opaque_auth_values_fail_semantic_validation(self) -> None:
        opaque_client_id = "x" * 1192
        with self.assertRaisesRegex(SystemExit, "AUTH_GOOGLE_ID") as raised:
            sync.validate_selected_values(
                valid_runtime_fixture(
                    AUTH_GOOGLE_ID=opaque_client_id,
                    ADMIN_EMAILS="rami.deltoro@gmail.com",
                )
            )
        self.assertNotIn(opaque_client_id, str(raised.exception))

    def test_observed_opaque_secret_lengths_fail_semantic_validation(self) -> None:
        opaque_google_secret = "x" * 1084
        opaque_auth_secret = "x" * 1168
        with self.assertRaisesRegex(SystemExit, "AUTH_GOOGLE_SECRET.*AUTH_SECRET") as raised:
            sync.validate_selected_values(
                valid_runtime_fixture(
                    AUTH_GOOGLE_SECRET=opaque_google_secret,
                    AUTH_SECRET=opaque_auth_secret,
                    ADMIN_EMAILS="rami.deltoro@gmail.com",
                )
            )
        self.assertNotIn(opaque_google_secret, str(raised.exception))
        self.assertNotIn(opaque_auth_secret, str(raised.exception))

    def test_observed_opaque_auth_secret_is_rejected_by_decryption_metadata(self) -> None:
        opaque_secret = "x" * 1168
        with self.assertRaisesRegex(SystemExit, "AUTH_SECRET") as raised:
            sync.classify_records(
                [
                    {
                        "key": "AUTH_SECRET",
                        "target": ["production"],
                        "type": "encrypted",
                        "decrypted": False,
                        "value": opaque_secret,
                    }
                ],
                self.mapping,
            )
        self.assertNotIn(opaque_secret, str(raised.exception))

    def test_auth_semantics_and_admin_email_format_are_checked(self) -> None:
        sync.validate_selected_values(
            valid_runtime_fixture(ADMIN_EMAILS="rami.deltoro@gmail.com,admin@example.com")
        )
        with self.assertRaisesRegex(SystemExit, "ADMIN_EMAILS"):
            sync.validate_selected_values(
                valid_runtime_fixture(ADMIN_EMAILS="not-an-email")
            )

    def test_backend_primary_requires_backend_api_token(self) -> None:
        with self.assertRaisesRegex(SystemExit, "NUTSNEWS_BACKEND_API_TOKEN"):
            sync.validate_selected_values(
                valid_runtime_fixture(
                    NUTSNEWS_DATABASE_PROVIDER_MODE="backend_postgres_primary",
                    NUTSNEWS_BACKEND_API_URL="https://backend.nutsnews.com/api/app/db",
                    NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION="enable-backend-postgres-primary",
                )
            )

        with self.assertRaisesRegex(SystemExit, "NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION"):
            sync.validate_selected_values(
                valid_runtime_fixture(
                    NUTSNEWS_DATABASE_PROVIDER_MODE="backend_postgres_primary",
                    NUTSNEWS_BACKEND_API_URL="https://backend.nutsnews.com/api/app/db",
                    NUTSNEWS_BACKEND_API_TOKEN="backend-token-fixture",
                )
            )

        sync.validate_selected_values(
            valid_runtime_fixture(
                NUTSNEWS_DATABASE_PROVIDER_MODE="backend_postgres_primary",
                NUTSNEWS_BACKEND_API_URL="https://backend.nutsnews.com/api/app/db",
                NUTSNEWS_BACKEND_API_TOKEN="backend-token-fixture",
                NUTSNEWS_BACKEND_POSTGRES_PRIMARY_CONFIRMATION="enable-backend-postgres-primary",
            )
        )

    def test_fetch_uses_documented_per_variable_decrypted_endpoint(self) -> None:
        saved = {
            name: os.environ.get(name)
            for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID")
        }
        os.environ.update(
            {
                "VERCEL_TOKEN": "token-fixture",
                "VERCEL_PROJECT_ID": "project-fixture",
                "VERCEL_TEAM_ID": "team-fixture",
            }
        )
        calls: list[tuple[str, str | None]] = []

        def fake_fetch(url: str, token: str, variable_name: str | None = None):
            calls.append((url, variable_name))
            if "/v10/" in url:
                return {
                    "envs": [
                        {
                            "id": "env-fixture",
                            "key": "AUTH_GOOGLE_ID",
                            "target": ["production"],
                            "type": "encrypted",
                            "decrypted": False,
                            "value": "list-envelope-not-used",
                        }
                    ]
                }
            return {
                "key": "AUTH_GOOGLE_ID",
                "type": "encrypted",
                "decrypted": True,
                "value": "1234567890-test-client.apps.googleusercontent.com",
            }

        try:
            with mock.patch.object(sync, "fetch_json", side_effect=fake_fetch):
                payload = sync.fetch_payload(self.mapping)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(payload[0]["value"], "1234567890-test-client.apps.googleusercontent.com")
        self.assertNotIn("decrypt=true", calls[0][0])
        self.assertIn("/v1/projects/project-fixture/env/env-fixture", calls[1][0])
        self.assertEqual(calls[1][1], "AUTH_GOOGLE_ID")

    def test_unknown_production_variable_fails_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Unclassified"):
            sync.classify_records(
                [{"key": "UNREVIEWED_RUNTIME_VALUE", "target": ["production"], "value": "unknown"}],
                self.mapping,
            )

    def test_multiple_unknown_variables_are_reported_together(self) -> None:
        with self.assertRaisesRegex(SystemExit, "FIRST_UNKNOWN, SECOND_UNKNOWN"):
            sync.classify_records(
                [
                    {"key": "SECOND_UNKNOWN", "target": ["production"], "value": "second"},
                    {"key": "FIRST_UNKNOWN", "target": ["production"], "value": "first"},
                ],
                self.mapping,
            )

    def test_manual_review_variable_stops_the_sync(self) -> None:
        with self.assertRaisesRegex(SystemExit, "manual review"):
            sync.classify_records(
                [{"key": "ADMIN_UNREVIEWED", "target": ["production"], "value": "review"}],
                self.mapping,
            )

    def test_missing_required_vercel_credential_fails_without_value_output(self) -> None:
        saved = {name: os.environ.pop(name, None) for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID")}
        try:
            with self.assertRaisesRegex(SystemExit, "VERCEL_TOKEN") as raised:
                sync.fetch_payload(self.mapping)
            self.assertNotIn("alpha", str(raised.exception))
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value

    def test_fingerprint_parser_never_returns_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.env"
            path.write_text('NEXT_PUBLIC_SITE_URL="alpha"\n', encoding="utf-8")
            fingerprints = sync.parse_env_file(path)
        self.assertEqual(fingerprints, {"NEXT_PUBLIC_SITE_URL": "alpha"})
        output = json.dumps({key: digest(value) for key, value in fingerprints.items()})
        self.assertNotIn("alpha", output)


if __name__ == "__main__":
    unittest.main()
