import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend import artifact_delete as delete, artifact_delete_remote as helper, artifacts, execution, recycle
from backend.attempt_operations import locked, serialized_submit


class ArtifactDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.local = self.base / 'local'
        self.workspace = self.base / 'fpilot'
        self.attempt = self.workspace / 'material-projects/test/flows/main/stages/03-scf/attempts/20261010-120000-abcd'
        self.attempt.mkdir(parents=True)
        self.key = 'a' * 32
        self.patches = [patch.object(m, 'ROOT', self.local) for m in (delete, artifacts, execution, recycle)]
        for item in self.patches: item.start()
        self.record = {'id': self.key, 'project_id': 'project', 'node_id': 'scf', 'state': 'COMPLETED',
                       'job_id': '123.node01', 'bundle_id': 'b' * 64, 'config': {'alias': 'fixture', 'scheduler': 'pbs'},
                       'path_schema': 'fpilot.remote-attempt-path', 'remote_workspace_root': str(self.workspace),
                       'remote_workspace_root_abs': str(self.workspace), 'remote_path_absolute': str(self.attempt),
                       'remote_relative_path': str(self.attempt.relative_to(self.workspace)), 'evidence': {'scheduler_state': 'C'}}
        self.save(self.record)
        (self.attempt / '_fpilot').mkdir()
        (self.attempt / '_fpilot/attempt.json').write_text(json.dumps({'id': self.key}))
        (self.attempt / '.job-id').write_text('123.node01\n')
        self.bin = self.base / 'bin'; self.bin.mkdir()
        self.scheduler_state = self.base / 'scheduler-state'; self.scheduler_state.write_text('C')
        qstat = self.bin / 'qstat'
        qstat.write_text('#!/bin/sh\nstate=$(cat '+str(self.scheduler_state)+')\nif [ "$state" = ABSENT ]; then echo "Unknown Job Id" >&2; exit 153; fi\nif [ "$state" = UNAVAILABLE ]; then echo "connection failure" >&2; exit 1; fi\necho "job_state = $state"\n')
        qstat.chmod(0o755)
        self.commands = []
        self.ssh_patch = patch.object(execution, 'ssh', side_effect=self.ssh); self.ssh_patch.start()
        self.python_patch = patch('backend.remote_transport._interpreter', return_value=sys.executable); self.python_patch.start()

    def tearDown(self):
        self.ssh_patch.stop(); self.python_patch.stop()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def save(self, record):
        with execution.db() as connection:
            connection.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (record['id'], record['state'], json.dumps(record)))

    def ssh(self, alias, command, **kwargs):
        self.commands.append(command)
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True,
                                env={**os.environ, 'PATH': str(self.bin)+os.pathsep+os.environ['PATH']})
        return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}

    def preview(self, path='CHGCAR'):
        return delete.preview('project', self.key, path)

    def remove(self, preview):
        return delete.remove('project', self.key, preview['path'], preview['token'], True)

    def test_large_sparse_file_no_download_and_records_survive(self):
        file = self.attempt / 'CHGCAR'
        with file.open('wb') as handle: handle.truncate(3 * 1024**3)
        self.local.joinpath('inputs').mkdir()
        frozen = self.local / 'inputs/frozen.zip'
        with zipfile.ZipFile(frozen, 'w') as archive: archive.writestr('manifest.json', '{}')
        preview = self.preview()
        self.assertEqual(preview['size_bytes'], 3 * 1024**3)
        result = self.remove(preview)
        self.assertEqual(result['result'], 'DELETED')
        self.assertFalse(file.exists())
        self.assertEqual(execution.get(self.key), self.record)
        self.assertTrue(frozen.exists())
        self.assertTrue((self.attempt / '_fpilot/attempt.json').exists())
        self.assertNotIn('sha256sum', '\n'.join(self.commands))
        self.assertNotIn('base64', '\n'.join(self.commands))

    def test_all_file_categories_and_unicode_space_paths(self):
        for name in ('INCAR', 'OUTCAR', 'WAVECAR', '_fpilot/attempt.json', '.job-id', '其他 文件.txt'):
            with self.subTest(name=name):
                file = self.attempt / name
                if not file.exists(): file.write_text('fixture')
                self.assertEqual(self.remove(self.preview(name))['result'], 'DELETED')
                self.assertFalse(file.exists())
        self.assertEqual(execution.get(self.key), self.record)

    def test_active_states_and_remote_activity_block(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        for state in delete.ACTIVE | {'PREPARED'}:
            self.save({**self.record, 'state': state})
            with self.assertRaisesRegex(ValueError, '禁止删除'): self.preview()
        self.save(self.record); self.scheduler_state.write_text('R')
        with self.assertRaisesRegex(ValueError, '在途'): self.preview()
        self.assertTrue((self.attempt / 'CHGCAR').exists())

    def test_unknown_requires_positive_terminal_evidence(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        self.save({**self.record, 'state': 'UNKNOWN', 'evidence': {}})
        self.scheduler_state.write_text('ABSENT')
        with self.assertRaisesRegex(ValueError, '终态证据'): self.preview()
        self.scheduler_state.write_text('C')
        self.assertEqual(self.remove(self.preview())['result'], 'DELETED')

    def test_unavailable_scheduler_cannot_use_cached_terminal(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        self.scheduler_state.write_text('UNAVAILABLE')
        with self.assertRaisesRegex(ValueError, '终态证据'): self.preview()

    def test_traversal_links_directories_and_linked_ancestors(self):
        outside = self.base / 'outside'; outside.mkdir(); (outside / 'file').write_text('keep')
        (self.attempt / 'link').symlink_to(outside / 'file')
        (self.attempt / 'linked').symlink_to(outside)
        for name in ('../outside/file', '/absolute', './CHGCAR', 'x//file', 'link', '_fpilot', 'linked/file'):
            with self.subTest(name=name), self.assertRaises(ValueError): self.preview(name)
        self.assertEqual((outside / 'file').read_text(), 'keep')

    def test_replacement_expiry_binding_and_double_request(self):
        file = self.attempt / 'CHGCAR'; file.write_text('old')
        preview = self.preview()
        file.unlink(); file.write_text('replacement')
        result = self.remove(preview)
        self.assertEqual(result['result'], 'UNKNOWN')
        self.assertEqual(delete.status('project', self.key, preview['token'])['result'], 'CONFLICT')
        self.assertEqual(file.read_text(), 'replacement')
        fresh = self.preview()
        with self.assertRaisesRegex(ValueError, '所选文件'): delete.remove('project', self.key, 'OUTCAR', fresh['token'], True)
        with self.assertRaisesRegex(ValueError, '明确确认'): delete.remove('project', self.key, 'CHGCAR', fresh['token'], False)
        operation = delete._load('project', self.key, fresh['token'])
        operation['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(); delete._save(operation)
        with self.assertRaisesRegex(ValueError, '过期'): self.remove(fresh)
        final = self.preview(); self.assertEqual(self.remove(final)['result'], 'DELETED')
        file.write_text('new same name')
        self.assertEqual(self.remove(final)['result'], 'DELETED')
        self.assertEqual(file.read_text(), 'new same name')

    def test_state_and_references_change_require_new_preview(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        preview = self.preview(); self.save({**self.record, 'state': 'FAILED'})
        with self.assertRaisesRegex(ValueError, '发生变化'): self.remove(preview)
        self.save(self.record)
        self.save({'id': 'c'*32, 'state': 'PREPARED', 'source_contract': {'roles': {'charge': {'attempt_id': self.key, 'file': 'CHGCAR'}}}})
        with self.assertRaisesRegex(ValueError, '引用发生变化'): self.remove(preview)
        new = self.preview(); self.assertEqual(len(new['references']), 1)
        self.assertEqual(self.remove(new)['result'], 'DELETED')
        self.assertEqual(execution.get('c'*32)['state'], 'PREPARED')

    def test_disconnect_after_unlink_and_before_unlink_only_read_recovery(self):
        file = self.attempt / 'CHGCAR'; file.write_text('charge')
        preview = self.preview(); ssh = self.ssh
        def lost(*args, **kwargs):
            ssh(*args, **kwargs)
            return {'exit_code': None, 'stdout': '', 'stderr': 'lost'}
        with patch.object(execution, 'ssh', side_effect=lost):
            self.assertEqual(self.remove(preview)['result'], 'UNKNOWN')
        count = len(self.commands)
        self.assertEqual(self.remove(preview)['result'], 'UNKNOWN')
        self.assertEqual(len(self.commands), count)
        recovered = delete.status('project', self.key, preview['token'])
        self.assertEqual(recovered['result'], 'DELETED')
        self.assertNotIn('note', recovered)
        file.write_text('next')
        fresh = self.preview()
        with patch.object(execution, 'ssh', return_value={'exit_code': None, 'stdout': '', 'stderr': 'lost'}):
            self.assertEqual(self.remove(fresh)['result'], 'UNKNOWN')
        self.assertEqual(delete.status('project', self.key, fresh['token'])['result'], 'NOT_DELETED')
        self.assertTrue(file.exists())

    def test_cache_failure_does_not_change_deletion_success(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        preview = self.preview()
        with patch.object(delete, 'invalidate', side_effect=OSError('cache busy')):
            result = self.remove(preview)
        self.assertEqual(result['result'], 'DELETED'); self.assertIn('cache_error', result)
        self.assertEqual(delete.status('project', self.key, preview['token'])['result'], 'DELETED')

    def test_deleted_frozen_source_fails_materialization_and_scheduler_change_blocks(self):
        from backend import source_contracts
        file = self.attempt / 'CHGCAR'; file.write_text('charge')
        preview = self.preview(); self.scheduler_state.write_text('R')
        self.assertEqual(self.remove(preview)['result'], 'UNKNOWN')
        self.assertTrue(file.exists())
        self.assertEqual(delete.status('project', self.key, preview['token'])['result'], 'NOT_DELETED')
        self.scheduler_state.write_text('C'); self.remove(self.preview())
        contract = {'mode': 'upstream', 'purpose': 'charge', 'verification_mode': 'lightweight',
                    'roles': {'charge': {'attempt_id': self.key, 'alias': 'fixture', 'file': 'CHGCAR',
                                         'source_path': str(self.attempt), 'metadata': {'size_bytes': 6}}}}
        with patch.object(source_contracts, 'eligible', return_value=[]):
            with self.assertRaisesRegex(ValueError, '来源文件'): source_contracts.materialize(contract, 'fixture')
        self.assertEqual(execution.get(self.key), self.record)

    def test_cached_list_preview_and_source_eligibility_invalidated(self):
        (self.attempt / 'CHGCAR').write_text('charge')
        preview = self.preview()
        for base, name, body in [('artifact-cache', self.key, {}), ('artifact-cache/previews', 'view', {'attempt_id': self.key}),
                                 ('remote-cache/wannier-source', 'source', {'identity': {'source_attempt_id': self.key}})]:
            path = self.local / base / (name+'.json'); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(body))
        self.remove(preview)
        self.assertFalse((self.local / 'artifact-cache' / (self.key+'.json')).exists())
        self.assertFalse((self.local / 'artifact-cache/previews/view.json').exists())
        self.assertFalse((self.local / 'remote-cache/wannier-source/source.json').exists())

    def test_hardlinks_and_exact_identity_markers(self):
        file = self.attempt / 'CHGCAR'; file.write_text('charge')
        copy = self.base / 'hardlink'; os.link(file, copy)
        preview = self.preview(); self.assertEqual(preview['link_count'], 2)
        self.assertEqual(self.remove(preview)['result'], 'DELETED'); self.assertEqual(copy.read_text(), 'charge')
        file.write_text('charge'); (self.attempt / '.job-id').write_text('999.node01')
        with self.assertRaisesRegex(ValueError, 'job ID'): self.preview()

    def test_dispatch_and_source_delete_share_cross_process_lock(self):
        @serialized_submit
        def fake_submit(key): return 'submitted'
        self.save({'id': 'c'*32, 'state': 'PREPARED', 'restart_snapshot': {'source_attempt_id': self.key}})
        with locked({self.key}):
            script = 'from backend.attempt_operations import locked\nwith locked({'+repr(self.key)+'}): print("unexpected")'
            result = subprocess.run([sys.executable, '-c', script], env={**os.environ, 'FPILOT_WORKSPACE': str(self.local)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('正在派发', result.stderr)
            self.assertEqual(fake_submit('c'*32), 'submitted')  # Nested owned source locks are reentrant.
        from backend.attempt_operations import AttemptBusy
        with patch('backend.attempt_operations.locked', side_effect=AttemptBusy('busy')):
            waiting = fake_submit('c'*32)
        self.assertEqual(waiting['state'], 'PREPARED')

    def test_slurm_exact_terminal_and_live_precedence(self):
        payload = {'job_id': '123', 'scheduler': 'slurm'}
        with patch.object(helper, 'command', side_effect=[(0, '', ''), (0, '123.batch|COMPLETED|0:0\n', '')]):
            with self.assertRaisesRegex(ValueError, '终态证据'): helper.stopped(payload)
        with patch.object(helper, 'command', side_effect=[(0, '', ''), (0, '123|FAILED|1:0\n', '')]):
            self.assertEqual(helper.stopped(payload), 'FAILED')
        with patch.object(helper, 'command', return_value=(0, 'RUNNING', '')):
            with self.assertRaisesRegex(ValueError, '在途'): helper.stopped({**payload, 'terminal_evidence': 'COMPLETED'})


if __name__ == '__main__': unittest.main()
