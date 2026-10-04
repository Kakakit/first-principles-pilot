#!/usr/bin/env python3
"""Small, human-runnable TB2J adapter for Wannier90 outputs.

The adapter checks the files that TB2J needs and then delegates the numerical
work to the user's ``wann2J.py`` executable.  It deliberately does not parse
or interpret exchange parameters.
"""

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path


VERSION_RE = re.compile(
    r"(?i)(?:tb2j|version)\s*[-_:]?\s*v?([0-9]+(?:\.[0-9]+)+(?:[-+._a-z0-9]*)?)"
)


class InputError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("{} must be a finite number".format(field))
    if not math.isfinite(float(value)):
        raise InputError("{} must be a finite number".format(field))
    return value


def _positive_ints(value, field, count=None):
    if not isinstance(value, list) or (count is not None and len(value) != count):
        expected = "a list of {} integers".format(count) if count else "a list of integers"
        raise InputError("{} must be {}".format(field, expected))
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise InputError("{} must contain positive integers".format(field))
        result.append(item)
    return result


def _strings(value, field):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise InputError("{} must be a non-empty list of strings".format(field))
    return [item.strip() for item in value]


def _resolve(input_dir, value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (input_dir / path).resolve()


def _read_hr_num_wann(path):
    try:
        lines = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    lines.append(line)
                if len(lines) == 2:
                    break
    except OSError as exc:
        raise InputError("cannot read {}: {}".format(path.name, exc)) from exc
    if len(lines) < 2:
        raise InputError("{} is empty or has no num_wann line".format(path.name))
    try:
        num_wann = int(lines[1].split()[0])
    except (IndexError, ValueError) as exc:
        raise InputError("{} has an invalid num_wann value".format(path.name)) from exc
    if num_wann <= 0:
        raise InputError("{} num_wann must be positive".format(path.name))
    return num_wann


def _win_metadata(path):
    metadata = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError("cannot read {}: {}".format(path.name, exc)) from exc
    for line in text.splitlines():
        match = re.match(r"^\s*(num_wann|spinors)\s*=\s*(.*?)\s*(?:!.*)?$", line, re.I)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "num_wann":
            try:
                metadata[key] = int(value.split()[0])
            except (IndexError, ValueError) as exc:
                raise InputError("{} has an invalid num_wann value".format(path.name)) from exc
        else:
            metadata[key] = value.lower() in {"true", ".true.", "1", "yes"}
    return metadata


def _check_win(path, mode, expected_num_wann):
    if not path.is_file():
        return []
    metadata = _win_metadata(path)
    errors = []
    if "num_wann" in metadata and expected_num_wann is not None:
        if metadata["num_wann"] != expected_num_wann:
            errors.append(
                "{} num_wann={} does not match HR num_wann={}".format(
                    path.name, metadata["num_wann"], expected_num_wann
                )
            )
    if "spinors" in metadata:
        expected_spinors = mode == "spinor"
        if metadata["spinors"] != expected_spinors:
            errors.append(
                "{} spinors={} does not match mode={}".format(
                    path.name, metadata["spinors"], mode
                )
            )
    return errors


def _file_info(path):
    return {"path": str(path), "exists": path.is_file(), "size": path.stat().st_size if path.is_file() else 0}


def _wannier_bundle(input_dir, prefix, label):
    hr = _resolve(input_dir, prefix + "_hr.dat")
    centres = _resolve(input_dir, prefix + "_centres.xyz")
    tb = _resolve(input_dir, prefix + "_tb.dat")
    hr_ready = (
        hr.is_file() and hr.stat().st_size > 0
        and centres.is_file() and centres.stat().st_size > 0
    )
    tb_ready = tb.is_file() and tb.stat().st_size > 0
    if not hr_ready and not tb_ready:
        raise InputError(
            "{} input needs {}_hr.dat plus {}_centres.xyz, or {}_tb.dat".format(
                label, prefix, prefix, prefix
            )
        )
    return {
        "prefix": prefix,
        "hr": _file_info(hr),
        "centres": _file_info(centres),
        "tb": _file_info(tb),
        "num_wann": _read_hr_num_wann(hr) if hr_ready else None,
    }


def _positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InputError("{} must be a positive integer".format(field))
    return value


def _optional_run_options(request, mode):
    options = {}
    for field in ("emin_ev", "emax_ev"):
        if field in request:
            options[field] = _number(request[field], field)
    if "emin_ev" in options and "emax_ev" in options:
        if options["emin_ev"] >= options["emax_ev"]:
            raise InputError("emin_ev must be smaller than emax_ev")
    if "rcut_angstrom" in request:
        options["rcut_angstrom"] = _number(request["rcut_angstrom"], "rcut_angstrom")
        if options["rcut_angstrom"] <= 0:
            raise InputError("rcut_angstrom must be positive")
    for field in ("nz", "nproc"):
        if field in request:
            options[field] = _positive_int(request[field], field)
    if "orb_decomposition" in request:
        if not isinstance(request["orb_decomposition"], bool):
            raise InputError("orb_decomposition must be true or false")
        if request["orb_decomposition"] and mode != "spinor":
            raise InputError("orb_decomposition is supported only for spinor mode")
        options["orb_decomposition"] = request["orb_decomposition"]
    if "description" in request:
        if not isinstance(request["description"], str) or not request["description"].strip():
            raise InputError("description must be a non-empty string")
        options["description"] = request["description"].strip()
    return options


def check_request(request):
    if not isinstance(request, dict):
        raise InputError("request must be a JSON object")
    if request.get("schema") not in (None, "fpilot.tb2j-request.v1"):
        raise InputError("schema must be fpilot.tb2j-request.v1")
    input_value = request.get("input_dir", request.get("parent"))
    if not isinstance(input_value, str) or not input_value.strip():
        raise InputError("input_dir must be a directory path")
    input_dir = Path(input_value).expanduser().resolve()
    if not input_dir.is_dir():
        raise InputError("input_dir does not exist: {}".format(input_dir))

    poscar_value = request.get("poscar", "POSCAR")
    if not isinstance(poscar_value, str) or not poscar_value.strip():
        raise InputError("poscar must be a file name or path")
    poscar = _resolve(input_dir, poscar_value)
    if not poscar.is_file() or poscar.stat().st_size == 0:
        raise InputError("missing or empty POSCAR: {}".format(poscar))

    mode = request.get("mode")
    if mode is None and "spinor" in request:
        mode = "spinor" if request["spinor"] else "collinear"
    if mode is None:
        mode = "spinor"
    if mode not in {"spinor", "collinear"}:
        raise InputError("mode must be spinor or collinear")
    groupby = request.get("groupby", "spin")
    if mode == "spinor" and groupby not in {"spin", "orbital"}:
        raise InputError("groupby must be spin or orbital")
    efermi = _number(request.get("efermi_ev"), "efermi_ev")
    kmesh = _positive_ints(request.get("kmesh"), "kmesh", 3)

    elements = request.get("elements")
    indices = request.get("index_magnetic_atoms")
    if elements is None and indices is None:
        raise InputError("provide elements or index_magnetic_atoms")
    if elements is not None:
        elements = _strings(elements, "elements")
    if indices is not None:
        indices = _positive_ints(indices, "index_magnetic_atoms")

    prefix = request.get("prefix", "wannier90")
    if not isinstance(prefix, str) or not prefix.strip():
        raise InputError("prefix must be a non-empty string")
    prefix = prefix.strip()

    hr_paths = []
    bundles = []
    prefixes = {"prefix": prefix}
    if mode == "spinor":
        bundle = _wannier_bundle(input_dir, prefix, "spinor")
        bundles.append(bundle)
        if bundle["hr"]["exists"] and bundle["centres"]["exists"]:
            hr_paths.append(Path(bundle["hr"]["path"]))
        num_wann = bundle["num_wann"]
        win_checks = [(_resolve(input_dir, prefix + ".win"), num_wann)]
    else:
        up = request.get("prefix_up", request.get("up_prefix", prefix + "_up"))
        down = request.get("prefix_down", request.get("down_prefix", prefix + "_down"))
        if not isinstance(up, str) or not up.strip() or not isinstance(down, str) or not down.strip():
            raise InputError("prefix_up and prefix_down must be non-empty strings")
        up, down = up.strip(), down.strip()
        prefixes.update({"up": up, "down": down})
        up_bundle = _wannier_bundle(input_dir, up, "collinear up")
        down_bundle = _wannier_bundle(input_dir, down, "collinear down")
        bundles.extend([up_bundle, down_bundle])
        for bundle in bundles:
            if bundle["hr"]["exists"] and bundle["centres"]["exists"]:
                hr_paths.append(Path(bundle["hr"]["path"]))
        up_num = up_bundle["num_wann"]
        down_num = down_bundle["num_wann"]
        if up_num is not None and down_num is not None and up_num != down_num:
            raise InputError("collinear HR num_wann mismatch: up={} down={}".format(up_num, down_num))
        num_wann = up_num if up_num == down_num else None
        win_checks = [
            (_resolve(input_dir, up + ".win"), up_num),
            (_resolve(input_dir, down + ".win"), down_num),
            (_resolve(input_dir, prefix + ".win"), num_wann),
        ]

    errors = []
    unique_win_checks = []
    seen_win_paths = set()
    for win_path, expected_num_wann in win_checks:
        if win_path in seen_win_paths:
            continue
        seen_win_paths.add(win_path)
        unique_win_checks.append((win_path, expected_num_wann))
        errors.extend(_check_win(win_path, mode, expected_num_wann))
    if errors:
        raise InputError("; ".join(errors))

    run_options = _optional_run_options(request, mode)

    result = {
        "status": "VALID",
        "mode": mode,
        "input_dir": str(input_dir),
        "poscar": _file_info(poscar),
        "prefixes": prefixes,
        "wannier_bundles": bundles,
        "hr_files": [_file_info(path) for path in hr_paths],
        "num_wann": num_wann,
        "efermi_ev": efermi,
        "kmesh": kmesh,
        "elements": elements,
        "index_magnetic_atoms": indices,
        "run_options": run_options,
    }
    result["win_files"] = [
        _file_info(path) for path, _ in unique_win_checks if path.is_file()
    ]
    return result


def _executable_argv(value):
    if value is None:
        return ["wann2J.py"]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return list(value)
    raise InputError("executable must be a command string or non-empty argv list")


def build_argv(request, checked, output_dir):
    argv = _executable_argv(request.get("executable"))
    argv.extend(["--path", checked["input_dir"]])
    argv.extend(["--posfile", checked["poscar"]["path"], "--efermi", str(checked["efermi_ev"])])
    argv.extend(["--kmesh"] + [str(value) for value in checked["kmesh"]])
    if checked["elements"]:
        argv.extend(["--elements"] + checked["elements"])
    if checked["index_magnetic_atoms"]:
        argv.extend(["--index_magnetic_atoms"] + [str(value) for value in checked["index_magnetic_atoms"]])
    prefixes = checked["prefixes"]
    if checked["mode"] == "spinor":
        argv.extend(["--spinor", "--groupby", request.get("groupby", "spin"), "--prefix_spinor", prefixes["prefix"]])
    else:
        argv.extend(["--prefix_up", prefixes["up"], "--prefix_down", prefixes["down"]])
    options = checked["run_options"]
    option_flags = {
        "emin_ev": "--emin",
        "emax_ev": "--emax",
        "rcut_angstrom": "--rcut",
        "nz": "--nz",
        "nproc": "--np",
        "description": "--description",
    }
    for field, flag in option_flags.items():
        if field in options:
            argv.extend([flag, str(options[field])])
    if options.get("orb_decomposition"):
        argv.append("--orb_decomposition")
    argv.extend(["--output_path", str(output_dir)])
    return argv


def doctor(executable):
    argv = _executable_argv(executable)
    attempts = []
    launched = False
    for flag in ("--version", "--help"):
        try:
            completed = subprocess.run(argv + [flag], capture_output=True, text=True, check=False)
            launched = True
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            attempts.append({"argv": argv + [flag], "returncode": completed.returncode, "output": output})
            if completed.returncode == 0:
                break
        except OSError as exc:
            attempts.append({"argv": argv + [flag], "error": str(exc)})
            break
    combined = "\n".join(item.get("output", "") for item in attempts)
    match = VERSION_RE.search(combined)
    return {
        "status": "OK" if launched else "ERROR",
        "executable": argv,
        "callable": launched,
        "version": match.group(1) if match else None,
        "version_recognized": bool(match),
        "probes": attempts,
    }


def _output_dir(request, override, input_dir):
    value = override if override is not None else request.get("output_dir", "TB2J_results")
    if not isinstance(value, str) or not value.strip():
        raise InputError("output_dir must be a directory path")
    return _resolve(input_dir, value)


def run_request(request, output_override=None):
    checked = check_request(request)
    input_dir = Path(checked["input_dir"])
    output_dir = _output_dir(request, output_override, input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    argv = build_argv(request, checked, output_dir)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(input_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = str(exc)

    exchange = output_dir / "exchange.out"
    receipt = {
        "status": "OK" if returncode == 0 and exchange.is_file() and exchange.stat().st_size > 0 else "ERROR",
        "argv": argv,
        "cwd": str(input_dir),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "output_dir": str(output_dir),
        "exchange_out": _file_info(exchange),
    }
    if receipt["status"] != "OK":
        if returncode is None:
            receipt["error"] = stderr or "wann2J.py could not be started"
        elif returncode != 0:
            receipt["error"] = "wann2J.py returned {}".format(returncode)
        else:
            receipt["error"] = "successful wann2J.py run did not produce a non-empty exchange.out"
    (output_dir / "tb2j_stdout.txt").write_text(stdout, encoding="utf-8")
    (output_dir / "tb2j_stderr.txt").write_text(stderr, encoding="utf-8")
    (output_dir / "tb2j_run.json").write_text(_json(receipt) + "\n", encoding="utf-8")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check and run TB2J on Wannier90 files")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--executable", default="wann2J.py")
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--request", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--request", required=True)
    run_parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.executable)
        else:
            request_path = Path(args.request).expanduser().resolve()
            request = json.loads(request_path.read_text(encoding="utf-8"))
            input_value = request.get("input_dir", request.get("parent"))
            if isinstance(input_value, str) and not Path(input_value).expanduser().is_absolute():
                request["input_dir"] = str((request_path.parent / input_value).resolve())
            if args.command == "check":
                result = check_request(request)
            else:
                result = run_request(request, args.output_dir)
    except (InputError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "INVALID", "errors": [str(exc)]}
    print(_json(result))
    return 0 if result.get("status") in {"OK", "VALID"} else 1


if __name__ == "__main__":
    sys.exit(main())
