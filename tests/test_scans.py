import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, inputs, scans, scan_results


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.ex_patch=patch.object(ex,'ROOT',self.root);self.ex_patch.start()
        self.input_patch=patch.object(inputs,'ROOT',self.root);self.input_patch.start()
        structure=json.loads((Path(__file__).parent/'fixtures/Si.structure.json').read_text())
        potcar=b'TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\nENMAX = 245.0;\nEnd of Dataset\n'
        key=inputs.upload_potcar(base64.b64encode(potcar).decode())['id']
        self.project={'id':'p','name':'scan','remoteSlug':'scan','nodes':[
            {'id':'s','data':{'kind':'structure','structure':structure,'executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}},
            {'id':'c','data':{'kind':'scf','parameters':{'preset':'basic','values':{}},'inputDraft':{'grid':'4 4 4','center':'Gamma','potcar_id':key},'executionMode':'vasp','stageSequence':2,'stageSlug':'scf'}},
            {'id':'scan','data':{'kind':'scan','executionMode':'scan','stageSequence':3,'stageSlug':'scan'}}],
            'edges':[{'source':'s','target':'c'},{'source':'c','target':'scan'}],
            'parameters':{'shared':{'values':{'XC_FUNCTIONAL':'PBE','ENCUT':'400','EDIFF':'1e-6','ISPIN':'1','ISMEAR':'0','SIGMA':'0.05','LDAU':'false'}}}}

    def tearDown(self):
        self.input_patch.stop();self.ex_patch.stop();self.temp.cleanup()

    def test_decimal_cases_and_frozen_inputs(self):
        dims=[{'kind':'incar','tag':'ENCUT','start':'400','end':'500','step':'50'},
              {'kind':'kmesh','meshes':[[4,4,4],[6,6,6]]}]
        report=scans.preview(self.project,'scan','c',dims)
        self.assertEqual(report['case_count'],6)
        self.assertTrue(report['ready'])
        self.assertIn('INCAR', report['cases'][2]['input_diff'])
        self.assertIn('KPOINTS', report['cases'][1]['input_diff'])
        batch=scans.freeze(self.project,'scan','c',dims,'收敛比较')
        self.assertEqual(len(batch['cases']),6)
        for case in batch['cases']:
            manifest,files=ex.bundle(case['bundle_id'])
            encut=next(p['value'] for p in case['parameters'] if p['key']=='ENCUT')
            mesh=next(p['value'] for p in case['parameters'] if p['key']=='grid')
            self.assertIn(('ENCUT = '+encut).encode(),files['INCAR'])
            self.assertIn(mesh.encode(),files['KPOINTS'])
            self.assertEqual(manifest['node_id'],'c')
        self.assertEqual(len({c['id'] for c in batch['cases']}),6)

    def test_scan_overwrites_raw_tag_and_uses_actual_hubbard_baseline(self):
        from backend.incar_text import parse
        layer=self.project['nodes'][1]['data']['parameters']
        layer['manualIncarText']='ENCUT=410\nLDAU=true\nLDAUTYPE=2\nLMAXMIX=2\nLDAUL=1\nLDAUU=3\nLDAUJ=0.5\n'
        layer['hubbard']={'Si':{'l':'-1','u':'0','j':'0'}}
        dimensions=[{'kind':'incar','tag':'ENCUT'},{'kind':'u','element':'Si'}]
        original=json.dumps(self.project,sort_keys=True)
        cloned=scans.case_project(self.project,'c',dimensions,['500','4'])
        report=inputs.generate(cloned,'c')
        self.assertEqual(report['errors'],[])
        tags=parse(report['files']['INCAR'])[0]
        self.assertEqual(tags['ENCUT'],'500')
        self.assertEqual(tags['LDAUU'],'4')
        self.assertEqual(tags['LDAUL'],'1')
        self.assertEqual(tags['LDAUJ'],'0.5')
        self.assertEqual(json.dumps(self.project,sort_keys=True),original)

    def test_reject_duplicate_limit_and_invalid_grid(self):
        base={'kind':'incar','tag':'ENCUT','start':'400','end':'407','step':'1'}
        with self.assertRaisesRegex(ValueError,'重复'):scans.preview(self.project,'scan','c',[base,base])
        with self.assertRaisesRegex(ValueError,'64'):scans.preview(self.project,'scan','c',[base,{'kind':'kmesh','meshes':[[i,i,i] for i in range(1,10)]}])
        with self.assertRaisesRegex(ValueError,'网格'):scans.preview(self.project,'scan','c',[{'kind':'kmesh','meshes':[[4,0,4]]}])
        with self.assertRaises(ValueError):scans.preview(self.project,'scan','c',[{'kind':'incar','tag':'NSW','start':'0','end':'1','step':'0.5'}])

    def test_four_dimensions_and_decimal_endpoint(self):
        dims=[{'kind':'incar','tag':'ENCUT','start':'400','end':'450','step':'50'},
              {'kind':'incar','tag':'EDIFF','start':'0.000001','end':'0.000002','step':'0.000001'},
              {'kind':'incar','tag':'SIGMA','start':'0.05','end':'0.10','step':'0.05'},
              {'kind':'kmesh','meshes':[[4,4,4],[6,6,6]]}]
        report=scans.preview(self.project,'scan','c',dims)
        self.assertEqual(report['case_count'],16)
        self.assertTrue(report['ready'])
        with self.assertRaisesRegex(ValueError,'1–4'):scans.preview(self.project,'scan','c',dims+[dims[0]])
        values=scans.axis({'kind':'incar','tag':'ENCUT','start':'400','end':'450','step':'20'},'scf')
        self.assertEqual(values,['400','420','440'])

    def test_u_scan_changes_actual_incar_and_rejects_inert_orbital(self):
        shared=self.project['parameters']['shared']
        shared['values'].update(LDAU='true',LDAUTYPE='2',LMAXMIX='2')
        shared['hubbard']={'Si':{'l':'1','u':'0','j':'0'}}
        dims=[{'kind':'u','element':'Si','start':'0','end':'2','step':'1'}]
        report=scans.preview(self.project,'scan','c',dims)
        self.assertTrue(report['ready'])
        batch=scans.freeze(self.project,'scan','c',dims,'模型扫描')
        self.assertEqual([ex.bundle(c['bundle_id'])[1]['INCAR'].decode().split('LDAUU = ')[1].splitlines()[0] for c in batch['cases']],['0.0','1.0','2.0'])
        shared['hubbard']['Si']['l']='-1'
        inert=scans.preview(self.project,'scan','c',dims)
        self.assertFalse(inert['ready'])
        self.assertTrue(any('相同' in e for c in inert['cases'] for e in c['errors']))

    def test_authorized_worker_does_not_repeat_unknown(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'450','step':'50'}],'比较')
        batch['authorization']={'config':{'alias':'jutai-1'},'node':'node01','cores':None,'reason':'测试','template_id':None,'dependency_attempt_id':None,'dependency_reason':'','preview_profile':{'node':'node01'},'approved_script_sha256':hashlib.sha256(b'script').hexdigest()}
        batch['state']='ACTIVE';scans.save(batch)
        def prepare(*args,**kwargs):
            record={'id':'attempt-1','state':'PREPARED','config':{'alias':'jutai-1'}}
            with ex.db() as connection: connection.execute('INSERT INTO attempts VALUES(?,?,?)',(record['id'],record['state'],json.dumps(record)))
            return record
        with patch.object(scans.servers,'probe',return_value={'id':'probe'}),patch.object(scans.servers,'preview',return_value={'resource_ready':True,'profile':{'node':'node01'},'script':'script'}),patch.object(ex,'prepare',side_effect=prepare) as prep,patch.object(ex,'submit',return_value={'state':'UNKNOWN'}) as submit:
            scans.tick();self.assertEqual(submit.call_count,1)
            record=ex.get('attempt-1');record['state']='UNKNOWN';ex.save(record)
            scans.tick();self.assertEqual(prep.call_count,1)
        self.assertEqual(scans.get(batch['id'])['state'],'BLOCKED')

    def test_authorization_binds_case_table_and_reviewed_script(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'400','step':'1'}],'比较')
        profile={'node':'node01'}
        with patch.object(scans.servers,'probe',return_value={'id':'p'}),patch.object(scans.servers,'preview',return_value={'resource_ready':True,'profile':profile,'script':'reviewed script'}),patch.object(ex,'submit') as submit:
            with self.assertRaisesRegex(ValueError,'预览已变化'):
                scans.authorize(batch['id'],'p',batch['case_table_hash'],{'alias':'jutai-1'},'node01',None,'测试','wrong',profile)
            authorized=scans.authorize(batch['id'],'p',batch['case_table_hash'],{'alias':'jutai-1'},'node01',None,'','reviewed script',profile)
            self.assertEqual(authorized['state'],'ACTIVE')
            self.assertEqual(authorized['authorization']['reason'],'')
            submit.assert_not_called()
            with self.assertRaises(ValueError):scans.authorize(batch['id'],'p',batch['case_table_hash'],{'alias':'jutai-1'},'node01',None,'测试','reviewed script',profile)

    def test_prepared_case_recovers_without_new_prepare(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'400','step':'1'}],'比较')
        batch['authorization']={'config':{'alias':'jutai-1'},'node':'node01','cores':None,'reason':'测试','template_id':None,'dependency_attempt_id':None,'dependency_reason':'','preview_profile':{},'approved_script_sha256':'x'}
        batch['state']='ACTIVE';batch['cases'][0].update(state='PREPARED',attempt_id='prepared')
        scans.save(batch)
        with ex.db() as connection:
            connection.execute('INSERT INTO attempts VALUES(?,?,?)',('prepared','PREPARED',json.dumps({'id':'prepared','state':'PREPARED','config':{'alias':'jutai-1'}})))
        with patch.object(ex,'prepare') as prepare,patch.object(ex,'submit',return_value={'state':'SUBMITTED'}) as submit:
            scans.tick()
            prepare.assert_not_called();submit.assert_called_once_with('prepared')

    def test_pause_wins_over_stale_worker_save(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'400','step':'1'}],'比较')
        stale=scans.get(batch['id'])
        stale['state']='ACTIVE';scans.save(stale)
        fresh=scans.pause(batch['id'],'p')
        self.assertEqual(fresh['state'],'PAUSED')
        with self.assertRaisesRegex(ValueError,'其他操作更新'):scans.save(stale)

    def test_metric_source_and_manual_promotion(self):
        batch=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'400','step':'1'}],'比较')
        batch['state']='COMPLETE';batch['cases'][0].update(state='COMPLETED',attempt_id='done');scans.save(batch)
        record={'id':'done','project_id':'p','state':'COMPLETED','bundle_id':batch['cases'][0]['bundle_id'],
                'config':{'alias':'jutai-1'},'evidence':{'vasp_timing':True,'ediff_reached':True,'wrapper_exit':0}}
        output='FPILOT_SHA='+('a'*64)+'\n free  energy   TOTEN  = -12.5000 eV\n number of electron  8.0 magnetization  2.0\n Elapsed time (sec): 120.5\nFPILOT_SHA_AFTER='+('a'*64)+'\n'
        with patch.object(ex,'get',return_value=record),patch.object(ex,'attempt_path',return_value={'shell':'$HOME/230269265lht/material-projects/p/flows/main/stages/02-scf/attempts/a'}),patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':output,'stderr':''}):
            row=scan_results.compare('p',['done'])['rows'][0]
            self.assertEqual(row['metrics']['energy']['value'],-12.5)
            self.assertEqual(row['metrics']['magnetization']['value'],2)
            self.assertEqual(row['metrics']['elapsed']['value'],120.5)
            self.assertEqual(row['metrics']['energy']['source']['sha256'],'a'*64)
            selected=scan_results.promote(batch['id'],'p',batch['cases'][0]['id'],'选择该收敛 case')
            self.assertEqual(selected['attempt_id'],'done')
            self.assertEqual(scans.get(batch['id'])['promotion']['case_id'],batch['cases'][0]['id'])
        batch=scans.get(batch['id']);batch['stage']='relax';scans.save(batch)
        with patch.object(ex,'get',return_value=record),patch.object(scan_results,'_extract',return_value={'ionic_converged':False}):
            with self.assertRaisesRegex(ValueError,'离子收敛'):scan_results.promote(batch['id'],'p',batch['cases'][0]['id'],'再次选择')


if __name__=='__main__':unittest.main()
