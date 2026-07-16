#!/usr/bin/env python3
"""Validate Ops Portal collector slow-section cache behavior."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_PATH = ROOT / "ansible/roles/vps_service_foundation/files/ops_portal_collector.py"
SPEC = importlib.util.spec_from_file_location("ops_portal_collector_cache_validation", COLLECTOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("Could not load Ops Portal collector.")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def state(result: dict[str, object]) -> str:
    cache = result.get("_collector_cache")
    require(isinstance(cache, dict), "Cached section result must include _collector_cache.")
    return str(cache.get("state"))


with tempfile.TemporaryDirectory() as raw_tmp:
    tmp = Path(raw_tmp)
    collector.SLOW_CACHE_FILE = tmp / "slow-cache.json"
    collector.CACHE_EVENTS.clear()
    calls = {"count": 0}

    def producer() -> dict[str, object]:
        calls["count"] += 1
        return {"available": True, "value": calls["count"]}

    first = collector.cached_slow_section("unit", 60, producer)
    second = collector.cached_slow_section("unit", 60, producer)
    require(first["value"] == 1, "First cache call must return live producer output.")
    require(second["value"] == 1, "Fresh cache call must reuse cached output.")
    require(calls["count"] == 1, "Fresh cache hit must not call the producer.")
    require(state(first) == "live", "First cache call must be marked live.")
    require(state(second) == "fresh_cache", "Second cache call must be marked fresh_cache.")

    cache = json.loads(collector.SLOW_CACHE_FILE.read_text(encoding="utf-8"))
    cache["sections"]["unit"]["collected_at_epoch"] = int(time.time()) - 120
    cache["sections"]["unit"]["collected_at"] = "2026-01-01T00:00:00+00:00"
    collector.SLOW_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    third = collector.cached_slow_section("unit", 60, producer)
    require(third["value"] == 2, "Expired cache must refresh from producer.")
    require(calls["count"] == 2, "Expired cache must call producer once.")
    require(state(third) == "live", "Expired refresh must be marked live.")

    def failing_producer() -> dict[str, object]:
        raise RuntimeError("synthetic token=hidden failure")

    cache = json.loads(collector.SLOW_CACHE_FILE.read_text(encoding="utf-8"))
    cache["sections"]["unit"]["collected_at_epoch"] = int(time.time()) - 120
    cache["sections"]["unit"]["collected_at"] = "2026-01-01T00:00:00+00:00"
    collector.SLOW_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    stale = collector.cached_slow_section("unit", 60, failing_producer)
    require(stale["value"] == 2, "Failure with prior cache must return stale cached data.")
    require(state(stale) == "stale_cache", "Failure with prior cache must be marked stale_cache.")
    require(
        stale["_collector_cache"]["error"] == "synthetic [redacted] failure",
        "Cache failure metadata must redact secret-looking text.",
    )

    collector.SLOW_CACHE_FILE.unlink()
    unavailable = collector.cached_slow_section("unit", 60, failing_producer)
    require(unavailable["available"] is False, "Failure without cache must return unavailable section.")
    require(state(unavailable) == "unavailable", "Failure without cache must be marked unavailable.")

print("Ops Portal collector slow-section cache guardrails passed.")
