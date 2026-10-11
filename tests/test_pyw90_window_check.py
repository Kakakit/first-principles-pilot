import tempfile
import unittest
from pathlib import Path

import numpy as np
from backend import pyw90_window_check as check


class WindowCheckTests(unittest.TestCase):
    def test_envelope_count_is_not_the_exact_per_k_maximum(self):
        energies = np.array([[-.5, 2], [-2, .5]])
        self.assertEqual(check.state_counts(energies, -1, 1)['max'], 1)
        self.assertEqual(sum((energies.min(axis=0) <= 1) & (energies.max(axis=0) >= -1)), 2)

    def test_gap_midpoint_can_be_below_analysis_minimum(self):
        energies = np.array([[-8, -6, 1], [-7, -5.5, 2]])
        gaps = check.global_gaps(energies)
        gap = gaps[0]
        diagnostic = check.lower_diagnostic(energies, -6.3, gaps)
        self.assertEqual(diagnostic['classification'], 'gap')
        self.assertEqual(gap['midpoint_eV'], -6.5)
        self.assertGreater(gap['i+1_min'], -6.3)
        self.assertEqual((gap['lower_band'], gap['upper_band']), (1, 2))

    def test_lower_crossing_and_no_global_gap(self):
        energies = np.array([[-1, 2], [-2, .5]])
        self.assertEqual(check.lower_diagnostic(energies, -.5, check.global_gaps(energies))['classification'], 'gap')
        energies = np.array([[-.5, 1], [-2, -.8]])
        self.assertEqual(check.global_gaps(energies), [])
        self.assertEqual(check.lower_diagnostic(energies, -.4, [])['classification'], 'crossing')

    def test_complete_outer_and_frozen_constraints_and_defaults(self):
        energies = np.array([[-2, 0, 2], [-1, 1, 3]])
        defaults = check.evaluate_windows(energies, 2, {}, (-2, 3))
        self.assertTrue(defaults['valid'])
        self.assertIsNone(defaults['frozen'])
        invalid = check.evaluate_windows(energies, 2, {'dis_win_min':-.5,'dis_win_max':2,'dis_froz_max':3}, (-2,3))
        self.assertFalse(invalid['valid'])
        self.assertEqual(len(invalid['errors']), 2)
        overflow = check.evaluate_windows(energies, 2, {'dis_froz_max':3}, (-2,3))
        self.assertFalse(overflow['valid'])
        self.assertEqual(overflow['frozen']['max'], 3)

    def test_read_eig_rejects_missing_duplicate_nonfinite_and_wrong_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.eig'
            valid = '1 1 -2\n2 1 0\n1 2 -1\n2 2 1\n'
            path.write_text(valid)
            np.testing.assert_equal(check.read_eig(path), [[-2,0],[-1,1]])
            # Rows may arrive in a different order; their explicit identities remain authoritative.
            path.write_text('2 2 1\n1 1 -2\n1 2 -1\n2 1 0\n')
            np.testing.assert_equal(check.read_eig(path), [[-2,0],[-1,1]])
            for bad in [valid.rsplit('\n',2)[0], valid.replace('2 2 1','1 2 1'),
                        valid.replace('2 2 1','2 2 nan'),valid.replace('2 2 1','2 2 -3')]:
                path.write_text(bad)
                with self.assertRaises(ValueError):check.read_eig(path)

    def test_nonzero_fermi_full_candidates_and_spinor_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            eig, win = Path(tmp)/'test.eig', Path(tmp)/'test.win'
            eig.write_text('1 1 -2\n2 1 0\n3 1 2\n1 2 -1\n2 2 1\n3 2 3\n')
            win.write_text('NUM_WANN : 2\nspinors = true\nbegin projections\nFe:d\nend projections\ndis_win_min=-2\n')
            report = check.window_check({'eig_name':str(eig),'win_name':str(win),'seed':'wannier90',
                'fermi_eV':5.5,'absolute_range':[-2,3], 'absolute_windows':None,
                'candidates':{'frozen':[{'dis_froz_min':-1,'dis_froz_max':1}]}})
            self.assertEqual(report['basis']['channel'],'spinor')
            self.assertEqual(report['basis']['projections'],['Fe:d'])
            self.assertEqual(report['current']['relative_windows_eV']['dis_win_min'],-7.5)
            self.assertEqual(report['candidates']['frozen'][0]['frozen']['max'],2)
            self.assertTrue(report['candidates']['frozen'][0]['valid'])

    def test_frozen_55_and_57_states_use_same_55th_upper_cutoff(self):
        energies = np.array([np.arange(61), np.arange(61) + .1])
        for upper in (54, 56):
            report = check.frozen_boundary_diagnostic(energies, 54, 0, upper)
            self.assertEqual(report['upper']['limit_eV'], 54)
            self.assertEqual(report['upper']['limiting_states'][0]['band'], 55)
            self.assertEqual(report['upper']['limiting_states'][0]['rank'], 55)
            self.assertEqual(report['upper']['trial_counts']['max'], 54)
            self.assertEqual(report['upper']['exact_n_interval_eV'], [53.1, 54])
        self.assertEqual(report['lower']['limit_eV'], 2)
        self.assertEqual(report['lower']['trial_counts']['max'], 54)

    def test_crossing_edge_changes_eligible_band_index_at_each_k(self):
        energies = np.array([[-1, 0, 1, 2, 3], [-2, -1, .5, 1.5, 2.5]])
        report = check.frozen_boundary_diagnostic(energies, 2, 0, 3)
        cutoff = report['upper']
        # Global minimum of the witness band is 1.5, but at that k point it
        # is the second eligible state and should still be kept.
        self.assertEqual(cutoff['limit_eV'], 2)
        self.assertEqual(cutoff['limiting_states'][0]['min_eV'], 1.5)
        self.assertEqual(cutoff['limiting_states'][0]['kpoint'], 1)
        self.assertEqual(cutoff['trial_counts']['max'], 2)
        self.assertEqual(cutoff['exact_n_interval_eV'], [1.5, 2])

    def test_lower_cutoff_and_degenerate_states_may_remove_more_than_one(self):
        energies = np.array([[0, 1, 1, 1, 2], [0, 1, 1, 1, 3]])
        report = check.frozen_boundary_diagnostic(energies, 2, 0, 3)
        for side in ('upper', 'lower'):
            cutoff = report[side]
            self.assertEqual(cutoff['limit_eV'], 1)
            self.assertEqual(cutoff['limiting_state_count'], 2)
            self.assertEqual(cutoff['trial_counts']['max'], 1)
            self.assertIsNone(cutoff['exact_n_interval_eV'])
        self.assertEqual(check.state_counts(energies, 0, 1)['max'], 4)
        self.assertEqual(check.state_counts(energies, 1, 3)['max'], 4)

    def test_cutoffs_cover_all_k_and_not_only_max_count_witness(self):
        energies = np.array([[0, 1, 4, 7, 9], [0, 2, 3, 6, 10], [-1, 1, 5, 8, 11]])
        report = check.frozen_boundary_diagnostic(energies, 2, 0, 10)
        for side in ('upper','lower'):
            cutoff = report[side]
            edge = cutoff['trial_value_eV']
            counts = [sum(0 <= e <= edge if side == 'upper' else edge <= e <= 10 for e in row) for row in energies]
            self.assertLessEqual(max(counts), 2)
            self.assertEqual(max(counts), cutoff['trial_counts']['max'])
        self.assertEqual(report['upper']['limit_eV'], 3)
        self.assertEqual(report['lower']['limit_eV'], 4)

    def test_no_frozen_window_or_insufficient_states_have_no_cutoff(self):
        energies = np.array([[0, 1], [2, 3]])
        self.assertIsNone(check.frozen_boundary_diagnostic(energies, 2, None, None))
        report = check.frozen_boundary_diagnostic(energies, 2, 0, 3)
        self.assertIsNone(report['upper'])
        self.assertIsNone(report['lower'])

    def test_window_report_retains_absolute_cutoffs_and_basis_for_spinor(self):
        with tempfile.TemporaryDirectory() as tmp:
            eig, win = Path(tmp)/'test.eig', Path(tmp)/'test.win'
            eig.write_text('1 1 5\n2 1 6\n3 1 7\n1 2 5.5\n2 2 6.5\n3 2 7.5\n')
            win.write_text('num_wann=2\nspinors=true\n')
            report = check.window_check({'eig_name':str(eig),'win_name':str(win),'seed':'wannier90',
                'fermi_eV':5,'absolute_range':[5,7.5], 'absolute_windows':{'dis_froz_min':5,'dis_froz_max':7.5}})
            self.assertEqual(report['current']['frozen']['max'], 3)
            self.assertEqual(report['frozen_boundary']['upper']['limit_eV'], 7)
            self.assertEqual(report['frozen_boundary']['upper']['trial_counts']['max'], 2)
            self.assertEqual(report['basis']['channel'], 'spinor')


if __name__=='__main__':unittest.main()
