#!/usr/bin/env python3
"""Collect read-only free-tier usage for the NutsNews Operations Portal."""

from __future__ import annotations

import json
import math
import os
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ALLOWED_SOURCE_STATUSES = {"live", "cached", "not configured", "unavailable", "unknown"}
ALLOWED_MEASUREMENT_STATUSES = {"measured", "missing credential", "unavailable", "unsupported", "unknown"}
ALLOWED_RISK_STATUSES = {"safe", "warning", "critical", "over_limit", "unknown", "not_configured"}
DEFAULT_WARNING_USED_PERCENT = 70.0
DEFAULT_CRITICAL_USED_PERCENT = 85.0
DEFAULT_OVER_LIMIT_USED_PERCENT = 100.0
DEFAULT_FREE_TIER_ENV_FILE = "/etc/nutsnews/free-tier-usage.env"
DEFAULT_VERCEL_FOCUS_MATCH_FIELDS = [
    "ServiceName",
    "ServiceCategory",
    "ChargeCategory",
    "ChargeDescription",
    "Description",
    "ResourceName",
    "PricingUnit",
    "ConsumedUnit",
    "Tags",
]
VERCEL_FOCUS_QUANTITY_FIELDS = ["ConsumedQuantity", "BilledQuantity", "UsageQuantity", "Quantity"]
FREE_TIER_ENV_PREFIXES = (
    "NUTSNEWS_FREE_TIER_",
    "NUTSNEWS_VERCEL_",
    "NUTSNEWS_SENTRY_",
    "NUTSNEWS_CLOUDFLARE_",
    "NUTSNEWS_BETTER_STACK_",
    "NUTSNEWS_SUPABASE_",
    "NUTSNEWS_GRAFANA_CLOUD_",
    "NUTSNEWS_GITHUB_",
)
CLOUDFLARE_WORKERS_USAGE_QUERY = """
query NutsNewsWorkersUsage($accountTag: string, $datetimeStart: string, $datetimeEnd: string) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      workersInvocationsAdaptive(
        limit: 10000,
        filter: {datetime_geq: $datetimeStart, datetime_leq: $datetimeEnd}
      ) {
        sum {
          requests
        }
      }
    }
  }
}
"""


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
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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


def read_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    loaded: dict[str, str] = {}
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return loaded, errors
    except OSError:
        return loaded, ["Free-tier environment file could not be read."]

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        try:
            parts = shlex.split(line, comments=False, posix=True)
        except ValueError:
            errors.append(f"Free-tier environment file line {line_number} could not be parsed.")
            continue
        if not parts or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        key = key.strip()
        if key.startswith(FREE_TIER_ENV_PREFIXES):
            loaded[key] = value
    return loaded, errors


def runtime_env_with_free_tier_file() -> tuple[dict[str, str], list[str]]:
    env = dict(os.environ)
    if env.get("NUTSNEWS_FREE_TIER_QUOTAS_JSON"):
        return env, []
    env_file = Path(env.get("NUTSNEWS_FREE_TIER_ENV_FILE", DEFAULT_FREE_TIER_ENV_FILE)).resolve()
    loaded, errors = read_env_file(env_file)
    for key, value in loaded.items():
        env.setdefault(key, value)
    return env, errors


def nested(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if part in {"__len__", "length"}:
            try:
                current = len(current)
            except TypeError:
                return None
        elif isinstance(current, dict):
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
    if unit == "%":
        return f"{display_number(value)}%"
    suffix = f" {unit}" if unit else ""
    return f"{display_number(value)}{suffix}"


def display_percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


def risk_counts(providers: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in ALLOWED_RISK_STATUSES}
    for provider in providers:
        status = str(provider.get("risk_status") or provider.get("health") or "unknown").lower()
        if status == "healthy":
            status = "safe"
        if status not in counts:
            status = "unknown"
        counts[status] += 1
    return counts


def summarize_providers(providers: list[dict[str, Any]]) -> dict[str, Any]:
    counts = risk_counts(providers)
    metrics = [
        metric
        for provider in providers
        for metric in provider.get("metrics", [])
        if isinstance(metric, dict)
    ]
    measured_metrics = [
        metric
        for metric in metrics
        if metric.get("measurement_status") == "measured" or metric.get("usage") is not None
    ]
    unavailable_metrics = [
        metric
        for metric in metrics
        if metric.get("measurement_status") in {"missing credential", "unavailable", "unknown"}
        and metric.get("usage") is None
    ]
    unsupported_metrics = [
        metric
        for metric in metrics
        if metric.get("measurement_status") == "unsupported"
    ]
    return {
        "total_services": len(providers),
        "total_metrics": len(metrics),
        "measured_metrics": len(measured_metrics),
        "unavailable_metrics": len(unavailable_metrics),
        "unsupported_metrics": len(unsupported_metrics),
        "safe": counts["safe"],
        "ok": counts["safe"],
        "warning": counts["warning"],
        "critical": counts["critical"],
        "over_limit": counts["over_limit"],
        "unknown": counts["unknown"],
        "not_configured": counts["not_configured"],
        "unknown_or_not_configured": counts["unknown"] + counts["not_configured"],
    }


class ApiResult:
    def __init__(self, status: str, metrics: dict[str, float] | None = None, detail: str = "") -> None:
        self.status = status if status in ALLOWED_SOURCE_STATUSES else "unknown"
        self.metrics = metrics or {}
        self.detail = detail


class ApiRequestError(Exception):
    def __init__(
        self,
        *,
        status_code: int | None = None,
        reason: str = "",
        content_type: str = "",
        body: bytes = b"",
        error_class: str = "",
    ) -> None:
        super().__init__(reason or error_class or "API request failed")
        self.status_code = status_code
        self.reason = reason
        self.content_type = content_type
        self.body = body[:4096]
        self.error_class = error_class


class JsonHttpClient:
    def encoded_url(self, url: str, params: dict[str, str] | None = None) -> str:
        if not params:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urllib.parse.urlencode(params, doseq=True)}"

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 8,
    ) -> Any:
        request = urllib.request.Request(self.encoded_url(url, params), headers=headers or {}, method="GET")
        return self.open_json(request, timeout)

    def get_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 8,
    ) -> str:
        request = urllib.request.Request(self.encoded_url(url, params), headers=headers or {}, method="GET")
        return self.open_text(request, timeout)

    def open_json(self, request: urllib.request.Request, timeout: int = 8) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                content_type = response.headers.get("content-type", "")
                try:
                    return json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiRequestError(
                        status_code=response.status,
                        content_type=content_type,
                        body=body,
                        error_class=exc.__class__.__name__,
                    ) from exc
        except urllib.error.HTTPError as exc:
            body = exc.read()
            raise ApiRequestError(
                status_code=exc.code,
                reason=str(exc.reason),
                content_type=exc.headers.get("content-type", "") if exc.headers else "",
                body=body,
                error_class="HTTPError",
            ) from exc
        except urllib.error.URLError as exc:
            raise ApiRequestError(reason=str(exc.reason), error_class=exc.__class__.__name__) from exc
        except (OSError, TimeoutError) as exc:
            raise ApiRequestError(reason=str(exc), error_class=exc.__class__.__name__) from exc

    def open_text(self, request: urllib.request.Request, timeout: int = 8) -> str:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                try:
                    return body.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ApiRequestError(
                        status_code=response.status,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                        error_class=exc.__class__.__name__,
                    ) from exc
        except urllib.error.HTTPError as exc:
            body = exc.read()
            raise ApiRequestError(
                status_code=exc.code,
                reason=str(exc.reason),
                content_type=exc.headers.get("content-type", "") if exc.headers else "",
                body=body,
                error_class="HTTPError",
            ) from exc
        except urllib.error.URLError as exc:
            raise ApiRequestError(reason=str(exc.reason), error_class=exc.__class__.__name__) from exc
        except (OSError, TimeoutError) as exc:
            raise ApiRequestError(reason=str(exc), error_class=exc.__class__.__name__) from exc

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: int = 8,
    ) -> Any:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        return self.open_json(request, timeout)


def response_shape(data: Any) -> str:
    if isinstance(data, dict):
        keys = ", ".join(sorted(str(key) for key in data.keys())[:10])
        return f"top-level keys: {keys or 'none'}"
    if isinstance(data, list):
        return f"top-level list length: {len(data)}"
    return f"top-level type: {type(data).__name__}"


def sanitize_text(value: Any, limit: int = 220) -> str:
    text = str(value).replace("\n", " ").strip()
    return text[:limit]


def response_message(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("message", "detail", "error", "errors"):
        value = data.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, list):
            if not value:
                continue
            first = value[0]
            if isinstance(first, dict):
                return sanitize_text(first.get("message") or first.get("detail") or first)
            return sanitize_text(first)
        if isinstance(value, dict):
            message = value.get("message") or value.get("detail") or value.get("code") or value
            return sanitize_text(message)
        return sanitize_text(value)
    return ""


def config_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


class FreeTierCollector:
    def __init__(
        self,
        env: dict[str, str] | None = None,
        http_client: JsonHttpClient | None = None,
        now: datetime | None = None,
    ) -> None:
        load_errors: list[str] = []
        if env is None:
            self.env, load_errors = runtime_env_with_free_tier_file()
        else:
            self.env = env
        self.http_client = http_client or JsonHttpClient()
        self.now = now or datetime.now(timezone.utc).replace(microsecond=0)
        self.errors: list[str] = load_errors

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
            "thresholds": {
                "warning_used_percent": DEFAULT_WARNING_USED_PERCENT,
                "critical_used_percent": DEFAULT_CRITICAL_USED_PERCENT,
                "over_limit_used_percent": DEFAULT_OVER_LIMIT_USED_PERCENT,
            },
            "summary": summarize_providers(providers),
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

        allow_usage_fallback = self.allow_usage_fallback(provider_config)
        if source_status != "live" and allow_usage_fallback:
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
                source_detail = source_detail or "No live API credentials or usage snapshot configured."
            elif source_status == "unknown":
                source_detail = source_detail or "Provider usage source is unknown."
            else:
                source_detail = source_detail or "Provider usage could not be read."
        elif source_status != "live":
            if source_detail:
                source_detail = f"{source_detail} Snapshot/cache fallback is disabled for this provider."
            elif source_status == "not configured":
                source_detail = "No live API credentials configured; snapshot/cache fallback is disabled for this provider."
            elif source_status == "unknown":
                source_detail = "Provider usage source is unknown; snapshot/cache fallback is disabled for this provider."
            else:
                source_detail = "Provider usage could not be read; snapshot/cache fallback is disabled for this provider."

        if source_status == "cached":
            cached_age = age_seconds(last_checked_at, self.now)
            stale = cached_age is not None and cached_age > cache_ttl_seconds
            if stale:
                source_detail = f"{source_detail} Cached data is stale."

        metrics = [
            self.metric_result(
                metric_config,
                usage_source.get(str(metric_config.get("key", ""))),
                source_status,
                provider_config,
                source_detail,
            )
            for metric_config in metrics_config
        ]
        metric_status_counts = self.measurement_status_counts(metrics)
        primary = self.primary_metric(metrics)
        if not metrics:
            source_status = "unknown"
            source_detail = "No quota metrics are configured for this provider."

        return {
            "key": key,
            "platform": platform,
            "plan": provider_config.get("plan", "Free"),
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
            "risk_status": self.provider_risk_status(source_status, primary),
            "risk_label": self.provider_risk_status(source_status, primary).replace("_", " "),
            "metric_status_counts": metric_status_counts,
            "metrics": metrics,
        }

    def provider_risk_status(self, source_status: str, primary: dict[str, Any]) -> str:
        risk_status = str(primary.get("risk_status") or primary.get("health") or "unknown").lower()
        if risk_status == "healthy":
            risk_status = "safe"
        if risk_status in {"unknown", ""}:
            if source_status == "not configured":
                return "not_configured"
            if source_status in {"unavailable", "unknown"}:
                return "unknown"
        return risk_status if risk_status in ALLOWED_RISK_STATUSES else "unknown"

    def allow_usage_fallback(self, provider_config: dict[str, Any]) -> bool:
        live = provider_config.get("live")
        live_fallback = live.get("allow_usage_fallback") if isinstance(live, dict) else None
        provider_fallback = provider_config.get("allow_usage_fallback")
        if live_fallback is not None:
            return config_bool(live_fallback, True)
        return config_bool(provider_fallback, True)

    def collect_live(self, provider_config: dict[str, Any]) -> ApiResult:
        live = provider_config.get("live")
        if not isinstance(live, dict) or not live.get("type"):
            return ApiResult("not configured")
        live_type = str(live.get("type", "")).strip()
        if live_type == "sentry_stats_v2":
            return self.collect_sentry_stats(provider_config, live)
        if live_type == "github_actions":
            return self.collect_github_actions(live)
        if live_type == "cloudflare_graphql":
            return self.collect_cloudflare_graphql(live)
        if live_type == "grafana_cloud_usage":
            return self.collect_grafana_cloud_usage(live)
        if live_type == "vercel_billing_charges":
            return self.collect_vercel_billing_charges(live)
        if live_type == "json_api":
            return self.collect_json_api(live)
        return ApiResult("unknown", detail="Unsupported live usage collector type.")

    def collect_grafana_cloud_usage(self, live: dict[str, Any]) -> ApiResult:
        url_env = str(live.get("url_env", "NUTSNEWS_GRAFANA_CLOUD_URL")).strip()
        token_env = str(live.get("token_env", "NUTSNEWS_GRAFANA_CLOUD_SERVICE_ACCOUNT_TOKEN")).strip()
        datasource_uid_env = str(live.get("usage_datasource_uid_env", "NUTSNEWS_GRAFANA_CLOUD_USAGE_DATASOURCE_UID")).strip()
        base_url = self.env.get(url_env, "").strip().rstrip("/") if url_env else ""
        token = self.env.get(token_env, "").strip() if token_env else ""
        datasource_uid = self.env.get(datasource_uid_env, "").strip() if datasource_uid_env else ""
        if not base_url or not token or not datasource_uid:
            missing = []
            if not base_url:
                missing.append(url_env or "Grafana Cloud URL")
            if not token:
                missing.append(token_env or "Grafana Cloud service account token")
            if not datasource_uid:
                missing.append(datasource_uid_env or "Grafana Cloud usage datasource UID")
            return ApiResult(
                "not configured",
                detail=f"Missing {', '.join(missing)}; configure read-only Grafana Cloud usage datasource access.",
            )
        if not base_url.startswith("https://"):
            return ApiResult("unavailable", detail="Grafana Cloud URL must use HTTPS.")

        queries = live.get("queries", {})
        if not isinstance(queries, dict) or not queries:
            return ApiResult("unknown", detail="Grafana Cloud usage queries are not configured.")

        endpoint = (
            f"{base_url}/api/datasources/proxy/uid/"
            f"{urllib.parse.quote(datasource_uid, safe='')}/api/v1/query"
        )
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        metrics: dict[str, float] = {}
        failures: list[str] = []
        for metric_key, query in queries.items():
            metric = str(metric_key)
            try:
                data = self.http_client.get_json(
                    endpoint,
                    headers=headers,
                    params={"query": str(query)},
                    timeout=self.http_timeout(),
                )
            except ApiRequestError as exc:
                return ApiResult("unavailable", detail=self.api_error_detail("Grafana Cloud usage datasource", exc))
            value = self.prometheus_vector_value(data)
            if value is None:
                failures.append(metric)
                continue
            metrics[metric] = value

        if not metrics:
            missing = ", ".join(str(key) for key in queries.keys())
            detail = f"Grafana Cloud usage datasource did not return configured metric values; missing metrics: {missing}."
            if failures:
                detail = f"{detail} Empty or non-numeric query results: {', '.join(failures)}."
            return ApiResult("unavailable", detail=detail)

        detail = "Usage loaded from Grafana Cloud usage datasource."
        if failures:
            detail = f"{detail} Some metrics are unavailable: {', '.join(failures)}."
        return ApiResult("live", metrics=metrics, detail=detail)

    def prometheus_vector_value(self, data: Any) -> float | None:
        if not isinstance(data, dict) or data.get("status") not in {None, "success"}:
            return None
        result = nested(data, "data.result")
        if not isinstance(result, list):
            return None
        values = []
        for item in result:
            value = nested(item, "value.1")
            number = safe_float(value)
            if number is not None:
                values.append(number)
        if not values:
            return None
        return max(values)

    def collect_json_api(self, live: dict[str, Any]) -> ApiResult:
        url_env = str(live.get("url_env", "")).strip()
        token_env = str(live.get("token_env", "")).strip()
        configured_url = self.env.get(url_env, "").strip() if url_env else ""
        url = configured_url or str(live.get("url", "")).strip()
        token = self.env.get(token_env, "").strip() if token_env else ""
        if not url or (token_env and not token):
            missing = []
            if not url:
                missing.append(url_env or "live usage URL")
            if token_env and not token:
                missing.append(token_env)
            return ApiResult(
                "not configured",
                detail=f"Missing {', '.join(missing)}; configure a read-only provider usage source.",
            )
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
            data = self.http_client.get_json(
                url,
                headers=headers,
                params=self.query_params(live),
                timeout=self.http_timeout(),
            )
        except ApiRequestError as exc:
            return ApiResult("unavailable", detail=self.api_error_detail("Provider API", exc))
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            return ApiResult("unavailable", detail=f"Provider API request failed: {exc.__class__.__name__}.")

        metric_paths = live.get("metric_paths", {})
        if not isinstance(metric_paths, dict):
            return ApiResult("unknown", detail="Provider API metric paths are not configured.")

        metrics: dict[str, float] = {}
        for metric_key, path in metric_paths.items():
            value = safe_float(nested(data, str(path)))
            if value is not None:
                metrics[str(metric_key)] = value
        if not metrics:
            missing = ", ".join(str(key) for key in metric_paths.keys())
            detail = f"Provider API response did not include configured metric values ({response_shape(data)}; missing metrics: {missing})."
            message = response_message(data)
            if message:
                detail = f"{detail} Provider message: {message}."
            return ApiResult("unavailable", detail=detail)
        return ApiResult("live", metrics=metrics, detail="Usage loaded from provider API.")

    def collect_vercel_billing_charges(self, live: dict[str, Any]) -> ApiResult:
        url_env = str(live.get("url_env", "NUTSNEWS_VERCEL_USAGE_API_URL")).strip()
        token_env = str(live.get("token_env", "NUTSNEWS_VERCEL_API_TOKEN")).strip()
        url = self.env.get(url_env, "").strip() if url_env else ""
        token = self.env.get(token_env, "").strip() if token_env else ""
        if not url or not token:
            missing = []
            if not url:
                missing.append(url_env or "Vercel billing charges URL")
            if not token:
                missing.append(token_env or "Vercel API token")
            return ApiResult(
                "not configured",
                detail=f"Missing {', '.join(missing)}; configure read-only Vercel billing usage access.",
            )
        if not url.startswith("https://"):
            return ApiResult("unavailable", detail="Vercel billing charges endpoint must use HTTPS.")
        if str(live.get("method", "GET")).upper() != "GET":
            return ApiResult("unavailable", detail="Only read-only GET Vercel usage requests are supported.")

        headers = {
            "Accept": "application/x-ndjson, application/json;q=0.9",
            "Authorization": f"Bearer {token}",
        }
        try:
            body = self.http_client.get_text(
                url,
                headers=headers,
                params=self.query_params(live),
                timeout=self.http_timeout(),
            )
        except ApiRequestError as exc:
            return ApiResult("unavailable", detail=self.vercel_api_error_detail(exc))
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            return ApiResult("unavailable", detail=f"Vercel billing charges API request failed: {exc.__class__.__name__}.")

        records, parse_detail = self.vercel_focus_records(body)
        if not records:
            detail = parse_detail or "Vercel billing charges response did not include FOCUS charge records."
            return ApiResult("unavailable", detail=detail)

        metrics = self.vercel_focus_metrics(records, live.get("focus_mappings", {}))
        if not metrics:
            sample_services = sorted(
                {
                    sanitize_text(row.get("ServiceName") or row.get("ServiceCategory") or "unknown", limit=80)
                    for row in records
                    if isinstance(row, dict)
                }
            )[:5]
            detail = "Vercel billing charges did not include configured quota metric records."
            if sample_services:
                detail = f"{detail} Returned services: {', '.join(sample_services)}."
            return ApiResult("unavailable", detail=detail)

        configured_keys = set((live.get("focus_mappings") or {}).keys())
        missing = sorted(configured_keys - set(metrics.keys()))
        detail = "Usage loaded from Vercel FOCUS billing charges API."
        if missing:
            detail = f"{detail} Missing configured metrics: {', '.join(missing)}."
        return ApiResult("live", metrics=metrics, detail=detail)

    def vercel_focus_records(self, body: str) -> tuple[list[dict[str, Any]], str]:
        stripped = body.strip()
        if not stripped:
            return [], "Vercel billing charges response was empty."

        if stripped[0] in "[{":
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)], ""
            if isinstance(parsed, dict):
                if self.looks_like_vercel_focus_record(parsed):
                    return [parsed], ""
                message = response_message(parsed)
                if message:
                    return [], f"Vercel billing charges API returned a message: {message}."
                data = parsed.get("data") or parsed.get("charges") or parsed.get("items") or parsed.get("rows")
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)], ""
                return [], f"Vercel billing charges response was JSON but not FOCUS charges ({response_shape(parsed)})."

        records = []
        malformed = 0
        for line in stripped.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(item, dict):
                records.append(item)
        if not records and malformed:
            return [], "Vercel billing charges JSONL response could not be parsed."
        detail = f" Ignored {malformed} malformed JSONL line(s)." if malformed else ""
        return records, detail.strip()

    def looks_like_vercel_focus_record(self, data: dict[str, Any]) -> bool:
        return any(key in data for key in ("ConsumedQuantity", "ConsumedUnit", "ServiceName", "ChargePeriodStart"))

    def vercel_focus_metrics(self, records: list[dict[str, Any]], mappings: Any) -> dict[str, float]:
        if not isinstance(mappings, dict):
            return {}
        metrics = {str(key): 0.0 for key in mappings.keys()}
        matched = {str(key): False for key in mappings.keys()}
        for record in records:
            quantity = self.vercel_focus_quantity(record)
            if quantity is None:
                continue
            for metric_key, raw_mapping in mappings.items():
                key = str(metric_key)
                if not isinstance(raw_mapping, dict):
                    continue
                if not self.vercel_focus_record_matches(record, raw_mapping):
                    continue
                multiplier = safe_float(raw_mapping.get("usage_multiplier"))
                metrics[key] += quantity * (1.0 if multiplier is None else multiplier)
                matched[key] = True
        return {key: round(value, 4) for key, value in metrics.items() if matched.get(key)}

    def vercel_focus_quantity(self, record: dict[str, Any]) -> float | None:
        for field in VERCEL_FOCUS_QUANTITY_FIELDS:
            value = safe_float(record.get(field))
            if value is not None:
                return value
        return None

    def vercel_focus_record_matches(self, record: dict[str, Any], mapping: dict[str, Any]) -> bool:
        fields = mapping.get("fields", DEFAULT_VERCEL_FOCUS_MATCH_FIELDS)
        haystack_parts = []
        if isinstance(fields, list):
            haystack_parts = [self.vercel_focus_field_text(record.get(str(field), "")) for field in fields]
        haystack = self.normalized_text(" ".join(haystack_parts))

        required_any = mapping.get("contains_any", [])
        if isinstance(required_any, str):
            required_any = [required_any]
        if required_any and not any(self.normalized_text(term) in haystack for term in required_any):
            return False

        required_all = mapping.get("contains_all", [])
        if isinstance(required_all, str):
            required_all = [required_all]
        if required_all and not all(self.normalized_text(term) in haystack for term in required_all):
            return False

        unit_any = mapping.get("unit_contains_any", [])
        if isinstance(unit_any, str):
            unit_any = [unit_any]
        unit = self.normalized_text(
            " ".join(
                self.vercel_focus_field_text(record.get(field, ""))
                for field in ("ConsumedUnit", "PricingUnit")
            )
        )
        if unit_any and not any(self.normalized_text(term) in unit for term in unit_any):
            return False
        return True

    def vercel_focus_field_text(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, sort_keys=True)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    def normalized_text(self, value: Any) -> str:
        return " ".join(str(value).lower().replace("_", " ").replace("-", " ").split())

    def collect_sentry_stats(self, provider_config: dict[str, Any], live: dict[str, Any]) -> ApiResult:
        token_env = str(live.get("token_env", "NUTSNEWS_SENTRY_AUTH_TOKEN")).strip()
        org_env = str(live.get("org_env", "NUTSNEWS_SENTRY_ORG")).strip()
        base_url_env = str(live.get("base_url_env", "NUTSNEWS_SENTRY_BASE_URL")).strip()
        token = self.env.get(token_env, "").strip()
        org = self.env.get(org_env, "").strip()
        base_url = self.env.get(base_url_env, "https://sentry.io").strip().rstrip("/")
        if base_url.endswith("/api/0"):
            base_url = base_url[: -len("/api/0")]
        if not token or not org:
            missing = []
            if not token:
                missing.append(token_env)
            if not org:
                missing.append(org_env)
            return ApiResult(
                "not configured",
                detail=f"Missing {', '.join(missing)}; configure a Sentry token and org slug with read access to Stats v2.",
            )
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

        failures = []
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
            except ApiRequestError as exc:
                failures.append(self.api_error_detail("Sentry stats API", exc))
                continue
            except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                failures.append(f"Sentry stats API request failed: {exc.__class__.__name__}.")
                continue
            total = 0.0
            for group in data.get("groups", []) if isinstance(data, dict) else []:
                totals = group.get("totals", {}) if isinstance(group, dict) else {}
                value = safe_float(totals.get("sum(quantity)"))
                if value is not None:
                    total += value
            metrics[metric_key] = total

        if not metrics:
            detail = failures[0] if failures else "Sentry stats could not be read."
            return ApiResult("unavailable", detail=detail)
        return ApiResult("live", metrics=metrics, detail="Usage loaded from Sentry stats API.")

    def collect_cloudflare_graphql(self, live: dict[str, Any]) -> ApiResult:
        url_env = str(live.get("url_env", "NUTSNEWS_CLOUDFLARE_USAGE_API_URL")).strip()
        token_env = str(live.get("token_env", "NUTSNEWS_CLOUDFLARE_API_TOKEN")).strip()
        account_id_env = str(live.get("account_id_env", "NUTSNEWS_CLOUDFLARE_ACCOUNT_ID")).strip()
        url = self.env.get(url_env, "").strip() or "https://api.cloudflare.com/client/v4/graphql"
        token = self.env.get(token_env, "").strip()
        account_id = self.env.get(account_id_env, "").strip()
        if not url or not token or not account_id:
            missing = []
            if not url:
                missing.append(url_env)
            if not token:
                missing.append(token_env)
            if not account_id:
                missing.append(account_id_env)
            return ApiResult(
                "not configured",
                detail=f"Missing {', '.join(missing)}; configure Cloudflare GraphQL read-only usage access.",
            )
        if not url.startswith("https://"):
            return ApiResult("unavailable", detail="Cloudflare GraphQL endpoint must use HTTPS.")

        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        datetime_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        datetime_end = self.now.isoformat().replace("+00:00", "Z")
        body = {
            "query": CLOUDFLARE_WORKERS_USAGE_QUERY,
            "variables": {
                "accountTag": account_id,
                "datetimeStart": datetime_start,
                "datetimeEnd": datetime_end,
            },
        }
        try:
            data = self.http_client.post_json(url, body, headers=headers, timeout=self.http_timeout())
        except ApiRequestError as exc:
            return ApiResult("unavailable", detail=self.api_error_detail("Cloudflare GraphQL API", exc))
        except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            return ApiResult("unavailable", detail=f"Cloudflare GraphQL API request failed: {exc.__class__.__name__}.")

        if isinstance(data, dict) and data.get("errors"):
            message = response_message(data)
            detail = f"Cloudflare GraphQL API returned errors ({response_shape(data)})."
            if message:
                detail = f"{detail} Provider message: {message}."
            return ApiResult("unavailable", detail=detail)

        accounts = nested(data, "data.viewer.accounts")
        if not isinstance(accounts, list) or not accounts:
            return ApiResult(
                "unavailable",
                detail=f"Cloudflare GraphQL API did not return account data for {account_id_env}.",
            )
        rows = accounts[0].get("workersInvocationsAdaptive") if isinstance(accounts[0], dict) else None
        if not isinstance(rows, list):
            return ApiResult(
                "unavailable",
                detail=f"Cloudflare GraphQL API response did not include Workers analytics ({response_shape(data)}).",
            )

        requests = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = safe_float(nested(row, "sum.requests"))
            if value is not None:
                requests += value
        return ApiResult(
            "live",
            metrics={"workers_requests": requests},
            detail="Workers requests loaded from Cloudflare GraphQL Analytics API. Pages and R2 quota metrics require a normalized snapshot or a dedicated collector.",
        )

    def collect_github_actions(self, live: dict[str, Any]) -> ApiResult:
        token_env = str(live.get("token_env", "NUTSNEWS_GITHUB_USAGE_API_TOKEN")).strip()
        url_env = str(live.get("url_env", "NUTSNEWS_GITHUB_ACTIONS_USAGE_API_URL")).strip()
        token = self.env.get(token_env, "").strip()
        base_url = (self.env.get(url_env, "").strip() if url_env else "") or str(live.get("url", "")).strip()
        if not base_url:
            return ApiResult("not configured", detail=f"Missing {url_env}; configure the repository REST API URL.")
        if not base_url.startswith("https://"):
            return ApiResult("unavailable", detail="GitHub Actions usage API URL must use HTTPS.")

        base_url = base_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        metrics: dict[str, float] = {}
        failures = []

        try:
            cache = self.http_client.get_json(f"{base_url}/actions/cache/usage", headers=headers, timeout=self.http_timeout())
        except ApiRequestError as exc:
            failures.append(self.api_error_detail("GitHub Actions cache API", exc))
        else:
            cache_bytes = safe_float(nested(cache, "active_caches_size_in_bytes"))
            if cache_bytes is not None:
                metrics["cache_storage_gb"] = round(cache_bytes / (1024**3), 4)

        try:
            artifacts = self.http_client.get_json(
                f"{base_url}/actions/artifacts",
                headers=headers,
                params={"per_page": "100"},
                timeout=self.http_timeout(),
            )
        except ApiRequestError as exc:
            failures.append(self.api_error_detail("GitHub Actions artifacts API", exc))
        else:
            artifact_items = artifacts.get("artifacts", []) if isinstance(artifacts, dict) else []
            if isinstance(artifact_items, list):
                total_bytes = sum(safe_float(item.get("size_in_bytes")) or 0.0 for item in artifact_items if isinstance(item, dict))
                metrics["artifact_storage_mb"] = round(total_bytes / (1024**2), 2)

        if token:
            try:
                rate = self.http_client.get_json("https://api.github.com/rate_limit", headers=headers, timeout=self.http_timeout())
            except ApiRequestError as exc:
                failures.append(self.api_error_detail("GitHub rate-limit API", exc))
            else:
                used = safe_float(nested(rate, "resources.core.used"))
                if used is not None:
                    metrics["rest_api_requests"] = used

        if not metrics:
            detail = failures[0] if failures else "GitHub Actions usage could not be read."
            if not token:
                detail = (
                    f"Missing {token_env}; unauthenticated public GitHub Actions usage could not be read. "
                    f"{detail}"
                )
                return ApiResult("not configured", detail=detail)
            return ApiResult("unavailable", detail=detail)

        if token:
            detail = "Usage loaded from read-only GitHub REST APIs."
        else:
            detail = (
                "Usage loaded from public GitHub REST APIs without a token. "
                f"Configure {token_env} for private repository access and authenticated REST rate-limit telemetry."
            )
        if failures:
            detail = f"{detail} Some metrics are unavailable: {failures[0]}"
        if "hosted_runner_minutes" not in metrics:
            detail = f"{detail} Hosted-runner billing minutes require a separate billing-scoped endpoint and remain unknown."
        return ApiResult("live", metrics=metrics, detail=detail)

    def query_params(self, live: dict[str, Any]) -> dict[str, str]:
        raw_params = live.get("query_params", {})
        if not isinstance(raw_params, dict):
            return {}
        params: dict[str, str] = {}
        for key, value in raw_params.items():
            rendered = self.dynamic_param_value(str(value))
            if rendered:
                params[str(key)] = rendered
        return params

    def dynamic_param_value(self, value: str) -> str:
        if value == "__current_month_start_iso__":
            start = self.now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat().replace("+00:00", "Z")
        if value == "__next_month_start_iso__":
            year = self.now.year + (1 if self.now.month == 12 else 0)
            month = 1 if self.now.month == 12 else self.now.month + 1
            start = self.now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat().replace("+00:00", "Z")
        if value == "__current_day_start_iso__":
            start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat().replace("+00:00", "Z")
        if value == "__next_day_start_iso__":
            start = (self.now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat().replace("+00:00", "Z")
        if value == "__now_iso__":
            return self.now.isoformat().replace("+00:00", "Z")
        if value == "__current_month_number__":
            return str(self.now.month)
        if value == "__current_year__":
            return str(self.now.year)
        return value

    def api_error_detail(self, label: str, exc: ApiRequestError) -> str:
        parts = [f"{label} request failed"]
        if exc.status_code is not None:
            parts.append(f"HTTP {exc.status_code}")
        elif exc.error_class:
            parts.append(exc.error_class)
        if exc.content_type:
            parts.append(f"content-type {exc.content_type.split(';')[0]}")
        message = self.api_error_message(exc)
        if message:
            parts.append(f"message: {message}")
        elif exc.reason:
            parts.append(f"reason: {sanitize_text(exc.reason)}")
        return "; ".join(parts) + "."

    def api_error_message(self, exc: ApiRequestError) -> str:
        if not exc.body:
            return ""
        try:
            return response_message(json.loads(exc.body.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ""

    def vercel_api_error_detail(self, exc: ApiRequestError) -> str:
        detail = self.api_error_detail("Vercel billing charges API", exc)
        message = self.api_error_message(exc).lower()
        if exc.status_code == 404 and "cost" in message and "not found" in message:
            return (
                f"{detail} Vercel returned Costs not found for the configured Billing Charges request; "
                "verify the protected teamId or slug, account billing visibility, and token role. "
                "Keep Vercel live usage unavailable until Vercel exposes a supported read-only usage source."
            )
        return detail

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
        provider: dict[str, Any] = {}
        if isinstance(snapshot.get(provider_key), dict):
            provider = {**provider, **snapshot[provider_key]}
        providers = snapshot.get("providers")
        if isinstance(providers, dict) and isinstance(providers.get(provider_key), dict):
            provider = {**provider, **providers[provider_key]}
        elif isinstance(providers, list):
            for item in providers:
                if isinstance(item, dict) and item.get("key") == provider_key:
                    provider = {**provider, **item}
                    break
        usage = snapshot.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get(provider_key), dict):
                provider = {**provider, **usage[provider_key]}
            elif snapshot.get("key") == provider_key or snapshot.get("provider") == provider_key:
                provider = {**provider, "usage": usage}
        if isinstance(snapshot.get("metrics"), (dict, list)) and (
            snapshot.get("key") == provider_key or snapshot.get("provider") == provider_key
        ):
            provider = {**provider, "metrics": snapshot["metrics"]}

        metrics: dict[str, float] = {}
        raw_metric_sources = []
        for field in ("metrics", "usage"):
            raw = provider.get(field)
            if isinstance(raw, (dict, list)):
                raw_metric_sources.append(raw)
        raw_metric_sources.append(provider)
        for raw_metrics in raw_metric_sources:
            self.merge_snapshot_metrics(metrics, raw_metrics)
        if not metrics:
            return {}
        return {
            "metrics": metrics,
            "last_checked_at": provider.get("last_checked_at")
            or provider.get("updated_at")
            or snapshot.get("last_checked_at")
            or snapshot.get("updated_at")
            or snapshot.get("generated_at"),
        }

    def merge_snapshot_metrics(self, metrics: dict[str, float], raw_metrics: Any) -> None:
        if isinstance(raw_metrics, dict):
            for key, value in raw_metrics.items():
                number = self.snapshot_metric_value(value)
                if number is not None:
                    metrics[str(key)] = number
        elif isinstance(raw_metrics, list):
            for item in raw_metrics:
                if not isinstance(item, dict) or "key" not in item:
                    continue
                number = self.snapshot_metric_value(item)
                if number is not None:
                    metrics[str(item["key"])] = number

    def snapshot_metric_value(self, value: Any) -> float | None:
        if isinstance(value, dict):
            for field in ("usage", "value", "current", "used"):
                number = safe_float(value.get(field))
                if number is not None:
                    return number
            return None
        return safe_float(value)

    def measurement_status(self, metric_config: dict[str, Any], usage: float | None, source_status: str) -> str:
        if usage is not None:
            return "measured"
        configured_status = str(metric_config.get("measurement_status") or "").strip().lower()
        if configured_status in ALLOWED_MEASUREMENT_STATUSES:
            return configured_status
        usage_source = str(metric_config.get("usage_source") or "").strip().lower()
        if usage_source == "unsupported":
            return "unsupported"
        if source_status == "not configured":
            return "missing credential"
        if source_status in {"live", "cached", "unavailable"}:
            return "unavailable"
        return "unknown"

    def measurement_detail(
        self,
        metric_config: dict[str, Any],
        measurement_status: str,
        source_status: str,
        source_detail: str = "",
    ) -> str:
        configured_detail = str(metric_config.get("measurement_detail") or "").strip()
        if configured_detail:
            return configured_detail
        if measurement_status == "measured":
            return "Usage was measured by the configured read-only source."
        if measurement_status in {"missing credential", "unavailable", "unknown"} and source_detail:
            return source_detail
        if measurement_status == "missing credential":
            return "Usage source is not configured for this metric."
        if measurement_status == "unsupported":
            return "No read-only usage source is wired for this quota yet."
        if measurement_status == "unavailable" and source_status in {"live", "cached"}:
            return "The configured usage source did not return this metric."
        if measurement_status == "unavailable":
            return "The configured usage source could not be read."
        return "Metric usage is unknown."

    def measurement_status_counts(self, metrics: list[dict[str, Any]]) -> dict[str, int]:
        counts = {status: 0 for status in ALLOWED_MEASUREMENT_STATUSES}
        for metric in metrics:
            status = str(metric.get("measurement_status") or "unknown").lower()
            if status not in counts:
                status = "unknown"
            counts[status] += 1
        return counts

    def metric_result(
        self,
        metric_config: dict[str, Any],
        raw_usage: Any,
        source_status: str = "unknown",
        provider_config: dict[str, Any] | None = None,
        source_detail: str = "",
    ) -> dict[str, Any]:
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
        over_limit = safe_float(metric_config.get("over_limit_used_percent")) or DEFAULT_OVER_LIMIT_USED_PERCENT
        if used_percent is None:
            risk_status = "unknown"
        elif used_percent >= over_limit or (remaining is not None and remaining < 0):
            risk_status = "over_limit"
        elif used_percent >= critical:
            risk_status = "critical"
        elif used_percent >= warning:
            risk_status = "warning"
        else:
            risk_status = "safe"
        provider_config = provider_config or {}
        measurement_status = self.measurement_status(metric_config, usage, source_status)
        reset_at = self.dynamic_param_value(str(metric_config.get("reset_at", "unknown")))
        unmeasured_display = self.unmeasured_display_label(provider_config, measurement_status, usage)
        usage_display = display_amount(usage, unit)
        remaining_display = display_amount(remaining, unit)
        percent_used_display = display_percent(used_percent)
        percent_remaining_display = display_percent(remaining_percent)
        if unmeasured_display:
            usage_display = unmeasured_display
            remaining_display = unmeasured_display
            percent_used_display = unmeasured_display
            percent_remaining_display = unmeasured_display
            if reset_at == "unknown" and measurement_status == "unsupported":
                reset_at = "unsupported"

        return {
            "key": key,
            "label": label,
            "unit": unit,
            "period": metric_config.get("period", ""),
            "reset_at": reset_at,
            "description": metric_config.get("description", ""),
            "quota_source": metric_config.get("quota_source") or provider_config.get("quota_source", ""),
            "quota_last_verified": metric_config.get("quota_last_verified")
            or provider_config.get("quota_last_verified", ""),
            "usage_source": metric_config.get("usage_source", ""),
            "measurement_status": measurement_status,
            "measurement_detail": self.measurement_detail(
                metric_config,
                measurement_status,
                source_status,
                source_detail,
            ),
            "usage": None if usage is None else round(usage, 2),
            "limit": limit,
            "remaining": remaining,
            "percent_used": used_percent,
            "percent_remaining": remaining_percent,
            "usage_display": usage_display,
            "limit_display": display_amount(limit, unit),
            "remaining_display": remaining_display,
            "percent_used_display": percent_used_display,
            "percent_remaining_display": percent_remaining_display,
            "health": "healthy" if risk_status == "safe" else risk_status,
            "risk_status": risk_status,
            "risk_label": risk_status.replace("_", " "),
        }

    def unmeasured_display_label(
        self,
        provider_config: dict[str, Any],
        measurement_status: str,
        usage: float | None,
    ) -> str:
        if usage is not None:
            return ""
        if not config_bool(provider_config.get("display_unmeasured_status"), False):
            return ""
        if measurement_status in {"missing credential", "unavailable", "unsupported"}:
            return measurement_status
        return ""

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
            "risk_status": "unknown",
            "risk_label": "unknown",
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
