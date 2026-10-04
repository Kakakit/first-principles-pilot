import importlib.util
import math
from pathlib import Path

import numpy as np

MODULE = Path(__file__).resolve().parents[1] / "src" / "crystal_distortion.py"
spec = importlib.util.spec_from_file_location("crystal_distortion", MODULE)
cd = importlib.util.module_from_spec(spec)
assert spec.loader is not None
import sys
sys.modules[spec.name] = cd
spec.loader.exec_module(cd)


def test_ideal_octahedron_metrics_are_ideal():
    r = 2.0
    vectors = np.array([
        [r, 0, 0], [-r, 0, 0], [0, r, 0], [0, -r, 0], [0, 0, r], [0, 0, -r]
    ], dtype=float)
    out = cd.analyze_vectors(vectors, 6)
    assert math.isclose(out["mean_bond_length_angstrom"], r, abs_tol=1e-12)
    assert out["bond_length_distortion_delta"] < 1e-24
    assert abs(out["quadratic_elongation"] - 1.0) < 1e-12
    assert out["angle_variance_degree2"] < 1e-20
    assert out["q_jt_angstrom"] < 1e-12
    assert abs(out["mean_trans_angle_degree"] - 180.0) < 1e-12


def test_ideal_tetrahedron_metrics_are_ideal():
    r = 2.0 / math.sqrt(3.0)
    vectors = np.array([
        [r, r, r], [r, -r, -r], [-r, r, -r], [-r, -r, r]
    ], dtype=float)
    out = cd.analyze_vectors(vectors, 4)
    assert math.isclose(out["mean_bond_length_angstrom"], 2.0, abs_tol=1e-12)
    assert out["bond_length_distortion_delta"] < 1e-24
    assert abs(out["quadratic_elongation"] - 1.0) < 1e-12
    assert out["angle_variance_degree2"] < 1e-20


def test_tetragonal_octahedron_has_nonzero_jt_amplitude():
    vectors = np.array([
        [2.0, 0, 0], [-2.0, 0, 0], [0, 2.0, 0], [0, -2.0, 0], [0, 0, 2.2], [0, 0, -2.2]
    ], dtype=float)
    out = cd.analyze_vectors(vectors, 6)
    assert out["q_jt_angstrom"] > 0.0
    assert out["q3_angstrom"] > 0.0
