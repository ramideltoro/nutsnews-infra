#!/usr/bin/env python3
"""Unit tests for append-only Grafana deployment annotations."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_deployment_annotation.py"
SPEC = importlib.util.spec_from_file_location("publish_deployment_annotation", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to import publish_deployment_annotation.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RedirectingHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[urllib.request.Request] = []

    def https_open(self, request: urllib.request.Request):
        self.requests.append(request)
        headers = Message()
        headers["Location"] = "https://attacker.invalid/steal"
        response = urllib.response.addinfourl(
            io.BytesIO(b""), headers, request.full_url, 302
        )
        response.msg = "Found"
        return response


class DeploymentAnnotationTests(unittest.TestCase):
    def test_grafana_origin_is_exact_query_free_https_grafana_cloud(self) -> None:
        for value in (
            "https://kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net/",
            "https://kindcantaloupe2036.grafana.net:443/",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    MODULE.validate_api_url(value),
                    "https://kindcantaloupe2036.grafana.net",
                )
        for value in (
            "",
            "http://kindcantaloupe2036.grafana.net",
            "https://grafana.net",
            "https://another-tenant.grafana.net",
            "https://synthetic-monitoring-api.grafana.net",
            "https://synthetic-monitoring-api.us.grafana.net",
            "https://kindcantaloupe2036.grafana.net.evil.invalid",
            "https://kindcantaloupe2036.grafana.net/api",
            "https://kindcantaloupe2036.grafana.net/?token=secret",
            "https://kindcantaloupe2036.grafana.net/#fragment",
            "https://user:secret@kindcantaloupe2036.grafana.net",
            "https://kindcantaloupe2036.grafana.net:444",
            "https://kindcantaloupe2036.grafana.net:",
            "https://bad_.grafana.net",
            " https://kindcantaloupe2036.grafana.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.GrafanaAnnotationClient(value, "sensitive-token")

    def test_malformed_nfkc_netloc_never_echoes_protected_input(self) -> None:
        protected_fragment = "do-not-reflect-this-value"
        malformed = f"https://kindcantaloupe2036.grafana.net\uff0f{protected_fragment}"

        with self.assertRaises(ValueError) as raised:
            MODULE.validate_api_url(malformed, "NUTSNEWS_GRAFANA_CLOUD_URL")

        message = str(raised.exception)
        self.assertEqual(
            message,
            "NUTSNEWS_GRAFANA_CLOUD_URL must be a query-free HTTPS Grafana Cloud API origin",
        )
        self.assertNotIn(protected_fragment, message)
        self.assertNotIn(malformed, message)

    def test_redirect_is_rejected_before_a_second_authenticated_request(self) -> None:
        transport = RedirectingHTTPSHandler()
        client = MODULE.GrafanaAnnotationClient(
            "https://kindcantaloupe2036.grafana.net", "sensitive-token"
        )
        client.opener = urllib.request.build_opener(
            MODULE.NoRedirectHandler(), transport
        )

        with self.assertRaisesRegex(RuntimeError, "failed with 302"):
            client.create({"time": 1, "tags": [], "text": "bounded"})

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0].get_header("Authorization"),
            "Bearer sensitive-token",
        )
        self.assertEqual(
            transport.requests[0].full_url,
            "https://kindcantaloupe2036.grafana.net/api/annotations",
        )

    def test_annotation_contains_bounded_operational_context(self) -> None:
        annotation = MODULE.build_annotation(
            event_type="promotion",
            commit="0123456789abcdef",
            image_digest="sha256:abcdef",
            version="2026.07.31",
            target="production-vps",
            outcome="succeeded",
            timestamp_ms=1_785_520_800_000,
            evidence="https://github.com/ramideltoro/nutsnews-infra/actions/runs/123",
        )
        self.assertEqual(annotation["tags"][0], "nutsnews-deployment")
        self.assertIn("event:promotion", annotation["tags"])
        context = json.loads(annotation["text"])
        self.assertEqual(context["commit"], "0123456789abcdef")
        self.assertEqual(context["image_digest"], "sha256:abcdef")
        self.assertEqual(context["target"], "production-vps")
        self.assertEqual(
            context["evidence"],
            "https://github.com/ramideltoro/nutsnews-infra/actions/runs/123",
        )

    def test_legacy_callers_get_a_bounded_non_evidence_default(self) -> None:
        annotation = MODULE.build_annotation(
            event_type="promotion",
            commit="0123456789abcdef",
            image_digest="sha256:abcdef",
            version="2026.07.31",
            target="production-vps",
            outcome="succeeded",
            timestamp_ms=1_785_520_800_000,
        )
        self.assertEqual(json.loads(annotation["text"])["evidence"], "not-applicable")

    def test_unbounded_or_sensitive_freeform_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.build_annotation(
                event_type="rollback",
                commit="commit with spaces",
                image_digest="sha256:abcdef",
                version="2026.07.31",
                target="production-vps",
                outcome="succeeded",
                timestamp_ms=1,
            )

    def test_unknown_event_or_outcome_is_rejected(self) -> None:
        common = {
            "commit": "0123456",
            "image_digest": "sha256:abcdef",
            "version": "2026.07.31",
            "target": "production-vps",
            "timestamp_ms": 1,
        }
        with self.assertRaises(ValueError):
            MODULE.build_annotation(event_type="deploy", outcome="succeeded", **common)
        with self.assertRaises(ValueError):
            MODULE.build_annotation(event_type="promotion", outcome="unknown", **common)

    def test_grafana_response_requires_positive_integer_annotation_id(self) -> None:
        self.assertEqual(MODULE.validated_annotation_id({"id": 123}), 123)
        for response in (
            {},
            {"id": None},
            {"id": 0},
            {"id": -1},
            {"id": True},
            {"id": "123"},
        ):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                MODULE.validated_annotation_id(response)


if __name__ == "__main__":
    unittest.main()
