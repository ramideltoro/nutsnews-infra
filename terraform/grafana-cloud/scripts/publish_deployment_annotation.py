#!/usr/bin/env python3
"""Append a bounded production-change annotation to Grafana Cloud dashboards."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


EVENT_TYPES = {"promotion", "rollback", "failover", "database-provider-change"}
OUTCOMES = {"started", "succeeded", "failed", "rolled-back"}
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$")
GRAFANA_UI_HOSTNAME = "nutsnews.grafana.net"


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


def validate_api_url(value: str, name: str = "GRAFANA_URL") -> str:
    """Return the exact query-free NutsNews Grafana HTTPS origin."""
    invalid_origin = f"{name} must be a query-free HTTPS Grafana Cloud API origin"
    if value != value.strip():
        raise ValueError(invalid_origin)
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        # urlsplit includes the raw netloc in its NFKC error. Replace that
        # diagnostic so a protected URL value can never be reflected to logs.
        raise ValueError(invalid_origin) from None
    if (
        parsed.scheme != "https"
        or hostname != GRAFANA_UI_HOSTNAME
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.lower()
        not in {GRAFANA_UI_HOSTNAME, f"{GRAFANA_UI_HOSTNAME}:443"}
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(invalid_origin)
    return f"https://{GRAFANA_UI_HOSTNAME}"


def bounded_value(name: str, value: str) -> str:
    normalized = value.strip()
    if not SAFE_VALUE.fullmatch(normalized):
        raise ValueError(
            f"{name} must contain 1-160 bounded identifier characters and no whitespace"
        )
    return normalized


def build_annotation(
    *,
    event_type: str,
    commit: str,
    image_digest: str,
    version: str,
    target: str,
    outcome: str,
    timestamp_ms: int,
    evidence: str = "not-applicable",
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event type: {event_type}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unsupported outcome: {outcome}")
    context = {
        "commit": bounded_value("commit", commit),
        "evidence": bounded_value("evidence", evidence),
        "event_type": event_type,
        "image_digest": bounded_value("image_digest", image_digest),
        "outcome": outcome,
        "target": bounded_value("target", target),
        "version": bounded_value("version", version),
    }
    return {
        "time": timestamp_ms,
        "tags": [
            "nutsnews-deployment",
            f"event:{event_type}",
            f"outcome:{outcome}",
        ],
        "text": json.dumps(context, sort_keys=True, separators=(",", ":")),
    }


class GrafanaAnnotationClient:
    def __init__(self, url: str, token: str, timeout: int = 20) -> None:
        self.url = validate_api_url(url)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirectHandler())

    def create(self, annotation: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}/api/annotations",
            data=json.dumps(annotation).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            exc.close()
            raise RuntimeError(
                f"Grafana annotation POST failed with {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Grafana annotation POST failed: {exc.reason}") from exc
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}


def validated_annotation_id(response: dict[str, Any]) -> int:
    """Require Grafana's durable positive annotation identifier."""
    annotation_id = response.get("id")
    if (
        not isinstance(annotation_id, int)
        or isinstance(annotation_id, bool)
        or annotation_id <= 0
    ):
        raise RuntimeError(
            "Grafana annotation response did not contain a positive integer id"
        )
    return annotation_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-type", choices=sorted(EVENT_TYPES), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    parser.add_argument(
        "--evidence",
        default="not-applicable",
        help="Bounded durable evidence URL or identifier; defaults for legacy callers.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_url = os.environ.get("GRAFANA_URL", "")
    try:
        url = validate_api_url(raw_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "").strip()
    if not token:
        print("GRAFANA_SERVICE_ACCOUNT_TOKEN is required", file=sys.stderr)
        return 1

    try:
        annotation = build_annotation(
            event_type=args.event_type,
            commit=args.commit,
            image_digest=args.image_digest,
            version=args.version,
            target=args.target,
            outcome=args.outcome,
            timestamp_ms=int(time.time() * 1000),
            evidence=args.evidence,
        )
        response = GrafanaAnnotationClient(url, token).create(annotation)
        annotation_id = validated_annotation_id(response)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report = {
        "annotation_id": annotation_id,
        "evidence": args.evidence,
        "event_type": args.event_type,
        "outcome": args.outcome,
        "status": "published",
        "target": args.target,
        "timestamp_ms": annotation["time"],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
