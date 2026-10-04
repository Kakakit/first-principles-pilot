import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pyw90.py"
spec = importlib.util.spec_from_file_location("fpilot_pyw90", SCRIPT)
pyw90 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pyw90
spec.loader.exec_module(pyw90)

COMPARE_SCRIPT = ROOT / "scripts" / "pyw90_compare.py"
compare_spec = importlib.util.spec_from_file_location("fpilot_pyw90_compare", COMPARE_SCRIPT)
pyw90_compare = importlib.util.module_from_spec(compare_spec)
sys.modules[compare_spec.name] = pyw90_compare
compare_spec.loader.exec_module(pyw90_compare)

FIXTURES = ROOT / "tests" / "fixtures"


class Pyw90AdapterTests(unittest.TestCase):
    def case(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        case = root / "case"
        case.mkdir()
        shutil.copy(FIXTURES / "wannier90.eig", case / "wannier90.eig")
        self.addCleanup(temp.cleanup)
        return root, case

    @staticmethod
    def request(case, **changes):
        request = {
            "schema": pyw90.REQUEST_SCHEMA,
            "input_dir": str(case),
            "command": ["eig", "--help"],
            "expected_inputs": ["wannier90.eig"],
            "expected_outputs": [],
            "output_dir": "results",
        }
        request.update(changes)
        return request

    def fake_executable(self):
        return [sys.executable, str(FIXTURES / "fake_pyw90.py")]

    def test_check_returns_normalized_argv(self):
        root, case = self.case()
        result = pyw90.check_request(self.request("case"), request_dir=root)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["argv"], ["pyw90", "eig", "--help"])
        self.assertEqual(Path(result["input_dir"]), case.resolve())

    def test_missing_expected_input_is_reported(self):
        root, case = self.case()
        (case / "wannier90.eig").unlink()
        with self.assertRaisesRegex(pyw90.InputError, "missing or empty"):
            pyw90.check_request(self.request("case"), request_dir=root)

    def test_auto_is_rejected(self):
        root, case = self.case()
        with self.assertRaisesRegex(pyw90.InputError, "auto is not supported"):
            pyw90.check_request(self.request("case", command=["auto", "run"]), request_dir=root)

    def test_schema_is_required(self):
        root, case = self.case()
        request = self.request("case")
        del request["schema"]
        with self.assertRaisesRegex(pyw90.InputError, "schema"):
            pyw90.check_request(request, request_dir=root)

    def test_matplotlib_cache_environment_is_created_only_when_missing(self):
        cache_dir = Path(tempfile.gettempdir()) / "fpilot-matplotlib-cache"
        with mock.patch.dict(pyw90.os.environ, {}, clear=True):
            environment = pyw90._process_env()
            self.assertEqual(environment["MPLCONFIGDIR"], str(cache_dir))
            self.assertTrue(cache_dir.is_dir())
        with mock.patch.dict(pyw90.os.environ, {"MPLCONFIGDIR": "/tmp/custom-mpl"}, clear=True):
            preserved = pyw90._process_env()
        self.assertEqual(preserved["MPLCONFIGDIR"], "/tmp/custom-mpl")

    def test_doctor_uses_python_package_version_when_cli_does_not_report_one(self):
        root, case = self.case()
        fake = root / "quiet.py"
        fake.write_text(
            "import sys\n"
            "print('help')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        with mock.patch.object(pyw90, "_python_package_version", return_value="1.0.7"):
            result = pyw90.doctor([sys.executable, str(fake)])
        self.assertEqual(result["version"], "1.0.7")
        self.assertEqual(result["version_source"], "python-package")

    def test_doctor_started_but_nonzero_probes_is_not_callable(self):
        root, case = self.case()
        fake = root / "failing.py"
        fake.write_text(
            "import sys\n"
            "print('pyw90 unavailable')\n"
            "sys.exit(3)\n",
            encoding="utf-8",
        )
        result = pyw90.doctor([sys.executable, str(fake)])
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["callable"])
        self.assertTrue(result["started"])
        self.assertEqual([item["returncode"] for item in result["probes"]], [3, 3])
        self.assertIn("unavailable", result["error"])

    def test_doctor_missing_executable_is_not_started(self):
        root, case = self.case()
        result = pyw90.doctor(str(root / "missing-pyw90"))
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["callable"])
        self.assertFalse(result["started"])
        self.assertIsNone(result["probes"][0]["returncode"])
        self.assertIn("No such file", result["error"])

    def test_fake_run_writes_receipt_and_checks_expected_output(self):
        root, case = self.case()
        request = self.request(
            "case",
            command=["eig"],
            executable=self.fake_executable(),
            expected_outputs=["eig-result.txt"],
            output_dir="run-record",
        )
        receipt = pyw90.run_request(request, request_dir=root)
        self.assertEqual(receipt["status"], "OK")
        self.assertEqual(receipt["returncode"], 0)
        output = root / "run-record"
        self.assertTrue((case / "eig-result.txt").is_file())
        self.assertTrue((output / "pyw90_run.json").is_file())
        self.assertTrue((output / "pyw90_stdout.txt").is_file())
        saved = json.loads((output / "pyw90_run.json").read_text(encoding="utf-8"))
        self.assertNotIn("stdout", saved)
        self.assertNotIn("stderr", saved)
        self.assertEqual(Path(saved["expected_inputs"][0]["path"]), (case / "wannier90.eig").resolve())
        self.assertTrue(Path(saved["logs"]["stdout"]).is_absolute())
        self.assertIn("fake pyw90 eig complete", (output / "pyw90_stdout.txt").read_text(encoding="utf-8"))
        self.assertEqual(receipt["argv"][-1], "eig")

    def test_doctor_reports_callable_version(self):
        result = pyw90.doctor(self.fake_executable())
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["callable"])
        self.assertEqual(result["version"], "1.0.7")

    def test_cli_resolves_relative_paths_from_request_location(self):
        root, case = self.case()
        request_path = root / "request.json"
        request_path.write_text(
            json.dumps(self.request("case", executable=self.fake_executable(), command=["eig"])),
            encoding="utf-8",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = pyw90.main(["check", "--request", str(request_path)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(returncode, 0)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(Path(result["input_dir"]), case.resolve())

    def test_request_relative_executable_is_not_resolved_from_input_dir(self):
        root, case = self.case()
        tool = root / "tools" / "fake.py"
        tool.parent.mkdir()
        shutil.copy(FIXTURES / "fake_pyw90.py", tool)
        result = pyw90.check_request(
            self.request("case", executable=[sys.executable, "tools/fake.py"]),
            request_dir=root,
        )
        self.assertEqual(result["executable"][1], "tools/fake.py")
        relative_head = pyw90.check_request(
            self.request("case", executable="tools/fake.py"),
            request_dir=root,
        )
        self.assertEqual(Path(relative_head["executable"][0]), tool.resolve())


class Pyw90CompareWorkflowTests(unittest.TestCase):
    def case(self, mismatched_window=False):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        self.addCleanup(temp.cleanup)
        (root / "OUTCAR").write_text(" E-fermi : 0.000000 XC(G=0)\n", encoding="utf-8")
        (root / "KPOINTS").write_text(
            "path\n3\nLine-mode\nReciprocal\n"
            "0 0 0 GAMMA\n0.5 0 0 X\n\n"
            "0.5 0 0 X\n0.5 0.5 0 M\n",
            encoding="utf-8",
        )
        x = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0]
        dft_bands = [
            [-2.0, -1.9, -1.8, -1.8, -1.7, -1.6],
            [-0.4, -0.2, 0.0, 0.0, 0.2, 0.4],
            [0.8, 0.9, 1.0, 1.0, 1.1, 1.2],
        ]
        w90_bands = [
            [-0.39, -0.19, 0.01, 0.01, 0.21, 0.41],
            [0.79, 0.89, 0.99, 0.99, 1.09, 1.19],
        ]

        def write_bands(path, bands):
            chunks = []
            for band in bands:
                chunks.append("\n".join(f"{xx:.6f} {energy:.6f}" for xx, energy in zip(x, band)))
            path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")

        for channel in pyw90_compare.CHANNELS:
            write_bands(root / f"dft_{channel}.dat", dft_bands)
            write_bands(root / f"wan_{channel}.dat", w90_bands)
            (root / f"wan_{channel}.labelinfo.dat").write_text(
                "GAMMA 1 0.0 0 0 0\nX 3 1.0 0.5 0 0\n"
                "X 4 1.0 0.5 0 0\nM 6 2.0 0.5 0.5 0\n",
                encoding="utf-8",
            )
            win_max = 3.0 if mismatched_window else 2.0
            (root / f"wan_{channel}.win").write_text(
                "num_wann = 2\nnum_bands = 3\nexclude_bands = 1\n"
                f"dis_win_min = -2\ndis_win_max = {win_max}\n"
                "dis_froz_min = -1\ndis_froz_max = 1\n",
                encoding="utf-8",
            )
            (root / f"wan_{channel}.wout").write_text(
                "Number of Wannier Functions : 2\n"
                "Number of input Bloch states : 3\n"
                "Outer: -2.0 to 2.0 (eV)\nInner: -1.0 to 1.0 (eV)\n"
                "Disentanglement convergence criteria satisfied\n",
                encoding="utf-8",
            )
            (root / f"wan_{channel}.chk").write_bytes(b"checkpoint")
        request = {
            "schema": pyw90_compare.SCHEMA,
            "dft": {
                "spin_mode": "collinear-ispin2",
                "fermi_eV": 0.0,
                "fermi_source": "OUTCAR",
                "kpoints": "KPOINTS",
                "expected_bands": 3,
                "bands": {
                    "spin_up": "dft_spin_up.dat",
                    "spin_down": "dft_spin_down.dat",
                },
            },
            "wannier": {
                channel: {
                    "label": channel.replace("_", " ").title(),
                    "seedname": "wan.1" if channel == "spin_up" else "wan.2",
                    "expected_num_wann": 2,
                    "win": f"wan_{channel}.win",
                    "wout": f"wan_{channel}.wout",
                    "checkpoint": f"wan_{channel}.chk",
                    "band": f"wan_{channel}.dat",
                    "labelinfo": f"wan_{channel}.labelinfo.dat",
                }
                for channel in pyw90_compare.CHANNELS
            },
            "comparison": {
                "display_window_relative_eV": [-2, 2],
                "metric_window_relative_eV": [-2, 2],
                "dft_band_offset": "auto",
                "allow_lineage_mismatch_for_diagnostic": False,
            },
            "output_dir": "output",
        }
        return root, request

    def test_complete_compare_contract_and_metrics(self):
        root, request = self.case()
        checked = pyw90_compare.check_compare_request(request, request_dir=root)
        self.assertEqual(checked["status"], "VALID")
        self.assertEqual(checked["channels"]["spin_up"]["lineage"]["status"], "MATCH")
        receipt = pyw90_compare.run_compare_request(request, request_dir=root)
        self.assertEqual(receipt["status"], "OK")
        metrics = json.loads((root / "output" / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["channels"]["spin_up"]["offset"], 1)
        self.assertAlmostEqual(metrics["channels"]["spin_up"]["rmse_meV"], 10.0, places=5)
        for suffix in ("png", "tiff", "pdf", "svg"):
            self.assertTrue((root / "output" / f"dft_wannier_spin_comparison.{suffix}").is_file())

    def test_lineage_mismatch_blocks_without_explicit_diagnostic_override(self):
        root, request = self.case(mismatched_window=True)
        with self.assertRaisesRegex(pyw90_compare.InputError, "lineage is MISMATCH"):
            pyw90_compare.check_compare_request(request, request_dir=root)

    def test_lineage_override_is_visible_not_silent(self):
        root, request = self.case(mismatched_window=True)
        request["comparison"]["allow_lineage_mismatch_for_diagnostic"] = True
        checked = pyw90_compare.check_compare_request(request, request_dir=root)
        self.assertEqual(checked["status"], "VALID_WITH_WARNINGS")
        self.assertIn("explicit diagnostic override", checked["warnings"][0])

    def test_segment_interpolation_uses_actual_nonuniform_abscissa(self):
        arrays = {
            "dft_x": pyw90_compare.np.asarray([0.0, 0.2, 1.0]),
            "dft_energy": pyw90_compare.np.asarray([[0.0], [0.2], [1.0]]),
            "dft_segments": [(0, 2)],
            "w90_x": pyw90_compare.np.asarray([0.0, 0.5, 1.0]),
            "w90_energy": pyw90_compare.np.asarray([[0.0], [0.5], [1.0]]),
            "w90_segments": [(0, 2)],
        }
        dft_interpolated, w90 = pyw90_compare._interpolated_pairs(arrays)
        pyw90_compare.np.testing.assert_allclose(dft_interpolated[:, 0], [0.0, 0.5, 1.0])
        pyw90_compare.np.testing.assert_allclose(dft_interpolated, w90)


if __name__ == "__main__":
    unittest.main()
