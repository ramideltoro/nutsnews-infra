#!/usr/bin/env python3
"""Fail-closed Cloudflare Access JWT verifier for the staging origin."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


HOST = "0.0.0.0"
PORT = 8091
TEAM_DOMAIN = os.environ.get("NUTSNEWS_STAGING_ACCESS_TEAM_DOMAIN", "").strip().lower()
AUDIENCE = os.environ.get("NUTSNEWS_STAGING_ACCESS_AUDIENCE", "").strip()
JWKS_URL = os.environ.get(
    "NUTSNEWS_STAGING_ACCESS_JWKS_URL",
    f"https://{TEAM_DOMAIN}/cdn-cgi/access/certs" if TEAM_DOMAIN else "",
).strip()
CLOCK_SKEW_SECONDS = 30
JWKS_TTL_SECONDS = 300
MAX_TOKEN_LENGTH = 16384
_jwks_cache: tuple[float, dict[str, dict[str, object]]] = (0.0, {})
_jwks_lock = Lock()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _integer(value: str) -> int:
    return int.from_bytes(_b64decode(value), "big")


def _load_jwks() -> dict[str, dict[str, object]]:
    global _jwks_cache
    now = time.time()
    with _jwks_lock:
        if _jwks_cache[1] and now - _jwks_cache[0] < JWKS_TTL_SECONDS:
            return _jwks_cache[1]
        request = urllib.request.Request(JWKS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - fixed configured HTTPS endpoint
            document = json.load(response)
        keys = {
            key["kid"]: key
            for key in document.get("keys", [])
            if isinstance(key, dict) and isinstance(key.get("kid"), str)
        }
        if not keys:
            raise ValueError("no signing keys")
        _jwks_cache = (now, keys)
        return keys


def _verify_rs256(signing_input: bytes, signature: bytes, key: dict[str, object]) -> None:
    if key.get("kty") != "RSA" or not isinstance(key.get("n"), str) or not isinstance(key.get("e"), str):
        raise ValueError("unsupported signing key")
    modulus = _integer(key["n"])
    exponent = _integer(key["e"])
    size = (modulus.bit_length() + 7) // 8
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(signing_input).digest()
    padding_length = size - len(digest_info) - 3
    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    if padding_length < 8 or not hmac.compare_digest(encoded, expected):
        raise ValueError("invalid signature")


def verify_access_token(token: str, now: int | None = None) -> dict[str, object]:
    if not TEAM_DOMAIN or not AUDIENCE or not JWKS_URL:
        raise ValueError("gateway is not configured")
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise ValueError("missing or oversized token")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid token shape")
    header = json.loads(_b64decode(parts[0]))
    claims = json.loads(_b64decode(parts[1]))
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise ValueError("invalid signing algorithm")
    _verify_rs256(f"{parts[0]}.{parts[1]}".encode(), _b64decode(parts[2]), _load_jwks()[header["kid"]])
    current = int(time.time()) if now is None else now
    audience = claims.get("aud", [])
    if isinstance(audience, str):
        audience = [audience]
    issuer = str(claims.get("iss", "")).rstrip("/").lower()
    expected_issuer = f"https://{TEAM_DOMAIN}".rstrip("/").lower()
    if AUDIENCE not in audience or issuer != expected_issuer:
        raise ValueError("token scope mismatch")
    if int(claims.get("exp", 0)) < current - CLOCK_SKEW_SECONDS:
        raise ValueError("expired token")
    if int(claims.get("nbf", 0)) > current + CLOCK_SKEW_SECONDS:
        raise ValueError("token is not active")
    return claims


class Handler(BaseHTTPRequestHandler):
    server_version = "nutsnews-staging-access"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            status = 200 if TEAM_DOMAIN and AUDIENCE and JWKS_URL else 503
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if self.path != "/verify":
            self.send_error(404)
            return
        try:
            verify_access_token(self.headers.get("Cf-Access-Jwt-Assertion", ""))
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            self.send_response(401)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
