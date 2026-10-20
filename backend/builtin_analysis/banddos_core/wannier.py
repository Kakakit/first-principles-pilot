from __future__ import annotations

from pathlib import Path

import numpy as np

from .common import pretty_label
from .model import BandChannel


def read_labelinfo(path: Path) -> tuple[list[int], np.ndarray, list[str]]:
    indices: list[int] = []
    positions: list[float] = []
    labels: list[str] = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            idx = int(fields[1]) - 1
            pos = float(fields[2])
        except ValueError:
            continue
        indices.append(idx)
        positions.append(pos)
        labels.append(pretty_label(fields[0]))
    if len(indices) < 2:
        raise ValueError(f"No valid Wannier label records in {path}")
    return indices, np.asarray(positions), labels


def read_band_dat(path: Path, points_per_band: int) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        rows.append((float(fields[0]), float(fields[1])))
    if not rows or len(rows) % points_per_band:
        raise ValueError(f"Wannier band row count is not divisible by {points_per_band}: {path}")
    nb = len(rows) // points_per_band
    x = np.asarray([rows[i][0] for i in range(points_per_band)], dtype=float)
    energies = np.empty((points_per_band, nb), dtype=float)
    for ib in range(nb):
        block = rows[ib * points_per_band : (ib + 1) * points_per_band]
        if not np.allclose([r[0] for r in block], x, atol=1e-9):
            raise ValueError(f"Wannier bands do not share the same x-grid: {path}")
        energies[:, ib] = [r[1] for r in block]
    return x, energies


def wannier_channel(band_file: Path, label_file: Path, energy_reference_eV: float = 0.0, name: str | None = None) -> BandChannel:
    indices, positions, raw_labels = read_labelinfo(label_file)
    points_per_band = max(indices) + 1
    x, energies = read_band_dat(band_file, points_per_band)
    # Labelinfo can contain duplicated boundary positions for disconnected/restarted segments.
    segments = []
    ticks = [float(positions[0])]
    labels = [raw_labels[0]]
    record = 1
    while record < len(indices):
        left_idx = indices[record - 1]
        right_idx = indices[record]
        left_pos = positions[record - 1]
        right_pos = positions[record]
        if abs(right_pos - left_pos) <= 1e-12:
            if raw_labels[record] != labels[-1]:
                labels[-1] += "|" + raw_labels[record]
            record += 1
            continue
        segments.append((left_idx, right_idx))
        ticks.append(float(right_pos))
        labels.append(raw_labels[record])
        record += 1
    if not segments:
        segments = [(0, points_per_band - 1)]
        ticks = [float(x[0]), float(x[-1])]
        labels = [raw_labels[0], raw_labels[-1]]
    return BandChannel(
        name=name or band_file.stem,
        x=x,
        energies=energies - energy_reference_eV,
        segments=segments,
        ticks=np.asarray(ticks, dtype=float),
        labels=labels,
        metadata={"band_file": str(band_file), "label_file": str(label_file)},
    )


def discover_wannier_channels(directory: Path, seed: str = "wannier90", energy_reference_eV: float = 0.0) -> list[BandChannel]:
    candidates = []
    for spin in (1, 2):
        b = directory / f"{seed}.{spin}_band.dat"
        l = directory / f"{seed}.{spin}_band.labelinfo.dat"
        if b.exists() and l.exists():
            candidates.append(wannier_channel(b, l, energy_reference_eV, f"spin-{'up' if spin == 1 else 'down'}"))
    if candidates:
        return candidates
    b = directory / f"{seed}_band.dat"
    l = directory / f"{seed}_band.labelinfo.dat"
    if b.exists() and l.exists():
        return [wannier_channel(b, l, energy_reference_eV, "bands")]
    raise ValueError(f"No Wannier90 band/labelinfo pair found in {directory} for seed '{seed}'")
