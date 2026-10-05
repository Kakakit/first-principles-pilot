import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_scans as fixtures
from backend import execution as ex, scans, candidate_pool, recycle


class PoolTests(unittest.TestCase):
    setUp=fixtures.ScanTests.setUp
    tearDown=fixtures.ScanTests.tearDown

    def batch(self):
        return scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'550','step':'50'}],'池测试')

    def rendered(self,config,probe,node,cores=None,reason=''):
        busy=node in getattr(self,'busy_nodes',set())
        return {'script':'script '+node,'profile':{'node':node,'ppn':cores or 4},'resource_ready':not busy,
                'node_status':'BUSY' if busy else 'FULLY_IDLE','blockers':['selected node CPUS_FREE must be greater than zero'] if busy else []}

    def approved(self,parallel=2):
        batch=self.batch()
        choices=[{'node':n,'cores':4,'reason':''} for n in ('node01','node02')]
        with patch.object(scans.servers,'probe',return_value={'id':'probe'}),patch.object(scans.servers,'preview',side_effect=self.rendered):
            previews=scans.resource_preview(batch['id'],'p',{'alias':'jutai-1'},choices)['previews']
            return scans.authorize(batch['id'],'p',batch['case_table_hash'],{'alias':'jutai-1'},candidates=choices,approved_previews=previews,max_parallel=parallel)

    def prepare(self,bundle,config,probe,node,cores,reason,template,**kwargs):
        record={'id':kwargs['attempt_id'],'bundle_id':bundle,'project_id':'p','state':'PREPARED','config':config,
                'execution_scope':'scan','node':node,'cores':cores,'reason':reason,'profile':{'node':node,'ppn':cores}}
        with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(record['id'],'PREPARED',json.dumps(record)))
        return record

    def submit(self,key):
        record=ex.get(key);record.update(state=getattr(self,'submission_state','SUBMITTED'),job_id=key[:6]+'.mock');ex.save(record)
        return record

    def run_tick(self,limits=None):
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',side_effect=self.rendered),patch.object(scans.control,'settings',return_value=limits or {'max_inflight_total':4,'max_inflight_per_host':4,'max_cores_per_host':None}),patch('backend.extension_runs.active_usage',return_value=[]),patch.object(ex,'prepare',side_effect=self.prepare) as prepare,patch.object(ex,'submit',side_effect=self.submit) as submit:
            scans.tick()
            return prepare.call_count,submit.call_count

    def test_authorization_uses_busy_candidates_and_exact_script_per_node(self):
        self.busy_nodes={'node01'}
        batch=self.approved()
        self.assertEqual(batch['authorization']['max_parallel'],2)
        self.assertEqual([c['node'] for c in batch['authorization']['candidates']],['node01','node02'])
        self.assertEqual(self.run_tick(),(1,1))
        current=scans.get(batch['id'])
        self.assertEqual(ex.get(current['cases'][0]['attempt_id'])['node'],'node02')
        self.assertEqual(current['state'],'ACTIVE');self.assertIn('占用',current['waiting_reason'])

    def test_one_cycle_fills_distinct_nodes_and_busy_wait_never_repeats(self):
        batch=self.approved()
        self.assertEqual(self.run_tick(),(2,2))
        current=scans.get(batch['id'])
        self.assertEqual({ex.get(c['attempt_id'])['node'] for c in current['cases'] if c['attempt_id']},{'node01','node02'})
        self.assertEqual(self.run_tick(),(0,0))
        first=current['cases'][0]['attempt_id'];record=ex.get(first);record['state']='COMPLETED';ex.save(record)
        self.assertEqual(self.run_tick(),(1,1))
        later=scans.get(batch['id'])
        self.assertEqual(later['cases'][0]['attempt_id'],first)
        self.assertEqual(ex.get(later['cases'][2]['attempt_id'])['node'],'node01')

    def test_batch_and_global_and_core_caps_wait_without_blocking(self):
        for kind,parallel,limits in [('batch',1,{'max_inflight_total':4,'max_inflight_per_host':4,'max_cores_per_host':None}),('host',2,{'max_inflight_total':4,'max_inflight_per_host':1,'max_cores_per_host':None}),('core',2,{'max_inflight_total':4,'max_inflight_per_host':4,'max_cores_per_host':4})]:
            batch=self.approved(parallel)
            self.assertEqual(self.run_tick(limits),(1,1),kind)
            self.assertEqual(scans.get(batch['id'])['state'],'ACTIVE')
            # Isolate each authorization in the same disposable fixture.
            with ex.db() as db:db.execute('DELETE FROM attempts');db.execute('DELETE FROM scan_batches')

    def test_all_busy_waits_but_runtime_or_script_changes_block(self):
        self.busy_nodes={'node01','node02'};batch=self.approved()
        self.assertEqual(self.run_tick(),(0,0));self.assertEqual(scans.get(batch['id'])['state'],'ACTIVE')
        self.busy_nodes=set()
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',return_value={'script':'changed','profile':{},'resource_ready':True}),patch.object(ex,'submit') as submit:
            scans.tick();submit.assert_not_called()
        self.assertEqual(scans.get(batch['id'])['state'],'BLOCKED')

    def test_unknown_stops_pool_and_resume_does_not_resubmit(self):
        batch=self.approved();self.submission_state='UNKNOWN'
        self.assertEqual(self.run_tick(),(1,1));self.assertEqual(self.run_tick(),(0,0))
        with self.assertRaisesRegex(ValueError,'UNKNOWN'):scans.resume(batch['id'],'p')

    def test_residual_load_waits_then_continues_pending_cases_once(self):
        batch=self.approved(1)
        self.assertEqual(self.run_tick(),(1,1))
        current=scans.get(batch['id']);first=current['cases'][0]['attempt_id']
        record=ex.get(first);record['state']='COMPLETED';ex.save(record)
        self.busy_nodes={'node01','node02'}
        original=self.rendered
        def residual(*args,**kwargs):
            return {**original(*args,**kwargs),'node_status':'SUSPECT_LOAD'}
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',side_effect=residual),patch.object(ex,'prepare') as prepare,patch.object(ex,'submit') as submit:
            scans.tick()
            prepare.assert_not_called();submit.assert_not_called()
        current=scans.get(batch['id'])
        self.assertEqual(current['state'],'ACTIVE')
        self.assertIn('负载未恢复',current['waiting_reason'])
        self.assertEqual([c['state'] for c in current['cases']],['COMPLETED','PENDING','PENDING','PENDING'])
        self.busy_nodes=set()
        self.assertEqual(self.run_tick(),(1,1))
        self.assertEqual(self.run_tick(),(0,0))
        self.assertEqual(scans.get(batch['id'])['cases'][0]['attempt_id'],first)

    def test_residual_load_with_failed_diagnostic_blocks_with_real_reason(self):
        batch=self.approved()
        value={'script':'script node01','profile':{'node':'node01','ppn':4},'resource_ready':False,
               'node_status':'SUSPECT_LOAD','blockers':['selected node CPUS_FREE must be greater than zero','运行时路径或调度器诊断未通过']}
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',return_value=value),patch.object(ex,'prepare') as prepare,patch.object(ex,'submit') as submit:
            scans.tick();prepare.assert_not_called();submit.assert_not_called()
        current=scans.get(batch['id'])
        self.assertEqual(current['state'],'BLOCKED')
        self.assertIn('运行时路径或调度器诊断未通过',current['blocker'])
        self.assertNotIn('偏离授权预览',current['blocker'])
        self.assertNotIn('waiting_reason',current)

    def test_capacity_changes_at_final_gate_wait_on_original_prepared_attempt(self):
        batch=self.approved()
        def busy_at_submit(key):
            self.busy_nodes={'node01'}
            raise ValueError('实时资源已变化或不可用，请重新准备；没有提交')
        with patch.object(scans.servers,'capacity_probe',return_value={'id':'live'}),patch.object(scans.servers,'preview',side_effect=self.rendered),patch.object(scans.control,'settings',return_value={'max_inflight_total':4,'max_inflight_per_host':4,'max_cores_per_host':None}),patch('backend.extension_runs.active_usage',return_value=[]),patch.object(ex,'prepare',side_effect=self.prepare),patch.object(ex,'submit',side_effect=busy_at_submit):
            scans.tick()
        current=scans.get(batch['id']);original=current['cases'][0]['attempt_id']
        self.assertEqual(current['state'],'ACTIVE');self.assertEqual(ex.get(original)['state'],'PREPARED')
        self.busy_nodes=set()
        self.assertEqual(self.run_tick(),(1,2));self.assertEqual(scans.get(batch['id'])['cases'][0]['attempt_id'],original)

    def test_prepared_record_resumes_exact_node_and_busy_record_stays_prepared(self):
        batch=self.approved()
        case=batch['cases'][0];c=batch['authorization']['candidates'][1]
        key=hashlib.sha256((case['id']+':0').encode()).hexdigest()[:32]
        self.prepare(case['bundle_id'],batch['authorization']['config'],'p',c['node'],4,'',None,attempt_id=key)
        case.update(state='PREPARED',attempt_id=key);scans.save(batch)
        self.busy_nodes={'node02'}
        self.assertEqual(self.run_tick(),(0,0));self.assertEqual(ex.get(key)['state'],'PREPARED')
        self.busy_nodes=set()
        self.assertEqual(self.run_tick(),(1,2));self.assertEqual(ex.get(key)['node'],'node02')

    def test_orphaned_preparation_is_recovered_without_new_prepare(self):
        batch=self.approved(1);case=batch['cases'][0]
        key=hashlib.sha256((case['id']+':0').encode()).hexdigest()[:32]
        self.prepare(case['bundle_id'],batch['authorization']['config'],'p','node02',4,'',None,attempt_id=key)
        self.assertEqual(self.run_tick(),(0,1));self.assertEqual(scans.get(batch['id'])['cases'][0]['attempt_id'],key)

    def test_legacy_prepared_authorization_resumes_original_attempt(self):
        batch=self.batch();config={'alias':'jutai-1'}
        with patch.object(scans.servers,'probe',return_value={'id':'probe'}),patch.object(scans.servers,'preview',side_effect=self.rendered):
            value=self.rendered(config,'probe','node01',4,'')
            batch=scans.authorize(batch['id'],'p',batch['case_table_hash'],config,'node01',4,'',value['script'],value['profile'])
        case=batch['cases'][0]
        key=hashlib.sha256((case['id']+':0').encode()).hexdigest()[:32]
        self.prepare(case['bundle_id'],config,'probe','node01',4,'',None,attempt_id=key)
        case.update(state='PREPARED',attempt_id=key);scans.save(batch)
        # Legacy PREPARED records retain submit's original final gate.
        with patch.object(scans.servers,'probe',side_effect=ValueError('stop new cases')),patch.object(ex,'submit',side_effect=self.submit) as submit,patch('backend.extension_runs.active_usage',return_value=[]):
            scans.tick()
        self.assertEqual(submit.call_count,1)
        self.assertEqual(scans.get(batch['id'])['cases'][0]['attempt_id'],key)
        self.assertEqual(ex.get(key)['state'],'SUBMITTED')

    def test_candidate_validation_and_missing_review_and_wrong_project(self):
        for values in ([],[{'node':'x','cores':0}],[{'node':'x','cores':4}]*2):
            with self.assertRaises(ValueError):candidate_pool.validate(values)
        batch=self.batch();choices=[{'node':'node01','cores':4}]
        with self.assertRaisesRegex(ValueError,'当前项目'):scans.resource_preview(batch['id'],'other',{},choices)
        with patch.object(scans.servers,'probe',return_value={'id':'p'}),patch.object(scans.servers,'preview',side_effect=self.rendered):
            with self.assertRaisesRegex(ValueError,'不完整'):scans.authorize(batch['id'],'p',batch['case_table_hash'],{'alias':'jutai-1'},candidates=choices,approved_previews={})


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(recycle,'ROOT',self.root);self.patch.start()
        self.project={'id':'a'*32,'name':'删除测试','remoteSlug':'sample','nodes':[{'id':'s','data':{'kind':'structure','executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}}],'edges':[]}
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def record(self,table,state,**extra):
        item={'id':'b'*32,'project_id':self.project['id'],'state':state,**extra}
        with sqlite3.connect(self.root/'execution.sqlite3') as db:
            if table=='attempts':
                db.execute('CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,state TEXT,body TEXT)')
                db.execute('INSERT INTO attempts VALUES(?,?,?)',(item['id'],state,json.dumps(item)))
            else:
                db.execute('CREATE TABLE IF NOT EXISTS '+table+' (id TEXT PRIMARY KEY,project_id TEXT,state TEXT,body TEXT)')
                db.execute('INSERT INTO '+table+' VALUES(?,?,?,?)',(item['id'],self.project['id'],state,json.dumps(item)))
    def test_project_cascades_unknown_and_active_plans_but_restore_does_not_resume(self):
        self.record('attempts','UNKNOWN');self.record('chain_runs','ACTIVE');self.record('scan_batches','ACTIVE')
        with patch.object(ex,'ROOT',self.root):
            recycle.move('projects',self.project['id'],self.project)
            self.assertEqual({i['kind'] for i in recycle.all_items()},{'projects','attempts','chains','scans'})
            self.assertEqual(ex.history(),[])
            recycle.restore('projects',self.project['id'])
            self.assertEqual(ex.get('b'*32)['state'],'UNKNOWN')
        with sqlite3.connect(self.root/'execution.sqlite3') as db:
            self.assertEqual(db.execute('SELECT state FROM scan_batches').fetchone()[0],'PAUSED')
    def test_unused_preparations_and_terminal_data_survive_project_roundtrip(self):
        self.record('attempts','PREPARED');self.record('scan_batches','FROZEN')
        original=self.root/'original.POSCAR';original.write_text('original')
        with patch.object(ex,'ROOT',self.root):
            recycle.move('projects',self.project['id'],self.project)
            self.assertEqual(recycle.list_items('projects')[0]['label'],'删除测试')
            self.assertEqual(recycle.restore('projects',self.project['id'])['project'],self.project)
        self.assertEqual(original.read_text(),'original')
    def test_active_local_analysis_can_be_recycled_with_project(self):
        key='c'*32;path=self.root/'analyses-v2'/key/'analysis.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'id':key,'project_id':self.project['id'],'state':'RUNNING'}))
        recycle.move('projects',self.project['id'],self.project)
        self.assertTrue(recycle.is_recycled('analyses',key))
