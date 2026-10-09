import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from backend import analysis_datasets as datasets, recycle, storage


class AnalysisDatasetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name)
        self.stack = ExitStack()
        for module in (datasets, recycle):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.stack.enter_context(patch('backend.execution.ssh', side_effect=AssertionError('No SSH permitted')))
        self.stack.enter_context(patch('backend.extension_runs.stop_local'))
        self.stack.enter_context(patch('backend.problem_records.backfill'))
        self.addCleanup(temporary.cleanup)
        self.addCleanup(self.stack.close)
        self.project = {'id': 'project-test', 'name': '测试材料', 'remoteSlug': 'test',
                        'nodes': [{'id': 'node-test', 'data': {'kind': 'scf', 'label': '极化 noSOC',
                                   'executionMode': 'vasp', 'stageSequence': 1, 'stageSlug': 'scf'}}], 'edges': []}
        store = storage.WorkspaceStore(self.root)
        store.save({'activeId': self.project['id'], 'projects': [self.project]}, 0)
        self.attempt = {'id': 'b' * 32, 'project_id': self.project['id'], 'node_id': 'node-test', 'state': 'COMPLETED'}
        with sqlite3.connect(self.root / 'execution.sqlite3') as connection:
            connection.executescript('CREATE TABLE attempts(id TEXT PRIMARY KEY, body TEXT); CREATE TABLE execution_events(attempt_id TEXT);')
            connection.execute('INSERT INTO attempts VALUES (?,?)', (self.attempt['id'], json.dumps(self.attempt)))
        self.record = {'id': 'a' * 32, 'schema': 'fpilot.analysis.v2', 'project_id': self.project['id'],
                       'extension_id': 'fixture', 'manifest': {'name': '形式极化', 'version': '2.1.0'},
                       'state': 'COMPLETED', 'created_at': '2026-10-09T01:00:00+00:00',
                       'sources': [{'id': self.attempt['id'], 'attempt_id': self.attempt['id'], 'name': 'job123'}],
                       'location': 'local', 'options': {'integer': [0, 1, 0]}, 'scientific_validation': 'INSUFFICIENT',
                       'outputs': [{'path': 'table.csv', 'media_type': 'text/csv', 'available': True, 'label': '表格', 'size_bytes': 10}],
                       'result': {'status': 'COMPLETED', 'summary': '测试结果', 'warnings': ['仅形式极化'], 'tables': [], 'curves': []}}
        self.original = self.write_source('v2', self.record)

    def write_source(self, kind, record):
        dest = datasets.source_path(kind, record['id']).parent
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'analysis.json').write_text(json.dumps(record))
        if kind == 'v2':
            (dest / 'outputs').mkdir(exist_ok=True)
            (dest / 'outputs/result.json').write_text(json.dumps(record['result']))
            (dest / 'outputs/table.csv').write_text('value,unit\n1,eV\n')
        return dest

    def test_manual_catalog_has_names_without_registering_or_running(self):
        rows = datasets.available()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['project_name'], '测试材料')
        self.assertEqual(rows[0]['source_names'], ['极化 noSOC'])
        self.assertIsNone(rows[0]['dataset_id'])
        self.assertEqual(datasets.list_records(), [])
        self.assertFalse((self.root / 'analysis-datasets').exists())

    def test_only_completed_and_nonrecycled_records_are_candidates(self):
        for state in ('FAILED', 'UNKNOWN', 'NOT_APPLICABLE', 'RUNNING'):
            item = copy.deepcopy(self.record)
            item['state'] = state
            (self.original / 'analysis.json').write_text(json.dumps(item))
            self.assertEqual(datasets.available(), [])
            with self.assertRaises(ValueError):
                datasets.register('v2', item['id'])
        (self.original / 'analysis.json').write_text(json.dumps(self.record))
        recycle.move('analyses', self.record['id'])
        self.assertEqual(datasets.available(), [])

    def test_long_default_title_and_legacy_plot_snapshot(self):
        record = {'schema': 'fpilot.local-plot.v1', 'id': 'd' * 32, 'state': 'COMPLETED',
                  'created_at': self.record['created_at'], 'kind': 'bands', 'outputs': ['figure.svg'],
                  'sources': [{'name': '长来源名称' * 150}], 'options': {'reference': 'fermi'}}
        original = self.write_source('plot', record)
        (original / 'figure.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        saved = datasets.register('plot', record['id'])
        self.assertEqual(len(saved['title']), 500)
        self.assertEqual(saved['source_names'], [record['sources'][0]['name']])
        self.assertEqual(saved['analysis']['options'], record['options'])
        self.assertTrue(datasets.output(saved['id'], 'figure.svg')[0].is_file())
        shutil.rmtree(original)
        self.assertTrue(datasets.get(saved['id'])['source_deleted'])

    def test_inconsistent_or_missing_core_result_cannot_be_registered(self):
        result = self.original / 'outputs/result.json'
        result.write_text(json.dumps({**self.record['result'], 'summary': '另一份结果'}))
        with self.assertRaisesRegex(ValueError, '不一致'):
            datasets.register('v2', self.record['id'])
        result.unlink()
        with self.assertRaisesRegex(ValueError, '缺少本机结果'):
            datasets.register('v2', self.record['id'])
        self.assertEqual(datasets.list_records(), [])

    def test_duplicate_and_metadata_edit_preserve_original_and_snapshot(self):
        original = (self.original / 'analysis.json').read_bytes()
        first = datasets.register('v2', self.record['id'], '自定义名称', '备注')
        (self.original / 'outputs/table.csv').write_text('different')
        second = datasets.register('v2', self.record['id'], '不要覆盖', '不要覆盖')
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['title'], '自定义名称')
        self.assertEqual(datasets.output(first['id'], 'table.csv')[0].read_text(), 'value,unit\n1,eV\n')
        edited = datasets.update(first['id'], '更新标题', '新的备注')
        self.assertEqual(edited['analysis'], first['analysis'])
        self.assertEqual(edited['scientific_validation'], 'INSUFFICIENT')
        self.assertEqual((self.original / 'analysis.json').read_bytes(), original)

    def test_concurrent_registration_has_one_snapshot(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: datasets.register('v2', self.record['id']), range(2)))
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual(sorted(result['duplicate'] for result in results), [False, True])
        self.assertEqual(len(datasets.list_records()), 1)

    def test_snapshot_survives_original_analysis_and_attempt_removal(self):
        registered = datasets.register('v2', self.record['id'])
        shutil.rmtree(self.original)
        deleted = recycle.deleted_marker('attempts', self.attempt['id'])
        deleted.parent.mkdir(parents=True, exist_ok=True)
        deleted.write_text('{}')
        self.assertTrue(datasets.get(registered['id'])['source_deleted'])
        self.assertEqual(len(datasets.list_records()), 1)
        self.assertTrue(datasets.output(registered['id'], 'result.json')[0].is_file())

    def test_project_recycling_and_purging_excludes_registered_datasets(self):
        registered = datasets.register('v2', self.record['id'])
        with patch('backend.execution.get', return_value=self.attempt):
            recycle.move('projects', self.project['id'], self.project)
        self.assertFalse(recycle.is_recycled('analysis-datasets', registered['id']))
        self.assertTrue(datasets.get(registered['id'])['source_deleted'])
        recycle.purge('projects', self.project['id'], True)
        self.assertEqual(len(datasets.list_records()), 1)
        self.assertTrue(datasets.output(registered['id'], 'table.csv')[0].exists())

    def test_registered_dataset_recycle_restore_and_explicit_purge(self):
        record = datasets.register('v2', self.record['id'])
        recycle.move('analysis-datasets', record['id'])
        self.assertEqual(datasets.list_records(), [])
        self.assertTrue(datasets.available()[0]['dataset_recycled'])
        recycle.restore('analysis-datasets', record['id'])
        self.assertEqual(len(datasets.list_records()), 1)
        recycle.move('analysis-datasets', record['id'])
        recycle.purge('analysis-datasets', record['id'], True)
        self.assertFalse(datasets.directory(record['id']).exists())
        self.assertTrue(self.original.exists())

    def test_legacy_and_old_polarization_use_distinct_identity_and_local_files(self):
        for kind, schema in [('v1', 'fpilot.analysis-record.v1'), ('local', 'fpilot.local-analysis.v1')]:
            record = {'id': ('c' if kind == 'v1' else 'd') * 32, 'schema': schema, 'attempt_id': self.attempt['id'],
                      'state': 'COMPLETED', 'extension': {'name': '空间对称性', 'version': '1.0'}, 'outputs': ['report.json'],
                      'created_at': self.record['created_at'], 'scientific_validation': 'NOT_PERFORMED'}
            dest = self.write_source(kind, record)
            (dest / 'report.json').write_text('{"spacegroup": 225}')
            item = datasets.register(kind, record['id'])
            self.assertEqual(item['project_id'], self.project['id'])
            self.assertEqual(datasets.history_detail(kind, record['id'])['source_names'], ['极化 noSOC'])
            self.assertEqual(datasets.output(item['id'], 'report.json')[1], 'application/json')
            self.assertTrue(datasets.history_output(kind, record['id'], 'report.json')[0].exists())
        self.assertEqual(len(datasets.list_records()), 2)

    def test_missing_and_remote_outputs_remain_explicit(self):
        (self.original / 'outputs/table.csv').unlink()
        record = copy.deepcopy(self.record)
        record['outputs'].append({'path': 'large.dat', 'media_type': 'text/plain', 'available': False, 'size_bytes': 999999999})
        (self.original / 'analysis.json').write_text(json.dumps(record))
        historical = datasets.history_detail('v2', record['id'])
        self.assertFalse(historical['analysis']['outputs'][0]['available'])
        self.assertIn('缺失', historical['files'][0]['reason'])
        self.assertEqual(json.loads((self.original / 'analysis.json').read_text()), record)
        registered = datasets.register('v2', record['id'])
        self.assertFalse(next(file for file in registered['files'] if file['path'] == 'table.csv')['available'])
        self.assertIn('缺失', next(file for file in registered['files'] if file['path'] == 'table.csv')['reason'])
        with self.assertRaises(ValueError):
            datasets.output(registered['id'], 'large.dat')

    def test_file_escape_symlink_and_corrupted_snapshot_are_rejected(self):
        for name in ('../outside', '/outside', 'dir\\outside'):
            with self.assertRaises(ValueError):
                datasets.inside(self.original, name)
        (self.original / 'outputs/table.csv').unlink()
        (self.original / 'outputs/table.csv').symlink_to(self.original / 'analysis.json')
        with self.assertRaises(ValueError):
            datasets.register('v2', self.record['id'])
        (self.original / 'outputs/table.csv').unlink()
        (self.original / 'outputs/table.csv').write_text('original')
        record = datasets.register('v2', self.record['id'])
        (datasets.directory(record['id']) / 'files/table.csv').write_text('changed')
        with self.assertRaisesRegex(ValueError, '身份'):
            datasets.output(record['id'], 'table.csv')

    def test_registration_rollback_when_source_record_changes(self):
        original_copy = datasets.copy_file
        def copying(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            if args[2] == 'table.csv':
                (self.original / 'analysis.json').write_text(json.dumps({**self.record, 'updated_at': 'changed'}))
            return result
        with patch.object(datasets, 'copy_file', side_effect=copying):
            with self.assertRaisesRegex(ValueError, '变化'):
                datasets.register('v2', self.record['id'])
        self.assertEqual(list((self.root / 'analysis-datasets').glob('*/record.json')), [])

    def test_presentation_and_state_are_independent_and_pin_original_version(self):
        version = 'e' * 32
        dest = self.original / 'presentations' / version
        (dest / 'outputs').mkdir(parents=True)
        (dest / 'outputs/index.html').write_text('<html><head></head><body>result</body></html>')
        (dest / 'outputs/data.json').write_text('{"value": 3}')
        view = {'id': version, 'status': 'READY', 'version': '2.1.0', 'manifest': {'entry_html': 'index.html',
                'assets': [{'path': 'index.html', 'media_type': 'text/html'}, {'path': 'data.json', 'media_type': 'application/json'}]}}
        (self.original / 'presentations/current.json').write_text(json.dumps(view))
        (dest / 'state.json').write_text(json.dumps({'revision': 2, 'value': {'integer': [1, 0, 0]}}))
        registered = datasets.register('v2', self.record['id'])
        datasets.view_state(registered['id'], version, {'integer': [2, 0, 0]})
        self.assertEqual(json.loads((dest / 'state.json').read_text())['value']['integer'], [1, 0, 0])
        shutil.rmtree(self.original)
        self.assertEqual(datasets.view_state(registered['id'], version)['revision'], 3)
        self.assertEqual(datasets.presentation(registered['id'], version)['version'], '2.1.0')
        self.assertTrue(datasets.output(registered['id'], 'presentation/data.json')[0].exists())


if __name__ == '__main__':
    unittest.main()
