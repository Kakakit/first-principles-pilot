import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pyw90_window_scan.py"
spec = importlib.util.spec_from_file_location("fpilot_pyw90_window_scan", SCRIPT)
window_scan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = window_scan
spec.loader.exec_module(window_scan)


class Pyw90WindowScanTests(unittest.TestCase):
    def test_measure_protocol_fixed_reference_keeps_out_of_target_residual(self):
        import csv
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'curves.csv'
            with path.open('w') as stream:
                writer=csv.writer(stream);writer.writerow(['method','k_index','band','energy_relative_eV'])
                for k in (1,2):
                    writer.writerow(['DFT',k,1,0.0])
                    writer.writerow(['Wannier90',k,1,10.0])
            req={'curves_csv':str(path),'target_relative_eV':[-1,1],'dft_band_offset':0}
            result=window_scan.measure_curves(req)
            self.assertEqual(result['status'],'MEASURED')
            self.assertEqual(result['reference_values'],2)
            self.assertEqual(result['rmse_meV'],10000)
            req['dft_band_offset']=1
            with self.assertRaises(window_scan.InputError):window_scan.measure_curves(req)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "scan-output"
        self._write_fixture()
        self.request = self._request()

    def tearDown(self):
        self.temp.cleanup()

    def _write_fixture(self):
        self.template = self.source / "wan.win"
        self.template.write_text(
            "num_wann = 2\n"
            "num_bands = 3\n"
            "dis_win_min = -2.0\n"
            "dis_win_max = 2.0\n"
            "dis_froz_min = -1.0\n"
            "dis_froz_max = 1.0\n"
            "begin projections\nFe:d\nend projections\n",
            encoding="utf-8",
        )
        # The regular-grid .eig is deliberately different from the line-path
        # DFT file below.  Feasibility must use this file, not dft_band.
        eig_rows = []
        eig_values = [
            [-2.0, -0.4, 0.8],
            [-1.9, -0.2, 0.9],
            [-1.8, 0.0, 1.0],
            [-1.8, 0.0, 1.0],
        ]
        for kpoint, row in enumerate(eig_values, 1):
            for band, energy in enumerate(row, 1):
                eig_rows.append(f"{band:12d}{kpoint:12d}{energy:18.8f}")
        self.eig = self.source / "wan.eig"
        self.eig.write_text("\n".join(eig_rows) + "\n", encoding="utf-8")
        x = [0.0, 0.5, 1.0, 0.0, 0.5, 1.0]
        dft_bands = [
            [-2.0, -1.9, -1.8, -1.8, -1.7, -1.6],
            [-0.4, -0.2, 0.0, 0.0, 0.2, 0.4],
            [0.8, 0.9, 1.0, 1.0, 1.1, 1.2],
        ]
        self.dft = self.source / "bnd.dat"
        self._write_bands(self.dft, x, dft_bands)
        self.shared = self.source / "wan.amn"
        self.shared.write_text("interface fixture\n", encoding="utf-8")

    @staticmethod
    def _write_bands(path, x, bands):
        blocks = []
        for band in bands:
            blocks.append("\n".join(f"{xx:.6f} {energy:.6f}" for xx, energy in zip(x, band)))
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    def _request(self):
        return {
            "schema": window_scan.SCHEMA,
            "source": {
                "spin": "spin_down",
                "seedname": "wan",
                "template_win": "source/wan.win",
                "eig": "source/wan.eig",
                "dft_band": "source/bnd.dat",
                "fermi_eV": 0.0,
                "expected_num_wann": 2,
            },
            "search": {
                "initial": {
                    "dis_win_min": -2.0,
                    "dis_win_max": 2.0,
                    "dis_froz_min": -1.0,
                    "dis_froz_max": 1.0,
                },
                "offsets_eV": [-0.5, 0.0, 0.5],
                "eps_eV": 0.004,
                "max_candidates": 12,
            },
            "target": {
                "mode": "explicit_energy",
                "energy_window_relative_eV": [-0.4, 0.4],
            },
            "comparison": {
                "metric_window_relative_eV": [-2.0, 2.0],
                "dft_band_offset": "auto",
            },
            "refinement": {
                "max_rounds": 2,
                "step_eV": [0.5, 0.25],
                "candidates_per_round": 8,
            },
            "review": {"quality": "not_reviewed", "spin": "spin_down", "note": None},
            "execution": {
                "argv": ["/bin/echo", "{seedname}"],
                "max_workers": 2,
                "timeout_seconds": 5,
                "shared_inputs": ["source/wan.amn"],
                "shared_input_mode": "copy",
                "result_files": {
                    "band": "{seedname}_band.dat",
                    "labelinfo": "{seedname}_band.labelinfo.dat",
                    "wout": "{seedname}.wout",
                },
            },
            "output_dir": "scan-output",
        }

    def _plan(self):
        result = window_scan.plan_scan(self.request, request_dir=self.root)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        return result, manifest

    def _write_candidate_result(self, candidate, quality="good", mismatch=False):
        directory = Path(candidate["directory"])
        x = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0]
        if quality == "good":
            bands = [
                [-0.39, -0.19, 0.01, 0.01, 0.21, 0.41],
                [0.79, 0.89, 0.99, 0.99, 1.09, 1.19],
            ]
        else:
            bands = [[energy + 0.8 for energy in band] for band in [
                [-0.39, -0.19, 0.01, 0.01, 0.21, 0.41],
                [0.79, 0.89, 0.99, 0.99, 1.09, 1.19],
            ]]
        self._write_bands(directory / "wan_band.dat", x, bands)
        (directory / "wan_band.labelinfo.dat").write_text(
            "GAMMA 1 0.0 0 0 0\n"
            "X 3 1.0 0.5 0 0\n"
            "X 4 0.0 0.5 0 0\n"
            "M 6 1.0 0.5 0.5 0\n",
            encoding="utf-8",
        )
        windows = candidate["windows_absolute_eV"]
        outer_min = windows["dis_win_min"] - (1.0 if mismatch else 0.0)
        wout_window = (
            f"Outer: {outer_min:.5f} to {windows['dis_win_max']:.5f} (eV)\n"
            f"Inner: {windows['dis_froz_min']:.5f} to {windows['dis_froz_max']:.5f} (eV)\n"
        )
        if quality == "incomplete":
            convergence = "Total Execution Time 0.1\n"
        else:
            convergence = (
                "Using band disentanglement : T\n"
                "Disentanglement convergence criteria satisfied\n"
                "Wannierisation convergence criteria satisfied\n"
                "Final State\n"
                "Total Execution Time 0.1\n"
            )
        (directory / "wan.wout").write_text(wout_window + convergence, encoding="utf-8")

    def test_check_requires_eig_and_plan_uses_eig_state_counts(self):
        no_eig = json.loads(json.dumps(self.request))
        del no_eig["source"]["eig"]
        with self.assertRaisesRegex(window_scan.InputError, "source.eig"):
            window_scan.check_scan_request(no_eig, request_dir=self.root)
        checked = window_scan.check_scan_request(self.request, request_dir=self.root)
        self.assertEqual(checked["source"]["eig_bands"], 3)
        self.assertEqual(checked["search"]["eps_eV"], 0.004)
        self.assertEqual(checked["comparison"]["ranking_window_relative_eV"], [-0.4, 0.4])
        self.assertEqual(
            checked["comparison"]["ranking_basis"],
            "scientific_subwindow_inside_projector_support",
        )
        self.assertEqual(checked["target"]["state_counts"]["most"], 1)
        result, manifest = self._plan()
        self.assertGreater(result["candidate_count"], 0)
        self.assertTrue(all(
            row["state_counts"]["outer_least"] >= 2
            and row["state_counts"]["frozen_most"] <= 1
            and row["state_counts"]["disentanglement_headroom_min"] >= 1
            for row in manifest["candidates"]
        ))
        self.assertEqual(self.template.read_text(encoding="utf-8").splitlines()[2], "dis_win_min = -2.0")
        self.assertTrue(all(Path(row["win"]).is_file() for row in manifest["candidates"]))

    def test_auto_projector_resolves_target_from_amn_and_keeps_conduction_weight(self):
        amn = self.source / "wan_project.amn"
        rows = ["projectability fixture", "3 4 1"]
        amplitudes = {1: 0.1, 2: 1.0, 3: 0.9}
        for kpoint in range(1, 5):
            for band in range(1, 4):
                rows.append(f"{band} 1 {kpoint} {amplitudes[band]:.8f} 0.0")
        amn.write_text("\n".join(rows) + "\n", encoding="utf-8")
        request = json.loads(json.dumps(self.request))
        request["target"] = {
            "mode": "auto_projector",
            "projectability": {"source": "amn", "path": "source/wan_project.amn"},
            "cumulative_weight_coverage": 0.9,
            "margin_eV": 0.0,
        }
        request["comparison"].pop("metric_window_relative_eV")
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        self.assertEqual(checked["target"]["mode"], "auto_projector")
        self.assertEqual(checked["target"]["energy_window_relative_eV"], [-0.4, 1.0])
        self.assertGreater(checked["target"]["weight_summary"]["selected_fraction"], 0.9)
        self.assertGreater(checked["comparison"]["ranking_window_relative_eV"][1], 0.0)
        windows = window_scan._candidate_windows(checked, None, 0)
        self.assertTrue(windows)
        self.assertTrue(all(row["dis_win_max"] >= 1.0 for row in windows))
        self.assertTrue(all(
            window_scan._window_valid(
                row,
                checked["_eig_energies"],
                2,
                tuple(checked["target"]["energy_window_relative_eV"]),
                1,
                3,
            )[1]["disentanglement_headroom_min"] >= 1
            for row in windows
        ))

    def test_processed_procar_csv_may_use_a_different_line_path_grid(self):
        table = self.source / "bands_spin_down.csv"
        rows = ["k_index,band,energy_eV,Fe:d"]
        energies = [
            [-2.0, -0.4, 0.8],
            [-1.9, -0.2, 0.9],
            [-1.8, 0.0, 1.0],
            [-1.8, 0.0, 1.0],
            [-1.7, 0.2, 1.1],
            [-1.6, 0.4, 1.2],
        ]
        for kpoint, state_energies in enumerate(energies, 1):
            for band, energy in enumerate(state_energies, 1):
                weight = {1: 0.01, 2: 1.0, 3: 0.81}[band]
                rows.append(f"{kpoint},{band},{energy},{weight}")
        table.write_text("\n".join(rows) + "\n", encoding="utf-8")
        request = json.loads(json.dumps(self.request))
        request["target"] = {
            "mode": "auto_projector",
            "projectability": {
                "source": "csv",
                "path": "source/bands_spin_down.csv",
                "selectors": ["Fe:d"],
                "energy_reference": "relative_to_fermi",
            },
            "cumulative_weight_coverage": 0.9,
        }
        request["comparison"].pop("metric_window_relative_eV")
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        self.assertEqual(checked["source"]["eig_kpoints"], 4)
        self.assertGreater(checked["target"]["energy_window_relative_eV"][1], 0.0)
        self.assertTrue(window_scan._candidate_windows(checked, None, 0))

    def test_target_requires_a_lower_global_gap(self):
        no_lower_gap = json.loads(json.dumps(self.request))
        no_lower_gap["target"]["energy_window_relative_eV"] = [-2.0, -1.6]
        with self.assertRaisesRegex(window_scan.InputError, "no complete global"):
            window_scan.check_scan_request(no_lower_gap, request_dir=self.root)

    def test_no_explicit_initial_uses_eig_gap_suggestion(self):
        request = json.loads(json.dumps(self.request))
        request["search"]["initial"] = {}
        request["search"]["offsets_eV"] = [0.0]
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        windows = window_scan._candidate_windows(checked, None, 0)
        self.assertTrue(windows)
        # The first valid suggestion is bounded by actual .eig states and not
        # by the unrelated line-path dft_band file.
        for row in windows:
            valid, counts = window_scan._window_valid(
                row,
                [[value for value in line] for line in checked["_eig_energies"]],
                2,
                tuple(checked["target"]["energy_window_relative_eV"]),
            )
            self.assertTrue(valid)
            self.assertGreaterEqual(counts["outer_least"], 2)
            self.assertEqual(counts["target_covered"], 1)
            self.assertGreater(row["dis_win_min"], -1.8)
            self.assertLess(row["dis_win_min"], -0.4)

    def test_invalid_historical_initial_is_replaced_by_target_led_candidates(self):
        checked = window_scan.check_scan_request(self.request, request_dir=self.root)
        windows = window_scan._candidate_windows(
            checked, checked["search"]["initial"], 0
        )
        self.assertNotIn(checked["search"]["initial"], windows)
        self.assertTrue(all(row["dis_froz_min"] <= -0.4 for row in windows))
        self.assertTrue(all(row["dis_froz_max"] <= 0.5 for row in windows))
        self.assertTrue(any(row["dis_froz_max"] < 0.4 for row in windows))

    def test_pyw90_suggestions_prioritize_minimal_extra_outer_states(self):
        request = json.loads(json.dumps(self.request))
        request["search"]["initial"] = {}
        request["search"]["max_candidates"] = 2
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        windows = window_scan._candidate_windows(checked, None, 0)
        counts = [
            window_scan._window_valid(
                row,
                checked["_eig_energies"],
                2,
                tuple(checked["target"]["energy_window_relative_eV"]),
            )[1]
            for row in windows
        ]
        self.assertTrue(all(item["outer_least"] == 2 for item in counts))
        self.assertEqual(
            [item["outer_most"] for item in counts],
            sorted(item["outer_most"] for item in counts),
        )

    def test_small_initial_batch_uses_gap_and_preserves_frozen_headroom(self):
        request = json.loads(json.dumps(self.request))
        request["search"]["max_candidates"] = 4
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        windows = window_scan._candidate_windows(
            checked, checked["search"]["initial"], 0
        )
        self.assertEqual(len(windows), 4)
        self.assertTrue(all(-1.8 < row["dis_win_min"] < -0.4 for row in windows))
        self.assertTrue(any(row["dis_froz_min"] < -0.4 for row in windows))
        self.assertTrue(all(row["dis_froz_max"] <= 0.5 for row in windows))

    def test_explicit_frozen_candidates_are_independent_of_projector_support(self):
        request = json.loads(json.dumps(self.request))
        request["target"]["energy_window_relative_eV"] = [-0.4, 1.1]
        request["search"]["frozen_windows_relative_eV"] = [[-1.0, 0.0]]
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        windows = window_scan._candidate_windows(checked, None, 0)
        self.assertTrue(windows)
        self.assertTrue(all(row["dis_win_max"] >= 1.1 for row in windows))
        self.assertTrue(all(row["dis_froz_max"] == 0.0 for row in windows))

    def test_ranking_window_may_be_a_scientific_subset_of_target(self):
        request = json.loads(json.dumps(self.request))
        request["target"]["energy_window_relative_eV"] = [-0.4, 1.1]
        request["comparison"]["ranking_window_relative_eV"] = [-0.4, 0.4]
        checked = window_scan.check_scan_request(request, request_dir=self.root)
        self.assertEqual(checked["comparison"]["ranking_window_relative_eV"], [-0.4, 0.4])

    def test_plan_is_idempotent_and_does_not_overwrite_candidate_win(self):
        _, first_manifest = self._plan()
        candidate = first_manifest["candidates"][0]
        win_path = Path(candidate["win"])
        before = win_path.read_text(encoding="utf-8")
        mtime = win_path.stat().st_mtime_ns
        time.sleep(0.01)
        _, second_manifest = self._plan()
        self.assertEqual(
            [row["candidate_id"] for row in first_manifest["candidates"]],
            [row["candidate_id"] for row in second_manifest["candidates"]],
        )
        self.assertEqual(win_path.read_text(encoding="utf-8"), before)
        self.assertEqual(win_path.stat().st_mtime_ns, mtime)

    def test_refine_requires_manual_poor_and_is_hard_bounded(self):
        _, manifest = self._plan()
        before = json.dumps(manifest, sort_keys=True)
        no_refine = window_scan.refine_scan(self.request, request_dir=self.root)
        self.assertEqual(no_refine["status"], "NOT_TRIGGERED")
        after = Path(window_scan._manifest_path(self.output)).read_text(encoding="utf-8")
        self.assertEqual(json.loads(after), json.loads(before))

        candidate = manifest["candidates"][0]
        self._write_candidate_result(candidate, quality="good")
        window_scan.score_scan(self.request, request_dir=self.root)
        poor = json.loads(json.dumps(self.request))
        poor["review"] = {
            "quality": "poor",
            "spin": "spin_down",
            "note": "visible mismatch remains near the Fermi level",
        }
        refined = window_scan.refine_scan(poor, request_dir=self.root)
        self.assertEqual(refined["status"], "REFINEMENT_PLANNED")
        self.assertEqual(refined["round"], 1)
        self.assertLessEqual(refined["added_candidate_count"], 8)
        updated_manifest = json.loads(Path(refined["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(updated_manifest["review_history"][-1]["quality"], "poor")
        self.assertIn("Fermi", updated_manifest["review_history"][-1]["note"])
        refined_again = window_scan.refine_scan(poor, request_dir=self.root)
        self.assertEqual(refined_again["round"], 2)
        with self.assertRaisesRegex(window_scan.InputError, "hard limit"):
            window_scan.refine_scan(poor, request_dir=self.root)

    def test_poor_review_requires_a_written_note(self):
        poor = json.loads(json.dumps(self.request))
        poor["review"] = {"quality": "poor", "spin": "spin_down", "note": None}
        with self.assertRaisesRegex(window_scan.InputError, "review.note"):
            window_scan.check_scan_request(poor, request_dir=self.root, action="refine")

    def test_hard_limits_and_forbidden_run_argv(self):
        too_many = json.loads(json.dumps(self.request))
        too_many["search"]["max_candidates"] = window_scan.MAX_CANDIDATES + 1
        with self.assertRaisesRegex(window_scan.InputError, "hard limit"):
            window_scan.check_scan_request(too_many, request_dir=self.root)
        too_many["search"]["max_candidates"] = 4
        too_many["refinement"]["max_rounds"] = window_scan.MAX_REFINEMENT_ROUNDS + 1
        with self.assertRaisesRegex(window_scan.InputError, "hard limit"):
            window_scan.check_scan_request(too_many, request_dir=self.root)
        bad = json.loads(json.dumps(self.request))
        bad["execution"]["argv"] = ["ssh", "host"]
        with self.assertRaisesRegex(window_scan.InputError, "forbidden"):
            window_scan.check_scan_request(bad, request_dir=self.root, action="run")
        bad["execution"]["argv"] = ["sh", "-c", "echo unsafe"]
        with self.assertRaisesRegex(window_scan.InputError, "shell wrapper"):
            window_scan.check_scan_request(bad, request_dir=self.root, action="run")
        bad_window = json.loads(json.dumps(self.request))
        bad_window["comparison"]["ranking_window_relative_eV"] = [-1.0, 0.5]
        with self.assertRaisesRegex(window_scan.InputError, "must lie inside"):
            window_scan.check_scan_request(bad_window, request_dir=self.root)

    def test_run_uses_shell_false_isolates_failure_and_is_idempotent(self):
        fake = self.root / "fake_wannier.py"
        fake.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "if Path('fail.marker').exists():\n"
            "    print('intentional failure')\n"
            "    sys.exit(7)\n"
            "print('fake local run')\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.request["execution"]["argv"] = [sys.executable, str(fake), "{seedname}"]
        _, manifest = self._plan()
        failed = Path(manifest["candidates"][0]["directory"])
        (failed / "fail.marker").write_text("x\n", encoding="utf-8")
        receipt = window_scan.run_scan(self.request, request_dir=self.root)
        self.assertEqual(receipt["selected"], len(manifest["candidates"]))
        self.assertEqual(receipt["failed_or_timeout"], 1)
        self.assertFalse(receipt["shell"])
        self.assertTrue((failed / "stdout.txt").is_file())
        self.assertTrue((failed / "stderr.txt").is_file())
        updated = json.loads(Path(receipt["manifest"]).read_text(encoding="utf-8"))
        statuses = {row["status"] for row in updated["candidates"]}
        self.assertIn("failed", statuses)
        self.assertIn("completed", statuses)
        resumed = window_scan.run_scan(self.request, request_dir=self.root)
        self.assertEqual(resumed["selected"], 0)

    def test_score_requires_convergence_and_matching_lineage_for_primary_rank(self):
        _, manifest = self._plan()
        incomplete, good, mismatch = manifest["candidates"][:3]
        self._write_candidate_result(good, quality="good")
        self._write_candidate_result(incomplete, quality="good")
        (Path(incomplete["directory"]) / "wan.wout").write_text(
            "Total Execution Time 0.1\n", encoding="utf-8"
        )
        self._write_candidate_result(mismatch, quality="good", mismatch=True)
        result = window_scan.score_scan(self.request, request_dir=self.root)
        self.assertEqual(result["best_candidate_id"], good["candidate_id"])
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        by_id = {row["candidate_id"]: row for row in payload["candidates"]}
        self.assertTrue(by_id[good["candidate_id"]]["eligible_for_primary_ranking"])
        self.assertEqual(
            by_id[good["candidate_id"]]["lineage"]["tolerance_eV"],
            window_scan.WOUT_WINDOW_TOLERANCE_EV,
        )
        self.assertFalse(by_id[incomplete["candidate_id"]]["eligible_for_primary_ranking"])
        self.assertFalse(by_id[mismatch["candidate_id"]]["eligible_for_primary_ranking"])
        self.assertTrue(by_id[incomplete["candidate_id"]]["fit_metrics_available"])
        self.assertTrue(Path(by_id[good["candidate_id"]]["review_figures"]["png"]).is_file())
        self.assertTrue(Path(by_id[good["candidate_id"]]["review_figures"]["pdf"]).is_file())
        self.assertTrue(Path(by_id[good["candidate_id"]]["review_figures"]["svg"]).is_file())
        compare = window_scan._load_compare_module()
        reset_x = compare.np.asarray([0.0, 0.5, 1.0, 0.0, 0.5, 1.0])
        reset_segments = compare.reset_segments(reset_x)
        self.assertEqual(
            compare.continuous_plot_x(reset_x, reset_segments).tolist(),
            [0.0, 0.5, 1.0, 1.0, 1.5, 2.0],
        )
        _, labels = compare._ticks(
            {
                "dft_segments": [(0, 2), (3, 5)],
                "w90_segments": [(0, 2), (3, 5)],
                "dft_x": [0.0, 0.5, 1.0, 1.0, 1.5, 2.0],
                "records": [
                    {"index": 0, "label": "GAMMA"},
                    {"index": 2, "label": "X"},
                    {"index": 3, "label": "X"},
                    {"index": 5, "label": "M"},
                ],
            }
        )
        self.assertEqual(labels, ["Γ", "X", "M"])
        self.assertFalse(payload["selection"]["spread_used_for_ranking"])
        self.assertEqual(
            payload["selection"]["ranking_basis"],
            "scientific_subwindow_inside_projector_support",
        )
        self.assertEqual(payload["selection"]["ranking_window_relative_eV"], [-0.4, 0.4])
        self.assertTrue(payload["selection"]["reference_sample_consistent"])
        self.assertEqual(
            payload["selection"]["ranking"],
            ["metrics.rmse_meV", "metrics.mae_meV", "metrics.max_abs_diff_meV", "candidate_id"],
        )
        self.assertIn("full_window_metrics", by_id[good["candidate_id"]])

    def test_target_metrics_drive_ranking_while_full_window_remains_diagnostic(self):
        _, manifest = self._plan()
        valence_best, full_window_best = manifest["candidates"][:2]
        self._write_candidate_result(valence_best, quality="good")
        self._write_candidate_result(full_window_best, quality="good")
        x = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0]
        self._write_bands(
            Path(valence_best["directory"]) / "wan_band.dat",
            x,
            [
                [-0.4, -0.2, 0.0, 0.0, 0.2, 0.4],
                [1.6, 1.7, 1.8, 1.8, 1.9, 2.0],
            ],
        )
        self._write_bands(
            Path(full_window_best["directory"]) / "wan_band.dat",
            x,
            [
                [-0.1, 0.1, 0.3, 0.3, 0.5, 0.7],
                [0.8, 0.9, 1.0, 1.0, 1.1, 1.2],
            ],
        )
        result = window_scan.score_scan(self.request, request_dir=self.root)
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        by_id = {row["candidate_id"]: row for row in payload["candidates"]}
        valence_row = by_id[valence_best["candidate_id"]]
        full_row = by_id[full_window_best["candidate_id"]]
        self.assertEqual(payload["candidates"][0]["candidate_id"], valence_best["candidate_id"])
        self.assertLess(valence_row["metrics"]["rmse_meV"], full_row["metrics"]["rmse_meV"])
        self.assertEqual(valence_row["metrics"]["sample_selector"], "dft_reference_energy")
        self.assertEqual(
            valence_row["metrics"]["reference_values"],
            full_row["metrics"]["reference_values"],
        )
        self.assertGreater(
            valence_row["full_window_metrics"]["rmse_meV"],
            full_row["full_window_metrics"]["rmse_meV"],
        )

    def test_auto_offset_mismatch_blocks_primary_best(self):
        _, manifest = self._plan()
        offset_zero, offset_one = manifest["candidates"][:2]
        self._write_candidate_result(offset_zero, quality="good")
        self._write_candidate_result(offset_one, quality="good")
        x = [0.0, 0.5, 1.0, 1.0, 1.5, 2.0]
        self._write_bands(
            Path(offset_zero["directory"]) / "wan_band.dat",
            x,
            [
                [-2.0, -1.9, -1.8, -1.8, -1.7, -1.6],
                [-0.4, -0.2, 0.0, 0.0, 0.2, 0.4],
            ],
        )
        self._write_bands(
            Path(offset_one["directory"]) / "wan_band.dat",
            x,
            [
                [-0.4, -0.2, 0.0, 0.0, 0.2, 0.4],
                [0.8, 0.9, 1.0, 1.0, 1.1, 1.2],
            ],
        )
        result = window_scan.score_scan(self.request, request_dir=self.root)
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        self.assertIsNone(result["best_candidate_id"])
        self.assertFalse(payload["selection"]["reference_sample_consistent"])
        self.assertEqual(
            {row["offset"] for row in payload["selection"]["observed_offset_reference_values"]},
            {0, 1},
        )
        populated = [row for row in payload["candidates"] if isinstance(row.get("metrics"), dict)]
        self.assertTrue(all(not row["eligible_for_primary_ranking"] for row in populated))

    def test_score_rejects_wout_without_explicit_window_lineage(self):
        _, manifest = self._plan()
        candidate = manifest["candidates"][0]
        self._write_candidate_result(candidate, quality="good")
        wout = Path(candidate["directory"]) / "wan.wout"
        text = wout.read_text(encoding="utf-8")
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith(("Outer:", "Inner:"))
        ) + "\n"
        wout.write_text(text, encoding="utf-8")
        result = window_scan.score_scan(self.request, request_dir=self.root)
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        row = next(item for item in payload["candidates"] if item["candidate_id"] == candidate["candidate_id"])
        self.assertEqual(row["lineage"]["status"], "PARTIAL")
        self.assertFalse(row["eligible_for_primary_ranking"])

    def test_optional_shape_gate_blocks_a_cross_gap_spike(self):
        self.request["comparison"]["dft_band_offset"] = 1
        self.request["comparison"]["max_excess_jump_eV"] = 0.5
        _, manifest = self._plan()
        candidate = manifest["candidates"][0]
        self._write_candidate_result(candidate, quality="good")
        self._write_bands(
            Path(candidate["directory"]) / "wan_band.dat",
            [0.0, 0.5, 1.0, 1.0, 1.5, 2.0],
            [
                [-0.4, 3.0, 0.0, 0.0, 0.2, 0.4],
                [0.8, 0.9, 1.0, 1.0, 1.1, 1.2],
            ],
        )
        result = window_scan.score_scan(self.request, request_dir=self.root)
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        row = next(
            item for item in payload["candidates"]
            if item["candidate_id"] == candidate["candidate_id"]
        )
        self.assertGreater(row["shape_diagnostics"]["max_excess_jump_eV"], 0.5)
        self.assertFalse(row["shape_eligible"])
        self.assertFalse(row["eligible_for_primary_ranking"])

    def test_missing_results_are_reported_and_sorted_after_eligible(self):
        _, manifest = self._plan()
        self._write_candidate_result(manifest["candidates"][0], quality="good")
        result = window_scan.score_scan(self.request, request_dir=self.root)
        payload = json.loads(Path(result["score"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["candidates"][0]["status"], "scored")
        self.assertTrue(all(row["status"] in {"missing", "not_eligible", "scored"} for row in payload["candidates"]))
        self.assertTrue(all(row["rank"] > 1 for row in payload["candidates"][1:]))
        self.assertGreater(result["missing_or_invalid_count"], 0)

    def test_cli_check_emits_json_contract(self):
        request_path = self.root / "request.json"
        request_path.write_text(json.dumps(self.request), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = window_scan.main(["check", "--request", str(request_path)])
        result = json.loads(stdout.getvalue())
        self.assertEqual(returncode, 0)
        self.assertEqual(result["schema"], window_scan.SCHEMA)
        self.assertIn("eig", result["source"])


if __name__ == "__main__":
    unittest.main()
