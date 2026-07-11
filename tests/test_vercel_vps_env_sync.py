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


SCRIPT = Path(__file__).parents[1] / "scripts" / "vercel_vps_env_sync.py"
MAPPING = Path(__file__).parents[1] / "config" / "vercel-vps-env-sync.json"
SPEC = importlib.util.spec_from_file_location("vercel_vps_env_sync", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
                [{"key": "NEXT_PUBLIC_TURNSTILE_SITE_KEY", "target": ["production"], "value": "review"}],
                self.mapping,
            )

    def test_missing_required_vercel_credential_fails_without_value_output(self) -> None:
        saved = {name: os.environ.pop(name, None) for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID")}
        try:
            with self.assertRaisesRegex(SystemExit, "VERCEL_TOKEN") as raised:
                sync.fetch_payload()
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
