import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "crystal_distortion.py"
FIXTURE = ROOT / "fixtures" / "ideal_fecr_polyhedra" / "POSCAR"
CONFIG = ROOT / "config" / "default_fecr2o4.json"


def test_dry_run_parses_fixture():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURE), "--config", str(CONFIG), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "dry-run"
    assert payload["planned_centers"]["Cr"]["sites_found"] == 1
    assert payload["planned_centers"]["Fe"]["sites_found"] == 1


def test_full_cli_writes_three_outputs(tmp_path):
    prefix = tmp_path / "distortion"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), str(FIXTURE),
            "--config", str(CONFIG), "--output-prefix", str(prefix)
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Analyzed 2 center sites" in proc.stdout
    assert prefix.with_suffix(".csv").exists()
    assert prefix.with_suffix(".json").exists()
    assert prefix.with_suffix(".md").exists()
    payload = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
    assert len(payload["site_results"]) == 2
    for site in payload["site_results"]:
        assert site["bond_length_distortion_delta"] < 1e-20
        assert abs(site["quadratic_elongation"] - 1.0) < 1e-10
