import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

from backend import wannier_scans as windows, wannier_execution as w90, scans, execution as ex
from backend import recycle, workbench_settings

KEYS=windows.helper.KEYS
WIN='''num_wann = 2
num_bands = 3
mp_grid = 2 1 1
num_iter = 30
conv_window = 3
conv_tol = 1e-6
dis_win_min = -3
dis_win_max = 3
dis_froz_min = -0.5
dis_froz_max = 0.5
spinors = .true.
begin unit_cell_cart
ang
3 0 0
0 3 0
0 0 3
end unit_cell_cart
begin atoms_frac
Fe 0 0 0
end atoms_frac
begin kpoints
0 0 0
0.5 0 0
end kpoints
begin projections
Fe:d
end projections
'''


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.stack=ExitStack()
        self.stack.enter_context(patch.object(w90,'prepare',w90.prepare.__wrapped__))
        for module in (windows,w90,ex,recycle,workbench_settings):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.project={'id':'project','name':'windows','remoteSlug':'windows','nodes':[
            {'id':'v','data':{'kind':'vasp-wannier','executionMode':'vasp','stageSequence':1,'stageSlug':'interface'}},
            {'id':'w','data':{'kind':'wannier90','executionMode':'wannier90','stageSequence':2,'stageSlug':'wannier'}},
            {'id':'scan','data':{'kind':'scan','executionMode':'scan','stageSequence':3,'stageSlug':'scan'}}],
            'edges':[{'source':'v','target':'w'},{'source':'w','target':'scan'}]}
        self.source={name:{'size_bytes':10,'mtime':'stamp','verification_mode':'metadata'} for name in w90.source_names('wannier90')}
        self.source['wannier90.win'].update(sha256='a'*64,verification_mode='content')
        self.reference={'fermi_eV':0.0,'outcar_sha256':'b'*64}
        self.bundle={'schema':'fpilot.wannier90-frozen-bundle.v1','project_id':'project','node_id':'w','project':self.project,
                     'alias':'jutai-1','parent_attempt_id':'c'*32,'seed':'wannier90','win':WIN,'win_sha256':w90._sha(WIN.encode()),
                     'source_path':str(self.root),'source_files':self.source,'source_win_name':'wannier90.win','verification_mode':'lightweight',
                     'created_at':ex.now(),'spinors':'.true.','energy_reference':self.reference,'band_path':None,'relative_windows':{},'input_reason':'baseline'}
        (self.root/'wannier-bundles').mkdir()
        raw=json.dumps(self.bundle,sort_keys=True,ensure_ascii=False).encode();self.frozen=w90._sha(raw)
        (self.root/'wannier-bundles'/(self.frozen+'.json')).write_bytes(raw)
        self._matrix([[-2,0,2],[-2,0,2]])
        self.stack.enter_context(patch.object(w90,'_parent',return_value=({'project_slug':'windows','job_id':'123'},self.project['nodes'][1])))
        self.stack.enter_context(patch('backend.source_files.snapshot',return_value=self.source))
        self.stack.enter_context(patch('backend.interactive_plots.energy_reference',return_value=self.reference))
        self.remote=self.stack.enter_context(patch.object(windows,'_remote',side_effect=lambda alias,action,req: windows.helper.check(req)))
        self.search={'phase':'outer','boundary':'dis_win_max','target':[-0.5,0.5],'lower':1,'upper':5,'step':1}

    def tearDown(self):self.stack.close();self.temp.cleanup()
    def _matrix(self,rows):
        (self.root/'wannier90.amn').write_text('header\n3 2 2\n')
        (self.root/'wannier90.mmn').write_text('header\n3 2 1\n')
        (self.root/'wannier90.eig').write_text(''.join(f'{b} {k} {e}\n' for k,row in enumerate(rows,1) for b,e in enumerate(row,1)))
    def preview(self,search=None):return scans.preview(self.project,'scan','w',mode=windows.MODE,frozen_id=self.frozen,window_search=search or self.search)
    def freeze(self,search=None):
        report=self.preview(search)
        return scans.freeze(self.project,'scan','w',[],'test',report['definition_hash'],windows.MODE,self.frozen,search or self.search)
    def complete(self,batch):
        for case in batch['cases']:
            attempt=hashlib.sha256(case['id'].encode()).hexdigest()[:32]
            rec={'id':attempt,'kind':'wannier90','state':'COMPLETED','project_id':'project','node_id':'w','execution_scope':'scan',
                 'bundle_id':case['bundle_id'],'config':{'alias':'jutai-1'},'evidence':{'wannier_all_done':True}}
            with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(attempt,'COMPLETED',json.dumps(rec)))
            case.update(attempt_id=attempt,state='COMPLETED')
        batch['state']='COMPLETE';return scans.save(batch)

    def test_single_edge_and_no_matrix_hash_cache(self):
        report=self.preview();self.assertEqual(report['case_count'],5)
        for c in report['cases']:
            self.assertEqual(c['relative_windows']['dis_win_min'],-3)
            self.assertEqual(c['relative_windows']['dis_froz_min'],-0.5)
            self.assertEqual(c['relative_windows']['dis_froz_max'],0.5)
            self.assertEqual(c['counts']['outer_margin'],0 if c['relative_windows']['dis_win_max']<2 else 1)
        self.preview();self.assertEqual(self.remote.call_count,1)
        self.assertNotIn('sha256',self.remote.call_args.args[2])

    def test_spread_bohr_is_converted_to_angstrom_squared(self):
        output=self.root/'wannier90.wout'
        output.write_text('Final State\nSum of centres and spreads (0,0,0) 10\nFinal Spread (bohr^2) Omega Total = 10\nAll done: wannier90 exiting\n')
        result=windows.helper.summary({'wout':str(output),'win':WIN+'length_unit = bohr\n'})
        self.assertAlmostEqual(result['spread_A2'],10*0.529177210903**2)
        self.assertEqual(result['spread_source_unit'],'bohr^2')
        self.assertEqual(result['localization'],'not_satisfied')

    def test_source_purge_removes_its_window_check_cache(self):
        self.preview()
        cache=list((self.root/'window-checks').glob('*.json'))
        self.assertEqual(len(cache),1)
        marker=recycle.marker('wannier-inputs',self.frozen)
        marker.parent.mkdir(parents=True);marker.write_text('{}')
        recycle.purge('wannier-inputs',self.frozen,True)
        self.assertFalse(cache[0].exists())

    def test_last_round_purge_cleans_campaign_comparison_contract(self):
        first=self.complete(self.freeze())
        second=self.freeze({**self.search,'previous_batch_id':first['id'],'selected_case_id':first['cases'][0]['id'],'refine':True})
        contract=self.root/'window-comparisons'/(first['campaign_id']+'.json')
        contract.parent.mkdir();contract.write_text('{}')
        for batch in (first,second):
            marker=recycle.marker('scans',batch['id']);marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('{}')
            recycle.purge('scans',batch['id'],True)
            self.assertEqual(contract.exists(),batch is first)

    def test_per_k_equality_allowed_and_violations_filtered(self):
        self._matrix([[-2,0,2],[0,1.8,2.5]])
        report=self.preview()
        self.assertTrue(any('态数不足' in x['reason'] for x in report['filtered']))
        equality=next(c for c in report['cases'] if c['relative_windows']['dis_win_max']==2)
        self.assertEqual(equality['counts']['outer_margin'],0)
        self.assertTrue(equality['warnings'])

    def test_eig_duplicate_missing_nonfinite_and_dimensions(self):
        request={'num_bands':3,'num_kpts':2,'num_wann':2,'candidates':[windows.helper.validate_base(WIN)['windows']],
                 **{ext:str(self.root/('wannier90.'+ext)) for ext in ('eig','amn','mmn')}}
        eig=self.root/'wannier90.eig';original=eig.read_text()
        for bad in (original+original.splitlines()[0]+'\n','\n'.join(original.splitlines()[:-1]),original.replace('1 1 -2','1 1 nan')):
            eig.write_text(bad)
            with self.assertRaises(ValueError):windows.helper.check(request)
        eig.write_text(original);(self.root/'wannier90.mmn').write_text('header\n4 2 1\n')
        with self.assertRaisesRegex(ValueError,'维度'):windows.helper.check(request)

    def test_freeze_identity_independent_checkpoint_scope(self):
        batch=self.freeze()
        self.assertEqual(batch['schema'],windows.SCHEMA)
        for c in batch['cases']:
            frozen=w90.configuration(c['bundle_id'])
            self.assertEqual(frozen['execution_scope'],'scan')
            self.assertEqual(frozen['parent_attempt_id'],'c'*32)
            self.assertIn('Fe:d',frozen['win'])
            self.assertNotIn('restart',frozen['win'])
        self.assertEqual(len(set(c['bundle_id'] for c in batch['cases'])),5)

    def test_stage_order_fixed_target_reuse_refine_and_adopt(self):
        first=self.complete(self.freeze())
        selected=next(c for c in first['cases'] if c['relative_windows']['dis_win_max']==3)
        search={**self.search,'previous_batch_id':first['id'],'selected_case_id':selected['id'],'refine':True}
        report=self.preview(search)
        self.assertEqual(report['search']['step'],0.5)
        self.assertTrue(any(c['reuse'] for c in report['cases']))
        self.assertEqual(report['round'],2)
        wrong={**search,'phase':'frozen','boundary':'dis_froz_max'}
        with self.assertRaisesRegex(ValueError,'顺序'):self.preview(wrong)
        with self.assertRaisesRegex(ValueError,'目标'):self.preview({**search,'target':[-1,1]})
        next_search={**search,'boundary':'dis_win_min','lower':-5,'upper':-1,'refine':False}
        second=self.freeze(next_search)
        self.assertEqual(second['campaign_id'],first['id'])
        self.assertTrue(all(c['relative_windows']['dis_win_max']==3 for c in second['cases']))
        adopted=windows.adopt(first,'project',selected['id'])
        self.assertEqual(adopted['node_id'],'w');self.assertIn('parameters',adopted)
        self.assertEqual(w90.configuration(self.frozen)['win'],WIN)

    def test_source_fermi_changed_recycled_and_no_new_candidates(self):
        with patch('backend.interactive_plots.energy_reference',return_value={**self.reference,'fermi_eV':1}):
            with self.assertRaisesRegex(ValueError,'费米'):self.preview()
        first=self.complete(self.freeze());c=first['cases'][0]
        search={**self.search,'previous_batch_id':first['id'],'selected_case_id':c['id']}
        report=self.preview(search);self.assertEqual(report['new_case_count'],0)
        self.assertTrue(report['message'])
        marker=recycle.marker('attempts',c['attempt_id']);marker.parent.mkdir(parents=True);marker.write_text('{}')
        with self.assertRaises(ValueError):self.preview(search)

    def test_special_modes_and_plot_restart_rejected(self):
        for tag in ('restart=plot','dis_froz_proj=true','dis_spheres_num=1','dis_proj_min=0.1'):
            with self.assertRaises(ValueError):windows.helper.validate_base(WIN+'\n'+tag)
        with self.assertRaisesRegex(ValueError,'网格'):
            windows.helper.validate_base(WIN.replace('0.5 0 0','0 0 0'))
        with self.assertRaisesRegex(ValueError,'均匀'):
            windows.helper.validate_base(WIN.replace('0.5 0 0','0.3 0 0'))

    def test_unordered_eigenvalues_and_wrong_run_input(self):
        self._matrix([[0,-2,2],[-2,0,2]])
        with self.assertRaisesRegex(ValueError,'有序'):
            self.preview()
        path=self.root/'result.wout';path.write_text('All done: wannier90 exiting')
        path.with_suffix('.win').write_text('wrong')
        with self.assertRaisesRegex(ValueError,'冻结输入'):
            windows.helper.summary({'wout':str(path),'win':WIN,'expected_win_sha256':w90._sha(WIN.encode())})

    def test_comparison_protocol_persists_one_reference_for_campaign(self):
        from backend import wannier_compare
        import csv
        batch=self.complete(self.freeze());ids=[c['id'] for c in batch['cases'][:2]]
        out=self.root/'comparison';out.mkdir()
        def comparison(*args,**kwargs):
            key=hashlib.sha256(args[1].encode()).hexdigest()[:32];folder=out/key;folder.mkdir(exist_ok=True)
            with (folder/'curves.csv').open('w') as f:
                writer=csv.writer(f);writer.writerow(['method','k_index','band','energy_relative_eV'])
                for k in (1,2):
                    for b,e in enumerate([-2,0,2],1):writer.writerow(['DFT',k,b,e])
                    for b,e in enumerate([-2,10],1):writer.writerow(['Wannier90',k,b,e])
            return {'id':key}
        with patch.object(wannier_compare,'ROOT_COMPARE',out),patch.object(wannier_compare,'run',side_effect=comparison):
            result=windows.compare(batch,'project',ids,'band-reference',0)
            self.assertEqual(result['rows'][0]['metrics']['rmse_meV'],10000)
            self.assertEqual(result['rows'][0]['metrics']['reference_values'],2)
            self.assertEqual(scans.get(batch['id'])['comparison']['band_attempt_id'],'band-reference')
            with self.assertRaisesRegex(ValueError,'绑定 DFT'):windows.compare(batch,'project',ids,'other-reference',0)

    def test_all_six_boundaries_and_final_local_recheck_preserve_source(self):
        previous=None
        for phase,boundary in windows.ORDER:
            lower,upper,step=(1,5,1) if boundary=='dis_win_max' else (-5,-1,1) if boundary=='dis_win_min' else (-0.25,1.25,0.25) if boundary=='dis_froz_max' else (-1.25,0.25,0.25)
            search={**self.search,'phase':phase,'boundary':boundary,'lower':lower,'upper':upper,'step':step}
            if previous:
                selected=previous['cases'][0]
                search.update(previous_batch_id=previous['id'],selected_case_id=selected['id'])
            batch=self.freeze(search)
            self.assertEqual(batch['source_frozen_id'],self.frozen)
            if batch['state']!='COMPLETE':batch=self.complete(batch)
            self.assertTrue(all(c['absolute_windows'][k]==c['relative_windows'][k]+self.reference['fermi_eV'] for c in batch['cases'] for k in KEYS))
            previous=batch
        self.assertEqual(previous['round'],6)
        self.assertEqual(previous['new_case_count'],0)

    def test_disabled_criteria_final_state_is_not_convergence(self):
        path=self.root/'case.wout';path.write_text('Final State\nSum of centres and spreads ( 0.0, 0.0, 0.0 ) 3.5\nOmega Total = 3.5\nTotal Execution Time 12.5\nAll done: wannier90 exiting\n')
        helper=windows.helper
        result=helper.summary({'wout':str(path),'win':WIN.replace('conv_window = 3','conv_window = -1')})
        self.assertEqual(result['localization'],'criteria_disabled');self.assertEqual(result['spread_A2'],3.5)
        self.assertEqual(result['elapsed_s'],12.5);self.assertTrue(result['program_done'])
        result=helper.summary({'wout':str(path),'win':WIN});self.assertEqual(result['localization'],'not_satisfied')
        path.write_text(path.read_text()+'\nWannierisation convergence criteria satisfied\nDisentanglement convergence criteria satisfied\n')
        result=helper.summary({'wout':str(path),'win':WIN});self.assertEqual(result['localization'],'confirmed')

    def test_wannier_prepare_deterministic_owner_no_single_bypass(self):
        batch=self.freeze();case=batch['cases'][0]
        config={'alias':'jutai-1','scheduler':'pbs','remote_workspace_root':'$HOME/230269265lht'}
        profile={'name':'w90','queue':'batch','node':'node01','ppn':2,'walltime':'01:00:00'}
        attempt='d'*32
        with patch.object(w90.servers,'preview',return_value={'resource_ready':True,'profile':profile}),patch.object(ex,'ssh',return_value={}),patch.object(w90.remote_paths,'parse_workspace_probe',return_value={'absolute_path':'/home/u/230269265lht'}):
            args=(self.project,'w','c'*32,config,'probe','node01',2)
            with self.assertRaisesRegex(ValueError,'批次'):w90.prepare(*args,frozen_id=case['bundle_id'])
            value=w90.prepare(*args,frozen_id=case['bundle_id'],attempt_id=attempt,execution_scope='scan',scan_batch_id=batch['id'],scan_case_id=case['id'],scan_case_table_hash=batch['case_table_hash'])
            again=w90.prepare(*args,frozen_id=case['bundle_id'],attempt_id=attempt,execution_scope='scan',scan_batch_id=batch['id'],scan_case_id=case['id'])
            self.assertEqual(value['id'],again['id']);self.assertEqual(value['stage_slug'],'scan')
            self.assertEqual(value['kind'],'wannier90');self.assertEqual(value['execution_scope'],'scan')
            self.assertNotIn('.chk',' '.join(value['source_file_plan']))
            self.assertNotIn('vasp_std',value['script'])
            self.assertEqual(w90.configurations('project','w')[0]['id'],self.frozen)


class WindowDispatchTests(WindowTests):
    def rendered(self,config,probe,node,cores=None,reason='',**kwargs):
        busy=node in getattr(self,'busy_nodes',set())
        return {'profile':{'name':'w90','queue':'batch','node':node,'ppn':cores,'walltime':'01:00:00'},'script':'ignored vasp',
                'resource_ready':not busy,'node_status':'BUSY' if busy else 'FULLY_IDLE','blockers':['selected node CPUS_FREE must be greater than zero'] if busy else []}
    def authorize(self):
        batch=self.freeze();config={'alias':'jutai-1','scheduler':'pbs','remote_workspace_root':'$HOME/230269265lht'}
        choices=[{'node':'node01','cores':2,'reason':''},{'node':'node02','cores':2,'reason':''}]
        with patch.object(windows.servers,'probe',return_value={'id':'probe'}),patch.object(windows.servers,'preview',side_effect=self.rendered):
            reviewed=scans.resource_preview(batch['id'],'project',config,choices)['previews']
            self.assertTrue(all('wannier90.x' in x['script'] for x in reviewed.values()))
            return scans.authorize(batch['id'],'project',batch['case_table_hash'],config,candidates=choices,approved_previews=reviewed,max_parallel=2)
    def tick(self,limit=4):
        def submit(key):
            r=ex.get(key);r.update(state=getattr(self,'state','QUEUED'),job_id=None if getattr(self,'state',None)=='UNKNOWN' else '123.mock');return ex.save(r)
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',side_effect=self.rendered),patch.object(w90.remote_paths,'parse_workspace_probe',return_value={'absolute_path':'/home/u/230269265lht'}),patch.object(ex,'ssh',return_value={}),patch.object(scans.control,'settings',return_value={'max_inflight_total':limit,'max_inflight_per_host':limit,'max_cores_per_host':None}),patch('backend.extension_runs.active_usage',return_value=[]),patch.object(ex,'submit',side_effect=submit) as submission:
            scans.tick();return submission.call_count
    def test_pool_fill_wait_unknown_and_resume(self):
        b=self.authorize();self.assertEqual(self.tick(),2);self.assertEqual(self.tick(),0)
        current=scans.get(b['id']);records=[ex.get(c['attempt_id']) for c in current['cases'] if c['attempt_id']]
        self.assertEqual({r['node'] for r in records},{'node01','node02'})
        r=records[0];r.update(state='COMPLETED',evidence={'wannier_all_done':True});ex.save(r)
        self.state='UNKNOWN';self.assertEqual(self.tick(),1);self.assertEqual(self.tick(),0)
        with self.assertRaisesRegex(ValueError,'UNKNOWN'):scans.resume(b['id'],'project')
        unknown=next(c for c in scans.get(b['id'])['cases'] if c['state']=='UNKNOWN')
        r=ex.get(unknown['attempt_id']);r.update(state='QUEUED',job_id='456.mock');ex.save(r)
        self.assertEqual(scans.resume(b['id'],'project')['state'],'ACTIVE')
        self.state='QUEUED';self.assertEqual(self.tick(),0)
    def test_orphan_prepare_recovery_and_recycle(self):
        batch=self.authorize();case=batch['cases'][0];auth=batch['authorization'];choice=auth['candidates'][1]
        deterministic=hashlib.sha256((case['id']+':0').encode()).hexdigest()[:32]
        with patch.object(scans.servers,'preview',side_effect=self.rendered),patch.object(w90.remote_paths,'parse_workspace_probe',return_value={'absolute_path':'/home/u/230269265lht'}),patch.object(ex,'ssh',return_value={}):
            windows.prepare_case(batch,case,auth,choice,{'id':'probe'},deterministic)
        self.busy_nodes={'node02'};self.assertEqual(self.tick(),0)
        self.busy_nodes=set();self.assertEqual(self.tick(),2)
        recovered=scans.get(batch['id']);self.assertEqual(recovered['cases'][0]['attempt_id'],deterministic)
        mark=recycle.marker('scans',batch['id']);mark.parent.mkdir(parents=True);mark.write_text('{}')
        self.assertEqual(self.tick(),0)
    def test_same_server_and_quota(self):
        batch=self.authorize()
        with self.assertRaisesRegex(ValueError,'来源服务器'):windows.resources(batch,{'alias':'jutai-5'},[])
        self.assertEqual(self.tick(limit=1),1);self.assertEqual(scans.get(batch['id'])['state'],'ACTIVE')

    def test_real_wannier_submit_entry_receipt_bound_once_and_no_checkpoint(self):
        from backend import branch_bindings
        batch=self.authorize();case=batch['cases'][0];auth=batch['authorization'];choice=auth['candidates'][0]
        key=hashlib.sha256((case['id']+':0').encode()).hexdigest()[:32]
        receipt={'exit_code':0,'stdout':'FPILOT_JOB_ID=678.mock\n','stderr':''}
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',side_effect=self.rendered),patch.object(w90.remote_paths,'parse_workspace_probe',return_value={'absolute_path':'/home/u/230269265lht'}),patch.object(ex,'ssh',return_value=receipt) as remote:
            windows.prepare_case(batch,case,auth,choice,{'id':'probe'},key)
            case.update(attempt_id=key,state='PREPARED');scans.save(batch)
            remote.reset_mock()
            batch['state']='PAUSED';scans.save(batch)
            with self.assertRaisesRegex(ValueError,'已暂停'):ex.submit(key)
            batch['state']='ACTIVE';scans.save(batch)
            r=ex.submit(key);again=ex.submit(key)
            self.assertEqual(r['state'],'SUBMITTED');self.assertEqual(again['job_id'],'678.mock')
            self.assertEqual(remote.call_count,1)
            self.assertIn('qsub -v W90_SEED=wannier90',remote.call_args.args[1])
            self.assertNotIn('wannier90.chk',remote.call_args.args[1])
            self.assertEqual(branch_bindings.inspect('project',['scan'])['state'],'BOUND')
