from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

from .model import BandChannel, DOSCurve


DEFAULT_COLORS = ["#2F5597", "#C00000", "#548235", "#7030A0", "#7F6000", "#008C95"]
SUPPORTED_EXPORT_SUFFIXES = (".png", ".tiff", ".pdf", ".svg")
RASTER_DPI = 600


def apply_style(font_size: float = 9.0) -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "font.size": font_size,
        "axes.labelsize": font_size + 1,
        "axes.titlesize": font_size + 1,
        "xtick.labelsize": font_size,
        "ytick.labelsize": font_size,
        "legend.fontsize": font_size - 1,
        "axes.linewidth": 0.8,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "savefig.dpi": 600,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def style_band_axis(ax, ticks, labels, emin, emax, zero_line=True):
    for position in ticks[1:-1]:
        ax.axvline(position, color="0.75", lw=0.55, zorder=0)
    if zero_line:
        ax.axhline(0.0, color="0.15", lw=0.75, ls="--", zorder=1)
    ax.set_xlim(float(ticks[0]), float(ticks[-1]))
    ax.set_ylim(emin, emax)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_ylabel("E − E_ref (eV)")


def save_figure(fig, output: Path, formats: list[str], dpi: int = RASTER_DPI) -> list[Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in formats:
        p = output.with_suffix("." + ext.lstrip("."))
        options = {"dpi": dpi, "bbox_inches": "tight"}
        if ext.lstrip(".").lower() in {"tif", "tiff"}:
            options["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(p, **options)
        paths.append(p)
    plt.close(fig)
    return paths


def plot_band_channels(channels: list[BandChannel], output: Path, emin: float, emax: float, formats: list[str], title: str | None = None) -> list[Path]:
    apply_style()
    n = len(channels)
    fig, axes = plt.subplots(1, n, figsize=(5.2 * n, 4.2), sharey=True, squeeze=False)
    for i, (ax, ch) in enumerate(zip(axes[0], channels)):
        color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
        for start, end in ch.segments:
            for ib in range(ch.energies.shape[1]):
                ax.plot(ch.x[start:end+1], ch.energies[start:end+1, ib], color=color, lw=0.72)
        style_band_axis(ax, ch.ticks, ch.labels, emin, emax)
        ax.set_title(ch.name)
        if i:
            ax.set_ylabel("")
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    return save_figure(fig, output, formats)


def remap_channel_to_ticks(source: BandChannel, target: BandChannel) -> list[np.ndarray]:
    if len(source.segments) != len(target.segments):
        raise ValueError("Band sources contain different numbers of path segments")
    mapped = []
    for (ss, se), (ts, te) in zip(source.segments, target.segments):
        sx = source.x[ss:se+1]
        tx0, tx1 = target.x[ts], target.x[te]
        width = sx[-1] - sx[0]
        if abs(width) < 1e-14:
            mapped.append(np.linspace(tx0, tx1, len(sx)))
        else:
            mapped.append(tx0 + (sx - sx[0]) / width * (tx1 - tx0))
    return mapped


def plot_compare(dft: list[BandChannel], wannier: list[BandChannel], output: Path, emin: float, emax: float, formats: list[str], title: str | None = None, title_font=None) -> list[Path]:
    if len(dft) != len(wannier):
        raise ValueError("DFT and Wannier channel counts differ")
    apply_style()
    fig, axes = plt.subplots(1, len(dft), figsize=(5.2 * len(dft), 4.2), sharey=True, squeeze=False)
    for i, (ax, dch, wch) in enumerate(zip(axes[0], dft, wannier)):
        mapped = remap_channel_to_ticks(wch, dch)
        first_dft = first_w = True
        for start, end in dch.segments:
            for ib in range(dch.energies.shape[1]):
                ax.plot(dch.x[start:end+1], dch.energies[start:end+1, ib], color="0.2", lw=0.65, alpha=0.78,
                        label="DFT" if first_dft else None)
                first_dft = False
        color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
        for ib in range(wch.energies.shape[1]):
            for (start, end), mx in zip(wch.segments, mapped):
                ax.scatter(mx, wch.energies[start:end+1, ib], s=4.0, color=color, alpha=0.78, linewidths=0,
                           label="Wannier90" if first_w else None)
                first_w = False
        style_band_axis(ax, dch.ticks, dch.labels, emin, emax)
        ax.set_title(dch.name)
        ax.legend(frameon=False, loc="best")
        if i:
            ax.set_ylabel("")
    if title:
        fig.suptitle(title, fontproperties=title_font)
    fig.tight_layout()
    return save_figure(fig, output, formats)


def plot_dos(curves: list[DOSCurve], output: Path, emin: float, emax: float, formats: list[str], mirror_down: bool = True, horizontal: bool = False, title: str | None = None) -> list[Path]:
    apply_style()
    fig, ax = plt.subplots(figsize=(4.4, 4.2))
    for i, curve in enumerate(curves):
        values = curve.values.copy()
        if mirror_down and curve.spin == "down" and np.nanmean(values) >= 0:
            values = -values
        color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
        if horizontal:
            ax.plot(curve.energy, values, lw=1.0, color=color, label=curve.label)
        else:
            ax.plot(values, curve.energy, lw=1.0, color=color, label=curve.label)
    if horizontal:
        ax.set_xlim(emin, emax)
        ax.set_xlabel("E − E_ref (eV)")
        ax.set_ylabel("DOS (states/eV)")
        ax.axvline(0, color="0.2", lw=0.7, ls="--")
    else:
        ax.set_ylim(emin, emax)
        ax.set_ylabel("E − E_ref (eV)")
        ax.set_xlabel("DOS (states/eV)")
        ax.axhline(0, color="0.2", lw=0.7, ls="--")
        ax.axvline(0, color="0.7", lw=0.55)
    if title:
        ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    return save_figure(fig, output, formats)


def plot_fatband(channel: BandChannel, projection: np.ndarray, output: Path, emin: float, emax: float, formats: list[str], scale: float = 80.0, title: str | None = None) -> list[Path]:
    if projection.shape != channel.energies.shape:
        raise ValueError("Projection matrix shape must match band energies")
    apply_style()
    fig, ax = plt.subplots(figsize=(5.4, 4.3))
    vmax = float(np.nanmax(projection))
    denom = vmax if vmax > 0 else 1.0
    for start, end in channel.segments:
        for ib in range(channel.energies.shape[1]):
            ax.plot(channel.x[start:end+1], channel.energies[start:end+1, ib], color="0.65", lw=0.5, zorder=1)
            sizes = scale * projection[start:end+1, ib] / denom
            ax.scatter(channel.x[start:end+1], channel.energies[start:end+1, ib], s=sizes, color=DEFAULT_COLORS[0], alpha=0.65, linewidths=0, zorder=2)
    style_band_axis(ax, channel.ticks, channel.labels, emin, emax)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    return save_figure(fig, output, formats)


def audit_layout_manifest(fig, axes, panel_ids: list[str], path: Path, tolerance_pt: float = 0.5) -> Path:
    """Audit panel alignment and write backend-measured geometry for external QA."""
    fig.canvas.draw()
    width_pt, height_pt = (fig.get_size_inches() * 72.0).tolist()
    panels = []
    for ax, panel_id in zip(np.asarray(axes).ravel(), panel_ids):
        bbox = ax.get_position()
        spec = ax.get_subplotspec()
        anchor_px = ax.transAxes.transform((-0.11, 1.035))
        panels.append({
            "id": panel_id,
            "bbox_pt": [
                float(bbox.x0 * width_pt),
                float(bbox.y0 * height_pt),
                float(bbox.x1 * width_pt),
                float(bbox.y1 * height_pt),
            ],
            "panel_label_anchor_pt": [
                float(anchor_px[0] / fig.dpi * 72.0),
                float(anchor_px[1] / fig.dpi * 72.0),
            ],
            "grid_id": "fatband-grid",
            "row_start": int(spec.rowspan.start),
            "row_stop": int(spec.rowspan.stop),
            "col_start": int(spec.colspan.start),
            "col_stop": int(spec.colspan.stop),
        })
    rows: dict[int, list[dict]] = {}
    columns: dict[int, list[dict]] = {}
    for panel in panels:
        rows.setdefault(panel["row_start"], []).append(panel)
        columns.setdefault(panel["col_start"], []).append(panel)
    failures = []
    for row, members in rows.items():
        bottoms = [item["bbox_pt"][1] for item in members]
        tops = [item["bbox_pt"][3] for item in members]
        if max(bottoms) - min(bottoms) > tolerance_pt or max(tops) - min(tops) > tolerance_pt:
            failures.append(f"row {row} vertical bounds differ")
    for column, members in columns.items():
        lefts = [item["bbox_pt"][0] for item in members]
        rights = [item["bbox_pt"][2] for item in members]
        if max(lefts) - min(lefts) > tolerance_pt or max(rights) - min(rights) > tolerance_pt:
            failures.append(f"column {column} horizontal bounds differ")
    if failures:
        raise ValueError("Panel alignment audit failed: " + "; ".join(failures))
    payload = {
        "schema_version": 1,
        "backend": "python-matplotlib",
        "figure": {"width_pt": float(width_pt), "height_pt": float(height_pt)},
        "panels": panels,
        "row_groups": [
            [item["id"] for item in members] for members in rows.values() if len(members) >= 2
        ],
        "column_groups": [
            [item["id"] for item in members] for members in columns.values() if len(members) >= 2
        ],
        "exemptions": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def plot_fatband_panels(
    channels_by_spin: dict[int, BandChannel],
    projections: list[dict],
    output: Path,
    emin: float,
    emax: float,
    formats: list[str],
    scale: float = 90.0,
    weight_threshold: float = 0.01,
    title: str | None = None,
    alignment_layout: Path | None = None,
    overlay_projections: bool = False,
) -> list[Path]:
    """Plot an absolute-weight, species/orbital by spin fatband grid."""
    if not projections:
        raise ValueError("At least one fatband projection is required")
    spins = sorted(channels_by_spin)
    if not spins:
        raise ValueError("No spin channels supplied")
    if not 0.0 <= weight_threshold < 1.0:
        raise ValueError("weight_threshold must be in [0, 1)")
    if scale <= 0:
        raise ValueError("scale must be positive")
    if emin >= emax:
        raise ValueError("emin must be smaller than emax")
    for item in projections:
        for spin in spins:
            weights = item["weights_by_spin"].get(spin)
            if weights is None or weights.shape != channels_by_spin[spin].energies.shape:
                raise ValueError("Projection matrix shape must match every spin channel")

    apply_style(8.0)
    nrows, ncols = (1 if overlay_projections else len(projections)), len(spins)
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(9.2 if ncols == 2 else 5.2, max(4.3, 2.15 * nrows)),
        sharex=True,
        sharey=True,
        squeeze=False,
        constrained_layout=True,
    )
    panel_ids = [chr(ord("a") + i) for i in range(nrows * ncols)]
    panel_index = 0
    projection_rows = [projections] if overlay_projections else [[item] for item in projections]
    for row, row_items in enumerate(projection_rows):
        for col, spin in enumerate(spins):
            ax = axes[row, col]
            ch = channels_by_spin[spin]
            for start, end in ch.segments:
                for ib in range(ch.energies.shape[1]):
                    ax.plot(
                        ch.x[start:end + 1],
                        ch.energies[start:end + 1, ib],
                        color="#B7BDC5",
                        lw=0.42,
                        alpha=0.78,
                        rasterized=True,
                        zorder=1,
                    )
            x_grid = np.broadcast_to(ch.x[:, None], ch.energies.shape)
            for item_index, item in enumerate(row_items):
                color = item.get("color", DEFAULT_COLORS[item_index % len(DEFAULT_COLORS)])
                weights = np.clip(item["weights_by_spin"][spin], 0.0, None)
                visible = (
                    (weights >= weight_threshold)
                    & (ch.energies >= emin)
                    & (ch.energies <= emax)
                )
                ax.scatter(
                    x_grid[visible],
                    ch.energies[visible],
                    s=scale * weights[visible],
                    color=color,
                    alpha=0.62 if overlay_projections else 0.72,
                    linewidths=0,
                    rasterized=True,
                    zorder=2 + item_index,
                )
            style_band_axis(ax, ch.ticks, ch.labels, emin, emax)
            spin_label = "Spin up" if spin == 1 and len(spins) == 2 else (
                "Spin down" if spin == 2 else f"Spin {spin}"
            )
            title_text = spin_label if overlay_projections else f"{row_items[0]['label']}  |  {spin_label}"
            ax.set_title(title_text, pad=7.0)
            if col:
                ax.set_ylabel("")
            if row < nrows - 1:
                ax.tick_params(labelbottom=False)
            else:
                ax.tick_params(axis="x", pad=5.0)
            panel_id = panel_ids[panel_index]
            ax.text(
                -0.11,
                1.035,
                panel_id,
                transform=ax.transAxes,
                fontsize=8.5,
                fontweight="bold",
                ha="left",
                va="bottom",
            )
            panel_index += 1

    if title:
        fig.suptitle(title, fontsize=9.0)
    legend_weights = [0.1, 0.5, 1.0]
    handles = []
    labels = []
    if overlay_projections:
        handles.extend(
            ax.scatter([], [], s=16.0, color=item["color"], alpha=0.8, linewidths=0)
            for item in projections
        )
        labels.extend(item["label"] for item in projections)
    handles.extend(
        ax.scatter([], [], s=scale * value, color="#68727D", alpha=0.72, linewidths=0)
        for value in legend_weights
    )
    labels.extend(f"w={value:g}" if overlay_projections else f"{value:g}" for value in legend_weights)
    fig.legend(
        handles,
        labels,
        title=(
            "Orbital component | PROCAR projection weight"
            if overlay_projections else "PROCAR projection weight"
        ),
        loc="outside lower center",
        ncol=len(handles) if overlay_projections else 3,
        frameon=False,
        fontsize=7.0,
        title_fontsize=7.0,
    )
    if alignment_layout is not None:
        audit_layout_manifest(fig, axes, panel_ids, alignment_layout)
    return save_figure(fig, output, formats)


def plot_band_dos_panel(channel: BandChannel, curves: list[DOSCurve], output: Path, emin: float, emax: float, formats: list[str], mirror_down: bool = True, title: str | None = None) -> list[Path]:
    apply_style()
    fig, (axb, axd) = plt.subplots(1, 2, figsize=(7.2, 4.3), sharey=True, constrained_layout=True, gridspec_kw={"width_ratios": [3.2, 1.25], "wspace": 0.08})
    for start, end in channel.segments:
        for ib in range(channel.energies.shape[1]):
            axb.plot(channel.x[start:end+1], channel.energies[start:end+1, ib], color=DEFAULT_COLORS[0], lw=0.7)
    style_band_axis(axb, channel.ticks, channel.labels, emin, emax)
    for i, curve in enumerate(curves):
        values = curve.values.copy()
        if mirror_down and curve.spin == "down" and np.nanmean(values) >= 0:
            values = -values
        axd.plot(values, curve.energy, lw=0.95, color=DEFAULT_COLORS[i % len(DEFAULT_COLORS)], label=curve.label)
    axd.axhline(0, color="0.2", lw=0.7, ls="--")
    axd.axvline(0, color="0.7", lw=0.55)
    axd.set_ylim(emin, emax)
    axd.set_xlabel("DOS")
    axd.tick_params(labelleft=False)
    axd.legend(frameon=False, fontsize=7)
    if title:
        fig.suptitle(title)
    return save_figure(fig, output, formats)


def _sum_processed_columns(table: dict[str, np.ndarray], columns: list[str]) -> np.ndarray:
    result = np.zeros_like(table[columns[0]])
    for column in columns:
        if column not in table:
            raise ValueError(f"Processed table does not contain projection column {column!r}")
        result += table[column]
    return result


def plot_processed_band_dos(
    manifest_path: Path,
    manifest: dict,
    selections: list[dict],
    output: Path,
    emin: float,
    emax: float,
    formats: list[str],
    scale: float,
    weight_threshold: float,
    include_total: bool,
    title: str | None,
    alignment_layout: Path | None,
) -> list[Path]:
    """Plot two spin rows, each containing a band axis and a DOS axis sharing energy."""
    from .processed import load_band_channel, read_wide_csv

    spin_keys = list(manifest["spin_order"])
    if not spin_keys:
        raise ValueError("Processed dataset has no spin channels")
    if any(not manifest["spins"][key].get("pdos_table") for key in spin_keys):
        raise ValueError("band-dos mode requires processed PDOS tables")
    if not 0.0 <= weight_threshold < 1.0:
        raise ValueError("weight_threshold must be in [0, 1)")
    apply_style(7.0)
    nrows = len(spin_keys)
    fig, axes = plt.subplots(
        nrows,
        2,
        figsize=(7.2, 3.25 * nrows),
        sharey=True,
        squeeze=False,
        constrained_layout=True,
        gridspec_kw={"width_ratios": [3.4, 1.25], "wspace": 0.06, "hspace": 0.16},
    )
    colors = ["#3567A8", "#D57932", "#2A8C82", "#8B5FA8", "#7A6A55", "#008C95"]
    panel_ids = []
    for row, spin_key in enumerate(spin_keys):
        axb, axd = axes[row]
        channel, band_table = load_band_channel(manifest_path, manifest, spin_key)
        pdos = read_wide_csv(manifest_path.parent / manifest["spins"][spin_key]["pdos_table"])
        dos_window = (pdos["energy_eV"] >= emin) & (pdos["energy_eV"] <= emax)
        dos_window_values = []
        for start, end in channel.segments:
            for ib in range(channel.energies.shape[1]):
                axb.plot(
                    channel.x[start:end + 1], channel.energies[start:end + 1, ib],
                    color="#AEB5BE" if selections else "#3567A8",
                    lw=0.45 if selections else 0.72,
                    alpha=0.8,
                    rasterized=True,
                    zorder=1,
                )
        x_grid = np.broadcast_to(channel.x[:, None], channel.energies.shape)
        nk, nb = channel.energies.shape
        for index, selection in enumerate(selections):
            color = colors[index % len(colors)]
            weights = _sum_processed_columns(band_table, selection["columns"]).reshape(nk, nb)
            visible = (
                (weights >= weight_threshold)
                & (channel.energies >= emin)
                & (channel.energies <= emax)
            )
            axb.scatter(
                x_grid[visible], channel.energies[visible],
                s=scale * weights[visible], color=color, alpha=0.62,
                linewidths=0, rasterized=True, zorder=2 + index,
            )
            dos_values = _sum_processed_columns(pdos, selection["columns"])
            axd.plot(
                dos_values, pdos["energy_eV"], color=color, lw=0.9,
                label=selection["label"], rasterized=True,
            )
            dos_window_values.append(dos_values[dos_window])
        if include_total:
            axd.plot(
                pdos["total"], pdos["energy_eV"], color="0.25", lw=0.85,
                label="Total", rasterized=True,
            )
            dos_window_values.append(pdos["total"][dos_window])
        style_band_axis(axb, channel.ticks, channel.labels, emin, emax)
        axb.set_title(manifest["spins"][spin_key]["label"], pad=5.0)
        axb.text(
            -0.105, 1.02, chr(ord("a") + row), transform=axb.transAxes,
            fontsize=8.5, fontweight="bold", ha="left", va="bottom",
        )
        axd.axhline(0.0, color="0.15", lw=0.75, ls="--")
        axd.axvline(0.0, color="0.75", lw=0.55)
        axd.set_ylim(emin, emax)
        window_max = max((float(np.max(values)) for values in dos_window_values if values.size), default=1.0)
        axd.set_xlim(0.0, max(1e-12, window_max) * 1.06)
        axd.set_xlabel("DOS (states/eV)")
        axd.tick_params(labelleft=False)
        if row < nrows - 1:
            axb.tick_params(labelbottom=False)
            axd.tick_params(labelbottom=False)
        panel_ids.extend([f"{chr(ord('a') + row)}-band", f"{chr(ord('a') + row)}-dos"])
    legend_handles = [
        Line2D([0], [0], color=colors[index % len(colors)], lw=1.1)
        for index in range(len(selections))
    ]
    legend_labels = [item["label"] for item in selections]
    if include_total:
        legend_handles.append(Line2D([0], [0], color="0.25", lw=1.1))
        legend_labels.append("Total")
    if legend_handles:
        fig.legend(
            legend_handles, legend_labels, loc="outside right center",
            frameon=False, fontsize=6.5, handlelength=1.0, handletextpad=1.2,
        )
    if title:
        fig.suptitle(title, fontsize=9.0)
    if alignment_layout is not None:
        audit_layout_manifest(fig, axes, panel_ids, alignment_layout)
    return save_figure(fig, output, formats)


def plot_processed_pdos(
    manifest_path: Path,
    manifest: dict,
    selections: list[dict],
    output: Path,
    emin: float,
    emax: float,
    formats: list[str],
    include_total: bool,
    mirror_down: bool,
    title: str | None,
) -> list[Path]:
    """Plot standalone PDOS with spin down mirrored on a common energy axis."""
    from .processed import read_wide_csv

    apply_style(7.0)
    fig, ax = plt.subplots(figsize=(3.5, 4.2), constrained_layout=True)
    colors = ["#3567A8", "#D57932", "#2A8C82", "#8B5FA8", "#7A6A55", "#008C95"]
    spin_keys = list(manifest["spin_order"])
    plotted_window_values = []
    for spin_index, spin_key in enumerate(spin_keys):
        pdos_file = manifest["spins"][spin_key].get("pdos_table")
        if not pdos_file:
            raise ValueError("pdos mode requires processed PDOS tables")
        table = read_wide_csv(manifest_path.parent / pdos_file)
        sign = -1.0 if mirror_down and spin_index == 1 else 1.0
        linestyle = "--" if spin_index == 1 else "-"
        window = (table["energy_eV"] >= emin) & (table["energy_eV"] <= emax)
        for index, selection in enumerate(selections):
            values = sign * _sum_processed_columns(table, selection["columns"])
            ax.plot(
                values,
                table["energy_eV"],
                color=colors[index % len(colors)], lw=0.95, ls=linestyle,
                rasterized=True,
            )
            plotted_window_values.append(values[window])
        if include_total:
            values = sign * table["total"]
            ax.plot(
                values, table["energy_eV"], color="0.25",
                lw=0.85, ls=linestyle, rasterized=True,
            )
            plotted_window_values.append(values[window])
    ax.axhline(0.0, color="0.15", lw=0.75, ls="--")
    ax.axvline(0.0, color="0.7", lw=0.55)
    ax.set_ylim(emin, emax)
    if plotted_window_values:
        if mirror_down and len(spin_keys) > 1:
            limit = max(float(np.max(np.abs(values))) for values in plotted_window_values if values.size)
            ax.set_xlim(-1.06 * limit, 1.06 * limit)
        else:
            lo = min(float(np.min(values)) for values in plotted_window_values if values.size)
            hi = max(float(np.max(values)) for values in plotted_window_values if values.size)
            margin = 0.06 * max(hi - lo, 1e-12)
            ax.set_xlim(lo - margin, hi + margin)
    ax.set_xlabel("DOS (states/eV)")
    ax.set_ylabel("E − E_ref (eV)")
    if title:
        ax.set_title(title)
    projection_handles = [
        Line2D([0], [0], color=colors[index % len(colors)], lw=1.1)
        for index in range(len(selections))
    ]
    projection_labels = [item["label"] for item in selections]
    if include_total:
        projection_handles.append(Line2D([0], [0], color="0.25", lw=1.1))
        projection_labels.append("Total")
    spin_handles = [Line2D([0], [0], color="0.4", lw=1.0, ls="-")]
    spin_labels = ["Spin up"]
    if len(spin_keys) > 1:
        spin_handles.append(Line2D([0], [0], color="0.4", lw=1.0, ls="--"))
        spin_labels.append("Spin down")
    fig.legend(
        [*projection_handles, *spin_handles],
        [*projection_labels, *spin_labels],
        title="Projection | Spin style",
        loc="outside lower center",
        ncol=2,
        frameon=False,
        fontsize=6.5,
        title_fontsize=6.5,
        handlelength=1.0,
        handletextpad=1.2,
        columnspacing=1.8,
    )
    return save_figure(fig, output, formats)


def plot_processed_fatband(
    manifest_path: Path,
    manifest: dict,
    selections: list[dict],
    output: Path,
    emin: float,
    emax: float,
    formats: list[str],
    scale: float,
    weight_threshold: float,
    title: str | None,
    alignment_layout: Path | None,
) -> list[Path]:
    """Reuse processed wide tables for a selectable fatband overlay."""
    from .processed import load_band_channel

    if not selections:
        raise ValueError("fatband mode requires at least one --select projection")
    channels = {}
    tables = {}
    for index, spin_key in enumerate(manifest["spin_order"], 1):
        channel, table = load_band_channel(manifest_path, manifest, spin_key)
        spin = int(manifest["spins"][spin_key]["spin_component"])
        channels[spin] = channel
        tables[spin] = table
    colors = ["#3567A8", "#D57932", "#2A8C82", "#8B5FA8", "#7A6A55", "#008C95"]
    projections = []
    for selection_index, selection in enumerate(selections):
        item = {
            "label": selection["label"],
            "color": colors[selection_index % len(colors)],
            "weights_by_spin": {},
        }
        for spin, channel in channels.items():
            item["weights_by_spin"][spin] = _sum_processed_columns(
                tables[spin], selection["columns"]
            ).reshape(channel.energies.shape)
        projections.append(item)
    return plot_fatband_panels(
        channels, projections, output, emin, emax, formats, scale,
        weight_threshold, title, alignment_layout, overlay_projections=True,
    )
