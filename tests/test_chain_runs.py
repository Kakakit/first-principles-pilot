import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import chain_runs as chains, execution as ex, inputs


class ChainRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ex_patch = patch.object(ex, 'ROOT', self.root)
        self.input_patch = patch.object(inputs, 'ROOT', self.root)
        self.ex_patch.start()
        self.input_patch.start()
        structure = json.loads((Path(__file__).parent / 'fixtures/Si.structure.json').read_text())
        potcar = b'TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\nENMAX = 245.0;\nEnd of Dataset\n'
        key = inputs.upload_potcar(base64.b64encode(potcar).decode())['id']
        def node(name, kind, sequence, values=None, draft=None):
            return {'id': name, 'data': {'kind': kind, 'label': name,
                    'executionMode': 'vasp', 'stageSequence': sequence,
                    'stageSlug': kind, 'parameters': {'preset': 'basic', 'values': values or {}},
                    'inputDraft': draft or {'grid': '4 4 4', 'center': 'Gamma', 'potcar_id': key}}}
        self.project = {'id': 'p', 'name': 'chain', 'remoteSlug': 'chain',
                        'nodes': [
                            {'id': 's', 'data': {'kind': 'structure', 'label': 'structure',
                                              'structure': structure, 'executionMode': 'vasp',
                                              'stageSequence': 1, 'stageSlug': 'structure'}},
                            node('r', 'relax', 2, {'NSW': '3', 'EDIFFG': '-0.02', 'LWAVE': 'false'}),
                            node('c', 'scf', 3, {'LCHARG': 'false'}),
                            node('d', 'dos', 4),
                            node('b', 'bands', 5, draft={'line_points': '40', 'potcar_id': key})],
                        'edges': [{'source': 's', 'target': 'r'},
                                  {'source': 'r', 'target': 'c'},
                                  {'source': 'c', 'target': 'd'},
                                  {'source': 'c', 'target': 'b'}],
                        'parameters': {'shared': {'values': {
                            'XC_FUNCTIONAL': 'PBE', 'ENCUT': '400', 'EDIFF': '1e-6',
                            'ISPIN': '1', 'ISMEAR': '0', 'SIGMA': '0.05',
                            'LDAU': 'false'}}}}

    def tearDown(self):
        self.input_patch.stop()
        self.ex_patch.stop()
        self.temp.cleanup()

    def frozen(self):
        report = chains.preview(self.project, ['r', 'c', 'd', 'b'])
        self.assertTrue(report['ready'], report['steps'])
        return chains.freeze(self.project, report['node_ids'], '计算链验收',
                             report['definition_hash'])

    def test_freeze_output_policy_and_dynamic_band_path(self):
        report = chains.preview(self.project, ['r', 'c', 'd', 'b'])
        self.assertEqual(report['max_jobs'], 6)
        self.assertEqual(report['steps'][3]['path_policy']['line_points'], '40')
        self.assertEqual(report['steps'][0]['conditional_retry']['incar_diff'],
                         {'ISTART': '1', 'ICHARG': '0'})
        self.assertEqual(report['steps'][1]['conditional_retry']['incar_diff'],
                         {'ISTART': '1', 'ICHARG': '1'})
        self.assertEqual(report['steps'][1]['pending_sources'][0]['file'], 'CONTCAR')
        run = self.frozen()
        self.assertEqual(run['state'], 'FROZEN')
        self.assertEqual(len(run['steps']), 4)
        for step in run['steps']:
            _, files = ex.bundle(step['bundle_id'], allow_dependencies=True)
            incar = files['INCAR']
            if step['stage'] == 'relax':
                self.assertEqual(ex._incar_value(incar, 'LWAVE'), '.TRUE.')
            if step['stage'] == 'scf':
                self.assertEqual(ex._incar_value(incar, 'LCHARG'), '.TRUE.')
                self.assertEqual(ex._incar_value(incar, 'LWAVE'), '.TRUE.')
            if step['stage'] in ('dos', 'bands'):
                self.assertEqual(ex._incar_value(incar, 'LWAVE'), '.FALSE.')
        self.assertEqual(len(chains.history('p')), 1)

    def test_authorization_requires_exact_reviewed_scripts(self):
        run = self.frozen()
        resources = {step['node_id']: {'node': 'node01', 'cores': None, 'reason': ''}
                     for step in run['steps']}
        script = '#!/bin/bash\ntrue\n'
        profile = {'node': 'node01', 'ppn': 4}
        previews = {key: {'script': script, 'profile': profile} for key in resources}
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={
                 'resource_ready': True, 'script': script, 'profile': profile}):
            with self.assertRaisesRegex(ValueError, '预览已变化'):
                chains.authorize(run['id'], 'p', run['plan_hash'], run['mismatch_hash'],
                                 {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}, resources, {})
            active = chains.authorize(run['id'], 'p', run['plan_hash'], run['mismatch_hash'],
                                      {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}, resources, previews)
        self.assertEqual(active['state'], 'ACTIVE')
        self.assertEqual(active['authorization']['max_jobs'], 6)
        with self.assertRaises(ValueError):
            chains.authorize(run['id'], 'p', run['plan_hash'], run['mismatch_hash'],
                             {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}, resources, previews)

    def test_node_pool_waits_for_capacity_then_dispatches_once(self):
        run = self.frozen()
        run['steps'] = [run['steps'][0]]
        run['node_ids'] = ['r']
        run = chains.save(run)
        config = {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}
        candidates = [{'node': name, 'cores': 48, 'reason': ''} for name in ('node01', 'node02')]
        resources = {'r': {'vasp_executable': 'vasp_std', 'candidates': candidates}}
        profile = lambda name: {'node': name, 'ppn': 48}
        script = lambda name: 'script for ' + name
        def busy(*args, **kwargs):
            name = args[2]
            return {'resource_ready': False, 'blockers': ['selected node CPUS_FREE must be greater than zero'],
                    'node_status': 'BUSY', 'profile': profile(name), 'script': script(name)}
        reviewed = {'r': {name: {'profile': profile(name), 'script': script(name)}
                          for name in ('node01', 'node02')}}
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', side_effect=lambda *args, **kwargs: {
                 **busy(*args, **kwargs), 'node_status': 'UNAVAILABLE'}):
            with self.assertRaisesRegex(ValueError, '预览已变化'):
                chains.authorize(run['id'], 'p', run['plan_hash'], run['mismatch_hash'],
                                 config, resources, reviewed)
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', side_effect=busy):
            run = chains.authorize(run['id'], 'p', run['plan_hash'], run['mismatch_hash'],
                                   config, resources, reviewed)
        def prepare(*args, **kwargs):
            record = {'id': kwargs['attempt_id'], 'state': 'PREPARED',
                      'bundle_id': args[0], 'project_id': 'p', 'config': args[1],
                      'node': args[3], 'cores': args[4], 'reason': args[5]}
            ex.save(record)
            return record
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', side_effect=busy), \
             patch.object(ex, 'prepare', side_effect=prepare) as prep, \
             patch.object(ex, 'submit') as submit:
            chains.tick()
            prep.assert_not_called()
            submit.assert_not_called()
        queued = chains.get(run['id'])['steps'][0]
        self.assertEqual(queued['state'], 'PENDING')
        self.assertIn('node01', queued['waiting_reason'])
        def one_free(*args, **kwargs):
            result = busy(*args, **kwargs)
            if args[2] == 'node02':
                result.update(resource_ready=True, blockers=[])
            else:
                result['node_status'] = 'UNAVAILABLE'
            return result
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', side_effect=one_free), \
             patch.object(ex, 'prepare', side_effect=prepare) as prep, \
             patch.object(ex, 'submit', return_value={'state': 'SUBMITTED'}) as submit:
            chains.tick()
            self.assertEqual(prep.call_count, 1)
            self.assertEqual(prep.call_args.args[3], 'node02')
            submit.assert_called_once()
        dispatched = chains.get(run['id'])['steps'][0]
        self.assertEqual(dispatched['state'], 'SUBMITTED')
        self.assertEqual(len(dispatched['attempts']), 1)
        self.assertNotIn('waiting_reason', dispatched)

    def test_convergence_and_one_retry_stop(self):
        run = self.frozen()
        step = run['steps'][0]
        run['authorization'] = {'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'},
                                'resources': {step['node_id']: {'node': 'node01', 'cores': None,
                                                                 'reason': '', 'profile': {},
                                                                 'script_sha256': hashlib.sha256(b'script').hexdigest()}}}
        # Test a single branch so unrelated planned stages cannot affect the verdict.
        run['steps'] = [step]
        run['state'] = 'ACTIVE'
        run = chains.save(run)
        prepared = {'id': 'attempt-one', 'state': 'PREPARED', 'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}}
        def prepare(*args, **kwargs):
            record = dict(prepared)
            record['id'] = kwargs['attempt_id']
            with ex.db() as db:
                db.execute('INSERT INTO attempts VALUES(?,?,?)',
                           (record['id'], record['state'], json.dumps(record)))
            return record
        with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={
                 'resource_ready': True, 'profile': {}, 'script': 'script'}), \
             patch.object(ex, 'prepare', side_effect=prepare) as prep, \
             patch.object(ex, 'submit', side_effect=lambda key: {'id': key, 'state': 'SUBMITTED'}):
            chains.tick()
            first = chains.get(run['id'])['steps'][0]['attempt_id']
            self.assertEqual(prep.call_count, 1)
            record = ex.get(first)
            record['state'] = 'COMPLETED'
            ex.save(record)
            with patch.object(chains, 'convergence', return_value={'status': 'EXHAUSTED',
                                                                  'reason': '达到 NELM'}):
                chains.tick()
                current = chains.get(run['id'])['steps'][0]
                self.assertEqual(len(current['attempts']), 2)
                self.assertEqual(prep.call_count, 2)
                self.assertEqual(prep.call_args.kwargs['chain_source_kind'], 'relax-restart')
                second = current['attempt_id']
                record = ex.get(second)
                record['state'] = 'COMPLETED'
                ex.save(record)
                chains.tick()
            self.assertEqual(chains.get(run['id'])['state'], 'BLOCKED')
            self.assertEqual(prep.call_count, 2)

    def test_successful_retry_is_the_only_downstream_source(self):
        run = self.frozen()
        relax, scf = run['steps'][:2]
        first, second = 'relax-first', 'relax-retry'
        relax.update(state='SUBMITTED', attempt_id=second, attempts=[first, second])
        run['steps'] = [relax, scf]
        profile, script = {'node': 'node01', 'ppn': 4}, 'script'
        run['authorization'] = {'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'},
                                'resources': {key: {'node': 'node01', 'cores': None,
                                                     'reason': '', 'profile': profile,
                                                     'script_sha256': hashlib.sha256(script.encode()).hexdigest()}
                                              for key in ('r', 'c')}}
        run['state'] = 'ACTIVE'
        run = chains.save(run)
        record = {'id': second, 'state': 'COMPLETED', 'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}}
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)',
                       (second, record['state'], json.dumps(record)))
        def prepare(*args, **kwargs):
            prepared = {'id': kwargs['attempt_id'], 'state': 'PREPARED',
                        'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}}
            with ex.db() as db:
                db.execute('INSERT INTO attempts VALUES(?,?,?)',
                           (prepared['id'], 'PREPARED', json.dumps(prepared)))
            return prepared
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(chains, 'convergence', return_value={'status': 'CONVERGED'}), \
             patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={
                 'resource_ready': True, 'profile': profile, 'script': script}), \
             patch.object(ex, 'prepare', side_effect=prepare) as preparation, \
             patch.object(ex, 'submit', side_effect=lambda key: {'id': key, 'state': 'SUBMITTED'}):
            chains.tick()
        current = chains.get(run['id'])
        self.assertEqual(current['steps'][0]['final_attempt_id'], second)
        self.assertEqual(preparation.call_count, 1)
        self.assertEqual(preparation.call_args.kwargs['chain_source_id'], second)
        self.assertEqual(preparation.call_args.kwargs['chain_source_kind'], 'dependency')
        self.assertEqual(current['steps'][1]['state'], 'SUBMITTED')

    def test_reject_missing_parent_in_selection(self):
        report = chains.preview(self.project, ['c', 'd'])
        self.assertFalse(report['ready'])
        self.assertIn('尚无提供 CONTCAR', report['steps'][0]['errors'][-1])

    def test_final_step_convergence_and_exhaustion_are_separate(self):
        run = self.frozen()
        relax = run['steps'][0]
        record = {'id': 'old', 'state': 'COMPLETED', 'bundle_id': relax['bundle_id'],
                  'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}, 'evidence': {
                      'wrapper_exit': 0, 'vasp_timing': True, 'output_sanity': True,
                      'ediff_reached': True, 'ediff_value': 1e-6,
                      'last_delta_e': 1e-7, 'last_delta_eps': 1e-7,
                      'last_electronic_step': 8}}
        with patch.object(ex, 'attempt_path', return_value={'shell': '/safe/attempt'}), \
             patch.object(ex, 'ssh', return_value={
                 'exit_code': 0, 'stdout': 'FPILOT_IONIC=1\nFPILOT_IONIC_STEP=2\n'}):
            result = chains.convergence(record, 'relax')
        self.assertEqual(result['status'], 'CONVERGED')
        record['evidence'].update(last_delta_e=0.01, last_delta_eps=0.01,
                                  last_electronic_step=60)
        with patch.object(ex, 'attempt_path', return_value={'shell': '/safe/attempt'}), \
             patch.object(ex, 'ssh', return_value={
                 'exit_code': 0, 'stdout': 'FPILOT_IONIC=0\nFPILOT_IONIC_STEP=3\n'}):
            result = chains.convergence(record, 'relax')
        self.assertEqual(result['status'], 'EXHAUSTED')
        self.assertFalse(result['electronic'])

    def test_chain_source_requires_readable_files_and_exact_parent(self):
        run = self.frozen()
        scf = run['steps'][1]
        manifest, _ = ex.bundle(scf['bundle_id'], allow_dependencies=True)
        source = {'id': 'source', 'state': 'COMPLETED', 'path_schema': 'fpilot.remote-attempt-path',
                  'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}, 'project_id': 'p', 'node_id': 'r',
                  'job_id': '123.node01'}
        good = 'FPILOT_FILE=CONTCAR|' + 'a'*64 + '|120|2026-09-30 12:00:00.000000000 +0000\n'
        with patch.object(ex, 'get', return_value=source), \
             patch.object(ex, 'attempt_path', return_value={'shell': '/safe/source'}), \
             patch.object(ex, 'ssh', return_value={'exit_code': 0, 'stdout': good}):
            snapshot = ex.chain_source_snapshot(scf['bundle_id'], manifest, 'source',
                                                'jutai-1', 'dependency')
        self.assertEqual(snapshot['files']['CONTCAR']['sha256'], 'a'*64)
        self.assertEqual(snapshot['source_job_id'], '123.node01')
        with patch.object(ex, 'get', return_value={**source, 'node_id': 'unrelated'}):
            with self.assertRaisesRegex(ValueError, '直接上游'):
                ex.chain_source_snapshot(scf['bundle_id'], manifest, 'source',
                                         'jutai-1', 'dependency')
        with patch.object(ex, 'get', return_value=source), \
             patch.object(ex, 'attempt_path', return_value={'shell': '/safe/source'}), \
             patch.object(ex, 'ssh', return_value={'exit_code': 1, 'stdout': ''}):
            with self.assertRaisesRegex(ValueError, '不可读'):
                ex.chain_source_snapshot(scf['bundle_id'], manifest, 'source',
                                         'jutai-1', 'dependency')

    def test_unknown_blocks_only_its_branch_and_pause_prevents_dispatch(self):
        run = self.frozen()
        steps = {item['node_id']: item for item in run['steps']}
        steps['r'].update(state='DONE', final_attempt_id='relax-final')
        steps['c'].update(state='DONE', final_attempt_id='scf-final')
        steps['d'].update(state='BLOCKED', blocker='attempt UNKNOWN；须核对原作业')
        steps['b']['actual_band_path'] = {'source_attempt_id': 'scf-final'}
        profile, script = {'node': 'node01', 'ppn': 4}, 'script'
        run['authorization'] = {'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'},
                                'resources': {key: {'node': 'node01', 'cores': None,
                                                     'reason': '', 'profile': profile,
                                                     'script_sha256': hashlib.sha256(script.encode()).hexdigest()}
                                              for key in steps}}
        run['state'] = 'PAUSED'
        run = chains.save(run)
        with patch.object(ex, 'prepare') as prepare:
            chains.tick()
            prepare.assert_not_called()
        run = chains.resume(run['id'], 'p')
        def prepare(*args, **kwargs):
            record = {'id': kwargs['attempt_id'], 'state': 'PREPARED',
                      'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}}
            with ex.db() as db:
                db.execute('INSERT INTO attempts VALUES(?,?,?)',
                           (record['id'], 'PREPARED', json.dumps(record)))
            return record
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={
                 'resource_ready': True, 'profile': profile, 'script': script}), \
             patch.object(ex, 'prepare', side_effect=prepare) as preparation, \
             patch.object(ex, 'submit', side_effect=lambda key: {'id': key, 'state': 'SUBMITTED'}):
            chains.tick()
        self.assertEqual(preparation.call_count, 1)
        self.assertEqual(preparation.call_args.kwargs['chain_source_id'], 'scf-final')
        self.assertEqual(chains.get(run['id'])['steps'][3]['state'], 'SUBMITTED')

    def test_prepared_recovery_and_quota_wait_never_create_another_attempt(self):
        run = self.frozen()
        step = run['steps'][0]
        run['steps'] = [step]
        step.update(state='PREPARED', attempt_id='already-prepared',
                    attempts=['already-prepared'])
        run['authorization'] = {'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'},
                                'resources': {'r': {'node': 'node01', 'cores': None,
                                                    'reason': '', 'profile': {},
                                                    'script_sha256': 'unused'}}}
        run['state'] = 'ACTIVE'
        run = chains.save(run)
        record = {'id': 'already-prepared', 'state': 'PREPARED',
                  'config': {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}}
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)',
                       (record['id'], record['state'], json.dumps(record)))
        with patch.object(chains, '_quota_available', return_value=False), \
             patch.object(ex, 'prepare') as prepare, patch.object(ex, 'submit') as submit:
            chains.tick()
            prepare.assert_not_called()
            submit.assert_not_called()
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(ex, 'prepare') as prepare, \
             patch.object(ex, 'submit', return_value={'id': record['id'],
                                                      'state': 'UNKNOWN'}) as submit:
            chains.tick()
            prepare.assert_not_called()
            submit.assert_called_once_with(record['id'])
        self.assertEqual(chains.get(run['id'])['steps'][0]['attempts'], ['already-prepared'])
        record['state'] = 'UNKNOWN'
        ex.save(record)
        with patch.object(ex, 'prepare') as prepare, patch.object(ex, 'submit') as submit:
            chains.tick()
            prepare.assert_not_called()
            submit.assert_not_called()
        self.assertEqual(chains.get(run['id'])['state'], 'BLOCKED')

    def test_orphan_prepared_attempt_is_recovered_after_restart(self):
        run = self.frozen()
        step = run['steps'][0]
        run['steps'] = [step]
        config = {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}
        profile, script = {'node': 'node01', 'ppn': 4}, 'script'
        run['authorization'] = {'config': config,
                                'resources': {'r': {'node': 'node01', 'cores': None,
                                                    'reason': '', 'profile': profile,
                                                    'script_sha256': hashlib.sha256(script.encode()).hexdigest()}}}
        run['state'] = 'ACTIVE'
        run = chains.save(run)
        key = hashlib.sha256((run['id'] + ':r:0').encode()).hexdigest()[:32]
        orphan = {'id': key, 'state': 'PREPARED', 'bundle_id': step['bundle_id'],
                  'project_id': 'p', 'config': config, 'node': 'node01',
                  'cores': None, 'reason': ''}
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)',
                       (key, 'PREPARED', json.dumps(orphan)))
        with patch.object(chains, '_quota_available', return_value=True), \
             patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
             patch.object(chains.servers, 'preview', return_value={
                 'resource_ready': True, 'profile': profile, 'script': script}), \
             patch.object(ex, 'prepare') as prepare, \
             patch.object(ex, 'submit', return_value={'id': key, 'state': 'SUBMITTED'}) as submit:
            chains.tick()
        prepare.assert_not_called()
        submit.assert_called_once_with(key)
        current = chains.get(run['id'])['steps'][0]
        self.assertEqual(current['attempts'], [key])
        self.assertEqual(current['state'], 'SUBMITTED')

    def test_restart_incar_and_copy_plan_use_stage_specific_files(self):
        run = self.frozen()
        relax = next(s for s in run['steps'] if s['stage'] == 'relax')
        scf = next(s for s in run['steps'] if s['stage'] == 'scf')
        for step, kind, names, charge in (
            (relax, 'relax-restart', ('CONTCAR', 'WAVECAR'), '0'),
            (scf, 'scf-restart', ('CONTCAR', 'CHGCAR', 'WAVECAR'), '1')):
            _, files = ex.bundle(step['bundle_id'], allow_dependencies=True)
            incar = ex._restart_incar(files['INCAR'], 'wave' if kind == 'relax-restart' else 'both')
            self.assertEqual(ex._incar_value(incar, 'ISTART'), '1')
            self.assertEqual(ex._incar_value(incar, 'ICHARG'), charge)
            snapshot = {'schema': 'fpilot.chain-source.v1', 'kind': kind,
                        'files': {name: {'sha256': 'a'*64, 'size_bytes': 100} for name in names}}
            plan = ex.source_file_plan(snapshot, incar)
            self.assertEqual(set(plan), set(names))
            self.assertEqual(plan['CONTCAR']['target'], 'POSCAR')
            self.assertEqual(plan['WAVECAR']['mode'], 'reflink-or-copy')
            if kind == 'scf-restart':
                self.assertEqual(plan['CHGCAR']['mode'], 'reflink-or-copy')

    def test_final_band_input_is_tied_to_scf_contcar(self):
        run = self.frozen()
        step = next(s for s in run['steps'] if s['stage'] == 'bands')
        structure = self.project['nodes'][0]['data']['structure']
        proposal = {'path': '0 0 0 G\n0.5 0 0 X', 'structure_hash': 'f'*64}
        with patch.object(ex, 'get', return_value={'id': 'scf-final', 'job_id': '123.node01'}), \
             patch.object(chains.artifacts, 'preview', return_value={
                 'partial': False, 'structure': structure}), \
             patch.object(chains.band_path, 'generate', return_value=proposal):
            chains._band_bundle(run, step, 'scf-final')
        manifest, files = ex.bundle(step['bundle_id'], allow_dependencies=True)
        self.assertEqual(manifest['band_path_source_sha256'], 'f'*64)
        self.assertEqual(manifest['band_path_source_attempt_id'], 'scf-final')
        self.assertIn(b'0.5 0.0 0.0', files['KPOINTS'])


if __name__ == '__main__':
    unittest.main()
