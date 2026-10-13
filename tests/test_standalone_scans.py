import copy
import json
import unittest
from unittest.mock import patch
from backend import scans, inputs, execution as ex, source_contracts as sources, artifacts, calculation_nodes as nodes, node_editability
import test_scans


class StandaloneScanTests(unittest.TestCase):
    setUp = test_scans.ScanTests.setUp
    tearDown = test_scans.ScanTests.tearDown

    def independent(self):
        project=copy.deepcopy(self.project)
        base=project['nodes'][1];scan=project['nodes'][2]
        scan['data'].update(parameters=copy.deepcopy(base['data']['parameters']),inputDraft=copy.deepcopy(base['data']['inputDraft']),scanSpec={'calculationKind':'scf'})
        project['edges']=[{'source':'s','target':'c'},{'source':'s','target':'scan'}]
        return project

    def frozen(self, project=None):
        project=project or self.independent()
        return scans.freeze(project,'scan','scan',[{'kind':'incar','tag':'ENCUT','start':'500','end':'550','step':'50'}],'case baseline')

    def complete(self, batch, index=0):
        case=batch['cases'][index]
        manifest,files=ex.bundle(case['bundle_id'],allow_dependencies=True)
        record={'id':str(index+1)*32,'project_id':'p','node_id':manifest['node_id'],'execution_scope':'scan',
                'state':'COMPLETED','job_id':str(100+index)+'.node01','config':{'alias':'jutai-1'},
                'bundle_id':case['bundle_id'],'scan_batch_id':batch['id'],'scan_case_id':case['id'],
                'path_schema':'fpilot.remote-attempt-path','created_at':ex.now(),
                'evidence':{'vasp_timing':True,'ediff_reached':True,'ionic_converged':True}}
        with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(record['id'],record['state'],json.dumps(record)))
        case.update(attempt_id=record['id'],state='COMPLETED');scans.save(batch)
        return record

    def child(self, project, record, overrides=None):
        project=copy.deepcopy(project)
        child={'id':'child','data':{'kind':'scf','label':'downstream','executionMode':'vasp','stageSequence':4,'stageSlug':'scf',
            'parameters':{'preset':'basic','values':overrides or {}},'inputDraft':{'grid':'4 4 4','center':'Gamma'},
            'sourceDraft':{'mode':'upstream','purpose':'charge','files':'charge','attempt_id':record['id'],
                           'scan_batch_id':record['scan_batch_id'],'scan_case_id':record['scan_case_id']}}}
        project['nodes'].append(child);project['edges'].append({'source':'scan','target':'child'})
        # This fixture's local POTCAR stays on the root/shared configuration.
        project['sharedPotcar']={'id':project['nodes'][1]['data']['inputDraft']['potcar_id']}
        return project

    def test_ordinary_generation_freeze_and_case_identity(self):
        project=self.independent();before=copy.deepcopy(project)
        report=scans.preview(project,'scan','scan',[{'kind':'incar','tag':'ENCUT','start':'500','end':'550','step':'50'}])
        self.assertTrue(report['ready']);self.assertEqual(report['stage'],'scf');self.assertEqual(project,before)
        batch=self.frozen(project);self.assertEqual(batch['schema'],'fpilot.vasp-scan.v2')
        for case in batch['cases']:
            manifest,files=ex.bundle(case['bundle_id'])
            self.assertEqual(manifest['node_id'],'scan');self.assertEqual(manifest['execution_scope'],'scan')
            self.assertEqual(manifest['stage'],'scf');self.assertEqual(ex.frozen_project(case['bundle_id'])['nodes'][2]['data']['kind'],'scan')
            self.assertIn(b'NSW = 0',files['INCAR'])

    def test_each_child_inherits_its_selected_case_and_overrides_remain(self):
        p=self.independent();batch=self.frozen(p);first=self.complete(batch);second=self.complete(scans.get(batch['id']),1)
        for record,encut in ((first,'500'),(second,'550')):
            child=self.child(p,record)
            report=inputs.generate(child,'child')
            self.assertEqual(report['parameters']['ENCUT']['value'],encut)
            self.assertIn('继承 case',report['parameters']['ENCUT']['source'])
            self.assertIn('NSW = 0',report['files']['INCAR']);self.assertEqual(report['parameter_source']['attempt_id'],record['id'])
            overridden=inputs.generate(self.child(p,record,{'ENCUT':'700'}),'child')
            self.assertEqual(overridden['parameters']['ENCUT']['value'],'700')
            self.assertEqual(overridden['parameters']['ENCUT']['source'],'节点覆盖')
        # The second case is available although the batch has no global selection.
        self.assertNotIn('promotion',scans.get(batch['id']))
        self.assertEqual(sources.eligible(first,'upstream','charge'),[])
        with self.assertRaisesRegex(ValueError,'不一致'):
            bad=self.child(p,first);bad['nodes'][-1]['data']['sourceDraft']['scan_case_id']=batch['cases'][1]['id'];inputs.generate(bad,'child')

    def test_hubbard_and_ordered_moments_from_actual_input(self):
        p=self.independent();p['parameters']['shared']['values'].update(ISPIN='2',LDAU='true',LDAUTYPE='2',LMAXMIX='2')
        p['parameters']['shared']['values'].update(PREC='Accurate',ALGO='Normal')
        p['parameters']['shared']['moments']={a['id']:'2.5' for a in p['nodes'][0]['data']['structure']['atoms']}
        p['parameters']['shared']['hubbard']={'Si':{'l':'1','u':'1.6','j':'0'}}
        batch=self.frozen(p);record=self.complete(batch)
        child=self.child(p,record);child['parameters']['shared']['hubbard']['Si']['u']='1.0'
        child['parameters']['shared']['moments']={k:'0.5' for k in child['parameters']['shared']['moments']}
        result=inputs.generate(child,'child');self.assertEqual(result['errors'],[])
        self.assertIn('LDAUU = 1.6',result['files']['INCAR']);self.assertIn('MAGMOM = 2.5 2.5',result['files']['INCAR'])

    def test_result_owner_and_incomplete_batch_source_no_auto_selection(self):
        p=self.independent();batch=self.frozen(p);record=self.complete(batch)
        self.assertEqual(len(artifacts.attempts('p','scan')),1);self.assertEqual(artifacts.attempts('p','c'),[])
        self.assertIn('ENCUT=500',artifacts.attempts('p','scan')[0]['parameter_summary'])
        child=self.child(p,record);child['nodes'][-1]['data']['sourceDraft'].pop('attempt_id')
        with patch.object(sources,'compatibility',return_value={'blockers':[],'differences':[],'warnings':[]}):
            resolved=sources.resolve(child,'child')
        self.assertIsNone(resolved['selected_attempt_id']);self.assertIn('请选择具体',resolved['blockers'][0])

    def test_type_guard_and_legacy_upgrade_without_history_rewrite(self):
        p=self.independent();batch=self.frozen(p)
        changed=copy.deepcopy(p);changed['nodes'][2]['data']['scanSpec']['calculationKind']='relax'
        with self.assertRaisesRegex(ValueError,'已有冻结或执行引用'):node_editability.assert_changes(self.root,{'projects':[p]},{'projects':[changed]})
        legacy=copy.deepcopy(self.project)
        # The old batch does not turn a matching legacy upgrade into a type switch.
        upgraded=copy.deepcopy(legacy);upgraded['nodes'][2]['data']['scanSpec']={'calculationKind':'scf'}
        node_editability.assert_changes(self.root,{'projects':[legacy]},{'projects':[upgraded]})
        self.assertEqual(scans.get(batch['id'])['cases'],batch['cases'])

    def test_all_vasp_types_generate_under_scan_identity(self):
        for kind in nodes.VASP:
            with self.subTest(kind=kind):
                p=self.independent();scan=p['nodes'][2]
                scan['data']['scanSpec']['calculationKind']=kind
                if kind=='relax':scan['data']['parameters']['values'].update(NSW='30',IBRION='2',ISIF='2',EDIFFG='-0.01')
                if kind in ('bands','dos','vasp-wannier'):
                    p['edges'][-1]['source']='c'
                    scan['data']['inputDraft'].update(path='0 0 0 G\n0.5 0 0 X',line_points='10',projections='Si:s',num_wann='1')
                report=inputs.generate(p,'scan')
                self.assertEqual(report['errors'],[])
                self.assertEqual(report['stage'],kind)
                if kind!='relax':self.assertIn('NSW = 0',report['files']['INCAR'])
                if kind=='vasp-wannier':self.assertIn('NCORE = 1',report['files']['INCAR'])

    def test_prepare_keeps_scan_stage_and_ordinary_audit(self):
        batch=self.frozen();case=batch['cases'][0]
        config={'alias':'jutai-1','vasp_executable':'/opt/vasp_std','remote_workspace_root':'$HOME/fpilot'}
        resource={'resource_ready':True,'profile':{'node':'node02','ppn':4},'script':'#!/bin/bash\ntrue\n'}
        root={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/fpilot\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        with patch.object(ex.servers,'preview',return_value=resource),patch.object(ex.servers.pbs,'audit_inputs',return_value={'status':'PASS'}),patch.object(ex,'ssh',return_value=root):
            rec=ex.prepare.__wrapped__(case['bundle_id'],config,'probe','node02',cores=4,reason='scan')
        self.assertEqual(rec['node_id'],'scan');self.assertEqual(rec['execution_scope'],'scan')
        self.assertIn('/stages/03-scan/attempts/',rec['remote_path'])
        self.assertEqual(ex.bundle(rec['bundle_id'])[0]['stage'],'scf')

    def test_legacy_case_owner_and_actual_inputs_survive_upgrade(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'500','end':'550','step':'50'}],'legacy');rec=self.complete(batch)
        self.assertEqual(rec['node_id'],'c')
        self.assertEqual(artifacts.attempts('p','c'),[])
        p=self.independent();report=inputs.generate(self.child(p,rec),'child')
        self.assertEqual(report['parameters']['ENCUT']['value'],'500')
        self.assertEqual(sources.eligible(rec,'upstream','charge'),[])

    def test_chain_children_reuse_different_cases_without_dispatching_batch(self):
        from backend import chain_runs as chains
        p=self.independent();batch=self.frozen(p);first=self.complete(batch);second=self.complete(scans.get(batch['id']),1)
        p=self.child(p,first);child=copy.deepcopy(self.child(self.independent(),second)['nodes'][-1]);child['id']='other'
        p['nodes'].append(child);p['edges'].append({'source':'scan','target':'other'})
        with patch.object(sources,'compatibility',return_value={'blockers':[],'differences':[],'warnings':[]}):
            report,effective,reports=chains._preview(p,['child','other'])
        self.assertTrue(report['ready'],report['steps'])
        self.assertEqual(report['reuse_bindings'],{})
        self.assertEqual(reports['child']['parameters']['ENCUT']['value'],'500')
        self.assertEqual(reports['other']['parameters']['ENCUT']['value'],'550')
        self.assertEqual([s['node_id'] for s in report['steps']],['child','other'])
        self.assertEqual(scans.get(batch['id'])['state'],'FROZEN')

    def test_chain_keeps_selected_case_band_path_identity(self):
        from backend import chain_runs as chains
        p=self.independent();batch=self.frozen(p);rec=self.complete(batch);p=self.child(p,rec)
        child=p['nodes'][-1];child['data']['kind']='bands'
        child['data']['inputDraft'].update(path='0 0 0 G\n0.5 0 0 X',line_points='10',path_source_attempt_id=rec['id'],path_structure_hash='a'*64)
        with patch.object(sources,'compatibility',return_value={'blockers':[],'differences':[],'warnings':[]}):
            report,effective,reports=chains._preview(p,['child'])
        self.assertTrue(report['ready'],report['steps'])
        self.assertEqual(reports['child']['band_path_source_attempt_id'],rec['id'])
        self.assertEqual(reports['child']['band_path_source_sha256'],'a'*64)


class IndependentWindowTests(unittest.TestCase):
    def test_draft_baseline_uses_scan_owner_without_separate_frozen_input(self):
        import test_wannier_windows as wfixture
        from backend import wannier_scans as windows, wannier_execution as w90
        fixture=wfixture.WindowTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        p=copy.deepcopy(fixture.project);p['nodes'][2]['data']['scanSpec']={'calculationKind':'wannier90'}
        p['edges'][-1]['source']='v'
        bundle={**fixture.bundle,'node_id':'scan','project':p}
        draft={'parent_id':bundle['parent_attempt_id'],'alias':'jutai-1','seed':'wannier90','win':wfixture.WIN,'energy_reference':fixture.reference}
        search={'boundary':'dis_froz_max','values':[0.5,1]}
        with patch.object(w90,'configuration_draft',return_value=bundle) as baseline:
            preview=scans.preview(p,'scan','scan',mode=windows.MODE,window_search=search,source_draft=draft)
            batch=scans.freeze(p,'scan','scan',[],'independent',preview['definition_hash'],windows.MODE,None,search,draft)
        self.assertEqual(baseline.call_args.args[1],'scan')
        self.assertEqual(batch['base_node_id'],'scan')
        for c in batch['cases']:
            config=w90.configuration(c['bundle_id'])
            self.assertEqual(config['node_id'],'scan');self.assertEqual(config['execution_scope'],'scan')
