import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, input_display as display, task_summary, extension_runs


class InputDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        patch.object(ex, 'ROOT', self.root).start()
        patch.object(extension_runs, 'ROOT', self.root).start()
        patch.object(ex, 'ssh', side_effect=AssertionError('no remote reads')).start()
        patch.object(ex, 'bundle', side_effect=AssertionError('no full bundle verification')).start()
        display._read.cache_clear()
        self.key = 'ab' * 32
        self.row = dict(id='one', bundle_id=self.key, state='UNKNOWN', reason='资源申请理由',
                        input_reason='当前记录不能覆盖冻结备注', config={'alias':'demo','name':'job-demo'})
        (self.root / 'inputs').mkdir()
        self.archive = self.root / 'inputs' / (self.key + '.zip')
        self.freeze('实际冻结输入备注')

    def freeze(self, note):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'reason': note}, ensure_ascii=False))
            archive.writestr('POTCAR', 'must never be read')
            archive.writestr('INCAR', 'must never be parsed for a caption')

    def test_only_frozen_manifest_is_read_and_shared_between_lists(self):
        original = zipfile.ZipFile.read
        names = []
        def tracked(archive, name, *args, **kw):
            names.append(name)
            return original(archive, name, *args, **kw)
        with patch.object(zipfile.ZipFile, 'read', tracked):
            self.assertEqual(task_summary.attempt(self.row)['input_note'], '实际冻结输入备注')
            self.assertEqual(extension_runs._input_note(self.row), '实际冻结输入备注')
            self.assertEqual(task_summary.attempt(dict(self.row, id='two'))['input_note'], '实际冻结输入备注')
        self.assertEqual(names, ['manifest.json'])
        self.assertEqual(self.row['state'], 'UNKNOWN')

    def test_empty_note_never_uses_resource_reason(self):
        self.freeze('')
        self.assertEqual(display.frozen_note(self.row), '')
        self.assertEqual(extension_runs._input_note(self.row), '')

    def test_missing_and_corrupt_input_are_optional_not_execution_errors(self):
        self.archive.write_bytes(b'broken')
        result = task_summary.attempt(self.row)
        self.assertIsNone(result['input_note'])
        self.assertEqual(result['state'], 'UNKNOWN')
        self.archive.unlink()
        self.assertIsNone(display.frozen_note(self.row))

    def test_file_version_and_workspace_invalidate_the_caption(self):
        self.assertEqual(display.frozen_note(self.row), '实际冻结输入备注')
        before = self.archive.stat()
        self.freeze('新的冻结文件版本')
        os.utime(self.archive, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        self.assertEqual(display.frozen_note(self.row), '新的冻结文件版本')
        other = self.root / 'other';(other / 'inputs').mkdir(parents=True)
        with zipfile.ZipFile(other / 'inputs' / (self.key + '.zip'), 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'reason':'另一工作区'}))
        self.assertEqual(display.frozen_note(self.row, other), '另一工作区')

    def test_wannier_caption_comes_from_frozen_win_bundle(self):
        (self.root / 'wannier-bundles').mkdir()
        (self.root / 'wannier-bundles' / (self.key + '.json')).write_text(json.dumps({'input_reason':'冻结能窗与投影', 'win':'unparsed'}))
        self.assertEqual(display.frozen_note(dict(self.row, kind='wannier90')), '冻结能窗与投影')

    def test_page_only_projects_the_requested_rows_and_retains_chain_identity(self):
        rows = [dict(self.row, id=str(i), chain_run_id='chain-one') for i in range(20)]
        with patch.object(ex, 'history', return_value=rows), patch.object(task_summary, 'attempt', wraps=task_summary.attempt) as projection:
            result = task_summary.page('p', 3)
        self.assertEqual(projection.call_count, 3)
        self.assertEqual(result['items'][0]['chain_run_id'], 'chain-one')
        self.assertEqual(result['items'][0]['config']['name'], 'job-demo')
        self.assertEqual(result['next_cursor'], '2')

    def test_scan_caption_does_not_load_or_replace_case_metadata(self):
        with patch.object(task_summary, 'frozen_note', side_effect=AssertionError('scan labels unchanged')):
            result = task_summary.attempt(dict(self.row, scan_batch_id='batch', parameter_summary='U=4'))
        self.assertEqual(result['parameter_summary'], 'U=4')

    def test_caption_cache_stays_bounded(self):
        for i in range(270):
            key = format(i, '064x')
            with zipfile.ZipFile(self.root / 'inputs' / (key + '.zip'), 'w') as archive:
                archive.writestr('manifest.json', json.dumps({'reason':str(i)}))
            self.assertEqual(display.frozen_note(dict(self.row, bundle_id=key)), str(i))
        self.assertLessEqual(display._read.cache_info().currsize, 256)

    def test_detail_for_a_record_outside_the_list_page_keeps_its_frozen_note(self):
        from backend.app import execution_detail
        from fastapi import HTTPException
        row = dict(self.row, project_id='p')
        with patch.object(ex, 'get', return_value=row), patch('backend.recycle.assert_available'):
            result = execution_detail(row['id'], 'p')
            self.assertEqual(result['input_note'], '实际冻结输入备注')
            with self.assertRaises(HTTPException) as caught:
                execution_detail(row['id'], 'another-project')
            self.assertEqual(caught.exception.status_code, 422)
        self.assertNotIn('input_note', row)
