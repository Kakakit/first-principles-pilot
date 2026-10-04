from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np

from .common import (
    axis_from_kpath,
    read_line_mode_kpoints,
    reciprocal_from_poscar,
    sha256_file,
    species_ion_indices_from_poscar,
    write_json,
)
from .model import BandChannel
from .vasp import infer_pdos_orbitals, read_doscar, read_procar, select_path_indices


DATASET_SCHEMA = "fpilot.electronic-structure-dataset.v1"
BASE_BAND_COLUMNS = [
    "k_index", "path_x_A^-1", "band", "energy_eV", "kx", "ky", "kz",
]
COMPONENT_PRESETS = {
    "d-components": ["dxy", "dyz", "dz2", "dxz", "dx2-y2"],
    "p-components": ["px", "py", "pz"],
}


def canonical_orbital(label: str) -> str:
    key = label.strip().lower()
    aliases = {"x2-y2": "dx2-y2", "d_x2-y2": "dx2-y2", "d_z2": "dz2"}
    return aliases.get(key, key)


def spin_label(spin: int, count: int) -> str:
    if count == 2:
        return "spin_up" if spin == 1 else "spin_down"
    return f"spin_{spin}"


def _projection_arrays(projections: np.ndarray, species_ions: dict[str, list[int]], labels: list[str]) -> dict[str, np.ndarray]:
    """Build species/orbital wide columns from (nk, nb, ion, orbital) weights."""
    names = [canonical_orbital(name) for name in labels]
    result: dict[str, np.ndarray] = {}
    groups = {group: [i for i, name in enumerate(names) if name.startswith(group)] for group in "spdf"}
    all_ions = list(range(projections.shape[2]))
    selections = {**{species: [i - 1 for i in ions] for species, ions in species_ions.items()}, "all": all_ions}
    for species, ion_indices in selections.items():
        selected = projections[:, :, np.asarray(ion_indices, dtype=int), :]
        for orbital_index, name in enumerate(names):
            result[f"{species}:{name}"] = selected[:, :, :, orbital_index].sum(axis=2)
        for group, orbital_indices in groups.items():
            if orbital_indices:
                result[f"{species}:{group}"] = selected[:, :, :, orbital_indices].sum(axis=(2, 3))
        result[f"{species}:total"] = selected.sum(axis=(2, 3))
    return result


def _write_band_table(
    path: Path,
    channel: BandChannel,
    kpoints: np.ndarray,
    projection_columns: dict[str, np.ndarray],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [*BASE_BAND_COLUMNS, *projection_columns]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for ik in range(len(channel.x)):
            for ib in range(channel.energies.shape[1]):
                row = [
                    ik + 1,
                    f"{channel.x[ik]:.10f}",
                    ib + 1,
                    f"{channel.energies[ik, ib]:.10f}",
                    f"{kpoints[ik, 0]:.10f}",
                    f"{kpoints[ik, 1]:.10f}",
                    f"{kpoints[ik, 2]:.10f}",
                ]
                row.extend(f"{values[ik, ib]:.10f}" for values in projection_columns.values())
                writer.writerow(row)


def _pdos_arrays(dos, species_ions: dict[str, list[int]], spin_index: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    blocks: list[np.ndarray] = dos.metadata.get("projected_blocks", [])
    if not blocks:
        raise ValueError("DOSCAR does not contain projected DOS blocks")
    nions = sum(len(indices) for indices in species_ions.values())
    if len(blocks) != nions:
        raise ValueError(f"DOSCAR has {len(blocks)} projected ion blocks but POSCAR defines {nions} ions")
    names, spin_count = infer_pdos_orbitals(blocks[0].shape[1] - 1)
    if spin_index > spin_count:
        raise ValueError("DOSCAR projected spin count differs from PROCAR")
    names = [canonical_orbital(name) for name in names]
    energy = blocks[0][:, 0].copy()
    if dos.metadata.get("energy_reference") == "fermi":
        energy -= float(dos.fermi_eV or 0.0)
    result: dict[str, np.ndarray] = {}
    groups = {group: [i for i, name in enumerate(names) if name.startswith(group)] for group in "spdf"}
    all_ions = list(range(nions))
    selections = {**{species: [i - 1 for i in ions] for species, ions in species_ions.items()}, "all": all_ions}
    stride = spin_count
    for species, ion_indices in selections.items():
        orbital_values: dict[str, np.ndarray] = {}
        for orbital_index, name in enumerate(names):
            values = np.zeros_like(energy)
            column = 1 + stride * orbital_index + (spin_index - 1)
            for ion_index in ion_indices:
                values += blocks[ion_index][:, column]
            orbital_values[name] = values
            result[f"{species}:{name}"] = values
        for group, orbital_indices in groups.items():
            if orbital_indices:
                result[f"{species}:{group}"] = sum(
                    (orbital_values[names[i]] for i in orbital_indices),
                    np.zeros_like(energy),
                )
        result[f"{species}:total"] = sum(orbital_values.values(), np.zeros_like(energy))
    return energy, result


def _write_dos_table(path: Path, energy: np.ndarray, total: np.ndarray, pdos_columns: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["energy_eV", "total", *pdos_columns])
        for i in range(len(energy)):
            writer.writerow([
                f"{energy[i]:.10f}",
                f"{total[i]:.10f}",
                *(f"{values[i]:.10f}" for values in pdos_columns.values()),
            ])


def read_wide_csv(path: Path) -> dict[str, np.ndarray]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        values = {name: [] for name in reader.fieldnames}
        for row in reader:
            for name in reader.fieldnames:
                values[name].append(float(row[name]))
    return {name: np.asarray(column, dtype=float) for name, column in values.items()}


def resolve_manifest(path: Path) -> tuple[Path, dict]:
    manifest_path = path / "manifest.json" if path.is_dir() else path
    if not manifest_path.exists():
        raise ValueError(f"Processed dataset manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"Unsupported processed dataset schema in {manifest_path}")
    return manifest_path.resolve(), payload


def load_band_channel(manifest_path: Path, manifest: dict, spin_key: str) -> tuple[BandChannel, dict[str, np.ndarray]]:
    spin_meta = manifest["spins"][spin_key]
    table = read_wide_csv(manifest_path.parent / spin_meta["band_table"])
    nk = int(manifest["band_path"]["nkpoints"])
    nb = int(manifest["band_path"]["nbands"])
    if len(table["energy_eV"]) != nk * nb:
        raise ValueError(f"Processed band table shape mismatch for {spin_key}")
    energy = table["energy_eV"].reshape(nk, nb)
    x = table["path_x_A^-1"].reshape(nk, nb)[:, 0]
    channel = BandChannel(
        name=spin_meta["label"],
        x=x,
        energies=energy,
        segments=[tuple(item) for item in manifest["band_path"]["segments_zero_based_inclusive"]],
        ticks=np.asarray(manifest["band_path"]["ticks_A^-1"], dtype=float),
        labels=list(manifest["band_path"]["labels"]),
        kpoints=np.column_stack([
            table["kx"].reshape(nk, nb)[:, 0],
            table["ky"].reshape(nk, nb)[:, 0],
            table["kz"].reshape(nk, nb)[:, 0],
        ]),
        metadata={"processed_table": spin_meta["band_table"]},
    )
    return channel, table


def resolve_selectors(selectors: list[str], available: list[str]) -> list[dict]:
    available_lookup = {name.lower(): name for name in available}
    resolved = []
    seen = set()
    for raw in selectors:
        species, separator, orbital_text = raw.partition(":")
        if not separator:
            key = raw.strip().lower()
            if key == "total":
                resolved.append({"label": "Total", "columns": ["total"]})
                continue
            raise ValueError(f"Selector must use SPECIES:ORBITAL syntax or 'total': {raw}")
        orbitals = [item.strip() for item in orbital_text.split(",") if item.strip()]
        if len(orbitals) == 1 and orbitals[0].lower() in COMPONENT_PRESETS:
            orbital_groups = [[item] for item in COMPONENT_PRESETS[orbitals[0].lower()]]
        else:
            orbital_groups = [orbitals]
        for group in orbital_groups:
            columns = []
            for orbital in group:
                candidate = f"{species.strip()}:{canonical_orbital(orbital)}".lower()
                if candidate not in available_lookup:
                    raise ValueError(
                        f"Projection {species}:{orbital} is unavailable; choose from {', '.join(available)}"
                    )
                columns.append(available_lookup[candidate])
            key = tuple(columns)
            if key in seen:
                continue
            seen.add(key)
            label = f"{species.strip()} {'+'.join(canonical_orbital(item) for item in group)}"
            resolved.append({"label": label, "columns": columns})
    return resolved


def parse_outcar_magnetization(path: Path | None, species_ions: dict[str, list[int]]) -> dict:
    if path is None:
        return {"available": False, "reason": "no magnetic OUTCAR supplied"}
    text = path.read_text(errors="replace")
    cell_matches = re.findall(
        r"number of electron\s+([-+0-9.Ee]+)\s+magnetization\s+([-+0-9.Ee]+)", text
    )
    cell = float(cell_matches[-1][1]) if cell_matches else None
    starts = [match.start() for match in re.finditer(r"^\s*magnetization \(x\)\s*$", text, re.M)]
    if not starts:
        return {
            "available": cell is not None,
            "source": str(path.resolve()),
            "cell_magnetization_muB": cell,
            "projected_table_available": False,
        }
    lines = text[starts[-1]:].splitlines()
    header_index = next((i for i, line in enumerate(lines) if "# of ion" in line), None)
    if header_index is None:
        return {"available": cell is not None, "cell_magnetization_muB": cell, "projected_table_available": False}
    headers = lines[header_index].split()[3:]
    ions = []
    projected_total = None
    for line in lines[header_index + 1:]:
        fields = line.split()
        if not fields or set(fields[0]) == {"-"}:
            continue
        if fields[0].lower() == "tot":
            projected_total = {name: float(value) for name, value in zip(headers, fields[1:])}
            break
        if fields[0].isdigit():
            ions.append({
                "ion": int(fields[0]),
                **{name: float(value) for name, value in zip(headers, fields[1:])},
            })
    by_species = {}
    for species, indices in species_ions.items():
        rows = [item for item in ions if item["ion"] in indices]
        by_species[species] = {
            name: float(sum(item.get(name, 0.0) for item in rows)) for name in headers
        }
    return {
        "available": bool(ions) or cell is not None,
        "source": str(path.resolve()),
        "sha256": sha256_file(path),
        "cell_magnetization_muB": cell,
        "projected_table_available": bool(ions),
        "projected_total_muB": projected_total,
        "projected_by_species_muB": by_species,
        "projected_by_ion_muB": ions,
        "boundary": "Cell magnetization and PAW-sphere projected totals are distinct quantities.",
    }


def _band_edge_report(energies: np.ndarray, crossing_tolerance_eV: float = 0.02) -> dict:
    below = energies[energies <= 0.0]
    above = energies[energies >= 0.0]
    vbm = float(np.max(below)) if below.size else None
    cbm = float(np.min(above)) if above.size else None
    crossing = [
        ib + 1 for ib in range(energies.shape[1])
        if np.min(energies[:, ib]) <= 0.0 <= np.max(energies[:, ib])
    ]
    min_abs = float(np.min(np.abs(energies)))
    direct = []
    for row in energies:
        lo = row[row <= 0.0]
        hi = row[row >= 0.0]
        if lo.size and hi.size:
            direct.append(float(np.min(hi) - np.max(lo)))
    if vbm is None or cbm is None:
        classification = "insufficient_sampled_energy_range"
    else:
        classification = "metallic_on_sampled_path" if crossing else "gapped_on_sampled_path"
    return {
        "classification": classification,
        "vbm_relative_eV": vbm,
        "cbm_relative_eV": cbm,
        "sampled_path_indirect_gap_eV": None if vbm is None or cbm is None else cbm - vbm,
        "minimum_sampled_direct_gap_eV": min(direct) if direct else None,
        "minimum_abs_energy_eV": min_abs,
        "bands_crossing_reference": crossing,
        "crossing_tolerance_eV": crossing_tolerance_eV,
        "boundary": "Sign-based PROCAR path metric; PROCAR has no occupations and the line path may miss global extrema.",
    }


def _near_fermi_projection_report(table: dict[str, np.ndarray], window_eV: float) -> dict:
    mask = np.abs(table["energy_eV"]) <= window_eV
    base = set(BASE_BAND_COLUMNS)
    columns = [name for name in table if name not in base and not name.startswith("all:")]
    group_names = {"s", "p", "d", "f", "total"}
    groups = [name for name in columns if name.split(":", 1)[1] in group_names]
    components = [name for name in columns if name.split(":", 1)[1] not in group_names]

    def ranked(names: list[str]) -> list[dict]:
        items = []
        for name in names:
            values = table[name][mask]
            items.append({
                "projection": name,
                "mean_weight": float(values.mean()) if values.size else None,
                "sum_weight": float(values.sum()) if values.size else 0.0,
                "max_weight": float(values.max()) if values.size else None,
            })
        return sorted(items, key=lambda item: item["sum_weight"], reverse=True)

    return {
        "window_eV": [-window_eV, window_eV],
        "state_records": int(mask.sum()),
        "top_species_groups": ranked(groups),
        "top_orbital_components": ranked(components),
        "boundary": "Weights are scalar PAW projections in the global orbital frame; entries are not independently normalized.",
    }


def _dos_report(table: dict[str, np.ndarray], window_eV: float, threshold: float = 1e-3) -> dict:
    energy = table["energy_eV"]
    order = np.argsort(energy)
    energy = energy[order]
    at_fermi = {}
    integrated = {}
    window_mask = (energy >= -window_eV) & (energy <= window_eV)
    for name, values_raw in table.items():
        if name == "energy_eV":
            continue
        values = values_raw[order]
        at_fermi[name] = float(np.interp(0.0, energy, values))
        integrated[name] = float(np.trapezoid(values[window_mask], energy[window_mask])) if window_mask.sum() >= 2 else None
    ranked = sorted(
        ({"projection": name, "dos_at_reference_states_per_eV": value} for name, value in at_fermi.items() if name not in {"total"} and not name.startswith("all:") and value > 0.0),
        key=lambda item: item["dos_at_reference_states_per_eV"],
        reverse=True,
    )
    ranked_integrated = sorted(
        (
            {"projection": name, "integrated_states": value}
            for name, value in integrated.items()
            if name not in {"total"} and not name.startswith("all:") and value is not None and value > 0.0
        ),
        key=lambda item: item["integrated_states"],
        reverse=True,
    )
    return {
        "total_dos_at_reference_states_per_eV": at_fermi.get("total"),
        "finite_total_dos_by_threshold": bool(at_fermi.get("total", 0.0) > threshold),
        "finite_threshold_states_per_eV": threshold,
        "integrated_total_dos_in_window": integrated.get("total"),
        "window_eV": [-window_eV, window_eV],
        "top_projected_dos_at_reference": ranked,
        "top_integrated_projected_dos_in_window": ranked_integrated,
        "boundary": "DOS at the reference depends on NEDOS, smearing and the DOS calculation k mesh.",
    }


def characterize_dataset(manifest_path: Path, manifest: dict, near_window_eV: float, magnetic_outcar: Path | None) -> dict:
    result = {
        "schema": "fpilot.electronic-structure-characterization.v1",
        "dataset_manifest": str(manifest_path),
        "energy_reference": manifest["energy_reference"],
        "near_fermi_window_eV": near_window_eV,
        "spins": {},
        "magnetization": parse_outcar_magnetization(
            magnetic_outcar,
            manifest["species_ions_1_based"],
        ),
        "scientific_boundaries": [
            "Band-edge metrics are bounded to the sampled line-mode path.",
            "PROCAR projections depend on LORBIT, PAW projectors and sphere definitions.",
            "PDOS and DOS(E_F) depend on the DOS mesh, broadening and NEDOS.",
            "Global-axis orbital components are not ligand-local crystal-field eigenchannels.",
        ],
    }
    for spin_key in manifest["spin_order"]:
        channel, band_table = load_band_channel(manifest_path, manifest, spin_key)
        spin_result = {
            "label": manifest["spins"][spin_key]["label"],
            "sampled_path_band_edges": _band_edge_report(channel.energies),
            "near_fermi_states": _near_fermi_projection_report(band_table, near_window_eV),
        }
        pdos_file = manifest["spins"][spin_key].get("pdos_table")
        if pdos_file:
            spin_result["density_of_states"] = _dos_report(
                read_wide_csv(manifest_path.parent / pdos_file), near_window_eV
            )
        result["spins"][spin_key] = spin_result
    return result


def write_characterization_markdown(path: Path, report: dict) -> None:
    lines = ["# Electronic-structure characterization", ""]
    ref = report["energy_reference"]
    lines.append(f"Energy reference: {ref['value_eV']:.8f} eV ({ref['kind']}).")
    lines.append("")
    for spin_key, item in report["spins"].items():
        edges = item["sampled_path_band_edges"]
        lines.extend([
            f"## {item['label']}",
            "",
            f"- sampled-path classification: `{edges['classification']}`",
            f"- VBM / CBM relative to reference: {edges['vbm_relative_eV']} / {edges['cbm_relative_eV']} eV",
            f"- sampled-path indirect gap: {edges['sampled_path_indirect_gap_eV']} eV",
            f"- states in ±{report['near_fermi_window_eV']} eV: {item['near_fermi_states']['state_records']}",
        ])
        if "density_of_states" in item:
            lines.append(
                f"- total DOS at reference: {item['density_of_states']['total_dos_at_reference_states_per_eV']:.8g} states/eV"
            )
        top = item["near_fermi_states"]["top_species_groups"][:5]
        lines.append("- leading near-reference species/orbital groups: " + ", ".join(
            f"{entry['projection']}=" + (
                "n/a" if entry["mean_weight"] is None else f"{entry['mean_weight']:.4g}"
            ) for entry in top
        ))
        lines.append("")
    magnetization = report["magnetization"]
    lines.extend(["## Magnetization", ""])
    if magnetization.get("available"):
        lines.append(f"- cell magnetization: {magnetization.get('cell_magnetization_muB')} μB")
        lines.append(f"- PAW projected total: {magnetization.get('projected_total_muB')}")
    else:
        lines.append("- unavailable from the supplied OUTCAR")
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {item}" for item in report["scientific_boundaries"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_dataset(
    *,
    procar: Path,
    doscar: Path | None,
    poscar: Path,
    kpoints: Path,
    fermi_eV: float,
    fermi_provenance: dict,
    band_audit: dict,
    output_dir: Path,
    path_selector: str,
    near_window_eV: float,
    magnetic_outcar: Path | None,
) -> list[Path]:
    data = read_procar(procar)
    species_ions = species_ion_indices_from_poscar(poscar)
    if sum(len(indices) for indices in species_ions.values()) != data.metadata["nions"]:
        raise ValueError("POSCAR atom count differs from PROCAR # of ions")
    selected = select_path_indices(data.kweights, path_selector)
    npoints, path_specs = read_line_mode_kpoints(kpoints)
    x, segments, ticks, labels = axis_from_kpath(
        data.kpoints[selected], path_specs, npoints, reciprocal_from_poscar(poscar)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    spins = sorted(data.energies_by_spin)
    manifest = {
        "schema": DATASET_SCHEMA,
        "extension": "electronic-structure-characterization",
        "energy_reference": fermi_provenance,
        "inputs": [
            {"role": role, "path": str(path.resolve()), "sha256": sha256_file(path)}
            for role, path in (("PROCAR", procar), ("POSCAR", poscar), ("KPOINTS", kpoints))
        ],
        "species_ions_1_based": species_ions,
        "procar": data.metadata,
        "band_outcar_audit": band_audit,
        "band_path": {
            "nkpoints": int(len(selected)),
            "nbands": int(data.metadata["nbands"]),
            "segments_zero_based_inclusive": [[int(a), int(b)] for a, b in segments],
            "ticks_A^-1": [float(value) for value in ticks],
            "labels": labels,
        },
        "spin_order": [],
        "spins": {},
        "scientific_boundaries": [
            "Processed projection columns store absolute summed scalar PROCAR weights without per-column normalization.",
            "The energy reference is explicit and shared by band and DOS tables.",
        ],
    }
    band_outcar_path = Path(band_audit["path"])
    manifest["inputs"].append({
        "role": "BAND_OUTCAR",
        "path": str(band_outcar_path),
        "sha256": band_audit["sha256"],
    })
    if fermi_provenance.get("path"):
        reference_path = Path(fermi_provenance["path"])
        manifest["inputs"].append({
            "role": "REFERENCE_OUTCAR",
            "path": str(reference_path),
            "sha256": fermi_provenance["sha256"],
        })
    written: list[Path] = []
    projection_names: list[str] | None = None
    for spin in spins:
        key = spin_label(spin, len(spins))
        channel = BandChannel(
            key,
            x.copy(),
            data.energies_by_spin[spin][selected] - fermi_eV,
            list(segments),
            ticks.copy(),
            list(labels),
        )
        columns = _projection_arrays(
            data.projections_by_spin[spin][selected], species_ions, data.orbital_labels
        )
        projection_names = list(columns)
        band_path = output_dir / f"bands_{key}.csv"
        _write_band_table(band_path, channel, data.kpoints[selected], columns)
        written.append(band_path)
        manifest["spin_order"].append(key)
        manifest["spins"][key] = {
            "spin_component": spin,
            "label": "Spin up" if key == "spin_up" else "Spin down" if key == "spin_down" else f"Spin {spin}",
            "band_table": band_path.name,
        }

    pdos_names: list[str] | None = None
    if doscar is not None:
        dos = read_doscar(doscar, "fermi", fermi_eV)
        manifest["inputs"].append({"role": "DOSCAR", "path": str(doscar.resolve()), "sha256": sha256_file(doscar)})
        if len(dos.curves) != len(spins):
            raise ValueError("DOSCAR total spin count differs from PROCAR")
        for spin, curve in zip(spins, dos.curves):
            key = spin_label(spin, len(spins))
            energy, columns = _pdos_arrays(dos, species_ions, spin)
            if not np.allclose(energy, curve.energy, atol=1e-8):
                raise ValueError("DOSCAR total and projected energy grids differ")
            pdos_names = list(columns)
            pdos_path = output_dir / f"pdos_{key}.csv"
            _write_dos_table(pdos_path, energy, curve.values, columns)
            written.append(pdos_path)
            manifest["spins"][key]["pdos_table"] = pdos_path.name
        manifest["dos"] = {"nedos": int(dos.metadata["nedos"]), "spin_mode": dos.metadata["spin_mode"]}
    manifest["available_band_projections"] = projection_names or []
    manifest["available_pdos_projections"] = pdos_names or []
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    written.append(manifest_path)

    report = characterize_dataset(manifest_path.resolve(), manifest, near_window_eV, magnetic_outcar)
    report_path = output_dir / "characterization.json"
    report_md = output_dir / "characterization.md"
    write_json(report_path, report)
    write_characterization_markdown(report_md, report)
    written.extend([report_path, report_md])
    return written
