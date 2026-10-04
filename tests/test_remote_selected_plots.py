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
    def test_hash_guards_and_diagnostic_use_one_request_with_compact_source_names(self):
        from backend import wannier_compare as compare
        band={'id':'band','config':{'alias':'jutai-1'}};model={'id':'model','config':{'alias':'jutai-1'}}
        sources=[{'attempt_id':record,'name':name,'sha256':'a'*64} for record,names in [('band',['EIGENVAL','KPOINTS','POSCAR']),('model',['wannier90.1_band.dat','wannier90.1_band.labelinfo.dat'])] for name in names]
        receipt={'exit_code':0,'stdout':'FPILOT_PRE_BAND_BYTES=100\nFinal Quality 2.0 meV','stderr':''}
        with patch.object(execution,'attempt_path',return_value={'shell':'/home/u/230269265lht/test'}),patch.object(compare.remote_transport,'run',return_value=receipt) as request,patch.object(compare.pyw90_remote,'_file_hashes') as old_hash:
            result=compare._pyw90_diagnostic(band,model,model,'wannier90.1',0,sources,None)
            self.assertEqual(result['status'],'COMPLETED');self.assertEqual(request.call_count,1);old_hash.assert_not_called()
            self.assertEqual(request.call_args.args[1].count('sha256sum --'),5)
            self.assertEqual(request.call_args.kwargs['lane'],'analysis')
