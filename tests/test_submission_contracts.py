import copy
import hashlib
import json
import unittest
from unittest.mock import patch
import test_chain_runs as fixture_module
from backend import execution as ex, inputs, source_contracts as sources, branch_bindings as branches

class SubmissionContracts(unittest.TestCase):
    def test_case_execution_identity_is_frozen_and_cannot_enter_single_submission(self):
        self.set_source('c','upstream')
        frozen=inputs.freeze(self.project,'c','scan case',execution_scope='scan-scope')
        self.assertEqual(frozen['manifest']['execution_scope'],'scan-scope')
        row=next(r for r in inputs.history('p') if r['id']==frozen['id'])
        self.assertEqual(row['execution_scope'],'scan-scope')
        config={'alias':'one','vasp_executable':'/vasp_std'}
        with patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':{}}),patch.object(ex,'ssh') as remote:
            with self.assertRaisesRegex(ValueError,'扫描 case'):
                ex.prepare.__wrapped__(frozen['id'],config,'probe','node01',project_id='p')
            with self.assertRaisesRegex(ValueError,'扫描 case'):
                ex.prepare.__wrapped__(frozen['id'],config,'probe','node01',project_id='p',execution_scope='wrong')
            remote.assert_not_called()

    def test_default_changes_invalidate_prepared_dispatch_by_host_and_program(self):
        from backend import workbench_settings as settings, wannier_execution as w90, wannier_plot_restart as plot
        path=self.fixture.root/'workbench-settings.json'
        state={'version':1,'runtimes':{},'defaults':{},'history_limit':5}
        with patch.object(settings,'ROOT',self.fixture.root):
            frozen=settings.snapshot('one','vasp_std')
            state['defaults']['two:vasp_std']='other';state['defaults']['one:vasp_ncl']='soc'
            path.write_text(json.dumps(state));settings.assert_current(frozen)
            state['defaults']['one:vasp_std']='changed';path.write_text(json.dumps(state))
            prepared={'state':'PREPARED','kind':'vasp','settings_snapshot':frozen}
            with patch.object(ex,'get',return_value=prepared),patch.object(ex,'ssh') as remote:
                with self.assertRaisesRegex(ValueError,'设置已变化'):ex.submit('prepared')
                remote.assert_not_called()
            frozen=settings.snapshot('one','wannier90')
            state['runtimes']['one:wannier90']={'wannier_executable':'new-path'};path.write_text(json.dumps(state))
            for submit,kind in [(w90.submit,'wannier90'),(plot.submit,'wannier90-plot')]:
                with patch.object(ex,'get',return_value={'state':'PREPARED','kind':kind,'settings_snapshot':frozen}),patch.object(ex,'ssh') as remote:
                    with self.assertRaisesRegex(ValueError,'设置已变化'):submit('prepared')
                    remote.assert_not_called()
            settings.assert_current(None)

    def setUp(self):
        self.fixture=fixture_module.ChainRunTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.project=copy.deepcopy(self.fixture.project)
        self.project['nodes']=self.project['nodes'][:3]
        self.project['edges']=self.project['edges'][:2]
        self.project['nodes'][2]['data']['parameters']['values']['LCHARG']='true'
        self.source_bundle=inputs.freeze(self.project,'r','original')['id']
        self.record={'id':'old','project_id':'p','node_id':'r','bundle_id':self.source_bundle,'stage_slug':'custom-relax',
                     'job_id':'1.node01','config':{'alias':'one'},'state':'COMPLETED','created_at':'2026-10-01',
                     'path_schema':'fpilot.remote-attempt-path','remote_workspace_root':'$HOME/230269265lht',
                     'remote_relative_path':'material-projects/chain/flows/main/stages/02-relax/attempts/run',
                     'remote_path':'$HOME/230269265lht/material-projects/chain/flows/main/stages/02-relax/attempts/run',
                     'remote_path_absolute':'/home/user/230269265lht/material-projects/chain/flows/main/stages/02-relax/attempts/run',
                     'evidence':{'vasp_timing':True,'ediff_reached':True,'ionic_converged':True,'wrapper_exit':0}}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('old','COMPLETED',json.dumps(self.record)))
        self.snapshot=patch.object(sources.source_files,'snapshot',side_effect=lambda alias,path,names,ssh,mode='lightweight':{n:{'size_bytes':42,'mtime':'time','verification_mode':'metadata' if n in ('CHGCAR','WAVECAR') else 'content',**({'sha256':self.project['nodes'][0]['data']['structure']['source']['sha256']} if n=='CONTCAR' else {})} for n in names});self.snapshot.start();self.addCleanup(self.snapshot.stop)
        from backend import artifacts
        structure=self.project['nodes'][0]['data']['structure']
        self.artifact=patch.object(artifacts,'preview',return_value={'structure':structure,'partial':False});self.artifact.start();self.addCleanup(self.artifact.stop)

    def set_source(self,node_id,mode,purpose='charge',files='charge'):
        node=next(n for n in self.project['nodes'] if n['id']==node_id)
        node['data']['sourceDraft']={'mode':mode,'purpose':purpose,'files':files,'attempt_id':'old','acknowledge_differences':True}

    def test_charge_recalculation_retains_configured_poscar(self):
        self.set_source('r','self')
        frozen=inputs.freeze(self.project,'r','charge')
        contract=frozen['manifest']['source_contract']
        self.assertEqual(contract['structure'],'configured');self.assertEqual(set(contract['roles']),{'charge'})
        _,files=ex.bundle(frozen['id'],allow_dependencies=True)
        self.assertIn('POSCAR',files);self.assertIn(b'ICHARG = 1',files['INCAR'])
        plan=ex.source_file_plan(sources.materialize(contract,'one'),files['INCAR'])
        self.assertNotIn('CONTCAR',plan);self.assertEqual(plan['CHGCAR']['mode'],'reflink-or-copy')

    def test_continue_optimization_uses_contcar_and_explicit_wave_choice(self):
        self.set_source('r','self','continue','both')
        frozen=inputs.freeze(self.project,'r','continue')
        self.assertEqual(set(frozen['manifest']['source_contract']['roles']),{'structure','charge','wavefunction'})
        _,files=ex.bundle(frozen['id'],allow_dependencies=True)
        self.assertIn(b'ISTART = 1',files['INCAR'])
        self.assertEqual(ex.source_file_plan(sources.materialize(frozen['manifest']['source_contract'],'one'),files['INCAR'])['CONTCAR']['target'],'POSCAR')

    def test_self_source_cannot_use_other_node(self):
        self.set_source('c','self')
        with self.assertRaisesRegex(ValueError,'没有可用来源'):inputs.freeze(self.project,'c','invalid')

    def test_latest_eligible_is_local_only_and_freeze_never_reselects(self):
        self.set_source('c','upstream');node=self.project['nodes'][2];node['data']['sourceDraft'].pop('attempt_id')
        newer={**self.record,'id':'new','created_at':'2026-10-02'}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('new','COMPLETED',json.dumps(newer)))
        with patch.object(ex,'ssh',side_effect=AssertionError('candidate listing must not use SSH')):
            self.assertEqual(sources.resolve(self.project,'c')['selected_attempt_id'],'new')
        frozen=inputs.freeze(self.project,'c','upstream');contract=frozen['manifest']['source_contract']
        self.assertEqual(contract['roles']['charge']['attempt_id'],'new')
        newer['id']='later';newer['created_at']='2026-10-03'
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('later','COMPLETED',json.dumps(newer)))
        self.assertEqual(sources.materialize(contract,'one')['source_attempt_id'],'new')
        with self.assertRaisesRegex(ValueError,'服务器不同'):sources.materialize(contract,'two')

    def test_unconverged_or_unknown_is_not_eligible(self):
        rec={**self.record,'evidence':{**self.record['evidence'],'ionic_converged':False}}
        self.assertTrue(sources.eligible(rec,'upstream','charge'))
        self.assertTrue(sources.eligible({**rec,'state':'UNKNOWN'},'self','continue'))
        self.assertFalse(sources.eligible({**rec,'state':'FAILED'},'self','continue'))

    def test_missing_or_changed_file_does_not_fallback(self):
        self.set_source('r','self');contract=inputs.freeze(self.project,'r','freeze')['manifest']['source_contract']
        with patch.object(sources.source_files,'snapshot',side_effect=ValueError('缺少 CHGCAR')):
            with self.assertRaisesRegex(ValueError,'CHGCAR'):sources.materialize(contract,'one')
        with patch.object(sources.source_files,'snapshot',return_value={'CHGCAR':{'size_bytes':99}}):
            with self.assertRaisesRegex(ValueError,'已变化'):sources.materialize(contract,'one')

    def test_local_structure_branches_and_scans_are_independent(self):
        p=copy.deepcopy(self.project);p['nodes'].append({'id':'scan','data':{'kind':'scan'}});p['edges'].append({'source':'r','target':'scan'})
        self.assertNotIn('scan',branches.scopes(p,'r'));self.assertEqual(branches.scopes(p,'scan'),['scan'])
        # A scan of SCF actually reading relax output joins that remote branch.
        p['edges'][-1]['source']='c';self.assertIn('r',branches.scopes(p,'scan'))

    def test_reservation_and_permanent_binding_survive_failed_job_and_repeat(self):
        rec={**self.record,'id':'prepared','state':'PREPARED','job_id':None,'branch_scopes':['x'],'execution_scope':'x'}
        self.assertEqual(branches.inspect('p',['x'])['state'],'UNBOUND')
        with ex.db() as c:
            c.execute('BEGIN IMMEDIATE');branches.reserve(c,rec);c.commit()
        self.assertEqual(branches.inspect('p',['x'])['state'],'RESERVED')
        with ex.db() as c:
            with self.assertRaisesRegex(ValueError,'其他服务器'):branches.reserve(c,{**rec,'config':{'alias':'two'}})
        rec.update(state='FAILED',job_id='12.node01')
        with ex.db() as c:branches.commit(c,rec);c.commit()
        self.assertEqual(branches.inspect('p',['x'])['state'],'BOUND')

    def test_new_calculation_keeps_geometry_source_without_requiring_charge(self):
        self.set_source('c','new');self.project['nodes'][2]['data']['sourceDraft'].pop('attempt_id')
        cache=ex.ROOT/'artifact-cache';cache.mkdir()
        (cache/'old.json').write_text(json.dumps({'files':[{'path':'CONTCAR','size_bytes':42}]}))
        contract=inputs.freeze(self.project,'c','geometry only')['manifest']['source_contract']
        self.assertEqual(contract['mode'],'new');self.assertEqual(set(contract['roles']),{'structure'})

    def test_graph_edit_blocks_dispatch_but_does_not_break_existing_monitoring(self):
        a={**self.record,'id':'a','state':'SUBMITTED','node_id':'a','execution_scope':'a','branch_scopes':['a']}
        b={**self.record,'id':'b','node_id':'b','execution_scope':'b','branch_scopes':['b'],'config':{'alias':'two'}}
        with ex.db() as c:
            for rec in (a,b):c.execute('INSERT INTO attempts VALUES(?,?,?)',(rec['id'],rec['state'],json.dumps(rec)));branches.commit(c,rec)
            c.commit()
        changed={'id':'p','nodes':[{'id':'a','data':{'kind':'scf'}},{'id':'b','data':{'kind':'dos'}}],'edges':[{'source':'a','target':'b'}]}
        independent={**changed,'edges':[]}
        self.assertEqual(branches.check(independent,'a','one')['alias'],'one')
        self.assertEqual(branches.check(independent,'b','two')['alias'],'two')
        with self.assertRaisesRegex(ValueError,'冲突'):branches.check(changed,'b','two')
        a['state']='RUNNING';ex.save(a)
        self.assertEqual(ex.get('a')['state'],'RUNNING')
        self.assertEqual(branches.inspect('p',['a'])['alias'],'one')

    def test_legacy_conflict_assignment_preserves_original_records_and_rejects_unknown(self):
        other={**self.record,'id':'other','config':{'alias':'two'}}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('other','COMPLETED',json.dumps(other)))
        before=ex.get('other')
        self.assertEqual(branches.inspect('p',['r','c'])['state'],'CONFLICT')
        result=branches.resolve_legacy(self.project,'r','one')
        self.assertEqual(result['alias'],'one');self.assertEqual(ex.get('other'),before)
        other.update(id='unknown',state='UNKNOWN',job_id=None)
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('unknown','UNKNOWN',json.dumps(other)))
        with self.assertRaisesRegex(ValueError,'尚未结束'):branches.resolve_legacy(self.project,'r','one')

    def test_prepare_materializes_frozen_charge_contract_and_rejects_mutable_source(self):
        self.set_source('r','self');frozen=inputs.freeze(self.project,'r','restart')
        # Align the historical path identity with this test node.
        with ex.db() as c:c.execute('DELETE FROM attempts')
        self.record.update(project_slug='chain',stage_sequence=2,stage_slug='relax')
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('old','COMPLETED',json.dumps(self.record)))
        resource={'resource_ready':True,'script':'#!/bin/bash\ntrue\n','profile':{'node':'node01','ppn':4}}
        root={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        config={'alias':'one','scheduler':'pbs','vasp_executable':'/bin/vasp_std','remote_workspace_root':'$HOME/230269265lht'}
        with patch.object(ex.servers,'preview',return_value=resource),patch.object(ex,'ssh',return_value=root):
            prepared=ex.prepare.__wrapped__(frozen['id'],config,'probe','node01',project_id='p')
            self.assertEqual(prepared['role_source_snapshot']['source_attempt_id'],'old')
            self.assertEqual(prepared['source_file_plan']['CHGCAR']['mode'],'reflink-or-copy')
            with self.assertRaisesRegex(ValueError,'不能覆盖来源'):
                ex.prepare.__wrapped__(frozen['id'],config,'probe','node01',project_id='p',restart_attempt_id='old')
        self.assertEqual(branches.inspect('p',['r'])['state'],'BOUND') # historical receipt only
        with patch.object(ex.servers,'capacity_probe',return_value={'id':'probe'}),patch.object(ex.servers,'preview',return_value=resource),patch.object(ex,'refresh',side_effect=lambda key:ex.get(key)),patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_JOB_ID=77.node01\n','stderr':''}) as remote:
            submitted=ex.submit(prepared['id']);ex.submit(prepared['id'])
            self.assertEqual(submitted['job_id'],'77.node01');self.assertEqual(remote.call_count,1)
            self.assertIn('cp --reflink=auto',remote.call_args.args[1])
            self.assertIn('stat -c %s CHGCAR',remote.call_args.args[1])

    def test_unknown_holds_reservation_and_exact_job_reconciliation_binds(self):
        project=copy.deepcopy(self.project);project.update(id='isolated',remoteSlug='isolated')
        next(n for n in project['nodes'] if n['id']=='r')['data']['sourceDraft']={'mode':'new','purpose':'charge','files':'charge'}
        frozen=inputs.freeze(project,'r','unknown receipt')
        config={'alias':'one','scheduler':'pbs','vasp_executable':'/bin/vasp_std','remote_workspace_root':'$HOME/230269265lht'}
        resource={'resource_ready':True,'script':'#!/bin/bash\ntrue\n','profile':{'node':'node01','ppn':4}}
        root={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        with patch.object(ex.servers,'preview',return_value=resource),patch.object(ex,'ssh',return_value=root):
            prepared=ex.prepare.__wrapped__(frozen['id'],config,'probe','node01',project_id='isolated')
        self.assertEqual(branches.inspect('isolated',['r'])['state'],'UNBOUND')
        with patch.object(ex.servers,'capacity_probe',return_value={'id':'probe'}),patch.object(ex.servers,'preview',return_value=resource),patch.object(ex,'ssh',return_value={'exit_code':None,'stdout':'','stderr':'timeout'}) as remote:
            self.assertEqual(ex.submit(prepared['id'])['state'],'UNKNOWN')
            ex.submit(prepared['id']);self.assertEqual(remote.call_count,1)
        self.assertEqual(branches.inspect('isolated',['r'])['state'],'RESERVED')
        with self.assertRaisesRegex(ValueError,'冲突'):branches.check(project,'r','two')
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_JOB_ID=91.node01\njob_state = R\n','stderr':''}):
            self.assertEqual(ex.refresh(prepared['id'])['job_id'],'91.node01')
        self.assertEqual(branches.inspect('isolated',['r'])['state'],'BOUND')

    def test_chain_self_source_does_not_require_unselected_graph_parent(self):
        from backend import chain_runs
        self.set_source('c','new');self.project['nodes'][2]['data']['sourceDraft'].pop('attempt_id')
        bundle=inputs.freeze(self.project,'c','source SCF')['id']
        rec={**self.record,'id':'self-c','node_id':'c','bundle_id':bundle}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',(rec['id'],rec['state'],json.dumps(rec)))
        self.set_source('c','self');self.project['nodes'][2]['data']['sourceDraft']['attempt_id']='self-c'
        report=chain_runs.preview(self.project,['c'],'one')
        self.assertTrue(report['ready'],report);self.assertEqual(report['sources'],[])
        self.assertIsNone(report['steps'][0]['parent_id'])

    def test_wannier_configuration_freezes_before_resources_and_reuses_exact_input(self):
        from backend import wannier_execution as w90, interactive_plots
        import test_wannier_execution
        win=test_wannier_execution.fixture_win()
        project=copy.deepcopy(self.project)
        project['nodes'].append({'id':'w','data':{'kind':'wannier90','stageSlug':'wannier','stageSequence':4}})
        project['edges'].append({'source':'c','target':'w'})
        source={'win':win,'seed':'wannier90','spinors':'.true.','source_path':'/home/user/source','win_source_name':'wannier90.win',
                'files':{n:{'sha256':hashlib.sha256(win.encode()).hexdigest(),'size_bytes':len(win)} for n in w90.SOURCE_NAMES},'verification_mode':'lightweight'}
        reference={'outcar_sha256':'a'*64,'fermi_eV':6.0}
        root={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        config={'alias':'one','scheduler':'pbs','remote_workspace_root':'$HOME/230269265lht'}
        with patch.object(w90,'ROOT',self.fixture.root),patch.object(w90,'_parent',return_value=(self.record,project['nodes'][-1])),patch.object(w90,'inspect',return_value=source) as inspect,patch.object(interactive_plots,'energy_reference',return_value=reference),patch.object(w90.servers,'preview') as resource,patch.object(w90,'_render_script',return_value=('script',{})),patch.object(ex,'ssh',return_value=root):
            frozen=w90.freeze_configuration.__wrapped__(project,'w','old','one','input freeze',win,energy_reference=reference)
            resource.assert_not_called()
            self.assertEqual(w90.configurations('p','w')[0]['id'],frozen['id'])
            resource.return_value={'resource_ready':True,'profile':{'node':'node01','ppn':4}}
            with patch('backend.source_files.snapshot',return_value=source['files']):
                prepared=w90.prepare.__wrapped__(project,'w','wrong-source',config,'probe','node01',win='invalid',frozen_id=frozen['id'])
            self.assertEqual(prepared['parent_attempt_id'],'old');self.assertEqual(prepared['bundle_id'],frozen['id'])
            self.assertEqual(w90.configuration(prepared['bundle_id'])['win'],win)
            inspect.return_value={**source,'files':{**source['files'],'wannier90.eig':{'size_bytes':99}}}
            with patch('backend.source_files.snapshot',return_value=inspect.return_value['files']),self.assertRaisesRegex(ValueError,'来源发生变化'):
                w90.prepare.__wrapped__(project,'w','old',config,'probe','node01',frozen_id=frozen['id'])
