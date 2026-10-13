#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt


LABELS = {
    "GAMMA": r"$\Gamma$",
    "S_0": r"$S_0$",
}


def read_bands(path: Path, points_per_band: int) -> tuple[list[float], list[list[float]]]:
    rows = [
        tuple(map(float, line.split()[:2]))
        for line in path.read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    if len(rows) % points_per_band:
        raise ValueError(f"{path}: row count is not divisible by {points_per_band}")
    bands = []
    for start in range(0, len(rows), points_per_band):
        block = rows[start : start + points_per_band]
        bands.append([energy for _, energy in block])
    return [x for x, _ in rows[:points_per_band]], bands


def read_labels(path: Path) -> tuple[list[float], list[str], list[int], int]:
    raw = []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) >= 3:
            raw.append((float(fields[2]), LABELS.get(fields[0], fields[0]), int(fields[1])))

    positions: list[float] = []
    labels: list[str] = []
    segment_starts = [0]
    for position, label, point_index in raw:
        if positions and abs(position - positions[-1]) < 1e-8:
            if label != labels[-1]:
                labels[-1] += "|" + label
            segment_starts.append(point_index - 1)
        else:
            positions.append(position)
            labels.append(label)
    return positions, labels, segment_starts, max(row[2] for row in raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot spin-resolved Wannier90 bands.")
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--fermi",
        type=float,
        default=5.6635,
        help="Fermi energy subtracted from Wannier90 eigenvalues (eV).",
    )
    parser.add_argument("--emin", type=float, default=-3.0)
    parser.add_argument("--emax", type=float, default=5.0)
    parser.add_argument("--output", default="wannier_bands_spin_resolved")
    args = parser.parse_args()

    directory = args.directory.resolve()
    labels_x, labels, segment_starts, points_per_band = read_labels(
        directory / "wannier90.1_band.labelinfo.dat"
    )
    spin_data = []
    for spin in (1, 2):
        label_path = directory / f"wannier90.{spin}_band.labelinfo.dat"
        channel_x, channel_labels, channel_starts, channel_points = read_labels(label_path)
        if (
            channel_labels != labels
            or channel_starts != segment_starts
            or channel_points != points_per_band
            or any(abs(a - b) > 1e-7 for a, b in zip(channel_x, labels_x))
        ):
            raise ValueError("Spin channels use different k paths")
        spin_data.append(read_bands(directory / f"wannier90.{spin}_band.dat", points_per_band))

    figure, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), sharex=True, sharey=True)
    colors = ("#b3262e", "#225ea8")
    titles = ("Spin up", "Spin down")
    segment_ends = segment_starts[1:] + [points_per_band]
    for axis, (x, bands), color, title in zip(axes, spin_data, colors, titles):
        for band in bands:
            shifted = [energy - args.fermi for energy in band]
            for start, end in zip(segment_starts, segment_ends):
                axis.plot(x[start:end], shifted[start:end], color=color, lw=0.75)
        for position in labels_x[1:-1]:
            axis.axvline(position, color="#8a8a8a", lw=0.55, alpha=0.55)
        axis.axhline(0.0, color="#202020", lw=0.8, ls="--")
        axis.set_ylim(args.emin, args.emax)
        axis.set_xlim(labels_x[0], labels_x[-1])
        axis.text(
            0.015,
            0.94,
            title,
            transform=axis.transAxes,
            ha="left",
            va="top",
            color=color,
            fontsize=11,
            fontweight="bold",
        )
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

        axis.set_xticks(labels_x, labels)
        axis.tick_params(axis="x", labelsize=8)
    axes[0].set_ylabel(r"$E-E_F$ (eV)")
    figure.suptitle("FeCr$_2$O$_4$ Wannier-interpolated bands", y=0.99, fontsize=13)
    figure.tight_layout(rect=(0, 0, 1, 0.96), w_pad=1.2)
    for extension in ("png", "pdf"):
        output = directory / f"{args.output}.{extension}"
        figure.savefig(output, dpi=300, bbox_inches="tight")
        print(output)
    plt.close(figure)


if __name__ == "__main__":
    main()
