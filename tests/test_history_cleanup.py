"""Local cleanup removes only owned data and keeps execution/source contracts."""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from backend import recycle, execution as ex, extension_runs, electronic_plots
from backend import local_analyses, postprocessing, interactive_plots, chain_runs, scans, inputs, wannier_compare


class HistoryCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (recycle,ex,extension_runs,electronic_plots,local_analyses,postprocessing,interactive_plots,inputs,wannier_compare):
            self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(interactive_plots,'CACHE_ROOT',self.root/'projection-cache'))
        self.stack.enter_context(patch.object(interactive_plots,'SAVED_ROOT',self.root/'plot-datasets'))
        self.stack.enter_context(patch.object(wannier_compare,'ROOT_COMPARE',self.root/'wannier-comparisons'))
        self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('cleanup must not inspect or delete remote data')))
        with ex.db() as db:pass
        for table in ('chain_runs','scan_batches'):
            with sqlite3.connect(self.root/'execution.sqlite3') as db:
                db.execute('CREATE TABLE '+table+' (id TEXT PRIMARY KEY,project_id TEXT,state TEXT,body TEXT)')
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)

    def write(self,path,value):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(value) if isinstance(value,dict) else value);return target

    def attempt(self,key='a'*32,state='FAILED',**extra):
        record={'id':key,'state':state,'project_id':'p','config':{'alias':'mock'},'job_id':'123.mock',**extra}
        with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,state,json.dumps(record)))
        return record

    def analysis(self,kind='analyses',key='b'*32,state='FAILED',**extra):
        record={'id':key,'project_id':'p','state':state,'created_at':'2026-10-01T00:00:00Z',
                'sources':[],'schema':'fpilot.local-analysis.v1',**extra}
        filename='record.json' if kind in ('saved-plots','comparisons') else 'analysis.json'
        self.write(Path(recycle.DIRECTORIES[kind])/key/filename,record)
        self.write(Path(recycle.DIRECTORIES[kind])/key/'stderr.log','fixture failure')
        return record

    def plan(self,kind,key='c'*32,state='FROZEN',**extra):
        value={'id':key,'state':state,'project_id':'p',**extra}
        with sqlite3.connect(self.root/'execution.sqlite3') as db:
            db.execute('INSERT INTO '+recycle.TABLES[kind]+' VALUES(?,?,?,?)',(key,'p',state,json.dumps(value)))
        return value

    def frozen(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('project.json',json.dumps({'id':'p'}))
            z.writestr('manifest.json',json.dumps({'node_id':'n','status':'assembled','stage':'scf','reason':'fixture'}))
        key=hashlib.sha256(stream.getvalue()).hexdigest()
        target=self.root/'inputs'/(key+'.zip');target.parent.mkdir(exist_ok=True);target.write_bytes(stream.getvalue())
        return key,target

    def test_hide_restore_across_attempt_and_analysis_histories_keeps_direct_access(self):
        a=self.attempt();b=self.analysis(sources=[{'attempt_id':a['id']}])
        recycle.move('attempts',a['id']);recycle.move('analyses',b['id'])
        self.assertEqual(ex.history('p'),[]);self.assertEqual(extension_runs.history('p'),[])
        self.assertEqual(ex.get(a['id'])['state'],'FAILED');self.assertEqual(extension_runs.get(b['id'])['state'],'FAILED')
        recycle.restore('attempts',a['id']);recycle.restore('analyses',b['id'])
        self.assertEqual(len(ex.history('p')),1);self.assertEqual(len(extension_runs.history('p')),1)

    def test_every_state_can_be_recycled_without_remote_action(self):
        for index,state in enumerate(('RUNNING','UNKNOWN','DISPATCHING','SUBMITTED','PREPARED','FROZEN')):
            key=f'{index+1:032x}';self.attempt(key,state)
            recycle.move('attempts',key)
            self.assertTrue(recycle.is_recycled('attempts',key))
            self.assertEqual(ex.get(key)['state'],state)
        for index,state in enumerate(('RUNNING','UNKNOWN','DISPATCHING','SUBMITTED')):
            key=f'{index+11:032x}';self.analysis(key=key,state=state)
            recycle.move('analyses',key)
        self.plan('chains',state='ACTIVE')
        recycle.move('chains','c'*32)
        self.assertEqual(chain_runs.get('c'*32)['state'],'PAUSED')
        recycle.restore('chains','c'*32)
        self.assertEqual(chain_runs.get('c'*32)['state'],'PAUSED')

    def test_user_can_recycle_unknown_analysis(self):
        self.analysis(state='UNKNOWN',location='scheduler',runtime={'config':{'alias':'mock'}},requested_cores=24)
        recycle.move('analyses','b'*32)
        self.assertEqual(extension_runs.history('p'),[])
        self.assertEqual(extension_runs.get('b'*32)['state'],'UNKNOWN')

    def test_failed_analysis_purge_frees_owned_files_and_keeps_originals(self):
        record=self.analysis();original=self.write('originals/source.POSCAR','original')
        recycle.move('analyses',record['id'])
        with self.assertRaises(ValueError):recycle.purge('analyses',record['id'])
        receipt=recycle.purge('analyses',record['id'],True)
        self.assertFalse(receipt['remote_deleted']);self.assertFalse((self.root/'analyses-v2'/record['id']).exists())
        self.assertEqual(original.read_text(),'original');self.assertEqual(recycle.list_items('analyses'),[])

    def test_referenced_input_can_be_cleared_and_prepared_submit_is_blocked(self):
        key,file=self.frozen();record=self.attempt(state='PREPARED',bundle_id=key)
        recycle.move('inputs',key);self.assertEqual(inputs.history('p'),[])
        receipt=recycle.purge('inputs',key,True)
        self.assertTrue(receipt['references']);self.assertFalse(file.exists())
        with self.assertRaisesRegex(ValueError,'来源已回收或清理'):ex.submit(record['id'])

    def test_unreferenced_input_cleanup_does_not_delete_potcar_library(self):
        key,file=self.frozen();library=self.write('potcars/original.POTCAR','original')
        recycle.move('inputs',key);recycle.purge('inputs',key,True)
        self.assertFalse(file.exists());self.assertEqual(library.read_text(),'original')

    def test_referenced_sources_can_be_purged_and_plans_explain_missing_source(self):
        self.attempt();self.attempt('d'*32,'COMPLETED',band_path={'source':{'attempt_id':'a'*32}})
        recycle.move('attempts','a'*32)
        self.assertIn('下游任务',recycle.purge('attempts','a'*32,True)['references'][0])
        self.attempt('e'*32);self.plan('scans',state='ACTIVE',cases=[{'attempt_id':'e'*32}])
        recycle.move('attempts','e'*32);recycle.purge('attempts','e'*32,True)
        plan=scans.get('c'*32)
        self.assertEqual(plan['state'],'PAUSED');self.assertIn('清理',plan['local_cleanup_blocker'])
        with self.assertRaisesRegex(ValueError,'来源已回收或清理'):scans.resume('c'*32,'p')

    def test_source_purge_reports_frozen_band_reference_without_bulk_scan(self):
        self.attempt();target=self.root/'inputs'/'pending.zip';target.parent.mkdir()
        with zipfile.ZipFile(target,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'band_path_source_attempt_id':'a'*32}))
            archive.writestr('POTCAR','fixture')
        recycle.move('attempts','a'*32)
        self.assertIn('冻结能带输入',recycle.purge('attempts','a'*32,True)['references'][0])
        self.assertTrue(target.exists())

    def test_recycling_prepared_attempt_pauses_its_authorized_plan(self):
        self.attempt(state='PREPARED');self.plan('chains',state='ACTIVE',steps=[{'attempts':['a'*32]}])
        recycle.move('attempts','a'*32)
        self.assertEqual(chain_runs.get('c'*32)['state'],'PAUSED')
        with self.assertRaisesRegex(ValueError,'回收站'):ex.submit('a'*32)
        recycle.restore('attempts','a'*32)
        self.assertEqual(chain_runs.get('c'*32)['state'],'PAUSED')

    def test_attempt_purge_removes_events_and_owned_cache_only(self):
        key='a'*32;self.attempt();self.attempt('d'*32)
        entry={'path':'OUTCAR','size_bytes':100,'modified_at':'fixture'}
        cached_key=hashlib.sha256(f'{key}|OUTCAR|100|fixture'.encode()).hexdigest()
        files=[self.write(key+'.watch.log','log'),self.write('artifact-cache/'+key+'.json',{'files':[entry]}),
               self.write('artifact-cache/previews/preview.json',{'attempt_id':key}),
               self.write('artifact-cache/inputs/'+cached_key,'owned'),
               self.write('result-summaries/'+key+'.json',{})]
        other=self.write('artifact-cache/previews/other.json',{'attempt_id':'d'*32})
        with ex.db() as db:db.execute('INSERT INTO execution_events(attempt_id,at,event,details) VALUES(?,?,?,?)',(key,'now','test','{}'))
        recycle.move('attempts',key);recycle.purge('attempts',key,True)
        with self.assertRaises(ValueError):ex.get(key)
        self.assertTrue(other.exists());self.assertTrue(all(not f.exists() for f in files))
        with ex.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM execution_events').fetchone()[0],0)

    def test_database_failure_restores_all_files_and_recycle_marker(self):
        self.attempt();log=self.write('a'*32+'.watch.log','keep')
        recycle.move('attempts','a'*32)
        # Force a transaction failure after the files have moved.
        with ex.db() as db:
            db.execute('INSERT INTO execution_events(attempt_id,at,event,details) VALUES(?,?,?,?)',('a'*32,'now','test','{}'))
            db.execute("CREATE TRIGGER fail_cleanup BEFORE DELETE ON execution_events BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaisesRegex(ValueError,'已恢复'):recycle.purge('attempts','a'*32,True)
        self.assertEqual(log.read_text(),'keep');self.assertTrue(recycle.is_recycled('attempts','a'*32))
        self.assertEqual(ex.get('a'*32)['state'],'FAILED')

    def test_terminal_plan_histories_restore_and_purge_without_orphaned_monitor(self):
        for kind,module in (('chains',chain_runs),('scans',scans)):
            self.plan(kind);recycle.move(kind,'c'*32);self.assertEqual(module.history('p'),[])
            recycle.restore(kind,'c'*32);self.assertEqual(len(module.history('p')),1)
            recycle.move(kind,'c'*32);recycle.purge(kind,'c'*32,True);self.assertEqual(module.history('p'),[])

    def test_saved_and_legacy_reports_follow_same_history_filter(self):
        legacy=self.analysis('local-analyses',attempt_id='a'*32)
        plot=self.analysis('plots',key='d'*32,attempt_id='a'*32)
        saved=self.analysis('saved-plots',key='e'*32)
        comparison=self.analysis('comparisons',key='f'*32)
        for kind,key in (('local-analyses',legacy['id']),('plots',plot['id']),('saved-plots',saved['id']),('comparisons',comparison['id'])):
            recycle.move(kind,key)
        self.assertEqual(local_analyses.history('p'),[]);self.assertEqual(postprocessing.history('a'*32),[])
        self.assertEqual(electronic_plots.history('p','a'*32),[]);self.assertEqual(interactive_plots.saved('p'),[])
        self.assertEqual(wannier_compare.saved('p'),[])
        for kind,key in (('local-analyses',legacy['id']),('plots',plot['id']),('saved-plots',saved['id']),('comparisons',comparison['id'])):
            recycle.purge(kind,key,True)

    def test_shared_projection_cache_is_retained_when_another_attempt_uses_it(self):
        self.attempt();self.attempt('d'*32)
        cache=self.write('projection-cache/shared/metadata.json',{'band_attempt_id':'a'*32,'dos_attempt_id':'d'*32})
        recycle.move('attempts','a'*32);recycle.purge('attempts','a'*32,True);self.assertTrue(cache.exists())

    def test_symlink_owned_output_is_removed_without_deleting_external_original(self):
        self.attempt();original=self.write('originals/log','original')
        (self.root/('a'*32+'.watch.log')).symlink_to(original)
        recycle.move('attempts','a'*32)
        recycle.purge('attempts','a'*32,True)
        self.assertFalse((self.root/('a'*32+'.watch.log')).is_symlink())
        self.assertEqual(original.read_text(),'original')

    def test_worker_does_not_refresh_recycled_unknown_or_rebuild_failed_cache(self):
        from backend import worker
        self.attempt(state='UNKNOWN');self.attempt('d'*32,'FAILED')
        recycle.move('attempts','a'*32);recycle.move('attempts','d'*32)
        with patch.object(scans,'tick'),patch.object(chain_runs,'tick'),patch.object(extension_runs,'tick'),patch.object(ex,'refresh') as refresh:
            self.assertEqual(worker.tick()['checked'],0);refresh.assert_not_called()
        self.assertFalse((self.root/'failure-snapshots'/('d'*32+'.json')).exists())

    def test_empty_only_explicit_snapshot_and_late_save_cannot_recreate_plan(self):
        self.attempt();self.attempt('d'*32)
        recycle.move('attempts','a'*32);selection=recycle.all_items()
        recycle.move('attempts','d'*32);recycle.empty(selection,True)
        self.assertEqual([i['key'] for i in recycle.all_items()],['d'*32])
        stale=self.plan('scans',state='ACTIVE',revision=0)
        recycle.move('scans',stale['id']);recycle.purge('scans',stale['id'],True)
        scans.save(stale)
        with self.assertRaises(ValueError):scans.get(stale['id'])

    def test_project_purge_removes_owned_records_inputs_and_reports_without_remote_action(self):
        project={'id':'p','name':'本地清理','remoteSlug':'sample','nodes':[{'id':'s','data':{'kind':'structure','executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}}],'edges':[]}
        self.attempt(state='FAILED');self.analysis(state='COMPLETED');self.plan('scans',state='ACTIVE')
        key,file=self.frozen();original=self.write('originals/source','keep')
        recycle.move('projects','p',project)
        self.assertEqual({r['kind'] for r in recycle.all_items()},{'projects','inputs','attempts','analyses','scans'})
        receipt=recycle.purge('projects','p',True)
        self.assertFalse(receipt['remote_deleted']);self.assertFalse(file.exists());self.assertEqual(recycle.all_items(),[])
        self.assertEqual(original.read_text(),'keep')
        with self.assertRaises(ValueError):ex.get('a'*32)
        with self.assertRaises(ValueError):scans.get('c'*32)
        with self.assertRaisesRegex(ValueError,'回收站'):recycle.assert_available('projects','p')

    def test_project_restore_leaves_independently_recycled_children_in_trash(self):
        project={'id':'p','name':'恢复测试','remoteSlug':'sample','nodes':[{'id':'s','data':{'kind':'structure','executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}}],'edges':[]}
        self.attempt();self.attempt('d'*32,'UNKNOWN')
        recycle.move('attempts','a'*32);recycle.move('projects','p',project);recycle.restore('projects','p')
        self.assertTrue(recycle.is_recycled('attempts','a'*32));self.assertFalse(recycle.is_recycled('attempts','d'*32))
        self.assertEqual([r['id'] for r in ex.history('p')],['d'*32])

    def test_wannier_frozen_input_recycles_and_purges_even_when_prepared_attempt_references_it(self):
        from backend import wannier_execution as w90
        key='f'*64
        file=self.write('wannier-bundles/'+key+'.json',{'project_id':'p','node_id':'w','project':{'id':'p'},'seed':'wannier90','alias':'mock','created_at':'now','input_reason':'fixture'})
        self.attempt(state='PREPARED',kind='wannier90',bundle_id=key)
        with patch.object(w90,'ROOT',self.root):
            self.assertEqual(len(w90.configurations('p','w')),1)
            recycle.move('wannier-inputs',key);self.assertEqual(w90.configurations('p','w'),[])
            recycle.purge('wannier-inputs',key,True);self.assertFalse(file.exists())
            with self.assertRaisesRegex(ValueError,'来源已回收或清理'):w90.submit('a'*32)

    def test_purged_attempt_receipt_cannot_recreate_events_or_worker_state(self):
        stale=self.attempt(state='UNKNOWN')
        recycle.move('attempts',stale['id'])
        with self.assertRaisesRegex(ValueError,'保留本地跟踪'):recycle.purge('attempts',stale['id'],True)
        ex.save({**stale,'state':'FAILED'})
        recycle.purge('attempts',stale['id'],True)
        stale.update(state='SUBMITTED',job_id='123.mock');ex.save(stale)
        with ex.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM execution_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)

    def test_attempt_cleanup_retains_independently_frozen_wannier_configuration(self):
        key='f'*64
        frozen=self.write('wannier-bundles/'+key+'.json',{'project_id':'p','project':{'id':'p'},'seed':'wannier90'})
        self.attempt(state='PREPARED',kind='wannier90',bundle_id=key)
        recycle.move('attempts','a'*32);recycle.purge('attempts','a'*32,True)
        self.assertTrue(frozen.exists())
        recycle.move('wannier-inputs',key);recycle.purge('wannier-inputs',key,True)
        self.assertFalse(frozen.exists())

    def test_all_submit_entry_points_reject_recycled_preparations(self):
        from backend import wannier_execution,wannier_plot_restart
        self.attempt(state='PREPARED')
        recycle.move('attempts','a'*32)
        for module in (ex,wannier_execution,wannier_plot_restart):
            with self.assertRaisesRegex(ValueError,'回收站'):module.submit('a'*32)

    def test_recycle_keeps_active_local_analysis_until_termination(self):
        import subprocess,sys
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
        def stop_fixture():
            if process.poll() is None:process.terminate()
            process.wait(timeout=5)
        self.addCleanup(stop_fixture)
        self.analysis(state='RUNNING',location='local');extension_runs._local_processes['b'*32]=process
        try:
            recycle.move('analyses','b'*32)
            self.assertIsNone(process.poll())
            with self.assertRaisesRegex(ValueError,'保留本地跟踪'):recycle.purge('analyses','b'*32,True)
            recycle.restore('analyses','b'*32)
            record=extension_runs.get('b'*32)
            self.assertEqual(record['state'],'RUNNING')
            recycle.move('analyses','b'*32)
            stop_fixture()
            extension_runs.save({**record,'state':'FAILED'})
            recycle.purge('analyses','b'*32,True)
            self.assertFalse((self.root/'analyses-v2'/('b'*32)).exists())
        finally:extension_runs._local_processes.pop('b'*32,None)
