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
ALLOWED_RISK_STATUSES = {"safe", "warning", "critical", "over_limit", "unknown", "not_configured"}
DEFAULT_WARNING_USED_PERCENT = 70.0
DEFAULT_CRITICAL_USED_PERCENT = 85.0
DEFAULT_OVER_LIMIT_USED_PERCENT = 100.0
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
    return {
        "total_services": len(providers),
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
        return self.open_json(request, timeout)

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
                source_detail = source_detail or "No live API credentials or usage snapshot configured."
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
        if live_type == "json_api":
            return self.collect_json_api(live)
        return ApiResult("unknown", detail="Unsupported live usage collector type.")

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
        message = ""
        if exc.body:
            try:
                message = response_message(json.loads(exc.body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = ""
        if message:
            parts.append(f"message: {message}")
        elif exc.reason:
            parts.append(f"reason: {sanitize_text(exc.reason)}")
        return "; ".join(parts) + "."

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

        return {
            "key": key,
            "label": label,
            "unit": unit,
            "period": metric_config.get("period", ""),
            "reset_at": metric_config.get("reset_at", "unknown"),
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
            "health": "healthy" if risk_status == "safe" else risk_status,
            "risk_status": risk_status,
            "risk_label": risk_status.replace("_", " "),
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
