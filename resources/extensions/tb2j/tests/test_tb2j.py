import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tb2j.py"
spec = importlib.util.spec_from_file_location("fpilot_tb2j", SCRIPT)
tb2j = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tb2j
spec.loader.exec_module(tb2j)

FIXTURES = ROOT / "tests" / "fixtures"


class Tb2jAdapterTests(unittest.TestCase):
    def copy_case(self, name):
        temp = tempfile.TemporaryDirectory()
        directory = Path(temp.name) / name
        shutil.copytree(FIXTURES / name, directory)
        self.addCleanup(temp.cleanup)
        return directory

    @staticmethod
    def request(input_dir, **changes):
        request = {
            "schema": "fpilot.tb2j-request.v1",
            "input_dir": str(input_dir),
            "mode": "spinor",
            "prefix": "wannier90",
            "efermi_ev": 0.25,
            "kmesh": [3, 3, 3],
            "elements": ["Fe"],
        }
        request.update(changes)
        return request

    def test_spinor_check_accepts_hr_centres_and_optional_win(self):
        directory = self.copy_case("spinor")
        result = tb2j.check_request(self.request(directory))
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["num_wann"], 1)
        self.assertEqual(result["kmesh"], [3, 3, 3])

    def test_missing_required_input_is_reported(self):
        directory = self.copy_case("spinor")
        (directory / "wannier90_centres.xyz").unlink()
        with self.assertRaises(tb2j.InputError):
            tb2j.check_request(self.request(directory))

    def test_invalid_mode_and_magnetic_selection_are_rejected(self):
        directory = self.copy_case("spinor")
        with self.assertRaisesRegex(tb2j.InputError, "mode"):
            tb2j.check_request(self.request(directory, mode="soc"))
        with self.assertRaisesRegex(tb2j.InputError, "elements or index"):
            tb2j.check_request(self.request(directory, elements=None))

    def test_collinear_hr_dimensions_must_match(self):
        directory = self.copy_case("collinear")
        request = self.request(
            directory,
            mode="collinear",
            prefix_up="wannier90_up",
            prefix_down="wannier90_down",
        )
        result = tb2j.check_request(request)
        self.assertEqual(result["status"], "VALID")
        down = directory / "wannier90_down_hr.dat"
        down.write_text(down.read_text(encoding="utf-8").replace("\n1\n1\n1\n", "\n2\n1\n1\n"), encoding="utf-8")
        with self.assertRaisesRegex(tb2j.InputError, "num_wann mismatch"):
            tb2j.check_request(request)

    def test_collinear_requires_centres_or_tb_for_each_prefix(self):
        directory = self.copy_case("collinear")
        (directory / "wannier90_up_centres.xyz").unlink()
        request = self.request(
            directory,
            mode="collinear",
            prefix_up="wannier90_up",
            prefix_down="wannier90_down",
        )
        with self.assertRaisesRegex(tb2j.InputError, "collinear up input"):
            tb2j.check_request(request)

    def test_fake_run_records_command_and_output(self):
        directory = self.copy_case("spinor")
        fake = FIXTURES / "fake_wann2j.py"
        output = directory / "result"
        request = self.request(
            directory,
            executable=[sys.executable, str(fake)],
            output_dir=str(output),
            emin_ev=-10.0,
            emax_ev=12.0,
            nz=80,
            nproc=2,
            orb_decomposition=True,
        )
        receipt = tb2j.run_request(request)
        self.assertEqual(receipt["status"], "OK")
        self.assertEqual(receipt["returncode"], 0)
        self.assertTrue((output / "exchange.out").is_file())
        self.assertTrue((output / "tb2j_run.json").is_file())
        self.assertIn("--spinor", receipt["argv"])
        self.assertIn("--kmesh", receipt["argv"])
        self.assertIn("--path", receipt["argv"])
        self.assertIn("--emin", receipt["argv"])
        self.assertIn("--emax", receipt["argv"])
        self.assertIn("--orb_decomposition", receipt["argv"])

    def test_doctor_reports_callable_without_version_gate(self):
        result = tb2j.doctor([sys.executable, str(FIXTURES / "fake_wann2j.py")])
        self.assertTrue(result["callable"])
        self.assertTrue(result["version_recognized"])
        self.assertEqual(result["version"], "0.0-fake")

    def test_missing_executable_is_reported_by_doctor_and_run(self):
        directory = self.copy_case("spinor")
        missing = str(directory / "missing-wann2j")
        diagnosis = tb2j.doctor(missing)
        self.assertEqual(diagnosis["status"], "ERROR")
        self.assertFalse(diagnosis["callable"])
        receipt = tb2j.run_request(self.request(directory, executable=missing))
        self.assertEqual(receipt["status"], "ERROR")
        self.assertIsNone(receipt["returncode"])
        self.assertIn("No such file", receipt["error"])

    def test_cli_resolves_relative_input_from_request_location(self):
        directory = self.copy_case("spinor")
        request_path = directory / "request.json"
        request = self.request(".")
        request_path.write_text(json.dumps(request), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = tb2j.main(["check", "--request", str(request_path)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(returncode, 0)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(Path(result["input_dir"]), directory.resolve())


if __name__ == "__main__":
    unittest.main()
