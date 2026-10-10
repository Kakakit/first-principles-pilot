import copy
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from backend import project_snapshot as snap, project_sync as sync, project_sync_remote as protocol
from backend.storage import WorkspaceStore
from backend.attempt_operations import serialized_submit


def project(pid='project-a'):
    return {'id': pid, 'name': '跨设备测试', 'remoteSlug': pid, 'nodes': [], 'edges': [], 'updatedAt': '2026-10-10'}


def initialize(root, pid='project-a', rich=True):
    p = project(pid)
    WorkspaceStore(root).save({'projects': [p, project('other-project')], 'activeId': pid}, 0)
    with sqlite3.connect(root / 'execution.sqlite3') as db:
        for table, definition in snap.TABLES.items():
            db.execute('CREATE TABLE ' + table + '(' + definition + ')')
        for key, owner, state in [('a' * 32, pid, 'UNKNOWN'), ('b' * 32, 'other-project', 'RUNNING')]:
            body = {'id': key, 'project_id': owner, 'state': state, 'job_id': '42.node01', 'config': {'alias': 'school'}, 'bundle_id': 'c' * 64,
                    'remote_path': '/server/material-projects/' + owner, 'authorization': {'authorized': True}, 'local_asset': str(root / 'analyses-v2' / ('d' * 32) / 'outputs' / 'data.csv')}
            db.execute('INSERT INTO attempts VALUES(?,?,?)', (key, state, json.dumps(body)))
        db.execute('INSERT INTO execution_events(attempt_id,at,event,details) VALUES(?,?,?,?)', ('a' * 32, 'now', 'UNKNOWN', '{}'))
        for table, state in [('chain_runs', 'ACTIVE'), ('scan_batches', 'PAUSED')]:
            body = {'id': table, 'project_id': pid, 'state': state, 'authorization': {'approved': True}}
            db.execute('INSERT INTO ' + table + ' VALUES(?,?,?,?)', (table, pid, state, json.dumps(body)))
    if not rich:return
    (root / 'inputs').mkdir()
    with zipfile.ZipFile(root / 'inputs' / ('c' * 64 + '.zip'), 'w') as archive:
        archive.writestr('project.json', json.dumps(p));archive.writestr('INCAR', 'NCORE=1')
    directory = root / 'analyses-v2' / ('d' * 32)
    (directory / 'outputs').mkdir(parents=True)
    (directory / 'outputs' / 'data.csv').write_text('x,y\n1,2')
    (directory / 'analysis.json').write_text(json.dumps({'id': 'd' * 32, 'project_id': pid, 'state': 'COMPLETED', 'asset': str(directory / 'outputs' / 'data.csv')}))
    (root / 'failure-notes').mkdir()
    (root / 'failure-notes' / ('a' * 32 + '.json')).write_text('{"cause":"保留原备注"}')
    (root / 'artifact-cache').mkdir();(root / 'artifact-cache' / 'CHGCAR').write_bytes(b'large download cache')
    (root / 'site-registry.json').write_text('{"secret":"local configuration"}')


class ProjectSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.a, self.b, self.remote_root = base / 'work', base / 'home', base / 'server'
        self.a.mkdir();self.b.mkdir();self.remote_root.mkdir()
        initialize(self.a)
        self.config = {'alias': 'school', 'root': str(self.remote_root)}
        self.origin = patch.object(sync.ex, 'ROOT', self.a);self.origin.start()
        self.registration = patch.object(sync, 'registered', return_value=self.config.copy());self.registration.start()
        self.calls = []

        def remote(config, action, pid=None, data=b'', **values):
            self.calls.append(action)
            return {'ok': True, **protocol.run(self.remote_root, {'action': action, 'project_id': pid, **values}, data)}
        self.transport = patch.object(sync, 'remote', side_effect=remote);self.transport.start()

    def tearDown(self):
        self.transport.stop();self.registration.stop();self.origin.stop();self.temp.cleanup()

    def upload(self):
        preview = sync.preview('project-a', 'school', 'upload')
        return sync.transfer('project-a', preview['preview_id'])

    def home(self):
        sync.ex.ROOT = self.b

    def download(self, choose=True):
        p = sync.preview('project-a', 'school', 'download')
        return sync.transfer('project-a', p['preview_id'], choose)

    def test_snapshot_scopes_and_excludes_cache_credentials(self):
        manifest, objects = snap.capture(self.a, 'project-a')
        self.assertEqual(len(manifest['tables']['attempts']), 1)
        self.assertEqual(manifest['counts']['chains'], 1)
        self.assertFalse(any('artifact-cache' in p or 'site-registry' in p for p in manifest['files']))
        self.assertTrue(any(p.endswith('.zip') for p in manifest['files']))
        snap.validate(manifest, objects)

    def test_round_trip_identity_paths_authorizations_and_repeat_download(self):
        result = self.upload();self.assertEqual(result['local']['mode'], 'OBSERVER')
        self.home();r = self.download();self.assertEqual(r['local']['mode'], 'ACTIVE')
        manifest, _ = snap.capture(self.b, 'project-a')
        attempt = json.loads(manifest['tables']['attempts'][0]['body'])
        self.assertEqual(attempt['id'], 'a' * 32);self.assertEqual(attempt['state'], 'UNKNOWN')
        self.assertEqual(attempt['remote_path'], '/server/material-projects/project-a')
        self.assertEqual(attempt['authorization'], {'authorized': True})
        self.assertTrue(attempt['local_asset'].startswith(str(self.b)))
        self.assertEqual(json.loads(manifest['tables']['scan_batches'][0]['body'])['state'], 'PAUSED')
        self.assertFalse(sync.status('project-a')['pending'])
        self.download(False)
        self.assertEqual(len(snap.read_tables(self.b, 'execution.sqlite3')['execution_events']), 1)

    def test_observer_dispatch_and_two_online_workers(self):
        self.upload()
        @serialized_submit
        def submit(key):raise AssertionError('must not submit')
        record = submit('a' * 32)
        self.assertEqual(record['state'], 'UNKNOWN')
        self.home();self.download()
        with sync.dispatch_guard({'project_id': 'project-a'}):
            with self.assertRaisesRegex(ValueError, '尚未完成'):
                protocol.run(self.remote_root, {'action': 'acquire', 'project_id': 'project-a', 'device': 'other-device',
                    'purpose': 'download', 'token': 'different', 'generation': 2, 'version': sync.local('project-a')['version']})
        self.assertEqual(sync.local('project-a')['mode'], 'ACTIVE')

    def test_unsynced_changes_require_explicit_choice_and_backup(self):
        self.upload();self.home();self.download()
        store = WorkspaceStore(self.b);w = store.read();w['workspace']['projects'][0]['name'] = '本机新备注'
        store.save(w['workspace'], w['revision'])
        p = sync.preview('project-a', 'school', 'download')
        with self.assertRaisesRegex(ValueError, '明确选择'):
            sync.transfer('project-a', p['preview_id'])
        result = sync.transfer('project-a', p['preview_id'], True)
        backup = json.loads((Path(result['backup']) / 'manifest.json').read_text())
        self.assertEqual(backup['project']['name'], '本机新备注')

    def test_unrelated_project_and_job_remain(self):
        self.upload();self.home();initialize(self.b, rich=False)
        before = snap.read_tables(self.b, 'execution.sqlite3')['attempts'][1]
        self.download()
        after = next(r for r in snap.read_tables(self.b, 'execution.sqlite3')['attempts'] if r['id'] == 'b' * 32)
        self.assertEqual(before, after)
        self.assertEqual(len(WorkspaceStore(self.b).read()['workspace']['projects']), 2)

    def test_reused_objects_and_stale_upload(self):
        first = self.upload();self.home();self.download()
        self.calls.clear();self.upload()
        self.assertLess(self.calls.count('put'), 4)
        sync.ex.ROOT = self.a
        with self.assertRaisesRegex(ValueError, '最新版本'):
            sync.preview('project-a', 'school', 'upload')
        self.assertTrue((self.remote_root / '.fpilot/project-sync/project-a/versions' / (first['state']['version'] + '.json')).exists())

    def test_corrupt_object_stops_restore_and_claim(self):
        r = self.upload();self.home()
        p = sync.preview('project-a', 'school', 'download')
        manifest = json.loads((self.remote_root / '.fpilot/project-sync/project-a/objects' / r['state']['version']).read_bytes())
        sha = next(iter(manifest['files'].values()))['sha256']
        (self.remote_root / '.fpilot/project-sync/project-a/objects' / sha).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, '身份校验'):
            sync.transfer('project-a', p['preview_id'])
        self.assertEqual(sync.local('project-a')['mode'], 'STOPPED')
        self.assertNotIn('claim', self.calls)

    def test_upload_receipt_lost_recovers_without_republish(self):
        original = sync.remote.side_effect
        def lose(config, action, *args, **kwargs):
            result = original(config, action, *args, **kwargs)
            if action == 'publish':raise sync.SyncStopped('断线')
            return result
        sync.remote.side_effect = lose
        with self.assertRaisesRegex(ValueError, '停止派发'):self.upload()
        sync.remote.side_effect = original;self.calls.clear()
        result = sync.recover('project-a')
        self.assertEqual(result['local']['mode'], 'OBSERVER')
        self.assertEqual(self.calls.count('publish'), 0)

    def test_claim_receipt_lost_recovers_without_reclaim(self):
        self.upload();self.home();original = sync.remote.side_effect
        def lose(config, action, *args, **kwargs):
            result = original(config, action, *args, **kwargs)
            if action == 'claim':raise sync.SyncStopped('断线')
            return result
        sync.remote.side_effect = lose
        with self.assertRaisesRegex(ValueError, '停止派发'):self.download()
        sync.remote.side_effect = original;self.calls.clear()
        result = sync.recover('project-a')
        self.assertEqual(result['local']['mode'], 'ACTIVE');self.assertNotIn('claim', self.calls)

    def test_preview_is_invalid_after_draft_changes(self):
        p = sync.preview('project-a', 'school', 'upload')
        w = WorkspaceStore(self.a).read();w['workspace']['projects'][0]['name'] = 'changed'
        WorkspaceStore(self.a).save(w['workspace'], w['revision'])
        with self.assertRaisesRegex(ValueError, '草稿已变化'):
            sync.transfer('project-a', p['preview_id'])

    def test_symlink_and_traversal_rejected(self):
        for relative in ('../secret', '/tmp/secret', 'inputs/../secret', 'site-registry.json'):
            with self.assertRaises(ValueError):snap.path_at(self.a, relative)
        (self.a / 'inputs' / 'link').symlink_to(self.a / 'site-registry.json')
        with self.assertRaises(ValueError):snap.path_at(self.a, 'inputs/link')

    def test_foreign_attempt_injection_rejected(self):
        manifest, objects = snap.capture(self.a, 'project-a')
        row = manifest['tables']['attempts'][0];body = json.loads(row['body']);body['project_id'] = 'different';row['body'] = json.dumps(body)
        with self.assertRaisesRegex(ValueError, '其他项目'):snap.validate(manifest, objects)

    def test_incomplete_upload_does_not_publish(self):
        original = sync.remote.side_effect
        def lose(config, action, *args, **kwargs):
            if action == 'put':raise sync.SyncStopped('网络断线')
            return original(config, action, *args, **kwargs)
        sync.remote.side_effect = lose
        with self.assertRaises(ValueError):self.upload()
        state = protocol.run(self.remote_root, {'action': 'status', 'project_id': 'project-a'})['state']
        self.assertIsNone(state['version']);self.assertEqual(sync.local('project-a')['mode'], 'STOPPED')
        sync.remote.side_effect = original
        self.assertEqual(sync.recover('project-a')['local']['mode'], 'RETRY_UPLOAD')
        self.upload()

    def test_unknown_refresh_error_is_retained_without_submit(self):
        with patch.object(sync.ex, 'refresh', side_effect=ValueError('远端目录不存在')):
            result = sync.refresh('project-a')
        self.assertEqual(result['records'][0]['state'], 'UNKNOWN')
        self.assertEqual(json.loads(snap.read_tables(self.a, 'execution.sqlite3')['attempts'][0]['body'])['job_id'], '42.node01')

    def test_missing_output_and_raw_dataset_hash_survive(self):
        directory = self.a / 'analyses-v2' / ('d' * 32)
        value = json.loads((directory / 'analysis.json').read_bytes())
        value['outputs'] = [{'path': 'missing.csv', 'available': True}]
        (directory / 'analysis.json').write_bytes(snap.packed(value))
        dataset = self.a / 'analysis-datasets' / ('e' * 64)
        (dataset / 'files').mkdir(parents=True)
        evidence = snap.packed({'project_id': 'project-a', 'original_path': str(directory)})
        (dataset / 'files' / 'analysis.json').write_bytes(evidence)
        (dataset / 'record.json').write_bytes(snap.packed({'project_id': 'project-a', 'files': [{'path': 'analysis.json', 'available': True, 'sha256': snap.digest(evidence)}]}))
        self.upload();self.home();self.download()
        restored = json.loads((self.b / directory.relative_to(self.a) / 'analysis.json').read_bytes())
        self.assertFalse(restored['outputs'][0]['available'])
        copied = self.b / dataset.relative_to(self.a) / 'files' / 'analysis.json'
        self.assertEqual(copied.read_bytes(), evidence)

    def test_completed_analysis_omitted_by_server_is_retained_outside_active_dirs(self):
        self.upload();self.home();self.download()
        directory = self.b / 'analyses-v2' / ('f' * 32)
        directory.mkdir();(directory / 'analysis.json').write_bytes(snap.packed({'project_id': 'project-a'}))
        self.download(True)
        self.assertFalse((directory / 'analysis.json').exists())
        self.assertTrue(list((self.b / 'project-sync' / 'retained-local-files').rglob('analysis.json')))

    def test_device_private_key_path_change_uses_live_server_identity(self):
        self.upload();self.home();self.download()
        self.assertTrue(sync.continuation_connection({'identityfile': '/work/key'}, {'identityfile': '/home/key'}, 'school'))
        old = sync.remote.side_effect
        def changed(config, action, *args, **kwargs):
            result = old(config, action, *args, **kwargs)
            if action == 'status':result['server']['host'] = 'wrong-host'
            return result
        sync.remote.side_effect = changed
        with self.assertRaisesRegex(ValueError, '身份不同'):
            sync.continuation_connection({}, {'identityfile': '/home/key'}, 'school')

    def test_remote_job_identity_mismatch_is_read_only(self):
        relative = 'material-projects/project-a/flows/main/stages/01-scf/attempts/test'
        target = self.remote_root / relative;(target / '_fpilot').mkdir(parents=True)
        (target / '_fpilot/attempt.json').write_text('{"id":"wrong"}')
        with self.assertRaisesRegex(ValueError, '身份不符'):
            protocol.run(self.remote_root, {'action': 'attempt', 'project_id': 'project-a', 'relative': relative, 'attempt_id': 'a' * 32, 'job_id': '42.node01'})

    def test_expired_preview_and_repeated_transfer_are_rejected(self):
        p = sync.preview('project-a', 'school', 'upload')
        path = self.a / 'project-sync/previews' / (p['preview_id'] + '.json')
        value = json.loads(path.read_bytes());value['created_at'] = '2020-01-01T00:00:00+00:00';path.write_bytes(snap.packed(value))
        with self.assertRaisesRegex(ValueError, '已过期'):sync.transfer('project-a', p['preview_id'])
        result = self.upload()
        self.assertEqual(result['local']['mode'], 'OBSERVER')
        with self.assertRaisesRegex(ValueError, '先下载并接管'):sync.preview('project-a', 'school', 'upload')

    def test_old_version_restores_read_only_without_claim_or_pointer_change(self):
        first = self.upload();self.home();self.download();second = self.upload()
        self.calls.clear()
        p = sync.preview('project-a', 'school', 'download', first['state']['version'])
        self.assertTrue(p['read_only'])
        result = sync.transfer('project-a', p['preview_id'], True)
        self.assertEqual(result['local']['mode'], 'OBSERVER')
        self.assertNotIn('claim', self.calls)
        self.assertEqual(protocol.run(self.remote_root, {'action':'status','project_id':'project-a'})['state'], second['state'])

    def test_reading_empty_history_does_not_mark_project_modified(self):
        root = self.a / 'empty';root.mkdir()
        WorkspaceStore(root).save({'projects':[project()],'activeId':'project-a'}, 0)
        before = snap.fingerprint(snap.capture(root, 'project-a')[0])
        with sqlite3.connect(root / 'execution.sqlite3') as db:
            for table, definition in snap.TABLES.items():db.execute('CREATE TABLE '+table+'('+definition+')')
        self.assertEqual(before, snap.fingerprint(snap.capture(root, 'project-a')[0]))

    def test_observer_draft_is_read_only_and_other_project_can_be_edited(self):
        self.upload();store=WorkspaceStore(self.a);w=store.read()
        w['workspace']['projects'][0]['name']='stale edit'
        with self.assertRaisesRegex(ValueError,'草稿只读'):store.save(w['workspace'],w['revision'])
        w=store.read();w['workspace']['projects'][1]['name']='another project edit'
        store.save(w['workspace'],w['revision'])

    def test_pending_local_analysis_is_imported_unknown_without_reexecution(self):
        directory=self.a/'analyses-v2'/('d'*32)
        value=json.loads((directory/'analysis.json').read_bytes());value.update(state='QUEUED',location='local')
        (directory/'analysis.json').write_bytes(snap.packed(value))
        self.upload();self.home();self.download()
        restored=json.loads((self.b/directory.relative_to(self.a)/'analysis.json').read_bytes())
        self.assertEqual(restored['state'],'UNKNOWN');self.assertEqual(restored['sync_origin_state'],'QUEUED')

    def test_observer_extension_worker_cannot_start_analysis(self):
        from backend import extension_runs as runs
        self.upload()
        record={'id':'d'*32,'project_id':'project-a','state':'QUEUED','location':'local'}
        with patch.object(runs,'ROOT',self.a),patch.object(runs,'get',return_value=record),patch.object(runs,'inactive',return_value=False),patch.object(runs,'_local') as execute:
            runs._execute('d'*32)
        execute.assert_not_called();self.assertEqual(record['state'],'QUEUED')


if __name__ == '__main__':unittest.main()
