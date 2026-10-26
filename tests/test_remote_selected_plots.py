import json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from backend import compact_plots as compact,interactive_plots as plots,remote_cache,recycle,execution


class SelectedProjectionTests(unittest.TestCase):
    def test_remote_selected_projection_has_identical_all_sample_values_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);local=root/'local';remote=root/'remote';local.mkdir();remote.mkdir()
            for folder in (local,remote):
                np.save(folder/'dos_energy.npy',np.array([-2.,0.,2.]))
                np.save(folder/'dos_total.npy',np.array([[1.,2.,3.],[4.,5.,6.]]))
                np.save(folder/'dos_projected.npy',np.arange(2*1*3*3).reshape(2,1,3,3).astype(float))
            (local/'source').mkdir();(local/'source'/'POSCAR').write_text('fixture')
            meta={'id':'test','sources':[{'attempt_id':'source','size_bytes':100,'modified_at':'one'}],
                  'band':None,'dos':{'elements':['Fe'],'orbitals':['px','py','pz'],'spins':2,
                                   'energy_reference':{'fermi_eV':0}},'dos_attempt_id':'source',
                  'remote_parts':{'dos':{'attempt_id':'source','remote_directory':str(remote)}}}
            selected=[{'element':'Fe','orbital':'p'}]
            expected=plots._series_data(meta,local,selected,-4,4)
            def controlled_pull(alias,files,companion_command,**kwargs):
                result=subprocess.run(companion_command,shell=True,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertLess(Path(files[0]['remote_path']).stat().st_size,10485760)
                shutil.copyfile(files[0]['remote_path'],files[0]['destination'])
                return {}
            with patch.object(remote_cache,'ROOT',root),patch.object(recycle,'assert_available'),patch.object(execution,'get',return_value={'id':'source','config':{'alias':'test'}}),patch.object(compact.remote_transport,'_interpreter',return_value=sys.executable),patch.object(compact.safe_files,'pull_many',side_effect=controlled_pull) as pull:
                actual=compact.selected_series(meta,selected,-4,4,local)
                self.assertEqual(actual,expected);self.assertEqual(pull.call_count,1)
                self.assertEqual(compact.selected_series(meta,selected,-4,4,local),actual)
                self.assertEqual(pull.call_count,1)
                compact.selected_series(meta,selected,-1,1,local);self.assertEqual(pull.call_count,2)


class SupplementaryDiagnosticTests(unittest.TestCase):
    def test_native_fallback_consumes_cached_arrays_without_remote_input_scans(self):
        from backend import wannier_diagnostics as diagnostics,pyw90_remote,remote_transport,remote_health
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'score-input.json').write_text('{}')
            record={'diagnostics':{'metric_window_relative_eV':[-2,3],'revision':diagnostics.metrics.REVISION,
                                  'status':'MEASURED','dft_band_first':21,'matched_bands':54,'expected_bands':54},'fermi_eV':5}
            missing=SimpleNamespace(returncode=0,stdout='FPILOT_PYW90_SCORE={"status":"UNAVAILABLE"}',stderr='')
            receipt={'exit_code':0,'stdout':'FPILOT_PYW90_SCORE={"status":"COMPLETED","quality_value":2.0}','stderr':''}
            with patch.object(remote_cache,'ROOT',root),patch.object(remote_health,'ROOT',root),patch.object(remote_health,'status',return_value=[]),patch.object(pyw90_remote,'available',return_value=True),patch.object(pyw90_remote,'runtime',return_value={'python':'/configured/python','package':'/configured/pyw90'}),patch.object(diagnostics.subprocess,'run',return_value=missing),patch.object(remote_transport,'run',return_value=receipt) as wire:
                result=diagnostics.native(root,record,'fixture',False)
                self.assertEqual(result['quality_value'],2)
                self.assertEqual(diagnostics.native(root,record,'fixture',False)['quality_value'],2)
                self.assertEqual(wire.call_count,1)
                command=wire.call_args.args[1]
                for forbidden in ('sha256sum','mktemp','pre band','main_cli','qsub'):
                    self.assertNotIn(forbidden,command)
                self.assertEqual(wire.call_args.kwargs['lane'],'analysis')
                self.assertEqual(json.loads(wire.call_args.kwargs['data'])['fermi_eV'],5)
                self.assertEqual(json.loads(wire.call_args.kwargs['data'])['dft_band_first'],21)
