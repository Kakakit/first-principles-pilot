from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "minimal"
CLI = ROOT / "src" / "banddos.py"


def run(*args):
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], text=True, capture_output=True, env=env)


def test_vasp_band_cli(tmp_path):
    out = tmp_path / "band"
    data = tmp_path / "band_data"
    r = run("vasp-band", "--eigenval", FIX / "EIGENVAL", "--outcar", FIX / "OUTCAR",
            "--kpoints", FIX / "KPOINTS", "--poscar", FIX / "POSCAR", "--output", out,
            "--data-prefix", data, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert out.with_suffix(".png").exists()
    assert data.with_suffix(".json").exists()


def test_wannier_and_compare_cli(tmp_path):
    w = tmp_path / "wannier"
    r = run("wannier-band", "--directory", FIX, "--output", w, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert w.with_suffix(".png").exists()
    c = tmp_path / "compare"
    r = run("compare", "--dft-format", "vaspkit", "--dft-band", FIX / "REFORMATTED_BAND_UP.dat",
            "--dft-band", FIX / "REFORMATTED_BAND_DW.dat", "--wannier-dir", FIX,
            "--output", c, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert c.with_suffix(".png").exists()


def test_dos_fatband_panel_cli(tmp_path):
    d = tmp_path / "dos"
    r = run("dos", "--doscar", FIX / "DOSCAR", "--ions", "1-2", "--orbitals", "d",
            "--output", d, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert d.with_suffix(".png").exists()
    f = tmp_path / "fat"
    r = run("fatband", "--procar", FIX / "PROCAR", "--kpoints", FIX / "KPOINTS_ONE",
            "--poscar", FIX / "POSCAR", "--ions", "1", "--orbitals", "d",
            "--output", f, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert f.with_suffix(".png").exists()
    p = tmp_path / "panel"
    r = run("panel", "--eigenval", FIX / "EIGENVAL", "--outcar", FIX / "OUTCAR",
            "--kpoints", FIX / "KPOINTS", "--poscar", FIX / "POSCAR", "--doscar", FIX / "DOSCAR",
            "--output", p, "--formats", "png")
    assert r.returncode == 0, r.stderr
    assert p.with_suffix(".png").exists()


def test_species_fatband_workflow_cli(tmp_path):
    out = tmp_path / "fatband_panel"
    data = tmp_path / "fatband_source"
    layout = tmp_path / "fatband_layout.json"
    r = run(
        "fatband-panel",
        "--procar", FIX / "PROCAR_HEADERLESS_SPIN",
        "--poscar", FIX / "POSCAR",
        "--kpoints", FIX / "KPOINTS_TWO",
        "--band-outcar", FIX / "BAND_OUTCAR",
        "--reference-outcar", FIX / "OUTCAR",
        "--projection", "X:d-components",
        "--overlay-projections",
        "--output", out,
        "--data-prefix", data,
        "--alignment-layout", layout,
        "--formats", "png,pdf,svg",
    )
    assert r.returncode == 0, r.stderr
    assert out.with_suffix(".png").exists()
    assert out.with_suffix(".pdf").exists()
    assert out.with_suffix(".svg").exists()
    assert data.with_suffix(".csv").exists()
    assert data.with_suffix(".json").exists()
    assert layout.exists()
    assert json.loads(data.with_suffix(".json").read_text())["projection_layout"] == "overlay"
    assert len(json.loads(layout.read_text())["panels"]) == 2

    separate_out = tmp_path / "fatband_panel_separate"
    separate_data = tmp_path / "fatband_source_separate"
    separate_layout = tmp_path / "fatband_layout_separate.json"
    r = run(
        "fatband-panel",
        "--procar", FIX / "PROCAR_HEADERLESS_SPIN",
        "--poscar", FIX / "POSCAR",
        "--kpoints", FIX / "KPOINTS_TWO",
        "--band-outcar", FIX / "BAND_OUTCAR",
        "--reference-outcar", FIX / "OUTCAR",
        "--projection", "X:d-components",
        "--output", separate_out,
        "--data-prefix", separate_data,
        "--alignment-layout", separate_layout,
        "--formats", "png",
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(separate_data.with_suffix(".json").read_text())["projection_layout"] == "separate-panels"
    assert len(json.loads(separate_layout.read_text())["panels"]) == 10


def test_processed_electronic_structure_workflow_cli(tmp_path):
    dataset = tmp_path / "dataset"
    r = run(
        "prepare",
        "--procar", FIX / "PROCAR_HEADERLESS_SPIN",
        "--doscar", FIX / "DOSCAR_ONE",
        "--poscar", FIX / "POSCAR",
        "--kpoints", FIX / "KPOINTS_TWO",
        "--band-outcar", FIX / "BAND_OUTCAR",
        "--reference-outcar", FIX / "OUTCAR",
        "--output-dir", dataset,
    )
    assert r.returncode == 0, r.stderr
    manifest = json.loads((dataset / "manifest.json").read_text())
    report = json.loads((dataset / "characterization.json").read_text())
    assert manifest["schema"] == "fpilot.electronic-structure-dataset.v1"
    assert manifest["spin_order"] == ["spin_up", "spin_down"]
    assert (dataset / "bands_spin_up.csv").exists()
    assert (dataset / "pdos_spin_down.csv").exists()
    assert report["magnetization"]["cell_magnetization_muB"] == 0.5
    assert report["magnetization"]["projected_total_muB"]["tot"] == 0.5

    for mode, selector in (("band-dos", "X:d"), ("pdos", "X:d"), ("fatband", "X:d-components")):
        output = tmp_path / mode
        r = run(
            "plot", "--mode", mode, "--dataset", dataset,
            "--select", selector, "--output", output, "--formats", "png",
        )
        assert r.returncode == 0, r.stderr
        assert output.with_suffix(".png").exists()
    assert (tmp_path / "band-dos_alignment-layout.json").exists()
    assert (tmp_path / "fatband_alignment-layout.json").exists()
