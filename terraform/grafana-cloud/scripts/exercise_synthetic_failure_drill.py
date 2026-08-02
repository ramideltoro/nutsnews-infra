#!/usr/bin/env python3
"""Exercise one bounded Synthetic Monitoring assertion and always restore it."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, NoReturn


JOBS = (
    "canonical_articles_api",
    "canonical_homepage",
    "canonical_readiness",
    "vercel_secondary_readiness",
    "vps_readiness",
)
JOB = "canonical_readiness"
DRILL = "synthetic-mismatch"
ALERT_UID = "nn-sm-probe-failure"
VARIANTS = ("status", "body", "header")
GRAFANA_UI_HOSTNAME = "kindcantaloupe2036.grafana.net"
SYNTHETIC_MONITORING_HOSTNAME = re.compile(
    r"synthetic-monitoring-api(?:[.-][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.grafana\.net",
    re.ASCII,
)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every HTTP redirect into an error before bearer credentials can move."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None
EXPECTED_LABELS = {
    "service_namespace": "nutsnews",
    "deployment_environment": "production",
    "owner": "nutsnews-observability",
    "service": "synthetic-monitoring",
}


def select_job(value: str) -> None:
    global JOB
    if value not in JOBS:
        fail("synthetic drill target is not in the approved five-check allowlist")
    JOB = value
CHECK_FIELDS = {
    "alertSensitivity",
    "basicMetricsOnly",
    "channels",
    "created",
    "description",
    "disableReason",
    "enabled",
    "folderUid",
    "frequency",
    "id",
    "job",
    "labels",
    "modified",
    "offset",
    "probes",
    "settings",
    "target",
    "tenantId",
    "timeout",
}
UPDATE_FIELDS = CHECK_FIELDS - {"created", "disableReason", "modified"}
SETTINGS_FIELDS = {
    "browser",
    "dns",
    "grpc",
    "http",
    "multihttp",
    "ping",
    "scripted",
    "tcp",
    "traceroute",
}
SENSITIVE_HTTP_FIELDS = {
    "basicAuth",
    "bearerToken",
    "body",
    "cacheBustingQueryParamName",
    "headers",
    "oauth2Config",
    "proxyConnectHeaders",
    "proxyURL",
    "secretManagerEnabled",
}
MUTATION_FIELDS = {
    "status": "validStatusCodes",
    "body": "failIfBodyNotMatchesRegexp",
    "header": "failIfHeaderNotMatchesRegexp",
}
ASSERTION_SET_FIELDS = {
    "validStatusCodes",
    "failIfBodyMatchesRegexp",
    "failIfBodyNotMatchesRegexp",
    "failIfHeaderMatchesRegexp",
    "failIfHeaderNotMatchesRegexp",
}
BODY_MISMATCH = "^NUTSNEWS_GRAFANA_FAILURE_DRILL_BODY_9d2f3b8a$"
HEADER_MISMATCH = {
    "allowMissing": False,
    "header": "X-NutsNews-Grafana-Failure-Drill",
    "regexp": "^present$",
}
WATCHDOG_WORKFLOW = "grafana-synthetic-recovery-watchdog"
WATCHDOG_PARENT_REPOSITORY = "ramideltoro/nutsnews-infra"
WATCHDOG_MAX_HOLD_SECONDS = 7200
WATCHDOG_ARM_MAX_AGE_SECONDS = 1200
WATCHDOG_ARM_FIELDS = {
    "armed_at",
    "check_id",
    "drill",
    "exact_restore_payload_ready",
    "max_hold_seconds",
    "mutation_performed",
    "parent_head_sha",
    "parent_repository",
    "parent_run_attempt",
    "parent_run_id",
    "private_snapshot_uploaded",
    "probe_count",
    "result",
    "safe_metadata_only",
    "schema_version",
    "snapshot_payload_hmac_sha256",
    "snapshot_ready",
    "status",
    "target",
    "variant",
    "watchdog_nonce_sha256",
    "watchdog_run_attempt",
    "watchdog_run_id",
    "workflow",
}


class DrillError(RuntimeError):
    """A bounded failure that is safe to summarize without remote values."""


def fail(message: str) -> NoReturn:
    raise DrillError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def watchdog_nonce_digest(nonce: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", nonce):
        fail("synthetic recovery watchdog nonce is invalid")
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


def watchdog_payload_hmac(payload: dict[str, Any], nonce: str) -> str:
    watchdog_nonce_digest(nonce)
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hmac.new(nonce.encode("ascii"), canonical, hashlib.sha256).hexdigest()


def parse_utc_timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail("synthetic recovery watchdog timestamp is invalid")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DrillError("synthetic recovery watchdog timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        fail("synthetic recovery watchdog timestamp is invalid")
    return parsed


def _validate_api_url(
    value: str,
    name: str,
    hostname_allowed: Callable[[str], bool],
) -> str:
    invalid_origin = f"{name} must be a query-free HTTPS Grafana Cloud API origin"
    if value != value.strip():
        fail(invalid_origin)
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        raise DrillError(invalid_origin) from None
    if (
        parsed.scheme != "https"
        or not hostname
        or not hostname_allowed(hostname)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower()
        not in {hostname, f"{hostname}:443"}
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        fail(invalid_origin)
    return f"https://{hostname}"


def validate_grafana_api_url(value: str, name: str = "GRAFANA_URL") -> str:
    """Return the exact query-free NutsNews Grafana HTTPS origin."""
    return _validate_api_url(
        value,
        name,
        lambda hostname: hostname == GRAFANA_UI_HOSTNAME,
    )


def validate_sm_api_url(value: str, name: str = "GRAFANA_SM_URL") -> str:
    """Return a query-free Grafana Synthetic Monitoring service origin."""
    return _validate_api_url(
        value,
        name,
        lambda hostname: SYNTHETIC_MONITORING_HOSTNAME.fullmatch(hostname)
        is not None,
    )


class JsonClient:
    def __init__(
        self,
        origin: str,
        token: str,
        timeout: int,
        origin_validator: Callable[[str], str],
    ) -> None:
        self.origin = origin_validator(origin)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.origin}{path}",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise DrillError(f"remote API request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise DrillError("remote API request failed") from exc
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise DrillError("remote API returned invalid JSON") from exc


class SyntheticClient(JsonClient):
    def __init__(self, origin: str, token: str, timeout: int = 20) -> None:
        super().__init__(origin, token, timeout, validate_sm_api_url)

    def list_checks(self) -> Any:
        return self.request("GET", "/api/v1/check")

    def get_check(self, check_id: int) -> Any:
        return self.request("GET", f"/api/v1/check/{check_id}")

    def update_check(self, check_id: int, payload: dict[str, Any]) -> Any:
        # The official SM OpenAPI 1.14.0 update operation is POST, not PUT.
        return self.request("POST", f"/api/v1/check/{check_id}", payload)


class GrafanaClient(JsonClient):
    def __init__(self, origin: str, token: str, timeout: int = 20) -> None:
        super().__init__(origin, token, timeout, validate_grafana_api_url)

    def probe_samples(
        self, datasource_uid: str, since_epoch: float
    ) -> list[dict[str, Any]]:
        source_freshness_cutoff = int(since_epoch - 5)
        query = (
            f'(probe_success{{job="{JOB}"}} '
            "and on(job, instance, probe, config_version) "
            f'(timestamp(probe_success{{job="{JOB}"}}) >= {source_freshness_cutoff})) '
            "* on(job, instance, probe, config_version) group_left() "
            f'sm_check_info{{job="{JOB}",label_service_namespace="nutsnews",'
            'label_deployment_environment="production"}'
        )
        encoded = urllib.parse.urlencode({"query": query})
        response = self.request(
            "GET",
            f"/api/datasources/proxy/uid/{urllib.parse.quote(datasource_uid, safe='')}/"
            f"api/v1/query?{encoded}",
        )
        data = response.get("data") if isinstance(response, dict) else None
        results = data.get("result") if isinstance(data, dict) else None
        if response.get("status") != "success" or not isinstance(results, list):
            fail("Grafana probe query did not return a successful vector")
        samples: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
                continue
            sample = item.get("value")
            if not isinstance(sample, list) or len(sample) < 2:
                continue
            probe = item["metric"].get("probe")
            config_version = item["metric"].get("config_version")
            try:
                timestamp = float(sample[0])
                value = float(sample[1])
            except (TypeError, ValueError):
                continue
            if (
                isinstance(probe, str)
                and probe
                and isinstance(config_version, str)
                and config_version
                and math.isfinite(timestamp)
                and math.isfinite(value)
            ):
                samples.append(
                    {
                        "probe": probe,
                        "config_version": config_version,
                        "timestamp": timestamp,
                        "value": value,
                    }
                )
        return samples

    def active_alert_instances(self) -> list[dict[str, str]]:
        query = urllib.parse.urlencode(
            {"active": "true", "silenced": "false", "inhibited": "false"}
        )
        response = self.request(
            "GET", f"/api/alertmanager/grafana/api/v2/alerts?{query}"
        )
        if not isinstance(response, list):
            fail("Grafana active-alert query returned an unsupported shape")
        observed: list[dict[str, str]] = []
        for alert in response:
            labels = alert.get("labels") if isinstance(alert, dict) else None
            if not isinstance(labels, dict):
                continue
            alert_uid = ""
            for key in ("__alert_rule_uid__", "grafana_rule_uid", "rule_uid", "uid"):
                value = labels.get(key)
                if isinstance(value, str) and value:
                    alert_uid = value
                    break
            if not alert_uid:
                continue
            observed.append(
                {
                    "alert_uid": alert_uid,
                    "job": labels.get("job") if isinstance(labels.get("job"), str) else "",
                    "probe": labels.get("probe") if isinstance(labels.get("probe"), str) else "",
                }
            )
        return observed


def wait_for_alert_state(
    client: GrafanaClient,
    firing: bool,
    timeout_seconds: int,
    *,
    expected_probes: set[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if firing and (
        expected_probes is None
        or len(expected_probes) != 2
        or any(not isinstance(probe, str) or not probe for probe in expected_probes)
    ):
        fail("firing alert observation requires exactly two expected probe labels")
    deadline = monotonic() + timeout_seconds
    while True:
        matching = [
            alert
            for alert in client.active_alert_instances()
            if alert.get("alert_uid") == ALERT_UID and alert.get("job") == JOB
        ]
        observed_probes = {
            alert["probe"]
            for alert in matching
            if isinstance(alert.get("probe"), str) and alert["probe"]
        }
        satisfied = (
            len(matching) == 2 and observed_probes == expected_probes
            if firing
            else not matching
        )
        if satisfied:
            return {
                "status": "pass",
                "alert_uid": ALERT_UID,
                "job": JOB,
                "probe_count": len(observed_probes),
            }
        if monotonic() >= deadline:
            return {
                "status": "fail",
                "alert_uid": ALERT_UID,
                "job": JOB,
                "probe_count": len(observed_probes),
            }
        sleep(10)


def check_list_items(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        items = response
    elif isinstance(response, dict) and isinstance(response.get("items"), list):
        items = response["items"]
    else:
        fail("Synthetic Monitoring check list has an unsupported shape")
    return [item for item in items if isinstance(item, dict)]


def resolve_check(client: SyntheticClient) -> dict[str, Any]:
    matches = [item for item in check_list_items(client.list_checks()) if item.get("job") == JOB]
    if len(matches) != 1:
        fail("Synthetic Monitoring must contain exactly one canonical_readiness check")
    check_id = matches[0].get("id")
    if not isinstance(check_id, int) or isinstance(check_id, bool) or check_id <= 0:
        fail("canonical_readiness has an invalid check ID")
    detail = client.get_check(check_id)
    if not isinstance(detail, dict) or detail.get("id") != check_id or detail.get("job") != JOB:
        fail("Synthetic Monitoring check detail does not match the resolved check")
    return detail


def labels_as_map(labels: Any) -> dict[str, str]:
    if not isinstance(labels, list):
        fail("canonical_readiness labels must be a list")
    result: dict[str, str] = {}
    for item in labels:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            fail("canonical_readiness contains an unsupported label shape")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in result:
            fail("canonical_readiness contains invalid or duplicate labels")
        result[name] = value
    return result


def is_configured(value: Any) -> bool:
    return value not in (None, "", [], {}) and value is not False


def normalize_channels(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"k6"}:
        fail("canonical_readiness contains an unsupported channel configuration")
    if "k6" not in value:
        return {}
    k6 = value["k6"]
    if k6 is None:
        return {"k6": None}
    if not isinstance(k6, dict) or not isinstance(k6.get("id"), str) or not k6["id"]:
        fail("canonical_readiness contains an invalid k6 channel")
    return {"k6": {"id": k6["id"]}}


def validate_http_settings(http: Any) -> None:
    if not isinstance(http, dict):
        fail("canonical_readiness must use HTTP settings")
    method = http.get("method", 0)
    if method not in (0, "GET"):
        fail("canonical_readiness must remain a read-only GET check")
    for field in SENSITIVE_HTTP_FIELDS:
        if is_configured(http.get(field)):
            fail("canonical_readiness may not contain request payloads, credentials, or proxy state")
    tls = http.get("tlsConfig")
    if is_configured(tls):
        if not isinstance(tls, dict):
            fail("canonical_readiness contains unsupported TLS state")
        allowed_tls = {"insecureSkipVerify", "serverName", "caCert", "clientCert", "clientKey"}
        if set(tls) - allowed_tls or any(
            is_configured(tls.get(field)) for field in ("caCert", "clientCert", "clientKey")
        ):
            fail("canonical_readiness may not contain client TLS credentials")
        if tls.get("insecureSkipVerify") is True:
            fail("canonical_readiness may not skip TLS verification")


def update_payload(remote: dict[str, Any]) -> dict[str, Any]:
    unknown = set(remote) - CHECK_FIELDS
    missing = UPDATE_FIELDS - set(remote)
    if unknown:
        fail("Synthetic Monitoring API added an unreviewed check field; refusing mutation")
    if missing:
        fail("Synthetic Monitoring check is missing mutable fields required for exact restoration")
    if remote.get("job") != JOB or remote.get("enabled") is not True:
        fail("canonical_readiness must exist and be enabled")
    if remote.get("frequency") != 300000:
        fail("canonical_readiness must retain the five-minute frequency")
    check_id = remote.get("id")
    probes = remote.get("probes")
    if (
        not isinstance(check_id, int)
        or isinstance(check_id, bool)
        or check_id <= 0
        or not isinstance(probes, list)
        or len(probes) != 2
        or len(set(probes)) != 2
        or any(not isinstance(probe, int) or isinstance(probe, bool) or probe <= 0 for probe in probes)
    ):
        fail("canonical_readiness must use exactly two distinct probe IDs")
    parsed_target = urllib.parse.urlsplit(str(remote.get("target", "")))
    try:
        target_port = parsed_target.port
    except ValueError:
        fail("canonical_readiness must target a query-free HTTPS route on port 443")
    expected_path = (
        ""
        if JOB == "canonical_homepage"
        else "/api/articles"
        if JOB == "canonical_articles_api"
        else "/readyz"
    )
    if (
        parsed_target.scheme != "https"
        or not parsed_target.hostname
        or parsed_target.username is not None
        or parsed_target.password is not None
        or target_port not in (None, 443)
        or parsed_target.query
        or parsed_target.fragment
        or parsed_target.path.rstrip("/") != expected_path
    ):
        fail("canonical_readiness must target its query-free HTTPS route on port 443")
    labels = labels_as_map(remote.get("labels"))
    expected_labels = {**EXPECTED_LABELS, "check": JOB}
    if any(labels.get(name) != value for name, value in expected_labels.items()):
        fail("canonical_readiness is missing its bounded production identity labels")
    settings = remote.get("settings")
    if not isinstance(settings, dict) or set(settings) - SETTINGS_FIELDS:
        fail("canonical_readiness contains an unreviewed settings type")
    configured = [name for name, value in settings.items() if is_configured(value)]
    if configured != ["http"]:
        fail("canonical_readiness must contain only HTTP settings")
    validate_http_settings(settings.get("http"))

    payload = {field: copy.deepcopy(remote[field]) for field in UPDATE_FIELDS}
    payload["channels"] = normalize_channels(payload["channels"])
    return payload


def mutated_payload(base: dict[str, Any], variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        fail("synthetic mismatch variant must be status, body, or header")
    result = copy.deepcopy(base)
    http = result["settings"]["http"]
    if variant == "status":
        http["validStatusCodes"] = [599]
    elif variant == "body":
        current = http.get("failIfBodyNotMatchesRegexp")
        if current is None:
            current = []
        if not isinstance(current, list):
            fail("canonical_readiness body assertions have an unsupported shape")
        http["failIfBodyNotMatchesRegexp"] = [*current, BODY_MISMATCH]
    else:
        current = http.get("failIfHeaderNotMatchesRegexp")
        if current is None:
            current = []
        if not isinstance(current, list):
            fail("canonical_readiness header assertions have an unsupported shape")
        http["failIfHeaderNotMatchesRegexp"] = [*current, copy.deepcopy(HEADER_MISMATCH)]
    validate_single_mutation(base, result, variant)
    return result


def validate_single_mutation(base: dict[str, Any], mutated: dict[str, Any], variant: str) -> None:
    field = MUTATION_FIELDS[variant]
    expected = copy.deepcopy(base)
    expected["settings"]["http"][field] = copy.deepcopy(mutated["settings"]["http"][field])
    if expected != mutated or base["settings"]["http"].get(field) == mutated["settings"]["http"][field]:
        fail("synthetic drill must change exactly one assertion family")


def private_snapshot_write(path: Path, remote: dict[str, Any], payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail("synthetic restore snapshot path is unsafe")
    snapshot = {
        "schema_version": 1,
        "drill": DRILL,
        "job": JOB,
        "check_id": payload["id"],
        "remote_check": remote,
        "restore_payload": payload,
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(snapshot, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def load_snapshot(path: Path) -> tuple[int, dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        fail("synthetic restore snapshot is missing or unsafe")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        fail("synthetic restore snapshot must be mode 0600")
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DrillError("synthetic restore snapshot is invalid") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("drill") != DRILL
        or snapshot.get("job") != JOB
        or not isinstance(snapshot.get("remote_check"), dict)
        or not isinstance(snapshot.get("restore_payload"), dict)
    ):
        fail("synthetic restore snapshot does not match this drill")
    payload = update_payload(snapshot["remote_check"])
    if payload != snapshot["restore_payload"] or snapshot.get("check_id") != payload["id"]:
        fail("synthetic restore snapshot failed its exact payload check")
    return payload["id"], payload


def run_watchdog_arm(
    sm_client: SyntheticClient,
    snapshot: Path,
    variant: str,
    nonce: str,
    parent_run_id: int,
    parent_run_attempt: int,
    parent_head_sha: str,
    watchdog_run_id: int,
    watchdog_run_attempt: int,
) -> dict[str, Any]:
    watchdog_nonce_digest(nonce)
    if (
        parent_run_id <= 0
        or parent_run_attempt <= 0
        or watchdog_run_id <= 0
        or watchdog_run_attempt <= 0
        or not re.fullmatch(r"[a-f0-9]{40}", parent_head_sha)
    ):
        fail("synthetic recovery watchdog run identity is invalid")
    remote = resolve_check(sm_client)
    restore = update_payload(remote)
    mutated_payload(restore, variant)
    private_snapshot_write(snapshot, remote, restore)
    return {
        "schema_version": 1,
        "safe_metadata_only": True,
        "workflow": WATCHDOG_WORKFLOW,
        "status": "armed",
        "result": "armed",
        "drill": DRILL,
        "target": JOB,
        "variant": variant,
        "parent_repository": WATCHDOG_PARENT_REPOSITORY,
        "parent_run_id": parent_run_id,
        "parent_run_attempt": parent_run_attempt,
        "parent_head_sha": parent_head_sha,
        "watchdog_run_id": watchdog_run_id,
        "watchdog_run_attempt": watchdog_run_attempt,
        "watchdog_nonce_sha256": watchdog_nonce_digest(nonce),
        "snapshot_payload_hmac_sha256": watchdog_payload_hmac(restore, nonce),
        "check_id": restore["id"],
        "probe_count": 2,
        "snapshot_ready": True,
        "exact_restore_payload_ready": True,
        "private_snapshot_uploaded": False,
        "mutation_performed": False,
        "max_hold_seconds": WATCHDOG_MAX_HOLD_SECONDS,
        "armed_at": utc_now(),
    }


def validate_watchdog_handshake(
    path: Path,
    nonce: str,
    variant: str,
    parent_run_id: int,
    parent_run_attempt: int,
    parent_head_sha: str,
    watchdog_run_id: int,
    watchdog_run_attempt: int,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000:
        fail("synthetic recovery watchdog armed handshake is missing or unsafe")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DrillError("synthetic recovery watchdog armed handshake is invalid") from exc
    if not isinstance(report, dict) or set(report) != WATCHDOG_ARM_FIELDS:
        fail("synthetic recovery watchdog armed handshake has an unsupported shape")
    expected = {
        "schema_version": 1,
        "safe_metadata_only": True,
        "workflow": WATCHDOG_WORKFLOW,
        "status": "armed",
        "result": "armed",
        "drill": DRILL,
        "target": JOB,
        "variant": variant,
        "parent_repository": WATCHDOG_PARENT_REPOSITORY,
        "parent_run_id": parent_run_id,
        "parent_run_attempt": parent_run_attempt,
        "parent_head_sha": parent_head_sha,
        "watchdog_run_id": watchdog_run_id,
        "watchdog_run_attempt": watchdog_run_attempt,
        "watchdog_nonce_sha256": watchdog_nonce_digest(nonce),
        "probe_count": 2,
        "snapshot_ready": True,
        "exact_restore_payload_ready": True,
        "private_snapshot_uploaded": False,
        "mutation_performed": False,
        "max_hold_seconds": WATCHDOG_MAX_HOLD_SECONDS,
    }
    if any(report.get(key) != value for key, value in expected.items()):
        fail("synthetic recovery watchdog armed handshake does not match this drill")
    if (
        not isinstance(report.get("check_id"), int)
        or isinstance(report.get("check_id"), bool)
        or report["check_id"] <= 0
        or not isinstance(report.get("snapshot_payload_hmac_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", report["snapshot_payload_hmac_sha256"])
    ):
        fail("synthetic recovery watchdog armed handshake is incomplete")
    armed_at = parse_utc_timestamp(report.get("armed_at"))
    observed_at = now or dt.datetime.now(dt.timezone.utc)
    age = (observed_at - armed_at).total_seconds()
    if age < -30 or age > WATCHDOG_ARM_MAX_AGE_SECONDS:
        fail("synthetic recovery watchdog armed handshake is stale")
    return copy.deepcopy(report)


def wait_for_probe_state(
    client: GrafanaClient,
    datasource_uid: str,
    expected_value: int,
    since_epoch: float,
    timeout_seconds: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while True:
        samples = client.probe_samples(datasource_uid, since_epoch)
        probes = {sample["probe"] for sample in samples}
        config_versions = {sample["config_version"] for sample in samples}
        current = (
            len(samples) == 2
            and len(probes) == 2
            and len(config_versions) == 1
            and all(sample["timestamp"] >= since_epoch - 5 for sample in samples)
            and all(sample["value"] == expected_value for sample in samples)
        )
        if current:
            return {
                "status": "pass",
                "probe_count": 2,
                "probes": sorted(probes),
                "sample_count": 2,
                "config_version_count": 1,
            }
        if monotonic() >= deadline:
            return {
                "status": "fail",
                "probe_count": len(probes),
                "probes": sorted(probes),
                "sample_count": len(samples),
                "config_version_count": len(config_versions),
            }
        sleep(10)


def canonical_assertion_set(field: str, value: Any) -> tuple[Any, ...]:
    if not isinstance(value, list):
        fail("Synthetic Monitoring assertion family has an unsupported shape")
    if field == "validStatusCodes":
        if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
            fail("Synthetic Monitoring status assertions must be integers")
        normalized: list[Any] = list(value)
    elif field in {"failIfBodyMatchesRegexp", "failIfBodyNotMatchesRegexp"}:
        if any(not isinstance(item, str) for item in value):
            fail("Synthetic Monitoring body assertions must be strings")
        normalized = list(value)
    else:
        normalized = []
        for item in value:
            if (
                not isinstance(item, dict)
                or set(item) != {"allowMissing", "header", "regexp"}
                or not isinstance(item.get("allowMissing"), bool)
                or not isinstance(item.get("header"), str)
                or not isinstance(item.get("regexp"), str)
            ):
                fail("Synthetic Monitoring header assertions have an unsupported shape")
            normalized.append(
                (item["allowMissing"], item["header"], item["regexp"])
            )
    if len(set(normalized)) != len(normalized):
        fail("Synthetic Monitoring assertion sets may not contain duplicates")
    return tuple(sorted(normalized))


def payload_comparison_view(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    settings = result.get("settings")
    http = settings.get("http") if isinstance(settings, dict) else None
    if not isinstance(http, dict):
        fail("Synthetic Monitoring payload must contain HTTP settings")
    for field in ASSERTION_SET_FIELDS:
        http[field] = canonical_assertion_set(field, http.get(field, []))
    return result


def verify_remote_payload(client: SyntheticClient, check_id: int, expected: dict[str, Any]) -> None:
    observed = client.get_check(check_id)
    if (
        not isinstance(observed, dict)
        or payload_comparison_view(update_payload(observed))
        != payload_comparison_view(expected)
    ):
        fail("Synthetic Monitoring did not preserve the exact requested check configuration")


def payloads_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return payload_comparison_view(left) == payload_comparison_view(right)


def restore_exact_if_owned(
    client: SyntheticClient,
    check_id: int,
    restore: dict[str, Any],
    variant: str,
) -> bool:
    current_remote = resolve_check(client)
    if current_remote.get("id") != check_id:
        fail("resolved check no longer matches the saved restore target")
    current = update_payload(current_remote)
    if payloads_equal(current, restore):
        verify_remote_payload(client, check_id, restore)
        return False
    expected_mutation = mutated_payload(restore, variant)
    if not payloads_equal(current, expected_mutation):
        fail("refusing to overwrite a synthetic check changed outside this drill")
    client.update_check(check_id, restore)
    verify_remote_payload(client, check_id, restore)
    return True


def safe_report(mode: str, variant: str, check_id: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "drill": DRILL,
        "target": JOB,
        "variant": variant,
        "mode": mode,
        "check_id": check_id,
        "probe_count": 2,
        "mutation_field": MUTATION_FIELDS[variant],
        "mutation_performed": False,
        "controlled_failure_observed": False,
        "alert_firing_observed": False,
        "restoration_attempted": False,
        "restore_write_performed": False,
        "restored": False,
        "recovery_observed": False,
        "alert_recovery_observed": False,
        "watchdog_confirmed_armed": False,
        "watchdog_snapshot_matches": False,
        "started_at": utc_now(),
        "result": "fail",
    }


def run_dry_run(client: SyntheticClient, variant: str) -> dict[str, Any]:
    remote = resolve_check(client)
    payload = update_payload(remote)
    mutated_payload(payload, variant)
    report = safe_report("dry-run", variant, payload["id"])
    report.update({"result": "dry-run", "resolved": True})
    return report


def restore_saved(
    sm_client: SyntheticClient,
    grafana_client: GrafanaClient,
    datasource_uid: str,
    snapshot: Path,
    timeout_seconds: int,
    variant: str,
) -> dict[str, Any]:
    check_id, restore = load_snapshot(snapshot)
    report = safe_report("restore", variant, check_id)
    report["restoration_attempted"] = True
    restore_started = time.time()
    report["restore_write_performed"] = restore_exact_if_owned(
        sm_client, check_id, restore, variant
    )
    report["restored"] = True
    recovered = wait_for_probe_state(
        grafana_client, datasource_uid, 1, restore_started, timeout_seconds
    )
    report["recovery_observed"] = recovered["status"] == "pass"
    alert_recovered = wait_for_alert_state(
        grafana_client, False, timeout_seconds
    )
    report["alert_recovery_observed"] = alert_recovered["status"] == "pass"
    report["probe_count"] = recovered["probe_count"]
    report["result"] = (
        "pass"
        if report["recovery_observed"] and report["alert_recovery_observed"]
        else "fail"
    )
    report["finished_at"] = utc_now()
    return report


def run_execute(
    sm_client: SyntheticClient,
    grafana_client: GrafanaClient,
    datasource_uid: str,
    snapshot: Path,
    variant: str,
    timeout_seconds: int,
    watchdog_handshake: Path,
    watchdog_nonce: str,
    parent_run_id: int,
    parent_run_attempt: int,
    parent_head_sha: str,
    watchdog_run_id: int,
    watchdog_run_attempt: int,
) -> dict[str, Any]:
    handshake = validate_watchdog_handshake(
        watchdog_handshake,
        watchdog_nonce,
        variant,
        parent_run_id,
        parent_run_attempt,
        parent_head_sha,
        watchdog_run_id,
        watchdog_run_attempt,
    )
    remote = resolve_check(sm_client)
    base = update_payload(remote)
    check_id = base["id"]
    report = safe_report("execute", variant, check_id)
    report["watchdog_confirmed_armed"] = True
    if (
        handshake["check_id"] != check_id
        or not hmac.compare_digest(
            handshake["snapshot_payload_hmac_sha256"],
            watchdog_payload_hmac(base, watchdog_nonce),
        )
    ):
        fail("synthetic recovery watchdog snapshot no longer matches the remote check")
    report["watchdog_snapshot_matches"] = True
    precheck = wait_for_probe_state(
        grafana_client,
        datasource_uid,
        1,
        time.time() - 600,
        min(timeout_seconds, 60),
    )
    if precheck["status"] != "pass":
        fail("selected synthetic check is not healthy on both probes before injection")
    alert_precheck = wait_for_alert_state(
        grafana_client, False, min(timeout_seconds, 60)
    )
    if alert_precheck["status"] != "pass":
        fail("synthetic probe failure alert is already firing before injection")
    changed = mutated_payload(base, variant)
    private_snapshot_write(snapshot, remote, base)
    mutation_attempted = False
    primary_error: DrillError | None = None
    try:
        mutation_started = time.time()
        mutation_attempted = True
        sm_client.update_check(check_id, changed)
        verify_remote_payload(sm_client, check_id, changed)
        report["mutation_performed"] = True
        failed = wait_for_probe_state(
            grafana_client, datasource_uid, 0, mutation_started, timeout_seconds
        )
        report["controlled_failure_observed"] = failed["status"] == "pass"
        if not report["controlled_failure_observed"]:
            fail("controlled synthetic failure was not observed on both probes")
        alert_fired = wait_for_alert_state(
            grafana_client,
            True,
            timeout_seconds,
            expected_probes=set(precheck["probes"]),
        )
        report["alert_firing_observed"] = alert_fired["status"] == "pass"
        if not report["alert_firing_observed"]:
            fail("synthetic probe failure alert did not fire during controlled mismatch")
    except DrillError as exc:
        primary_error = exc
    finally:
        if mutation_attempted:
            report["restoration_attempted"] = True
            try:
                restore_started = time.time()
                report["restore_write_performed"] = restore_exact_if_owned(
                    sm_client, check_id, base, variant
                )
                report["restored"] = True
                recovered = wait_for_probe_state(
                    grafana_client, datasource_uid, 1, restore_started, timeout_seconds
                )
                report["recovery_observed"] = recovered["status"] == "pass"
                report["probe_count"] = recovered["probe_count"]
                if not report["recovery_observed"] and primary_error is None:
                    primary_error = DrillError("synthetic recovery was not observed on both probes")
                alert_recovered = wait_for_alert_state(
                    grafana_client, False, timeout_seconds
                )
                report["alert_recovery_observed"] = (
                    alert_recovered["status"] == "pass"
                )
                if not report["alert_recovery_observed"] and primary_error is None:
                    primary_error = DrillError(
                        "synthetic probe failure alert did not resolve after restoration"
                    )
            except DrillError as exc:
                if primary_error is None:
                    primary_error = exc
    report["finished_at"] = utc_now()
    report["result"] = "pass" if primary_error is None else "fail"
    return report


def synthetic_client_from_environment() -> SyntheticClient:
    sm_url = os.environ.get(
        "GRAFANA_SM_URL",
        os.environ.get("GRAFANA_SYNTHETIC_MONITORING_URL", ""),
    )
    sm_token = os.environ.get("GRAFANA_SM_ACCESS_TOKEN", "").strip()
    if not sm_token:
        fail("Synthetic Monitoring access token is required")
    return SyntheticClient(sm_url, sm_token)


def clients_from_environment() -> tuple[SyntheticClient, GrafanaClient, str]:
    grafana_url = os.environ.get("GRAFANA_URL", "")
    grafana_token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "").strip()
    datasource_uid = os.environ.get("GRAFANA_PROMETHEUS_DATASOURCE_UID", "").strip()
    if not grafana_token:
        fail("Grafana service-account token is required")
    if not datasource_uid or len(datasource_uid) > 128 or not datasource_uid.replace("-", "").isalnum():
        fail("GRAFANA_PROMETHEUS_DATASOURCE_UID is missing or invalid")
    return (
        synthetic_client_from_environment(),
        GrafanaClient(grafana_url, grafana_token),
        datasource_uid,
    )


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("dry-run", "execute", "restore", "watchdog-arm", "validate-watchdog-arm"),
    )
    parser.add_argument("--target", choices=JOBS, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--target-confirmation", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--watchdog-handshake", type=Path)
    parser.add_argument("--watchdog-nonce", default="")
    parser.add_argument("--parent-run-id", type=int, default=0)
    parser.add_argument("--parent-run-attempt", type=int, default=0)
    parser.add_argument("--parent-head-sha", default="")
    parser.add_argument("--watchdog-run-id", type=int, default=0)
    parser.add_argument("--watchdog-run-attempt", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=720)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout_seconds < 30 or args.timeout_seconds > 1200:
        parser.error("--timeout-seconds must be between 30 and 1200")
    if args.mode in {"execute", "restore", "watchdog-arm"} and args.snapshot is None:
        parser.error("--snapshot is required for execute, restore, and watchdog-arm")
    if args.mode in {"execute", "validate-watchdog-arm"} and args.watchdog_handshake is None:
        parser.error("--watchdog-handshake is required before execute")
    if args.mode in {"execute", "watchdog-arm", "validate-watchdog-arm"} and (
        not re.fullmatch(r"[a-f0-9]{64}", args.watchdog_nonce)
        or args.parent_run_id <= 0
        or args.parent_run_attempt <= 0
        or not re.fullmatch(r"[a-f0-9]{40}", args.parent_head_sha)
        or args.watchdog_run_id <= 0
        or args.watchdog_run_attempt <= 0
    ):
        parser.error("watchdog and parent run identity arguments are required and must be valid")
    if args.mode == "execute" and (
        args.target_confirmation != args.target
        or args.confirmation
        != f"execute-grafana-failure-drill:{args.target}:{DRILL}"
    ):
        parser.error("execute requires the exact target and drill confirmation")
    if args.mode == "restore" and (
        args.target_confirmation != args.target
        or args.confirmation
        != f"recover-grafana-failure-drill:{args.target}:{DRILL}"
    ):
        parser.error("restore requires the exact target and recovery confirmation")
    return args


def main() -> int:
    args = parse_args()
    select_job(args.target)
    report: dict[str, Any]
    try:
        if args.mode == "validate-watchdog-arm":
            handshake = validate_watchdog_handshake(
                args.watchdog_handshake,
                args.watchdog_nonce,
                args.variant,
                args.parent_run_id,
                args.parent_run_attempt,
                args.parent_head_sha,
                args.watchdog_run_id,
                args.watchdog_run_attempt,
            )
            report = {
                "schema_version": 1,
                "safe_metadata_only": True,
                "drill": DRILL,
                "target": JOB,
                "variant": args.variant,
                "mode": args.mode,
                "watchdog_run_id": handshake["watchdog_run_id"],
                "watchdog_run_attempt": handshake["watchdog_run_attempt"],
                "watchdog_confirmed_armed": True,
                "result": "pass",
                "finished_at": utc_now(),
            }
        elif args.mode == "watchdog-arm":
            report = run_watchdog_arm(
                synthetic_client_from_environment(),
                args.snapshot,
                args.variant,
                args.watchdog_nonce,
                args.parent_run_id,
                args.parent_run_attempt,
                args.parent_head_sha,
                args.watchdog_run_id,
                args.watchdog_run_attempt,
            )
        else:
            sm_client, grafana_client, datasource_uid = clients_from_environment()
            if args.mode == "dry-run":
                report = run_dry_run(sm_client, args.variant)
            elif args.mode == "execute":
                report = run_execute(
                    sm_client,
                    grafana_client,
                    datasource_uid,
                    args.snapshot,
                    args.variant,
                    args.timeout_seconds,
                    args.watchdog_handshake,
                    args.watchdog_nonce,
                    args.parent_run_id,
                    args.parent_run_attempt,
                    args.parent_head_sha,
                    args.watchdog_run_id,
                    args.watchdog_run_attempt,
                )
            else:
                report = restore_saved(
                    sm_client,
                    grafana_client,
                    datasource_uid,
                    args.snapshot,
                    args.timeout_seconds,
                    args.variant,
                )
    except (DrillError, OSError, ValueError, TypeError):
        report = {
            "schema_version": 1,
            "drill": DRILL,
            "target": JOB,
            "variant": args.variant,
            "mode": args.mode,
            "result": "fail",
            "finished_at": utc_now(),
        }
    write_report(args.output, report)
    return 0 if report["result"] in {"pass", "dry-run", "armed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
