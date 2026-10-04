"""Small PBS/Torque adapter for one already materialized VASP attempt.

This module intentionally handles only the local login-node part of the
workflow: render a readable script, submit it with ``qsub``, inspect one job
with ``qstat``, and collect the bounded VASP summary.  Staging, SSH, node
selection, restart policy, and input editing belong outside this adapter.
"""

import importlib.util
import json
import re
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROFILE_SCHEMA = "fpilot.pbs-site.v1"
JOB_SCHEMA = "fpilot.pbs-job.v1"
JOB_TOKEN_RE = re.compile(r"^[0-9]+(?:\.[A-Za-z0-9_.-]+)?$")
STATE_RE = re.compile(r"\bjob_state\s*=\s*([A-Z])\b", re.I)
PBS_STATES = set("Q R H W R E F C".split())
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class PBSJobError(ValueError):
    """A user-facing PBS preparation or scheduler error."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PBSJobError("cannot read JSON {}: {}".format(path, exc)) from exc


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix="." + path.name + ".", delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise PBSJobError("cannot load bundled helper: {}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_inputs(attempt, profile="static", executable="vasp_std"):
    """Call the bundled input audit without adding a package dependency."""
    path = Path(__file__).resolve().parents[1] / "skills" / "calc-review" / "scripts" / "input_audit.py"
    return _load_helper("fpilot_pbs_input_audit", path).audit_inputs(
        attempt, profile=profile, executable=executable
    )


def collect_summary(attempt, tail_lines=20):
    """Call the bundled bounded summary collector."""
    path = Path(__file__).resolve().parents[1] / "skills" / "calc-review" / "scripts" / "collect_summary.py"
    return _load_helper("fpilot_pbs_collect_summary", path).collect_summary(attempt, tail_lines)


def load_profile(profile):
    if isinstance(profile, (str, Path)):
        profile = _read_json(profile)
    if not isinstance(profile, dict):
        raise PBSJobError("PBS site profile must be a JSON object")
    value = dict(profile)
    required = (
        "schema", "name", "queue", "node", "ppn", "walltime",
        "mpi_executable", "vasp_executable", "environment",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise PBSJobError("PBS profile is missing: {}".format(", ".join(missing)))
    if value["schema"] != PROFILE_SCHEMA:
        raise PBSJobError("unsupported PBS profile schema: {}".format(value["schema"]))
    for key in ("name", "queue", "node", "walltime", "mpi_executable", "vasp_executable"):
        if not isinstance(value[key], str) or not value[key].strip() or "\n" in value[key] or "\r" in value[key]:
            raise PBSJobError("PBS profile field {} must be a non-empty single line".format(key))
    for key in ("name", "queue", "node"):
        if not SAFE_TOKEN_RE.fullmatch(value[key]):
            raise PBSJobError("PBS profile field {} is not a valid PBS token".format(key))
    if not isinstance(value["ppn"], int) or isinstance(value["ppn"], bool) or value["ppn"] <= 0:
        raise PBSJobError("PBS profile ppn must be a positive integer")
    if not re.fullmatch(r"(?:[0-9]{2}:)?[0-9]{2}:[0-9]{2}", value["walltime"]):
        raise PBSJobError("PBS profile walltime must be HH:MM:SS")
    for key in ("mpi_executable", "vasp_executable"):
        if not Path(value[key]).is_absolute():
            raise PBSJobError("PBS profile {} must be an absolute path".format(key))
    if not isinstance(value["environment"], list) or any(
        not isinstance(line, str) or "\n" in line or "\r" in line for line in value["environment"]
    ):
        raise PBSJobError("PBS profile environment must be a list of single-line strings")
    for key, default in (("qsub_command", "qsub"), ("qstat_command", "qstat -f")):
        command = value.get(key, default)
        if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
            raise PBSJobError("PBS profile {} must be a command string".format(key))
        value[key] = command
    return value


def _attempt(attempt, profile):
    root = Path(attempt).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise PBSJobError("attempt directory does not exist: {}".format(root))
    metadata = _read_json(root / "_fpilot" / "attempt.json")
    lifecycle = metadata.get("state", {}).get("lifecycle")
    existing_job = root / "job.json"
    if lifecycle != "PREFLIGHTED" and not (
        existing_job.is_file() and _read_json(existing_job).get("job_id")
    ):
        raise PBSJobError("attempt lifecycle is not PREFLIGHTED")
    materialization = _read_json(root / "_fpilot" / "materialization.json")
    if materialization.get("status") != "PREFLIGHTED":
        raise PBSJobError("materialization status is not PREFLIGHTED")
    job = _read_json(root / "_fpilot" / "vasp-job.json")
    executable = job.get("executable")
    if not isinstance(executable, str) or not executable.strip():
        raise PBSJobError("vasp-job.json has no executable")
    if Path(executable).name != Path(profile["vasp_executable"]).name:
        raise PBSJobError("profile VASP basename does not match vasp-job.json")
    return root, metadata, materialization, job


def _audit(root, profile):
    report = audit_inputs(root, profile="static", executable=profile["vasp_executable"])
    if not isinstance(report, dict) or report.get("status") != "PASS":
        errors = report.get("errors") if isinstance(report, dict) else None
        raise PBSJobError("input audit failed: {}".format("; ".join(errors or ["unknown error"])))
    return report


def _quote(value):
    value = str(value)
    return "'" + value.replace("'", "'\\''") + "'"


def render_script(profile):
    profile = load_profile(profile)
    env = "\n".join(profile["environment"])
    if env:
        env += "\n"
    return """#!/bin/bash
#PBS -N {name}
#PBS -q {queue}
#PBS -l nodes={node}:ppn={ppn}
#PBS -l walltime={walltime}
#PBS -j n
#PBS -o pbs.out
#PBS -e pbs.err
#PBS -S /bin/bash

{environment}set -euo pipefail
cd "$PBS_O_WORKDIR"
export OMP_NUM_THREADS=1
ulimit -s unlimited
NPROCS=$(wc -l < "$PBS_NODEFILE")
MPI_EXECUTABLE={mpi}
VASP_EXECUTABLE={vasp}
test -x "$MPI_EXECUTABLE"
test -x "$VASP_EXECUTABLE"
"$MPI_EXECUTABLE" -machinefile "$PBS_NODEFILE" -np "$NPROCS" "$VASP_EXECUTABLE" > vasp.out 2> vasp.err
""".format(
        name=profile["name"], queue=profile["queue"], node=profile["node"],
        ppn=profile["ppn"], walltime=profile["walltime"], environment=env,
        mpi=_quote(profile["mpi_executable"]), vasp=_quote(profile["vasp_executable"]),
    )


def _command(value, argument):
    try:
        command = shlex.split(value)
    except ValueError as exc:
        raise PBSJobError("invalid scheduler command: {}".format(exc)) from exc
    if not command:
        raise PBSJobError("scheduler command is empty")
    return command + [argument]


def _run(command, cwd, runner=None):
    if runner is not None:
        result = runner(command, cwd)
    else:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    if hasattr(result, "returncode"):
        return int(result.returncode), str(getattr(result, "stdout", "") or ""), str(getattr(result, "stderr", "") or "")
    if isinstance(result, (tuple, list)) and len(result) >= 3:
        return int(result[0]), str(result[1] or ""), str(result[2] or "")
    raise PBSJobError("scheduler runner must return CompletedProcess or (returncode, stdout, stderr)")


def _append_log(root, title, command, returncode, stdout, stderr):
    with (root / "submit.log").open("a", encoding="utf-8") as handle:
        handle.write("\n[{}] {}\n".format(_now(), title))
        handle.write("$ {}\n".format(" ".join(shlex.quote(item) for item in command)))
        handle.write("returncode: {}\n".format(returncode))
        if stdout:
            handle.write("stdout:\n{}\n".format(stdout.rstrip()))
        if stderr:
            handle.write("stderr:\n{}\n".format(stderr.rstrip()))


def _summary(text, limit=2000):
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _update_attempt(
    root, *, lifecycle=None, automation=None, scheduler=None, evidence=None,
    job_id=None, submission_authorized=None, submission_performed=None,
):
    path = root / "_fpilot" / "attempt.json"
    value = _read_json(path)
    state = value.setdefault("state", {})
    if lifecycle is not None:
        state["lifecycle"] = lifecycle
    if automation is not None:
        state["automation"] = automation
    if scheduler is not None:
        state["scheduler"] = scheduler
    if evidence is not None:
        state["scheduler_evidence"] = evidence
    if submission_authorized is not None or submission_performed is not None:
        details = value.setdefault("details", {})
        if submission_authorized is not None:
            details["submission_authorized"] = submission_authorized
        if submission_performed is not None:
            details["submission_performed"] = submission_performed
    if job_id is not None:
        value.setdefault("details", {})["job_id"] = job_id
        value["details"]["job_json_path"] = "job.json"
    _write_json(path, value)


def _ensure_script(root, script):
    path = root / "run.pbs"
    if path.exists():
        if not path.is_file() or path.read_text(encoding="utf-8") != script:
            raise PBSJobError("run.pbs already exists with different content")
    else:
        path.write_text(script, encoding="utf-8")
    return path


def dry_run(attempt, profile, runner=None):
    # ``runner`` is accepted so callers can use one common invocation shape;
    # dry-run deliberately never invokes it.
    del runner
    profile = load_profile(profile)
    root, _, _, _ = _attempt(attempt, profile)
    report = _audit(root, profile)
    script = render_script(profile)
    return {
        "schema": "fpilot.pbs-dry-run.v1",
        "status": "READY",
        "attempt_dir": str(root),
        "audit": report,
        "script_text": script,
        "commands": [
            "cd {}".format(shlex.quote(str(root))),
            " ".join(shlex.quote(item) for item in _command(profile["qsub_command"], "run.pbs")),
        ],
    }


def submit(attempt, profile, runner=None):
    profile = load_profile(profile)
    root, _, _, _ = _attempt(attempt, profile)
    report = _audit(root, profile)
    job_path = root / "job.json"
    if job_path.is_file():
        existing = _read_json(job_path)
        if existing.get("job_id"):
            return {
                "schema": JOB_SCHEMA,
                "status": existing.get("status", "SUBMITTED"),
                "idempotent": True,
                "job_id": existing["job_id"],
                "job_json_path": str(job_path.resolve()),
                "job": existing,
            }
        if existing.get("status") == "SUBMIT_UNCERTAIN":
            raise PBSJobError(
                "previous qsub result is uncertain; inspect submit.log and qstat before submitting again"
            )
    script = render_script(profile)
    script_path = _ensure_script(root, script)
    qsub = _command(profile["qsub_command"], script_path.name)
    code, stdout, stderr = _run(qsub, root, runner)
    _append_log(root, "qsub", qsub, code, stdout, stderr)
    if code != 0:
        raise PBSJobError("qsub failed with return code {}".format(code))
    token = stdout.strip()
    if not JOB_TOKEN_RE.fullmatch(token):
        receipt = {
            "schema": JOB_SCHEMA, "status": "SUBMIT_UNCERTAIN", "scheduler": "pbs",
            "attempt_dir": str(root), "script": "run.pbs", "submitted_at": _now(),
            "input_manifest_sha256": report.get("input_manifest_sha256"),
            "qsub": {"command": qsub, "returncode": code, "stdout": _summary(stdout), "stderr": _summary(stderr)},
        }
        _write_json(job_path, receipt)
        _update_attempt(
            root, automation="SUBMIT_UNCERTAIN", scheduler="UNKNOWN", evidence="UNCONFIRMED",
            submission_authorized=True, submission_performed=True,
        )
        raise PBSJobError(
            "qsub returned no single job id; inspect submit.log and qstat before submitting again"
        )
    receipt = {
        "schema": JOB_SCHEMA, "status": "SUBMITTED", "scheduler": "pbs", "job_id": token,
        "attempt_dir": str(root), "script": "run.pbs", "submitted_at": _now(),
        "input_manifest_sha256": report.get("input_manifest_sha256"),
        "qsub": {"command": qsub, "returncode": code, "stdout": _summary(stdout), "stderr": _summary(stderr)},
    }
    _write_json(job_path, receipt)
    _update_attempt(
        root, lifecycle="SUBMITTED", automation="SUBMITTED", scheduler="PENDING",
        evidence="UNCONFIRMED", job_id=token, submission_authorized=True,
        submission_performed=True,
    )
    qstat = _command(profile["qstat_command"], token)
    qcode, qstdout, qstderr = _run(qstat, root, runner)
    _append_log(root, "qstat after submit", qstat, qcode, qstdout, qstderr)
    if qcode == 0:
        state = parse_qstat_state(qstdout, token)
        if state:
            lifecycle, scheduler = state_to_status(state)
            receipt.update({"status": lifecycle, "scheduler_state": state, "qstat": {"command": qstat, "returncode": qcode, "stdout": _summary(qstdout), "stderr": _summary(qstderr)}, "checked_at": _now()})
            _update_attempt(root, lifecycle=lifecycle, scheduler=scheduler, evidence="CONFIRMED")
        else:
            receipt.update({"status": "SUBMITTED_UNVERIFIED", "qstat": {"command": qstat, "returncode": qcode, "stdout": _summary(qstdout), "stderr": _summary(qstderr)}})
            _update_attempt(root, scheduler="UNKNOWN", evidence="UNCONFIRMED")
    else:
        receipt.update({"status": "SUBMITTED_UNVERIFIED", "qstat": {"command": qstat, "returncode": qcode, "stdout": _summary(qstdout), "stderr": _summary(qstderr)}})
        _update_attempt(root, scheduler="UNKNOWN", evidence="UNCONFIRMED")
    _write_json(job_path, receipt)
    return {
        "schema": JOB_SCHEMA,
        "status": receipt["status"],
        "idempotent": False,
        "job_id": token,
        "job_json_path": str(job_path.resolve()),
        "job": receipt,
        "audit": report,
    }


def parse_qstat_state(text, job_id=None):
    match = STATE_RE.search(text)
    if match:
        state = match.group(1).upper()
        return state if state in PBS_STATES else None
    for line in text.splitlines():
        fields = line.split()
        if job_id and fields and fields[0] != job_id:
            continue
        for field in fields:
            if field.upper() in PBS_STATES:
                return field.upper()
    return None


def state_to_status(state):
    state = state.upper()
    if state == "H":
        return "QUEUED", "HELD"
    if state in {"Q", "W"}:
        return "QUEUED", "QUEUED"
    if state in {"R", "E"}:
        return "RUNNING", "RUNNING"
    if state in {"F", "C"}:
        return "TERMINAL", "COMPLETED"
    return "SUBMITTED", "UNKNOWN"


def status(attempt, profile, runner=None):
    profile = load_profile(profile)
    root = Path(attempt).expanduser().resolve(strict=False)
    job_path = root / "job.json"
    if not job_path.is_file():
        raise PBSJobError("job.json does not exist")
    receipt = _read_json(job_path)
    job_id = receipt.get("job_id")
    if not job_id:
        raise PBSJobError("job.json has no job_id")
    qstat = _command(profile["qstat_command"], job_id)
    code, stdout, stderr = _run(qstat, root, runner)
    _append_log(root, "qstat", qstat, code, stdout, stderr)
    state = parse_qstat_state(stdout, job_id) if code == 0 else None
    if state:
        lifecycle, scheduler = state_to_status(state)
        receipt.update({"status": lifecycle, "scheduler_state": state})
        _update_attempt(root, lifecycle=lifecycle, scheduler=scheduler, evidence="CONFIRMED")
    else:
        receipt["status"] = "UNKNOWN"
        _update_attempt(root, scheduler="UNKNOWN", evidence="UNCONFIRMED")
    receipt["qstat"] = {"command": qstat, "returncode": code, "stdout": _summary(stdout), "stderr": _summary(stderr)}
    receipt["checked_at"] = _now()
    _write_json(job_path, receipt)
    return {
        "schema": JOB_SCHEMA,
        "status": receipt["status"],
        "job_id": job_id,
        "job_json_path": str(job_path.resolve()),
        "job": receipt,
    }


def collect(attempt, tail_lines=20):
    root = Path(attempt).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise PBSJobError("attempt directory does not exist: {}".format(root))
    try:
        tail_lines = int(tail_lines)
    except (TypeError, ValueError) as exc:
        raise PBSJobError("tail_lines must be an integer between 1 and 200") from exc
    if not 1 <= tail_lines <= 200:
        raise PBSJobError("tail_lines must be between 1 and 200")
    summary = collect_summary(root, tail_lines)
    output = root / "_fpilot" / "output-summary.json"
    _write_json(output, summary)
    return {"schema": "fpilot.pbs-collect.v1", "status": "SUMMARY_READY", "summary": summary, "output": str(output)}


__all__ = [
    "JOB_SCHEMA", "PROFILE_SCHEMA", "PBSJobError", "collect", "dry_run", "load_profile",
    "parse_qstat_state", "render_script", "state_to_status", "status", "submit",
]
