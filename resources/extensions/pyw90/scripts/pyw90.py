#!/usr/bin/env python3
"""Small, human-runnable adapter for the external ``pyw90`` command.

The extension validates a local request, delegates to the user's installed
PyW90 command, and saves a small run receipt.  It does not copy PyW90's
algorithms, recreate VASP-generated Wannier90 files, or manage a scheduler.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path


REQUEST_SCHEMA = "fpilot.pyw90-request.v1"
COMMANDS = {"eig", "pre", "cmp"}
VERSION_RE = re.compile(
    r"(?i)(?:pyw90|version)\s*[-_:]?\s*v?([0-9]+(?:\.[0-9]+)+(?:[-+._a-z0-9]*)?)"
)


class InputError(ValueError):
    """A malformed request or missing local input."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _resolve(base, value, field):
    if not isinstance(value, str) or not value.strip():
        raise InputError("{} must be a non-empty path".format(field))
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _executable_argv(value, base=None):
    if value is None:
        items = ["pyw90"]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    elif isinstance(value, list) and value and all(
        isinstance(item, str) and item.strip() for item in value
    ):
        items = list(value)
    else:
        raise InputError("executable must be a command string or non-empty argv list")
    head = items[0]
    if base is not None and ("/" in head or head.startswith(".")):
        path = Path(head).expanduser()
        items[0] = str(path.resolve() if path.is_absolute() else (Path(base) / path).resolve())
    return items


def _path_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InputError("{} must be a list of non-empty paths".format(field))
    return [item.strip() for item in value]


def _file_info(path):
    result = {
        "path": str(path),
        "exists": path.is_file(),
        "size": path.stat().st_size if path.is_file() else 0,
    }
    if result["exists"]:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result["sha256"] = digest.hexdigest()
    else:
        result["sha256"] = None
    return result


def _short_text(value, limit=500):
    value = (value or "").strip()
    return value if len(value) <= limit else value[-limit:]


def check_request(request, request_dir=None):
    """Validate a request and return its resolved, normalized form.

    Relative ``input_dir``/``output_dir`` values are resolved from the
    directory containing the request file.  ``expected_inputs`` and
    ``expected_outputs`` are relative to the resolved input directory because
    PyW90 runs with that directory as its working directory.
    """

    if not isinstance(request, dict):
        raise InputError("request must be a JSON object")
    if request.get("schema") != REQUEST_SCHEMA:
        raise InputError("schema must be {}".format(REQUEST_SCHEMA))

    base = Path(request_dir or Path.cwd()).expanduser().resolve()
    input_dir = _resolve(base, request.get("input_dir"), "input_dir")
    if not input_dir.is_dir():
        raise InputError("input_dir does not exist: {}".format(input_dir))

    command = request.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        raise InputError("command must be a non-empty argv list")
    command = [item.strip() for item in command]
    if command[0] not in COMMANDS:
        raise InputError(
            "unsupported command {!r}; use eig, pre, or cmp (auto is not supported)".format(
                command[0]
            )
        )

    expected_inputs = _path_list(request.get("expected_inputs", []), "expected_inputs")
    input_files = []
    for name in expected_inputs:
        path = _resolve(input_dir, name, "expected_inputs item")
        info = _file_info(path)
        if not info["exists"] or info["size"] == 0:
            raise InputError("missing or empty expected input: {}".format(path))
        input_files.append(info)

    output_dir = _resolve(base, request.get("output_dir", "pyw90_results"), "output_dir")
    expected_outputs = _path_list(request.get("expected_outputs", []), "expected_outputs")
    expected_output_base = request.get("expected_output_base", "input_dir")
    if expected_output_base not in {"input_dir", "output_dir"}:
        raise InputError("expected_output_base must be input_dir or output_dir")
    output_base = input_dir if expected_output_base == "input_dir" else output_dir
    output_paths = [_resolve(output_base, name, "expected_outputs item") for name in expected_outputs]
    executable = _executable_argv(request.get("executable"), base=base)
    argv = executable + command
    return {
        "status": "VALID",
        "schema": REQUEST_SCHEMA,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "executable": executable,
        "command": command,
        "argv": argv,
        "expected_inputs": input_files,
        "expected_outputs": [_file_info(path) for path in output_paths],
        "expected_output_base": expected_output_base,
        "executable_resolution": "request-relative" if "/" in executable[0] else "PATH",
    }


def _process_env():
    """Return the child environment with a writable matplotlib cache."""

    environment = os.environ.copy()
    if "MPLCONFIGDIR" not in environment:
        cache_dir = Path(tempfile.gettempdir()) / "fpilot-matplotlib-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        environment["MPLCONFIGDIR"] = str(cache_dir)
    return environment


def _python_package_version():
    """Best-effort version lookup in the interpreter running this adapter."""

    try:
        return metadata.version("pyw90")
    except metadata.PackageNotFoundError:
        return None


def doctor(executable="pyw90"):
    """Report whether the external command can be started.

    Version detection is best effort.  A command that starts but does not
    expose a version flag is still useful and is reported with an unknown
    version.
    """

    argv = _executable_argv(executable, base=Path.cwd())
    probes = []
    probe_outputs = []
    probe_errors = []
    launched = False
    for flag in ("--version", "--help"):
        try:
            completed = subprocess.run(
                argv + [flag],
                capture_output=True,
                text=True,
                check=False,
                env=_process_env(),
            )
            launched = True
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            probes.append({"argv": argv + [flag], "returncode": completed.returncode})
            probe_outputs.append(output)
            if completed.returncode == 0:
                break
        except OSError as exc:
            probes.append({"argv": argv + [flag], "returncode": None})
            probe_errors.append(str(exc))
            break
    combined = "\n".join(probe_outputs)
    match = VERSION_RE.search(combined)
    version_source = "command" if match else None
    version = match.group(1) if match else None
    callable_ = any(item.get("returncode") == 0 for item in probes)
    if version is None and callable_:
        version = _python_package_version()
        version_source = "python-package" if version else None
    result = {
        "status": "OK" if callable_ else "ERROR",
        "executable": argv,
        "callable": callable_,
        "started": launched,
        "version": version,
        "version_source": version_source,
        "version_recognized": version is not None,
        "probes": probes,
    }
    if not callable_:
        summary = next((text for text in reversed(probe_outputs) if text.strip()), None)
        summary = summary or (probe_errors[-1] if probe_errors else "all pyw90 probes failed")
        result["error"] = _short_text(summary)
    return result


def run_request(request, request_dir=None):
    """Run the selected local PyW90 command and save its receipt."""

    checked = check_request(request, request_dir=request_dir)
    input_dir = Path(checked["input_dir"])
    output_dir = Path(checked["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            checked["argv"],
            cwd=str(input_dir),
            capture_output=True,
            text=True,
            check=False,
            env=_process_env(),
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = str(exc)

    expected_outputs = [Path(item["path"]) for item in checked["expected_outputs"]]
    output_info = [_file_info(path) for path in expected_outputs]
    outputs_ready = all(item["exists"] and item["size"] > 0 for item in output_info)
    success = returncode == 0 and outputs_ready
    stdout_path = output_dir / "pyw90_stdout.txt"
    stderr_path = output_dir / "pyw90_stderr.txt"
    version_match = VERSION_RE.search((stdout or "") + "\n" + (stderr or ""))
    pyw90_version = version_match.group(1) if version_match else _python_package_version()
    receipt = {
        "status": "OK" if success else "ERROR",
        "schema": REQUEST_SCHEMA,
        "argv": checked["argv"],
        "cwd": str(input_dir),
        "returncode": returncode,
        "output_dir": str(output_dir),
        "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
        "expected_inputs": checked["expected_inputs"],
        "expected_outputs": output_info,
        "expected_output_base": checked["expected_output_base"],
        "pyw90_version": pyw90_version,
        "runtime": {
            "python": sys.version.split()[0],
            "MPLCONFIGDIR": _process_env().get("MPLCONFIGDIR"),
            "PYTHONPYCACHEPREFIX": os.environ.get("PYTHONPYCACHEPREFIX"),
        },
    }
    if not success:
        if returncode is None:
            receipt["error"] = _short_text(stderr) or "pyw90 could not be started"
        elif returncode != 0:
            receipt["error"] = _short_text(stderr or stdout) or "pyw90 returned {}".format(returncode)
        else:
            receipt["error"] = "run did not produce all non-empty expected_outputs"

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    (output_dir / "pyw90_run.json").write_text(_json(receipt) + "\n", encoding="utf-8")
    return receipt


def _load_request(path):
    path = Path(path).expanduser().resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    return request, path.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check and run the external PyW90 command")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--executable", default="pyw90")
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--request", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.executable)
        else:
            request, request_dir = _load_request(args.request)
            if args.command == "check":
                result = check_request(request, request_dir=request_dir)
            else:
                result = run_request(request, request_dir=request_dir)
    except (InputError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "ERROR", "errors": [str(exc)]}
    print(_json(result))
    return 0 if result.get("status") in {"OK", "VALID"} else 1


if __name__ == "__main__":
    sys.exit(main())
