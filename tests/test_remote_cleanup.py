"""Permanent remote deletion requires an exact, terminal, previewed attempt."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, remote_cleanup


class RemoteCleanupTest(unittest.TestCase):
    def test_preview_is_read_only_and_exact_confirmation_deletes_only_one_attempt(self):
        key = 'a' * 32
        root = '$HOME/230269265lht'
        relative = 'material-projects/test/flows/main/stages/03-scf/attempts/20260927-120000-aaaa'
        absolute = '/home/user/230269265lht/' + relative
        record = {'id': key, 'state': 'FAILED', 'project_id': 'project', 'kind': 'vasp',
                  'path_schema': 'fpilot.remote-attempt-path', 'remote_workspace_root': root,
                  'remote_workspace_root_abs': '/home/user/230269265lht',
                  'remote_relative_path': relative, 'remote_path_absolute': absolute,
                  'attempt_segment': '20260927-120000-aaaa', 'job_id': '123.node01',
                  'config': {'alias': 'jutai-1', 'scheduler': 'pbs'}}
        marker = 'b' * 64
        previews = {'exit_code': 0, 'stdout': f'FPILOT_DELETE_PREVIEW={marker}|4096\n', 'stderr': ''}
        removed = {'exit_code': 0, 'stdout': f'FPILOT_DELETE_DONE={marker}|4096\n', 'stderr': ''}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ex, 'ROOT', Path(temporary)), patch.object(remote_cleanup, 'ROOT', Path(temporary)):
                with ex.db() as connection:
                    connection.execute('INSERT INTO attempts VALUES(?,?,?)', (key, 'FAILED', json.dumps(record)))
                with patch.object(ex, 'ssh', side_effect=[previews, removed]) as ssh:
                    preview = remote_cleanup.preview('project', key)
                    self.assertNotIn('rm -rf', ssh.call_args_list[0].args[1])
                    self.assertIn(absolute, ssh.call_args_list[0].args[1])
                    with self.assertRaisesRegex(ValueError, '远端路径或 attempt 目录名'):
                        remote_cleanup.remove('project', key, preview['token'], 'wrong')
                    result = remote_cleanup.remove('project', key, preview['token'], preview['confirm_text'])
                self.assertTrue(result['deleted'])
                self.assertEqual(ssh.call_count, 2)
                self.assertIn('rm -rf -- "$dest"', ssh.call_args_list[1].args[1])
                self.assertIn('test "$(cd "$dest" && pwd -P)"', ssh.call_args_list[1].args[1])
                with self.assertRaisesRegex(ValueError, '不存在'):
                    ex.get(key)
                self.assertTrue((Path(temporary) / 'deleted-attempts' / (key + '.json')).exists())

    def test_nonterminal_and_unmaterialized_child_block_deletion(self):
        key = 'a' * 32
        with patch.object(ex, 'get', return_value={'id':key,'project_id':'project','state':'RUNNING'}):
            with self.assertRaisesRegex(ValueError, '仍显示此任务在途'):
                remote_cleanup.preview('project',key)
        child = {'id':'c'*32,'state':'PREPARED','dependency_snapshot':{'upstream_attempt_id':key}}
        with patch.object(ex, 'db') as database:
            connection = database.return_value
            connection.execute.return_value = [(json.dumps(child),)]
            self.assertEqual(remote_cleanup._pending_children(key), [child['id']])

    def test_prepared_removes_only_local_record_after_absence_check(self):
        key = 'd' * 32
        relative = 'material-projects/test/flows/main/stages/14-wannier90/attempts/20260927-120000-dddd'
        record = {'id': key, 'state': 'PREPARED', 'project_id': 'project', 'kind': 'wannier90',
                  'path_schema': 'fpilot.remote-attempt-path', 'remote_workspace_root': '$HOME/230269265lht',
                  'remote_workspace_root_abs': '/home/user/230269265lht', 'remote_relative_path': relative,
                  'remote_path_absolute': '/home/user/230269265lht/' + relative,
                  'attempt_segment': '20260927-120000-dddd', 'config': {'alias': 'jutai-1', 'scheduler': 'pbs'}}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ex, 'ROOT', Path(temporary)), patch.object(remote_cleanup, 'ROOT', Path(temporary)):
                with ex.db() as connection:
                    connection.execute('INSERT INTO attempts VALUES(?,?,?)', (key, 'PREPARED', json.dumps(record)))
                preview = {'exit_code': 0, 'stdout': 'FPILOT_DELETE_PREVIEW=' + '0' * 64 + '|0\n', 'stderr': ''}
                done = {'exit_code': 0, 'stdout': 'FPILOT_DELETE_DONE=' + '0' * 64 + '|0\n', 'stderr': ''}
                with patch.object(ex, 'ssh', side_effect=[preview, done]) as ssh:
                    frozen = remote_cleanup.preview('project', key)
                    self.assertIn('test ! -e "$dest"', ssh.call_args.args[1])
                    remote_cleanup.remove('project', key, frozen['token'], frozen['confirm_text'])
                    self.assertNotIn('rm -rf', ssh.call_args.args[1])
                with self.assertRaisesRegex(ValueError, '不存在'):
                    ex.get(key)

    def test_unknown_requires_full_path_and_allows_missing_marker_only_in_force_mode(self):
        key = 'e' * 32
        record = {'id': key, 'state': 'UNKNOWN', 'project_id': 'project', 'kind': 'vasp',
                  'path_schema': 'fpilot.remote-attempt-path', 'remote_workspace_root': '$HOME/230269265lht',
                  'remote_workspace_root_abs': '/home/user/230269265lht',
                  'remote_relative_path': 'material-projects/test/flows/main/stages/03-scf/attempts/20260927-120000-eeee',
                  'remote_path_absolute': '/home/user/230269265lht/material-projects/test/flows/main/stages/03-scf/attempts/20260927-120000-eeee',
                  'attempt_segment': '20260927-120000-eeee', 'config': {'alias': 'jutai-1', 'scheduler': 'pbs'}}
        script = remote_cleanup._script(record, force=True)
        self.assertIn('find "$dest" -maxdepth 2 -type f', script)
        self.assertNotIn('test -s "$dest/_fpilot/attempt.json"\ngrep', script)
        self.assertIn('test -s "$dest/_fpilot/attempt.json"', remote_cleanup._script(record, force=False))


if __name__ == '__main__':
    unittest.main()
