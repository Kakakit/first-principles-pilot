from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np

from .common import (
    load_numeric_table,
    parse_int_ranges,
    read_fermi_from_outcar,
    sha256_file,
    species_ion_indices_from_poscar,
    write_json,
)
from .model import BandChannel, DOSCurve
from .plotting import (
    plot_band_channels,
    plot_band_dos_panel,
    plot_compare,
    plot_dos,
    plot_fatband,
    plot_fatband_panels,
    plot_processed_band_dos,
    plot_processed_fatband,
    plot_processed_pdos,
)
from .processed import (
    characterize_dataset,
    prepare_dataset,
    resolve_manifest,
    resolve_selectors,
    write_characterization_markdown,
)
from .vasp import (
    band_edges,
    pdos_curves_from_doscar,
    read_doscar,
    read_procar,
    read_vaspkit_band_matrix,
    select_path_indices,
    vasp_band_channels_from_eigenval,
)
from .wannier import discover_wannier_channels, wannier_channel
from .common import axis_from_kpath, read_line_mode_kpoints, reciprocal_from_poscar


ORBITAL_ALIASES = {
    "d_z2": "dz2",
    "dx2-y2": "x2-y2",
    "d_x2-y2": "x2-y2",
}
ORBITAL_COMPONENT_PRESETS = {
    "d-components": ["dxy", "dyz", "dz2", "dxz", "dx2-y2"],
    "p-components": ["px", "py", "pz"],
}


def parse_formats(text: str) -> list[str]:
    values = [x.strip().lower().lstrip(".") for x in text.split(",") if x.strip()]
    allowed = {"png", "tif", "tiff", "pdf", "svg"}
    if not values or any(v not in allowed for v in values):
        raise ValueError("formats must be a comma-separated subset of png,tiff,pdf,svg")
    return values


def parse_labels(text: str | None) -> list[str] | None:
    return None if not text else [x.strip() for x in text.split(",")]


def resolve_fermi(args) -> float:
    if getattr(args, "fermi", None) is not None:
        return float(args.fermi)
    if getattr(args, "outcar", None) is not None:
        return read_fermi_from_outcar(args.outcar)
    return 0.0


def write_band_csv(channel: BandChannel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["k_index", "path_x", "band", "energy_eV", "occupation"])
        for ik in range(len(channel.x)):
            for ib in range(channel.energies.shape[1]):
                occ = "" if channel.occupations is None else f"{channel.occupations[ik, ib]:.8f}"
                writer.writerow([ik + 1, f"{channel.x[ik]:.10f}", ib + 1, f"{channel.energies[ik, ib]:.10f}", occ])


def band_summary(channels: list[BandChannel], metadata: dict, inputs: list[Path], reference: float) -> dict:
    result = {
        "schema_version": "1.0",
        "analysis": "band",
        "energy_reference_eV": reference,
        "inputs": [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in inputs if p and p.exists()],
        "metadata": metadata,
        "channels": [],
    }
    for ch in channels:
        item = {
            "name": ch.name,
            "nkpoints": len(ch.x),
            "nbands": int(ch.energies.shape[1]),
            "segments": [[int(a), int(b)] for a, b in ch.segments],
            "ticks": [float(x) for x in ch.ticks],
            "labels": ch.labels,
        }
        edges = band_edges(ch)
        if edges:
            item["band_edges"] = edges
        result["channels"].append(item)
    return result


def cmd_vasp_band(args) -> int:
    ref = resolve_fermi(args)
    formats = parse_formats(args.formats)
    channels, metadata = vasp_band_channels_from_eigenval(
        args.eigenval, args.kpoints, args.poscar, args.path_selector, ref
    )
    if args.spin == "up" and len(channels) > 1:
        channels = [channels[0]]
    elif args.spin == "down":
        if len(channels) < 2:
            raise ValueError("EIGENVAL has no second spin channel")
        channels = [channels[1]]
    outputs = plot_band_channels(channels, args.output, args.emin, args.emax, formats, args.title)
    if args.data_prefix:
        for i, ch in enumerate(channels, 1):
            suffix = "" if len(channels) == 1 else f"_{i}"
            write_band_csv(ch, Path(str(args.data_prefix) + suffix + ".csv"))
        summary = band_summary(channels, metadata, [args.eigenval, args.outcar, args.kpoints, args.poscar], ref)
        write_json(Path(str(args.data_prefix) + ".json"), summary)
    print("\n".join(str(p) for p in outputs))
    return 0


def cmd_vaspkit_band(args) -> int:
    formats = parse_formats(args.formats)
    labels = parse_labels(args.labels)
    channels = [read_vaspkit_band_matrix(p, labels) for p in args.band_files]
    if args.fermi:
        for ch in channels:
            ch.energies = ch.energies - args.fermi
    names = parse_labels(args.channel_names)
    if names:
        if len(names) != len(channels):
            raise ValueError("--channel-names count must match --band-file count")
        for ch, name in zip(channels, names):
            ch.name = name
    outputs = plot_band_channels(channels, args.output, args.emin, args.emax, formats, args.title)
    print("\n".join(str(p) for p in outputs))
    return 0


def cmd_wannier_band(args) -> int:
    formats = parse_formats(args.formats)
    if args.band_file:
        if not args.label_file or len(args.band_file) != len(args.label_file):
            raise ValueError("Explicit --band-file requires the same number of --label-file arguments")
        channels = [
            wannier_channel(b, l, args.fermi, name=f"channel-{i+1}")
            for i, (b, l) in enumerate(zip(args.band_file, args.label_file))
        ]
    else:
        channels = discover_wannier_channels(args.directory, args.seed, args.fermi)
    outputs = plot_band_channels(channels, args.output, args.emin, args.emax, formats, args.title)
    print("\n".join(str(p) for p in outputs))
    return 0


def cmd_compare(args) -> int:
    formats = parse_formats(args.formats)
    wchannels = discover_wannier_channels(args.wannier_dir, args.seed, args.wannier_fermi)
    dft_labels = None
    if args.dft_format == "vaspkit":
        if not args.dft_band:
            raise ValueError("--dft-band is required for --dft-format vaspkit")
        dft_labels = [label.replace("$", "") for label in wchannels[0].labels]
        dchannels = [read_vaspkit_band_matrix(p, dft_labels) for p in args.dft_band]
        if args.dft_fermi:
            for ch in dchannels:
                ch.energies = ch.energies - args.dft_fermi
    else:
        if not args.eigenval:
            raise ValueError("--eigenval is required for --dft-format eigenval")
        ref = args.dft_fermi
        if ref is None and args.outcar:
            ref = read_fermi_from_outcar(args.outcar)
        if ref is None:
            ref = 0.0
        dchannels, _ = vasp_band_channels_from_eigenval(
            args.eigenval, args.kpoints, args.poscar, args.path_selector, ref
        )
    if len(dchannels) != len(wchannels):
        # Common case: one spin-degenerate Wannier result against one DFT channel.
        if len(dchannels) == 1 and len(wchannels) > 1:
            wchannels = [wchannels[0]]
        elif len(wchannels) == 1 and len(dchannels) > 1:
            dchannels = [dchannels[0]]
        else:
            raise ValueError("DFT and Wannier channel counts differ")
    outputs = plot_compare(dchannels, wchannels, args.output, args.emin, args.emax, formats, args.title)
    print("\n".join(str(p) for p in outputs))
    return 0


def selected_orbital_indices(labels: list[str], selection: list[str]) -> list[int]:
    result: set[int] = set()
    lower = [x.lower() for x in labels]
    for token in selection:
        t = ORBITAL_ALIASES.get(token.lower(), token.lower())
        if t in {"p", "d", "f"}:
            result.update(i for i, name in enumerate(lower) if name.startswith(t))
        elif t == "all":
            result.update(range(len(labels)))
        elif t in lower:
            result.add(lower.index(t))
        elif t == "x2-y2" and "dx2-y2" in lower:
            result.add(lower.index("dx2-y2"))
        else:
            raise ValueError(f"Unknown orbital '{token}'; available: {', '.join(labels)}")
    return sorted(result)


def audit_completed_band_outcar(path: Path) -> dict:
    text = path.read_text(errors="replace")
    fatal_patterns = [r"VERY BAD NEWS", r"ERROR:", r"MPI_ABORT", r"walltime exceeded"]
    fatal = [pattern for pattern in fatal_patterns if re.search(pattern, text, re.I)]
    completed = "General timing and accounting informations" in text
    if not completed or fatal:
        reason = "missing General timing" if not completed else "fatal markers: " + ", ".join(fatal)
        raise ValueError(f"Band OUTCAR did not pass terminal audit: {reason}")
    def last_int(name: str) -> int | None:
        values = re.findall(rf"\b{name}\s*=\s*(-?\d+)", text)
        return int(values[-1]) if values else None
    ispin = last_int("ISPIN")
    lorbit = last_int("LORBIT")
    if lorbit is None or lorbit < 10:
        raise ValueError("Band OUTCAR does not confirm LORBIT >= 10 projections")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "general_timing": True,
        "fatal_markers": [],
        "ispin": ispin,
        "lorbit": lorbit,
    }


def resolve_required_fermi(fermi: float | None, reference_outcar: Path | None) -> tuple[float, dict]:
    if fermi is not None and reference_outcar is not None:
        raise ValueError("Use either --fermi or --reference-outcar, not both")
    if fermi is None and reference_outcar is None:
        raise ValueError("Fatband panels require --fermi or --reference-outcar")
    if reference_outcar is not None:
        value = read_fermi_from_outcar(reference_outcar)
        return value, {
            "kind": "reference-outcar",
            "value_eV": value,
            "path": str(reference_outcar.resolve()),
            "sha256": sha256_file(reference_outcar),
        }
    return float(fermi), {"kind": "explicit", "value_eV": float(fermi)}


def parse_species_projection_specs(
    specs: list[str], species_ions: dict[str, list[int]], orbital_labels: list[str]
) -> list[dict]:
    lookup = {symbol.lower(): symbol for symbol in species_ions}
    projections = []
    seen = set()
    for raw in specs:
        species_text, separator, orbital_text = raw.partition(":")
        if not separator or not species_text.strip() or not orbital_text.strip():
            raise ValueError("--projection must use SPECIES:ORBITAL[,ORBITAL...] syntax")
        species_key = species_text.strip().lower()
        if species_key not in lookup:
            raise ValueError(
                f"Unknown POSCAR species {species_text!r}; available: {', '.join(species_ions)}"
            )
        species = lookup[species_key]
        orbitals = [item.strip() for item in orbital_text.split(",") if item.strip()]
        preset = ORBITAL_COMPONENT_PRESETS.get(orbitals[0].lower()) if len(orbitals) == 1 else None
        expanded = [[orbital] for orbital in preset] if preset else [orbitals]
        for selectors in expanded:
            orbital_indices = selected_orbital_indices(orbital_labels, selectors)
            key = (species, tuple(orbital_indices))
            if key in seen:
                raise ValueError(f"Duplicate fatband projection: {species}:{','.join(selectors)}")
            seen.add(key)
            projections.append({
                "label": f"{species} {'+'.join(selectors)}",
                "species": species,
                "ions": list(species_ions[species]),
                "orbital_selectors": selectors,
                "orbital_labels": [orbital_labels[index] for index in orbital_indices],
                "orbital_indices": orbital_indices,
            })
    return projections


def write_fatband_source_csv(
    path: Path,
    channels_by_spin: dict[int, BandChannel],
    projections: list[dict],
    emin: float,
    emax: float,
    weight_threshold: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "spin", "projection", "k_index", "path_x_A^-1", "band",
            "energy_eV", "weight", "marker_visible",
        ])
        for spin in sorted(channels_by_spin):
            channel = channels_by_spin[spin]
            for item in projections:
                weights = item["weights_by_spin"][spin]
                for ik in range(len(channel.x)):
                    for ib in range(channel.energies.shape[1]):
                        energy = float(channel.energies[ik, ib])
                        if not emin <= energy <= emax:
                            continue
                        weight = float(weights[ik, ib])
                        writer.writerow([
                            spin,
                            item["label"],
                            ik + 1,
                            f"{channel.x[ik]:.10f}",
                            ib + 1,
                            f"{energy:.10f}",
                            f"{weight:.10f}",
                            int(weight >= weight_threshold),
                        ])


def cmd_fatband_panel(args) -> int:
    if args.emin >= args.emax:
        raise ValueError("--emin must be smaller than --emax")
    if args.scale <= 0:
        raise ValueError("--scale must be positive")
    formats = parse_formats(args.formats)
    band_audit = audit_completed_band_outcar(args.band_outcar)
    fermi, fermi_provenance = resolve_required_fermi(args.fermi, args.reference_outcar)
    data = read_procar(args.procar)
    species_ions = species_ion_indices_from_poscar(args.poscar)
    if sum(len(indices) for indices in species_ions.values()) != data.metadata["nions"]:
        raise ValueError("POSCAR atom count differs from PROCAR # of ions")
    projection_specs = parse_species_projection_specs(
        args.projection, species_ions, data.orbital_labels
    )
    idx = select_path_indices(data.kweights, args.path_selector)
    npoints, path_specs = read_line_mode_kpoints(args.kpoints)
    x, segments, ticks, labels = axis_from_kpath(
        data.kpoints[idx], path_specs, npoints, reciprocal_from_poscar(args.poscar)
    )
    spins = sorted(data.energies_by_spin)
    if band_audit["ispin"] is not None and band_audit["ispin"] != len(spins):
        raise ValueError(
            f"Band OUTCAR confirms ISPIN={band_audit['ispin']} but PROCAR contains "
            f"{len(spins)} spin component(s)"
        )
    if args.spin == "up":
        spins = [spins[0]]
    elif args.spin == "down":
        if len(spins) < 2:
            raise ValueError("PROCAR has no second spin component")
        spins = [spins[1]]
    channels_by_spin = {
        spin: BandChannel(
            name=f"spin-{spin}",
            x=x.copy(),
            energies=data.energies_by_spin[spin][idx] - fermi,
            segments=list(segments),
            ticks=ticks.copy(),
            labels=list(labels),
            kpoints=data.kpoints[idx].copy(),
            metadata={"spin_component": spin},
        )
        for spin in spins
    }
    colors = ["#3567A8", "#D57932", "#2A8C82", "#8B5FA8", "#7A6A55"]
    for item_index, item in enumerate(projection_specs):
        item["color"] = colors[item_index % len(colors)]
        ion_indices = np.asarray(item["ions"], dtype=int) - 1
        orbital_indices = np.asarray(item["orbital_indices"], dtype=int)
        item["weights_by_spin"] = {}
        for spin in spins:
            selected = data.projections_by_spin[spin][idx][:, :, ion_indices, :]
            item["weights_by_spin"][spin] = selected[:, :, :, orbital_indices].sum(axis=(2, 3))

    data_prefix = args.data_prefix or args.output.with_name(args.output.name + "_source")
    csv_path = data_prefix.with_suffix(".csv")
    json_path = data_prefix.with_suffix(".json")
    alignment_layout = args.alignment_layout or args.output.with_name(
        args.output.name + "_alignment-layout.json"
    )
    write_fatband_source_csv(
        csv_path, channels_by_spin, projection_specs, args.emin, args.emax,
        args.weight_threshold,
    )
    figure_outputs = plot_fatband_panels(
        channels_by_spin,
        projection_specs,
        args.output,
        args.emin,
        args.emax,
        formats,
        args.scale,
        args.weight_threshold,
        args.title,
        alignment_layout,
        args.overlay_projections,
    )
    summaries = []
    for item in projection_specs:
        for spin in spins:
            channel = channels_by_spin[spin]
            mask = (channel.energies >= args.emin) & (channel.energies <= args.emax)
            values = item["weights_by_spin"][spin][mask]
            summaries.append({
                "projection": item["label"],
                "spin": spin,
                "states_in_energy_window": int(values.size),
                "sum_weight": float(values.sum()),
                "mean_weight": float(values.mean()) if values.size else None,
                "max_weight": float(values.max()) if values.size else None,
                "visible_marker_fraction": float(np.mean(values >= args.weight_threshold)) if values.size else None,
            })
    manifest_projections = [
        {
            "label": item["label"],
            "species": item["species"],
            "ions_1_based": item["ions"],
            "orbital_selectors": item["orbital_selectors"],
            "resolved_orbitals": item["orbital_labels"],
            "color": item["color"],
        }
        for item in projection_specs
    ]
    inputs = [args.procar, args.poscar, args.kpoints, args.band_outcar]
    summary = {
        "schema_version": "1.0",
        "analysis": "fatband-panel",
        "inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in inputs
        ],
        "band_outcar_audit": band_audit,
        "fermi_reference": fermi_provenance,
        "procar": data.metadata,
        "poscar_species_ions_1_based": species_ions,
        "selected_kpoints": int(len(idx)),
        "path_segments": len(segments),
        "energy_window_eV": [args.emin, args.emax],
        "weight_encoding": {
            "definition": "absolute summed scalar PROCAR projection weight",
            "marker_area_pt2_at_weight_1": args.scale,
            "display_threshold": args.weight_threshold,
            "threshold_changes_source_data": False,
        },
        "projection_layout": "overlay" if args.overlay_projections else "separate-panels",
        "projections": manifest_projections,
        "window_summaries": summaries,
        "outputs": {
            "figures": [str(path.resolve()) for path in figure_outputs],
            "source_csv": str(csv_path.resolve()),
            "alignment_layout": str(alignment_layout.resolve()),
        },
        "scientific_boundaries": [
            "PROCAR weights depend on LORBIT, PAW projectors, and augmentation spheres.",
            "The line-mode path does not prove global band extrema outside the sampled path.",
            "Marker-size comparison is quantitative because one absolute area scale is shared across panels.",
            "Spin-up and spin-down labels apply only to the collinear calculation represented by this PROCAR.",
        ],
    }
    write_json(json_path, summary)
    print("\n".join(str(path) for path in [*figure_outputs, csv_path, json_path, alignment_layout]))
    return 0


def cmd_fatband(args) -> int:
    formats = parse_formats(args.formats)
    if args.table:
        table = load_numeric_table(args.table, 3)
        x = table[:, args.x_col - 1]
        e = table[:, args.energy_col - 1] - args.fermi
        w = table[:, args.weight_col - 1]
        # Generic table is one point cloud; render it as one pseudo-band.
        ch = BandChannel("fatband", x, e[:, None], [(0, len(x)-1)], np.asarray([x[0], x[-1]]), ["$K_0$", "$K_1$"])
        proj = w[:, None]
        outputs = plot_fatband(ch, proj, args.output, args.emin, args.emax, formats, args.scale, args.title)
        print("\n".join(str(p) for p in outputs))
        return 0

    if not args.procar:
        raise ValueError("Provide --procar or --table")
    data = read_procar(args.procar)
    ions = parse_int_ranges(args.ions, data.metadata["nions"])
    orbitals = [x.strip() for x in args.orbitals.split(",") if x.strip()]
    oidx = selected_orbital_indices(data.orbital_labels, orbitals)
    idx = select_path_indices(data.kweights, args.path_selector)
    ksel = data.kpoints[idx]
    if args.kpoints:
        npoints, specs = read_line_mode_kpoints(args.kpoints)
        reciprocal = reciprocal_from_poscar(args.poscar) if args.poscar else None
        x, segments, ticks, labels = axis_from_kpath(ksel, specs, npoints, reciprocal)
    else:
        x = np.arange(len(idx), dtype=float)
        segments = [(0, len(idx)-1)]
        ticks = np.asarray([x[0], x[-1]])
        labels = ["$K_0$", "$K_1$"]
    spins = sorted(data.energies_by_spin)
    if args.spin == "up":
        spins = [spins[0]]
    elif args.spin == "down":
        if len(spins) < 2:
            raise ValueError("PROCAR has no second spin component")
        spins = [spins[1]]
    outputs_all = []
    for s in spins:
        energy = data.energies_by_spin[s][idx] - args.fermi
        p = data.projections_by_spin[s][idx][:, :, np.asarray(ions)-1, :]
        projection = p[:, :, :, oidx].sum(axis=(2, 3))
        name = f"spin-{s}" if len(spins) > 1 else "fatband"
        ch = BandChannel(name, x, energy, segments, ticks, labels)
        out = args.output if len(spins) == 1 else args.output.with_name(args.output.name + f"_spin{s}")
        outputs_all.extend(plot_fatband(ch, projection, out, args.emin, args.emax, formats, args.scale, args.title))
    print("\n".join(str(p) for p in outputs_all))
    return 0


def table_dos_curves(path: Path, labels: list[str] | None, fermi: float) -> list[DOSCurve]:
    data = load_numeric_table(path, 2)
    energy = data[:, 0] - fermi
    n = data.shape[1] - 1
    if labels is None:
        labels = [f"DOS {i+1}" for i in range(n)]
    if len(labels) != n:
        raise ValueError(f"--labels needs {n} entries for table columns 2..{n+1}")
    curves = []
    for i, label in enumerate(labels):
        spin = "down" if "down" in label.lower() or label.lower().endswith("dn") else ("up" if "up" in label.lower() else None)
        curves.append(DOSCurve(label, energy, data[:, i+1], spin))
    return curves


def cmd_dos(args) -> int:
    formats = parse_formats(args.formats)
    curves: list[DOSCurve]
    if args.doscar:
        dos = read_doscar(args.doscar, "absolute" if args.absolute_energy else "fermi", args.fermi)
        curves = list(dos.curves) if not args.no_total else []
        if args.ions or args.orbitals:
            if not args.ions or not args.orbitals:
                raise ValueError("Projected DOS requires both --ions and --orbitals")
            blocks = dos.metadata.get("projected_blocks", [])
            ions = parse_int_ranges(args.ions, len(blocks))
            orbitals = [x.strip() for x in args.orbitals.split(",") if x.strip()]
            curves.extend(pdos_curves_from_doscar(dos, ions, orbitals, mirror_down=False))
    elif args.table:
        curves = table_dos_curves(args.table, parse_labels(args.labels), args.fermi or 0.0)
    else:
        raise ValueError("Provide --doscar or --table")
    outputs = plot_dos(curves, args.output, args.emin, args.emax, formats, args.mirror_down, args.horizontal, args.title)
    print("\n".join(str(p) for p in outputs))
    return 0


def cmd_panel(args) -> int:
    formats = parse_formats(args.formats)
    ref = resolve_fermi(args)
    channels, _ = vasp_band_channels_from_eigenval(args.eigenval, args.kpoints, args.poscar, args.path_selector, ref)
    if args.spin == "down":
        if len(channels) < 2:
            raise ValueError("No down-spin band channel")
        ch = channels[1]
    else:
        ch = channels[0]
    dos = read_doscar(args.doscar, "fermi", args.dos_fermi)
    curves = dos.curves
    if args.ions and args.orbitals:
        blocks = dos.metadata.get("projected_blocks", [])
        ions = parse_int_ranges(args.ions, len(blocks))
        curves = curves + pdos_curves_from_doscar(dos, ions, [x.strip() for x in args.orbitals.split(",") if x.strip()])
    outputs = plot_band_dos_panel(ch, curves, args.output, args.emin, args.emax, formats, args.mirror_down, args.title)
    print("\n".join(str(p) for p in outputs))
    return 0


def cmd_prepare(args) -> int:
    if args.near_window <= 0:
        raise ValueError("--near-window must be positive")
    band_audit = audit_completed_band_outcar(args.band_outcar)
    fermi, fermi_provenance = resolve_required_fermi(args.fermi, args.reference_outcar)
    magnetic_outcar = args.magnetic_outcar or args.reference_outcar
    outputs = prepare_dataset(
        procar=args.procar,
        doscar=args.doscar,
        poscar=args.poscar,
        kpoints=args.kpoints,
        fermi_eV=fermi,
        fermi_provenance=fermi_provenance,
        band_audit=band_audit,
        output_dir=args.output_dir,
        path_selector=args.path_selector,
        near_window_eV=args.near_window,
        magnetic_outcar=magnetic_outcar,
    )
    print("\n".join(str(path) for path in outputs))
    return 0


def cmd_characterize(args) -> int:
    if args.near_window <= 0:
        raise ValueError("--near-window must be positive")
    manifest_path, manifest = resolve_manifest(args.dataset)
    magnetic_outcar = args.magnetic_outcar
    if magnetic_outcar is None:
        for item in manifest.get("inputs", []):
            if item.get("role") == "REFERENCE_OUTCAR":
                magnetic_outcar = Path(item["path"])
                break
    report = characterize_dataset(manifest_path, manifest, args.near_window, magnetic_outcar)
    output = args.output or manifest_path.parent / "characterization.json"
    markdown = args.markdown or output.with_suffix(".md")
    write_json(output, report)
    write_characterization_markdown(markdown, report)
    print(f"{output}\n{markdown}")
    return 0


def cmd_plot_processed(args) -> int:
    if args.emin >= args.emax:
        raise ValueError("--emin must be smaller than --emax")
    if args.scale <= 0:
        raise ValueError("--scale must be positive")
    formats = parse_formats(args.formats)
    manifest_path, manifest = resolve_manifest(args.dataset)
    raw_selectors = args.select or []
    if args.mode == "pdos":
        selections = resolve_selectors(raw_selectors, manifest.get("available_pdos_projections", []))
    else:
        selections = resolve_selectors(raw_selectors, manifest.get("available_band_projections", []))
        if args.mode == "band-dos":
            pdos_available = set(manifest.get("available_pdos_projections", []))
            missing = [column for item in selections for column in item["columns"] if column not in pdos_available]
            if missing:
                raise ValueError("Selected band projections are missing from processed PDOS: " + ", ".join(missing))
    alignment_layout = args.alignment_layout
    if alignment_layout is None and args.mode in {"band-dos", "fatband"}:
        alignment_layout = args.output.with_name(args.output.name + "_alignment-layout.json")
    if args.mode == "band-dos":
        outputs = plot_processed_band_dos(
            manifest_path, manifest, selections, args.output, args.emin, args.emax,
            formats, args.scale, args.weight_threshold, args.include_total,
            args.title, alignment_layout,
        )
    elif args.mode == "pdos":
        outputs = plot_processed_pdos(
            manifest_path, manifest, selections, args.output, args.emin, args.emax,
            formats, args.include_total, args.mirror_down, args.title,
        )
    else:
        outputs = plot_processed_fatband(
            manifest_path, manifest, selections, args.output, args.emin, args.emax,
            formats, args.scale, args.weight_threshold, args.title, alignment_layout,
        )
    if alignment_layout is not None:
        outputs = [*outputs, alignment_layout]
    print("\n".join(str(path) for path in outputs))
    return 0


def add_common_plot(parser):
    parser.add_argument("--emin", type=float, default=-3.0)
    parser.add_argument("--emax", type=float, default=3.0)
    parser.add_argument("--formats", default="png,pdf", help="Comma-separated: png,pdf,svg")
    parser.add_argument("--title")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eschar",
        description="Prepare, characterize and plot reusable VASP electronic-structure datasets",
    )
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("vasp-band", help="Plot VASP EIGENVAL bands")
    q.add_argument("--eigenval", type=Path, required=True)
    q.add_argument("--outcar", type=Path)
    q.add_argument("--fermi", type=float)
    q.add_argument("--kpoints", type=Path, help="Line-mode KPOINTS/KPATH for ticks and segments")
    q.add_argument("--poscar", type=Path, help="POSCAR for reciprocal-distance x-axis")
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--spin", choices=["both", "up", "down"], default="both")
    q.add_argument("--output", type=Path, default=Path("vasp_band"))
    q.add_argument("--data-prefix", type=Path, help="Optional CSV + JSON parsed-data output prefix")
    add_common_plot(q)
    q.set_defaults(func=cmd_vasp_band)

    q = sub.add_parser("vaspkit-band", help="Plot VASPKIT REFORMATTED_BAND_*.dat matrices")
    q.add_argument("--band-file", dest="band_files", type=Path, action="append", required=True)
    q.add_argument("--labels", help="Comma-separated high-symmetry labels")
    q.add_argument("--channel-names", help="Comma-separated subplot names")
    q.add_argument("--fermi", type=float, default=0.0, help="Energy reference subtracted from matrix energies")
    q.add_argument("--output", type=Path, default=Path("vaspkit_band"))
    add_common_plot(q)
    q.set_defaults(func=cmd_vaspkit_band)

    q = sub.add_parser("wannier-band", help="Plot Wannier90 *_band.dat")
    q.add_argument("--directory", type=Path, default=Path.cwd())
    q.add_argument("--seed", default="wannier90")
    q.add_argument("--fermi", type=float, default=0.0)
    q.add_argument("--band-file", type=Path, action="append")
    q.add_argument("--label-file", type=Path, action="append")
    q.add_argument("--output", type=Path, default=Path("wannier_band"))
    add_common_plot(q)
    q.set_defaults(func=cmd_wannier_band)

    q = sub.add_parser("compare", help="Overlay DFT and Wannier90 bands")
    q.add_argument("--dft-format", choices=["vaspkit", "eigenval"], default="vaspkit")
    q.add_argument("--dft-band", type=Path, action="append")
    q.add_argument("--eigenval", type=Path)
    q.add_argument("--outcar", type=Path)
    q.add_argument("--dft-fermi", type=float)
    q.add_argument("--kpoints", type=Path)
    q.add_argument("--poscar", type=Path)
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--wannier-dir", type=Path, required=True)
    q.add_argument("--seed", default="wannier90")
    q.add_argument("--wannier-fermi", type=float, default=0.0)
    q.add_argument("--output", type=Path, default=Path("dft_wannier_compare"))
    add_common_plot(q)
    q.set_defaults(func=cmd_compare)

    q = sub.add_parser("fatband", help="Plot orbital/ion projected fatbands from PROCAR or generic x/E/weight table")
    q.add_argument("--procar", type=Path)
    q.add_argument("--table", type=Path)
    q.add_argument("--kpoints", type=Path)
    q.add_argument("--poscar", type=Path)
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--ions", default="all", help="1-based selection, e.g. 1,3-6 or all")
    q.add_argument("--orbitals", default="d", help="Comma-separated PROCAR orbitals or groups p,d,f,all")
    q.add_argument("--spin", choices=["both", "up", "down"], default="both")
    q.add_argument("--fermi", type=float, default=0.0)
    q.add_argument("--scale", type=float, default=80.0, help="Maximum marker area")
    q.add_argument("--x-col", type=int, default=1)
    q.add_argument("--energy-col", type=int, default=2)
    q.add_argument("--weight-col", type=int, default=3)
    q.add_argument("--output", type=Path, default=Path("fatband"))
    add_common_plot(q)
    q.set_defaults(func=cmd_fatband)

    q = sub.add_parser(
        "fatband-panel",
        help="Audit and plot a species/orbital by spin fatband grid from a completed VASP band run",
    )
    q.add_argument("--procar", type=Path, required=True)
    q.add_argument("--poscar", type=Path, required=True)
    q.add_argument("--kpoints", type=Path, required=True)
    q.add_argument("--band-outcar", type=Path, required=True)
    q.add_argument("--reference-outcar", type=Path, help="SCF/reference OUTCAR supplying E_F")
    q.add_argument("--fermi", type=float, help="Explicit E_F in eV; exclusive with --reference-outcar")
    q.add_argument(
        "--projection",
        action="append",
        required=True,
        help=(
            "Repeatable SPECIES:ORBITAL[,ORBITAL...] selector, e.g. Cr:d; "
            "use SPECIES:d-components or SPECIES:p-components to expand "
            "the five d or three p components into separate panels"
        ),
    )
    q.add_argument("--spin", choices=["both", "up", "down"], default="both")
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--scale", type=float, default=90.0, help="Marker area in pt^2 for projection weight 1")
    q.add_argument("--weight-threshold", type=float, default=0.01, help="Display markers at or above this raw weight")
    q.add_argument(
        "--overlay-projections",
        action="store_true",
        help="Overlay all selected projection weights by color in each spin panel",
    )
    q.add_argument("--data-prefix", type=Path, help="Source CSV and provenance JSON prefix")
    q.add_argument("--alignment-layout", type=Path, help="Backend-measured panel geometry JSON")
    q.add_argument("--output", type=Path, default=Path("fatband_panel"))
    add_common_plot(q)
    q.set_defaults(func=cmd_fatband_panel, formats="png,tiff,pdf,svg")

    q = sub.add_parser("dos", help="Plot total/projected DOS from DOSCAR or a numeric table")
    q.add_argument("--doscar", type=Path)
    q.add_argument("--table", type=Path)
    q.add_argument("--labels", help="Comma-separated labels for generic table columns after energy")
    q.add_argument("--fermi", type=float, help="Override DOSCAR E_F or subtract from generic table")
    q.add_argument("--absolute-energy", action="store_true", help="Do not shift DOSCAR energies by E_F")
    q.add_argument("--ions", help="Projected DOS ion selection, e.g. 1,3-6")
    q.add_argument("--orbitals", help="Projected DOS orbitals/groups, e.g. d or dxy,dxz")
    q.add_argument("--no-total", action="store_true")
    q.add_argument("--mirror-down", action=argparse.BooleanOptionalAction, default=True)
    q.add_argument("--horizontal", action="store_true", help="Energy on x-axis instead of y-axis")
    q.add_argument("--output", type=Path, default=Path("dos"))
    add_common_plot(q)
    q.set_defaults(func=cmd_dos)

    q = sub.add_parser("panel", help="Combined VASP band + DOS figure")
    q.add_argument("--eigenval", type=Path, required=True)
    q.add_argument("--outcar", type=Path)
    q.add_argument("--fermi", type=float)
    q.add_argument("--kpoints", type=Path)
    q.add_argument("--poscar", type=Path)
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--spin", choices=["up", "down"], default="up")
    q.add_argument("--doscar", type=Path, required=True)
    q.add_argument("--dos-fermi", type=float)
    q.add_argument("--ions")
    q.add_argument("--orbitals")
    q.add_argument("--mirror-down", action=argparse.BooleanOptionalAction, default=True)
    q.add_argument("--output", type=Path, default=Path("band_dos_panel"))
    add_common_plot(q)
    q.set_defaults(func=cmd_panel)

    q = sub.add_parser(
        "prepare",
        help="Parse PROCAR/DOSCAR once into reusable spin-resolved band, weight and PDOS tables",
    )
    q.add_argument("--procar", type=Path, required=True)
    q.add_argument("--doscar", type=Path, help="Optional DOSCAR; required for PDOS and band+DOS plotting")
    q.add_argument("--poscar", type=Path, required=True)
    q.add_argument("--kpoints", type=Path, required=True)
    q.add_argument("--band-outcar", type=Path, required=True)
    q.add_argument("--reference-outcar", type=Path, help="Reference OUTCAR supplying E_F and, by default, magnetization")
    q.add_argument("--magnetic-outcar", type=Path, help="Optional separate OUTCAR for magnetic-moment extraction")
    q.add_argument("--fermi", type=float, help="Explicit E_F in eV; exclusive with --reference-outcar")
    q.add_argument("--path-selector", choices=["auto", "all", "zero-weight", "positive-weight"], default="auto")
    q.add_argument("--near-window", type=float, default=0.5, help="Half-width in eV for near-reference state analysis")
    q.add_argument("--output-dir", type=Path, default=Path("electronic_structure_dataset"))
    q.set_defaults(func=cmd_prepare)

    q = sub.add_parser(
        "characterize",
        help="Recompute band-edge, near-Fermi, DOS and magnetic summaries from a processed dataset",
    )
    q.add_argument("--dataset", type=Path, required=True, help="Dataset directory or manifest.json")
    q.add_argument("--magnetic-outcar", type=Path)
    q.add_argument("--near-window", type=float, default=0.5)
    q.add_argument("--output", type=Path)
    q.add_argument("--markdown", type=Path)
    q.set_defaults(func=cmd_characterize)

    q = sub.add_parser(
        "plot",
        help="Plot band-dos, pdos or fatband modes from processed files without reparsing VASP outputs",
    )
    q.add_argument("--mode", choices=["band-dos", "pdos", "fatband"], required=True)
    q.add_argument("--dataset", type=Path, required=True, help="Dataset directory or manifest.json")
    q.add_argument(
        "--select", action="append",
        help="Repeatable processed projection, e.g. Fe:d, O:p or Cr:d-components",
    )
    q.add_argument("--include-total", action=argparse.BooleanOptionalAction, default=True)
    q.add_argument("--mirror-down", action=argparse.BooleanOptionalAction, default=True)
    q.add_argument("--scale", type=float, default=30.0, help="Fatband marker area in pt^2 for weight 1")
    q.add_argument("--weight-threshold", type=float, default=0.01)
    q.add_argument("--alignment-layout", type=Path)
    q.add_argument("--output", type=Path, default=Path("electronic_structure"))
    add_common_plot(q)
    q.set_defaults(func=cmd_plot_processed, formats="png,tiff,pdf,svg")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except (ValueError, OSError, IndexError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
