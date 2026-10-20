from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Iterable

import numpy as np


LABEL_MAP = {
    "GAMMA": "Γ",
    "Gamma": "Γ",
    "gamma": "Γ",
    "S_0": "S0",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pretty_label(label: str) -> str:
    label = label.strip().replace("Γ", "GAMMA")
    if label in LABEL_MAP:
        return LABEL_MAP[label]
    if label.startswith("$") and label.endswith("$"):
        return label
    return label.replace("_0", "0").replace("_1", "1").replace("_2", "2")


def read_fermi_from_outcar(path: Path) -> float:
    text = path.read_text(errors="ignore")
    values = re.findall(r"E-fermi\s*:\s*([-+0-9.Ee]+)", text)
    if not values:
        raise ValueError(f"No E-fermi entry found in {path}")
    return float(values[-1])


def reciprocal_from_poscar(path: Path) -> np.ndarray:
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 5:
        raise ValueError(f"Invalid POSCAR: {path}")
    scale_fields = lines[1].split()
    if len(scale_fields) != 1:
        raise ValueError("Only the common single POSCAR scale factor is supported")
    scale = float(scale_fields[0])
    lattice = np.array([[float(x) for x in lines[i].split()[:3]] for i in range(2, 5)])
    if scale > 0:
        lattice *= scale
    elif scale < 0:
        target = abs(scale)
        raw_volume = abs(np.linalg.det(lattice))
        lattice *= (target / raw_volume) ** (1.0 / 3.0)
    else:
        raise ValueError("POSCAR scale factor cannot be zero")
    return 2.0 * np.pi * np.linalg.inv(lattice).T


def species_ion_indices_from_poscar(path: Path) -> dict[str, list[int]]:
    """Return 1-based ion indices grouped by VASP5/6 POSCAR species order."""
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 7:
        raise ValueError(f"Invalid POSCAR: {path}")
    species = lines[5].split()
    try:
        [float(token) for token in species]
    except ValueError:
        pass
    else:
        raise ValueError("Species-aware fatband panels require VASP5/6 POSCAR symbols")
    try:
        counts = [int(token) for token in lines[6].split()]
    except ValueError as exc:
        raise ValueError(f"Cannot parse POSCAR species counts: {path}") from exc
    if len(species) != len(counts) or any(count <= 0 for count in counts):
        raise ValueError("POSCAR species/count rows are inconsistent")
    result: dict[str, list[int]] = {}
    start = 1
    for symbol, count in zip(species, counts):
        if symbol in result:
            raise ValueError(f"POSCAR repeats species symbol {symbol!r}")
        result[symbol] = list(range(start, start + count))
        start += count
    return result


def parse_kpoint_endpoint(line: str) -> tuple[np.ndarray, str] | None:
    raw = line.strip()
    if not raw:
        return None
    before, bang, after = raw.partition("!")
    fields = before.split()
    if len(fields) < 3:
        return None
    try:
        coord = np.array([float(fields[0]), float(fields[1]), float(fields[2])], dtype=float)
    except ValueError:
        return None
    label = after.strip().split()[0] if bang and after.strip() else (fields[3] if len(fields) >= 4 else "")
    return coord, label


def read_line_mode_kpoints(path: Path) -> tuple[int, list[tuple[np.ndarray, str, np.ndarray, str]]]:
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 5:
        raise ValueError(f"KPOINTS/KPATH file too short: {path}")
    try:
        npoints = int(lines[1].split()[0])
    except Exception as exc:
        raise ValueError(f"Cannot read points-per-segment from {path}") from exc
    endpoints = [item for line in lines[4:] if (item := parse_kpoint_endpoint(line)) is not None]
    if len(endpoints) < 2 or len(endpoints) % 2 != 0:
        raise ValueError(f"Expected paired line-mode endpoints in {path}")
    segments = [(*endpoints[i], *endpoints[i + 1]) for i in range(0, len(endpoints), 2)]
    return npoints, segments


def contiguous_segments_from_reset(x: np.ndarray, atol: float = 1e-10) -> list[tuple[int, int]]:
    if len(x) == 0:
        return []
    starts = [0]
    starts.extend(i for i in range(1, len(x)) if x[i] <= x[i - 1] + atol)
    ends = [s - 1 for s in starts[1:]] + [len(x) - 1]
    return list(zip(starts, ends))


def axis_from_kpath(
    kpoints: np.ndarray,
    segments_spec: list[tuple[np.ndarray, str, np.ndarray, str]],
    npoints: int,
    reciprocal: np.ndarray | None,
    atol: float = 2e-7,
) -> tuple[np.ndarray, list[tuple[int, int]], np.ndarray, list[str]]:
    expected = len(segments_spec) * npoints
    if len(kpoints) != expected:
        raise ValueError(f"Path contains {len(kpoints)} k-points but KPOINTS defines {expected}")
    x = np.empty(expected, dtype=float)
    segs: list[tuple[int, int]] = []
    ticks = [0.0]
    labels = [pretty_label(segments_spec[0][1] or "K0")]
    offset = 0.0
    for i, (start, start_label, end, end_label) in enumerate(segments_spec):
        if i:
            previous_end_label = segments_spec[i - 1][3] or f"K{i}"
            current_start_label = start_label or f"K{i}"
            if previous_end_label != current_start_label:
                # Independent line-mode segments share one plotting x position.
                # Preserve both endpoint identities instead of silently dropping
                # the start label of the new, discontinuous segment.
                labels[-1] = (
                    f"{pretty_label(previous_end_label)}|"
                    f"{pretty_label(current_start_label)}"
                )
        lo, hi = i * npoints, (i + 1) * npoints
        actual = kpoints[lo:hi]
        if not np.allclose(actual[0], start, atol=atol) or not np.allclose(actual[-1], end, atol=atol):
            raise ValueError(f"Segment {i + 1} endpoints differ from KPOINTS/KPATH")
        coords = actual if reciprocal is None else actual @ reciprocal
        d = np.r_[0.0, np.linalg.norm(np.diff(coords, axis=0), axis=1)]
        local = np.cumsum(d)
        if local[-1] <= 1e-14:
            local = np.linspace(0.0, 1.0, len(local))
        x[lo:hi] = offset + local
        offset = x[hi - 1]
        ticks.append(offset)
        labels.append(pretty_label(end_label or f"K{i+1}"))
        segs.append((lo, hi - 1))
    return x, segs, np.asarray(ticks), labels


def parse_int_ranges(text: str, maximum: int | None = None) -> list[int]:
    text = text.strip().lower()
    if text in {"all", "*"}:
        if maximum is None:
            raise ValueError("'all' requires a known maximum")
        return list(range(1, maximum + 1))
    result: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
            if end < start:
                start, end = end, start
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    if not result:
        raise ValueError("No indices selected")
    if maximum is not None and (min(result) < 1 or max(result) > maximum):
        raise ValueError(f"Index selection exceeds 1..{maximum}")
    return sorted(result)


def load_numeric_table(path: Path, min_columns: int = 2) -> np.ndarray:
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "!", ";")):
            continue
        try:
            row = [float(v) for v in s.replace(",", " ").split()]
        except ValueError:
            continue
        if len(row) >= min_columns:
            rows.append(row)
    if not rows:
        raise ValueError(f"No numeric rows found in {path}")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError(f"Inconsistent numeric column count in {path}")
    return np.asarray(rows, dtype=float)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
