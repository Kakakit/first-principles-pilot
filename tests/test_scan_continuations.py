import copy
import hashlib
import json
import subprocess
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_scans
from backend import artifacts, batch_manifest, execution as ex, inputs, recycle, scans
from backend import scan_continuations as continuation, source_contracts as sources, source_files


class ScanContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_scans.ScanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (artifacts, recycle):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.stack.enter_context(patch.object(ex, 'ssh', side_effect=self.local_ssh))
        self.stack.enter_context(patch.object(source_files, 'snapshot', side_effect=self.snapshot))
        self.stack.enter_context(patch.object(ex, 'submit', side_effect=AssertionError('submission forbidden')))
        self.project = copy.deepcopy(self.fixture.project)
        self.project.update(updatedAt='2026-11-12T00:00:00Z')
        self.project['parameters']['version'] = 1
        for index, node in enumerate(self.project['nodes']):
            node.update(type='step', position={'x': index * 300, 'y': 0})
            node['data'].update(label=node['id'], note='', params={})
        for index, edge in enumerate(self.project['edges']):
            edge['id'] = 'edge-' + str(index)
        base = self.project['nodes'][1]
        base['data'].update(kind='relax', stageSlug='relax', parameters={'preset': 'basic', 'values': {'NSW': '5', 'IBRION': '2', 'EDIFFG': '-0.02'}})
        self.batch = scans.freeze(self.project, 'scan', 'c', [{'kind': 'incar', 'tag': 'ENCUT', 'start': '400', 'end': '500', 'step': '50'}], '优化批次')
        self.records = []
        for index, case in enumerate(self.batch['cases']):
            key = format(index + 1, '032x')
            manifest, files = ex.bundle(case['bundle_id'])
            directory = self.root / 'remote' / key
            directory.mkdir(parents=True)
            for name, raw in files.items():
                if name in ('INCAR', 'KPOINTS', 'POSCAR', 'POTCAR'):
                    (directory / name).write_bytes(raw)
            lines = files['POSCAR'].decode().splitlines()
            lines[1] = str(1 + index * 0.02)
            (directory / 'CONTCAR').write_text('\n'.join(lines) + '\n')
            record = {'id': key, 'state': 'COMPLETED', 'bundle_id': case['bundle_id'], 'project_id': self.project['id'],
                      'node_id': 'c', 'execution_scope': 'scan', 'scan_batch_id': self.batch['id'], 'scan_case_id': case['id'],
                      'flow_id': 'single', 'path_schema': 'fpilot.remote-attempt-path',
                      'remote_workspace_root': str(self.root / 'remote'), 'remote_workspace_root_abs': str(self.root / 'remote'),
                      'remote_relative_path': key, 'remote_path_absolute': str(directory),
                      'config': {'alias': 'test-server', 'scheduler': 'pbs'}, 'job_id': str(index + 100) + '.node01',
                      'created_at': '2026-11-12T00:00:00Z',
                      'evidence': {'vasp_timing': True, 'ediff_reached': True, 'ionic_converged': True, 'wrapper_exit': 0}}
            with ex.db() as database:
                database.execute('INSERT INTO attempts VALUES(?,?,?)', (key, record['state'], json.dumps(record)))
            case.update(attempt_id=key, state='COMPLETED')
            self.records.append(record)
        scans.save(self.batch)

    def local_ssh(self, alias, command, data=None, **kwargs):
        self.assertEqual(alias, 'test-server')
        completed = subprocess.run(['bash', '-c', command], input=data, capture_output=True)
        return {'exit_code': completed.returncode, 'stdout': completed.stdout.decode(), 'stderr': completed.stderr.decode()}

    def snapshot(self, alias, path, names, ssh, mode='lightweight'):
        from pathlib import Path
        result = {}
        for name in names:
            file = Path(path) / name
            if not file.is_file() or file.is_symlink() or not file.stat().st_size:
                raise ValueError('来源文件缺失：' + name)
            stamp = file.stat()
            result[name] = {'size_bytes': stamp.st_size, 'mtime': str(stamp.st_mtime_ns),
                            'verification_mode': 'content', 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
        return result

    def selections(self, indices=(0, 1, 2)):
        return [{'case_id': self.batch['cases'][index]['id'], 'attempt_id': self.records[index]['id']} for index in indices]

    def preview(self, indices=(0, 1, 2), **options):
        return continuation.preview(self.project, self.batch['id'], self.selections(indices), **options)

    def test_three_structures_create_three_scf_inputs_with_individual_frozen_sources(self):
        before = scans.get(self.batch['id'])
        source_inputs = [(self.root / 'inputs' / (case['bundle_id'] + '.zip')).read_bytes() for case in self.batch['cases']]
        preview = self.preview(incar='EDIFF=1e-7\n', grid='6 6 6')
        self.assertTrue(preview['ready'], preview['cases'])
        created = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        batch = scans.get(created['batch']['id'])
        self.assertEqual(batch['state'], 'FROZEN')
        self.assertNotIn('authorization', batch)
        self.assertEqual(len(batch['cases']), 3)
        for index, case in enumerate(batch['cases']):
            manifest, files = ex.bundle(case['bundle_id'], True)
            source = self.records[index]
            self.assertEqual(files['POSCAR'], (self.root / 'remote' / source['id'] / 'CONTCAR').read_bytes())
            self.assertIn(('ENCUT = ' + str(400 + 50 * index)).encode(), files['INCAR'])
            self.assertIn(b'NSW = 0', files['INCAR'])
            self.assertIn(b'IBRION = -1', files['INCAR'])
            self.assertEqual(float(sources.tag(files['INCAR'], 'EDIFF')), 1e-7)
            self.assertIn(b'6 6 6', files['KPOINTS'])
            self.assertEqual(manifest['source_contract']['roles']['structure']['attempt_id'], source['id'])
            self.assertEqual(set(manifest['source_contract']['roles']), {'structure'})
            materialized = sources.materialize(manifest['source_contract'], 'test-server')
            self.assertEqual(materialized['files']['CONTCAR']['sha256'], hashlib.sha256(files['POSCAR']).hexdigest())
            self.assertEqual(case['continued_from']['case_id'], self.batch['cases'][index]['id'])
        self.assertEqual(scans.get(self.batch['id']), before)
        self.assertEqual(source_inputs, [(self.root / 'inputs' / (case['bundle_id'] + '.zip')).read_bytes() for case in self.batch['cases']])
        exported = batch_manifest.manifest(batch['id'], self.project['id'])
        self.assertEqual(exported['cases'][2]['continued_from']['attempt_id'], self.records[2]['id'])

    def test_subset_order_and_retry_after_source_batch_changes_keep_exact_attempts(self):
        preview = self.preview((2, 0))
        self.assertTrue(preview['ready'], preview['cases'])
        original = self.batch['cases'][2]['attempt_id']
        self.batch['cases'][2].update(attempt_id=None, attempt_history=[original])
        self.batch['cases'].reverse()
        self.batch['reason'] = '重命名后的原批次'
        scans.save(self.batch)
        historical = continuation.choices(self.batch['id'], self.project['id'], original)
        self.assertTrue(next(row for row in historical['cases'] if row['attempt_id'] == original)['eligible'])
        result = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        frozen = scans.get(result['batch']['id'])
        self.assertEqual([case['continued_from']['attempt_id'] for case in frozen['cases']], [self.records[2]['id'], self.records[0]['id']])
        again = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        self.assertEqual(result, again)
        self.assertEqual(len([batch for batch in scans.history(self.project['id']) if batch.get('continuation')]), 1)

    def test_source_file_change_requires_another_preview(self):
        preview = self.preview((0,))
        self.assertTrue(preview['ready'], preview['cases'])
        path = self.root / 'remote' / self.records[0]['id'] / 'CONTCAR'
        path.write_text(path.read_text().replace('1.0', '1.03', 1))
        with self.assertRaisesRegex(ValueError, '发生变化'):
            continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        self.assertEqual(len(scans.history(self.project['id'])), 1)

    def test_unconverged_and_missing_files_are_reported_per_case(self):
        record = {**self.records[1], 'evidence': {**self.records[1]['evidence'], 'ionic_converged': False}}
        with ex.db() as database:
            database.execute('UPDATE attempts SET body=? WHERE id=?', (json.dumps(record), record['id']))
        (self.root / 'remote' / self.records[2]['id'] / 'CONTCAR').unlink()
        choices = continuation.choices(self.batch['id'], self.project['id'])
        self.assertFalse(choices['cases'][1]['eligible'])
        preview = self.preview()
        self.assertFalse(preview['ready'])
        self.assertEqual([bool(case['errors']) for case in preview['cases']], [False, True, True])
        self.assertTrue(self.preview((0,))['ready'])

    def test_electronic_inputs_are_optional_and_wrong_project_or_attempt_is_rejected(self):
        self.assertTrue(self.preview((0,))['ready'])
        self.assertFalse(self.preview((0,), files='charge')['ready'])
        with self.assertRaisesRegex(ValueError, '当前项目'):
            continuation.choices(self.batch['id'], 'other-project')
        selection = [{'case_id': self.batch['cases'][0]['id'], 'attempt_id': self.records[1]['id']}]
        preview = continuation.preview(self.project, self.batch['id'], selection)
        self.assertFalse(preview['ready'])
        with self.assertRaisesRegex(ValueError, '重复'):
            continuation.preview(self.project, self.batch['id'], self.selections((0, 0)))

    def test_partial_freeze_failure_can_retry_without_a_partial_batch(self):
        preview = self.preview()
        freeze = inputs.freeze
        calls = []

        def interrupted(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('synthetic freeze interruption')
            return freeze(*args, **kwargs)

        with patch.object(inputs, 'freeze', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        self.assertEqual(len(scans.history(self.project['id'])), 1)
        created = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        self.assertEqual(len(scans.get(created['batch']['id'])['cases']), 3)

    def test_concurrent_create_returns_one_batch(self):
        from concurrent.futures import ThreadPoolExecutor
        preview = self.preview((0,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash']), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len([batch for batch in scans.history(self.project['id']) if batch.get('continuation')]), 1)

    def test_charge_can_be_selected_when_its_geometry_matches_each_case(self):
        for record in self.records:
            directory = self.root / 'remote' / record['id']
            (directory / 'CHGCAR').write_bytes((directory / 'CONTCAR').read_bytes() + b'\n1 1 1\n0.1\n')
        preview = self.preview(files='charge')
        self.assertTrue(preview['ready'], preview['cases'])
        created = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        for case in scans.get(created['batch']['id'])['cases']:
            manifest, files = ex.bundle(case['bundle_id'], True)
            self.assertEqual(set(manifest['source_contract']['roles']), {'structure', 'charge'})
            self.assertEqual(sources.tag(files['INCAR'], 'ICHARG'), '1')

    def test_pending_continuation_registers_exact_source_file_dependencies(self):
        from backend import artifact_delete, file_lifecycle
        preview = self.preview((0,))
        created = continuation.create(self.project['id'], preview['preview_id'], preview['definition_hash'])
        batch = scans.get(created['batch']['id'])
        names, potential = artifact_delete._bound_files(batch, self.records[0]['id'], self.records[0]['remote_path_absolute'])
        self.assertEqual(names, {'CONTCAR'})
        self.assertFalse(potential)
        self.assertTrue(any(batch['id'][:12] in message for message in file_lifecycle._attempt_dependents(self.records[0]['id'])))


if __name__ == '__main__':
    unittest.main()
