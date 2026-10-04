import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np


EXTENSION = Path(__file__).resolve().parents[1]
SCRIPT = EXTENSION / "scripts/calc_lcalcpol.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


spec = importlib.util.spec_from_file_location("calc_lcalcpol", SCRIPT)
lcalcpol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lcalcpol
spec.loader.exec_module(lcalcpol)


class LcalcpolTests(unittest.TestCase):
    def compare_coefficients(self, no_coeff, soc_coeff, lattice=None):
        if lattice is None:
            lattice = np.diag([2., 3., 4.])
        volume = abs(np.linalg.det(lattice))
        quantum = lattice * lcalcpol.EANG_TO_C_M2 / volume
        def endpoint(coeff):
            return {
                "lattice_matrix_angstrom": lattice.tolist(),
                "raw": {"polarization_c_m2": (np.asarray(coeff) @ quantum).tolist()},
                "branch_reduced": {"polarization_c_m2": ((np.asarray(coeff) - np.round(coeff)) @ quantum).tolist()},
                "polarization_quantum": {"vectors_c_m2": quantum.tolist()},
            }
        with patch.object(lcalcpol, "_single_result", side_effect=[endpoint(no_coeff), endpoint(soc_coeff)]):
            return lcalcpol.compare_polarizations("a", "b", "c", "d")["comparison"], quantum

    def test_compare_subtracts_before_reducing_across_endpoint_boundary(self):
        result, quantum = self.compare_coefficients([.499, 0, 0], [.501, 0, 0])
        np.testing.assert_allclose(result["delta_p_branch_reduced_candidate_c_m2"], .002 * quantum[0], atol=1e-14)
        np.testing.assert_allclose(result["endpoint_reduced_difference_c_m2"], -.998 * quantum[0])
        self.assertEqual(result["difference_reduction"]["integer_quanta_removed"], [0, 0, 0])

    def test_compare_invariant_under_independent_integer_branch_shifts(self):
        base, _ = self.compare_coefficients([.13, -.24, .36], [.25, .08, -.1])
        shifted, _ = self.compare_coefficients([3.13, -2.24, 5.36], [-6.75, 4.08, 8.9])
        np.testing.assert_allclose(shifted["delta_p_branch_reduced_candidate_c_m2"], base["delta_p_branch_reduced_candidate_c_m2"], atol=1e-14)

    def test_compare_nonorthogonal_reconstruction_and_units(self):
        lattice = np.array([[2., 0, 0], [1., 3., 0], [.5, .5, 4.]])
        result, quantum = self.compare_coefficients([0, 0, 0], [1.2, -.7, 2.4], lattice)
        reduction = result["difference_reduction"]
        self.assertEqual(reduction["integer_quanta_removed"], [1, -1, 2])
        expected = np.array([.2, .3, .4]) @ quantum
        np.testing.assert_allclose(result["delta_p_branch_reduced_candidate_c_m2"], expected)
        np.testing.assert_allclose(np.asarray(result["delta_p_raw_c_m2"]) - reduction["removed_polarization_c_m2"], expected)
        np.testing.assert_allclose(result["delta_p_branch_reduced_candidate_uc_m2"], expected * 1e6)
        self.assertAlmostEqual(result["delta_p_branch_reduced_candidate_magnitude_uc_m2"], np.linalg.norm(expected) * 1e6)

    def test_compare_zero_and_exact_half_quantum(self):
        zero, _ = self.compare_coefficients([.1, .2, .3], [.1, .2, .3])
        np.testing.assert_allclose(zero["delta_p_branch_reduced_candidate_c_m2"], [0, 0, 0])
        half, _ = self.compare_coefficients([0, 0, 0], [.5, 1.5, -.5])
        np.testing.assert_allclose(half["difference_reduction"]["distance_to_half_integer"], [0, 0, 0], atol=1e-14)
        self.assertEqual(half["same_branch_delta_p"]["status"], "INSUFFICIENT")

    def test_compare_rejects_invalid_tolerance(self):
        for tolerance in [-1, float("nan"), float("inf")]:
            with self.assertRaisesRegex(ValueError, "finite and non-negative"):
                lcalcpol.compare_polarizations("a", "b", "c", "d", tolerance)

    def test_poscar_lattice_and_volume(self):
        lattice, volume = lcalcpol.read_poscar(FIXTURES / "POSCAR")

        np.testing.assert_allclose(
            lattice,
            np.diag([2.0, 3.0, 4.0]),
        )
        self.assertAlmostEqual(volume, 24.0)

    def test_lcalcpol_dipoles_are_parsed(self):
        p_ion, p_elc = lcalcpol.read_lcalcpol(FIXTURES / "OUTCAR")

        np.testing.assert_allclose(p_ion, [1.0, -2.0, 3.5])
        np.testing.assert_allclose(p_elc, [-0.25, 0.5, -1.5])

    def test_lcalcpol_uses_last_record_and_scientific_notation(self):
        with tempfile.TemporaryDirectory() as directory:
            outcar = Path(directory) / "OUTCAR"
            outcar.write_text(
                "Ionic dipole moment: p[ion] = ( 1.0, 2.0, 3.0 )\n"
                "Total electronic dipole moment: p[elc] = ( -1.0, -2.0, -3.0 )\n"
                "Ionic dipole moment: p[ion] = ( 1.0E+1, -2.0d+0, 3.0e-1 )\n"
                "Total electronic dipole moment: p[elc] = ( -2.5E-1, 5.0e-1, -1.5D+0 )\n",
                encoding="utf-8",
            )
            p_ion, p_elc = lcalcpol.read_lcalcpol(outcar)
            np.testing.assert_allclose(p_ion, [10.0, -2.0, 0.3])
            np.testing.assert_allclose(p_elc, [-0.25, 0.5, -1.5])

    def test_lcalcpol_ignores_trailing_incomplete_record(self):
        with tempfile.TemporaryDirectory() as directory:
            outcar = Path(directory) / "OUTCAR"
            outcar.write_text(
                "Ionic dipole moment: p[ion] = ( 1.0, 2.0, 3.0 )\n"
                "Total electronic dipole moment: p[elc] = ( 4.0, 5.0, 6.0 )\n"
                "Ionic dipole moment: p[ion] = ( 10.0, 20.0, 30.0 )\n",
                encoding="utf-8",
            )
            p_ion, p_elc = lcalcpol.read_lcalcpol(outcar)
            np.testing.assert_allclose(p_ion, [1.0, 2.0, 3.0])
            np.testing.assert_allclose(p_elc, [4.0, 5.0, 6.0])

    def test_dipole_to_p_uses_the_source_conversion(self):
        result = lcalcpol.dipole_to_p(np.array([0.75, -1.5, 2.0]), 24.0)

        np.testing.assert_allclose(
            result,
            np.array([0.75, -1.5, 2.0]) / 24.0 * 16.021766,
        )

    def test_polarization_magnitude_helper(self):
        self.assertAlmostEqual(
            lcalcpol.polarization_magnitude(np.array([3.0, 4.0, 12.0])),
            13.0,
        )

    def test_reduce_branch_for_orthogonal_lattice(self):
        lattice = np.diag([2.0, 3.0, 4.0])
        dipole = np.array([2.7, -3.6, 6.5])

        reduced, coeff, coeff_reduced, integer = lcalcpol.reduce_branch(
            dipole, lattice
        )

        np.testing.assert_allclose(coeff, [1.35, -1.2, 1.625])
        np.testing.assert_allclose(coeff_reduced, [0.35, -0.2, -0.375])
        np.testing.assert_allclose(integer, [1.0, -1.0, 2.0])
        np.testing.assert_allclose(reduced, [0.7, -0.6, -1.5])

    def test_reduce_branch_for_nonorthogonal_lattice(self):
        lattice = np.array([
            [2.0, 0.0, 0.0],
            [1.0, 3.0, 0.0],
            [0.5, 0.5, 4.0],
        ])
        coefficients = np.array([1.2, -0.7, 2.4])
        dipole = coefficients @ lattice

        reduced, coeff, coeff_reduced, integer = lcalcpol.reduce_branch(
            dipole, lattice
        )

        np.testing.assert_allclose(coeff, coefficients)
        np.testing.assert_allclose(coeff_reduced, [0.2, 0.3, 0.4])
        np.testing.assert_allclose(integer, [1.0, -1.0, 2.0])
        np.testing.assert_allclose(reduced, [0.9, 1.1, 1.6])

    def test_missing_output_records_raise_clear_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "OUTCAR"
            output.write_text(
                "Ionic dipole moment: p[ion] = ( 1.0, 2.0, 3.0 )\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, r"Cannot find p\[elc\]"):
                lcalcpol.read_lcalcpol(output)

            output.write_text(
                "Total electronic dipole moment: p[elc] = ( 1.0, 2.0, 3.0 )\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, r"Cannot find p\[ion\]"):
                lcalcpol.read_lcalcpol(output)

            with self.assertRaises(FileNotFoundError):
                lcalcpol.read_lcalcpol(Path(directory) / "missing-OUTCAR")

    def test_cli_reports_raw_and_branch_reduced_magnitudes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=FIXTURES,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("========== LCALCPOL ==========", result.stdout)
        self.assertIn("Total dipole (e Ang):", result.stdout)
        self.assertIn("C/m^2", result.stdout)
        self.assertIn("uC/m^2", result.stdout)

        lattice, volume = lcalcpol.read_poscar(FIXTURES / "POSCAR")
        p_ion, p_elc = lcalcpol.read_lcalcpol(FIXTURES / "OUTCAR")
        raw_p = lcalcpol.dipole_to_p(p_ion + p_elc, volume)
        reduced_dipole = lcalcpol.reduce_branch(p_ion + p_elc, lattice)[0]
        reduced_p = lcalcpol.dipole_to_p(reduced_dipole, volume)

        raw_match = re.search(
            r"Raw polarization magnitude \|P\|:\s*\n\s*([-+0-9.eE]+) C/m\^2",
            result.stdout,
        )
        reduced_match = re.search(
            r"Branch-reduced polarization magnitude \|P_red\|:\s*\n\s*([-+0-9.eE]+) C/m\^2",
            result.stdout,
        )
        reduced_micro_match = re.search(
            r"Branch-reduced polarization magnitude \|P_red\|\*1e6:\s*\n\s*([-+0-9.eE]+) uC/m\^2",
            result.stdout,
        )

        self.assertIsNotNone(raw_match)
        self.assertIsNotNone(reduced_match)
        self.assertIsNotNone(reduced_micro_match)
        self.assertAlmostEqual(
            float(raw_match.group(1)),
            lcalcpol.polarization_magnitude(raw_p),
        )
        self.assertAlmostEqual(
            float(reduced_match.group(1)),
            lcalcpol.polarization_magnitude(reduced_p),
        )
        self.assertAlmostEqual(
            float(reduced_micro_match.group(1)),
            lcalcpol.polarization_magnitude(reduced_p) * 1e6,
        )

    def test_compare_reports_hashes_quanta_and_insufficient_same_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            no_poscar = Path(directory) / "noSOC-POSCAR"
            soc_poscar = Path(directory) / "SOC-POSCAR"
            no_outcar = Path(directory) / "noSOC-OUTCAR"
            soc_outcar = Path(directory) / "SOC-OUTCAR"
            no_poscar.write_text((FIXTURES / "POSCAR").read_text(), encoding="utf-8")
            soc_poscar.write_text((FIXTURES / "POSCAR").read_text(), encoding="utf-8")
            no_outcar.write_text((FIXTURES / "OUTCAR").read_text(), encoding="utf-8")
            soc_outcar.write_text(
                "Ionic dipole moment: p[ion] = ( 1.1E+0, -2.0, 3.5 )\n"
                "Total electronic dipole moment: p[elc] = ( -0.25, 0.5, -1.5 )\n",
                encoding="utf-8",
            )
            result = lcalcpol.compare_polarizations(no_outcar, no_poscar, soc_outcar, soc_poscar)
            self.assertTrue(result["comparison"]["lattice_match"])
            self.assertEqual(result["comparison"]["same_branch_delta_p"]["status"], "INSUFFICIENT")
            self.assertIsNone(result["comparison"]["same_branch_delta_p"]["value_c_m2"])
            self.assertNotIn("delta_p_same_branch_c_m2", result["comparison"])
            self.assertEqual(len(result["noSOC"]["polarization_quantum"]["vectors_c_m2"]), 3)
            self.assertEqual(len(result["SOC"]["inputs"]["OUTCAR"]["sha256"]), 64)

    def test_compare_rejects_mismatched_lattices(self):
        with tempfile.TemporaryDirectory() as directory:
            no_poscar = Path(directory) / "noSOC-POSCAR"
            soc_poscar = Path(directory) / "SOC-POSCAR"
            no_outcar = Path(directory) / "noSOC-OUTCAR"
            soc_outcar = Path(directory) / "SOC-OUTCAR"
            no_poscar.write_text((FIXTURES / "POSCAR").read_text(), encoding="utf-8")
            soc_poscar.write_text((FIXTURES / "POSCAR").read_text().replace("3.0", "3.1", 1), encoding="utf-8")
            no_outcar.write_text((FIXTURES / "OUTCAR").read_text(), encoding="utf-8")
            soc_outcar.write_text((FIXTURES / "OUTCAR").read_text(), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lattices differ"):
                lcalcpol.compare_polarizations(no_outcar, no_poscar, soc_outcar, soc_poscar)


if __name__ == "__main__":
    unittest.main()
