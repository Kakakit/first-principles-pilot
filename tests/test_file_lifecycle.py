"""Exercise the actual remote cleanup script only inside synthetic local directories."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend import execution as ex, extension_runs, file_lifecycle, recycle, site_registration
from backend import project_sync_remote as sync_remote


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        stack = self.enter_stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for module in (ex, extension_runs, recycle, site_registration):
            stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.remote = self.root/'synthetic-remote'
        self.remote.mkdir()

    def attempt(self, key='a'*32, alias='mock-a', state='FAILED', node='a', **extra):
        relative=f'project/flows/main/stages/03-scf/attempts/{key}'
        path=self.remote/relative
        path.mkdir(parents=True,exist_ok=True)
        (path/'_fpilot').mkdir(exist_ok=True)
        (path/'_fpilot'/'attempt.json').write_text(json.dumps({'id':key}))
        record={'id':key,'project_id':'project','node_id':node,'state':state,'path_schema':'fpilot.remote-attempt-path',
                'remote_workspace_root':str(self.remote),'remote_workspace_root_abs':str(self.remote),
                'remote_relative_path':relative,'remote_path_absolute':str(path),
                'attempt_segment':key,'config':{'alias':alias,'scheduler':'custom'},**extra}
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,state,json.dumps(record)))
        return record,path

    def analysis(self, key='b'*32, state='COMPLETED', **extra):
        path=self.remote/'project'/'analyses'/key
        path.mkdir(parents=True)
        (path/'request.json').write_text(json.dumps({'analysis_id':key}))
        record={'id':key,'project_id':'project','state':state,'location':'ssh','created_at':ex.now(),'sources':[],
                'remote_dir':str(path),'runtime':{'alias':'mock-b','python':sys.executable},**extra}
        directory=extension_runs.directory(key)
        directory.mkdir(parents=True)
        (directory/'analysis.json').write_text(json.dumps(record))
        return record,path

    @staticmethod
    def shell(alias, script, **kwargs):
        result=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True,timeout=10)
        return {'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}

    def test_retain_purge_later_clean_and_keep_linked_neighbor(self):
        record,path=self.attempt()
        (path/'WAVECAR').write_bytes(b'scientific input')
        neighbor=self.remote/'external-WAVECAR'
        os.link(path/'WAVECAR',neighbor)
        key=record['id']
        with patch.object(ex,'ssh',side_effect=AssertionError('keep and purge are local')):
            result=file_lifecycle.execute('attempts',key)
            self.assertTrue(result['recycled'])
            recycle.purge('attempts',key,True)
        self.assertTrue(path.is_dir())
        asset,=file_lifecycle.list_assets()
        self.assertEqual((asset['alias'],asset['path']),('mock-a',str(path)))
        file_lifecycle.queue([asset['id']])
        with patch.object(ex,'ssh',side_effect=self.shell):
            file_lifecycle.tick()
        self.assertEqual(file_lifecycle.list_assets()[0]['state'],'deleted')
        self.assertFalse(path.exists())
        self.assertEqual(neighbor.read_bytes(),b'scientific input')

    def test_multi_host_partial_success_and_retry_never_deletes_analysis_sources(self):
        first,source=self.attempt()
        second,analysis=self.analysis(sources=[{'kind':'attempt','attempt_id':first['id']}])
        sibling,other=self.attempt('c'*32,alias='mock-c')
        assets=[]
        for kind,record in [('analyses',second),('attempts',sibling)]:
            preview=file_lifecycle.preview(kind,record['id'])
            result=file_lifecycle.execute(kind,record['id'],asset_ids=[preview['assets'][0]['id']])
            assets.extend(result['assets'])
        def offline(alias,script,**options):
            if alias=='mock-c':
                return {'exit_code':None,'transport_state':'NOT_SENT','stderr':'offline'}
            return self.shell(alias,script,**options)
        with patch.object(ex,'ssh',side_effect=offline):file_lifecycle.tick()
        statuses={row['alias']:row['state'] for row in file_lifecycle.list_assets()}
        self.assertEqual(statuses,{'mock-b':'deleted','mock-c':'waiting'})
        self.assertTrue(source.is_dir())
        self.assertFalse(analysis.exists())
        self.assertTrue(other.is_dir())
        with patch.object(ex,'ssh',side_effect=self.shell):file_lifecycle.tick()
        self.assertFalse(other.exists())

    def test_lost_receipt_only_checks_original_quarantine_and_preserves_recreated_directory(self):
        record,path=self.analysis()
        key=record['id']
        asset=file_lifecycle.preview('analyses',key)['assets'][0]
        file_lifecycle.execute('analyses',key,asset_ids=[asset['id']])
        def lose(alias,script,**options):
            self.assertEqual(self.shell(alias,script)['exit_code'],0)
            return {'exit_code':None,'transport_state':'UNCERTAIN','stderr':'lost acknowledgement'}
        with patch.object(ex,'ssh',side_effect=lose):file_lifecycle.tick()
        self.assertEqual(file_lifecycle.list_assets()[0]['state'],'unknown')
        path.mkdir()
        (path/'request.json').write_text(json.dumps({'analysis_id':key}))
        (path/'new-content').touch()
        with patch.object(ex,'ssh',side_effect=self.shell):file_lifecycle.tick()
        self.assertEqual(file_lifecycle.list_assets()[0]['state'],'blocked')
        self.assertTrue((path/'new-content').exists())

    def _interrupted_cleanup(self):
        record,path=self.analysis()
        asset=file_lifecycle.preview('analyses',record['id'])['assets'][0]
        operation=file_lifecycle.execute('analyses',record['id'],asset_ids=[asset['id']])['operation']
        with patch.object(ex,'ssh',return_value={'exit_code':None,'transport_state':'UNCERTAIN','stderr':'not executed'}):
            file_lifecycle.tick()
        with patch.object(ex,'ssh',side_effect=self.shell):
            file_lifecycle.tick()
        self.assertTrue(path.is_dir())
        self.assertEqual(file_lifecycle.list_assets()[0]['state'],'blocked')
        return asset['id'],path,operation

    def test_explicit_repreview_recovers_interrupted_cleanup_with_new_operation(self):
        asset_id,path,old_operation=self._interrupted_cleanup()
        old_token=file_lifecycle._stored(asset_id)['token']
        with self.assertRaisesRegex(ValueError,'原清理状态已变化'):
            file_lifecycle.queue([asset_id],check_operation='other-operation')
        self.assertEqual(file_lifecycle.queue([asset_id],check_operation=old_operation)['operation'],old_operation)
        with patch.object(ex,'ssh',side_effect=self.shell):file_lifecycle.tick()
        with patch.object(ex,'ssh',side_effect=self.shell) as ssh, \
             patch.object(site_registration,'connection_identity',return_value={'hostname':'fixture'}):
            preview=file_lifecycle.preview_recovery(asset_id)
            self.assertFalse(ssh.call_args.kwargs['mutating'])
            self.assertNotIn('rm -rf',ssh.call_args.args[1])
            self.assertTrue(path.is_dir())
            new=file_lifecycle.confirm_recovery(asset_id,preview['token'])
            with self.assertRaises(ValueError):file_lifecycle.confirm_recovery(asset_id,preview['token'])
            file_lifecycle.tick()
        self.assertFalse(path.exists())
        self.assertNotEqual(new['operation'],old_operation)
        self.assertNotEqual(file_lifecycle._stored(asset_id)['token'],old_token)
        self.assertEqual(file_lifecycle.operation_status(old_operation)[0]['state'],'blocked')
        self.assertEqual(file_lifecycle.operation_status(new['operation'])[0]['state'],'deleted')

    def test_replacement_after_recovery_preview_requires_another_confirmation(self):
        asset_id,path,_=self._interrupted_cleanup()
        with patch.object(ex,'ssh',side_effect=self.shell), \
             patch.object(site_registration,'connection_identity',return_value={'hostname':'fixture'}):
            preview=file_lifecycle.preview_recovery(asset_id)
            path.rename(path.with_name('old-instance'))
            path.mkdir()
            (path/'request.json').write_text(json.dumps({'analysis_id':path.name}))
            file_lifecycle.confirm_recovery(asset_id,preview['token'])
            file_lifecycle.tick()
            file_lifecycle.queue([asset_id])
            file_lifecycle.tick()
        self.assertTrue(path.is_dir())
        self.assertTrue(file_lifecycle.list_assets()[0]['recovery_required'])

    def test_project_preview_allows_cleaning_source_of_analysis_recycled_in_same_batch(self):
        a,path=self.attempt()
        queued,_=self.analysis(state='WAIT_RESOURCE',sources=[{'kind':'attempt','attempt_id':a['id']}])
        plan=file_lifecycle.preview('projects','project')
        source=next(x for x in plan['assets'] if x['kind']=='attempts')
        self.assertIsNone(source['blocked'])
        project={'id':'project','name':'project','remoteSlug':'project','edges':[],'nodes':[]}
        receipt=file_lifecycle.execute('projects','project',project,asset_ids=[source['id']])
        self.assertTrue(recycle.is_recycled('analyses',queued['id']))
        self.assertEqual(receipt['assets'][0]['state'],'pending')
        with patch.object(ex,'ssh',side_effect=self.shell):
            file_lifecycle.tick()
        self.assertFalse(path.exists())

    def test_batch_preview_keeps_outside_queued_and_in_batch_running_readers(self):
        a,_=self.attempt()
        sources=[{'kind':'attempt','attempt_id':a['id']}]
        outside,_=self.analysis(state='QUEUED',project_id='other-project',sources=sources)
        running,_=self.analysis('c'*32,state='RUNNING',sources=sources)
        plan=file_lifecycle.preview('projects','project')
        blocker=next(x for x in plan['assets'] if x['kind']=='attempts')['blocked']
        self.assertIn(outside['id'][:12],blocker)
        self.assertIn(running['id'][:12],blocker)

    def test_source_and_active_blockers_and_node_scope(self):
        a,_=self.attempt()
        b,_=self.attempt('c'*32,node='b')
        queued,_=self.analysis(state='QUEUED',sources=[{'kind':'attempt','attempt_id':a['id']},{'kind':'attempt','attempt_id':b['id']}])
        project={'id':'project','name':'project','remoteSlug':'project','edges':[],
                 'nodes':[{'id':k,'data':{'label':k,'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}} for k in ('a','b')]}
        plan=file_lifecycle.preview('nodes','a','project')
        self.assertEqual(plan['records'],[{'kind':'attempts','key':a['id']}])
        self.assertIn('仍有来源引用',plan['assets'][0]['blocked'])
        receipt=file_lifecycle.execute('nodes','a',project,project_id='project')
        self.assertFalse(recycle.is_recycled('analyses',queued['id']))
        self.assertFalse(recycle.is_recycled('attempts',b['id']))
        view=recycle.workspace_after_recycle({'activeId':'project','projects':[project]})
        self.assertEqual([n['id'] for n in view['projects'][0]['nodes']],['b'])
        restored=recycle.restore('nodes',receipt['key'])
        self.assertEqual(restored['node']['id'],'a')
        self.assertFalse(recycle.is_recycled('attempts',a['id']))

    def test_active_recycled_attempt_remains_available_for_worker_refresh(self):
        record,_=self.attempt(state='RUNNING',config={'alias':'mock','scheduler':'pbs'})
        recycle.move('attempts',record['id'])
        with self.assertRaisesRegex(ValueError,'保留本地跟踪'):recycle.purge('attempts',record['id'],True)
        self.assertNotIn(record['id'],[row['id'] for row in ex.history()])
        with patch.object(ex,'ssh',side_effect=AssertionError('supplied scheduler receipt is sufficient')):
            refreshed=ex.refresh(record['id'],poll_receipt={'exit_code':None,'stderr':'fixture disconnect'},automatic=True)
        self.assertEqual(refreshed['tracking_error'],'fixture disconnect')
        self.assertEqual(file_lifecycle.list_assets()[0]['owner_state'],'RUNNING')


class PlotTemporaryTests(unittest.TestCase):
    def test_sweep_preserves_recent_selection_and_final_reports(self):
        from backend.compact_plots import SELECTION_SWEEP
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            stale=root/('selection-'+'a'*64+'.json.gz')
            current=root/('selection-'+'b'*32+'.json.gz')
            other=root/'selection-user-report.json.gz'
            final=root/'data-full.npz'
            for path in (stale,current,other,final):path.touch()
            for path in (stale,other,final):os.utime(path,(time.time()-7200,)*2)
            exec(SELECTION_SWEEP,{'output':current,'time':time,'re':re})
            self.assertFalse(stale.exists())
            self.assertTrue(all(path.exists() for path in (current,other,final)))


class SnapshotCleanupTests(unittest.TestCase):
    def test_collect_only_unpublished_unreferenced_blobs_under_sync_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            req={'snapshot_directory':True,'project_id':'project'}
            pid=root/'project'
            objects=pid/'objects';objects.mkdir(parents=True)
            versions=pid/'versions';versions.mkdir()
            def put(raw,old=True):
                sha=hashlib.sha256(raw).hexdigest()
                path=objects/sha
                path.write_bytes(raw)
                if old:os.utime(path,(time.time()-172800,)*2)
                return sha
            historical=put(b'historical-only bytes')
            current=put(b'current-only bytes')
            orphan=put(b'orphan')
            recent=put(b'recent upload',False)
            def publish(sha,generation):
                manifest=put(json.dumps({'project':{'id':'project'},'files':{'f':{'sha256':sha}}}).encode())
                state={'project_id':'project','version':manifest,'generation':generation,'owner':None}
                (versions/(manifest+'.json')).write_text(json.dumps(state))
                (pid/'state.json').write_text(json.dumps(state))
                return manifest
            old_manifest=publish(historical,1)
            new_manifest=publish(current,2)
            preview=sync_remote.run(root,{**req,'action':'gc-preview'})
            self.assertEqual([v['sha256'] for v in preview['candidates']],[orphan])
            remove={**req,'action':'gc-remove','state':preview['state'],'server':preview['server'],
                    'selected':[orphan],'device':'test','token':'test'}
            held=pid/'operation.json';held.write_text('{}')
            with self.assertRaisesRegex(ValueError,'已有上传'):sync_remote.run(root,remove)
            held.unlink()
            with self.assertRaisesRegex(ValueError,'已发布'):sync_remote.run(root,{**remove,'selected':[historical]})
            self.assertEqual(sync_remote.run(root,remove)['deleted'],[orphan])
            self.assertEqual({f.name for f in objects.iterdir()},{historical,current,recent,old_manifest,new_manifest})
            self.assertFalse(held.exists())
