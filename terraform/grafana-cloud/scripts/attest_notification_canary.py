#!/usr/bin/env python3
"""Record a human receipt attestation for an existing canary without refiring it."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from exercise_notification_canary import (  # noqa: E402
    iso8601,
    safe_canary_id,
    utc_now,
)


EXPECTED_REPOSITORY = "ramideltoro/nutsnews-infra"
EXPECTED_REF = "refs/heads/main"
EXPECTED_EVENT = "workflow_dispatch"
SHA256_REFERENCE = re.compile(r"sha256:[a-f0-9]{64}")
HUMAN_CONFIRMATION_TEMPLATE = (
    "human-attest-grafana-notification-canary:{canary_id}:"
    "firing-and-resolved-received"
)


def normalized_sha256_reference(value: str, label: str) -> str:
    """Return one opaque content digest without accepting evidence locators."""
    reference = value.strip()
    if SHA256_REFERENCE.fullmatch(reference) is None:
        raise ValueError(f"{label} must be an opaque lowercase sha256:<64-hex> reference")
    return reference


def normalized_human_actor(value: str) -> str:
    actor = value.strip()
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", actor) is None:
        raise ValueError("human receipt attestation requires a valid GitHub user actor")
    return actor


def positive_run_number(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def build_attestation(
    canary_id: str,
    api_transition_evidence_sha256: str,
    receipt_evidence_sha256: str,
    human_confirmation: str,
    github_actor: str,
    github_run_id: int,
    github_run_attempt: int,
    github_repository: str,
    github_ref: str,
    github_event_name: str,
    *,
    attested_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build a human attestation bound to one GitHub actor and workflow attempt."""
    raw_canary_id = canary_id.strip()
    canary_id = safe_canary_id(raw_canary_id)
    if canary_id != raw_canary_id:
        raise ValueError("receipt attestation requires an already-normalized canary ID")
    run_match = re.fullmatch(r"github-([1-9][0-9]*)", canary_id)
    if run_match is None:
        raise ValueError("receipt attestation requires canary ID github-<actions-run-id>")
    original_run_id = int(run_match.group(1))
    api_reference = normalized_sha256_reference(
        api_transition_evidence_sha256, "API-transition evidence reference"
    )
    receipt_reference = normalized_sha256_reference(
        receipt_evidence_sha256, "mailbox-receipt evidence reference"
    )
    if api_reference == receipt_reference:
        raise ValueError(
            "API-transition and mailbox-receipt evidence must use distinct references"
        )
    actor = normalized_human_actor(github_actor)
    attestation_run_id = positive_run_number(github_run_id, "GitHub run ID")
    attestation_run_attempt = positive_run_number(
        github_run_attempt, "GitHub run attempt"
    )
    if attestation_run_id == original_run_id:
        raise ValueError("receipt attestation must run separately from the fire/resolve run")
    if github_repository.strip() != EXPECTED_REPOSITORY:
        raise ValueError("receipt attestation requires the exact nutsnews-infra repository")
    if github_ref.strip() != EXPECTED_REF:
        raise ValueError("receipt attestation requires the exact main branch ref")
    if github_event_name.strip() != EXPECTED_EVENT:
        raise ValueError("receipt attestation requires workflow_dispatch")
    expected_confirmation = HUMAN_CONFIRMATION_TEMPLATE.format(canary_id=canary_id)
    if human_confirmation.strip() != expected_confirmation:
        raise ValueError("receipt attestation requires the exact human confirmation")

    timestamp = attested_at or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("receipt attestation timestamp must be timezone-aware")
    timestamp_text = iso8601(timestamp)
    binding = {
        "api_transition_evidence_sha256": api_reference,
        "attestation_run_attempt": attestation_run_attempt,
        "attestation_run_id": attestation_run_id,
        "attested_at": timestamp_text,
        "attested_by": actor,
        "canary_id": canary_id,
        "github_ref": EXPECTED_REF,
        "github_repository": EXPECTED_REPOSITORY,
        "original_run_id": original_run_id,
        "receipt_evidence_sha256": receipt_reference,
    }
    binding_sha256 = hashlib.sha256(
        json.dumps(binding, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 4,
        "phase": "receipt_human_attested",
        "status": "pass",
        "canary_id": canary_id,
        "original_run_id": original_run_id,
        "receipt_status": "human_attested",
        "attestation_method": "explicit_github_human",
        "human_confirmation_recorded": True,
        "attested_by": actor,
        "attestation_run_id": attestation_run_id,
        "attestation_run_attempt": attestation_run_attempt,
        "github_repository": EXPECTED_REPOSITORY,
        "github_ref": EXPECTED_REF,
        "api_transition_evidence_sha256": api_reference,
        "receipt_evidence_sha256": receipt_reference,
        "attestation_binding_sha256": f"sha256:{binding_sha256}",
        "evidence_store_allowlisted": False,
        "evidence_store_fetched": False,
        "independent_verification_performed": False,
        "firing_receipt_human_attested": True,
        "resolved_receipt_human_attested": True,
        "refired": False,
        "attested_at": timestamp_text,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canary-id", required=True)
    parser.add_argument("--api-transition-evidence-sha256", required=True)
    parser.add_argument("--receipt-evidence-sha256", required=True)
    parser.add_argument("--human-confirmation", required=True)
    parser.add_argument("--github-actor", required=True)
    parser.add_argument("--github-run-id", type=int, required=True)
    parser.add_argument("--github-run-attempt", type=int, required=True)
    parser.add_argument("--github-repository", required=True)
    parser.add_argument("--github-ref", required=True)
    parser.add_argument("--github-event-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        attestation = build_attestation(
            args.canary_id,
            args.api_transition_evidence_sha256,
            args.receipt_evidence_sha256,
            args.human_confirmation,
            args.github_actor,
            args.github_run_id,
            args.github_run_attempt,
            args.github_repository,
            args.github_ref,
            args.github_event_name,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(attestation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
