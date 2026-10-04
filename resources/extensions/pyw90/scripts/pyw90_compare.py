#!/usr/bin/env python3
"""Deterministic spin-resolved DFT--Wannier band comparison workflow.

This high-level adapter validates all scientific/file choices in one request,
checks the ``.win``/``.wout``/checkpoint lineage, maps discontinuous k-path
segments without joining them, selects a reproducible DFT band offset, and
writes bounded comparison metrics plus a publication-ready two-panel figure.
It consumes already prepared band files and never runs VASP or Wannier90.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


SCHEMA = "fpilot.pyw90-compare-request.v1"
CHANNELS = ("spin_up", "spin_down")
WINDOW_KEYS = ("dis_win_min", "dis_win_max", "dis_froz_min", "dis_froz_max")
FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


class InputError(ValueError):
    """Malformed or scientifically inconsistent comparison input."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _resolve(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty path")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


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


def _normalize_label(label: str) -> str:
    label = label.strip().upper().replace("\\GAMMA", "GAMMA")
    return {"GAMMA": "GAMMA", "\u0393": "GAMMA", "S0": "S_0"}.get(label, label)


def _display_label(label: str) -> str:
    return {"GAMMA": "\u0393", "S_0": "S0"}.get(_normalize_label(label), label)


def read_two_column_blocks(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read blank-line separated two-column band blocks."""

    blocks: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = raw.split()
        if not fields:
            if current:
                blocks.append(current)
                current = []
            continue
        if len(fields) < 2:
            raise InputError(f"{path}:{line_number}: expected two numeric columns")
        try:
            current.append((float(fields[0]), float(fields[1])))
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: non-numeric band row") from exc
    if current:
        blocks.append(current)
    if not blocks:
        raise InputError(f"{path}: no band blocks")
    npoints = len(blocks[0])
    if npoints < 2:
        raise InputError(f"{path}: fewer than two points per band")
    x = np.asarray([row[0] for row in blocks[0]], dtype=float)
    energies = np.empty((npoints, len(blocks)), dtype=float)
    for band_index, block in enumerate(blocks):
        if len(block) != npoints:
            raise InputError(f"{path}: inconsistent point count in band {band_index + 1}")
        block_x = np.asarray([row[0] for row in block], dtype=float)
        if not np.allclose(block_x, x, rtol=0.0, atol=2.0e-7):
            raise InputError(f"{path}: abscissa differs between band blocks")
        energies[:, band_index] = np.asarray([row[1] for row in block], dtype=float)
    if not np.all(np.isfinite(energies)):
        raise InputError(f"{path}: non-finite band energy")
    return x, energies


def reset_segments(x: np.ndarray, tolerance: float = 2.0e-7) -> list[tuple[int, int]]:
    reset_after = np.flatnonzero(np.diff(x) <= tolerance)
    starts = [0, *(reset_after + 1).tolist()]
    ends = [*reset_after.tolist(), len(x) - 1]
    segments = list(zip(starts, ends))
    if any(start >= end for start, end in segments):
        raise InputError(f"invalid or zero-length DFT path segments: {segments}")
    for start, end in segments:
        if not np.all(np.diff(x[start : end + 1]) > 0.0):
            raise InputError(f"DFT segment {start}:{end} is not strictly increasing")
    return segments


def continuous_plot_x(x: np.ndarray, segments: list[tuple[int, int]]) -> np.ndarray:
    """Map reset-per-segment abscissae onto one continuous display axis."""

    result = np.empty_like(x, dtype=float)
    cursor = 0.0
    for start, end in segments:
        source = x[start : end + 1]
        span = float(source[-1] - source[0])
        if span <= 2.0e-7 or not np.all(np.diff(source) > 0.0):
            raise InputError("segment abscissa must be strictly increasing")
        result[start : end + 1] = cursor + source - float(source[0])
        cursor = float(result[end])
    return result


def read_labelinfo(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) < 3:
            raise InputError(f"{path}:{line_number}: incomplete label record")
        try:
            index = int(fields[1]) - 1
            position = float(fields[2])
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: invalid label record") from exc
        if index < 0:
            raise InputError(f"{path}:{line_number}: label index must be positive")
        records.append({"label": _normalize_label(fields[0]), "index": index, "position": position})
    if len(records) < 2:
        raise InputError(f"{path}: fewer than two high-symmetry labels")
    return records


def label_segments(records: list[dict[str, object]]) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    for left, right in zip(records, records[1:]):
        if float(right["position"]) - float(left["position"]) > 1.0e-9:
            segments.append((int(left["index"]), int(right["index"])))
    if not segments or any(start >= end for start, end in segments):
        raise InputError("labelinfo contains no valid non-zero path segments")
    return segments


def label_segment_pairs(records: list[dict[str, object]]) -> list[list[str]]:
    pairs: list[list[str]] = []
    for left, right in zip(records, records[1:]):
        if float(right["position"]) - float(left["position"]) > 1.0e-9:
            pairs.append([str(left["label"]), str(right["label"])])
    return pairs


def read_kpoints_segments(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 6 or "line" not in lines[2].lower():
        raise InputError(f"{path}: expected VASP line-mode KPOINTS")
    try:
        points_per_segment = int(lines[1].split()[0])
    except (IndexError, ValueError) as exc:
        raise InputError(f"{path}: invalid line-mode point count") from exc
    records: list[tuple[list[float], str]] = []
    for line_number, raw in enumerate(lines[4:], 5):
        fields = raw.split()
        if not fields:
            continue
        if len(fields) < 4:
            raise InputError(f"{path}:{line_number}: endpoint needs kx ky kz label")
        try:
            vector = [float(fields[0]), float(fields[1]), float(fields[2])]
        except ValueError as exc:
            raise InputError(f"{path}:{line_number}: invalid endpoint vector") from exc
        records.append((vector, _normalize_label(fields[3])))
    if not records or len(records) % 2:
        raise InputError(f"{path}: line-mode endpoints must occur in pairs")
    pairs = [[records[index][1], records[index + 1][1]] for index in range(0, len(records), 2)]
    canonical = {"points_per_segment": points_per_segment, "segment_labels": pairs}
    path_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
    return {**canonical, "path_hash": path_hash, "segments": len(pairs)}


def _label_path_hash(records: list[dict[str, object]], points_per_segment: int) -> str:
    canonical = {
        "points_per_segment": points_per_segment,
        "segment_labels": label_segment_pairs(records),
    }
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def parse_win(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: dict[str, object] = {}
    for key in ("num_wann", "num_bands"):
        match = re.search(rf"(?im)^\s*{key}\s*=\s*(\d+)", text)
        result[key] = int(match.group(1)) if match else None
    match = re.search(r"(?im)^\s*exclude_bands\s*=\s*([^!#\r\n]+)", text)
    result["exclude_bands"] = re.sub(r"\s+", "", match.group(1)) if match else None
    for key in WINDOW_KEYS:
        match = re.search(rf"(?im)^\s*{key}\s*=\s*({FLOAT_RE})", text)
        result[key] = float(match.group(1)) if match else None
    spinors = re.search(r"(?im)^\s*spinors\s*=\s*([^!#\r\n]+)", text)
    result["spinors"] = spinors.group(1).strip().lower() if spinors else None
    return result


def parse_wout(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: dict[str, object] = {}
    patterns = {
        "num_wann": r"Number of Wannier Functions\s*:\s*(\d+)",
        "num_bands": r"Number of input Bloch states\s*:\s*(\d+)",
    }
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        result[key] = int(matches[-1]) if matches else None
    outer = re.findall(rf"Outer:\s*({FLOAT_RE})\s+to\s+({FLOAT_RE})", text, flags=re.IGNORECASE)
    inner = re.findall(rf"Inner:\s*({FLOAT_RE})\s+to\s+({FLOAT_RE})", text, flags=re.IGNORECASE)
    result["dis_win_min"] = float(outer[-1][0]) if outer else None
    result["dis_win_max"] = float(outer[-1][1]) if outer else None
    result["dis_froz_min"] = float(inner[-1][0]) if inner else None
    result["dis_froz_max"] = float(inner[-1][1]) if inner else None
    converged = bool(re.search(r"disentanglement convergence criteria satisfied", text, re.IGNORECASE))
    result["disentanglement_converged"] = converged
    return result


def _last_outcar_fermi(path: Path) -> float | None:
    matches = re.findall(rf"E-fermi\s*:\s*({FLOAT_RE})", path.read_text(encoding="utf-8", errors="replace"))
    return float(matches[-1]) if matches else None


def _checkpoint_record(base: Path, value: object, field: str) -> tuple[dict[str, object], str]:
    if isinstance(value, str):
        record = _file_record(_resolve(base, value, field), field)
        return record, "BOUND_LOCAL"
    if not isinstance(value, dict):
        raise InputError(f"{field} must be a path or an unavailable-checkpoint object")
    digest = value.get("sha256")
    reason = value.get("unavailable_reason")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        raise InputError(f"{field}.sha256 must be a 64-character hexadecimal digest")
    if not isinstance(reason, str) or not reason.strip():
        raise InputError(f"{field}.unavailable_reason is required when checkpoint is not local")
    return {"path": None, "sha256": digest, "unavailable_reason": reason.strip()}, "DECLARED_UNAVAILABLE"


def _lineage(win: dict[str, object], wout: dict[str, object], checkpoint_status: str) -> dict[str, object]:
    mismatches: list[dict[str, object]] = []
    for key in ("num_wann", "num_bands", *WINDOW_KEYS):
        left, right = win.get(key), wout.get(key)
        if left is not None and right is not None and left != right:
            mismatches.append({"field": key, "win": left, "wout": right})
    if mismatches:
        status = "MISMATCH"
    elif checkpoint_status != "BOUND_LOCAL":
        status = "PARTIAL"
    else:
        status = "MATCH"
    return {
        "status": status,
        "checkpoint_status": checkpoint_status,
        "win": win,
        "wout": wout,
        "mismatches": mismatches,
        "boundary": "The wout records the model that was built; a current win mismatch must not be attributed to the checkpoint model.",
    }


def check_compare_request(request: object, request_dir: Path | None = None) -> dict[str, object]:
    """Resolve and audit a complete two-spin comparison request."""

    if not isinstance(request, dict) or request.get("schema") != SCHEMA:
        raise InputError(f"schema must be {SCHEMA}")
    base = Path(request_dir or Path.cwd()).expanduser().resolve()
    dft = request.get("dft")
    wannier = request.get("wannier")
    comparison = request.get("comparison")
    if not isinstance(dft, dict) or not isinstance(wannier, dict) or not isinstance(comparison, dict):
        raise InputError("dft, wannier, and comparison must be JSON objects")
    if dft.get("spin_mode") != "collinear-ispin2":
        raise InputError("dft.spin_mode must be collinear-ispin2")
    fermi = dft.get("fermi_eV")
    if isinstance(fermi, bool) or not isinstance(fermi, (int, float)) or not math.isfinite(float(fermi)):
        raise InputError("dft.fermi_eV must be finite and explicit")
    fermi = float(fermi)
    expected_dft_bands = dft.get("expected_bands")
    if not isinstance(expected_dft_bands, int) or expected_dft_bands <= 0:
        raise InputError("dft.expected_bands must be a positive integer")

    kpoints_path = _resolve(base, dft.get("kpoints"), "dft.kpoints")
    fermi_source_path = _resolve(base, dft.get("fermi_source"), "dft.fermi_source")
    kpoints_file = _file_record(kpoints_path, "dft.kpoints")
    fermi_source = _file_record(fermi_source_path, "dft.fermi_source")
    parsed_fermi = _last_outcar_fermi(fermi_source_path)
    if parsed_fermi is None:
        raise InputError("dft.fermi_source has no E-fermi record")
    if abs(parsed_fermi - fermi) > 1.0e-6:
        raise InputError(f"dft.fermi_eV={fermi} differs from the last source E-fermi={parsed_fermi}")
    kpath = read_kpoints_segments(kpoints_path)

    display_window = _number_pair(comparison.get("display_window_relative_eV"), "comparison.display_window_relative_eV")
    metric_window = _number_pair(comparison.get("metric_window_relative_eV"), "comparison.metric_window_relative_eV")
    allow_lineage = comparison.get("allow_lineage_mismatch_for_diagnostic", False)
    if not isinstance(allow_lineage, bool):
        raise InputError("comparison.allow_lineage_mismatch_for_diagnostic must be boolean")
    band_offset = comparison.get("dft_band_offset", "auto")
    if band_offset != "auto" and (not isinstance(band_offset, int) or band_offset < 0):
        raise InputError("comparison.dft_band_offset must be auto or a non-negative integer")

    dft_bands = dft.get("bands")
    if not isinstance(dft_bands, dict) or set(dft_bands) != set(CHANNELS):
        raise InputError("dft.bands must contain exactly spin_up and spin_down")
    if set(wannier) != set(CHANNELS):
        raise InputError("wannier must contain exactly spin_up and spin_down")

    channels: dict[str, object] = {}
    warnings: list[str] = []
    seednames: set[str] = set()
    for channel in CHANNELS:
        dft_path = _resolve(base, dft_bands[channel], f"dft.bands.{channel}")
        dft_file = _file_record(dft_path, f"dft.bands.{channel}")
        dft_x, dft_energy = read_two_column_blocks(dft_path)
        dft_segments = reset_segments(dft_x)
        if dft_energy.shape[1] != expected_dft_bands:
            raise InputError(
                f"{channel}: DFT band count {dft_energy.shape[1]} != expected {expected_dft_bands}"
            )
        if len(dft_segments) != int(kpath["segments"]):
            raise InputError(
                f"{channel}: DFT segment count {len(dft_segments)} != KPOINTS {kpath['segments']}"
            )

        spec = wannier[channel]
        if not isinstance(spec, dict):
            raise InputError(f"wannier.{channel} must be an object")
        seedname = spec.get("seedname")
        if not isinstance(seedname, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", seedname):
            raise InputError(f"wannier.{channel}.seedname is invalid")
        if seedname in seednames:
            raise InputError("spin channels must use distinct seedname values")
        seednames.add(seedname)
        expected_num_wann = spec.get("expected_num_wann")
        if not isinstance(expected_num_wann, int) or expected_num_wann <= 0:
            raise InputError(f"wannier.{channel}.expected_num_wann must be positive")

        files: dict[str, object] = {}
        paths: dict[str, Path] = {}
        for name in ("win", "wout", "band", "labelinfo"):
            paths[name] = _resolve(base, spec.get(name), f"wannier.{channel}.{name}")
            files[name] = _file_record(paths[name], f"wannier.{channel}.{name}")
        checkpoint, checkpoint_status = _checkpoint_record(
            base, spec.get("checkpoint"), f"wannier.{channel}.checkpoint"
        )
        files["checkpoint"] = checkpoint
        win = parse_win(paths["win"])
        wout = parse_wout(paths["wout"])
        lineage = _lineage(win, wout, checkpoint_status)
        if win.get("num_wann") != expected_num_wann or wout.get("num_wann") != expected_num_wann:
            raise InputError(f"{channel}: expected_num_wann disagrees with win/wout")

        w90_x, w90_energy = read_two_column_blocks(paths["band"])
        records = read_labelinfo(paths["labelinfo"])
        w90_segments = label_segments(records)
        if w90_energy.shape[1] != expected_num_wann:
            raise InputError(
                f"{channel}: Wannier band count {w90_energy.shape[1]} != expected {expected_num_wann}"
            )
        if len(w90_segments) != int(kpath["segments"]):
            raise InputError(
                f"{channel}: Wannier segment count {len(w90_segments)} != KPOINTS {kpath['segments']}"
            )
        if w90_segments[-1][1] >= w90_energy.shape[0]:
            raise InputError(f"{channel}: labelinfo index exceeds Wannier band rows")
        label_hash = _label_path_hash(records, int(kpath["points_per_segment"]))
        if label_hash != kpath["path_hash"]:
            raise InputError(
                f"{channel}: KPOINTS and labelinfo path labels differ; do not invent or silently remap a path"
            )
        if lineage["status"] != "MATCH":
            message = f"{channel}: checkpoint lineage is {lineage['status']}"
            if not allow_lineage:
                raise InputError(message + "; set explicit diagnostic override only for a bounded comparison")
            warnings.append(message + " (explicit diagnostic override)")
        channels[channel] = {
            "label": spec.get("label", channel.replace("_", " ").title()),
            "seedname": seedname,
            "expected_num_wann": expected_num_wann,
            "files": files,
            "shape": {
                "dft_points": int(dft_energy.shape[0]),
                "dft_bands": int(dft_energy.shape[1]),
                "wannier_points": int(w90_energy.shape[0]),
                "wannier_bands": int(w90_energy.shape[1]),
                "segments": len(dft_segments),
            },
            "path_hash": label_hash,
            "lineage": lineage,
        }

    output_dir = _resolve(base, request.get("output_dir"), "output_dir")
    return {
        "status": "VALID_WITH_WARNINGS" if warnings else "VALID",
        "schema": SCHEMA,
        "request_dir": str(base),
        "output_dir": str(output_dir),
        "dft": {
            "spin_mode": dft["spin_mode"],
            "fermi_eV": fermi,
            "fermi_source": fermi_source,
            "kpoints": kpoints_file,
            "k_path": kpath,
            "expected_bands": expected_dft_bands,
        },
        "comparison": {
            "display_window_relative_eV": list(display_window),
            "metric_window_relative_eV": list(metric_window),
            "dft_band_offset": band_offset,
            "allow_lineage_mismatch_for_diagnostic": allow_lineage,
            "metric_definition": "Pointwise DFT-minus-Wannier residual after segment-local interpolation and one common order-preserving DFT band offset; both paired energies must lie in the metric window.",
        },
        "channels": channels,
        "warnings": warnings,
        "scientific_boundary": "A diagnostic band match does not prove Wannier convergence or authorize attribution to a mismatched current win file.",
    }


def _channel_arrays(checked: dict[str, object], channel: str) -> dict[str, object]:
    entry = checked["channels"][channel]
    files = entry["files"]
    # Resolved band paths are already bound into the file records.
    dft_file = checked["raw_request"]["dft"]["bands"][channel]
    base = Path(checked["request_dir"])
    dft_path = _resolve(base, dft_file, f"dft.bands.{channel}")
    w90_path = Path(files["band"]["path"])
    label_path = Path(files["labelinfo"]["path"])
    dft_x, dft_energy = read_two_column_blocks(dft_path)
    w90_x, w90_energy = read_two_column_blocks(w90_path)
    records = read_labelinfo(label_path)
    dft_segments = reset_segments(dft_x)
    return {
        "dft_x": dft_x,
        "dft_plot_x": continuous_plot_x(dft_x, dft_segments),
        "dft_energy": dft_energy,
        "dft_segments": dft_segments,
        "w90_x": w90_x,
        "w90_energy": w90_energy,
        "w90_segments": label_segments(records),
        "records": records,
    }


def _interpolated_pairs(arrays: dict[str, object]) -> tuple[np.ndarray, np.ndarray]:
    dft_parts: list[np.ndarray] = []
    w90_parts: list[np.ndarray] = []
    for dft_part, w90_part in zip(arrays["dft_segments"], arrays["w90_segments"]):
        d0, d1 = dft_part
        w0, w1 = w90_part
        dft_segment_x = arrays["dft_x"][d0 : d1 + 1]
        w90_segment_x = arrays["w90_x"][w0 : w1 + 1]
        if dft_segment_x[-1] <= dft_segment_x[0] or w90_segment_x[-1] <= w90_segment_x[0]:
            raise InputError("segment abscissa must have positive span")
        dfrac = (dft_segment_x - dft_segment_x[0]) / (dft_segment_x[-1] - dft_segment_x[0])
        wfrac = (w90_segment_x - w90_segment_x[0]) / (w90_segment_x[-1] - w90_segment_x[0])
        if not np.all(np.diff(dfrac) > 0.0) or not np.all(np.diff(wfrac) > 0.0):
            raise InputError("segment fractions must be strictly increasing")
        dft_interp = np.empty((len(wfrac), arrays["dft_energy"].shape[1]), dtype=float)
        for band in range(arrays["dft_energy"].shape[1]):
            dft_interp[:, band] = np.interp(
                wfrac, dfrac, arrays["dft_energy"][d0 : d1 + 1, band]
            )
        dft_parts.append(dft_interp)
        w90_parts.append(arrays["w90_energy"][w0 : w1 + 1, :])
    return np.vstack(dft_parts), np.vstack(w90_parts)


def _offset_metrics(
    dft: np.ndarray,
    w90: np.ndarray,
    fermi: float,
    window: tuple[float, float],
    requested_offset: int | str,
) -> dict[str, object]:
    max_offset = dft.shape[1] - w90.shape[1]
    if max_offset < 0:
        raise InputError("Wannier band count exceeds DFT band count")
    offsets = range(max_offset + 1) if requested_offset == "auto" else [int(requested_offset)]
    candidates: list[dict[str, object]] = []
    for offset in offsets:
        if offset > max_offset:
            raise InputError(f"DFT band offset {offset} exceeds maximum {max_offset}")
        selected = dft[:, offset : offset + w90.shape[1]]
        mask = (
            (selected - fermi >= window[0])
            & (selected - fermi <= window[1])
            & (w90 - fermi >= window[0])
            & (w90 - fermi <= window[1])
        )
        if not np.any(mask):
            continue
        residual = (w90 - selected)[mask]
        candidates.append(
            {
                "offset": offset,
                "paired_values": int(residual.size),
                "mae_meV": float(np.mean(np.abs(residual)) * 1000.0),
                "rmse_meV": float(np.sqrt(np.mean(residual**2)) * 1000.0),
                "max_abs_diff_meV": float(np.max(np.abs(residual)) * 1000.0),
            }
        )
    if not candidates:
        raise InputError("no paired DFT/Wannier energies lie in the requested metric window")
    selected_metric = min(candidates, key=lambda item: (item["rmse_meV"], item["offset"]))
    offset = int(selected_metric["offset"])
    selected = dft[:, offset : offset + w90.shape[1]]
    per_band: list[dict[str, object]] = []
    for band in range(w90.shape[1]):
        mask = (
            (selected[:, band] - fermi >= window[0])
            & (selected[:, band] - fermi <= window[1])
            & (w90[:, band] - fermi >= window[0])
            & (w90[:, band] - fermi <= window[1])
        )
        if not np.any(mask):
            continue
        residual = w90[mask, band] - selected[mask, band]
        per_band.append(
            {
                "wannier_band_1_based": band + 1,
                "dft_band_1_based": band + offset + 1,
                "paired_points": int(residual.size),
                "mae_meV": float(np.mean(np.abs(residual)) * 1000.0),
                "rmse_meV": float(np.sqrt(np.mean(residual**2)) * 1000.0),
                "max_abs_diff_meV": float(np.max(np.abs(residual)) * 1000.0),
            }
        )
    return {**selected_metric, "selection": "minimum RMSE over valid order-preserving offsets", "per_band": per_band}


def _ticks(arrays: dict[str, object]) -> tuple[list[float], list[str]]:
    plot_x = arrays.get("dft_plot_x", arrays["dft_x"])
    index_to_x: dict[int, float] = {}
    for dft_part, w90_part in zip(arrays["dft_segments"], arrays["w90_segments"]):
        index_to_x[w90_part[0]] = float(plot_x[dft_part[0]])
        index_to_x[w90_part[1]] = float(plot_x[dft_part[1]])
    ticks: list[float] = []
    labels: list[str] = []
    for record in arrays["records"]:
        index = int(record["index"])
        if index not in index_to_x:
            continue
        xvalue = index_to_x[index]
        label = _display_label(str(record["label"]))
        if ticks and abs(xvalue - ticks[-1]) <= 2.0e-7:
            if label not in labels[-1].split("|"):
                labels[-1] += "|" + label
        else:
            ticks.append(xvalue)
            labels.append(label)
    return ticks, [item.replace("|", "\n") for item in labels]


def require_matplotlib_panel_alignment(fig, axes: list[plt.Axes], output: Path) -> dict[str, object]:
    """Measure equal-row axes geometry and block misaligned export."""

    fig.canvas.draw()
    width_pt, height_pt = (fig.get_size_inches() * 72.0).tolist()
    panels: list[dict[str, object]] = []
    for index, ax in enumerate(axes):
        bbox = ax.get_position()
        panels.append(
            {
                "id": chr(ord("a") + index),
                "bbox_pt": [bbox.x0 * width_pt, bbox.y0 * height_pt, bbox.x1 * width_pt, bbox.y1 * height_pt],
                "grid_id": "spin-grid",
                "row_start": 0,
                "row_stop": 1,
                "col_start": index,
                "col_stop": index + 1,
            }
        )
    bottoms = [float(item["bbox_pt"][1]) for item in panels]
    tops = [float(item["bbox_pt"][3]) for item in panels]
    heights = [top - bottom for top, bottom in zip(tops, bottoms)]
    tolerance = 1.5
    if max(bottoms) - min(bottoms) > tolerance or max(tops) - min(tops) > tolerance:
        raise InputError("rendered spin panels fail the 1.5 pt vertical-alignment gate")
    if max(heights) - min(heights) > tolerance:
        raise InputError("rendered spin panels fail the equal-height gate")
    payload = {
        "schema_version": 1,
        "backend": "python-matplotlib",
        "figure": {"width_pt": width_pt, "height_pt": height_pt},
        "panels": panels,
        "row_groups": [{"id": "spin-row", "panels": ["a", "b"]}],
        "column_groups": [],
        "exemptions": [],
        "internal_verdict": "PASS",
        "tolerance_pt": tolerance,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _plot(
    checked: dict[str, object],
    arrays_by_channel: dict[str, dict[str, object]],
    output_dir: Path,
) -> dict[str, str]:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 7.0,
            "axes.linewidth": 0.65,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "legend.frameon": False,
            "pdf.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    fig, axes_array = plt.subplots(1, 2, figsize=(7.2047, 3.35), sharey=True)
    axes = list(np.ravel(axes_array))
    colors = {"spin_up": "#c44e52", "spin_down": "#4c72b0"}
    fermi = float(checked["dft"]["fermi_eV"])
    ylim = tuple(checked["comparison"]["display_window_relative_eV"])
    for index, (channel, ax) in enumerate(zip(CHANNELS, axes)):
        arrays = arrays_by_channel[channel]
        plot_x = arrays["dft_plot_x"]
        for start, end in arrays["dft_segments"]:
            for band in range(arrays["dft_energy"].shape[1]):
                ax.plot(
                    plot_x[start : end + 1],
                    arrays["dft_energy"][start : end + 1, band] - fermi,
                    color="#555555",
                    linewidth=0.35,
                    alpha=0.62,
                    rasterized=True,
                    zorder=1,
                )
        for dft_part, w90_part in zip(arrays["dft_segments"], arrays["w90_segments"]):
            d0, d1 = dft_part
            w0, w1 = w90_part
            source_x = arrays["w90_x"][w0 : w1 + 1]
            if source_x[-1] <= source_x[0] or not np.all(np.diff(source_x) > 0.0):
                raise InputError(f"{channel}: Wannier segment is not strictly increasing")
            fraction = (source_x - source_x[0]) / (source_x[-1] - source_x[0])
            mapped_x = float(plot_x[d0]) + fraction * (
                float(plot_x[d1]) - float(plot_x[d0])
            )
            for band in range(arrays["w90_energy"].shape[1]):
                ax.plot(
                    mapped_x,
                    arrays["w90_energy"][w0 : w1 + 1, band] - fermi,
                    color=colors[channel],
                    linewidth=0.55,
                    alpha=0.92,
                    rasterized=True,
                    zorder=2,
                )
        ticks, labels = _ticks(arrays)
        ax.set_xticks(ticks, labels)
        for tick in ticks[1:-1]:
            ax.axvline(tick, color="#8c8c8c", linewidth=0.35, alpha=0.55, zorder=0)
        ax.axhline(0.0, color="#222222", linewidth=0.55, linestyle=(0, (3, 2)), zorder=3)
        ax.set_xlim(float(plot_x[0]), float(plot_x[-1]))
        ax.set_ylim(*ylim)
        ax.set_title(str(checked["channels"][channel]["label"]), fontsize=8.0, pad=3.5, color=colors[channel])
        ax.text(-0.105, 1.025, chr(ord("a") + index), transform=ax.transAxes, fontsize=8.0, fontweight="bold")
        ax.tick_params(axis="both", labelsize=6.5, length=2.5, width=0.55, pad=1.8)
    axes[0].set_ylabel("E \u2212 E_F (eV)", fontsize=7.5)
    fig.text(0.5, 0.035, "Wave vector", ha="center", fontsize=7.0)
    fig.legend(
        handles=[
            Line2D([0], [0], color="#555555", linewidth=0.9, label="VASP DFT"),
            Line2D([0], [0], color="#c44e52", linewidth=0.9, label="Wannier90 spin up"),
            Line2D([0], [0], color="#4c72b0", linewidth=0.9, label="Wannier90 spin down"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.975),
        ncol=3,
        fontsize=6.5,
        handlelength=1.5,
        columnspacing=1.2,
    )
    fig.subplots_adjust(left=0.083, right=0.99, bottom=0.17, top=0.82, wspace=0.055)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / "dft_wannier_spin_comparison"
    alignment_path = output_dir / "dft_wannier_spin_comparison.alignment-layout.json"
    require_matplotlib_panel_alignment(fig, axes, alignment_path)
    outputs = {
        "png": str(stem.with_suffix(".png")),
        "tiff": str(stem.with_suffix(".tiff")),
        "pdf": str(stem.with_suffix(".pdf")),
        "svg": str(stem.with_suffix(".svg")),
        "alignment_layout": str(alignment_path),
    }
    fig.savefig(outputs["png"], dpi=600, facecolor="white")
    fig.savefig(outputs["tiff"], dpi=600, facecolor="white", pil_kwargs={"compression": "tiff_lzw"})
    fig.savefig(outputs["pdf"], dpi=600, facecolor="white")
    fig.savefig(outputs["svg"], dpi=600, facecolor="white")
    plt.close(fig)
    return outputs


def run_compare_request(request: object, request_dir: Path | None = None) -> dict[str, object]:
    checked = check_compare_request(request, request_dir=request_dir)
    checked["raw_request"] = request
    arrays_by_channel: dict[str, dict[str, object]] = {}
    metrics: dict[str, object] = {}
    fermi = float(checked["dft"]["fermi_eV"])
    window = tuple(checked["comparison"]["metric_window_relative_eV"])
    requested_offset = checked["comparison"]["dft_band_offset"]
    for channel in CHANNELS:
        arrays = _channel_arrays(checked, channel)
        arrays_by_channel[channel] = arrays
        dft_interp, w90 = _interpolated_pairs(arrays)
        metrics[channel] = _offset_metrics(dft_interp, w90, fermi, window, requested_offset)
    output_dir = Path(checked["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    preflight = dict(checked)
    preflight.pop("raw_request", None)
    preflight_path = output_dir / "preflight.json"
    metrics_path = output_dir / "metrics.json"
    run_path = output_dir / "run.json"
    preflight_path.write_text(json.dumps(preflight, indent=2) + "\n", encoding="utf-8")
    metric_payload = {
        "schema": "fpilot.pyw90-comparison-metrics.v1",
        "energy_reference_eV": fermi,
        "metric_window_relative_eV": list(window),
        "channels": metrics,
        "boundary": checked["comparison"]["metric_definition"],
    }
    metrics_path.write_text(json.dumps(metric_payload, indent=2) + "\n", encoding="utf-8")
    outputs = _plot(checked, arrays_by_channel, output_dir)
    receipt = {
        "status": "OK_WITH_WARNINGS" if checked["warnings"] else "OK",
        "schema": SCHEMA,
        "preflight": str(preflight_path),
        "metrics": str(metrics_path),
        "outputs": outputs,
        "warnings": checked["warnings"],
        "scientific_boundary": checked["scientific_boundary"],
    }
    run_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt["run_receipt"] = str(run_path)
    return receipt


def _load(path: Path) -> tuple[object, Path]:
    resolved = path.expanduser().resolve()
    return json.loads(resolved.read_text(encoding="utf-8")), resolved.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or run a deterministic two-spin PyW90 comparison workflow")
    parser.add_argument("action", choices=("check", "run"))
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        request, request_dir = _load(args.request)
        result = (
            check_compare_request(request, request_dir=request_dir)
            if args.action == "check"
            else run_compare_request(request, request_dir=request_dir)
        )
    except (InputError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "ERROR", "errors": [str(exc)]}
    print(_json(result))
    return 0 if result.get("status") in {"VALID", "VALID_WITH_WARNINGS", "OK", "OK_WITH_WARNINGS"} else 1


if __name__ == "__main__":
    sys.exit(main())
