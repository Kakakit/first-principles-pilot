"""Reuse contracts and cache invalidation, with no real remote operations."""
import copy
import os
import unittest
from unittest.mock import patch

from backend import chain_runs as chains, execution as ex, inputs, source_files
import test_chain_runs
import json
from contextlib import closing


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_chain_runs.ChainRunTests()
        self.fixture.setUp()
        self.project = self.fixture.project
        original = self.fixture.frozen()
        self.bundle = original['steps'][0]['bundle_id']
        self.old = {'id': 'old-relax', 'state': 'COMPLETED', 'project_id': 'p',
                    'node_id': 'r', 'bundle_id': self.bundle, 'job_id': '123.node01',
                    'path_schema': 'fpilot.remote-attempt-path',
                    'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'},
                    'evidence': {'wrapper_exit': 0, 'vasp_timing': True, 'output_sanity': True,
                                 'last_delta_e': 1e-8, 'last_delta_eps': 1e-8, 'ediff_value': 1e-6,
                                 'last_electronic_step': 5, 'ionic_converged': True, 'last_ionic_step': 2}}
        self.store(self.old)
        self.receipt = {'exit_code': 0, 'stdout': 'FPILOT_FILE=CONTCAR|'+'a'*64+'|120|2026-09-30 12:00:00.000000000 +0000\n'}
        self.path = patch.object(ex, 'attempt_path', return_value={'shell': '/safe/source'})
        self.remote = patch.object(ex, 'ssh', return_value=self.receipt)
        self.path.start()
        self.ssh = self.remote.start()

    def tearDown(self):
        self.remote.stop()
        self.path.stop()
        self.fixture.tearDown()

    def store(self, record):
        with closing(ex.db()) as db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',
                       (record['id'], record['state'], json.dumps(record)))
            db.commit()
        return record

    def preview(self, choices=None, project=None):
        return chains.preview(project or self.project, ['c', 'd'], 'jutai-1', choices)

    def freeze(self):
        report = self.preview()
        self.assertTrue(report['ready'], report)
        return chains.freeze(self.project, ['c', 'd'], '复用已完成 relax', report['definition_hash'],
                             'jutai-1', report['reuse_sources'])

    def test_unique_converged_source_is_bound_and_excluded_from_budget(self):
        report = self.preview()
        self.assertTrue(report['ready'])
        self.assertEqual(report['reuse_sources'], {'r': 'old-relax'})
        run = self.freeze()
        self.assertEqual(run['max_jobs'], 3)
        self.assertEqual(run['node_ids'], ['c', 'd'])
        self.assertEqual(run['reuse_bindings']['r']['attempt_id'], 'old-relax')
        self.assertNotIn('r', [item['node_id'] for item in run['steps']])

    def test_latest_matching_result_is_default_and_manual_choice_remains(self):
        self.store({**self.old, 'id': 'another-relax', 'created_at':'2099-01-01'})
        report = self.preview()
        self.assertTrue(report['ready'])
        self.assertEqual(report['reuse_sources']['r'],'another-relax')
        self.assertEqual(len(report['sources'][0]['candidates']), 2)
        self.assertTrue(self.preview({'r': 'old-relax'})['ready'])

    def test_historical_input_difference_requires_choice_and_drives_model_comparison(self):
        project = copy.deepcopy(self.project)
        project['nodes'][1]['data']['parameters']['values']['ENCUT'] = '500'
        report = self.preview(project=project)
        self.assertFalse(report['ready'])
        candidate = report['sources'][0]['candidates'][0]
        self.assertEqual(next(item for item in candidate['differences'] if item['field'] == 'ENCUT')['before'], '400')
        report = self.preview({'r': 'old-relax'}, project)
        self.assertTrue(report['ready'])
        self.assertFalse(any(item['field'] == 'ENCUT' for item in report['mismatches']))

    def test_wrong_host_nonconvergence_unknown_and_missing_files_are_explained(self):
        for changed, expected in (({'config': {'alias': 'other'}}, '主机'),
                                  ({'state': 'UNKNOWN'}, '正常结束'),
                                  ({'evidence': {**self.old['evidence'], 'ionic_converged': False}}, '收敛')):
            self.store({**self.old, **changed})
            report = self.preview()
            self.assertFalse(report['ready'])
            self.assertIn(expected, report['sources'][0]['candidates'][0]['reason'])
        self.store(self.old)
        self.ssh.return_value = {'exit_code': 1, 'stdout': ''}
        self.assertIn('普通文件', self.preview()['sources'][0]['candidates'][0]['reason'])

    def test_freeze_rejects_changed_source_metadata(self):
        report = self.preview()
        self.ssh.return_value = {**self.receipt, 'stdout': self.receipt['stdout'].replace('|120|', '|121|')}
        with self.assertRaisesRegex(ValueError, '变化'):
            chains.freeze(self.project, ['c', 'd'], '复用', report['definition_hash'], 'jutai-1', report['reuse_sources'])

    def test_worker_binds_exact_source_and_does_not_submit_reused_relax(self):
        run = self.freeze()
        candidate = {'node': 'node01', 'cores': 48, 'reason': '', 'profile': {'ppn': 48}, 'script_sha256': chains.digest('script')}
        import hashlib
        candidate['script_sha256'] = hashlib.sha256(b'script').hexdigest()
        run.update(state='ACTIVE', authorization={'config': self.old['config'], 'resources': {
            key: {'vasp_executable': 'vasp_std', 'candidates': [candidate]} for key in run['node_ids']}})
        run = chains.save(run)
        def prepare(bundle, config, probe, node, cores, reason, **kwargs):
            record = {'id': kwargs['attempt_id'], 'state': 'PREPARED', 'config': config, 'bundle_id': bundle,
                      'project_id': 'p', 'node_id': 'c', 'chain_source_snapshot': {'source_attempt_id': kwargs['chain_source_id'], 'kind': 'dependency'}}
            return self.store(record)
        def submit(key):
            record = ex.get(key); record['state'] = 'SUBMITTED'; return ex.save(record)
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={'resource_ready': True, 'profile': {'ppn': 48}, 'script': 'script'}), \
             patch.object(chains, '_quota_available', return_value=True), \
             patch.object(ex, 'prepare', side_effect=prepare) as prep, \
             patch.object(ex, 'submit', side_effect=submit) as dispatch:
            chains._tick_one(run['id'])
            chains._tick_one(run['id'])
        self.assertEqual(prep.call_count, 1)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(prep.call_args.kwargs['chain_source_id'], 'old-relax')
        self.assertEqual(ex.get('old-relax')['state'], 'COMPLETED')

    def test_unchanged_run_does_not_bump_revision(self):
        run = self.freeze()
        self.assertEqual(chains.save(run)['revision'], run['revision'])
        stale = copy.deepcopy(run)
        run['state'] = 'PAUSED'; chains.save(run)
        with self.assertRaisesRegex(ValueError, '变化'):
            chains.save(stale)

    def test_batch_resource_preview_reuses_identical_scripts(self):
        run = self.fixture.frozen()
        resources = {key: {'vasp_executable': 'vasp_std', 'candidates': [{'node': 'node01', 'cores': 48}]} for key in run['node_ids']}
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}) as probes, \
             patch.object(chains.servers, 'preview', return_value={'script': 'script', 'resource_ready': True}) as scripts:
            result = chains.resource_preview(run['id'], 'p', self.old['config'], resources)
        self.assertEqual(len(result['previews']), 4)
        self.assertEqual(probes.call_count, 1)
        self.assertEqual(scripts.call_count, 1)

    def test_worker_blocks_changed_frozen_source_before_preparation(self):
        run = self.freeze()
        run.update(state='ACTIVE', authorization={'config': self.old['config'], 'resources': {
            key: {'vasp_executable': 'vasp_std', 'candidates': []} for key in run['node_ids']}})
        run = chains.save(run)
        self.ssh.return_value = {**self.receipt, 'stdout': self.receipt['stdout'].replace('|120|', '|121|')}
        with patch.object(chains, '_quota_available', return_value=True), patch.object(ex, 'prepare') as prepare, patch.object(ex, 'submit') as submit:
            chains._tick_one(run['id'])
        prepare.assert_not_called()
        submit.assert_not_called()
        self.assertIn('来源文件已变化', chains.get(run['id'])['steps'][0]['blocker'])

    def test_potcar_parse_cache_invalidates_on_stat_change_and_freeze_generates_once(self):
        with patch.object(inputs.contract, '_parse_potcar', wraps=inputs.contract._parse_potcar) as parser:
            for _ in range(3):
                chains.preview(self.project, ['r', 'c', 'd', 'b'])
            self.assertEqual(parser.call_count, 1)
            potcar = self.fixture.root/'potcars'/self.project['nodes'][1]['data']['inputDraft']['potcar_id']
            stat = potcar.stat(); os.utime(potcar, ns=(stat.st_atime_ns, stat.st_mtime_ns+1000000))
            chains.preview(self.project, ['r', 'c', 'd', 'b'])
            self.assertEqual(parser.call_count, 2)
        report = chains.preview(self.project, ['r', 'c', 'd', 'b'])
        with patch.object(inputs, 'generate', wraps=inputs.generate) as generate:
            chains.freeze(self.project, report['node_ids'], '缓存验收', report['definition_hash'])
            self.assertEqual(generate.call_count, 4)


class FileVerificationTests(unittest.TestCase):
    def test_small_input_requires_content_receipt_even_in_lightweight_mode(self):
        with self.assertRaisesRegex(ValueError, '策略'):
            source_files.snapshot('host', '/source', ['CONTCAR'], lambda *a, **k: {'exit_code': 0, 'stdout': 'FPILOT_FILE=CONTCAR|-|10|stamp\n'})

    def test_optional_full_verification_does_not_rewrite_the_contract(self):
        from backend.app import verify_source_files
        record = {'config': {'alias': 'host'}, 'chain_source_snapshot': {
            'source_path': '/source', 'files': {'CHGCAR': {'size_bytes': 10, 'mtime': 'stamp', 'verification_mode': 'metadata'}}}}
        receipt = {'exit_code': 0, 'stdout': 'FPILOT_FILE=CHGCAR|'+'a'*64+'|10|stamp\n'}
        original = copy.deepcopy(record)
        with patch.object(ex, 'get', return_value=record), patch.object(ex, 'ssh', return_value=receipt), patch.object(ex, 'save') as save:
            result = verify_source_files('attempt')
        self.assertEqual(result['verification_mode'], 'full')
        self.assertEqual(result['changed'], [])
        self.assertEqual(result['files']['CHGCAR']['sha256'], 'a'*64)
        self.assertEqual(record, original)
        save.assert_not_called()

    def test_lightweight_large_files_are_not_hashed_and_full_mode_is_available(self):
        def remote(alias, command, **kwargs):
            large = '-' if "printf -- '-|'" in command else 'b'*64
            return {'exit_code': 0, 'stdout': 'FPILOT_FILE=CONTCAR|'+'a'*64+'|10|stamp\nFPILOT_FILE=CHGCAR|'+large+'|9000000|stamp\n'}
        with patch.object(ex, 'ssh', side_effect=remote) as ssh:
            receipt = source_files.snapshot('host', '/source', ['CONTCAR', 'CHGCAR'], ex.ssh)
            command = ssh.call_args.args[1]
            self.assertNotIn('sha256sum CHGCAR', command)
            self.assertIn('sha256sum CONTCAR', command)
            plan = ex.source_file_plan({'files': receipt}, b'LCHARG = .FALSE.\n')
            materialize = ex.source_file_commands({'source_path': '/source', 'files': receipt}, plan)
            self.assertNotIn('sha256sum /source/CHGCAR', materialize)
            self.assertIn('stat -c %y /source/CHGCAR', materialize)
            self.assertIn('stat -c %s CHGCAR', materialize)
            full = source_files.snapshot('host', '/source', ['CONTCAR', 'CHGCAR'], ex.ssh, 'full')
            self.assertIn('sha256sum CHGCAR', ssh.call_args.args[1])
            self.assertIn('sha256', full['CHGCAR'])

    def test_new_freezes_are_lightweight_legacy_snapshots_keep_full_hashes(self):
        old = {'source_path': '/source', 'files': {'CHGCAR': {'sha256': 'a'*64, 'size_bytes': 10}}}
        plan = ex.source_file_plan(old, b'LCHARG = .FALSE.\n')
        self.assertIn('sha256sum /source/CHGCAR', ex.source_file_commands(old, plan))


if __name__ == '__main__':
    unittest.main()
