#!/usr/bin/env python3
"""Regression tests for the bounded observability textfile collector."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock


MODULE_PATH = Path("ansible/roles/vps_service_foundation/files/observability_textfile_exporter.py")
SPEC = importlib.util.spec_from_file_location("observability_textfile_exporter", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load observability textfile exporter.")
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "https://www.nutsnews.com/readyz",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.url = url
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class ObservabilityTextfileExporterTests(unittest.TestCase):
    def test_missing_timestamp_is_explicitly_unavailable(self) -> None:
        samples = EXPORTER.timestamp_samples("nutsnews_example", "unknown")
        self.assertEqual(
            samples,
            [
                "nutsnews_example_available 0",
                "nutsnews_example_timestamp_seconds -1",
                "nutsnews_example_age_seconds -1",
            ],
        )

    def test_docker_stats_are_bounded_and_parse_resource_values(self) -> None:
        status = {
            "app": {"enabled": True},
            "docker": {
                "available": True,
                "containers": [
                    {
                        "name": "nutsnews-caddy",
                        "state": "running",
                        "health": "healthy",
                        "restart_count": 2,
                    }
                ]
            },
        }
        caddy = {
            "CPUPerc": "1.5%",
            "MemUsage": "10MiB / 256MiB",
            "MemPerc": "3.9%",
            "NetIO": "2kB / 3kB",
            "BlockIO": "4MB / 5MB",
            "PIDs": "7",
        }

        def fake_command(argv: list[str], timeout: float = 5) -> CompletedProcess[str]:
            row = caddy if argv[-1] == "nutsnews-caddy" else {}
            return CompletedProcess(argv, 0 if row else 1, json.dumps(row) if row else "", "")

        with mock.patch.object(EXPORTER, "command", side_effect=fake_command):
            samples = EXPORTER.collect_docker_stats(status)

        self.assertIn('nutsnews_docker_stats_available{service="caddy"} 1', samples)
        self.assertIn('nutsnews_docker_container_state_available{service="caddy"} 1', samples)
        self.assertIn('nutsnews_docker_container_running{service="caddy"} 1', samples)
        self.assertIn('nutsnews_docker_container_healthy{service="caddy"} 1', samples)
        self.assertIn('nutsnews_docker_container_cpu_percent{service="caddy"} 1.5', samples)
        self.assertIn('nutsnews_docker_container_memory_used_bytes{service="caddy"} 10485760', samples)
        self.assertIn('nutsnews_docker_stats_available{service="web"} 0', samples)
        self.assertFalse(any("container=" in item or "compose_project=" in item for item in samples))

    def test_collection_failure_atomically_replaces_stale_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nutsnews.prom"
            output.write_text("stale_success 1\n", encoding="utf-8")
            with (
                mock.patch.object(EXPORTER, "OUTPUT_FILE", output),
                mock.patch.object(EXPORTER, "collect", side_effect=RuntimeError("fixture")),
            ):
                EXPORTER.main()
            rendered = output.read_text(encoding="utf-8")

        self.assertNotIn("stale_success", rendered)
        self.assertIn("nutsnews_observability_textfile_collector_success 0", rendered)
        self.assertIn("nutsnews_ops_portal_status_available 0", rendered)
        for family in (
            "alert",
            "backup",
            "email_reporting",
            "app",
            "resource",
            "security",
            "systemd_service",
        ):
            self.assertIn(f"nutsnews_{family}_status_available 0", rendered)
        self.assertIn('nutsnews_docker_stats_available{service="caddy"} 0', rendered)
        self.assertIn('nutsnews_caddy_tls_certificate_probe_success{service="caddy"} 0', rendered)
        self.assertIn("nutsnews_production_ownership_available 0", rendered)
        self.assertIn("nutsnews_production_ownership_last_success_timestamp_seconds -1", rendered)

    def test_production_ownership_uses_validated_canonical_readiness(self) -> None:
        web_revision = "a" * 40
        infra_revision = "b" * 40
        payload = {
            "ready": True,
            "service": "nutsnews-web",
            "deploymentTarget": "production-vps",
            "databaseProviderMode": "backend_postgres_primary",
            "sourceCommit": web_revision,
        }
        headers = {
            "Cache-Control": "private, no-store",
            "X-NutsNews-Deployment-Target": "production-vps",
            "X-NutsNews-Database-Provider-Mode": "backend_postgres_primary",
            "X-NutsNews-Source-Commit": web_revision,
        }
        with tempfile.TemporaryDirectory() as directory:
            commit_file = Path(directory) / "deployed-infra-commit"
            commit_file.write_text(f"{infra_revision}\n", encoding="utf-8")
            response = FakeResponse(json.dumps(payload).encode(), headers=headers)
            with (
                mock.patch.object(EXPORTER, "DEPLOYED_INFRA_COMMIT_FILE", commit_file),
                mock.patch.object(EXPORTER.urllib.request, "urlopen", return_value=response),
                mock.patch.object(EXPORTER.time, "time", return_value=1_800_000_000),
            ):
                samples = EXPORTER.production_ownership_samples()

        self.assertEqual(
            samples,
            [
                "nutsnews_production_ownership_info{"
                'database_provider="backend_postgres_primary",'
                f'infra_revision="{infra_revision}",'
                f'web_revision="{web_revision}",'
                'web_target="production-vps"} 1',
                "nutsnews_production_ownership_available 1",
                "nutsnews_production_ownership_last_success_timestamp_seconds 1800000000",
            ],
        )
        self.assertFalse(hasattr(EXPORTER, "PRODUCTION_OWNERSHIP"))
        self.assertFalse(any("ingestion_owner" in item or "worker_uplift_mode" in item for item in samples))

    def test_production_ownership_fails_closed_on_untrusted_readiness(self) -> None:
        web_revision = "a" * 40
        infra_revision = "b" * 40
        valid_payload = {
            "ready": True,
            "service": "nutsnews-web",
            "deploymentTarget": "vercel-production",
            "databaseProviderMode": "supabase_primary",
            "sourceCommit": web_revision,
        }
        valid_headers = {
            "Cache-Control": "no-store",
            "X-NutsNews-Deployment-Target": "vercel-production",
            "X-NutsNews-Database-Provider-Mode": "supabase_primary",
            "X-NutsNews-Source-Commit": web_revision,
        }
        fixtures = {
            "malformed body": FakeResponse(b"{", headers=valid_headers),
            "header mismatch": FakeResponse(
                json.dumps(valid_payload).encode(),
                headers={**valid_headers, "X-NutsNews-Deployment-Target": "production-vps"},
            ),
            "invalid payload": FakeResponse(
                json.dumps({**valid_payload, "ready": False}).encode(), headers=valid_headers
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            commit_file = Path(directory) / "deployed-infra-commit"
            commit_file.write_text(f"{infra_revision}\n", encoding="utf-8")
            for name, response in fixtures.items():
                with (
                    self.subTest(name=name),
                    mock.patch.object(EXPORTER, "DEPLOYED_INFRA_COMMIT_FILE", commit_file),
                    mock.patch.object(EXPORTER.urllib.request, "urlopen", return_value=response),
                ):
                    self.assertEqual(
                        EXPORTER.production_ownership_samples(),
                        [
                            "nutsnews_production_ownership_available 0",
                            "nutsnews_production_ownership_last_success_timestamp_seconds -1",
                        ],
                    )

    def test_report_conclusion_and_distinct_success_timestamps_are_exported(self) -> None:
        status = {
            "generated_at": "2026-07-31T00:00:00Z",
            "alerts": {"items": []},
            "email_reporting": {
                "enabled": True,
                "configured": True,
                "pending_alerts": 1,
                "suppressed_alerts": 0,
                "recipients_count": 1,
                "updated_at": "2026-07-31T00:05:00Z",
                "last_report_run_at": "2026-07-31T00:05:00Z",
                "last_report_success_at": "2026-07-30T00:05:00Z",
                "last_report_delivery_success_at": "2026-07-31T00:05:00Z",
                "last_report_conclusion": "critical",
                "last_report_exit_code": 2,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            status_file.write_text(json.dumps(status), encoding="utf-8")
            with (
                mock.patch.object(EXPORTER, "STATUS_FILE", status_file),
                mock.patch.object(EXPORTER, "collect_docker_stats", return_value=[]),
                mock.patch.object(EXPORTER, "collect_alloy_readiness", return_value=[]),
                mock.patch.object(EXPORTER, "collect_tls_expiry", return_value=[]),
                mock.patch.object(EXPORTER, "production_ownership_samples", return_value=[]),
            ):
                samples = EXPORTER.collect()

        self.assertIn("nutsnews_email_reporting_last_report_exit_code 2", samples)
        self.assertIn('nutsnews_email_reporting_last_report_conclusion{outcome="critical"} 1', samples)
        self.assertIn('nutsnews_email_reporting_last_report_conclusion{outcome="success"} 0', samples)
        self.assertIn("nutsnews_email_reporting_last_report_run_timestamp_seconds 1785456300", samples)
        self.assertIn("nutsnews_email_reporting_last_report_success_timestamp_seconds 1785369900", samples)
        self.assertIn("nutsnews_email_reporting_last_report_delivery_success_timestamp_seconds 1785456300", samples)

    def test_sample_preserves_epoch_timestamp_precision(self) -> None:
        rendered = EXPORTER.sample(
            "nutsnews_example_timestamp_seconds",
            1_785_662_843.125,
        )

        self.assertEqual(rendered, "nutsnews_example_timestamp_seconds 1785662843.125")
        self.assertLess(abs(float(rendered.split()[1]) - 1_785_662_843.125), 0.001)


if __name__ == "__main__":
    unittest.main()
