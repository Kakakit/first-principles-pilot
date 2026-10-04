import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend import wannier_bands, wannier_plot_restart as plot


class PlotRestartTests(unittest.TestCase):
    def test_real_frozen_win_accepts_independent_restart_plot(self):
        from backend import execution, wannier_execution
        source = next((item for item in execution.history()
                       if item['id'].startswith('637362f5') and item.get('kind') == 'wannier90'), None)
        if source is None:
            self.skipTest('历史 Wannier90 样例未安装在当前工作区')
        bundle = wannier_execution._bundle(source)
        proposal = {'path_lines': ['GAMMA 0 0 0 X 0 0.5 -0.5'], 'bands_num_points': 40}
        win = plot._plot_win(bundle, proposal)
        self.assertIn('restart = plot', win)
        wannier_bands.assert_applied(win, proposal)
        with self.assertRaisesRegex(ValueError, 'kpoint_path'):
            wannier_bands.assert_applied(win, {**proposal, 'path_lines': ['X 0 0 0 M 0.5 0 0']})

    def test_path_requires_completed_same_source_and_missing_chk_blocks(self):
        with patch.object(plot, '_source', side_effect=ValueError('同源检查失败')):
            with self.assertRaisesRegex(ValueError, '同源检查失败'):
                plot.path_proposal({'id': 'p'}, 'w90', 'bands')
        with patch.object(plot.ex, 'attempt_path', return_value={'shell': '/remote/attempt'}), \
             patch.object(plot.pyw90_remote, '_file_hashes', side_effect=ValueError('缺少 chk')):
            with self.assertRaisesRegex(ValueError, '缺少 chk'):
                plot._checked_source({'config': {'alias': 'jutai-1'}}, {'seed': 'wannier90'})

    def test_pbs_history_requires_exact_complete_and_exit_zero(self):
        from backend import execution
        command = execution.pbs_history_command('25119.node01')
        self.assertIn('tracejob -n 30 25119.node01', command)
        self.assertEqual(execution.pbs_history_evidence('FPILOT_PBS_TRACE_EXIT=0\nFPILOT_PBS_TRACE_COMPLETE=1\n'), (0, True))
        self.assertEqual(execution.pbs_history_evidence('FPILOT_PBS_TRACE_EXIT=1\nFPILOT_PBS_TRACE_COMPLETE=0\n'), (1, False))

    def test_comparison_rejects_same_labels_at_different_coordinates(self):
        from backend import wannier_compare
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'KPOINTS'
            path.write_text('Explicit path\n40\nLine-mode\nReciprocal\n0 0 0 ! GAMMA\n0 0.5 -0.5 ! X\n')
            proposal = {'path_lines': ['GAMMA 0 0 0 X 0 0.5 -0.5'], 'bands_num_points': 40}
            wannier_compare._same_path(path, proposal)
            with self.assertRaisesRegex(ValueError, '坐标不同'):
                wannier_compare._same_path(path, {**proposal, 'path_lines': ['GAMMA 0 0 0 X 0 0.4 -0.5']})


if __name__ == '__main__':
    unittest.main()
