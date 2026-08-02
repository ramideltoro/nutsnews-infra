"""Value-free normalization for Grafana Alloy Docker reader diagnostics."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


DIAGNOSTIC_KEYS = (
    "response_status",
    "component_health",
    "targets_total",
    "running_total",
    "caddy_running",
    "web_running",
    "reader_contract_satisfied",
)
SERVICE_LABEL_PATTERN = re.compile(
    r'(?:^|[{,])\s*service="(?P<value>(?:\\.|[^"\\])*)"\s*(?=,|}|$)'
)


def _bounded_status(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        status = int(value)
    except (TypeError, ValueError):
        return 0
    return status if 100 <= status <= 599 else 0


def _decode_content(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    content = response.get("content")
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _attribute_value(
    body: Any,
    attribute_name: str,
    wrapper_type: str,
    python_type: type[Any],
) -> Any:
    if not isinstance(body, list):
        return None
    for attribute in body:
        if not isinstance(attribute, Mapping) or attribute.get("name") != attribute_name:
            continue
        if attribute.get("type") != "attr":
            return None
        wrapped_value = attribute.get("value")
        if (
            not isinstance(wrapped_value, Mapping)
            or wrapped_value.get("type") != wrapper_type
            or "value" not in wrapped_value
        ):
            return None
        value = wrapped_value["value"]
        return value if isinstance(value, python_type) else None
    return None


def _has_service_label(labels: str, expected_service: str) -> bool:
    return any(
        match.group("value") == expected_service
        for match in SERVICE_LABEL_PATTERN.finditer(labels)
    )


def alloy_docker_reader_diagnostic(response: Any) -> dict[str, Any]:
    """Return only bounded counts and booleans from an Alloy URI result."""

    diagnostic: dict[str, Any] = {
        "response_status": 0,
        "component_health": "unavailable",
        "targets_total": 0,
        "running_total": 0,
        "caddy_running": False,
        "web_running": False,
        "reader_contract_satisfied": False,
    }
    if not isinstance(response, Mapping):
        return diagnostic

    diagnostic["response_status"] = _bounded_status(response.get("status"))
    if response.get("failed") is True or response.get("unreachable") is True:
        return diagnostic

    payload = _decode_content(response)
    if payload is None:
        return diagnostic

    health = payload.get("health")
    if isinstance(health, Mapping):
        health_state = health.get("state")
        if health_state == "healthy":
            diagnostic["component_health"] = "healthy"
        elif isinstance(health_state, str) and health_state:
            diagnostic["component_health"] = "unhealthy"

    debug_info = payload.get("debugInfo")
    if not isinstance(debug_info, list):
        return diagnostic

    target_blocks = [
        block
        for block in debug_info
        if isinstance(block, Mapping)
        and block.get("name") == "targets_info"
        and block.get("type") == "block"
        and isinstance(block.get("body"), list)
    ]
    diagnostic["targets_total"] = len(target_blocks)

    for target_block in target_blocks:
        body = target_block["body"]
        if _attribute_value(body, "is_running", "bool", bool) is not True:
            continue
        diagnostic["running_total"] += 1
        labels = _attribute_value(body, "labels", "string", str)
        if not isinstance(labels, str):
            continue
        if _has_service_label(labels, "caddy"):
            diagnostic["caddy_running"] = True
        if _has_service_label(labels, "web"):
            diagnostic["web_running"] = True

    diagnostic["reader_contract_satisfied"] = (
        diagnostic["response_status"] == 200
        and diagnostic["component_health"] == "healthy"
        and diagnostic["running_total"] >= 3
        and diagnostic["caddy_running"]
        and diagnostic["web_running"]
    )
    return diagnostic


class FilterModule:
    """Register the role-scoped Alloy diagnostic filter."""

    def filters(self) -> dict[str, Any]:
        return {"alloy_docker_reader_diagnostic": alloy_docker_reader_diagnostic}
