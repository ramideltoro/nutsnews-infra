#!/usr/bin/env python3
"""Serve the public NutsNews infrastructure health check."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SERVICE_NAME = "nutsnews-infra"

HOST = os.environ.get("NUTSNEWS_INFRA_HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("NUTSNEWS_INFRA_HEALTH_PORT", "18080") or "18080")
LOG_FILE = Path(os.environ.get("NUTSNEWS_INFRA_HEALTH_LOG_FILE", "/opt/nutsnews/logs/health/health-failures.jsonl"))
THRESHOLD_PERCENT = float(os.environ.get("NUTSNEWS_INFRA_HEALTH_THRESHOLD_PERCENT", "60") or "60")

RESOURCE_CHECKS = {
    item.strip().lower()
    for item in os.environ.get("NUTSNEWS_INFRA_HEALTH_RESOURCE_CHECKS", "cpu,memory,disk").split(",")
    if item.strip()
}
REQUIRED_DISKS = [
    item.strip()
    for item in os.environ.get("NUTSNEWS_INFRA_HEALTH_REQUIRED_DISKS", "/,/opt/nutsnews").split(",")
    if item.strip()
]
REQUIRED_SERVICES = [
    item.strip()
    for item in os.environ.get("NUTSNEWS_INFRA_HEALTH_REQUIRED_SERVICES", "").split(",")
    if item.strip()
]
REQUIRED_CONTAINERS = [
    item.strip()
    for item in os.environ.get("NUTSNEWS_INFRA_HEALTH_REQUIRED_CONTAINERS", "").split(",")
    if item.strip()
]
SIMULATED_FAILURES = {
    item.strip()
    for item in os.environ.get("NUTSNEWS_INFRA_HEALTH_SIMULATE_FAILURES", "").split(",")
    if item.strip()
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run(argv: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        completed = subprocess.run(argv, capture_output=True, check=False, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{argv[0]} not found", "returncode": 127}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "command timed out", "returncode": 124}

    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def percent(used: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round((used / total) * 100, 1)


def cpu_sample() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        values = [int(field) for field in fields]
    except (OSError, IndexError, ValueError):
        return None

    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent() -> float | None:
    first = cpu_sample()
    if not first:
        return None
    time.sleep(0.15)
    second = cpu_sample()
    if not second:
        return None

    idle_a, total_a = first
    idle_b, total_b = second
    total_delta = total_b - total_a
    if total_delta <= 0:
        return None
    return round((1 - ((idle_b - idle_a) / total_delta)) * 100, 1)


def memory_percent() -> float | None:
    values: dict[str, int] = {}
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        parts = line.replace(":", "").split()
        if len(parts) < 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        return None
    return percent(max(total - available, 0), total)


def fail(
    failures: list[dict[str, Any]],
    check: str,
    measured: Any,
    reason: str,
    *,
    threshold: Any = THRESHOLD_PERCENT,
    target: str = "",
    public_group: str | None = None,
) -> None:
    failures.append(
        {
            "timestamp": utc_now(),
            "check": check,
            "public_group": public_group or check.split(":", 1)[0],
            "measured": measured,
            "threshold": threshold,
            "target": target,
            "reason": reason,
        }
    )


def check_resources(failures: list[dict[str, Any]]) -> None:
    if "cpu" in RESOURCE_CHECKS:
        value = cpu_percent()
        if value is None:
            fail(failures, "cpu", "unavailable", "CPU usage could not be read.", public_group="resource")
        elif value >= THRESHOLD_PERCENT:
            fail(failures, "cpu", value, "CPU usage is at or above threshold.", public_group="resource")

    if "memory" in RESOURCE_CHECKS:
        value = memory_percent()
        if value is None:
            fail(failures, "memory", "unavailable", "Memory usage could not be read.", public_group="resource")
        elif value >= THRESHOLD_PERCENT:
            fail(failures, "memory", value, "Memory usage is at or above threshold.", public_group="resource")

    if "disk" in RESOURCE_CHECKS:
        for raw_path in REQUIRED_DISKS:
            path = Path(raw_path)
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                fail(failures, "disk", "unavailable", "Disk path is not available.", target=raw_path, public_group="resource")
                continue

            value = percent(usage.used, usage.total)
            if value >= THRESHOLD_PERCENT:
                fail(
                    failures,
                    "disk",
                    value,
                    "Disk usage is at or above threshold.",
                    target=raw_path,
                    public_group="resource",
                )


def check_systemd(failures: list[dict[str, Any]]) -> None:
    for service in REQUIRED_SERVICES:
        result = run(["systemctl", "is-active", service], timeout=4)
        measured = result["stdout"].strip() or result["stderr"].strip() or f"exit:{result['returncode']}"
        if measured not in {"active", "activating"}:
            fail(
                failures,
                "systemd",
                measured,
                "Required systemd unit is not active.",
                threshold="active",
                target=service,
                public_group="service",
            )


def check_docker(failures: list[dict[str, Any]]) -> None:
    if not REQUIRED_CONTAINERS:
        return

    for container in REQUIRED_CONTAINERS:
        result = run(
            ["docker", "inspect", "--format", "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container],
            timeout=6,
        )
        measured = result["stdout"].strip() or result["stderr"].strip() or f"exit:{result['returncode']}"
        if not result["ok"]:
            fail(
                failures,
                "docker",
                measured,
                "Required container could not be inspected.",
                threshold="running|healthy-or-none",
                target=container,
                public_group="container",
            )
            continue

        running, _, health = measured.partition("|")
        if running != "true" or health not in {"healthy", "none"}:
            fail(
                failures,
                "docker",
                measured,
                "Required container is not running or healthy.",
                threshold="running|healthy-or-none",
                target=container,
                public_group="container",
            )


def add_simulated_failures(failures: list[dict[str, Any]]) -> None:
    for name in sorted(SIMULATED_FAILURES):
        fail(
            failures,
            f"simulated:{name}",
            "simulated",
            "Simulated health check failure.",
            threshold="none",
            target=name,
            public_group="simulated",
        )


def log_failures(failures: list[dict[str, Any]]) -> None:
    if not failures:
        return

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            for item in failures:
                handle.write(json.dumps(item, sort_keys=True) + "\n")
    except OSError as error:
        print(f"Could not write health failure log: {error}", file=sys.stderr)

    for item in failures:
        print(json.dumps(item, sort_keys=True), file=sys.stderr)


def evaluate_health() -> tuple[int, dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    check_resources(failures)
    check_systemd(failures)
    check_docker(failures)
    add_simulated_failures(failures)
    log_failures(failures)

    if failures:
        failed_groups = sorted({item["public_group"] for item in failures})
        return 503, {"ok": False, "service": SERVICE_NAME, "failed_checks": failed_groups}
    return 200, {"ok": True, "service": SERVICE_NAME}


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "NutsNewsInfraHealth/1.0"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            self.send_json(404, {"ok": False, "service": SERVICE_NAME})
            return

        status, payload = evaluate_health()
        self.send_json(status, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), HealthHandler)
    print(f"Serving {SERVICE_NAME} health on {HOST}:{PORT}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopping health server.", file=sys.stderr)
    finally:
        httpd.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="NutsNews infrastructure health check")
    parser.add_argument("--once", action="store_true", help="Evaluate once, print JSON, and exit 0 for healthy or 1 for unhealthy.")
    args = parser.parse_args()

    if args.once:
        status, payload = evaluate_health()
        print(json.dumps(payload, separators=(",", ":")))
        return 0 if status == 200 else 1

    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
