#!/usr/bin/env python3
"""Plot the zero-weight SOC band path from a mixed-mesh VASP EIGENVAL."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "figures"
SKILL = Path("/Users/kafka/.agents/skills/scipilot-figure-skill/scripts")
sys.path.insert(0, str(SKILL))

from export_figure import export_figure
from setup_style import setup_style
from visual_qa import audit_layout, print_report, render_preview


UNIFORM_KPOINTS = 150
Y_LIMITS = (-4.0, 3.0)
LINE_COLOR = "#234E70"


def read_fermi(path: Path) -> float:
    values = re.findall(r"E-fermi\s*:\s*([-+0-9.Ee]+)", path.read_text(errors="ignore"))
    if not values:
        raise ValueError(f"No Fermi energy in {path}")
    return float(values[-1])


def read_eigenval(path: Path) -> tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    lines = path.read_text().splitlines()
    nelect, nkpts, nbands = map(int, lines[5].split()[:3])
    kpoints = np.empty((nkpts, 3))
    weights = np.empty(nkpts)
    energies = np.empty((nkpts, nbands))
    occupations = np.empty((nkpts, nbands))
    cursor = 6
    for ik in range(nkpts):
        while not lines[cursor].strip():
            cursor += 1
        fields = lines[cursor].split()
        kpoints[ik], weights[ik] = np.array(fields[:3], float), float(fields[3])
        cursor += 1
        for ib in range(nbands):
            fields = lines[cursor].split()
            energies[ik, ib], occupations[ik, ib] = float(fields[1]), float(fields[2])
            cursor += 1
    return nelect, kpoints, weights, energies, occupations


def read_path(path: Path) -> tuple[int, list[tuple[np.ndarray, str, np.ndarray, str]]]:
    lines = path.read_text().splitlines()
    npoints = int(lines[1])
    endpoints = []
    for line in lines[4:]:
        fields = line.split()
        if len(fields) >= 4:
            endpoints.append((np.array(fields[:3], float), fields[3]))
    segments = [(*endpoints[i], *endpoints[i + 1]) for i in range(0, len(endpoints), 2)]
    return npoints, segments


def reciprocal_lattice(path: Path) -> np.ndarray:
    lines = path.read_text().splitlines()
    scale = float(lines[1].split()[0])
    lattice = scale * np.array([lines[i].split()[:3] for i in range(2, 5)], float)
    return 2 * np.pi * np.linalg.inv(lattice).T


def label(text: str) -> str:
    return {"GAMMA": r"$\Gamma$", "S_0": r"$S_0$"}.get(text, rf"${text}$")


def make_axis(kpoints, reciprocal, segments, npoints):
    if len(kpoints) != len(segments) * npoints:
        raise ValueError("Path point count does not match KPATH.in")
    x = np.empty(len(kpoints))
    slices, boundaries, offset = [], [0.0], 0.0
    for i, (start, _, end, _) in enumerate(segments):
        sl = slice(i * npoints, (i + 1) * npoints)
        if not np.allclose(kpoints[sl][0], start, atol=2e-8) or not np.allclose(
            kpoints[sl][-1], end, atol=2e-8
        ):
            raise ValueError(f"Segment {i + 1} endpoints differ from KPATH.in")
        cart = kpoints[sl] @ reciprocal
        distance = np.r_[0.0, np.linalg.norm(np.diff(cart, axis=0), axis=1)]
        x[sl] = offset + np.cumsum(distance)
        offset = x[sl][-1]
        boundaries.append(offset)
        slices.append(sl)
    return x, slices, np.array(boundaries)


def band_edges(energies, occupations):
    occupied = occupations > 0.5
    occupied_energy = np.where(occupied, energies, -np.inf)
    empty_energy = np.where(~occupied, energies, np.inf)
    vbm = float(np.max(occupied_energy))
    cbm = float(np.min(empty_energy))
    direct = np.min(empty_energy, axis=1) - np.max(occupied_energy, axis=1)
    return vbm, cbm, cbm - vbm, float(np.min(direct))


def write_data(x, energies, occupations, fermi, npoints, gap, direct_gap):
    PROCESSED.mkdir(parents=True, exist_ok=True)
    with (PROCESSED / "soc_band_path_50.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path_point", "distance_1_per_A", "band", "energy_minus_Ef_eV", "occupation"])
        for ik in range(len(x)):
            for ib in range(energies.shape[1]):
                writer.writerow([ik + 1, f"{x[ik]:.10f}", ib + 1, f"{energies[ik, ib]:.8f}", f"{occupations[ik, ib]:.6f}"])
    (PROCESSED / "soc_band_summary_50.txt").write_text(
        f"points_per_segment = {npoints}\n"
        f"path_points = {len(x)}\n"
        f"bands = {energies.shape[1]}\n"
        f"band_run_fermi_eV = {fermi:.6f}\n"
        f"indirect_gap_eV = {gap:.6f}\n"
        f"minimum_direct_gap_eV = {direct_gap:.6f}\n"
    )


def main() -> None:
    setup_style(journal="nature", lang="en")
    plt.rcParams.update({"axes.spines.top": True, "axes.spines.right": True})
    fermi = read_fermi(RAW / "BAND50_OUTCAR")
    nelect, all_k, weights, all_e, all_occ = read_eigenval(RAW / "EIGENVAL_band50")
    npoints, segments = read_path(ROOT / "inputs" / "KPATH_50.in")
    expected = len(segments) * npoints
    if len(all_k) != UNIFORM_KPOINTS + expected:
        raise ValueError("Unexpected uniform/path partition")
    if not np.all(weights[:UNIFORM_KPOINTS] > 0) or not np.allclose(weights[UNIFORM_KPOINTS:], 0):
        raise ValueError("K-point weights do not identify the expected zero-weight path")

    kpoints = all_k[UNIFORM_KPOINTS:]
    energies = all_e[UNIFORM_KPOINTS:] - fermi
    occupations = all_occ[UNIFORM_KPOINTS:]
    x, slices, boundaries = make_axis(
        kpoints, reciprocal_lattice(RAW / "POSCAR_SOC_relaxed"), segments, npoints
    )
    vbm, cbm, gap, direct_gap = band_edges(energies, occupations)
    write_data(x, energies, occupations, fermi, npoints, gap, direct_gap)

    main_end = 6
    widths = [boundaries[main_end] - boundaries[0]] + [
        boundaries[i + 1] - boundaries[i] for i in range(main_end, len(segments))
    ]
    fig, axes = plt.subplots(
        1, 4, figsize=(7.2, 4.35), sharey=True, constrained_layout=True,
        gridspec_kw={"width_ratios": widths, "wspace": 0.045},
    )
    for sl in slices[:main_end]:
        axes[0].plot(x[sl], energies[sl], color=LINE_COLOR, linewidth=0.52, alpha=0.84)
    for boundary in boundaries[1:main_end]:
        axes[0].axvline(boundary, color="#C7CDD3", linewidth=0.5, zorder=0)
    axes[0].set_xlim(boundaries[0], boundaries[main_end])
    axes[0].set_xticks(boundaries[: main_end + 1])
    axes[0].set_xticklabels([label(segments[0][1])] + [label(s[3]) for s in segments[:main_end]])
    axes[0].set_ylabel(r"$E-E_\mathrm{F}$ (eV)")
    axes[0].text(
        0.72, 0.62,
        rf"$E_\mathrm{{g}}^{{\mathrm{{ind}}}}={gap:.3f}$ eV" + "\n" +
        rf"$E_\mathrm{{g}}^{{\mathrm{{dir,min}}}}={direct_gap:.3f}$ eV",
        transform=axes[0].transAxes, ha="center", va="center", fontsize=7,
    )
    for i, ax in enumerate(axes[1:], start=main_end):
        sl = slices[i]
        local_x = x[sl] - x[sl][0]
        ax.plot(local_x, energies[sl], color=LINE_COLOR, linewidth=0.52, alpha=0.84)
        ax.set_xlim(local_x[0], local_x[-1])
        ax.set_xticks([local_x[0], local_x[-1]])
        ax.set_xticklabels([label(segments[i][1]), label(segments[i][3])])
        ax.tick_params(axis="y", left=False, labelleft=False)
    for ax in axes:
        ax.set_ylim(*Y_LIMITS)
        ax.axhline(0, color="#D55E00", linewidth=0.9, linestyle="--", zorder=3)
        ax.tick_params(axis="x", length=0, pad=4)

    FIGURES.mkdir(parents=True, exist_ok=True)
    render_preview(fig, str(FIGURES / "soc_band_50_preview.png"), dpi=180)
    verdict = print_report(audit_layout(fig))
    if verdict == "FAIL":
        raise RuntimeError("Visual QA failed")
    basename = str(FIGURES / "FeCr2O4_DFTU_SOC_band_50")
    export_figure(
        fig, basename, formats=["pdf", "svg", "png"], size_inches=(7.2, 4.35),
        dpi=600, grayscale_preview=False, tight=False,
    )
    Image.open(f"{basename}.png").convert("L").save(
        f"{basename}_grayscale.png", dpi=(600, 600)
    )
    plt.close(fig)
    print(f"NELECT={nelect}; path={len(kpoints)}; bands={energies.shape[1]}")
    print(f"E_F={fermi:.6f} eV; VBM={vbm:.6f}; CBM={cbm:.6f}")
    print(f"indirect gap={gap:.6f} eV; minimum direct gap={direct_gap:.6f} eV")


if __name__ == "__main__":
    main()
