#!/usr/bin/env python3
"""Validate staging auto-idle stays scoped and observable."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT = ROOT / "roles/vps_service_foundation/files/staging_auto_idle.py"
SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
DEFAULTS = (ROOT / "roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
TASKS = (ROOT / "roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
SERVICE = (ROOT / "roles/vps_service_foundation/templates/nutsnews-staging-auto-idle.service.j2").read_text(
    encoding="utf-8"
)
TIMER = (ROOT / "roles/vps_service_foundation/templates/nutsnews-staging-auto-idle.timer.j2").read_text(
    encoding="utf-8"
)
COLLECTOR = (ROOT / "roles/vps_service_foundation/files/ops_portal_collector.py").read_text(encoding="utf-8")
REPORTER = (ROOT / "roles/vps_service_foundation/files/ops_portal_reporter.py").read_text(encoding="utf-8")
APP_JS = (REPO / "portal/assets/app.js").read_text(encoding="utf-8")
WORKFLOW_SAFETY = (REPO / ".github/workflows/workflow-safety.yml").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


for required in (
    "vps_service_foundation_nutsnews_staging_auto_idle_enabled: true",
    "vps_service_foundation_nutsnews_staging_auto_idle_grace_seconds: 3600",
    "vps_service_foundation_nutsnews_staging_auto_idle_remove_cache_volume: true",
    "vps_service_foundation_nutsnews_staging_auto_idle_status_file:",
    "vps_service_foundation_nutsnews_staging_auto_idle_log_file:",
):
    require(required in DEFAULTS, f"Default missing {required}.")

for required in (
    "Install NutsNews staging auto-idle runner",
    "Validate NutsNews staging auto-idle settings",
    "Install NutsNews staging auto-idle service",
    "Install NutsNews staging auto-idle timer",
    "Seed staging auto-idle status before the first scheduled run",
    "Manage NutsNews staging auto-idle timer",
):
    require(required in TASKS, f"Service foundation tasks missing {required}.")

for forbidden in ("nutsnews-app.env", "nutsnews-app ", "production-vps", "NUTSNEWS_VPS_SSH_PRIVATE_KEY"):
    require(forbidden not in SERVICE, f"Auto-idle service must not reference production authority: {forbidden}")

for required in (
    "NUTSNEWS_STAGING_PROJECT_NAME",
    "NUTSNEWS_STAGING_ACCESS_PROJECT",
    "NUTSNEWS_STAGING_MUTATION_LOCK",
    "NUTSNEWS_APP_APPLY_MARKER_FILE",
    "NUTSNEWS_STAGING_APPLY_MARKER_FILE",
    "NoNewPrivileges=true",
    "ProtectSystem=strict",
):
    require(required in SERVICE, f"Auto-idle service missing {required}.")

require("OnCalendar={{ vps_service_foundation_nutsnews_staging_auto_idle_on_calendar }}" in TIMER, "Timer cadence must be managed by defaults.")
require("production_touched" in SCRIPT_TEXT and '"production_touched": False' in SCRIPT_TEXT, "Status must explicitly record that production was not touched.")
require("staging_marker_deployment_id_mismatch" in SCRIPT_TEXT, "Auto-idle must not idle a superseded staging candidate.")
require("compose_down(STAGING_ACCESS_PROJECT" in SCRIPT_TEXT, "Auto-idle must stop staging access verifier.")
require("compose_down(STAGING_PROJECT_NAME" in SCRIPT_TEXT, "Auto-idle must stop staging app.")
require("docker\", \"volume\", \"rm\", STAGING_CACHE_VOLUME" in SCRIPT_TEXT, "Auto-idle must remove the staging cache volume when enabled.")
require("nutsnews-app-staging" in SCRIPT_TEXT and "nutsnews-staging-access-verifier" in SCRIPT_TEXT, "Auto-idle must target only staging containers.")
require('"nutsnews-app"' not in SCRIPT_TEXT, "Auto-idle script must not target the production container.")
require("staging_auto_idle" in COLLECTOR, "Collector must expose staging auto-idle status.")
require("Staging idle" in APP_JS, "Portal UI must show staging idle state.")
require("staging_auto_idle_lines" in REPORTER, "Reporter must include staging auto-idle state.")
require("validate_staging_auto_idle.py" in WORKFLOW_SAFETY, "Workflow Safety must run staging auto-idle guardrails.")


def load_module(root: Path):
    staging_dir = root / "staging-app"
    access_dir = root / "staging-access"
    staging_dir.mkdir(exist_ok=True)
    access_dir.mkdir(exist_ok=True)
    (staging_dir / "compose.yml").write_text("name: nutsnews-staging\n", encoding="utf-8")
    (access_dir / "compose.yml").write_text("name: nutsnews-staging-access\n", encoding="utf-8")
    (root / "staging.env").write_text("DUMMY=1\n", encoding="utf-8")
    (root / "access.env").write_text("DUMMY=1\n", encoding="utf-8")
    os.environ.update(
        {
            "NUTSNEWS_APP_APPLY_MARKER_FILE": str(root / "last-app-apply.json"),
            "NUTSNEWS_STAGING_APPLY_MARKER_FILE": str(root / "staging-last-apply.json"),
            "NUTSNEWS_STAGING_AUTO_IDLE_STATUS_FILE": str(root / "status.json"),
            "NUTSNEWS_STAGING_AUTO_IDLE_LOG_FILE": str(root / "idle.jsonl"),
            "NUTSNEWS_STAGING_MUTATION_LOCK": str(root / "lock"),
            "NUTSNEWS_STAGING_AUTO_IDLE_NOW": "2026-07-16T12:00:00Z",
            "NUTSNEWS_STAGING_APP_DIR": str(staging_dir),
            "NUTSNEWS_STAGING_COMPOSE_FILE": str(staging_dir / "compose.yml"),
            "NUTSNEWS_STAGING_ENV_FILE": str(root / "staging.env"),
            "NUTSNEWS_STAGING_ACCESS_DIR": str(access_dir),
            "NUTSNEWS_STAGING_ACCESS_COMPOSE_FILE": str(access_dir / "compose.yml"),
            "NUTSNEWS_STAGING_ACCESS_ENV_FILE": str(root / "access.env"),
        }
    )
    spec = importlib.util.spec_from_file_location("staging_auto_idle_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "last-app-apply.json").write_text(
        json.dumps(
            {
                "qualification_expires_at": "2026-07-16T09:00:00Z",
                "staging_deployment_id": "stg-current",
            }
        ),
        encoding="utf-8",
    )
    (root / "staging-last-apply.json").write_text(json.dumps({"deployment_id": "stg-newer"}), encoding="utf-8")
    module = load_module(root)
    superseded = module.evaluate()
    require(superseded["status"] == "superseded", "Superseded staging deployment must not be idled.")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "last-app-apply.json").write_text(
        json.dumps(
            {
                "qualification_expires_at": "2026-07-16T09:00:00Z",
                "staging_deployment_id": "stg-current",
            }
        ),
        encoding="utf-8",
    )
    (root / "staging-last-apply.json").write_text(json.dumps({"deployment_id": "stg-current"}), encoding="utf-8")
    module = load_module(root)
    calls: list[list[str]] = []

    def fake_running(name: str) -> bool:
        return name in {"nutsnews-app-staging", "nutsnews-staging-access-verifier"}

    def fake_command(args: list[str]) -> dict[str, object]:
        calls.append(args)
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    module.container_running = fake_running
    module.command = fake_command
    idled = module.evaluate()
    require(idled["status"] == "idled", "Expired matching staging deployment must be idled.")
    serialized = json.dumps(calls)
    require("nutsnews-staging" in serialized, "Staging app Compose project must be stopped.")
    require("nutsnews-staging-access" in serialized, "Staging access Compose project must be stopped.")
    require("nutsnews-app-staging-cache" in serialized, "Staging cache volume must be removed.")
    require("nutsnews-app\"" not in serialized, "Production app project must not be targeted.")

require(datetime.now(timezone.utc).tzinfo is not None, "Timezone-aware validation sanity check.")
print("Staging auto-idle guardrails passed.")
