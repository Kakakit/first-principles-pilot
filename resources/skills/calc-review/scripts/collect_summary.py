#!/usr/bin/env python3
"""Collect a bounded server-side summary from a local VASP calc directory.

Only metadata, selected terminal markers, and short log tails leave this
process.  The implementation streams OUTCAR and never returns its contents.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import deque
from pathlib import Path


SUMMARY_FILES = (
    "INCAR", "POSCAR", "KPOINTS", "POTCAR", "CONTCAR", "OSZICAR", "OUTCAR",
    "vasp.err", "vasp.out", "CHGCAR", "WAVECAR", "vasprun.xml", "PROCAR", "DOSCAR",
)
POLARIZATION_KEY_RE = re.compile(r"p\[(ion|elc)\]\s*=", re.I)
NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
FATAL_RE = re.compile(r"VERY\s+BAD\s+NEWS|\bFATAL\b|SEGMENTATION\s+FAULT|MPI[_ -]?ABORT", re.I)


class SummaryError(Exception):
    """A user-facing summary collection error."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_canonical(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _file_info(calc_dir, name):
    path = calc_dir / name
    if not path.exists():
        return {"exists": False, "size_bytes": None}
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"exists": True, "size_bytes": None, "error": str(exc)}
    return {"exists": path.is_file(), "size_bytes": size}


def _tail(path, limit):
    lines = deque(maxlen=limit)
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
    except OSError as exc:
        return {"exists": False, "lines": [], "error": str(exc)}
    return {"exists": True, "lines": list(lines)}


def _scan_outcar(path):
    result = {
        "exists": False,
        "line_count": 0,
        "terminated": False,
        "timing_footer_seen": False,
        "converged": False,
        "final_electronic_converged": None,
        "last_electronic_event_line": None,
        "ionic_converged": False,
        "fatal_markers": [],
        "polarization": {},
    }
    if not Path(path).is_file():
        return result
    result["exists"] = True
    fatal = set()
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                result["line_count"] += 1
                if re.search(r"General timing and accounting", line, re.I):
                    result["terminated"] = True
                    result["timing_footer_seen"] = True
                if re.search(r"EDIFF\s+was\s+not\s+reached", line, re.I):
                    result["final_electronic_converged"] = False
                    result["last_electronic_event_line"] = result["line_count"]
                elif re.search(r"EDIFF\s+is\s+reached", line, re.I):
                    result["final_electronic_converged"] = True
                    result["last_electronic_event_line"] = result["line_count"]
                if re.search(r"reached\s+required\s+accuracy", line, re.I):
                    result["ionic_converged"] = True
                marker = FATAL_RE.search(line)
                if marker:
                    fatal.add(marker.group(0).upper())
                match = POLARIZATION_KEY_RE.search(line)
                if match:
                    tail = line[match.end():]
                    values = NUMBER_RE.findall(tail)
                    if values:
                        result["polarization"][match.group(1).lower()] = values[0] if len(values) == 1 else values[:3]
    except OSError as exc:
        result["error"] = str(exc)
    result["converged"] = result["final_electronic_converged"] is True
    result["fatal_markers"] = sorted(fatal)
    return result


def collect_summary(calc_dir, tail_lines=20):
    calc_dir = Path(calc_dir).expanduser().resolve(strict=False)
    if not calc_dir.is_dir():
        raise SummaryError("calc directory does not exist: {}".format(calc_dir))
    try:
        tail_lines = int(tail_lines)
    except (TypeError, ValueError) as exc:
        raise SummaryError("tail_lines must be an integer") from exc
    if not 1 <= tail_lines <= 200:
        raise SummaryError("tail_lines must be between 1 and 200")
    files = {name: _file_info(calc_dir, name) for name in SUMMARY_FILES}
    outcar = _scan_outcar(calc_dir / "OUTCAR")
    oszicar_tail = _tail(calc_dir / "OSZICAR", tail_lines) if files["OSZICAR"]["exists"] else {"exists": False, "lines": []}
    err_tail = _tail(calc_dir / "vasp.err", tail_lines) if files["vasp.err"]["exists"] else {"exists": False, "lines": []}
    return {
        "schema": "fpilot.server-summary.v1",
        "status": "SUMMARY_READY",
        "calc_dir": str(calc_dir),
        "files": files,
        "outcar": outcar,
        "oszicar_tail": oszicar_tail,
        "vasp_err_tail": err_tail,
        "limits": {"tail_lines": tail_lines, "full_files_returned": False},
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Collect a bounded VASP summary without returning large files.")
    parser.add_argument("calc_dir", nargs="?", help="local calc directory")
    parser.add_argument("--calc-dir", dest="calc_dir_opt")
    parser.add_argument("--tail-lines", type=int, default=20)
    parser.add_argument("--output", help="write JSON to this path")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.calc_dir and args.calc_dir_opt:
            raise SummaryError("provide calc directory once")
        calc_dir = args.calc_dir_opt or args.calc_dir
        if not calc_dir:
            raise SummaryError("calc directory is required")
        result = collect_summary(calc_dir, args.tail_lines)
        if args.output:
            _atomic_write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (SummaryError, OSError) as exc:
        print("collect_summary: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
