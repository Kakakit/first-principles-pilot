import base64
import copy
import json
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from backend import node_editability, worker, storage, remote_cache, job_cache, extension_runs
from backend.file_cache import ByteCache
from backend.structures import parse_structure
from test_node_editability import project


class StabilityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)

    def test_unrelated_broken_archives_do_not_block_new_node_but_known_owner_is_reported(self):
        folder=self.root/'inputs';folder.mkdir();(folder/'bad.zip').write_bytes(b'broken')
        before={'projects':[project()]};after=copy.deepcopy(before)
        after['projects'][0]['nodes'].append({'id':'new','data':{'kind':'scf'}})
        node_editability.assert_changes(self.root,before,after)
        with zipfile.ZipFile(folder/'owned.zip','w') as z:z.writestr('project.json',json.dumps(project()))
        with self.assertRaisesRegex(ValueError,'owned.zip'):node_editability.assert_changes(self.root,before,after)

    def test_host_failure_preserves_other_hosts_and_dispatch_phases(self):
        from backend import remote_health, remote_transport, task_recovery, scans, chain_runs
        with ExitStack() as stack:
            stack.enter_context(patch.object(worker.ex,'ROOT',self.root))
            stack.enter_context(patch.object(remote_health,'due',return_value=['bad','good']))
            recover=stack.enter_context(patch.object(remote_transport,'recover',side_effect=lambda alias: (_ for _ in ()).throw(OSError('offline')) if alias=='bad' else {}))
            ticks=[stack.enter_context(patch.object(module,'tick')) for module in (task_recovery,scans,chain_runs,extension_runs)]
            result=worker.tick()
        self.assertEqual(recover.call_count,2);self.assertEqual(result['errors'][0]['host'],'bad')
        for tick in ticks:tick.assert_called_once()

    def test_unexpected_cycle_error_is_recorded_without_worker_crash(self):
        with patch.object(worker.ex,'ROOT',self.root),patch.object(worker,'interval_seconds',return_value=300),patch.object(worker.signal,'signal'),patch.object(worker,'tick',side_effect=OSError('temporary db error')):
            self.assertEqual(worker.run(once=True),0)
        state=json.loads((self.root/'worker-state.json').read_text())
        self.assertEqual(state['last_cycle']['errors'][0]['batch'],'worker-cycle')

    def test_byte_budget_and_replaced_file_metadata(self):
        cache=ByteCache(6);cache.put('a',b'aaa',3);cache.put('b',b'bbb',3);cache.get('a');cache.put('c',b'ccc',3)
        self.assertIsNone(cache.get('b'));self.assertEqual(cache.size,6)
        cache.put('oversized',b'1234567',7);self.assertIsNone(cache.get('oversized'));self.assertEqual(cache.size,6)
        from backend.file_cache import read_bytes
        file=self.root/'file';file.write_bytes(b'old');self.assertEqual(read_bytes(file),b'old')
        file.write_bytes(b'new-value');self.assertEqual(read_bytes(file),b'new-value')

    def test_storage_reuses_unchanged_structure_and_revalidates_edits(self):
        text=(Path(__file__).parent/'fixtures/Si.POSCAR').read_bytes()
        structure=parse_structure('Si.POSCAR','poscar',base64.b64encode(text).decode())
        workspace={'activeId':'p','projects':[project()]};workspace['projects'][0]['nodes'][0]['data']['structure']=structure
        store=storage.WorkspaceStore(self.root)
        with patch.object(storage,'source_bytes',wraps=storage.source_bytes) as validate:
            receipt=store.save(workspace,0);count=validate.call_count
            edited=copy.deepcopy(workspace);edited['projects'][0]['name']='new name'
            store.save(edited,receipt['revision']);self.assertEqual(validate.call_count,count)
            bad=copy.deepcopy(edited);bad['projects'][0]['nodes'][0]['data']['structure']['source']['base64']=base64.b64encode(b'changed').decode()
            with self.assertRaises(ValueError):store.save(bad,2)
        self.assertEqual(store.read()['workspace'],edited);self.assertIn('base64',structure['source'])

    def test_preview_ignores_status_and_unselected_files_but_tracks_selected_file(self):
        manifest={'inputs':[{'role':'structure','names':['POSCAR']} ]}
        source={'id':'a','kind':'attempt','alias':'server','files':[{'name':'POSCAR','size_bytes':10,'modified_at':'now'}]}
        binding={'sources':[source],'runtime':{'python':'/python'},'options':{},'revision':'r'}
        changed=copy.deepcopy(binding);changed['sources'][0].update(host_state='COOLDOWN',refreshing=True,state='COMPLETED')
        changed['sources'][0]['files'].append({'name':'unselected.log','size_bytes':123})
        with patch.object(extension_runs.p,'mode_manifest',side_effect=lambda m,s:m,create=True):
            self.assertEqual(extension_runs.preview_binding(binding,manifest),extension_runs.preview_binding(changed,manifest))
            changed['sources'][0]['files'][0]['size_bytes']=11
            self.assertNotEqual(extension_runs.preview_binding(binding,manifest),extension_runs.preview_binding(changed,manifest))

    def test_singleflight_releases_locks_only_after_all_waiters(self):
        entered=threading.Event();release=threading.Event();counts=[]
        def first():
            with remote_cache.singleflight('test',['same']):entered.set();release.wait(2);counts.append('first')
        def second():
            with remote_cache.singleflight('test',['same']):counts.append('second')
        a=threading.Thread(target=first);b=threading.Thread(target=second);a.start();self.assertTrue(entered.wait(2));b.start()
        release.set();a.join(2);b.join(2)
        self.assertEqual(counts,['first','second']);self.assertNotIn(remote_cache.key('test',['same']),remote_cache.LOCKS)

    def test_completed_plot_eviction_keeps_receipt_and_active_jobs(self):
        key='a'*64;active='b'*64;now=time.time()
        jobs={key:{'id':key,'state':'FAILED','error':'test failure','finished_at':now-4000},active:{'id':active,'state':'RUNNING'}}
        job_cache.prune(jobs,self.root)
        self.assertEqual(list(jobs),[active]);self.assertEqual(job_cache.read(self.root,key)['error'],'test failure')
