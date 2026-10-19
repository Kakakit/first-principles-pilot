"""Standalone contract/scientific indexing tests with a clearly synthetic TB2J CLI."""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
FIXTURES=BASE/'tests/fixtures/v2'
FAKE=BASE/'tests/fixtures/fake_tb2j'


def options():
    return {'mode':'auto','energy_source':'frozen','magnetic_elements':['Fe'],'magnetic_atoms':[],'kmesh':[5,5,5],'emin_ev':-14,'emax_ev':0,'nz':100,'nproc':1,'orb_decomposition':True,'groupby':'spin','basis_confirmed':True,'orbital_mapping':[],'description':'synthetic protocol acceptance'}


def request(root,mode='spinor',combination='hr'):
    sources=[];inputs={};folders=['spinor'] if mode=='spinor' else ['up','down']
    for i,folder in enumerate(folders):
        source_id=str(i+1)*32;seed={'spinor':'wannier90','up':'wannier90.1','down':'wannier90.2'}[folder]
        energy={'fermi_eV':2.345678901234,'attempt_id':'parent','host':'simulation','outcar_sha256':'a'*64}
        sources.append({'id':source_id,'attempt_id':source_id,'kind':'attempt','stage':'wannier90','state':'COMPLETED','alias':'simulation','evidence':{'wannier_all_done':True},'input_summary':{'seed':seed,'parent_attempt_id':'parent','spinors':mode=='spinor','num_wann':8 if mode=='spinor' else 4,'energy_reference':energy}})
        d=root/'inputs'/folder;shutil.copytree(FIXTURES/folder,d)
        roles={'wannier-input':seed+'.win','wannier-output':seed+'.wout','hamiltonian':seed+('_hr.dat' if combination=='hr' else '_tb.dat')}
        if combination=='hr':roles['centres']=seed+'_centres.xyz'
        for role,name in roles.items():inputs.setdefault(role,[]).append({'path':str(d/name),'name':name,'source_id':source_id,'size_bytes':(d/name).stat().st_size})
    return {'schema':'fpilot.extension-request.v2','analysis_id':'a'*32,'inputs':inputs,'sources':sources,'options':options(),'output_dir':str(root/'outputs'),'result_file':str(root/'outputs/result.json')}


class TB2JV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
    def cli(self,action,req=None,env=None):
        args=[sys.executable,str(BASE/'scripts/cli.py'),action]
        if req is not None:
            path=self.root/'request.json';path.write_text(json.dumps(req));args+=['--request',str(path)]
        result=subprocess.run(args,capture_output=True,text=True,timeout=35,env={**os.environ,'PYTHONPATH':str(FAKE),'PYTHONDONTWRITEBYTECODE':'1',**(env or {})})
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)
    def check_reason(self,req):
        result=self.cli('check',req);self.assertEqual(result['status'],'NOT_APPLICABLE',result);return result['reason']
    def test_doctor_imports_selected_module_and_reports_cli_capabilities(self):
        result=self.cli('doctor');self.assertTrue(result['ready'],result);self.assertEqual(result['python'],sys.executable);self.assertEqual(result['version'],'0.0.0-simulation')
        broken=self.root/'broken/TB2J';broken.mkdir(parents=True);(broken/'__init__.py').write_text('raise ImportError("broken actual import")')
        missing=self.cli('doctor',env={'PYTHONPATH':str(broken.parent)});self.assertFalse(missing['ready']);self.assertIn('TB2J',missing['missing'])
    def test_independent_presentation_keeps_precision_all_rows_and_originals(self):
        for mode in ('spinor','collinear'):
            root=self.root/mode;req=request(root,mode);self.cli('run',req)
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'outputs').rglob('*') if p.is_file()}
            full=json.loads((root/'outputs/exchange.json').read_text())
            payload={'schema':'fpilot.extension-presentation-request.v1','source_dir':str(root/'outputs'),'output_dir':str(root/'view')}
            path=root/'view-request.json';path.write_text(json.dumps(payload))
            result=subprocess.run([sys.executable,str(BASE/'scripts/presentation.py'),'render','--request',str(path)],capture_output=True,text=True,timeout=30,env={**os.environ,'PYTHONPATH':''})
            self.assertEqual(result.returncode,0,result.stderr);manifest=json.loads(result.stdout);self.assertEqual(manifest['schema'],'fpilot.extension-presentation.v1')
            index=json.loads((root/'view/index.json').read_text());self.assertEqual(index['exchange_count'],len(full['exchange']));self.assertEqual(index['orbital_count'],len(full['orbital']))
            pairs=sum([json.loads((root/'view'/part['path']).read_text()) for part in index['exchange_parts']],[])
            self.assertEqual(pairs[0][7],full['exchange'][0]['J_meV']);self.assertEqual(pairs[0][1:3],[1,3]);self.assertIn('–'.join(sorted((full['exchange'][0]['symbol_i'],full['exchange'][0]['symbol_j']))),index['element_pairs'])
            self.assertTrue(all((root/'view'/a['path']).stat().st_size<10*1024**2 for a in manifest['assets']))
            self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'outputs').rglob('*') if p.is_file()})
    def test_collinear_and_soc_hr_and_tb_preserve_originals_precision_units_and_indices(self):
        for mode in ('spinor','collinear'):
            for combination in ('hr','tb'):
                with self.subTest(mode=mode,combination=combination):
                    root=self.root/(mode+combination);req=request(root,mode,combination)
                    if mode=='collinear' and combination=='tb':req['options'].update(magnetic_elements=[],magnetic_atoms=[1,3])
                    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'inputs').rglob('*') if p.is_file()}
                    self.assertEqual(self.cli('check',req)['status'],'APPLICABLE')
                    self.cli('run',req);full=json.loads((root/'outputs/exchange.json').read_text());result=json.loads((root/'outputs/result.json').read_text());row=full['exchange'][0]
                    self.assertEqual((row['atom_i'],row['atom_j'],row['R']),(1,3,[1,0,0]));self.assertAlmostEqual(row['J_meV'],1.2345678901234567,places=14);self.assertEqual(row['distance_A'],8)
                    self.assertEqual(full['orbital'][0]['orbital_i'],1);self.assertEqual(result['scientific_validation'],'NOT_PERFORMED');self.assertEqual(result['validation']['numerical_convergence'],'NOT_EVALUATED')
                    if mode=='spinor':self.assertEqual(row['DMI_meV'],[0.1,0.2,0.3]);self.assertEqual(full['orbital'][0]['DMI_meV'],[0.01]*3)
                    argv=json.loads((root/'outputs/command.json').read_text())['argv'];self.assertEqual(argv[:3],[sys.executable,'-m','TB2J.scripts.wann2J']);self.assertIn('--np',argv);self.assertNotIn('qsub',' '.join(argv));self.assertNotIn('FPILOT_ALLOCATED',' '.join(argv))
                    if mode=='collinear' and combination=='tb':self.assertEqual(argv[argv.index('--index_magnetic_atoms')+1:argv.index('--prefix_up')],['1','3'])
                    self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'inputs').rglob('*') if p.is_file()})
    def test_cutoff_is_frozen_in_official_argv_and_outside_native_results_are_rejected(self):
        req=request(self.root/'inside','collinear');req['options']['rcut_angstrom']=9;self.cli('run',req)
        command=json.loads((Path(req['output_dir'])/'command.json').read_text());self.assertEqual(command['options']['rcut_angstrom'],9);self.assertEqual(command['argv'][command['argv'].index('--rcut')+1],'9')
        outside=request(self.root/'outside','collinear');outside['options']['rcut_angstrom']=7;path=self.root/'outside-request.json';path.write_text(json.dumps(outside))
        result=subprocess.run([sys.executable,str(BASE/'scripts/cli.py'),'run','--request',str(path)],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(FAKE)},timeout=35)
        self.assertEqual(result.returncode,2);self.assertIn('超出请求最大距离',result.stderr);self.assertTrue((Path(outside['output_dir'])/'TB2J_results/TB2J.pickle').is_file())

    def test_optional_inputs_match_source_id_when_first_source_omitted(self):
        req=request(self.root,'collinear','tb');seed='wannier90.2';path=self.root/'inputs/down'/(seed+'_centres.xyz');req['inputs']['centres']=[{'path':str(path),'name':path.name,'source_id':req['sources'][1]['id'],'size_bytes':path.stat().st_size}]
        self.assertEqual(self.cli('check',req)['status'],'APPLICABLE')
    def test_wrong_channels_parent_server_spin_mode_and_prefix_fail(self):
        mutations=[lambda r:r['sources'][1]['input_summary'].update(seed='wannier90.1'),lambda r:r['sources'][1]['input_summary'].update(parent_attempt_id='other'),lambda r:r['sources'][1].update(alias='other'),lambda r:r['sources'][0]['input_summary'].update(spinors=True),lambda r:r['inputs']['hamiltonian'][0].update(name='wannier90_hr.dat')]
        for i,mutation in enumerate(mutations):
            req=request(self.root/str(i),'collinear');mutation(req);self.check_reason(req)
    def test_frozen_energy_missing_wrong_parent_or_mismatched_channel_references(self):
        for i,mutation in enumerate([lambda r:r['sources'][0]['input_summary'].update(energy_reference={}),lambda r:r['sources'][0]['input_summary']['energy_reference'].update(attempt_id='other'),lambda r:r['sources'][1]['input_summary']['energy_reference'].update(fermi_eV=3)]):
            req=request(self.root/str(i),'collinear');mutation(req);self.assertIn('费米能',self.check_reason(req))
    def test_manual_energy_requires_note_and_finite_number(self):
        req=request(self.root);req['options'].update(energy_source='manual',efermi_ev=1.25,energy_note='')
        self.assertIn('来源说明',self.check_reason(req));req['options']['energy_note']='parent OUTCAR precise Fermi energy';self.assertEqual(self.cli('check',req)['status'],'APPLICABLE');req['options']['efermi_ev']=float('nan');self.assertIn('有限',self.check_reason(req))
    def test_soc_basis_explicit_and_even_num_wann(self):
        req=request(self.root);req['options']['basis_confirmed']=False;self.assertIn('基底',self.check_reason(req));req['options']['basis_confirmed']=True;req['options'].pop('groupby');self.assertIn('groupby',self.check_reason(req))
    def test_missing_centres_hr_is_actionable_but_tb_works(self):
        req=request(self.root);req['inputs'].pop('centres');self.assertIn('write_xyz',self.check_reason(req))
        ham=req['inputs']['hamiltonian'][0];ham.update(path=ham['path'].replace('_hr','_tb'),name=ham['name'].replace('_hr','_tb'));ham['size_bytes']=Path(ham['path']).stat().st_size;self.assertEqual(self.cli('check',req)['status'],'APPLICABLE')
    def test_mismatched_cell_atom_order_num_wann_and_completion_fail(self):
        for i,(role,old,new) in enumerate([('wannier-input','6 0 0','7 0 0'),('wannier-input','Fe 0 0 0','O 0 0 0'),('wannier-input','num_wann = 4','num_wann = 5'),('wannier-output','All done','unfinished')]):
            req=request(self.root/str(i),'collinear','tb');item=req['inputs'][role][1];p=Path(item['path']);p.write_text(p.read_text().replace(old,new));item['size_bytes']=p.stat().st_size;self.check_reason(req)
    def test_mapping_group_sum_residual_and_raw_unmapped_ordinals(self):
        req=request(self.root);req['options']['orbital_mapping']=[{'atom_index':1,'orbital_index':1,'label':'confirmed dxy','group':'user t2g'},{'atom_index':3,'orbital_index':1,'label':'confirmed dz2','group':'user eg'}];self.cli('run',req)
        data=json.loads((self.root/'outputs/exchange.json').read_text());group=data['groups'][0];self.assertAlmostEqual(group['mapped_group_sum_meV'],0.5);self.assertAlmostEqual(group['mapped_minus_J_meV'],0.5-1.2345678901234567);self.assertEqual(data['orbital'][1]['label_j'],'orb_2');self.assertEqual(data['orbital_mapping'],req['options']['orbital_mapping'])
    def test_duplicate_mapping_and_zero_atom_index_fail_before_run(self):
        req=request(self.root);row={'atom_index':0,'orbital_index':1,'label':'test'};req['options']['orbital_mapping']=[row];self.assertIn('映射',self.check_reason(req));row['atom_index']=1;req['options']['orbital_mapping'].append(copy.deepcopy(row));self.assertIn('映射',self.check_reason(req))
    def test_out_of_actual_matrix_mapping_keeps_raw_products(self):
        req=request(self.root);req['options']['orbital_mapping']=[{'atom_index':1,'orbital_index':3,'label':'too large'}];path=self.root/'request.json';path.write_text(json.dumps(req));r=subprocess.run([sys.executable,str(BASE/'scripts/cli.py'),'run','--request',str(path)],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(FAKE)},timeout=35);self.assertEqual(r.returncode,2);self.assertIn('实际',r.stderr);self.assertTrue((self.root/'outputs/TB2J_results/TB2J.pickle').is_file())
    def test_unknown_format_fails_and_preserves_native_outputs(self):
        req=request(self.root);path=self.root/'request.json';path.write_text(json.dumps(req));r=subprocess.run([sys.executable,str(BASE/'scripts/cli.py'),'run','--request',str(path)],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(FAKE),'TB2J_TEST_FORMAT':'unknown'},timeout=35);self.assertEqual(r.returncode,2);self.assertIn('未知',r.stderr);self.assertTrue((self.root/'outputs/TB2J_results/exchange.out').exists());self.assertFalse((self.root/'outputs/result.json').exists())
    def test_missing_soc_tensor_fails_and_preserves_native_outputs(self):
        req=request(self.root);path=self.root/'request.json';path.write_text(json.dumps(req));r=subprocess.run([sys.executable,str(BASE/'scripts/cli.py'),'run','--request',str(path)],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(FAKE),'TB2J_TEST_FORMAT':'missing-soc'},timeout=35);self.assertEqual(r.returncode,2);self.assertIn('dmi_ddict',r.stderr);self.assertTrue((self.root/'outputs/TB2J_results/TB2J.pickle').exists());self.assertFalse((self.root/'outputs/result.json').exists())
    def test_outputs_cannot_be_inside_inputs(self):
        req=request(self.root);req.update(output_dir=str(self.root/'inputs/spinor/output'),result_file=str(self.root/'inputs/spinor/output/result.json'));self.assertIn('原输入',self.check_reason(req))

if __name__=='__main__':unittest.main()
