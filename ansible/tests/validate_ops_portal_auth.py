#!/usr/bin/env python3
"""Validate Ops Portal Google OAuth wiring and allowlist behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(".")
sys.path.insert(0, str(ROOT / "portal"))

import auth_gateway  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    compose = (ROOT / "compose/caddy/compose.yml").read_text(encoding="utf-8")
    caddyfile = (ROOT / "compose/caddy/Caddyfile").read_text(encoding="utf-8")
    defaults = (ROOT / "ansible/roles/vps_service_foundation/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "ansible/roles/vps_service_foundation/tasks/main.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/protected-ansible-apply.yml").read_text(encoding="utf-8")
    gateway = (ROOT / "portal/auth_gateway.py").read_text(encoding="utf-8")

    require(auth_gateway.ALLOWED_EMAIL == "rami.deltoro@gmail.com", "Allowed email constant changed unexpectedly.")
    require(auth_gateway.CALLBACK_PATH == "/api/auth/callback/google", "Google callback path is incorrect.")
    require(auth_gateway.is_allowed_google_user({"email": "rami.deltoro@gmail.com", "email_verified": True}), "Allowed user cannot sign in.")
    require(not auth_gateway.is_allowed_google_user({"email": "other@example.com", "email_verified": True}), "Non-allowlisted user can sign in.")
    require(not auth_gateway.is_allowed_google_user({"email": "rami.deltoro@gmail.com", "email_verified": False}), "Unverified Google email can sign in.")

    for url in (
        "https://ops.nutsnews.com/api/auth/callback/google",
        "https://staging.ops.nutsnews.com/api/auth/callback/google",
        "https://dev.ops.nutsnews.com/api/auth/callback/google",
    ):
        auth_gateway.validate_callback_url(url)
    for bad_url in ("https:///api/auth/callback/google", "http://ops.nutsnews.com/api/auth/callback/google", "https://ops.nutsnews.com/oauth2/callback"):
        try:
            auth_gateway.validate_callback_url(bad_url)
        except ValueError:
            pass
        else:
            raise SystemExit(f"Invalid callback URL was accepted: {bad_url}")

    require("ops-auth:" in compose, "Compose must include the ops-auth service.")
    require("portal/auth_gateway.py" in compose, "Compose must mount the auth gateway.")
    require("ops-portal-auth.env" in compose, "Compose must use the root-only auth env file.")
    require("reverse_proxy ops-auth:8090" in caddyfile, "Caddy must proxy portal traffic through auth gateway.")
    require("handle /data/*" not in caddyfile, "Caddy must not bypass auth for portal data.")
    require("handle /api/auth/*" in caddyfile, "Caddy must expose OAuth auth routes.")
    require("vps_service_foundation_ops_portal_allowed_email: rami.deltoro@gmail.com" in defaults, "Default allowlist is missing.")
    require("ops.nutsnews.com/api/auth/callback/google" in defaults, "Production callback URL is not configured.")
    require("staging.ops.nutsnews.com/api/auth/callback/google" in defaults, "Staging callback URL is not documented/configured.")
    require("dev.ops.nutsnews.com/api/auth/callback/google" in defaults, "Dev callback URL is not documented/configured.")
    require("Validate operations portal Google OAuth configuration" in tasks, "Ansible auth validation is missing.")
    require("Install operations portal auth environment" in tasks, "Ansible auth env install task is missing.")
    require("Recreate Caddy service foundation after config changes" in tasks, "Caddy must reload mounted auth routing config changes.")
    require("--force-recreate" in tasks, "Caddy config changes must force-recreate running containers.")
    require("--max-redirs" in tasks, "Portal OAuth verification must not follow redirects to Google.")
    require("^location: /api/auth/signin/google" in tasks, "Portal OAuth verification must assert the local sign-in redirect.")
    require("no_log: true" in tasks, "Auth secret-bearing Ansible tasks must use no_log.")
    require("NUTSNEWS_OPS_PORTAL_CALLBACK_URL" in workflow, "Protected apply workflow must pass callback URL.")
    require("NUTSNEWS_GOOGLE_CLIENT_ID" in workflow, "Protected apply workflow must pass Google client ID.")
    require("NUTSNEWS_GOOGLE_CLIENT_SECRET" in workflow, "Protected apply workflow must pass Google client secret.")
    require("if path == CALLBACK_PATH:" in gateway, "Auth gateway does not route the Google callback path.")
    require("self.finish_login(settings" in gateway, "Auth gateway callback does not finish login.")
    require("ACCESS_DENIED" in gateway and "HTTPStatus.FORBIDDEN" in gateway, "Denied Google users must receive a clear 403.")
    require("session.get(\"email\") != settings.allowed_email" in gateway, "Dashboard routes must require the allowlisted session email.")

    env = os.environ.copy()
    env.update(
        {
            "GOOGLE_CLIENT_ID": "test-client-id",
            "GOOGLE_CLIENT_SECRET": "test-client-secret",
            "NUTSNEWS_OPS_PORTAL_CALLBACK_URL": "https://ops.nutsnews.com/api/auth/callback/google",
            "NUTSNEWS_OPS_PORTAL_SESSION_SECRET": "x" * 40,
            "NUTSNEWS_OPS_PORTAL_ROOT": str(ROOT / "portal"),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'portal'); import auth_gateway; auth_gateway.Settings(); print('settings ok')",
        ],
        env=env,
        text=True,
        check=True,
        capture_output=True,
    )
    require(result.stdout.strip() == "settings ok", "Auth gateway settings did not validate with expected callback.")

    print("Ops Portal Google OAuth guardrails passed.")


if __name__ == "__main__":
    main()
