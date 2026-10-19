#!/usr/bin/env python3
"""Bounded, resumable Wannier90 energy-window search for the PyW90 extension.

The search is deliberately a small local workflow around an immutable
``.win`` template.  ``check`` audits inputs, ``plan`` creates independent
candidate directories, ``run`` optionally executes one explicit local argv
for pending candidates, ``score`` evaluates completed band files with the
same bounded metrics as :mod:`pyw90_compare`, and ``refine`` adds one bounded
neighbourhood around the currently best candidate only after a human marks a
spin as ``poor``.

This module never edits the production template, submits to a scheduler,
uses SSH, invokes ``pyw90 auto``, or loops until a quality target is reached.
Every search is finite and can be resumed from ``window_scan_manifest.json``.
"""

from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "fpilot.pyw90-window-scan-request.v1"
MANIFEST_SCHEMA = "fpilot.pyw90-window-scan-manifest.v1"
SCORE_SCHEMA = "fpilot.pyw90-window-scan-score.v1"
WINDOW_KEYS = ("dis_win_min", "dis_win_max", "dis_froz_min", "dis_froz_max")
MAX_CANDIDATES = 64
MAX_REFINEMENT_ROUNDS = 3
MAX_REFINEMENT_CANDIDATES = 16
MAX_WORKERS = 8
MAX_TIMEOUT_SECONDS = 24 * 60 * 60
WOUT_WINDOW_TOLERANCE_EV = 1.0e-5
FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_WINDOW_RE = re.compile(rf"(?im)^\s*(dis_win_min|dis_win_max|dis_froz_min|dis_froz_max)\s*[=:]\s*({FLOAT_RE})")
_FORBIDDEN_EXECUTABLES = {
    "ssh",
    "scp",
    "sftp",
    "sbatch",
    "salloc",
    "srun",
    "qsub",
    "qstat",
    "qdel",
    "bsub",
    "bjobs",
    "bkill",
    "bsub",
    "oarsub",
    "pjsub",
}
_SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "csh", "tcsh", "cmd", "powershell", "pwsh"}


class InputError(ValueError):
    """Malformed, unsafe, or scientifically inconsistent scan input."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _resolve(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty path")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path, field: str) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise InputError(f"missing or empty {field}: {path}")
    return {"path": str(path), "size": path.stat().st_size, "sha256": _sha256(path)}


def _float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise InputError(f"{field} must be a finite number")
    return result


def _positive_int(value: object, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InputError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise InputError(f"{field} exceeds hard limit {maximum}")
    return value


def _nonnegative_int(value: object, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(f"{field} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise InputError(f"{field} exceeds hard limit {maximum}")
    return value


def _number_pair(value: object, field: str) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        raise InputError(f"{field} must be a two-number array")
    result = (float(value[0]), float(value[1]))
    if not all(math.isfinite(item) for item in result) or result[0] >= result[1]:
        raise InputError(f"{field} must contain finite increasing values")
    return result


def _windows_from_text(text: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {key: None for key in WINDOW_KEYS}
    for match in _WINDOW_RE.finditer(text):
        result[match.group(1).lower()] = float(match.group(2).replace("D", "E").replace("d", "e"))
    return result


def _parse_num_wann(text: str) -> int | None:
    match = re.search(r"(?im)^\s*num_wann\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _update_windows(text: str, windows_absolute: dict[str, float]) -> str:
    """Return a new template text with only the four window scalars changed."""

    lines = text.splitlines()
    for key in WINDOW_KEYS:
        value = windows_absolute[key]
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*[=:].*$", re.IGNORECASE)
        replacement = f"{key} = {value:.10g}"
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                comment = ""
                if "#" in line:
                    comment = "  #" + line.split("#", 1)[1]
                lines[index] = match.group(1) + replacement + comment
                break
        else:
            lines.insert(0, replacement)
    return "\n".join(lines) + "\n"


def _read_band_blocks(path: Path) -> tuple[list[float], list[list[float]]]:
    """Read a two-column, blank-line-separated band file without NumPy."""

    blocks: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        fields = raw.replace("D", "E").replace("d", "e").split()
        if not fields:
            if current:
                blocks.append(current)
                current = []
            continue
        if fields[0].startswith(("#", "!")):
            continue
        if len(fields) < 2:
            raise InputError(f"{path}:{line_number}: expected two numeric columns")
        try:
            current.append((float(fields[0]), float(fields[1])))
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: non-numeric band row") from exc
    if current:
        blocks.append(current)
    if not blocks or len(blocks[0]) < 2:
        raise InputError(f"{path}: no usable band blocks")
    npoints = len(blocks[0])
    x = [row[0] for row in blocks[0]]
    energies: list[list[float]] = [[0.0 for _ in blocks] for _ in range(npoints)]
    for band_index, block in enumerate(blocks):
        if len(block) != npoints:
            raise InputError(f"{path}: inconsistent point count in band {band_index + 1}")
        for point_index, (point_x, energy) in enumerate(block):
            if abs(point_x - x[point_index]) > 2.0e-7:
                raise InputError(f"{path}: abscissa differs between band blocks")
            energies[point_index][band_index] = energy
    return x, energies


def _read_eig(path: Path) -> list[list[float]]:
    """Read a seedname ``.eig`` file as ``k-point -> band energies``.

    PyW90's ``eig`` input is a three-column table (band index, k-point index,
    energy).  These are the regular-grid eigenvalues used by its
    ``count_states_least/most`` and ``suggest_win_*`` routines.  It is
    intentionally separate from the line-path ``dft_band`` file used later
    for fit scoring.
    """

    rows: list[tuple[int, int, float]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        fields = raw.replace("D", "E").replace("d", "e").split()
        if not fields or fields[0].startswith(("#", "!")):
            continue
        if len(fields) < 3:
            raise InputError(f"{path}:{line_number}: .eig requires band, k-point, and energy columns")
        try:
            band = int(fields[0])
            kpoint = int(fields[1])
            energy = float(fields[2])
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: invalid .eig row") from exc
        if band <= 0 or kpoint <= 0 or not math.isfinite(energy):
            raise InputError(f"{path}:{line_number}: .eig indices must be positive and energy finite")
        rows.append((band, kpoint, energy))
    if not rows:
        raise InputError(f"{path}: no .eig rows")
    bands = max(row[0] for row in rows)
    kpoints = max(row[1] for row in rows)
    table: list[list[float | None]] = [[None for _ in range(bands)] for _ in range(kpoints)]
    for band, kpoint, energy in rows:
        if table[kpoint - 1][band - 1] is not None:
            raise InputError(f"{path}: duplicate band/k-point row ({band}, {kpoint})")
        table[kpoint - 1][band - 1] = energy
    if any(value is None for row in table for value in row):
        raise InputError(f"{path}: incomplete band/k-point grid")
    return [[float(value) for value in row] for row in table]


def _count_segments(x: list[float]) -> list[tuple[int, int]]:
    resets = [index for index in range(1, len(x)) if x[index] <= x[index - 1] + 2.0e-7]
    starts = [0, *resets]
    ends = [index - 1 for index in resets] + [len(x) - 1]
    segments = list(zip(starts, ends))
    if any(start >= end for start, end in segments):
        raise InputError(f"invalid zero-length band path segment: {segments}")
    return segments


def _state_counts(energies: list[list[float]], window: tuple[float, float]) -> tuple[int, int]:
    """Return the exact minimum and maximum per-k state counts."""

    lower, upper = window
    per_k = [sum(lower <= energy <= upper for energy in row) for row in energies]
    return min(per_k), max(per_k)


def _window_valid(
    windows: dict[str, float],
    energies: list[list[float]],
    expected_num_wann: int,
    target_window: tuple[float, float] | None = None,
    min_disentanglement_headroom: int = 0,
    max_outer_excess_states: int | None = None,
) -> tuple[bool, dict[str, int]]:
    outer = (windows["dis_win_min"], windows["dis_win_max"])
    frozen = (windows["dis_froz_min"], windows["dis_froz_max"])
    if not (outer[0] <= frozen[0] < frozen[1] <= outer[1]):
        return False, {"outer_least": 0, "frozen_most": expected_num_wann + 1}
    outer_least, outer_most = _state_counts(energies, outer)
    frozen_least, frozen_most = _state_counts(energies, frozen)
    counts = {
        "outer_least": outer_least,
        "outer_most": outer_most,
        "frozen_least": frozen_least,
        "frozen_most": frozen_most,
        "disentanglement_headroom_min": expected_num_wann - frozen_most,
    }
    target_covered = True
    if target_window is not None:
        target_covered = bool(
            outer[0] <= target_window[0]
            and target_window[1] <= outer[1]
        )
        counts["target_covered_by_outer"] = int(target_covered)
        # Retain the old key in v1 receipts, but its meaning is now explicitly
        # outer coverage. Projector support is not a frozen-window mandate.
        counts["target_covered"] = int(target_covered)
    outer_ceiling_ok = (
        max_outer_excess_states is None
        or outer_most <= expected_num_wann + max_outer_excess_states
    )
    return (
        outer_least >= expected_num_wann
        and frozen_most <= expected_num_wann - min_disentanglement_headroom
        and outer_ceiling_ok
        and target_covered
    ), counts


def _read_amn_projectability(path: Path, energies: list[list[float]]) -> list[list[float]]:
    """Read total trial-orbital projectability from a Wannier90 ``.amn`` file."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        raise InputError(f"{path}: incomplete .amn file")
    try:
        nbands, nkpoints, nproj = (int(value) for value in lines[1].split()[:3])
    except (ValueError, IndexError) as exc:
        raise InputError(f"{path}: invalid .amn dimensions") from exc
    if nkpoints != len(energies) or nbands != len(energies[0]):
        raise InputError("projectability .amn dimensions differ from source.eig")
    values = [[0.0 for _ in range(nbands)] for _ in range(nkpoints)]
    seen: set[tuple[int, int, int]] = set()
    for line_number, raw in enumerate(lines[2:], 3):
        fields = raw.replace("D", "E").replace("d", "e").split()
        if not fields:
            continue
        if len(fields) < 5:
            raise InputError(f"{path}:{line_number}: invalid .amn row")
        try:
            band, projection, kpoint = (int(fields[index]) for index in range(3))
            real, imag = float(fields[3]), float(fields[4])
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: invalid .amn value") from exc
        key = (band, projection, kpoint)
        if key in seen:
            raise InputError(f"{path}:{line_number}: duplicate .amn row")
        if not (1 <= band <= nbands and 1 <= projection <= nproj and 1 <= kpoint <= nkpoints):
            raise InputError(f"{path}:{line_number}: .amn index exceeds declared dimensions")
        seen.add(key)
        values[kpoint - 1][band - 1] += real * real + imag * imag
    expected_rows = nbands * nkpoints * nproj
    if len(seen) != expected_rows:
        raise InputError(f"{path}: incomplete .amn matrix ({len(seen)}/{expected_rows} rows)")
    return values


def _read_projectability_csv(
    path: Path,
    selectors: list[str],
) -> tuple[list[list[float]], list[list[float]]]:
    """Read projector-energy samples from an eschar processed band CSV.

    The CSV normally comes from a line-path PROCAR and therefore deliberately
    does not need the same k grid or band count as the regular-grid ``.eig``.
    It determines only the projector-weight energy support; all feasibility,
    gap, and per-k counts are evaluated later on ``.eig``.
    """

    if not selectors:
        raise InputError("target.projection_selectors is required for a CSV projectability source")
    sample_energies: list[float] = []
    sample_weights: list[float] = []
    seen: set[tuple[int, int]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {"k_index", "band", "energy_eV", *selectors}
        missing = sorted(required - fields)
        if missing:
            raise InputError("projectability CSV is missing columns: " + ", ".join(missing))
        for line_number, row in enumerate(reader, 2):
            try:
                kpoint = int(row["k_index"])
                band = int(row["band"])
                energy = float(row["energy_eV"])
                weight = sum(float(row[name]) for name in selectors)
            except (TypeError, ValueError) as exc:
                raise InputError(f"{path}:{line_number}: invalid projectability row") from exc
            if kpoint <= 0 or band <= 0:
                raise InputError(f"{path}:{line_number}: state indices must be positive")
            if (kpoint, band) in seen:
                raise InputError(f"{path}:{line_number}: duplicate k_index/band row")
            seen.add((kpoint, band))
            if not math.isfinite(energy):
                raise InputError(f"{path}:{line_number}: energy_eV must be finite")
            if not math.isfinite(weight) or weight < 0.0:
                raise InputError(f"{path}:{line_number}: projectability weight must be finite and non-negative")
            sample_energies.append(energy)
            sample_weights.append(weight)
    if not sample_energies:
        raise InputError(f"{path}: no projectability rows")
    return [sample_energies], [sample_weights]


def _shortest_weight_interval(
    energies: list[list[float]],
    weights: list[list[float]],
    coverage: float,
    margin_eV: float,
) -> tuple[tuple[float, float], dict[str, float]]:
    """Return the narrowest energy interval containing the requested total weight."""

    states = sorted(
        (float(energy), float(weight))
        for energy_row, weight_row in zip(energies, weights)
        for energy, weight in zip(energy_row, weight_row)
        if weight > 0.0
    )
    total = sum(weight for _, weight in states)
    if not states or total <= 0.0:
        raise InputError("projectability source contains no positive target weight")
    required = coverage * total
    best: tuple[float, float, int, int] | None = None
    accumulated = 0.0
    left = 0
    for right, (upper, weight) in enumerate(states):
        accumulated += weight
        while left <= right and accumulated - states[left][1] >= required:
            accumulated -= states[left][1]
            left += 1
        if accumulated >= required:
            lower = states[left][0]
            candidate = (upper - lower, lower, left, right)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    if best is None:
        raise InputError("cannot resolve the requested cumulative projectability coverage")
    lower = states[best[2]][0] - margin_eV
    upper = states[best[3]][0] + margin_eV
    if lower >= upper:
        raise InputError("automatic target energy window collapsed to zero width")
    selected_weight = sum(weight for energy, weight in states if lower <= energy <= upper)
    return (
        (_round_float(lower), _round_float(upper)),
        {
            "total_weight": total,
            "selected_weight": selected_weight,
            "selected_fraction": selected_weight / total,
            "requested_fraction": coverage,
        },
    )


def _global_gap_midpoints(energies: list[list[float]], eps_eV: float) -> list[dict[str, float]]:
    """Find non-overlapping global adjacent-band gaps on the uniform grid."""

    band_min = [min(row[index] for row in energies) for index in range(len(energies[0]))]
    band_max = [max(row[index] for row in energies) for index in range(len(energies[0]))]
    gaps = []
    for index in range(len(band_min) - 1):
        width = band_min[index + 1] - band_max[index]
        if width > eps_eV:
            gaps.append(
                {
                    "lower_band_1_based": index + 1,
                    "upper_band_1_based": index + 2,
                    "lower_edge_eV": band_max[index],
                    "upper_edge_eV": band_min[index + 1],
                    "midpoint_eV": (band_max[index] + band_min[index + 1]) / 2.0,
                    "width_eV": width,
                }
            )
    return gaps


def _hash_request(request: dict[str, object]) -> str:
    copy_request = copy.deepcopy(request)
    if isinstance(copy_request, dict):
        copy_request.pop("review", None)
    return hashlib.sha256(_json(copy_request).encode("utf-8")).hexdigest()


def _safe_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise InputError(f"{field} must stay inside each candidate directory")
    return str(path)


def _validate_argv(value: object, base: Path, required: bool = False) -> list[str] | None:
    if value is None:
        if required:
            raise InputError("execution.argv is required for the run action")
        return None
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise InputError("execution.argv must be a non-empty list of strings")
    argv = [item.strip() for item in value]
    head_name = Path(argv[0]).name.lower()
    if head_name in _FORBIDDEN_EXECUTABLES:
        raise InputError(f"remote/scheduler executable is forbidden: {argv[0]}")
    if head_name in _SHELL_EXECUTABLES:
        raise InputError(f"shell wrapper is forbidden in execution.argv: {argv[0]}")
    if any(item.lower() in {"auto", "ssh", "sbatch", "qsub", "srun"} for item in argv):
        raise InputError("execution.argv cannot invoke PyW90 auto, SSH, or a scheduler")
    if head_name.endswith(".py") and ("/" in argv[0] or argv[0].startswith(".")):
        head = Path(argv[0]).expanduser()
        argv[0] = str(head.resolve() if head.is_absolute() else (base / head).resolve())
    elif "/" in argv[0] or argv[0].startswith("."):
        head = Path(argv[0]).expanduser()
        argv[0] = str(head.resolve() if head.is_absolute() else (base / head).resolve())
    return argv


def _load_compare_module():
    """Load the sibling comparison implementation without making a package."""

    path = Path(__file__).with_name("pyw90_compare.py")
    spec = importlib.util.spec_from_file_location("fpilot_pyw90_compare_for_scan", path)
    if spec is None or spec.loader is None:
        raise InputError(f"cannot load sibling comparison module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_scan_request(request: object, request_dir: Path | None = None, action: str = "check") -> dict[str, object]:
    """Validate a local window-scan request and return resolved metadata."""

    if not isinstance(request, dict) or request.get("schema") != SCHEMA:
        raise InputError(f"schema must be {SCHEMA}")
    base = Path(request_dir or Path.cwd()).expanduser().resolve()
    source = request.get("source")
    if not isinstance(source, dict):
        raise InputError("source must be an object")
    template_path = _resolve(base, source.get("template_win"), "source.template_win")
    dft_path = _resolve(base, source.get("dft_band"), "source.dft_band")
    eig_path = _resolve(base, source.get("eig"), "source.eig")
    template_file = _file_record(template_path, "source.template_win")
    dft_file = _file_record(dft_path, "source.dft_band")
    eig_file = _file_record(eig_path, "source.eig")
    template_text = template_path.read_text(encoding="utf-8", errors="replace")
    template_windows = _windows_from_text(template_text)
    missing_template_windows = [key for key in WINDOW_KEYS if template_windows[key] is None]
    if missing_template_windows:
        raise InputError("template_win is missing window keys: " + ", ".join(missing_template_windows))
    template_num_wann = _parse_num_wann(template_text)
    expected_num_wann = _positive_int(source.get("expected_num_wann"), "source.expected_num_wann")
    if template_num_wann is not None and template_num_wann != expected_num_wann:
        raise InputError("source.expected_num_wann disagrees with template num_wann")
    seedname = source.get("seedname", template_path.stem)
    if not isinstance(seedname, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", seedname):
        raise InputError("source.seedname must contain only letters, digits, dot, underscore, or hyphen")
    fermi_eV = _float(source.get("fermi_eV"), "source.fermi_eV")
    dft_x, dft_energies = _read_band_blocks(dft_path)
    dft_segments = _count_segments(dft_x)
    if len(dft_energies[0]) <= 0:
        raise InputError("source.dft_band contains no bands")
    eig_energies = _read_eig(eig_path)

    comparison = request.get("comparison", {})
    if not isinstance(comparison, dict):
        raise InputError("comparison must be an object")
    band_offset = comparison.get("dft_band_offset", "auto")
    if band_offset != "auto" and (not isinstance(band_offset, int) or band_offset < 0):
        raise InputError("comparison.dft_band_offset must be auto or a non-negative integer")
    spin = source.get("spin", "spin_down")
    if not isinstance(spin, str) or not spin.strip():
        raise InputError("source.spin must be a non-empty string")

    search = request.get("search", {})
    if not isinstance(search, dict):
        raise InputError("search must be an object")
    max_candidates = _positive_int(search.get("max_candidates", 32), "search.max_candidates", MAX_CANDIDATES)
    eps_eV = _float(search.get("eps_eV", 0.004), "search.eps_eV")
    if eps_eV <= 0.0 or eps_eV > 1.0:
        raise InputError("search.eps_eV must be in (0, 1] eV")
    initial = search.get("initial", {})
    if not isinstance(initial, dict):
        raise InputError("search.initial must be an object")
    base_window: dict[str, float] | None = None
    supplied_initial = [key for key in WINDOW_KEYS if key in initial]
    if supplied_initial and len(supplied_initial) != len(WINDOW_KEYS):
        raise InputError("search.initial must provide all four window keys or none")
    if supplied_initial:
        base_window = {key: _float(initial[key], f"search.initial.{key}") for key in WINDOW_KEYS}
    offsets = search.get("offsets_eV", [-0.5, 0.0, 0.5])
    if not isinstance(offsets, list) or not offsets or len(offsets) > 7:
        raise InputError("search.offsets_eV must contain 1 to 7 values")
    offsets_eV = [_float(value, "search.offsets_eV item") for value in offsets]
    gap_candidates = _positive_int(
        search.get("gap_candidates", 1), "search.gap_candidates", 8
    )
    allow_deep_gap_fallback = search.get("allow_deep_gap_fallback", False)
    if not isinstance(allow_deep_gap_fallback, bool):
        raise InputError("search.allow_deep_gap_fallback must be boolean")
    max_outer_excess_states = _nonnegative_int(
        search.get("max_outer_excess_states", 3),
        "search.max_outer_excess_states",
        64,
    )
    min_disentanglement_headroom = _nonnegative_int(
        search.get("min_disentanglement_headroom", 1),
        "search.min_disentanglement_headroom",
    )
    if min_disentanglement_headroom >= expected_num_wann:
        raise InputError(
            "search.min_disentanglement_headroom must be smaller than source.expected_num_wann"
        )
    explicit_frozen_windows = search.get("frozen_windows_relative_eV", [])
    if not isinstance(explicit_frozen_windows, list) or len(explicit_frozen_windows) > 16:
        raise InputError("search.frozen_windows_relative_eV must be an array of at most 16 windows")
    frozen_windows = [
        _number_pair(value, f"search.frozen_windows_relative_eV[{index}]")
        for index, value in enumerate(explicit_frozen_windows)
    ]
    frozen_projectability_coverages_raw = search.get(
        "frozen_projectability_coverages", [0.4, 0.5, 0.6]
    )
    if (
        not isinstance(frozen_projectability_coverages_raw, list)
        or not frozen_projectability_coverages_raw
        or len(frozen_projectability_coverages_raw) > 8
    ):
        raise InputError(
            "search.frozen_projectability_coverages must contain 1 to 8 values"
        )
    frozen_projectability_coverages = sorted(
        {
            _float(value, "search.frozen_projectability_coverages item")
            for value in frozen_projectability_coverages_raw
        }
    )
    if any(value < 0.1 or value >= 1.0 for value in frozen_projectability_coverages):
        raise InputError(
            "search.frozen_projectability_coverages values must be in [0.1, 1.0)"
        )
    seed_value = search.get("frozen_seed_window_relative_eV")
    if seed_value is None:
        frozen_seed_window = (
            float(template_windows["dis_froz_min"]) - fermi_eV,
            float(template_windows["dis_froz_max"]) - fermi_eV,
        )
    else:
        frozen_seed_window = _number_pair(
            seed_value, "search.frozen_seed_window_relative_eV"
        )

    target = request.get("target")
    legacy_target = target is None
    if target is None:
        target = {
            "mode": "explicit_energy",
            "energy_window_relative_eV": comparison.get(
                "ranking_window_relative_eV",
                [-8.0, 0.0],
            ),
        }
    if not isinstance(target, dict):
        raise InputError("target must be an object")
    target_mode = target.get("mode", "auto_projector")
    if target_mode not in {"auto_projector", "explicit_energy"}:
        raise InputError("target.mode must be auto_projector or explicit_energy")
    target_projectability: dict[str, object] | None = None
    target_weight_summary: dict[str, float] | None = None
    projectability_values: list[list[float]] | None = None
    relative_eig = [[value - fermi_eV for value in row] for row in eig_energies]
    projectability_energies = relative_eig
    if target_mode == "explicit_energy":
        target_window = _number_pair(
            target.get("energy_window_relative_eV"),
            "target.energy_window_relative_eV",
        )
    else:
        projectability = target.get("projectability", {})
        if not isinstance(projectability, dict):
            raise InputError("target.projectability must be an object")
        projectability_source = projectability.get("source", "amn")
        if projectability_source not in {"amn", "csv"}:
            raise InputError("target.projectability.source must be amn or csv")
        projectability_path = _resolve(
            base,
            projectability.get("path"),
            "target.projectability.path",
        )
        projectability_file = _file_record(
            projectability_path, "target.projectability.path"
        )
        selectors = projectability.get("selectors", [])
        if not isinstance(selectors, list) or any(
            not isinstance(item, str) or not item.strip() for item in selectors
        ):
            raise InputError("target.projectability.selectors must be a list of column names")
        selectors = [item.strip() for item in selectors]
        energy_reference = projectability.get("energy_reference", "relative_to_fermi")
        if energy_reference != "relative_to_fermi":
            raise InputError(
                "target.projectability.energy_reference must be relative_to_fermi"
            )
        if projectability_source == "amn":
            if selectors:
                raise InputError("target.projectability.selectors is only valid for CSV input")
            projectability_values = _read_amn_projectability(
                projectability_path, eig_energies
            )
        else:
            projectability_energies, projectability_values = _read_projectability_csv(
                projectability_path,
                selectors,
            )
        weight_coverage = _float(
            target.get("cumulative_weight_coverage", 0.99),
            "target.cumulative_weight_coverage",
        )
        if not 0.5 <= weight_coverage <= 1.0:
            raise InputError("target.cumulative_weight_coverage must be in [0.5, 1.0]")
        margin_eV = _float(target.get("margin_eV", 0.0), "target.margin_eV")
        if margin_eV < 0.0:
            raise InputError("target.margin_eV must be non-negative")
        target_window, target_weight_summary = _shortest_weight_interval(
            projectability_energies,
            projectability_values,
            weight_coverage,
            margin_eV,
        )
        target_projectability = {
            "source": projectability_source,
            "file": projectability_file,
            "selectors": selectors,
            "energy_reference": energy_reference,
        }

    frozen_projector_windows: list[dict[str, object]] = []
    if target_mode == "auto_projector" and not frozen_windows:
        for coverage in frozen_projectability_coverages:
            if coverage >= weight_coverage:
                continue
            window, summary = _shortest_weight_interval(
                projectability_energies,
                projectability_values,
                coverage,
                0.0,
            )
            frozen_projector_windows.append(
                {
                    "coverage": coverage,
                    "window_relative_eV": list(window),
                    "weight_summary": summary,
                }
            )

    default_ranking_window = (
        frozen_projector_windows[-1]["window_relative_eV"]
        if frozen_projector_windows
        else list(target_window)
    )
    metric_window = _number_pair(
        comparison.get("metric_window_relative_eV", list(target_window)),
        "comparison.metric_window_relative_eV",
    )
    ranking_window = _number_pair(
        comparison.get("ranking_window_relative_eV", default_ranking_window),
        "comparison.ranking_window_relative_eV",
    )
    if ranking_window[0] < target_window[0] or ranking_window[1] > target_window[1]:
        raise InputError(
            "comparison.ranking_window_relative_eV must lie inside the resolved target window"
        )
    if metric_window[0] > ranking_window[0] or metric_window[1] < ranking_window[1]:
        raise InputError("comparison.metric_window_relative_eV must contain the ranking window")

    max_excess_jump_eV = comparison.get("max_excess_jump_eV")
    if max_excess_jump_eV is not None:
        max_excess_jump_eV = _float(
            max_excess_jump_eV, "comparison.max_excess_jump_eV"
        )
        if max_excess_jump_eV <= 0.0:
            raise InputError("comparison.max_excess_jump_eV must be positive when provided")

    target_least, target_most = _state_counts(relative_eig, target_window)
    all_global_gaps = _global_gap_midpoints(relative_eig, eps_eV)
    gaps_below_target = sorted(
        (
            gap
            for gap in all_global_gaps
            if float(gap["upper_edge_eV"]) <= target_window[0]
        ),
        key=lambda gap: float(gap["midpoint_eV"]),
        reverse=True,
    )
    if not gaps_below_target:
        raise InputError(
            "no complete global source.eig band gap lies below the resolved target "
            "window; automatic dis_win_min cannot satisfy the gap constraint"
        )
    active_gap_count = gap_candidates if allow_deep_gap_fallback else 1
    selected_gaps = gaps_below_target[:active_gap_count]
    max_refinement_rounds = _positive_int(
        request.get("refinement", {}).get("max_rounds", 2)
        if isinstance(request.get("refinement", {}), dict)
        else None,
        "refinement.max_rounds",
        MAX_REFINEMENT_ROUNDS,
    )
    refinement = request.get("refinement", {})
    if not isinstance(refinement, dict):
        raise InputError("refinement must be an object")
    refinement_step = refinement.get("step_eV", [0.5, 0.25, 0.125])
    if isinstance(refinement_step, (int, float)) and not isinstance(refinement_step, bool):
        refinement_step = [refinement_step]
    if not isinstance(refinement_step, list) or not refinement_step:
        raise InputError("refinement.step_eV must be a number or non-empty list")
    refinement_steps = [_float(value, "refinement.step_eV item") for value in refinement_step]
    if any(value <= 0.0 for value in refinement_steps):
        raise InputError("refinement.step_eV values must be positive")
    per_round = _positive_int(
        refinement.get("candidates_per_round", 8),
        "refinement.candidates_per_round",
        MAX_REFINEMENT_CANDIDATES,
    )

    review = request.get("review", {})
    if not isinstance(review, dict):
        raise InputError("review must be an object")
    review_quality = review.get("quality", "not_reviewed")
    if review_quality not in {"not_reviewed", "good", "poor"}:
        raise InputError("review.quality must be not_reviewed, good, or poor")
    review_spin = review.get("spin", spin)
    if not isinstance(review_spin, str) or not review_spin.strip():
        raise InputError("review.spin must be a non-empty string")
    if review_quality == "poor" and review_spin != spin:
        raise InputError("review.spin must match source.spin for a bounded refinement")
    review_note = review.get("note")
    if review_note is not None and (not isinstance(review_note, str) or not review_note.strip()):
        raise InputError("review.note must be null or a non-empty string")
    if review_quality == "poor" and not isinstance(review_note, str):
        raise InputError("review.note is required when review.quality=poor")

    output_dir = _resolve(base, request.get("output_dir"), "output_dir")
    try:
        common = os.path.commonpath([str(output_dir), str(template_path)])
    except ValueError:
        common = ""
    if common == str(template_path) or output_dir == template_path:
        raise InputError("output_dir cannot contain or replace the production template")
    if output_dir == dft_path:
        raise InputError("output_dir cannot replace the DFT reference file")

    execution = request.get("execution", {})
    if not isinstance(execution, dict):
        raise InputError("execution must be an object")
    argv = _validate_argv(execution.get("argv"), base, required=(action == "run"))
    workers = _positive_int(execution.get("max_workers", 1), "execution.max_workers", MAX_WORKERS)
    timeout = _float(execution.get("timeout_seconds", 3600.0), "execution.timeout_seconds")
    if timeout <= 0.0 or timeout > MAX_TIMEOUT_SECONDS:
        raise InputError(f"execution.timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}]" )
    result_files = execution.get("result_files", {})
    if not isinstance(result_files, dict):
        raise InputError("execution.result_files must be an object")
    result_file_names = {
        "band": _safe_relative_path(result_files.get("band", "{seedname}_band.dat").replace("{seedname}", seedname) if isinstance(result_files.get("band", "{seedname}_band.dat"), str) else result_files.get("band"), "execution.result_files.band"),
        "labelinfo": _safe_relative_path(result_files.get("labelinfo", "{seedname}_band.labelinfo.dat").replace("{seedname}", seedname) if isinstance(result_files.get("labelinfo", "{seedname}_band.labelinfo.dat"), str) else result_files.get("labelinfo"), "execution.result_files.labelinfo"),
        "wout": _safe_relative_path(result_files.get("wout", "{seedname}.wout").replace("{seedname}", seedname) if isinstance(result_files.get("wout", "{seedname}.wout"), str) else result_files.get("wout"), "execution.result_files.wout"),
    }
    shared_inputs = execution.get("shared_inputs", [])
    if not isinstance(shared_inputs, list) or any(not isinstance(item, str) or not item.strip() for item in shared_inputs):
        raise InputError("execution.shared_inputs must be a list of source paths")
    shared_records = []
    for index, item in enumerate(shared_inputs):
        path = _resolve(base, item, f"execution.shared_inputs[{index}]")
        record = _file_record(path, f"execution.shared_inputs[{index}]")
        record["candidate_name"] = path.name
        shared_records.append(record)
    input_mode = execution.get("shared_input_mode", "copy")
    if input_mode not in {"copy", "symlink"}:
        raise InputError("execution.shared_input_mode must be copy or symlink")

    warnings: list[str] = []
    if legacy_target:
        warnings.append(
            "legacy request without target: comparison.ranking_window_relative_eV was "
            "interpreted as target.mode=explicit_energy"
        )
    if review_quality == "poor":
        warnings.append("manual review marked the selected spin poor; refinement remains opt-in and bounded")
    if gap_candidates > 1 and not allow_deep_gap_fallback:
        warnings.append(
            "search.gap_candidates>1 is inactive unless allow_deep_gap_fallback=true; "
            "only the nearest complete gap will be used"
        )
    return {
        "status": "VALID_WITH_WARNINGS" if warnings else "VALID",
        "schema": SCHEMA,
        "request_dir": str(base),
        "request_fingerprint": _hash_request(request),
        "source": {
            "template_win": template_file,
            "dft_band": dft_file,
            "eig": eig_file,
            "seedname": seedname,
            "spin": spin,
            "fermi_eV": fermi_eV,
            "expected_num_wann": expected_num_wann,
            "template_num_wann": template_num_wann,
            "template_windows_absolute_eV": template_windows,
            "dft_points": len(dft_x),
            "dft_bands": len(dft_energies[0]),
            "dft_segments": len(dft_segments),
            "eig_kpoints": len(eig_energies),
            "eig_bands": len(eig_energies[0]),
        },
        "comparison": {
            "metric_window_relative_eV": list(metric_window),
            "ranking_window_relative_eV": list(ranking_window),
            "ranking_basis": "scientific_subwindow_inside_projector_support",
            "dft_band_offset": band_offset,
            "max_excess_jump_eV": max_excess_jump_eV,
            "metric_definition": (
                "Primary RMSE, MAE, and maximum residual use every state selected by the "
                "reference DFT energy inside the declared ranking subwindow; "
                "a mismatched Wannier partner is never dropped for crossing the boundary. "
                "The broader metric window uses the legacy paired-state intersection and "
                "is reported separately as a diagnostic. Both use segment-local interpolation; "
                "when dft_band_offset=auto, each metric records its independently selected "
                "order-preserving DFT band offset."
            ),
        },
        "target": {
            "mode": target_mode,
            "energy_window_relative_eV": list(target_window),
            "projectability": target_projectability,
            "weight_summary": target_weight_summary,
            "state_counts": {"least": target_least, "most": target_most},
            "global_gaps_below_target": gaps_below_target,
            "selected_outer_lower_gaps": selected_gaps,
        },
        "search": {
            "max_candidates": max_candidates,
            "eps_eV": eps_eV,
            "gap_candidates": gap_candidates,
            "allow_deep_gap_fallback": allow_deep_gap_fallback,
            "max_outer_excess_states": max_outer_excess_states,
            "min_disentanglement_headroom": min_disentanglement_headroom,
            "frozen_seed_window_relative_eV": list(frozen_seed_window),
            "frozen_windows_relative_eV": [list(value) for value in frozen_windows],
            "frozen_projectability_coverages": frozen_projectability_coverages,
            "frozen_projector_windows": frozen_projector_windows,
            "initial": base_window,
            "offsets_eV": offsets_eV,
        },
        "refinement": {
            "max_rounds": max_refinement_rounds,
            "step_eV": refinement_steps,
            "candidates_per_round": per_round,
        },
        "review": {"quality": review_quality, "spin": review_spin, "note": review_note},
        "execution": {
            "argv": argv,
            "max_workers": workers,
            "timeout_seconds": timeout,
            "result_files": result_file_names,
            "shared_inputs": shared_records,
            "shared_input_mode": input_mode,
        },
        "output_dir": str(output_dir),
        "warnings": warnings,
        "scientific_boundary": (
            "Window counts are exact per-k eligibility constraints evaluated from source.eig. "
            "The outer lower boundary is restricted to a complete global eig gap below the "
            "resolved projector-support target. The frozen window is independent: it starts "
            "from an audited seed or explicit candidates and must preserve configured "
            "disentanglement headroom; it is never widened merely to approach num_wann. "
            "The primary scoring window is a scientific subwindow inside the target support. "
            "The broader metric window remains diagnostic. "
            "Band-fit metrics require completed candidate W90 artifacts and do not prove "
            "projection quality, topology, or convergence from spread alone."
        ),
        "_dft_x": dft_x,
        "_dft_energies": dft_energies,
        "_eig_energies": eig_energies,
        "_projectability_values": projectability_values,
    }


def _strip_private(checked: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in checked.items() if not key.startswith("_")}


def _candidate_id(seedname: str, windows: dict[str, float], round_number: int) -> str:
    payload = {"seedname": seedname, "round": round_number, "windows_relative_eV": windows}
    digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()[:12]
    return f"win_r{round_number:02d}_{digest}"


def _round_float(value: float) -> float:
    return float(f"{value:.10g}")


def _candidate_windows(
    checked: dict[str, object],
    base_window: dict[str, float] | None,
    round_number: int = 0,
) -> list[dict[str, float]]:
    """Generate finite windows with separate outer-support and frozen-subspace roles."""

    energies = checked["_eig_energies"]
    fermi = float(checked["source"]["fermi_eV"])
    nwann = int(checked["source"]["expected_num_wann"])
    search = checked["search"]
    offsets = sorted({abs(float(value)) for value in search["offsets_eV"]})
    eps_eV = float(search["eps_eV"])
    relative = [[value - fermi for value in row] for row in energies]
    target_min, target_max = (
        float(value) for value in checked["target"]["energy_window_relative_eV"]
    )
    gaps = list(checked["target"]["selected_outer_lower_gaps"])
    min_headroom = int(search["min_disentanglement_headroom"])
    max_outer_excess = int(search["max_outer_excess_states"])
    explicit_frozen = [
        (float(value[0]), float(value[1]))
        for value in search["frozen_windows_relative_eV"]
    ]
    projector_frozen = [
        (
            float(item["window_relative_eV"][0]),
            float(item["window_relative_eV"][1]),
        )
        for item in search["frozen_projector_windows"]
    ]
    seed_min, seed_max = (
        float(value) for value in search["frozen_seed_window_relative_eV"]
    )
    if explicit_frozen:
        frozen_windows = explicit_frozen
    elif projector_frozen:
        frozen_windows = projector_frozen
    else:
        # PROCAR/.amn determines the outer support, not a frozen mandate. In
        # automatic mode, start with the audited template/user seed and widen
        # only the lower boundary progressively toward the projector support.
        # Upward/conduction expansion requires explicit frozen candidates,
        # because energy alone cannot establish the orbital purity of those
        # bands.
        progressive_lowers = [
            seed_min,
            0.5 * (seed_min + min(seed_min, target_min)),
            min(seed_min, target_min),
        ]
        conservative_uppers = [
            seed_max,
            seed_min + 0.75 * (seed_max - seed_min),
            seed_min + 0.5 * (seed_max - seed_min),
        ]
        frozen_windows = [
            (lower, upper)
            for lower in progressive_lowers
            for upper in conservative_uppers
            if lower < upper
        ]

    candidates: list[dict[str, float]] = []
    seen: set[tuple[float, ...]] = set()

    def add(window: dict[str, float], origin: str) -> None:
        row = {key: _round_float(float(window[key])) for key in WINDOW_KEYS}
        valid, counts = _window_valid(
            row,
            relative,
            nwann,
            (target_min, target_max),
            min_headroom,
            max_outer_excess,
        )
        if not valid:
            return
        signature = tuple(row[key] for key in WINDOW_KEYS)
        if signature not in seen:
            seen.add(signature)
            row["_outer_least"] = float(counts["outer_least"])
            row["_outer_most"] = float(counts["outer_most"])
            row["_frozen_least"] = float(counts["frozen_least"])
            row["_frozen_most"] = float(counts["frozen_most"])
            row["_headroom"] = float(counts["disentanglement_headroom_min"])
            row["_origin"] = origin
            candidates.append(row)

    if base_window is not None:
        # Preserve an otherwise valid historical shape only after snapping
        # its outer lower edge to the nearest allowed gap midpoint.
        initial = dict(base_window)
        initial["dis_win_min"] = float(gaps[0]["midpoint_eV"])
        add(initial, "gap-snapped-initial")

    # The lower edge is fixed at the nearest true global-gap midpoint by
    # default. For that lower edge, choose the smallest upper edge that gives
    # num_wann states at every sampled k point and covers projector support.
    # The independent frozen candidates are accepted only when they leave the
    # requested disentanglement headroom.
    for gap in gaps:
        lower = float(gap["midpoint_eV"])
        required_upper: float | None = None
        for row in relative:
            eligible = sorted(value for value in row if value >= lower)
            if len(eligible) < nwann:
                required_upper = None
                break
            kth = eligible[nwann - 1]
            required_upper = kth if required_upper is None else max(required_upper, kth)
        if required_upper is None:
            continue
        for frozen_min, frozen_max in frozen_windows:
            if frozen_min <= lower + eps_eV:
                continue
            minimum_outer_max = max(required_upper, target_max, frozen_max) + eps_eV
            for outer_expand in offsets:
                add(
                    {
                        "dis_win_min": lower,
                        "dis_win_max": minimum_outer_max + outer_expand,
                        "dis_froz_min": frozen_min,
                        "dis_froz_max": frozen_max,
                    },
                    "projector-support-with-progressive-frozen",
                )

    limit = int(search["max_candidates"])
    # Prefer the nearest allowed gap, minimum excess outer states, and more
    # disentanglement headroom. Fit quality is decided only after completed
    # Wannier90 results are scored and visually reviewed.
    def suggestion_key(item: dict[str, float]) -> tuple[object, ...]:
        return (
            -item["dis_win_min"],
            abs(item["_outer_least"] - nwann),
            abs(item["_outer_most"] - nwann),
            item["dis_win_max"] - item["dis_win_min"],
            -item["_headroom"],
            item["dis_froz_max"] - item["dis_froz_min"],
            tuple(item[key] for key in WINDOW_KEYS),
        )

    candidates = sorted(candidates, key=suggestion_key)
    for candidate in candidates:
        candidate.pop("_outer_least", None)
        candidate.pop("_outer_most", None)
        candidate.pop("_frozen_least", None)
        candidate.pop("_frozen_most", None)
        candidate.pop("_headroom", None)
        candidate.pop("_origin", None)
    return candidates[:limit]


def _window_record(window: dict[str, float], checked: dict[str, object], round_number: int, parent: str | None = None) -> dict[str, object]:
    fermi = float(checked["source"]["fermi_eV"])
    absolute = {key: _round_float(window[key] + fermi) for key in WINDOW_KEYS}
    valid, counts = _window_valid(
        window,
        [[value - fermi for value in line] for line in checked["_eig_energies"]],
        int(checked["source"]["expected_num_wann"]),
        tuple(float(value) for value in checked["target"]["energy_window_relative_eV"]),
        int(checked["search"]["min_disentanglement_headroom"]),
        int(checked["search"]["max_outer_excess_states"]),
    )
    if not valid:
        raise InputError("attempted to record a window that violates PyW90 state-count constraints")
    result = {
        "candidate_id": _candidate_id(str(checked["source"]["seedname"]), window, round_number),
        "round": round_number,
        "parent_candidate_id": parent,
        "windows_relative_eV": {key: _round_float(window[key]) for key in WINDOW_KEYS},
        "windows_absolute_eV": absolute,
        "state_counts": counts,
        "status": "pending",
        "score_status": "unscored",
    }
    return result


def _manifest_path(output_dir: Path) -> Path:
    return output_dir / "window_scan_manifest.json"


def _write_json_if_changed(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_manifest(checked: dict[str, object]) -> tuple[dict[str, object], Path]:
    path = _manifest_path(Path(checked["output_dir"]))
    if not path.is_file():
        raise InputError(f"manifest is missing; run plan first: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid manifest JSON: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise InputError("manifest schema is not a PyW90 window-scan manifest")
    if manifest.get("request_fingerprint") != checked["request_fingerprint"]:
        raise InputError("manifest was generated from a different request; plan a new output directory")
    if not isinstance(manifest.get("candidates"), list):
        raise InputError("manifest.candidates must be a list")
    return manifest, path


def _candidate_dir(output_dir: Path, candidate_id: str) -> Path:
    return output_dir / "candidates" / candidate_id


def _ensure_candidate_files(
    checked: dict[str, object], candidate: dict[str, object], template_text: str
) -> None:
    output_dir = Path(checked["output_dir"])
    candidate_id = str(candidate["candidate_id"])
    directory = _candidate_dir(output_dir, candidate_id)
    directory.mkdir(parents=True, exist_ok=True)
    candidate["directory"] = str(directory)
    seedname = str(checked["source"]["seedname"])
    win_path = directory / f"{seedname}.win"
    rendered = _update_windows(template_text, candidate["windows_absolute_eV"])
    if win_path.exists():
        if not win_path.is_file() or win_path.read_text(encoding="utf-8", errors="replace") != rendered:
            raise InputError(f"candidate win exists with different contents; refusing overwrite: {win_path}")
    else:
        win_path.write_text(rendered, encoding="utf-8")
    candidate["win"] = str(win_path)
    for record in checked["execution"]["shared_inputs"]:
        source = Path(record["path"])
        destination = directory / str(record["candidate_name"])
        mode = checked["execution"]["shared_input_mode"]
        if destination.exists() or destination.is_symlink():
            if mode == "symlink" and destination.is_symlink() and destination.resolve() == source.resolve():
                continue
            if mode == "copy" and destination.is_file() and _sha256(destination) == record["sha256"]:
                continue
            raise InputError(f"candidate input exists with different contents; refusing overwrite: {destination}")
        if mode == "symlink":
            destination.symlink_to(source)
        else:
            shutil.copy2(source, destination)


def plan_scan(request: object, request_dir: Path | None = None) -> dict[str, object]:
    checked = check_scan_request(request, request_dir=request_dir, action="plan")
    output_dir = Path(checked["output_dir"])
    manifest_path = _manifest_path(output_dir)
    if manifest_path.exists():
        manifest, _ = _load_manifest(checked)
        existing = {str(row["candidate_id"]): row for row in manifest["candidates"] if isinstance(row, dict) and "candidate_id" in row}
    else:
        existing = {}
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "request_fingerprint": checked["request_fingerprint"],
            "created_at": _now(),
            "updated_at": _now(),
            "status": "planned",
            "refinement_rounds_completed": 0,
            "candidates": [],
        }
    template_text = Path(checked["source"]["template_win"]["path"]).read_text(encoding="utf-8", errors="replace")
    windows = _candidate_windows(checked, checked["search"]["initial"], 0)
    if not windows:
        raise InputError(
            "no window candidate satisfies outer projector-support coverage, the selected "
            "global-gap anchor, the outer excess-state ceiling, and frozen "
            "disentanglement-headroom constraints"
        )
    new_candidates: list[dict[str, object]] = []
    for window in windows:
        candidate = _window_record(window, checked, 0)
        old = existing.get(str(candidate["candidate_id"]))
        if old is not None:
            for key in ("windows_relative_eV", "windows_absolute_eV", "state_counts"):
                if old.get(key) != candidate.get(key):
                    raise InputError(f"manifest candidate identity collision for {candidate['candidate_id']}")
            candidate = old
        _ensure_candidate_files(checked, candidate, template_text)
        new_candidates.append(candidate)
    # Keep refinement candidates and their statuses, but replace only the
    # generated initial set to make repeated plan calls deterministic.
    refinement_candidates = [
        row for row in manifest.get("candidates", [])
        if isinstance(row, dict) and int(row.get("round", 0)) > 0
    ]
    manifest["candidates"] = new_candidates + refinement_candidates
    manifest["updated_at"] = _now()
    manifest["status"] = "planned"
    _write_json_if_changed(manifest_path, manifest)
    return {
        "status": "PLANNED",
        "manifest": str(manifest_path),
        "candidate_count": len(new_candidates),
        "refinement_candidate_count": len(refinement_candidates),
        "created_directories": [str(row["directory"]) for row in new_candidates],
        "scientific_boundary": checked["scientific_boundary"],
    }


def _attempt_number(directory: Path) -> int:
    numbers = []
    for path in directory.glob("run_status*.json"):
        match = re.search(r"attempt-(\d+)", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _attempt_paths(directory: Path, attempt: int) -> tuple[Path, Path, Path]:
    suffix = "" if attempt == 1 else f".attempt-{attempt:03d}"
    return (
        directory / f"stdout{suffix}.txt",
        directory / f"stderr{suffix}.txt",
        directory / ("run_status.json" if attempt == 1 else f"run_status.attempt-{attempt:03d}.json"),
    )


def _argv_for_candidate(argv: list[str], seedname: str) -> list[str]:
    return [item.replace("{seedname}", seedname) for item in argv]


def _run_one(checked: dict[str, object], candidate: dict[str, object], attempt: int) -> dict[str, object]:
    directory = Path(candidate["directory"])
    stdout_path, stderr_path, status_path = _attempt_paths(directory, attempt)
    argv = _argv_for_candidate(list(checked["execution"]["argv"]), str(checked["source"]["seedname"]))
    started = _now()
    start = time.monotonic()
    status: dict[str, object] = {
        "schema": "fpilot.pyw90-window-scan-run-status.v1",
        "candidate_id": candidate["candidate_id"],
        "attempt": attempt,
        "state": "running",
        "argv": argv,
        "cwd": str(directory),
        "started_at": started,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    _write_json_if_changed(status_path, status)
    returncode: int | None = None
    error: str | None = None
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.run(
                argv,
                cwd=str(directory),
                stdout=stdout,
                stderr=stderr,
                timeout=float(checked["execution"]["timeout_seconds"]),
                check=False,
                shell=False,
            )
            returncode = process.returncode
            state = "completed" if returncode == 0 else "failed"
            error = None if returncode == 0 else f"exit code {returncode}"
    except subprocess.TimeoutExpired:
        state = "timeout"
        error = f"timeout after {checked['execution']['timeout_seconds']} seconds"
    except OSError as exc:
        state = "launch_error"
        error = str(exc)
    finished = _now()
    status.update(
        {
            "state": state,
            "returncode": returncode,
            "error": error,
            "finished_at": finished,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
    )
    _write_json_if_changed(status_path, status)
    result = dict(candidate)
    result.update(
        {
            "status": state,
            "attempts": int(candidate.get("attempts", 0)) + 1,
            "last_run": status,
        }
    )
    return result


def run_scan(request: object, request_dir: Path | None = None, retry_failed: bool = False, retry_running: bool = False) -> dict[str, object]:
    checked = check_scan_request(request, request_dir=request_dir, action="run")
    manifest, manifest_path = _load_manifest(checked)
    candidates = [row for row in manifest["candidates"] if isinstance(row, dict)]
    allowed = {"pending"}
    if retry_failed:
        allowed.update({"failed", "timeout", "launch_error"})
    if retry_running:
        allowed.add("running")
    selected = [row for row in candidates if row.get("status", "pending") in allowed]
    if not selected:
        return {"status": "RUN_COMPLETE", "manifest": str(manifest_path), "selected": 0, "completed": 0, "message": "no pending candidates; completed candidates are not rerun by default"}
    for row in selected:
        row["status"] = "running"
        row["attempts"] = int(row.get("attempts", 0))
    manifest["status"] = "running"
    manifest["updated_at"] = _now()
    _write_json_if_changed(manifest_path, manifest)
    by_id = {str(row["candidate_id"]): row for row in candidates}
    completed = 0
    failure_count = 0
    workers = min(int(checked["execution"]["max_workers"]), len(selected))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, checked, row, _attempt_number(Path(row["directory"]))): row
            for row in selected
        }
        for future in as_completed(futures):
            result = future.result()
            by_id[str(result["candidate_id"])] = result
            if result["status"] == "completed":
                completed += 1
            else:
                failure_count += 1
    manifest["candidates"] = [by_id[str(row["candidate_id"])] for row in candidates]
    manifest["status"] = "complete" if failure_count == 0 else "complete_with_failures"
    manifest["updated_at"] = _now()
    _write_json_if_changed(manifest_path, manifest)
    return {
        "status": "RUN_COMPLETE",
        "manifest": str(manifest_path),
        "selected": len(selected),
        "completed": completed,
        "failed_or_timeout": failure_count,
        "shell": False,
        "scientific_boundary": checked["scientific_boundary"],
    }


def _parse_label_segments(compare: Any, path: Path) -> list[tuple[int, int]]:
    records = compare.read_labelinfo(path)
    return compare.label_segments(records)


def _parse_wout_state(path: Path, compare: Any) -> dict[str, object]:
    """Add completion/convergence gates to the sibling parser's window data."""

    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    info = dict(compare.parse_wout(path))
    info["completed"] = "total execution time" in lower or "all done" in lower
    uses_disentanglement = bool(
        re.search(r"using band disentanglement\s*:\s*t", text, re.IGNORECASE)
    )
    info["uses_disentanglement"] = uses_disentanglement
    info["disentanglement_converged"] = (
        "disentanglement convergence criteria satisfied" in lower
        if uses_disentanglement
        else True
    )
    final_state_position = text.lower().rfind("final state")
    has_final_state = final_state_position >= 0
    explicit_wannier_convergence = bool(
        re.search(r"wannierisation convergence criteria satisfied", text, re.IGNORECASE)
    )
    requested_matches = re.findall(r"total number of iterations\s*:\s*(\d+)", text, re.IGNORECASE)
    requested_iterations = int(requested_matches[0]) if requested_matches else None
    iterations = [
        int(value)
        for value in re.findall(r"^\s*(\d+)\s+.*<--\s*CONV\s*$", text, re.IGNORECASE | re.MULTILINE)
    ]
    last_iteration = max(iterations) if iterations else None
    localization_converged = bool(info["completed"] and has_final_state)
    if requested_iterations is not None and last_iteration is not None:
        if last_iteration >= requested_iterations and not explicit_wannier_convergence:
            localization_converged = False
    if explicit_wannier_convergence:
        localization_converged = True
    info["localization_converged"] = localization_converged
    info["last_localization_iteration"] = last_iteration
    info["explicit_wannier_convergence"] = explicit_wannier_convergence
    return info


def _plot_candidate_overlay(
    checked: dict[str, object],
    candidate: dict[str, object],
    arrays: dict[str, object],
    metrics: dict[str, object],
    full_window_metrics: dict[str, object],
    compare: Any,
) -> dict[str, str]:
    """Write a compact per-candidate overlay for the required human review."""

    plt = compare.plt
    np = compare.np
    plot_x = arrays["dft_plot_x"]
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 7.0,
            "axes.linewidth": 0.65,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "pdf.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    fig, ax = plt.subplots(figsize=(5.6, 3.35))
    fermi = float(checked["source"]["fermi_eV"])
    for start, end in arrays["dft_segments"]:
        for band in range(arrays["dft_energy"].shape[1]):
            ax.plot(
                plot_x[start : end + 1],
                arrays["dft_energy"][start : end + 1, band] - fermi,
                color="#555555",
                linewidth=0.35,
                alpha=0.55,
                rasterized=True,
            )
    for dft_part, w90_part in zip(arrays["dft_segments"], arrays["w90_segments"]):
        d0, d1 = dft_part
        w0, w1 = w90_part
        source_x = arrays["w90_x"][w0 : w1 + 1]
        if source_x[-1] <= source_x[0] or not np.all(np.diff(source_x) > 0.0):
            raise InputError("candidate Wannier segment is not strictly increasing")
        fraction = (source_x - source_x[0]) / (source_x[-1] - source_x[0])
        mapped_x = float(plot_x[d0]) + fraction * (
            float(plot_x[d1]) - float(plot_x[d0])
        )
        for band in range(arrays["w90_energy"].shape[1]):
            ax.plot(
                mapped_x,
                arrays["w90_energy"][w0 : w1 + 1, band] - fermi,
                color="#4c72b0",
                linewidth=0.58,
                alpha=0.92,
                rasterized=True,
            )
    ticks, labels = compare._ticks({**arrays, "records": arrays["records"]})
    ax.set_xticks(ticks, labels)
    for tick in ticks[1:-1]:
        ax.axvline(tick, color="#999999", linewidth=0.35, alpha=0.5)
    ax.axhline(0.0, color="#222222", linewidth=0.55, linestyle=(0, (3, 2)))
    ax.set_xlim(float(plot_x[0]), float(plot_x[-1]))
    ax.set_ylim(*checked["comparison"]["metric_window_relative_eV"])
    ax.set_xlabel("Wave vector")
    ax.set_ylabel("E − E_F (eV)")
    fig.suptitle(
        f"{checked['source']['spin']}  {candidate['candidate_id']}  "
        f"target RMSE={float(metrics['rmse_meV']):.1f} meV  |  "
        f"full RMSE={float(full_window_metrics['rmse_meV']):.1f} meV",
        fontsize=7.5,
        y=0.985,
    )
    handles = [
        ax.plot([], [], color="#555555", linewidth=0.9, label="VASP DFT")[0],
        ax.plot([], [], color="#4c72b0", linewidth=0.9, label="Wannier90")[0],
    ]
    fig.legend(
        handles=handles,
        frameon=False,
        fontsize=6.5,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.94),
        ncol=2,
        handlelength=1.5,
        columnspacing=1.5,
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.84))
    directory = Path(candidate["directory"])
    png = directory / "fit_overlay.png"
    pdf = directory / "fit_overlay.pdf"
    svg = directory / "fit_overlay.svg"
    fig.savefig(png, dpi=300, facecolor="white")
    fig.savefig(pdf, dpi=300, facecolor="white")
    fig.savefig(svg, dpi=300, facecolor="white")
    plt.close(fig)
    return {"png": str(png), "pdf": str(pdf), "svg": str(svg)}


def _reference_window_metrics(
    dft: Any,
    w90: Any,
    fermi: float,
    window: tuple[float, float],
    requested_offset: int | str,
    compare: Any,
) -> dict[str, object]:
    """Score every state selected by the reference DFT energy window.

    Unlike the general comparison helper, the scan ranking must not drop a
    target reference state merely because its mismatched Wannier partner has
    moved outside the target window. Therefore only the DFT energy selects
    samples; every corresponding residual remains in the score.
    """

    spec=importlib.util.spec_from_file_location("fpilot_shared_comparison_metrics",Path(__file__).with_name("comparison_metrics.py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    try:return module.scan_reference_metrics(dft,w90,fermi,window,requested_offset)
    except ValueError as exc:raise InputError(str(exc)) from exc


def _shape_jump_metrics(
    dft: Any,
    w90: Any,
    offset: int,
    segments: list[tuple[int, int]],
    compare: Any,
) -> dict[str, float | int]:
    """Measure candidate-only jumps relative to the matched DFT band ordering.

    This does not replace visual review. It exposes the conspicuous cross-gap
    spike failure mode that a global RMSE can average away.
    """

    np = compare.np
    selected = dft[:, offset : offset + w90.shape[1]]
    dft_jumps: list[float] = []
    w90_jumps: list[float] = []
    for start, end in segments:
        if end <= start:
            continue
        dft_jumps.extend(np.abs(np.diff(selected[start : end + 1], axis=0)).ravel().tolist())
        w90_jumps.extend(np.abs(np.diff(w90[start : end + 1], axis=0)).ravel().tolist())
    if not dft_jumps or not w90_jumps:
        raise InputError("band path has no adjacent points for shape diagnostics")
    max_dft = float(max(dft_jumps))
    max_w90 = float(max(w90_jumps))
    return {
        "adjacent_pairs": len(w90_jumps),
        "max_dft_jump_eV": max_dft,
        "max_wannier_jump_eV": max_w90,
        "max_excess_jump_eV": max(0.0, max_w90 - max_dft),
        "p95_wannier_jump_eV": float(np.percentile(np.asarray(w90_jumps), 95.0)),
    }


def _score_candidate(checked: dict[str, object], candidate: dict[str, object], compare: Any) -> dict[str, object]:
    directory = Path(candidate["directory"])
    result_files = checked["execution"]["result_files"]
    paths = {name: directory / str(relative) for name, relative in result_files.items()}
    result: dict[str, object] = {
        "candidate_id": candidate["candidate_id"],
        "round": candidate.get("round", 0),
        "directory": str(directory),
        "windows_relative_eV": candidate["windows_relative_eV"],
        "windows_absolute_eV": candidate["windows_absolute_eV"],
        "state_counts": candidate.get("state_counts", {}),
        "run_status": candidate.get("status", "pending"),
        "artifacts": {},
        "status": "missing",
        "score_error": None,
        "spread_used_for_ranking": False,
    }
    missing = []
    for name, path in paths.items():
        if path.is_file() and path.stat().st_size > 0:
            result["artifacts"][name] = {"path": str(path), "size": path.stat().st_size, "sha256": _sha256(path)}
        else:
            result["artifacts"][name] = {"path": str(path), "exists": False}
            missing.append(name)
    if missing:
        result["score_error"] = "missing candidate artifact(s): " + ", ".join(missing)
        return result
    try:
        dft_path = Path(checked["source"]["dft_band"]["path"])
        dft_x, dft_energy = compare.read_two_column_blocks(dft_path)
        w90_x, w90_energy = compare.read_two_column_blocks(paths["band"])
        records = compare.read_labelinfo(paths["labelinfo"])
        dft_segments = compare.reset_segments(dft_x)
        w90_segments = compare.label_segments(records)
        if len(dft_segments) != len(w90_segments):
            raise InputError("DFT and candidate labelinfo segment counts differ")
        arrays = {
            "dft_x": dft_x,
            "dft_plot_x": compare.continuous_plot_x(dft_x, dft_segments),
            "dft_energy": dft_energy,
            "dft_segments": dft_segments,
            "w90_x": w90_x,
            "w90_energy": w90_energy,
            "w90_segments": w90_segments,
            "records": records,
        }
        dft_interp, w90_pairs = compare._interpolated_pairs(arrays)
        ranking_window = tuple(checked["comparison"]["ranking_window_relative_eV"])
        metrics = _reference_window_metrics(
            dft_interp,
            w90_pairs,
            float(checked["source"]["fermi_eV"]),
            ranking_window,
            checked["comparison"]["dft_band_offset"],
            compare,
        )
        metric_window = tuple(checked["comparison"]["metric_window_relative_eV"])
        full_window_metrics = compare._offset_metrics(
            dft_interp,
            w90_pairs,
            float(checked["source"]["fermi_eV"]),
            metric_window,
            checked["comparison"]["dft_band_offset"],
        )
        result["metrics"] = metrics
        result["ranking_metrics"] = metrics
        result["full_window_metrics"] = full_window_metrics
        shape_metrics = _shape_jump_metrics(
            dft_interp,
            w90_pairs,
            int(metrics["offset"]),
            arrays["dft_segments"],
            compare,
        )
        result["shape_diagnostics"] = shape_metrics
        result["metric_windows_relative_eV"] = {
            "ranking": list(ranking_window),
            "full_diagnostic": list(metric_window),
        }
        result["review_figures"] = _plot_candidate_overlay(
            checked, candidate, arrays, metrics, full_window_metrics, compare
        )
        wout_info = _parse_wout_state(paths["wout"], compare)
        result["wout"] = wout_info
        win_info = _windows_from_text((directory / f"{checked['source']['seedname']}.win").read_text(encoding="utf-8", errors="replace"))
        result["lineage"] = {
            "candidate_win": win_info,
            "wout": wout_info,
            "status": "MATCH",
            "tolerance_eV": WOUT_WINDOW_TOLERANCE_EV,
        }
        missing_window_keys = [key for key in WINDOW_KEYS if wout_info.get(key) is None]
        mismatches = []
        for key in WINDOW_KEYS:
            win_value = win_info.get(key)
            wout_value = wout_info.get(key)
            if (
                win_value is not None
                and wout_value is not None
                and abs(float(win_value) - float(wout_value)) > WOUT_WINDOW_TOLERANCE_EV
            ):
                mismatches.append(key)
        if missing_window_keys:
            result["lineage"]["status"] = "PARTIAL"
            result["lineage"]["missing_wout_window_keys"] = missing_window_keys
            result["warnings"] = [
                "candidate .wout does not record every window field: "
                + ", ".join(missing_window_keys)
            ]
        elif mismatches:
            result["lineage"]["status"] = "MISMATCH"
            result["lineage"]["mismatches"] = mismatches
            result["warnings"] = ["candidate .wout window differs from candidate .win: " + ", ".join(mismatches)]
        convergence_eligible = bool(
            wout_info.get("completed")
            and wout_info.get("disentanglement_converged")
            and wout_info.get("localization_converged")
        )
        result["convergence_eligible"] = convergence_eligible
        result["fit_metrics_available"] = True
        jump_limit = checked["comparison"]["max_excess_jump_eV"]
        shape_eligible = bool(
            jump_limit is None
            or float(shape_metrics["max_excess_jump_eV"]) <= float(jump_limit)
        )
        result["shape_eligible"] = shape_eligible
        result["eligible_for_primary_ranking"] = bool(
            convergence_eligible
            and result["lineage"]["status"] == "MATCH"
            and shape_eligible
        )
        result["status"] = "scored" if result["eligible_for_primary_ranking"] else "not_eligible"
        if not convergence_eligible:
            result.setdefault("warnings", []).append(
                "candidate .wout is incomplete or not explicitly converged; fit metrics are diagnostic only"
            )
        if result["lineage"]["status"] != "MATCH":
            result.setdefault("warnings", []).append(
                "candidate .wout/.win lineage is incomplete or mismatched; fit metrics are diagnostic only"
            )
        if not shape_eligible:
            result.setdefault("warnings", []).append(
                "candidate exceeds comparison.max_excess_jump_eV; cross-gap spike review is required"
            )
    except (OSError, ValueError, RuntimeError, IndexError, KeyError) as exc:
        result["status"] = "invalid"
        result["score_error"] = str(exc)
    return result


def score_scan(request: object, request_dir: Path | None = None) -> dict[str, object]:
    checked = check_scan_request(request, request_dir=request_dir, action="score")
    manifest, manifest_path = _load_manifest(checked)
    compare = _load_compare_module()
    scored: list[dict[str, object]] = []
    for candidate in manifest["candidates"]:
        if not isinstance(candidate, dict):
            continue
        result = _score_candidate(checked, candidate, compare)
        candidate["score_status"] = result["status"]
        candidate["last_score"] = result
        scored.append(result)
    reference_signatures = sorted(
        {
            (
                int(item["metrics"]["offset"]),
                int(item["metrics"]["reference_values"]),
            )
            for item in scored
            if isinstance(item.get("metrics"), dict)
        }
    )
    reference_sample_consistent = len(reference_signatures) <= 1
    if not reference_sample_consistent:
        warning = (
            "candidate auto-offset/reference sample signatures differ; diagnostic metrics "
            "remain available, but primary ranking requires one explicit common dft_band_offset"
        )
        for item in scored:
            if isinstance(item.get("metrics"), dict):
                item["eligible_for_primary_ranking"] = False
                item["status"] = "not_eligible"
                item.setdefault("warnings", []).append(warning)
    ranked = sorted(
        scored,
        key=lambda item: (
            0 if item.get("eligible_for_primary_ranking") is True and isinstance(item.get("metrics"), dict) else 1,
            float(item.get("metrics", {}).get("rmse_meV", math.inf)) if isinstance(item.get("metrics"), dict) else math.inf,
            float(item.get("metrics", {}).get("mae_meV", math.inf)) if isinstance(item.get("metrics"), dict) else math.inf,
            float(item.get("metrics", {}).get("max_abs_diff_meV", math.inf)) if isinstance(item.get("metrics"), dict) else math.inf,
            str(item.get("candidate_id")),
        ),
    )
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    best = next(
        (
            item
            for item in ranked
            if item.get("eligible_for_primary_ranking") is True
            and isinstance(item.get("metrics"), dict)
        ),
        None,
    )
    score_path = Path(checked["output_dir"]) / "score.json"
    payload = {
        "schema": SCORE_SCHEMA,
        "created_at": _now(),
        "request_fingerprint": checked["request_fingerprint"],
        "spin": checked["source"]["spin"],
        "metric_definition": checked["comparison"]["metric_definition"],
        "selection": {
            "ranking_basis": checked["comparison"]["ranking_basis"],
            "ranking_window_relative_eV": checked["comparison"]["ranking_window_relative_eV"],
            "full_diagnostic_window_relative_eV": checked["comparison"]["metric_window_relative_eV"],
            "reference_sample_consistent": reference_sample_consistent,
            "observed_offset_reference_values": [
                {"offset": offset, "reference_values": count}
                for offset, count in reference_signatures
            ],
            "max_excess_jump_eV": checked["comparison"]["max_excess_jump_eV"],
            "shape_gate": (
                "optional maximum excess adjacent-band jump relative to the matched DFT bands"
            ),
            "ranking": ["metrics.rmse_meV", "metrics.mae_meV", "metrics.max_abs_diff_meV", "candidate_id"],
            "spread_used_for_ranking": False,
            "best_candidate_id": best["candidate_id"] if best else None,
        },
        "candidates": ranked,
        "scientific_boundary": checked["scientific_boundary"],
    }
    _write_json_if_changed(score_path, payload)
    manifest["last_score_file"] = str(score_path)
    manifest["updated_at"] = _now()
    _write_json_if_changed(manifest_path, manifest)
    return {
        "status": "SCORE_COMPLETE",
        "manifest": str(manifest_path),
        "score": str(score_path),
        "candidate_count": len(scored),
        "scored_count": sum(item.get("status") == "scored" for item in scored),
        "missing_or_invalid_count": sum(item.get("status") != "scored" for item in scored),
        "best_candidate_id": best["candidate_id"] if best else None,
        "ranking": [
            {
                "rank": item["rank"],
                "candidate_id": item["candidate_id"],
                "status": item["status"],
                "target_rmse_meV": item.get("metrics", {}).get("rmse_meV") if isinstance(item.get("metrics"), dict) else None,
                "full_window_rmse_meV": item.get("full_window_metrics", {}).get("rmse_meV") if isinstance(item.get("full_window_metrics"), dict) else None,
                "max_excess_jump_eV": item.get("shape_diagnostics", {}).get("max_excess_jump_eV") if isinstance(item.get("shape_diagnostics"), dict) else None,
            }
            for item in ranked
        ],
    }


def refine_scan(request: object, request_dir: Path | None = None) -> dict[str, object]:
    checked = check_scan_request(request, request_dir=request_dir, action="refine")
    if checked["review"]["quality"] != "poor":
        return {
            "status": "NOT_TRIGGERED",
            "reason": "refinement requires review.quality=poor; no files or manifest entries were changed",
        }
    manifest, manifest_path = _load_manifest(checked)
    completed_rounds = int(manifest.get("refinement_rounds_completed", 0))
    max_rounds = int(checked["refinement"]["max_rounds"])
    if completed_rounds >= max_rounds:
        raise InputError(f"refinement hard limit reached: {completed_rounds}/{max_rounds} rounds")
    score_path = Path(manifest.get("last_score_file", Path(checked["output_dir"]) / "score.json"))
    if not score_path.is_file():
        raise InputError("refine requires a completed score.json; run score first")
    score = json.loads(score_path.read_text(encoding="utf-8"))
    if score.get("request_fingerprint") != checked["request_fingerprint"]:
        raise InputError("score.json was generated from a different request")
    best_id = score.get("selection", {}).get("best_candidate_id")
    if not isinstance(best_id, str):
        raise InputError("refine requires at least one scored candidate")
    candidates = [row for row in manifest["candidates"] if isinstance(row, dict)]
    best = next((row for row in candidates if row.get("candidate_id") == best_id), None)
    if best is None:
        raise InputError("best candidate from score.json is absent from manifest")
    base = {key: float(best["windows_relative_eV"][key]) for key in WINDOW_KEYS}
    steps = list(checked["refinement"]["step_eV"])
    step = float(steps[min(completed_rounds, len(steps) - 1)])
    round_number = completed_rounds + 1
    windows: list[dict[str, float]] = []
    # Keep dis_win_min pinned to its audited global-gap midpoint. Refine only
    # the outer upper and frozen boundaries, rechecking target coverage and
    # exact per-k state counts after every one-coordinate move.
    for key in ("dis_win_max", "dis_froz_min", "dis_froz_max"):
        for sign in (-1.0, 1.0):
            candidate_window = dict(base)
            candidate_window[key] = _round_float(candidate_window[key] + sign * step)
            valid, _ = _window_valid(
                candidate_window,
                [[value - float(checked["source"]["fermi_eV"]) for value in line] for line in checked["_eig_energies"]],
                int(checked["source"]["expected_num_wann"]),
                tuple(float(value) for value in checked["target"]["energy_window_relative_eV"]),
                int(checked["search"]["min_disentanglement_headroom"]),
                int(checked["search"]["max_outer_excess_states"]),
            )
            if valid:
                windows.append(candidate_window)
    existing_ids = {str(row.get("candidate_id")) for row in candidates}
    template_text = Path(checked["source"]["template_win"]["path"]).read_text(encoding="utf-8", errors="replace")
    added: list[dict[str, object]] = []
    for window in windows:
        candidate = _window_record(window, checked, round_number, parent=str(best_id))
        if candidate["candidate_id"] in existing_ids:
            continue
        _ensure_candidate_files(checked, candidate, template_text)
        candidates.append(candidate)
        added.append(candidate)
        existing_ids.add(str(candidate["candidate_id"]))
        if len(added) >= int(checked["refinement"]["candidates_per_round"]):
            break
    manifest["candidates"] = candidates
    manifest.setdefault("review_history", []).append(
        {
            "recorded_at": _now(),
            "spin": checked["review"]["spin"],
            "quality": checked["review"]["quality"],
            "note": checked["review"]["note"],
            "triggered_round": round_number,
            "parent_candidate_id": best_id,
        }
    )
    manifest["refinement_rounds_completed"] = round_number
    manifest["updated_at"] = _now()
    manifest["status"] = "planned_refinement"
    _write_json_if_changed(manifest_path, manifest)
    return {
        "status": "REFINEMENT_PLANNED",
        "manifest": str(manifest_path),
        "round": round_number,
        "parent_candidate_id": best_id,
        "step_eV": step,
        "added_candidate_count": len(added),
        "added_candidate_ids": [row["candidate_id"] for row in added],
        "remaining_rounds": max_rounds - round_number,
    }


def _load_request(path: Path) -> tuple[dict[str, object], Path]:
    resolved = path.expanduser().resolve()
    try:
        request = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid request JSON: {resolved}") from exc
    if not isinstance(request, dict):
        raise InputError("request root must be a JSON object")
    return request, resolved.parent


def measure_curves(request: object) -> dict[str, object]:
    """Measure one lineage-checked channel, including a single spinor channel.

    The caller checks the path/spin identity; this action never launches jobs.
    """
    compare = _load_compare_module()
    tables = {"DFT": {}, "Wannier90": {}}
    with Path(request["curves_csv"]).open() as stream:
        for row in csv.DictReader(stream):
            method = row["method"]
            if method not in tables:
                raise InputError("unknown curve method")
            key = (int(row["k_index"]), int(row["band"]))
            if key in tables[method]:
                raise InputError("duplicate curve sample")
            tables[method][key] = _float(float(row["energy_relative_eV"]), "energy")
    matrices = []
    for table in tables.values():
        if not table:
            raise InputError("missing curve samples")
        nk, nb = max(k for k, _ in table), max(b for _, b in table)
        if len(table) != nk * nb:
            raise InputError("incomplete curve grid")
        matrices.append(compare.np.array([[table[k,b] for b in range(1,nb+1)] for k in range(1,nk+1)]))
    if matrices[0].shape[0] != matrices[1].shape[0]:
        raise InputError("point count differs")
    offset = request["dft_band_offset"]
    if type(offset) is not int or offset < 0:
        raise InputError("one explicit common DFT band offset is required")
    return {"status":"MEASURED", **_reference_window_metrics(matrices[0], matrices[1], 0.0,
                    _number_pair(request["target_relative_eV"], "target"), offset, compare),
                    "selection":"caller_fixed_order_preserving_correspondence"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded local PyW90 Wannier90 energy-window search")
    parser.add_argument("action", choices=("check", "plan", "run", "score", "refine", "measure"))
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--retry-failed", action="store_true", help="allow run to retry failed/timeout candidates")
    parser.add_argument("--retry-running", action="store_true", help="allow run to recover candidates left in running state")
    args = parser.parse_args(argv)
    try:
        request, request_dir = _load_request(args.request)
        if args.action == "measure":
            result = measure_curves(request)
        elif args.action == "check":
            result = _strip_private(check_scan_request(request, request_dir=request_dir, action="check"))
        elif args.action == "plan":
            result = plan_scan(request, request_dir=request_dir)
        elif args.action == "run":
            result = run_scan(request, request_dir=request_dir, retry_failed=args.retry_failed, retry_running=args.retry_running)
        elif args.action == "score":
            result = score_scan(request, request_dir=request_dir)
        else:
            result = refine_scan(request, request_dir=request_dir)
    except (InputError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "ERROR", "errors": [str(exc)]}
    print(_json(result))
    return 0 if result.get("status") in {
        "MEASURED",
        "VALID",
        "VALID_WITH_WARNINGS",
        "PLANNED",
        "RUN_COMPLETE",
        "SCORE_COMPLETE",
        "REFINEMENT_PLANNED",
        "NOT_TRIGGERED",
    } else 1


if __name__ == "__main__":
    sys.exit(main())
