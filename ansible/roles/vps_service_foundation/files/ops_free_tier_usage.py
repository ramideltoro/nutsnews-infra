#!/usr/bin/env python3
"""Collect read-only free-tier usage for the NutsNews Operations Portal."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_SOURCE_STATUSES = {"live", "cached", "not configured", "unavailable", "unknown"}
DEFAULT_WARNING_USED_PERCENT = 70.0
DEFAULT_CRITICAL_USED_PERCENT = 90.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value in {"never", "unknown"}:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(value: Any, now: datetime | None = None) -> int | None:
    parsed = parse_timestamp(value)
    if not parsed:
        return None
    reference = now or datetime.now(timezone.utc)
    return max(int((reference - parsed).total_seconds()), 0)


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percent(used: float, limit: float) -> float | None:
    if limit <= 0:
        return None
    return round((used / limit) * 100, 1)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return min(max(value, low), high)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_file.replace(path)
    path.chmod(0o644)


def nested(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def display_number(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def display_amount(value: float | None, unit: str) -> str:
    if value is None:
        return "unknown"
    suffix = f" {unit}" if unit else ""
    return f"{display_number(value)}{suffix}"


def display_percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


class ApiResult:
    def __init__(self, status: str, metrics: dict[str, float] | None = None, detail: str = "") -> None:
        self.status = status if status in ALLOWED_SOURCE_STATUSES else "unknown"
        self.metrics = metrics or {}
        self.detail = detail


class JsonHttpClient:
    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 8,
    ) -> Any:
        encoded_url = url
        if params:
            separator = "&" if "?" in url else "?"
            encoded_url = f"{url}{separator}{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(encoded_url, headers=headers or {}, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

class FreeTierCollector:
    def __init__(
        self,
        env: dict[str, str] | None = None,
        http_client: JsonHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self.env = env if env is not None else os.environ
        self.http_client = http_client or JsonHttpClient()
        self.now = now or datetime.now(timezone.utc).replace(microsecond=0)
        self.errors: list[str] = []

    def collect(self) -> dict[str, Any]:
        config = self.quota_config()
        cache_file = Path(self.env.get("NUTSNEWS_FREE_TIER_CACHE_FILE", "/opt/nutsnews/portal-assets/data/free-tier-usage-cache.json"))
        cache_ttl_seconds = self.cache_ttl_seconds()
        cache = self.read_cache(cache_file)
        snapshot = self.usage_snapshot()

        providers = []
        updated_cache = {"schema_version": 1, "updated_at": utc_now(), "providers": {}}
        for provider_config in config:
            provider = self.collect_provider(provider_config, snapshot, cache, cache_ttl_seconds)
            providers.append(provider)
            if provider["status"] in {"live", "cached"}:
                updated_cache["providers"][provider["key"]] = {
                    "status": provider["status"],
                    "last_checked_at": provider["last_checked_at"],
                    "metrics": {metric["key"]: metric["usage"] for metric in provider["metrics"] if metric["usage"] is not None},
                }

        if updated_cache["providers"]:
            try:
                write_json(cache_file, updated_cache)
            except OSError:
                self.errors.append("Free-tier usage cache could not be written.")

        return {
            "schema_version": 1,
            "generated_at": self.now.isoformat(),
            "cache_file": str(cache_file),
            "cache_ttl_seconds": cache_ttl_seconds,
            "guardrails": [
                "Read-only collectors only; provider mutations and automatic upgrades are intentionally unsupported.",
                "Quota limits are supplied by Ansible configuration and should be rechecked against provider docs before rollout.",
                "Provider tokens are optional and never included in the generated portal status JSON.",
            ],
            "providers": providers,
            "errors": self.errors,
        }

    def quota_config(self) -> list[dict[str, Any]]:
        raw = self.env.get("NUTSNEWS_FREE_TIER_QUOTAS_JSON", "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            self.errors.append("Free-tier quota config is invalid JSON.")
            return []
        if isinstance(parsed, dict):
            parsed = parsed.get("providers", [])
        if not isinstance(parsed, list):
            self.errors.append("Free-tier quota config must be a provider list.")
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def usage_snapshot(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        usage_file = self.env.get("NUTSNEWS_FREE_TIER_USAGE_FILE", "").strip()
        if usage_file:
            file_data = read_json(Path(usage_file), {})
            if isinstance(file_data, dict):
                merged.update(file_data)
        raw = self.env.get("NUTSNEWS_FREE_TIER_USAGE_JSON", "").strip()
        if raw:
            try:
                env_data = json.loads(raw)
            except json.JSONDecodeError:
                self.errors.append("Free-tier usage snapshot is invalid JSON.")
            else:
                if isinstance(env_data, dict):
                    merged.update(env_data)
        return merged

    def read_cache(self, cache_file: Path) -> dict[str, Any]:
        cache = read_json(cache_file, {})
        return cache if isinstance(cache, dict) else {}

    def collect_provider(
        self,
        provider_config: dict[str, Any],
        snapshot: dict[str, Any],
        cache: dict[str, Any],
        cache_ttl_seconds: int,
    ) -> dict[str, Any]:
        key = str(provider_config.get("key") or "unknown").strip() or "unknown"
        platform = str(provider_config.get("platform") or key).strip() or key
        metrics_config = [item for item in provider_config.get("metrics", []) if isinstance(item, dict)]

        live = self.collect_live(provider_config)
        usage_source = live.metrics
        source_status = live.status
        source_detail = live.detail
        last_checked_at = self.now.isoformat() if source_status == "live" else "unknown"
        stale = False

        if source_status != "live":
            snapshot_usage = self.provider_usage_from_snapshot(snapshot, key)
            cache_usage = self.provider_usage_from_snapshot(cache, key)
            if snapshot_usage:
                usage_source = snapshot_usage.get("metrics", {})
                source_status = "cached"
                source_detail = "Usage loaded from configured snapshot."
                last_checked_at = str(snapshot_usage.get("last_checked_at") or self.now.isoformat())
            elif cache_usage:
                usage_source = cache_usage.get("metrics", {})
                source_status = "cached"
                source_detail = "Usage loaded from local collector cache."
                last_checked_at = str(cache_usage.get("last_checked_at") or cache.get("updated_at") or "unknown")
            elif source_status == "not configured":
                source_detail = "No live API credentials or usage snapshot configured."
            elif source_status == "unknown":
                source_detail = source_detail or "Provider usage source is unknown."
            else:
                source_detail = source_detail or "Provider usage could not be read."

        if source_status == "cached":
            cached_age = age_seconds(last_checked_at, self.now)
            stale = cached_age is not None and cached_age > cache_ttl_seconds
            if stale:
                source_detail = f"{source_detail} Cached data is stale."

        metrics = [
            self.metric_result(metric_config, usage_source.get(str(metric_config.get("key", "")))) for metric_config in metrics_config
        ]
        primary = self.primary_metric(metrics)
        if not metrics:
            source_status = "unknown"
            source_detail = "No quota metrics are configured for this provider."

        return {
            "key": key,
            "platform": platform,
            "status": source_status if source_status in ALLOWED_SOURCE_STATUSES else "unknown",
            "source_status": source_status if source_status in ALLOWED_SOURCE_STATUSES else "unknown",
            "source_detail": source_detail,
            "last_checked_at": last_checked_at,
            "stale": stale,
            "quota_source": provider_config.get("quota_source", ""),
            "quota_last_verified": provider_config.get("quota_last_verified", ""),
            "notes": provider_config.get("notes", ""),
            "current_usage": primary["usage_display"],
            "quota": primary["limit_display"],
            "remaining": primary["remaining_display"],
            "percent_used": primary["percent_used"],
            "percent_remaining": primary["percent_remaining"],
            "percent_used_display": primary["percent_used_display"],
            "percent_remaining_display": primary["percent_remaining_display"],
            "health": primary["health"],
            "metrics": metrics,
        }

    def collect_live(self, provider_config: dict[str, Any]) -> ApiResult:
        live = provider_config.get("live")
        if not isinstance(live, dict) or not live.get("type"):
            return ApiResult("not configured")
        live_type = str(live.get("type", "")).strip()
        if live_type == "sentry_stats_v2":
            return self.collect_sentry_stats(provider_config, live)
        if live_type == "json_api":
            return self.collect_json_api(live)
        return ApiResult("unknown", detail="Unsupported live usage collector type.")

    def collect_json_api(self, live: dict[str, Any]) -> ApiResult:
        url_env = str(live.get("url_env", "")).strip()
        token_env = str(live.get("token_env", "")).strip()
        url = self.env.get(url_env, "").strip() if url_env else str(live.get("url", "")).strip()
        token = self.env.get(token_env, "").strip() if token_env else ""
        if not url or (token_env and not token):
            return ApiResult("not configured")
        if not url.startswith("https://"):
            return ApiResult("unavailable", detail="Provider usage endpoint must use HTTPS.")
        if str(live.get("method", "GET")).upper() != "GET":
            return ApiResult("unavailable", detail="Only read-only GET usage requests are supported.")

        headers = {"Accept": "application/json"}
        if token:
            token_header = str(live.get("token_header", "Authorization")).strip() or "Authorization"
            token_scheme = str(live.get("token_scheme", "Bearer")).strip()
            headers[token_header] = f"{token_scheme} {token}".strip()

        try:
            data = self.http_client.get_json(url, headers=headers, timeout=self.http_timeout())
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return ApiResult("unavailable", detail="Provider API request failed.")

        metric_paths = live.get("metric_paths", {})
        if not isinstance(metric_paths, dict):
            return ApiResult("unknown", detail="Provider API metric paths are not configured.")

        metrics: dict[str, float] = {}
        for metric_key, path in metric_paths.items():
            value = safe_float(nested(data, str(path)))
            if value is not None:
                metrics[str(metric_key)] = value
        if not metrics:
            return ApiResult("unavailable", detail="Provider API response did not include configured metric values.")
        return ApiResult("live", metrics=metrics, detail="Usage loaded from provider API.")

    def collect_sentry_stats(self, provider_config: dict[str, Any], live: dict[str, Any]) -> ApiResult:
        token_env = str(live.get("token_env", "NUTSNEWS_SENTRY_AUTH_TOKEN")).strip()
        org_env = str(live.get("org_env", "NUTSNEWS_SENTRY_ORG")).strip()
        base_url_env = str(live.get("base_url_env", "NUTSNEWS_SENTRY_BASE_URL")).strip()
        token = self.env.get(token_env, "").strip()
        org = self.env.get(org_env, "").strip()
        base_url = self.env.get(base_url_env, "https://sentry.io").strip().rstrip("/")
        if not token or not org:
            return ApiResult("not configured")
        if not base_url.startswith("https://"):
            return ApiResult("unavailable", detail="Sentry base URL must use HTTPS.")

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        stats_period = str(live.get("stats_period", "30d"))
        metrics: dict[str, float] = {}
        categories = {
            str(metric.get("key")): metric.get("live_category")
            for metric in provider_config.get("metrics", [])
            if isinstance(metric, dict) and metric.get("live_category")
        }
        if not categories:
            return ApiResult("unknown", detail="Sentry categories are not configured.")

        for metric_key, category in categories.items():
            params = {
                "groupBy": "category",
                "field": "sum(quantity)",
                "statsPeriod": stats_period,
                "interval": "1d",
                "category": str(category),
                "outcome": "accepted",
            }
            try:
                data = self.http_client.get_json(
                    f"{base_url}/api/0/organizations/{urllib.parse.quote(org)}/stats_v2/",
                    headers=headers,
                    params=params,
                    timeout=self.http_timeout(),
                )
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
                continue
            total = 0.0
            for group in data.get("groups", []) if isinstance(data, dict) else []:
                totals = group.get("totals", {}) if isinstance(group, dict) else {}
                value = safe_float(totals.get("sum(quantity)"))
                if value is not None:
                    total += value
            metrics[metric_key] = total

        if not metrics:
            return ApiResult("unavailable", detail="Sentry stats could not be read.")
        return ApiResult("live", metrics=metrics, detail="Usage loaded from Sentry stats API.")

    def http_timeout(self) -> int:
        try:
            return int(self.env.get("NUTSNEWS_FREE_TIER_HTTP_TIMEOUT_SECONDS", "8") or 8)
        except ValueError:
            return 8

    def cache_ttl_seconds(self) -> int:
        try:
            return int(self.env.get("NUTSNEWS_FREE_TIER_CACHE_TTL_SECONDS", "21600") or 21600)
        except ValueError:
            self.errors.append("Free-tier cache TTL is invalid; using the default.")
            return 21600

    def provider_usage_from_snapshot(self, snapshot: dict[str, Any], provider_key: str) -> dict[str, Any]:
        provider = {}
        if isinstance(snapshot.get(provider_key), dict):
            provider = snapshot[provider_key]
        providers = snapshot.get("providers")
        if isinstance(providers, dict) and isinstance(providers.get(provider_key), dict):
            provider = {**provider, **providers[provider_key]}
        elif isinstance(providers, list):
            for item in providers:
                if isinstance(item, dict) and item.get("key") == provider_key:
                    provider = {**provider, **item}
                    break

        metrics: dict[str, float] = {}
        raw_metrics = provider.get("metrics", provider)
        if isinstance(raw_metrics, dict):
            for key, value in raw_metrics.items():
                number = safe_float(value)
                if number is not None:
                    metrics[str(key)] = number
        elif isinstance(raw_metrics, list):
            for item in raw_metrics:
                if not isinstance(item, dict) or "key" not in item:
                    continue
                number = safe_float(item.get("usage"))
                if number is not None:
                    metrics[str(item["key"])] = number
        if not metrics:
            return {}
        return {"metrics": metrics, "last_checked_at": provider.get("last_checked_at") or snapshot.get("last_checked_at")}

    def metric_result(self, metric_config: dict[str, Any], raw_usage: Any) -> dict[str, Any]:
        key = str(metric_config.get("key") or "unknown")
        label = str(metric_config.get("label") or key)
        unit = str(metric_config.get("unit") or "")
        limit = safe_float(metric_config.get("limit"))
        usage = safe_float(raw_usage)
        remaining = None if usage is None or limit is None else round(limit - usage, 2)
        used_percent = None if usage is None or limit is None else percent(usage, limit)
        remaining_percent = None if used_percent is None else round(max(100.0 - used_percent, 0.0), 1)
        warning = safe_float(metric_config.get("warning_used_percent")) or DEFAULT_WARNING_USED_PERCENT
        critical = safe_float(metric_config.get("critical_used_percent")) or DEFAULT_CRITICAL_USED_PERCENT
        if used_percent is None:
            health = "unknown"
        elif used_percent >= critical or (remaining is not None and remaining <= 0):
            health = "critical"
        elif used_percent >= warning:
            health = "warning"
        else:
            health = "healthy"

        return {
            "key": key,
            "label": label,
            "unit": unit,
            "period": metric_config.get("period", ""),
            "usage": None if usage is None else round(usage, 2),
            "limit": limit,
            "remaining": remaining,
            "percent_used": used_percent,
            "percent_remaining": remaining_percent,
            "usage_display": display_amount(usage, unit),
            "limit_display": display_amount(limit, unit),
            "remaining_display": display_amount(remaining, unit),
            "percent_used_display": display_percent(used_percent),
            "percent_remaining_display": display_percent(remaining_percent),
            "health": health,
        }

    def primary_metric(self, metrics: list[dict[str, Any]]) -> dict[str, Any]:
        unknown = {
            "usage_display": "unknown",
            "limit_display": "unknown",
            "remaining_display": "unknown",
            "percent_used": None,
            "percent_remaining": 0.0,
            "percent_used_display": "unknown",
            "percent_remaining_display": "unknown",
            "health": "unknown",
        }
        if not metrics:
            return unknown
        with_usage = [metric for metric in metrics if metric.get("percent_used") is not None]
        if not with_usage:
            first = metrics[0]
            return {**unknown, **{key: first.get(key, unknown.get(key)) for key in unknown}}
        return max(with_usage, key=lambda item: item.get("percent_used") or 0.0)


def collect_free_tier_usage(env: dict[str, str] | None = None) -> dict[str, Any]:
    return FreeTierCollector(env=env).collect()


if __name__ == "__main__":
    print(json.dumps(collect_free_tier_usage(), indent=2, sort_keys=True))
