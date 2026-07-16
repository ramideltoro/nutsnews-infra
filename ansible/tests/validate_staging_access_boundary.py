#!/usr/bin/env python3
"""Offline regression coverage for staging hostname, credentials, and origin access."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
GATEWAY_PATH = REPO / "staging-access/jwt_gateway.py"
CADDY_BASE = REPO / "compose/caddy/Caddyfile"
CADDY_TEMPLATE = ROOT / "roles/vps_service_foundation/templates/Caddyfile.j2"
STAGING_WORKFLOW = REPO / ".github/workflows/nutsnews-staging-deploy.yml"
TEST_WORKFLOW = REPO / ".github/workflows/staging-access-probe.yml"
CLOUDFLARE_WORKFLOW = REPO / ".github/workflows/cloudflare-access-apply.yml"
CLOUDFLARE_MAIN = REPO / "terraform/staging-access/main.tf"
STAGING_ACCESS_COMPOSE = REPO / "compose/staging-access/compose.yml"
ENVIRONMENT_TASKS = ROOT / "roles/vps_service_foundation/tasks/nutsnews_environment.yml"
ACCESS_TASKS = ROOT / "roles/vps_service_foundation/tasks/staging_access.yml"
FORCED_COMMAND = ROOT / "scripts/staging_forced_deploy.py"
WRITE_VARS = ROOT / "scripts/write_staging_ansible_vars.py"
MAIN_TASKS = ROOT / "roles/vps_service_foundation/tasks/main.yml"


def render_caddy(enabled: bool) -> str:
    text = CADDY_TEMPLATE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\{% if vps_service_foundation_nutsnews_staging_access_enabled \| bool %\}\n"
        r"(?P<staging>.*?)"
        r"\{% endif %\}\n",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match
    return text[: match.start()] + (match.group("staging") if enabled else "") + text[match.end() :]


base = CADDY_BASE.read_text(encoding="utf-8")
disabled = render_caddy(False)
enabled = render_caddy(True)
assert disabled == base, "Disabled staging access must render the production Caddyfile byte-for-byte."
assert enabled.count("staging.nutsnews.com {") == 1
assert "admin off" in enabled
assert "forward_auth nutsnews-staging-access:8091" in enabled
assert "uri /verify?" in enabled, "The verifier subrequest must discard OAuth callback query material."
assert "uri /verify\n" not in enabled, "The verifier must not receive the original request query."
assert "request>uri delete" in enabled, "Staging access logs must omit query-bearing request URIs."
for log_field in (
    "request>headers>Cookie delete",
    "request>headers>Authorization delete",
    "request>headers>Proxy-Authorization delete",
    "request>headers>Cf-Access-Jwt-Assertion delete",
    "request>headers>CF-Access-Client-Id delete",
    "request>headers>CF-Access-Client-Secret delete",
    "resp_headers>Set-Cookie delete",
    "resp_headers>Location delete",
):
    assert log_field in enabled, f"Staging access logs must delete {log_field}."
assert "reverse_proxy nutsnews-app-staging:3000" in enabled
assert 'X-Robots-Tag "noindex, nofollow, noarchive, nosnippet"' in enabled
assert enabled.replace(enabled[enabled.index("staging.nutsnews.com {") : enabled.index("ops.nutsnews.com {")], "") == base

workflow = STAGING_WORKFLOW.read_text(encoding="utf-8")
test_workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
cloudflare_workflow = CLOUDFLARE_WORKFLOW.read_text(encoding="utf-8")
cloudflare_main = CLOUDFLARE_MAIN.read_text(encoding="utf-8")
staging_access_compose = STAGING_ACCESS_COMPOSE.read_text(encoding="utf-8")
environment_tasks = ENVIRONMENT_TASKS.read_text(encoding="utf-8")
access_tasks = ACCESS_TASKS.read_text(encoding="utf-8")
forced_command = FORCED_COMMAND.read_text(encoding="utf-8")
write_vars = WRITE_VARS.read_text(encoding="utf-8")
raw_main_tasks = yaml.safe_load(MAIN_TASKS.read_text(encoding="utf-8"))


def flatten_tasks(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for task in tasks:
        flattened.append(task)
        nested = task.get("block")
        if isinstance(nested, list):
            flattened.extend(flatten_tasks(nested))
    return flattened


main_tasks = flatten_tasks(raw_main_tasks)
parsed_access_tasks = yaml.safe_load(access_tasks)

staging_input_task = next(
    task for task in main_tasks if task.get("name") == "Validate opt-in staging access boundary inputs"
)
staging_assertions = staging_input_task["ansible.builtin.assert"]["that"]
assert all(isinstance(assertion, str) for assertion in staging_assertions), (
    "Every staging input assertion must remain a YAML string so Ansible can evaluate it."
)

staging_sudoers_task = next(
    task for task in parsed_access_tasks if task.get("name") == "Restrict staging identity sudo to the forced command"
)
staging_sudoers_content = staging_sudoers_task["ansible.builtin.copy"]["content"]
assert staging_sudoers_content.endswith("\n"), (
    "The sudoers fragment must end with a newline so visudo accepts the generated file."
)

for compose_task_name in (
    "Validate staging access Compose configuration",
    "Start isolated staging access verifier",
):
    compose_task = next(task for task in parsed_access_tasks if task.get("name") == compose_task_name)
    assert compose_task["environment"]["NUTSNEWS_STAGING_ACCESS_ENV_FILE"] == (
        "{{ vps_service_foundation_nutsnews_staging_access_env_file }}"
    ), f"{compose_task_name} must define the Compose env-file interpolation variable."

assert not any(task.get("name") == "Reload Caddy after staging access becomes reachable" for task in parsed_access_tasks), (
    "Staging access must not invoke Caddy's disabled admin API after connecting the existing container network."
)

assert "environment: staging-vps" in workflow
assert "production-vps" not in workflow
assert "nutsnews_staging_deploy@" in workflow
assert "nutsnews_ops@" not in workflow
assert "Prove the deployment key rejects arbitrary commands" in workflow
assert "environment: staging-tests" in test_workflow
assert "production-vps" not in test_workflow and "staging-vps" not in test_workflow
assert "NUTSNEWS_STAGING_VPS_SSH_PRIVATE_KEY" not in test_workflow
assert "NUTSNEWS_STAGING_APP_ENVS_JSON" not in test_workflow
assert "returned HTTP ${authenticated_status}, expected 200" in test_workflow
assert "for endpoint in healthz readyz" in test_workflow
assert "environment: cloudflare-admin" in cloudflare_workflow
assert "production-vps" not in cloudflare_workflow
assert "staging-vps" not in cloudflare_workflow
assert "staging-tests" not in cloudflare_workflow
assert 'decision   = "bypass"' in cloudflare_main
assert 'domain               = "staging.nutsnews.com/.well-known/acme-challenge/*"' in cloudflare_main
assert "cloudflare_zero_trust_access_policy.acme_challenge.id" in cloudflare_main
assert 'same_site_cookie_attribute = "lax"' in cloudflare_main
assert 'same_site_cookie_attribute = "strict"' not in cloudflare_main
assert "http_only_cookie_attribute = true" in cloudflare_main
assert "enable_binding_cookie      = true" in cloudflare_main
assert "flexible" not in cloudflare_main.lower()
assert "no-new-privileges=true" in staging_access_compose
assert "no-new-privileges:true" not in staging_access_compose
assert 'mode: "0600"' in environment_tasks and "no_log: true" in environment_tasks
defaults = (ROOT / "roles/vps_service_foundation/defaults/main.yml").read_text()
assert "vps_service_foundation_nutsnews_app_runtime_owner: root" in defaults
assert "vps_service_foundation_nutsnews_app_runtime_group: root" in defaults
assert "nutsnews-staging-app.env" in defaults
assert 'mode: "0600"' in access_tasks and "no_log: true" in access_tasks
assert 'SSH_ORIGINAL_COMMAND' in forced_command and 'arbitrary_command_rejected' in forced_command
assert 'operation in {"check", "apply"}' in forced_command
assert 'elif operation == "verify"' in forced_command
assert 'operation == "production"' not in forced_command
assert 'operation in {"production"' not in forced_command
assert "stdout=subprocess.PIPE" in forced_command
assert "stderr=subprocess.STDOUT" in forced_command
assert "TASK_LINE.findall(result.stdout)" in forced_command
assert '"ANSIBLE_NOCOLOR": "1"' in forced_command
assert '"ANSIBLE_ROLES_PATH": str(BUNDLE / "ansible/roles")' in forced_command
assert '"ANSIBLE_STDOUT_CALLBACK": "default"' in forced_command
assert '"ANSIBLE_PRIVATE_ROLE_VARS": "false"' in forced_command
assert "Ansible output can contain rendered diffs" in forced_command
assert "Staging gateway returned an invalid task label." in workflow
assert "Staging gateway returned an invalid diagnostic class." in workflow
assert "Staging gateway returned an invalid controller version." in workflow
assert "reviewed task" in workflow
assert "staging_syntax_failed" in forced_command
assert "classify_controller_output" in forced_command
for boundary_check in (
    "staging_unpublished",
    "compose_projects",
    "immutable_digest",
    "network_separation",
    "resource_limits",
    "log_limits",
    "directory_separation",
    "env_file_permissions",
    "caddy_route",
    "access_verifier_healthy",
):
    assert boundary_check in forced_command and boundary_check in workflow
assert '"production": production_observation' in forced_command
assert 'production = result.get("production")' in workflow
assert "Production container health was recorded as sanitized observation only" in workflow
assert '"production_healthy"' not in workflow
assert '"uri /verify?" in caddy_text' in forced_command
assert '"request>uri delete" in caddy_text' in forced_command
assert '"request>headers>Cf-Access-Jwt-Assertion delete" in caddy_text' in forced_command
assert '"resp_headers>Location delete" in caddy_text' in forced_command
assert "TEST_USER" in write_vars and "staging-tests" in write_vars
assert "NUTSNEWS_PRODUCTION_SUPABASE_PROJECT_REF" in write_vars

staging_tls_before = next(
    task for task in main_tasks if task.get("name") == "Probe direct staging Caddy TLS before reconciliation"
)
staging_tls_after = next(
    task for task in main_tasks if task.get("name") == "Wait for direct staging Caddy TLS after reconciliation"
)
staging_tls_assertion = next(
    task for task in main_tasks if task.get("name") == "Assert direct staging Caddy TLS was reconciled"
)
caddy_recreate = next(
    task for task in main_tasks if task.get("name") == "Recreate Caddy service foundation after config changes"
)
expected_tls_command = [
    "timeout",
    "15",
    "openssl",
    "s_client",
    "-brief",
    "-connect",
    "127.0.0.1:443",
    "-servername",
    "staging.nutsnews.com",
    "-verify_return_error",
    "-verify_hostname",
    "staging.nutsnews.com",
]
assert staging_tls_before["ansible.builtin.command"]["argv"] == expected_tls_command
assert staging_tls_before["ansible.builtin.command"]["stdin"] == ""
assert staging_tls_before["failed_when"] is False
expected_tls_when = [
    "vps_service_foundation_nutsnews_staging_access_enabled | bool",
    "not ansible_check_mode",
]
assert staging_tls_before["when"] == expected_tls_when
assert staging_tls_after["ansible.builtin.command"]["argv"] == expected_tls_command
assert staging_tls_after["retries"] == 12 and staging_tls_after["delay"] == 5
assert staging_tls_after["until"] == "vps_service_foundation_staging_caddy_tls_after.rc == 0"
assert staging_tls_after["when"] == expected_tls_when
assert staging_tls_assertion["ansible.builtin.assert"]["that"] == [
    "vps_service_foundation_staging_caddy_tls_after.rc == 0"
]
assert staging_tls_assertion["when"] == expected_tls_when
assert "vps_service_foundation_staging_caddy_tls_before.rc" in caddy_recreate["when"], (
    "A failed direct staging TLS probe must force only the existing Caddy reconciliation path."
)

# Exercise both authenticated and unauthenticated verifier paths with a local RSA fixture.
os.environ["NUTSNEWS_STAGING_ACCESS_TEAM_DOMAIN"] = "fixture.cloudflareaccess.com"
os.environ["NUTSNEWS_STAGING_ACCESS_AUDIENCE"] = "fixture-audience-1234567890"
spec = importlib.util.spec_from_file_location("staging_access_gateway", GATEWAY_PATH)
assert spec and spec.loader
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
jwk = {
    "kid": "fixture-key",
    "kty": "RSA",
    "n": "0CMFZ0K-JvtHvjuTusghJoihPZXWUaqusW-UTQFnyoVpbN5lbqQy2EuC6_dWhhJwZEeuBJKAIuRVHqFumlUROrStO1OERakGAgPB03HGtOh7jzqff03MNzN6jRNqLoNvKtR_wiTfzyfSDTsf8g2fTh3p5IxMimaj-alXG48uq33L7quT-U83d-_NVLB2XqlpROzzYcT03iqQO8OCyEHXIHUEbrEHpT2f0MDwPoqVCIbJKt6qwbuKJmbeVUZE4YfMbfHjvdQLKNDp_tYvroYNQNFKDbSEDJuSBcJhLf_ScgJ_nZspXR3ZI69p35ZbOG08urWhj8_eKMdZD2engk9bjw",
    "e": "AQAB",
}
gateway._load_jwks = lambda: {"fixture-key": jwk}
now = int(time.time())
token = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZpeHR1cmUta2V5In0."
    "eyJhdWQiOlsiZml4dHVyZS1hdWRpZW5jZS0xMjM0NTY3ODkwIl0sImlzcyI6Imh0dHBzOi8vZml4dHVyZS5jbG91ZGZsYXJlYWNjZXNzLmNvbSIsImV4cCI6NDEwMjQ0NDgwMCwibmJmIjowfQ."
    "qfz-YE5OkrL_3M1EGitMALckW_j3NJfSYQYh3KTlY526BKAtETSxJRx-nk67j7CWhi06Tj5zZFi-UyYF7KJVQdYOI39YFzkWuOr1VVRvrvCpkWKiujOw2Ngjm6nfTDaVrqUXqoxpihSet4ugXChocMtFn_4Ps0g2lTMDbKq-lMMgfbpP2S6uW93ZV5UpkRVAkA3IxbE23_Myv758WizrAdt-lE7CXBCJUR9MDujmQ8AlnMxcEp2J2rhGGAlcHaUi6cxXDlG3PSd9hSaGwyg7QV3Xk-e9KJUnQUQVi-8sqdfEaMQ_7NEaQLLyzuiEgZNrj9J26GsfQ_DJipkyVm_0Xg"
)
assert gateway.verify_access_token(token, now)["aud"] == [gateway.AUDIENCE]
try:
    gateway.verify_access_token(token + "x", now)
except ValueError:
    pass
else:
    raise AssertionError("Tampered Access token must fail closed.")

server = gateway.ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    verify_url = f"http://127.0.0.1:{server.server_port}/verify"
    try:
        urllib.request.urlopen(verify_url, timeout=2)
    except urllib.error.HTTPError as error:
        assert error.code == 401
        assert error.headers["Cache-Control"] == "no-store"
    else:
        raise AssertionError("Unauthenticated origin request must fail closed.")
    request = urllib.request.Request(
        verify_url,
        headers={"Cf-Access-Jwt-Assertion": token},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 204
        assert response.headers["Cache-Control"] == "no-store"
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

print("Staging access, workflow trust boundary, and production Caddy invariance checks passed.")
