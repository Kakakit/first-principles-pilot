from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .common import (
    axis_from_kpath,
    contiguous_segments_from_reset,
    load_numeric_table,
    pretty_label,
    read_line_mode_kpoints,
    reciprocal_from_poscar,
)
from .model import BandChannel, DOSCurve, DOSData, ProcarData


FLOAT_TOKEN_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")


def read_eigenval(path: Path) -> tuple[int, np.ndarray, np.ndarray, dict[int, np.ndarray], dict[int, np.ndarray]]:
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 7:
        raise ValueError(f"EIGENVAL too short: {path}")
    try:
        nelect, nkpts, nbands = map(int, lines[5].split()[:3])
    except Exception as exc:
        raise ValueError(f"Cannot parse EIGENVAL header in {path}") from exc
    kpoints = np.empty((nkpts, 3), dtype=float)
    weights = np.empty(nkpts, dtype=float)
    e1 = np.empty((nkpts, nbands), dtype=float)
    o1 = np.empty((nkpts, nbands), dtype=float)
    e2 = np.empty((nkpts, nbands), dtype=float)
    o2 = np.empty((nkpts, nbands), dtype=float)
    spin2 = False
    cursor = 6
    for ik in range(nkpts):
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        fields = lines[cursor].split()
        if len(fields) < 4:
            raise ValueError(f"Invalid EIGENVAL k-point record near line {cursor+1}")
        kpoints[ik] = np.asarray(fields[:3], float)
        weights[ik] = float(fields[3])
        cursor += 1
        for ib in range(nbands):
            f = lines[cursor].split()
            cursor += 1
            if len(f) >= 5:
                spin2 = True
                e1[ik, ib], e2[ik, ib] = float(f[1]), float(f[2])
                o1[ik, ib], o2[ik, ib] = float(f[3]), float(f[4])
            elif len(f) >= 3:
                e1[ik, ib], o1[ik, ib] = float(f[1]), float(f[2])
            else:
                raise ValueError(f"Invalid EIGENVAL band record near line {cursor}")
    energies = {1: e1}
    occ = {1: o1}
    if spin2:
        energies[2] = e2
        occ[2] = o2
    return nelect, kpoints, weights, energies, occ


def select_path_indices(weights: np.ndarray, selector: str = "auto") -> np.ndarray:
    selector = selector.lower()
    zero = np.isclose(weights, 0.0, atol=1e-12)
    positive = weights > 1e-12
    if selector == "all":
        return np.arange(len(weights))
    if selector == "zero-weight":
        idx = np.flatnonzero(zero)
    elif selector == "positive-weight":
        idx = np.flatnonzero(positive)
    elif selector == "auto":
        idx = np.flatnonzero(zero) if zero.any() and positive.any() else np.arange(len(weights))
    else:
        raise ValueError(f"Unknown path selector: {selector}")
    if len(idx) == 0:
        raise ValueError(f"Path selector '{selector}' selected no k-points")
    if len(idx) > 1 and not np.all(np.diff(idx) == 1):
        raise ValueError("Selected k-points are not a contiguous path block")
    return idx


def vasp_band_channels_from_eigenval(
    eigenval: Path,
    kpoints_file: Path | None = None,
    poscar: Path | None = None,
    selector: str = "auto",
    energy_reference_eV: float = 0.0,
) -> tuple[list[BandChannel], dict]:
    nelect, all_k, all_w, energies, occupations = read_eigenval(eigenval)
    idx = select_path_indices(all_w, selector)
    ksel = all_k[idx]
    wsel = all_w[idx]
    if kpoints_file is not None:
        npoints, specs = read_line_mode_kpoints(kpoints_file)
        reciprocal = reciprocal_from_poscar(poscar) if poscar is not None else None
        x, segments, ticks, labels = axis_from_kpath(ksel, specs, npoints, reciprocal)
    else:
        x = np.arange(len(idx), dtype=float)
        segments = [(0, len(idx) - 1)]
        ticks = np.asarray([x[0], x[-1]])
        labels = ["$K_0$", "$K_1$"]
    channels = []
    for spin in sorted(energies):
        name = "spin-up" if len(energies) == 2 and spin == 1 else ("spin-down" if spin == 2 else "bands")
        channels.append(
            BandChannel(
                name=name,
                x=x.copy(),
                energies=energies[spin][idx] - energy_reference_eV,
                segments=list(segments),
                ticks=ticks.copy(),
                labels=list(labels),
                occupations=occupations[spin][idx],
                weights=wsel.copy(),
                kpoints=ksel.copy(),
                metadata={"source": str(eigenval), "spin_component": spin},
            )
        )
    return channels, {"nelect": nelect, "selected_kpoints": len(idx), "total_kpoints": len(all_k)}


def read_vaspkit_band_matrix(path: Path, labels: list[str] | None = None) -> BandChannel:
    table = load_numeric_table(path, min_columns=2)
    raw_x = table[:, 0]
    energies = table[:, 1:]
    segments = contiguous_segments_from_reset(raw_x)
    # VASPKIT-style files may restart the path coordinate at each segment.
    # Convert to one continuous plotting coordinate while retaining segment slices.
    x = np.empty_like(raw_x)
    offset = 0.0
    ticks = [0.0]
    for start, end in segments:
        local = raw_x[start:end+1] - raw_x[start]
        if len(local) > 1 and local[-1] <= 1e-14:
            local = np.linspace(0.0, 1.0, len(local))
        x[start:end+1] = offset + local
        offset = float(x[end])
        ticks.append(offset)
    if labels is None:
        labels = [pretty_label(f"K{i}") for i in range(len(ticks))]
    else:
        if len(labels) != len(ticks):
            raise ValueError(f"Need {len(ticks)} labels for {len(segments)} segments")
        labels = [pretty_label(x) for x in labels]
    return BandChannel(
        name=path.stem,
        x=x,
        energies=energies,
        segments=segments,
        ticks=np.asarray(ticks),
        labels=labels,
        metadata={"source": str(path), "format": "vaspkit-matrix"},
    )


def band_edges(channel: BandChannel, occupation_threshold: float = 0.5) -> dict | None:
    if channel.occupations is None:
        return None
    occ = channel.occupations > occupation_threshold
    if not occ.any() or occ.all():
        return None
    e = channel.energies
    occupied = np.where(occ, e, -np.inf)
    empty = np.where(~occ, e, np.inf)
    vbm = float(np.max(occupied))
    cbm = float(np.min(empty))
    direct = np.min(empty, axis=1) - np.max(occupied, axis=1)
    return {
        "vbm_eV": vbm,
        "cbm_eV": cbm,
        "indirect_gap_eV": cbm - vbm,
        "minimum_direct_gap_eV": float(np.min(direct)),
    }


def read_doscar(path: Path, energy_reference: str = "fermi", fermi_override: float | None = None) -> DOSData:
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 7:
        raise ValueError(f"DOSCAR too short: {path}")
    header = lines[5].split()
    if len(header) < 4:
        raise ValueError(f"Cannot parse DOSCAR DOS header: {path}")
    nedos = int(float(header[2]))
    efermi = float(header[3]) if fermi_override is None else float(fermi_override)
    total = np.asarray([[float(v) for v in lines[6 + i].split()] for i in range(nedos)], dtype=float)
    energy = total[:, 0] - (efermi if energy_reference == "fermi" else 0.0)
    curves: list[DOSCurve] = []
    if total.shape[1] >= 5:
        curves.append(DOSCurve("Total up", energy, total[:, 1], "up"))
        curves.append(DOSCurve("Total down", energy, total[:, 2], "down"))
        spin_mode = "collinear"
    elif total.shape[1] >= 3:
        curves.append(DOSCurve("Total DOS", energy, total[:, 1], None))
        spin_mode = "nonspin-or-noncollinear-total"
    else:
        raise ValueError("Unsupported DOSCAR total DOS column count")

    projected = []
    cursor = 6 + nedos
    ion = 0
    while cursor < len(lines):
        if not lines[cursor].strip():
            cursor += 1
            continue
        h = lines[cursor].split()
        if len(h) < 3:
            break
        try:
            ion_nedos = int(float(h[2]))
        except ValueError:
            break
        cursor += 1
        block_rows = []
        for _ in range(ion_nedos):
            if cursor >= len(lines):
                break
            try:
                block_rows.append([float(v) for v in lines[cursor].split()])
            except ValueError:
                break
            cursor += 1
        if len(block_rows) != ion_nedos:
            break
        ion += 1
        projected.append(np.asarray(block_rows, dtype=float))
    return DOSData(curves=curves, fermi_eV=efermi, metadata={
        "source": str(path), "nedos": nedos, "spin_mode": spin_mode, "projected_blocks": projected,
        "energy_reference": energy_reference,
    })


def infer_pdos_orbitals(n_value_columns: int) -> tuple[list[str], int]:
    base = {
        3: ["s", "p", "d"],
        9: ["s", "py", "pz", "px", "dxy", "dyz", "dz2", "dxz", "dx2-y2"],
        16: [
            "s", "py", "pz", "px", "dxy", "dyz", "dz2", "dxz", "dx2-y2",
            "fy(3x2-y2)", "fxyz", "fyz2", "fz3", "fxz2", "fz(x2-y2)", "fx(x2-3y2)",
        ],
    }
    if n_value_columns in base:
        return base[n_value_columns], 1
    if n_value_columns % 2 == 0 and n_value_columns // 2 in base:
        return base[n_value_columns // 2], 2
    raise ValueError(
        f"Cannot infer collinear DOSCAR projected-orbital layout from {n_value_columns} value columns"
    )


def pdos_curves_from_doscar(
    dos: DOSData,
    ions: list[int],
    orbitals: list[str],
    mirror_down: bool = False,
) -> list[DOSCurve]:
    blocks: list[np.ndarray] = dos.metadata.get("projected_blocks", [])
    if not blocks:
        raise ValueError("DOSCAR does not contain projected DOS blocks")
    if min(ions) < 1 or max(ions) > len(blocks):
        raise ValueError(f"Ion selection exceeds DOSCAR projected blocks 1..{len(blocks)}")
    sample = blocks[0]
    names, spin_count = infer_pdos_orbitals(sample.shape[1] - 1)
    requested: list[int] = []
    for token in orbitals:
        t = token.lower()
        if t in {"p", "d", "f"}:
            requested.extend(i for i, name in enumerate(names) if name.startswith(t))
        else:
            matches = [i for i, name in enumerate(names) if name.lower() == t]
            if not matches:
                raise ValueError(f"Orbital '{token}' not available; choices: {', '.join(names)}")
            requested.extend(matches)
    requested = sorted(set(requested))
    efermi = dos.fermi_eV or 0.0
    ref = efermi if dos.metadata.get("energy_reference") == "fermi" else 0.0
    energy = blocks[0][:, 0] - ref
    curves = []
    if spin_count == 1:
        values = np.zeros_like(energy)
        for ion in ions:
            block = blocks[ion - 1]
            for oi in requested:
                values += block[:, 1 + oi]
        curves.append(DOSCurve(f"PDOS ions {','.join(map(str, ions))}: {','.join(orbitals)}", energy, values))
    else:
        up = np.zeros_like(energy)
        down = np.zeros_like(energy)
        for ion in ions:
            block = blocks[ion - 1]
            for oi in requested:
                up += block[:, 1 + 2 * oi]
                down += block[:, 1 + 2 * oi + 1]
        curves.append(DOSCurve(f"PDOS up: {','.join(orbitals)}", energy, up, "up"))
        curves.append(DOSCurve(f"PDOS down: {','.join(orbitals)}", energy, -down if mirror_down else down, "down"))
    return curves


def read_procar(path: Path) -> ProcarData:
    lines = path.read_text(errors="replace").splitlines()
    header_re = re.compile(r"# of k-points:\s*(\d+).*# of bands:\s*(\d+).*# of ions:\s*(\d+)")
    nk = nb = nion = None
    for line in lines[:20]:
        m = header_re.search(line)
        if m:
            nk, nb, nion = map(int, m.groups())
            break
    if nk is None:
        raise ValueError(f"Cannot parse PROCAR header: {path}")
    spin = 1
    energies: dict[int, np.ndarray] = {}
    projections: dict[int, np.ndarray] = {}
    kpoints_by_spin: dict[int, np.ndarray] = {}
    weights_by_spin: dict[int, np.ndarray] = {}
    orbital_labels: list[str] | None = None
    projection_tables_seen: set[tuple[int, int, int]] = set()
    current_k = current_band = None
    explicit_spin_headers = False
    inferred_spin_blocks = False
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        mspin = re.match(r"spin component\s+(\d+)", line, re.I)
        if mspin:
            explicit_spin_headers = True
            spin = int(mspin.group(1))
            current_k = current_band = None
            i += 1
            continue
        mk = re.match(
            r"k-point\s+(\d+)\s*:\s*(.*?)\s+weight\s*=\s*([-+0-9.Ee]+)",
            line,
            re.I,
        )
        if mk:
            next_k = int(mk.group(1)) - 1
            coordinates = [float(value) for value in FLOAT_TOKEN_RE.findall(mk.group(2))]
            if len(coordinates) != 3:
                raise ValueError(f"Cannot parse PROCAR k-point coordinates: {line}")
            # Some VASP 6 collinear PROCAR files concatenate the two ISPIN
            # blocks without printing "spin component" headers. A completed
            # nk-point block followed by k-point 1 is the channel boundary.
            if (
                not explicit_spin_headers
                and current_k == nk - 1
                and next_k == 0
            ):
                spin += 1
                inferred_spin_blocks = True
                current_band = None
            current_k = next_k
            energies.setdefault(spin, np.full((nk, nb), np.nan))
            kpoints_by_spin.setdefault(spin, np.full((nk, 3), np.nan))
            weights_by_spin.setdefault(spin, np.full(nk, np.nan))
            kpoints_by_spin[spin][current_k] = coordinates
            weights_by_spin[spin][current_k] = float(mk.group(3))
            i += 1
            continue
        mb = re.match(r"band\s+(\d+).*energy\s+([-+0-9.Ee]+)", line, re.I)
        if mb and current_k is not None:
            current_band = int(mb.group(1)) - 1
            energies.setdefault(spin, np.full((nk, nb), np.nan))[current_k, current_band] = float(mb.group(2))
            i += 1
            continue
        if line.startswith("ion") and current_k is not None and current_band is not None:
            labels = line.split()[1:]
            if labels and labels[-1].lower() == "tot":
                labels = labels[:-1]
            if orbital_labels is None:
                orbital_labels = labels
                for s in list(energies):
                    projections.setdefault(s, np.zeros((nk, nb, nion, len(labels)), dtype=float))
            projections.setdefault(spin, np.zeros((nk, nb, nion, len(orbital_labels)), dtype=float))
            # The first projection table following a band is the scalar charge
            # table.  Track it explicitly because a physically valid table can
            # contain only zeros; testing np.any would then mistake mx/my/mz
            # tables for the scalar table in noncollinear output.
            table_key = (spin, current_k, current_band)
            if table_key in projection_tables_seen:
                i += 1
                continue
            i += 1
            rows_read = 0
            while i < len(lines) and rows_read < nion:
                fields = lines[i].split()
                if not fields:
                    i += 1
                    continue
                if fields[0].lower() == "tot":
                    break
                try:
                    ion_idx = int(fields[0]) - 1
                    vals = [float(v) for v in fields[1 : 1 + len(orbital_labels)]]
                except (ValueError, IndexError):
                    break
                projections[spin][current_k, current_band, ion_idx, :] = vals
                rows_read += 1
                i += 1
            if rows_read != nion:
                raise ValueError(
                    f"Incomplete PROCAR projection table at spin {spin}, "
                    f"k-point {current_k + 1}, band {current_band + 1}"
                )
            projection_tables_seen.add(table_key)
            continue
        i += 1
    if orbital_labels is None:
        raise ValueError("No PROCAR projection tables found")
    spins = sorted(energies)
    first = spins[0]
    for s in spins:
        if np.isnan(energies[s]).any():
            raise ValueError(f"Incomplete PROCAR energy table for spin component {s}")
        if np.isnan(kpoints_by_spin[s]).any() or np.isnan(weights_by_spin[s]).any():
            raise ValueError(f"Incomplete PROCAR k-point table for spin component {s}")
        if len([key for key in projection_tables_seen if key[0] == s]) != nk * nb:
            raise ValueError(f"Incomplete PROCAR projection coverage for spin component {s}")
    for s in spins[1:]:
        if not np.allclose(kpoints_by_spin[s], kpoints_by_spin[first], atol=1e-10):
            raise ValueError(f"PROCAR spin component {s} uses a different k-point path")
        if not np.allclose(weights_by_spin[s], weights_by_spin[first], atol=1e-12):
            raise ValueError(f"PROCAR spin component {s} uses different k-point weights")
    return ProcarData(
        kpoints=kpoints_by_spin[first],
        kweights=weights_by_spin[first],
        energies_by_spin=energies,
        projections_by_spin=projections,
        orbital_labels=orbital_labels,
        metadata={
            "source": str(path),
            "nkpoints": nk,
            "nbands": nb,
            "nions": nion,
            "spin_detection": (
                "explicit-headers" if explicit_spin_headers
                else "headerless-concatenated-blocks" if inferred_spin_blocks
                else "single-block"
            ),
        },
    )
