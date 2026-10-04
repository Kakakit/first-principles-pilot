from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from banddos_core.common import axis_from_kpath, species_ion_indices_from_poscar
from banddos_core.cli import parse_species_projection_specs
from banddos_core.vasp import read_doscar, read_eigenval, read_procar, read_vaspkit_band_matrix, pdos_curves_from_doscar
from banddos_core.wannier import discover_wannier_channels

FIX = ROOT / "fixtures" / "minimal"


def test_eigenval_spin_and_shape():
    nelect, k, w, e, occ = read_eigenval(FIX / "EIGENVAL")
    assert nelect == 2
    assert k.shape == (6, 3)
    assert set(e) == {1, 2}
    assert e[1].shape == (6, 2)
    assert np.allclose(w, 0.0)
    assert np.all(occ[1][:, 0] > 0.5)


def test_wannier_discovery_and_segments():
    channels = discover_wannier_channels(FIX, "wannier90", 0.0)
    assert len(channels) == 2
    assert channels[0].energies.shape == (6, 2)
    assert channels[0].segments == [(0, 2), (3, 5)]
    assert len(channels[0].ticks) == 3


def test_vaspkit_segment_restart_becomes_continuous():
    ch = read_vaspkit_band_matrix(FIX / "REFORMATTED_BAND_UP.dat", ["GAMMA", "X", "M"])
    assert ch.segments == [(0, 2), (3, 5)]
    assert np.all(np.diff(ch.ticks) > 0)
    assert ch.x[3] == ch.x[2]


def test_doscar_total_and_grouped_pdos():
    dos = read_doscar(FIX / "DOSCAR")
    assert len(dos.curves) == 2
    assert len(dos.metadata["projected_blocks"]) == 2
    curves = pdos_curves_from_doscar(dos, [1, 2], ["d"])
    assert len(curves) == 2
    assert curves[0].spin == "up"
    assert np.all(curves[0].values >= 0)


def test_procar_projection_parser():
    data = read_procar(FIX / "PROCAR")
    assert data.metadata["nkpoints"] == 3
    assert data.metadata["nbands"] == 2
    assert data.metadata["nions"] == 1
    assert "dxy" in data.orbital_labels
    assert data.projections_by_spin[1].shape[:3] == (3, 2, 1)
    assert np.isclose(data.projections_by_spin[1][0, 0, 0].sum(), 0.50)


def test_headerless_spin_and_fixed_width_procar_parser():
    data = read_procar(FIX / "PROCAR_HEADERLESS_SPIN")
    assert set(data.energies_by_spin) == {1, 2}
    assert data.metadata["spin_detection"] == "headerless-concatenated-blocks"
    assert data.projections_by_spin[2].shape == (2, 1, 1, 9)
    assert np.allclose(data.kpoints[1], [0.5, 0.25, -0.25])
    assert np.isclose(data.energies_by_spin[2][1, 0], 0.6)


def test_species_ion_indices_from_poscar():
    assert species_ion_indices_from_poscar(FIX / "POSCAR") == {"X": [1]}


def test_discontinuous_path_keeps_both_junction_labels():
    specs = [
        (np.array([0., 0., 0.]), "GAMMA", np.array([0.5, 0., 0.]), "X"),
        (np.array([0., 0.5, 0.]), "N", np.array([0., 0., 0.]), "GAMMA"),
    ]
    kpoints = np.array([
        [0., 0., 0.], [0.5, 0., 0.],
        [0., 0.5, 0.], [0., 0., 0.],
    ])
    _, _, _, labels = axis_from_kpath(kpoints, specs, 2, None)
    assert labels == ["Γ", "X|N", "Γ"]


def test_species_component_presets_expand_to_individual_panels():
    labels = ["s", "py", "pz", "px", "dxy", "dyz", "dz2", "dxz", "x2-y2"]
    d_specs = parse_species_projection_specs(["Cr:d-components"], {"Cr": [1]}, labels)
    p_specs = parse_species_projection_specs(["O:p-components"], {"O": [2]}, labels)
    assert [item["label"] for item in d_specs] == [
        "Cr dxy", "Cr dyz", "Cr dz2", "Cr dxz", "Cr dx2-y2"
    ]
    assert [item["orbital_labels"] for item in d_specs] == [
        ["dxy"], ["dyz"], ["dz2"], ["dxz"], ["x2-y2"]
    ]
    assert [item["label"] for item in p_specs] == ["O px", "O py", "O pz"]
