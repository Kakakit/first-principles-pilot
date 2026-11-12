import asyncio
import csv
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from backend import artifact_refs, artifacts, batch_manifest, execution as ex
from backend import extension_runs, recycle, result_overview, scans
from backend.extension_protocol import atomic_json


class BatchManifestTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for module in (ex, artifacts, extension_runs, recycle):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.stack.enter_context(patch.object(ex, 'ssh', side_effect=AssertionError('SSH forbidden')))
        self.batch_id = 'b' * 32
        self.current = self.record('a' * 32)
        self.previous = self.record('d' * 32)
        self.failed = self.record('f' * 32, state='FAILED', alias='server-b', case_id='case-b')
        self.batch = {'id': self.batch_id, 'project_id': 'project', 'state': 'COMPLETE',
                      'stage': 'relax', 'reason': '结构优化批次', 'scan_node_id': 'scan',
                      'created_at': '2026-11-10T00:00:00Z', 'case_table_hash': 'frozen-hash',
                      'cases': [
                          {'id': 'case-a', 'index': 5, 'parameters': [{'key': 'ENCUT', 'value': '500'}],
                           'attempt_id': self.current['id'], 'attempt_history': [self.previous['id'], 'e' * 32],
                           'state': 'COMPLETED', 'retry_count': 1},
                          {'id': 'case-b', 'index': 1, 'parameters': [], 'attempt_id': self.failed['id'], 'state': 'FAILED'},
                          {'id': 'case-c', 'index': 2, 'parameters': [], 'attempt_id': None, 'state': 'PENDING'}]}
        scans.save(self.batch)
        self.observe(self.current)
        result_overview.cache(self.current, {'metrics': {'energy': {'value': -10.5, 'unit': 'eV/cell'},
                                                         'ionic_converged': True}})

    def record(self, key, state='COMPLETED', alias='server-a', case_id='case-a'):
        record = {'id': key, 'project_id': 'project', 'node_id': 'scan', 'state': state,
                  'config': {'alias': alias}, 'created_at': '2026-11-10T00:00:00Z',
                  'path_schema': 'fpilot.remote-attempt-path', 'remote_workspace_root': '/data',
                  'remote_workspace_root_abs': '/data',
                  'remote_relative_path': '项目/flows/main/stages/01-relax/attempts/' + key,
                  'remote_path_absolute': '/data/项目/flows/main/stages/01-relax/attempts/' + key,
                  'scan_batch_id': self.batch_id, 'scan_case_id': case_id,
                  'evidence': {'vasp_timing': state == 'COMPLETED', 'ediff_reached': state == 'COMPLETED'}}
        with ex.db() as db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (key, state, json.dumps(record)))
        return record

    def observe(self, record, modified='2026-11-10T12:00:00Z'):
        atomic_json(self.root / 'artifact-cache' / (record['id'] + '.json'),
                    {'source_identity': artifacts._identity(record), 'attempt_id': record['id'],
                     'refreshed_at': modified, 'checked_epoch': time.time(), 'truncated': False,
                     'files': [{'path': 'CONTCAR', 'size_bytes': 140, 'modified_at': modified}]})

    def test_export_keeps_case_numbers_exact_attempts_all_hosts_and_missing_records(self):
        before = scans.get(self.batch_id)
        data = batch_manifest.manifest(self.batch_id, 'project')
        self.assertEqual(scans.get(self.batch_id), before)
        self.assertFalse(data['remote_checked'])
        self.assertEqual([c['case_number'] for c in data['cases']], [6, 2, 3])
        first = data['cases'][0]
        self.assertEqual(first['current_attempt_id'], self.current['id'])
        self.assertEqual(len(first['attempts']), 3)
        self.assertIn('error', first['attempts'][1])
        current = first['attempts'][-1]
        self.assertEqual(current['convergence']['ionic_converged'], True)
        self.assertEqual(current['metrics']['energy']['value'], -10.5)
        self.assertEqual(current['artifacts'][0]['reference']['producer_id'], self.current['id'])
        self.assertEqual(data['cases'][1]['attempts'][0]['host'], 'server-b')
        self.assertEqual(data['cases'][2]['attempts'], [])

    def test_stale_summary_is_not_exported_and_pending_or_failed_rows_are_retained(self):
        changed = self.record(self.current['id'], state='FAILED')
        self.assertIsNone(result_overview.read_cache(changed))
        data = batch_manifest.manifest(self.batch_id, 'project')
        self.assertIsNone(data['cases'][0]['attempts'][-1]['metrics'])
        table = list(csv.DictReader(io.StringIO(batch_manifest.cases_csv(data))))
        self.assertEqual(len(table), 5)
        self.assertEqual(table[-1]['attempt_id'], '')
        self.assertEqual(table[-1]['state'], 'PENDING')
        self.assertEqual(table[2]['state'], 'FAILED')

    def test_zip_contains_one_consistent_snapshot_and_csv_preserves_numeric_energy(self):
        raw, media, _ = batch_manifest.export(self.batch_id, 'project', 'zip')
        self.assertEqual(media, 'application/zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(set(archive.namelist()), {'batch.json', 'cases.csv', 'README.txt'})
            data = json.loads(archive.read('batch.json'))
            table = list(csv.DictReader(io.StringIO(archive.read('cases.csv').decode('utf-8-sig'))))
            self.assertIn(data['collected_at'], archive.read('README.txt').decode())
            self.assertEqual(table[2]['energy_eV_cell'], '-10.5')
            self.assertEqual(table[2]['is_current'], 'True')
        self.batch['cases'][0]['note'] = '=HYPERLINK("bad")'
        scans.save(self.batch)
        self.assertIn("'=HYPERLINK", batch_manifest.cases_csv(batch_manifest.manifest(self.batch_id, 'project')))

    def test_project_and_case_membership_cannot_silently_change(self):
        with self.assertRaisesRegex(ValueError, '当前项目'):
            batch_manifest.manifest(self.batch_id, 'other')
        self.record(self.current['id'], case_id='unrelated-case')
        data = batch_manifest.manifest(self.batch_id, 'project')
        self.assertIn('批次身份', data['cases'][0]['attempts'][-1]['error'])

    def test_reference_is_logical_and_rejects_changed_metadata_or_foreign_project(self):
        item = artifact_refs.catalog('project', 'attempt', self.current['id'])['items'][0]
        ref = item['reference']
        self.assertNotIn('path', ref)
        resolved = artifact_refs.resolve('project', ref)
        self.assertEqual(resolved['locations'][0]['path'], self.current['remote_path_absolute'] + '/CONTCAR')
        self.assertFalse(resolved['remote_checked'])
        with self.assertRaisesRegex(ValueError, '当前项目'):
            artifact_refs.resolve('other', ref)
        invalid = {**ref, 'artifact_id': '../CONTCAR'}
        with self.assertRaises(ValueError):
            artifact_refs.resolve('project', invalid)
        self.observe(self.current, '2026-11-11T12:00:00Z')
        with self.assertRaisesRegex(ValueError, '发生变化'):
            artifact_refs.resolve('project', ref)

    def test_reference_never_follows_an_upstream_retry(self):
        ref = artifact_refs.catalog('project', 'attempt', self.current['id'])['items'][0]['reference']
        newest = self.record('9' * 32)
        self.batch['cases'][0]['attempt_id'] = newest['id']
        scans.save(self.batch)
        self.assertEqual(artifact_refs.resolve('project', ref)['reference']['producer_id'], self.current['id'])

    def test_recycled_and_cleaned_sources_do_not_advertise_available_artifacts(self):
        ref = artifact_refs.catalog('project', 'attempt', self.current['id'])['items'][0]['reference']
        for target in ('backend.artifact_refs.recycle.inactive', 'backend.artifact_refs.file_lifecycle.remote_is_cleaned'):
            with patch(target, return_value=True):
                self.assertEqual(artifact_refs.catalog('project', 'attempt', self.current['id'])['items'], [])
                with self.assertRaisesRegex(ValueError, '可解析'):
                    artifact_refs.resolve('project', ref)

    def test_remote_large_analysis_outputs_are_listed_without_local_download(self):
        key = '2' * 32
        record = {'id': key, 'project_id': 'project', 'state': 'COMPLETED', 'location': 'ssh',
                  'remote_dir': '/data/project/analyses/' + key, 'runtime': {'alias': 'server-a'},
                  'outputs': [{'path': 'large.bin', 'label': '原始数据', 'size_bytes': 30 * 1024**2,
                               'available': False}],
                  'remote_outputs': {'large.bin': {'size_bytes': 30 * 1024**2}}}
        with patch.object(extension_runs, 'get', return_value=record):
            item = artifact_refs.catalog('project', 'analysis', key)['items'][0]
            self.assertEqual(item['locations'][0]['kind'], 'remote')
            self.assertIsNone(item['reference']['version']['modified_at'])
            self.assertTrue(item['requires_verification'])
            self.assertEqual(artifact_refs.resolve('project', item['reference'])['reference'], item['reference'])

    def test_local_analysis_version_and_nested_paths(self):
        key = '3' * 32
        output = extension_runs.directory(key) / 'outputs' / 'nested' / 'report.json'
        output.parent.mkdir(parents=True)
        output.write_bytes(b'{}')
        record = {'id': key, 'project_id': 'project', 'state': 'COMPLETED', 'location': 'local',
                  'outputs': [{'path': 'nested/report.json', 'size_bytes': 2, 'available': True}]}
        with patch.object(extension_runs, 'get', return_value=record):
            ref = artifact_refs.catalog('project', 'analysis', key)['items'][0]['reference']
            self.assertEqual(artifact_refs.resolve('project', ref)['locations'][0]['path'], str(output))
            os.utime(output, ns=(output.stat().st_atime_ns, output.stat().st_mtime_ns + 1_000_000))
            with self.assertRaisesRegex(ValueError, '发生变化'):
                artifact_refs.resolve('project', ref)
            output.write_bytes(b'{ }')
            with self.assertRaisesRegex(ValueError, '可解析'):
                artifact_refs.resolve('project', ref)

    def test_source_case_is_resolved_from_its_exact_attempt_and_removed_files_stay_absent(self):
        record = {**self.current, 'source_contract': {'roles': {'structure': {
            'attempt_id': self.failed['id'], 'file': 'CONTCAR'}}}}
        with ex.db() as db:
            db.execute('UPDATE attempts SET body=? WHERE id=?', (json.dumps(record), record['id']))
        data = batch_manifest.manifest(self.batch_id, 'project')
        source = data['cases'][0]['attempts'][-1]['sources'][0]
        self.assertEqual(source['case_id'], 'case-b')
        ref = artifact_refs.catalog('project', 'attempt', record['id'])['items'][0]['reference']
        (self.root / 'artifact-cache' / (record['id'] + '.json')).unlink()
        with self.assertRaisesRegex(ValueError, '可解析'):
            artifact_refs.resolve('project', ref)

    def test_wannier_reused_case_keeps_original_producer_and_its_convergence(self):
        record = {**self.current, 'kind': 'wannier90', 'bundle_id': '0' * 64,
                  'evidence': {'wannier_all_done': True, 'disentanglement_converged': False}}
        with ex.db() as db:
            db.execute('UPDATE attempts SET body=? WHERE id=?', (json.dumps(record), record['id']))
        identity = scans.digest({'attempt': record['id'], 'bundle': record['bundle_id'],
                                 'updated': record.get('updated_at'), 'evidence': record['evidence']})
        atomic_json(self.root / 'window-summaries' / record['id'] / (identity + '.json'), {'spread': 8.5})
        self.batch['cases'] = [{'id': 'new-case', 'index': 0, 'attempt_id': record['id'], 'state': 'COMPLETED',
                               'reuse': {'attempt_id': record['id'], 'case_id': 'case-a', 'batch_id': self.batch_id}}]
        scans.save(self.batch)
        attempt = batch_manifest.manifest(self.batch_id, 'project')['cases'][0]['attempts'][0]
        self.assertTrue(attempt['convergence']['program_done'])
        self.assertFalse(attempt['convergence']['disentanglement_converged'])
        self.assertEqual(attempt['metrics']['spread'], 8.5)

    def test_window_export_preserves_context_for_pending_completed_and_reused_cases(self):
        source = {'alias': 'server-a', 'seed': 'wannier90.1', 'parent_attempt_id': self.failed['id'],
                  'spinors': False, 'energy_reference': {'fermi_eV': 5.0, 'outcar_sha256': '7' * 64}}
        relative = {'dis_win_min': -8, 'dis_win_max': 4, 'dis_froz_min': -4, 'dis_froz_max': 2}
        absolute = {'dis_win_min': -3, 'dis_win_max': 9, 'dis_froz_min': 1, 'dis_froz_max': 7}
        self.batch.update(stage='wannier90', mode='wannier90-window', source=source, source_frozen_id='8' * 64)
        for original in (self.current, self.previous):
            record = {**original, 'kind': 'wannier90', 'bundle_id': '0' * 64,
                      'evidence': {'wannier_all_done': True}}
            if original['id'] == self.previous['id']:
                record['scan_batch_id'] = '6' * 32
            with ex.db() as db:
                db.execute('UPDATE attempts SET body=? WHERE id=?', (json.dumps(record), record['id']))
        settings = {'bundle_id': '0' * 64, 'relative_windows': relative, 'absolute_windows': absolute,
                    'parameters': [{'kind': 'window', 'key': 'dis_win_min', 'value': '-8'}]}
        reuse = {'attempt_id': self.previous['id'], 'batch_id': '6' * 32, 'case_id': 'case-a'}
        self.batch['cases'] = [
            {**settings, 'id': 'window-pending', 'index': 0, 'state': 'PENDING', 'attempt_id': None},
            {**settings, 'id': 'case-a', 'index': 1, 'state': 'COMPLETED', 'attempt_id': self.current['id']},
            {**settings, 'id': 'window-reused', 'index': 2, 'state': 'COMPLETED',
             'attempt_id': self.previous['id'], 'reuse': reuse}]
        scans.save(self.batch)
        before = scans.get(self.batch_id)
        raw, _, _ = batch_manifest.export(self.batch_id, 'project', 'zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            data = json.loads(archive.read('batch.json'))
            rows = list(csv.DictReader(io.StringIO(archive.read('cases.csv').decode('utf-8-sig'))))
        self.assertEqual(data['source'], source)
        self.assertEqual(data['source_frozen_id'], '8' * 64)
        self.assertEqual(len(rows), 3)
        for case, row in zip(data['cases'], rows):
            with self.subTest(state=case['state'], reused=case['reused_from'] is not None):
                self.assertEqual(case['relative_windows'], relative)
                self.assertEqual(case['absolute_windows'], absolute)
                self.assertEqual(json.loads(row['relative_windows']), relative)
                self.assertEqual(json.loads(row['absolute_windows']), absolute)
                self.assertEqual(json.loads(row['source']), source)
                self.assertEqual(row['source_frozen_id'], '8' * 64)
        self.assertEqual(data['cases'][2]['reused_from'], reuse)
        self.assertEqual(data['cases'][2]['attempts'][0]['attempt_id'], self.previous['id'])
        self.assertEqual(scans.get(self.batch_id), before)

    def test_derived_pending_case_exports_parent_separately_from_reuse(self):
        self.batch['cases'].append({'id': 'derived', 'index': 6, 'state': 'PENDING', 'attempt_id': None,
                                    'parameters': [{'key': 'ENCUT', 'value': '520'}],
                                    'derived_from_case_id': 'case-a'})
        scans.save(self.batch)
        data = batch_manifest.manifest(self.batch_id, 'project')
        derived = data['cases'][-1]
        self.assertEqual(derived['derived_from_case_id'], 'case-a')
        self.assertEqual(derived['derived_from_batch_id'], self.batch_id)
        self.assertIsNone(derived['reused_from'])
        row = list(csv.DictReader(io.StringIO(batch_manifest.cases_csv(data))))[-1]
        self.assertEqual(row['derived_from_case_id'], 'case-a')
        self.assertEqual(row['derived_from_batch_id'], self.batch_id)
        self.assertEqual(row['attempt_id'], '')
        self.assertIsNone(data['cases'][0]['derived_from_case_id'])
        self.assertIsNone(data['cases'][0]['derived_from_batch_id'])

    def test_legacy_window_metadata_stays_unknown(self):
        self.batch.update(stage='wannier90', mode='wannier90-window')
        scans.save(self.batch)
        data = batch_manifest.manifest(self.batch_id, 'project')
        self.assertIsNone(data['source'])
        self.assertIsNone(data['source_frozen_id'])
        rows = list(csv.DictReader(io.StringIO(batch_manifest.cases_csv(data))))
        for case in data['cases']:
            self.assertIsNone(case['relative_windows'])
            self.assertIsNone(case['absolute_windows'])
        for row in rows:
            self.assertIsNone(json.loads(row['source']))
            self.assertIsNone(json.loads(row['relative_windows']))
            self.assertIsNone(json.loads(row['absolute_windows']))
            self.assertEqual(row['source_frozen_id'], '')
            self.assertEqual(row['derived_from_case_id'], '')
            self.assertEqual(row['derived_from_batch_id'], '')

    def test_http_entry_points_export_and_resolve(self):
        from backend.app import app

        async def request(path, params=None, payload=None):
            body = json.dumps(payload).encode() if payload is not None else b''
            sent = []

            async def receive():
                return {'type': 'http.request', 'body': body, 'more_body': False}

            async def send(message):
                sent.append(message)

            await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                       'method': 'POST' if payload is not None else 'GET', 'scheme': 'http',
                       'path': path, 'query_string': urlencode(params or {}).encode(),
                       'headers': [(b'content-type', b'application/json')],
                       'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}, receive, send)
            return sent[0]['status'], b''.join(m.get('body', b'') for m in sent[1:])

        status, raw = asyncio.run(request('/api/scans/' + self.batch_id + '/manifest',
                                         {'project_id': 'project', 'format': 'zip'}))
        self.assertEqual(status, 200, raw)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(raw)))
        status, raw = asyncio.run(request('/api/artifact-refs',
                                         {'project_id': 'project', 'producer_kind': 'attempt',
                                          'producer_id': self.current['id']}))
        self.assertEqual(status, 200, raw)
        ref = json.loads(raw)['items'][0]['reference']
        status, raw = asyncio.run(request('/api/artifact-refs/resolve',
                                         payload={'project_id': 'project', 'reference': ref}))
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['reference'], ref)
        status, _ = asyncio.run(request('/api/scans/' + self.batch_id + '/manifest',
                                       {'project_id': 'other'}))
        self.assertEqual(status, 422)


if __name__ == '__main__':
    unittest.main()
