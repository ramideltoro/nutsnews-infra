#!/usr/bin/env python3
"""Google OAuth gate for the read-only NutsNews operations portal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ALLOWED_EMAIL = "rami.deltoro@gmail.com"
CALLBACK_PATH = "/api/auth/callback/google"
SIGNIN_PATH = "/api/auth/signin/google"
SIGNOUT_PATH = "/api/auth/signout"
ACCESS_DENIED = "Access denied. This Google account is not allowed to use the NutsNews operations portal."
SESSION_TTL_SECONDS = 8 * 60 * 60
STATE_TTL_SECONDS = 10 * 60


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + ("=" * (-len(data) % 4)))


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    body = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{b64url_encode(signature)}"


def unsign_payload(value: str, secret: str) -> dict[str, Any] | None:
    try:
        body, signature = value.split(".", 1)
    except ValueError:
        return None
    expected = b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, (int, float)) or expires_at < time.time():
        return None
    return payload


def read_cookie(header: str | None, name: str) -> str:
    if not header:
        return ""
    cookie = SimpleCookie()
    cookie.load(header)
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def validate_callback_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path != CALLBACK_PATH:
        raise ValueError(f"NUTSNEWS_OPS_PORTAL_CALLBACK_URL must be https://<host>{CALLBACK_PATH}")
    return parsed


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def is_allowed_google_user(profile: dict[str, Any], allowed_email: str = ALLOWED_EMAIL) -> bool:
    return profile.get("email") == allowed_email and profile.get("email_verified") in (True, "true", "True")


class Settings:
    def __init__(self) -> None:
        self.host = os.environ.get("NUTSNEWS_OPS_PORTAL_LISTEN_HOST", "0.0.0.0")
        self.port = int(os.environ.get("NUTSNEWS_OPS_PORTAL_LISTEN_PORT", "8090"))
        self.portal_root = Path(os.environ.get("NUTSNEWS_OPS_PORTAL_ROOT", "/srv/nutsnews-portal")).resolve()
        self.client_id = require_env("GOOGLE_CLIENT_ID")
        self.client_secret = require_env("GOOGLE_CLIENT_SECRET")
        self.callback_url = require_env("NUTSNEWS_OPS_PORTAL_CALLBACK_URL")
        validate_callback_url(self.callback_url)
        self.session_secret = require_env("NUTSNEWS_OPS_PORTAL_SESSION_SECRET")
        if len(self.session_secret) < 32:
            raise ValueError("NUTSNEWS_OPS_PORTAL_SESSION_SECRET must be at least 32 characters")
        self.allowed_email = os.environ.get("NUTSNEWS_OPS_PORTAL_ALLOWED_EMAIL", ALLOWED_EMAIL).strip()
        if self.allowed_email != ALLOWED_EMAIL:
            raise ValueError(f"NUTSNEWS_OPS_PORTAL_ALLOWED_EMAIL must be exactly {ALLOWED_EMAIL}")
        self.cookie_name = os.environ.get("NUTSNEWS_OPS_PORTAL_COOKIE_NAME", "__Host-nutsnews_ops_session")
        self.state_cookie_name = os.environ.get("NUTSNEWS_OPS_PORTAL_STATE_COOKIE_NAME", "__Host-nutsnews_ops_state")


class OAuthGateway(BaseHTTPRequestHandler):
    server_version = "NutsNewsOpsOAuth/1.0"

    def do_HEAD(self) -> None:
        self.handle_request(head=True)

    def do_GET(self) -> None:
        self.handle_request(head=False)

    def handle_request(self, head: bool) -> None:
        settings: Settings = self.server.settings  # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)

        if path == "/healthz":
            self.respond_text(HTTPStatus.OK, "ok\n", head=head)
            return
        if path == SIGNIN_PATH:
            self.start_login(settings, head=head)
            return
        if path == CALLBACK_PATH:
            self.finish_login(settings, urllib.parse.parse_qs(parsed.query), head=head)
            return
        if path == SIGNOUT_PATH:
            self.clear_cookie(settings.cookie_name)
            self.redirect(SIGNIN_PATH, head=head)
            return

        session = unsign_payload(read_cookie(self.headers.get("Cookie"), settings.cookie_name), settings.session_secret)
        if not session or session.get("email") != settings.allowed_email:
            self.redirect(SIGNIN_PATH, head=head)
            return

        self.serve_portal_file(settings, path, head=head)

    def start_login(self, settings: Settings, head: bool) -> None:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        state_cookie = sign_payload({"state": state, "nonce": nonce, "expires_at": time.time() + STATE_TTL_SECONDS}, settings.session_secret)
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", self.google_authorization_url(settings, state, nonce))
        self.set_cookie(settings.state_cookie_name, state_cookie, STATE_TTL_SECONDS)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def finish_login(self, settings: Settings, query: dict[str, list[str]], head: bool) -> None:
        if query.get("error"):
            self.respond_text(HTTPStatus.FORBIDDEN, f"Google OAuth error: {query['error'][0]}\n", head=head)
            return
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        state_payload = unsign_payload(read_cookie(self.headers.get("Cookie"), settings.state_cookie_name), settings.session_secret)
        if not code:
            self.respond_text(HTTPStatus.BAD_REQUEST, "Missing OAuth code.\n", head=head)
            return
        if not state_payload or state_payload.get("state") != state:
            self.respond_text(HTTPStatus.BAD_REQUEST, "Invalid OAuth state.\n", head=head)
            return

        try:
            token_data = self.exchange_code(settings, code)
            profile = self.verify_id_token(settings, token_data["id_token"])
        except Exception as exc:
            self.respond_text(HTTPStatus.BAD_GATEWAY, f"Google OAuth validation failed: {exc}\n", head=head)
            return

        if profile.get("nonce") not in (None, state_payload.get("nonce")):
            self.respond_text(HTTPStatus.BAD_REQUEST, "Invalid OAuth nonce.\n", head=head)
            return
        if not is_allowed_google_user(profile, settings.allowed_email):
            self.respond_text(HTTPStatus.FORBIDDEN, ACCESS_DENIED + "\n", head=head)
            return

        session = sign_payload(
            {"email": settings.allowed_email, "expires_at": time.time() + SESSION_TTL_SECONDS},
            settings.session_secret,
        )
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.set_cookie(settings.cookie_name, session, SESSION_TTL_SECONDS)
        self.clear_cookie(settings.state_cookie_name)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def google_authorization_url(self, settings: Settings, state: str, nonce: str) -> str:
        params = {
            "client_id": settings.client_id,
            "redirect_uri": settings.callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    def exchange_code(self, settings: Settings, code: str) -> dict[str, Any]:
        payload = urllib.parse.urlencode(
            {
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.callback_url,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            token_data = json.loads(response.read().decode("utf-8"))
        if "id_token" not in token_data:
            raise ValueError("Google token response did not include id_token")
        return token_data

    def verify_id_token(self, settings: Settings, id_token: str) -> dict[str, Any]:
        url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": id_token})
        with urllib.request.urlopen(url, timeout=10) as response:
            profile = json.loads(response.read().decode("utf-8"))
        if profile.get("aud") != settings.client_id:
            raise ValueError("Google ID token audience does not match this portal")
        if profile.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Google ID token issuer is invalid")
        return profile

    def serve_portal_file(self, settings: Settings, path: str, head: bool) -> None:
        if path.startswith("/api/auth/"):
            self.respond_text(HTTPStatus.NOT_FOUND, "Not found.\n", head=head)
            return
        relative = path.lstrip("/") or "index.html"
        candidate = (settings.portal_root / relative).resolve()
        if settings.portal_root not in candidate.parents and candidate != settings.portal_root:
            self.respond_text(HTTPStatus.NOT_FOUND, "Not found.\n", head=head)
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists() and "." not in Path(path).name:
            candidate = settings.portal_root / "index.html"
        if not candidate.exists() or not candidate.is_file():
            self.respond_text(HTTPStatus.NOT_FOUND, "Not found.\n", head=head)
            return

        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(candidate.stat().st_size))
        self.end_headers()
        if not head:
            with candidate.open("rb") as fh:
                self.wfile.write(fh.read())

    def respond_text(self, status: HTTPStatus, text: str, head: bool) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def redirect(self, location: str, head: bool) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def set_cookie(self, name: str, value: str, max_age: int) -> None:
        self.send_header("Set-Cookie", f"{name}={value}; Max-Age={max_age}; Path=/; Secure; HttpOnly; SameSite=Lax")

    def clear_cookie(self, name: str) -> None:
        self.send_header("Set-Cookie", f"{name}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax")

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    settings = Settings()
    server = ThreadingHTTPServer((settings.host, settings.port), OAuthGateway)
    server.settings = settings  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
