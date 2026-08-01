#!/usr/bin/env python3
"""Run one bounded VPS observability failure injection with durable recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn


DRILLS = {"alloy-stopped", "textfile-stale"}
STATE_ROOT = Path("/var/lib/nutsnews/observability-drills")
TEXTFILE = Path("/var/lib/nutsnews/alloy/textfile/nutsnews.prom")
ALLOY_READY_URL = "http://127.0.0.1:12345/-/ready"
ALLOY_UNIT = "alloy.service"
TEXTFILE_SERVICE = "nutsnews-observability-textfile.service"
TEXTFILE_TIMER = "nutsnews-observability-textfile.timer"
INSTALLED_PATH = "/usr/local/sbin/nutsnews-observability-failure-drill"
AUTOMATIC_RECOVERY_CONFIRMATION = "automatic-observability-drill-recovery"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")
TIMESTAMP_LINE = re.compile(
    r"(?m)^nutsnews_observability_textfile_last_success_timestamp_seconds\s+[-+0-9.eE]+$"
)
COLLECTOR_SUCCESS_LINE = re.compile(
    r"(?m)^nutsnews_observability_textfile_collector_success\s+1(?:\.0+)?$"
)


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_command(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"fixed command failed ({argv[0]} {argv[1]}), rc={result.returncode}")
    return result


def atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode(), 0o600)


def state_paths(run_id: str) -> tuple[Path, Path, Path]:
    if not RUN_ID.fullmatch(run_id):
        fail("run ID must contain 1-50 bounded characters")
    run_dir = STATE_ROOT / run_id
    if run_dir.parent != STATE_ROOT:
        fail("run state escaped the fixed state root")
    return run_dir, run_dir / "state.json", run_dir / "nutsnews.prom.before"


def require_safe_state_root() -> None:
    root_stat = STATE_ROOT.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeError("drill state root must be one non-symlink directory")
    if STATE_ROOT.resolve() != STATE_ROOT or stat.S_IMODE(root_stat.st_mode) & 0o077:
        raise RuntimeError("drill state root must be the exact root-only managed directory")


def read_state(run_id: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    require_safe_state_root()
    run_dir, state_file, backup = state_paths(run_id)
    try:
        state_value = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("drill recovery state is missing or invalid") from exc
    if not isinstance(state_value, dict) or state_value.get("run_id") != run_id:
        raise RuntimeError("drill recovery state identity mismatch")
    if state_value.get("drill") not in DRILLS:
        raise RuntimeError("drill recovery state has an unsupported drill")
    return run_dir, state_file, backup, state_value


def service_active(unit: str) -> bool:
    return run_command(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0


def alloy_ready() -> bool:
    try:
        request = urllib.request.Request(ALLOY_READY_URL, method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200
    except (TimeoutError, urllib.error.URLError):
        return False


def safe_textfile() -> os.stat_result:
    file_stat = TEXTFILE.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError("managed textfile metric path must be one regular non-symlink file")
    if TEXTFILE.resolve() != TEXTFILE:
        raise RuntimeError("managed textfile metric path resolved outside its exact path")
    return file_stat


def replace_success_timestamp(payload: bytes, timestamp: int) -> bytes:
    text = payload.decode("utf-8")
    matches = list(TIMESTAMP_LINE.finditer(text))
    if len(matches) != 1:
        raise RuntimeError("textfile output must contain exactly one collector success timestamp")
    replacement = f"nutsnews_observability_textfile_last_success_timestamp_seconds {timestamp}"
    return TIMESTAMP_LINE.sub(replacement, text, count=1).encode()


def collector_is_fresh(payload: bytes, now: int) -> bool:
    text = payload.decode("utf-8")
    matches = list(TIMESTAMP_LINE.finditer(text))
    if len(matches) != 1 or not COLLECTOR_SUCCESS_LINE.search(text):
        return False
    try:
        timestamp = float(matches[0].group(0).rsplit(maxsplit=1)[1])
    except (IndexError, ValueError):
        return False
    age = now - timestamp
    return -300 <= age <= 120


def recovery_unit(run_id: str) -> str:
    return f"nutsnews-observability-drill-recovery-{run_id}"


def schedule_recovery(run_id: str, failsafe_seconds: int) -> None:
    unit = recovery_unit(run_id)
    run_command(
        [
            "systemd-run",
            "--quiet",
            f"--unit={unit}",
            f"--on-active={failsafe_seconds}s",
            "--property=Type=oneshot",
            INSTALLED_PATH,
            "recover",
            "--run-id",
            run_id,
            "--confirmation",
            AUTOMATIC_RECOVERY_CONFIRMATION,
        ]
    )


def cancel_recovery_timer(run_id: str) -> None:
    unit = recovery_unit(run_id)
    run_command(["systemctl", "stop", f"{unit}.timer"], check=False)
    run_command(["systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"], check=False)


def plan(drill: str, run_id: str, failsafe_seconds: int) -> dict[str, Any]:
    target = "vps.nutsnews.com"
    return {
        "drill": drill,
        "run_id": run_id,
        "target": target,
        "mode": "dry-run",
        "mutation_performed": False,
        "failsafe_seconds": failsafe_seconds,
        "confirmation_required": f"execute-grafana-failure-drill:{target}:{drill}",
        "injection": "stop Alloy" if drill == "alloy-stopped" else "stop textfile timer and age collector timestamp",
        "recovery": "start Alloy and require readiness" if drill == "alloy-stopped" else "refresh textfile output and restart timer",
    }


def inject(drill: str, run_id: str, confirmation: str, failsafe_seconds: int) -> dict[str, Any]:
    target = "vps.nutsnews.com"
    if confirmation != f"execute-grafana-failure-drill:{target}:{drill}":
        fail("execute confirmation does not match the exact target and drill")
    require_safe_state_root()
    run_dir, state_file, backup = state_paths(run_id)
    try:
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeError("refusing to reuse an existing drill run ID") from exc

    state_value: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "drill": drill,
        "target": target,
        "status": "prechecked",
        "started_at": utc_timestamp(),
        "failsafe_seconds": failsafe_seconds,
        "recovery_scheduled": False,
    }
    try:
        if drill == "alloy-stopped":
            if not service_active(ALLOY_UNIT) or not alloy_ready():
                raise RuntimeError("Alloy must be active and ready before failure injection")
            state_value["precheck"] = {"alloy_active": True, "alloy_ready": True}
        else:
            if not service_active(TEXTFILE_TIMER):
                raise RuntimeError("textfile timer must be active before failure injection")
            file_stat = safe_textfile()
            original = TEXTFILE.read_bytes()
            replace_success_timestamp(original, int(time.time()) - 600)
            atomic_write(backup, original, 0o600)
            state_value["precheck"] = {
                "timer_active": True,
                "textfile_sha256": hashlib.sha256(original).hexdigest(),
                "textfile_mode": stat.S_IMODE(file_stat.st_mode),
                "textfile_uid": file_stat.st_uid,
                "textfile_gid": file_stat.st_gid,
            }

        atomic_json(state_file, state_value)
        schedule_recovery(run_id, failsafe_seconds)
        state_value["recovery_scheduled"] = True
        state_value["status"] = "recovery-scheduled"
        atomic_json(state_file, state_value)

        if drill == "alloy-stopped":
            run_command(["systemctl", "stop", ALLOY_UNIT])
            if service_active(ALLOY_UNIT):
                raise RuntimeError("Alloy remained active after fixed stop injection")
            state_value["injection"] = {"alloy_active": False}
        else:
            run_command(["systemctl", "stop", TEXTFILE_TIMER])
            run_command(["systemctl", "stop", TEXTFILE_SERVICE], check=False)
            file_stat = safe_textfile()
            original = backup.read_bytes()
            stale_timestamp = int(time.time()) - 600
            stale = replace_success_timestamp(original, stale_timestamp)
            atomic_write(TEXTFILE, stale, stat.S_IMODE(file_stat.st_mode))
            os.chown(TEXTFILE, file_stat.st_uid, file_stat.st_gid)
            if service_active(TEXTFILE_TIMER):
                raise RuntimeError("textfile timer remained active after fixed stop injection")
            state_value["injection"] = {"timer_active": False, "stale_age_seconds": 600}

        state_value["status"] = "injected"
        state_value["injected_at"] = utc_timestamp()
        atomic_json(state_file, state_value)
        return state_value
    except Exception:
        state_value["status"] = "injection-failed"
        state_value["injection_failed_at"] = utc_timestamp()
        atomic_json(state_file, state_value)
        if state_value.get("recovery_scheduled"):
            recover(run_id, AUTOMATIC_RECOVERY_CONFIRMATION)
        raise


def recover(run_id: str, confirmation: str) -> dict[str, Any]:
    run_dir, state_file, backup, state_value = read_state(run_id)
    drill = str(state_value["drill"])
    target = str(state_value["target"])
    expected = f"recover-grafana-failure-drill:{target}:{drill}"
    if confirmation not in {expected, AUTOMATIC_RECOVERY_CONFIRMATION}:
        fail("recovery confirmation does not match the exact target and drill")
    if state_value.get("status") == "recovered":
        return state_value

    errors: list[str] = []
    try:
        if drill == "alloy-stopped":
            result = run_command(["systemctl", "start", ALLOY_UNIT], check=False)
            if result.returncode != 0:
                errors.append("alloy_start_failed")
        else:
            refresh = run_command(["systemctl", "start", TEXTFILE_SERVICE], check=False)
            if refresh.returncode != 0:
                errors.append("textfile_refresh_failed")
                if backup.exists():
                    precheck = state_value.get("precheck", {})
                    atomic_write(TEXTFILE, backup.read_bytes(), int(precheck.get("textfile_mode", 0o644)))
                    os.chown(TEXTFILE, int(precheck.get("textfile_uid", 0)), int(precheck.get("textfile_gid", 0)))
            timer = run_command(["systemctl", "start", TEXTFILE_TIMER], check=False)
            if timer.returncode != 0:
                errors.append("textfile_timer_start_failed")

        deadline = time.monotonic() + 60
        if drill == "alloy-stopped":
            while time.monotonic() < deadline and not (service_active(ALLOY_UNIT) and alloy_ready()):
                time.sleep(2)
            postcheck = {"alloy_active": service_active(ALLOY_UNIT), "alloy_ready": alloy_ready()}
            if not all(postcheck.values()):
                errors.append("alloy_postcheck_failed")
        else:
            while time.monotonic() < deadline and not service_active(TEXTFILE_TIMER):
                time.sleep(2)
            postcheck = {"timer_active": service_active(TEXTFILE_TIMER), "textfile_regular": False}
            try:
                safe_textfile()
                refreshed = TEXTFILE.read_bytes()
                postcheck["textfile_regular"] = True
                postcheck["collector_fresh"] = collector_is_fresh(refreshed, int(time.time()))
            except OSError:
                postcheck["collector_fresh"] = False
            if not all(postcheck.values()):
                errors.append("textfile_postcheck_failed")

        state_value["recovery"] = postcheck
        state_value["recovered_at"] = utc_timestamp()
        state_value["status"] = "recovered" if not errors else "recovery-failed"
        state_value["recovery_errors"] = errors
        atomic_json(state_file, state_value)
    finally:
        # Keep the independently scheduled recovery alive when an explicit
        # workflow recovery attempt fails. It is the last bounded retry path.
        # Successful recovery can safely cancel that timer and remove backup
        # material; failed recovery retains both for the scheduled attempt.
        if state_value.get("status") == "recovered":
            cancel_recovery_timer(run_id)
            if backup.exists():
                backup.unlink()
        os.chmod(run_dir, 0o700)
    if errors:
        raise RuntimeError("bounded drill recovery failed: " + ",".join(errors))
    return state_value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "inject", "recover", "status"))
    parser.add_argument("--drill", choices=sorted(DRILLS))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--failsafe-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.failsafe_seconds < 300 or args.failsafe_seconds > 1800:
        parser.error("--failsafe-seconds must be between 300 and 1800")
    if args.action in {"plan", "inject"} and not args.drill:
        parser.error("--drill is required for plan and inject")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.action == "plan":
            report = plan(args.drill, args.run_id, args.failsafe_seconds)
        elif args.action == "inject":
            report = inject(args.drill, args.run_id, args.confirmation, args.failsafe_seconds)
        elif args.action == "recover":
            report = recover(args.run_id, args.confirmation)
        else:
            report = read_state(args.run_id)[3]
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
