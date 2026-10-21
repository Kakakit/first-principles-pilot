"""Exercise actual tool adapters with fake transport and local fixtures, never SSH."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import analysis_queue, compact_plots, execution, electronic_plots, pyw90_remote, remote_transport, wannier_kpath


KPOINTS = b'path\n40\nLine-mode\nReciprocal\n0 0 0 ! G\n.5 0 0 ! X\n\n.5 .5 0 ! M\n0 0 0 ! G\n'


class RemoteToolTests(unittest.TestCase):
    def test_analysis_lane_crosses_real_ssh_wrapper_for_pyw90(self):
        receipt=dict(exit_code=0, stdout='native result', stderr='')
        with patch.dict(os.environ, FPILOT_SSH_MODE='session'), \
             patch.object(pyw90_remote, 'runtime', return_value={'python':'/fixed/python','package':'/fixed/package'}), \
             patch.object(remote_transport, 'run', autospec=True, return_value=receipt) as wire:
            self.assertEqual(pyw90_remote._run('fixture', '/source', ['pre','dos'], ('vasprun.xml',))['stdout'], 'native result')
        self.assertEqual(wire.call_args.kwargs['lane'], 'analysis')
        self.assertIn('/fixed/python', wire.call_args.args[1])

    def test_legacy_ssh_forwards_analysis_lane(self):
        with patch.dict(os.environ, FPILOT_SSH_MODE='legacy'), \
             patch.object(execution,'ssh_multiplex_options',return_value=[]), \
             patch.object(remote_transport,'legacy',autospec=True,return_value={}) as wire:
            execution.ssh('fixture','native --task',lane='analysis')
        self.assertEqual(wire.call_args.kwargs['lane'], 'analysis')

    def test_plot_extraction_crosses_real_wrapper_without_using_control_python(self):
        out='FPILOT_META='+json.dumps({'kind':'doscar','sha256':'a'*64})+'\n1 2\nFPILOT_END\n'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, FPILOT_SSH_MODE='session'), \
             patch.object(analysis_queue, 'interpreter', return_value='/analysis/python'), \
             patch.object(electronic_plots.artifacts,'_root',return_value='/source'), \
             patch.object(remote_transport,'run',autospec=True,return_value={'exit_code':0,'stdout':out}) as wire:
            electronic_plots._remote_table({'config':{'alias':'fixture'}},
                {'name':'DOSCAR','path':'DOSCAR','size_bytes':20},Path(tmp),'doscar','all','d')
        self.assertEqual(wire.call_args.kwargs['lane'],'analysis')
        self.assertIn('/analysis/python',wire.call_args.args[1])

    def test_slot_deadline_releases_lock_and_does_not_dispatch(self):
        with patch.object(analysis_queue,'remote_busy',return_value=True), \
             patch.object(analysis_queue.remote_health,'status',return_value=[]):
            with self.assertRaisesRegex(analysis_queue.Busy,'超时.*未发送'):
                with analysis_queue.slot('fixture',wait_timeout=0):self.fail('not acquired')
        with analysis_queue.slot('fixture',wait=False):pass

    def test_busy_pyw90_request_returns_without_sending_or_waiting(self):
        with patch.object(pyw90_remote,'runtime',return_value={'python':'/fixed/python'}), \
             patch.object(analysis_queue,'remote_busy',return_value=True),patch.object(execution,'ssh') as ssh:
            with self.assertRaisesRegex(analysis_queue.Busy,'未发送'):
                pyw90_remote._run('fixture','/source',['pre','dos'],())
        ssh.assert_not_called()

    def test_import_failure_names_the_actual_runtime(self):
        with patch.object(pyw90_remote,'runtime',return_value={'python':'/dedicated/python','package':'/dedicated/pyw90'}), \
             patch.object(execution,'ssh',return_value={'exit_code':1,'stdout':'','stderr':"ModuleNotFoundError: No module named 'pyw90'"}):
            with self.assertRaisesRegex(ValueError,'/dedicated/python.*/dedicated/pyw90.*ModuleNotFoundError'):
                pyw90_remote._run('fixture','/source',['pre','dos'],())

    def test_builtin_window_check_does_not_require_pyw90_runtime_or_adapter(self):
        with patch.object(pyw90_remote,'runtime',side_effect=AssertionError('no PyW90 environment lookup')), \
             patch.object(analysis_queue,'interpreter',return_value='/analysis/python'), \
             patch.object(execution,'ssh',return_value={'exit_code':0,'stdout':'checked','stderr':''}) as ssh:
            pyw90_remote._run('fixture','/source',['window-check','{}'],('wannier90.eig','wannier90.win'))
        command=ssh.call_args.args[1]
        self.assertIn('/analysis/python',command)
        self.assertNotIn('from pyw90',command)
        self.assertNotIn('import pandas',command)

    def test_analysis_python_uses_existing_analysis_setting(self):
        with patch('backend.extension_remote.runtimes',return_value={'fixture':{'python':'/analysis/bin/python'}}), \
             patch.object(remote_transport,'_interpreter',side_effect=AssertionError('no control Python lookup')):
            self.assertEqual(analysis_queue.interpreter('fixture'),'/analysis/bin/python')

    def test_soc_scoring_does_not_import_pyw90_or_trigger_remote_fallback(self):
        from backend import pyw90_score
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.startswith('pyw90'):raise AssertionError('no PyW90 import for SOC')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=guarded):
            self.assertEqual(pyw90_score.score({'spinors':True})['status'],'NOT_APPLICABLE')

    def test_tool_paths_can_be_saved_without_any_submission_script_or_remote_test(self):
        from backend import site_registration, servers
        with tempfile.TemporaryDirectory() as tmp,patch.object(site_registration,'ROOT',Path(tmp)), \
             patch.object(servers,'load_registry',return_value={'hosts':{'fixture':{'scheduler':'pbs'}}}), \
             patch.object(site_registration,'status',return_value={}),patch.object(execution,'ssh') as ssh:
            site_registration.update_paths('fixture',{'pyw90_python':'/analysis/python','pyw90_package':'/analysis/pyw90'})
            saved=json.loads((Path(tmp)/'site-registry.json').read_text())['hosts']['fixture']
            self.assertEqual(saved['pyw90']['python'],'/analysis/python')
            with self.assertRaises(ValueError):site_registration.update_paths('fixture',{'pyw90_python':'relative'})
            ssh.assert_not_called()

    def test_builtin_kpath_preserves_segments_and_points(self):
        from backend.wannier_bands import validate_path_lines
        result=wannier_kpath.read(KPOINTS)
        self.assertEqual(result['path_lines'],['G 0 0 0 X 0.5 0 0','M 0.5 0.5 0 G 0 0 0'])
        self.assertEqual(result['bands_num_points'],40)
        self.assertEqual(result['warnings'],[])
        for raw in (KPOINTS.replace(b'Reciprocal',b'Cartesian'), b'grid\n0\nGamma\n4 4 4\n'):
            with self.assertRaises(ValueError):wannier_kpath.read(raw)
        for lines in (['G 0 0'], ['G nan 0 0 X 0.5 0 0']):
            with self.assertRaises(ValueError):validate_path_lines(lines)



if __name__ == '__main__':unittest.main()
