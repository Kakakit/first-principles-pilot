#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt


LABELS = {"GAMMA": r"$\Gamma$", "S_0": r"$S_0$"}


def read_dft(path: Path) -> tuple[list[float], list[list[float]]]:
    rows = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        try:
            rows.append([float(value) for value in line.split()])
        except ValueError:
            continue
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f"Invalid DFT band matrix: {path}")
    return [row[0] for row in rows], [row[1:] for row in rows]


def read_wannier(path: Path, points_per_band: int) -> tuple[list[float], list[list[float]]]:
    rows = [
        tuple(map(float, line.split()[:2]))
        for line in path.read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    if len(rows) % points_per_band:
        raise ValueError(f"Invalid Wannier90 band data: {path}")
    x = [value[0] for value in rows[:points_per_band]]
    bands = []
    for start in range(0, len(rows), points_per_band):
        bands.append([value[1] for value in rows[start : start + points_per_band]])
    return x, bands


def read_wannier_labels(path: Path) -> tuple[list[int], list[float], list[str]]:
    indices, positions, labels = [], [], []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        indices.append(int(fields[1]) - 1)
        positions.append(float(fields[2]))
        labels.append(LABELS.get(fields[0], fields[0]))
    if len(indices) < 2:
        raise ValueError(f"No path labels found: {path}")
    return indices, positions, labels


def dft_segments(x: list[float]) -> list[tuple[int, int]]:
    starts = [0]
    starts.extend(i for i in range(1, len(x)) if x[i] <= x[i - 1] + 1e-10)
    ends = [start - 1 for start in starts[1:]] + [len(x) - 1]
    return list(zip(starts, ends))


def path_layout(
    dft_x: list[float], label_path: Path
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[float], list[str]]:
    dft_parts = dft_segments(dft_x)
    indices, positions, raw_labels = read_wannier_labels(label_path)
    wannier_parts = [
        (left_index, right_index)
        for left_index, right_index, left_x, right_x in zip(
            indices, indices[1:], positions, positions[1:]
        )
        if abs(right_x - left_x) > 1e-10
    ]
    if len(dft_parts) != len(wannier_parts):
        raise ValueError("DFT and Wannier files contain different path segments")

    ticks = [dft_x[dft_parts[0][0]]]
    tick_labels = [raw_labels[0]]
    record = 1
    for segment, (left, right) in enumerate(dft_parts):
        while record < len(positions) and abs(positions[record] - positions[record - 1]) < 1e-10:
            tick_labels[-1] += "|" + raw_labels[record]
            record += 1
        ticks.append(dft_x[right])
        tick_labels.append(raw_labels[record])
        record += 1
    return dft_parts, wannier_parts, ticks, tick_labels


def map_wannier_x(
    wannier_x: list[float],
    dft_x: list[float],
    dft_parts: list[tuple[int, int]],
    wannier_parts: list[tuple[int, int]],
) -> list[list[float]]:
    mapped = []
    for (dft_start, dft_end), (wann_start, wann_end) in zip(dft_parts, wannier_parts):
        source = wannier_x[wann_start : wann_end + 1]
        width = source[-1] - source[0]
        mapped.append(
            [
                dft_x[dft_start]
                + (value - source[0]) / width * (dft_x[dft_end] - dft_x[dft_start])
                for value in source
            ]
        )
    return mapped


def style_axis(axis, ticks: list[float], labels: list[str], emin: float, emax: float) -> None:
    for position in ticks[1:-1]:
        axis.axvline(position, color="#a0a0a0", lw=0.5, alpha=0.55, zorder=0)
    axis.axhline(0.0, color="black", lw=0.75, ls="--", zorder=1)
    axis.set_xlim(ticks[0], ticks[-1])
    axis.set_ylim(emin, emax)
    axis.set_xticks(ticks)
    axis.set_xticklabels(labels, fontsize=8)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def save(figure, output_dir: Path, stem: str) -> None:
    for extension in ("png", "pdf"):
        path = output_dir / f"{stem}.{extension}"
        figure.savefig(path, dpi=300, bbox_inches="tight")
        print(path)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot DFT and Wannier90 spin bands.")
    parser.add_argument("--dft-dir", type=Path, required=True)
    parser.add_argument("--wannier-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--fermi", type=float, default=5.6635)
    parser.add_argument("--emin", type=float, default=-3.0)
    parser.add_argument("--emax", type=float, default=5.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    channels = []
    for spin, tag in ((1, "UP"), (2, "DW")):
        dft_x, dft_rows = read_dft(args.dft_dir / f"REFORMATTED_BAND_{tag}.dat")
        label_path = args.wannier_dir / f"spin{spin}" / f"wannier90.{spin}_band.labelinfo.dat"
        indices, _, _ = read_wannier_labels(label_path)
        wannier_x, wannier_bands = read_wannier(
            args.wannier_dir / f"spin{spin}" / f"wannier90.{spin}_band.dat",
            max(indices) + 1,
        )
        dft_parts, wannier_parts, ticks, labels = path_layout(dft_x, label_path)
        mapped_x = map_wannier_x(wannier_x, dft_x, dft_parts, wannier_parts)
        channels.append(
            (dft_x, dft_rows, wannier_bands, dft_parts, wannier_parts, mapped_x, ticks, labels)
        )

    colors = ("#b3262e", "#225ea8")
    titles = ("Spin up", "Spin down")

    figure, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), sharey=True)
    for axis, channel, color, title in zip(axes, channels, colors, titles):
        dft_x, dft_rows, _, dft_parts, _, _, ticks, labels = channel
        for start, end in dft_parts:
            segment = dft_rows[start : end + 1]
            for band in range(len(segment[0])):
                axis.plot(
                    dft_x[start : end + 1],
                    [row[band] for row in segment],
                    color=color,
                    lw=0.65,
                )
        style_axis(axis, ticks, labels, args.emin, args.emax)
        axis.text(
            0.015,
            0.95,
            title,
            transform=axis.transAxes,
            va="top",
            color=color,
            fontweight="bold",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
            zorder=10,
        )
    axes[0].set_ylabel(r"$E-E_F$ (eV)")
    figure.suptitle("FeCr$_2$O$_4$ DFT bands", y=0.99, fontsize=13)
    figure.tight_layout(rect=(0, 0, 1, 0.96), w_pad=1.2)
    save(figure, args.output_dir, "dft_bands_spin_resolved")

    figure, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), sharey=True)
    for axis, channel, color, title in zip(axes, channels, colors, titles):
        dft_x, dft_rows, wannier_bands, dft_parts, wannier_parts, mapped_x, ticks, labels = channel
        dft_label, wannier_label = True, True
        for start, end in dft_parts:
            segment = dft_rows[start : end + 1]
            for band in range(len(segment[0])):
                axis.plot(
                    dft_x[start : end + 1],
                    [row[band] for row in segment],
                    color="#303030",
                    lw=0.65,
                    alpha=0.8,
                    label="DFT" if dft_label else None,
                    zorder=2,
                )
                dft_label = False
        for band in wannier_bands:
            for (start, end), x_segment in zip(wannier_parts, mapped_x):
                axis.scatter(
                    x_segment,
                    [energy - args.fermi for energy in band[start : end + 1]],
                    s=3.2,
                    color=color,
                    alpha=0.8,
                    linewidths=0,
                    label="Wannier90" if wannier_label else None,
                    zorder=3,
                )
                wannier_label = False
        style_axis(axis, ticks, labels, args.emin, args.emax)
        axis.text(
            0.015,
            0.95,
            title,
            transform=axis.transAxes,
            va="top",
            color=color,
            fontweight="bold",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 1.5},
            zorder=10,
        )
        axis.legend(loc="upper right", frameon=False, fontsize=8, markerscale=2)
    axes[0].set_ylabel(r"$E-E_F$ (eV)")
    figure.suptitle("DFT and Wannier90 band comparison", y=0.99, fontsize=13)
    figure.tight_layout(rect=(0, 0, 1, 0.96), w_pad=1.2)
    save(figure, args.output_dir, "dft_wannier_overlay")


if __name__ == "__main__":
    main()
