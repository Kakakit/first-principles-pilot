"""Exercise the shipped FeCr2O4 JSON through import, freeze, and a fake worker."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import chain_runs as chains, execution as ex, inputs, recipes
from backend.storage import WorkspaceStore


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'recipes' / 'FeCr2O4_unit_cell_A1_A2_B.json'


class FeCrRecipeChainTests(unittest.TestCase):
    def test_json_import_freeze_and_simulated_dispatch(self):
        raw = RECIPE.read_text(encoding='utf-8')
        recipe = json.loads(raw)
        potcar_id = recipe['stages'][0]['input']['potcar_id']
        provenance_id = recipe['stages'][0]['input']['potcar_provenance_id']
        potcar_source = ROOT / 'workspace-data' / 'potcars' / potcar_id
        provenance_source = ROOT / 'workspace-data' / 'potcars' / (provenance_id + '.library.json')
        if not potcar_source.exists() or not provenance_source.exists():
            self.skipTest('Project POTCAR is not present in this checkout')
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / 'potcars').mkdir()
            shutil.copyfile(potcar_source, temp / 'potcars' / potcar_id)
            shutil.copyfile(provenance_source, temp / 'potcars' / provenance_source.name)
            with patch.object(inputs, 'ROOT', temp), patch.object(ex, 'ROOT', temp), patch.object(recipes, 'ROOT', temp), patch('backend.recycle.ROOT', temp), patch('backend.remote_transport._interpreter', return_value='/usr/bin/python3'), patch('backend.servers.select_profile', side_effect=lambda alias,variant='vasp_std': {'alias':alias,'vasp_executable':'/bin/'+variant}):
                imported = recipes.import_text(raw)
                self.assertFalse(imported['submission_authorized'])
                self.assertEqual((temp / 'recipes' / 'originals' / imported['original']['sha256']).read_text(), raw)
                project = imported['project']
                store = WorkspaceStore(temp)
                store.save({'activeId': project['id'], 'projects': [project]}, 0, 'autosave')
                project = store.read()['workspace']['projects'][0]
                selected = [stage['id'] for stage in recipe['stages']]
                preview = chains.preview(project, selected)
                self.assertTrue(preview['ready'], preview['steps'])
                self.assertEqual((len(preview['steps']), preview['max_jobs']), (12, 20))
                self.assertTrue(any(row['parent_id'] == 'a1-relax' and row['node_id'] == 'a2-scf'
                                    and row['field'] == 'MAGMOM' for row in preview['mismatches']))
                run = chains.freeze(project, selected, '模拟导入配方验收', preview['definition_hash'])
                by_id = {step['node_id']: step for step in run['steps']}
                for child, parent in [('a1-pol', 'a1-scf'), ('a2-pol', 'a2-scf'), ('b-pol', 'b-scf')]:
                    step = by_id[child]
                    self.assertEqual(step['parent_id'], parent)
                    self.assertEqual([part['file'] for part in step['pending_sources']], ['CONTCAR', 'CHGCAR'])
                    manifest, files = ex.bundle(step['bundle_id'], allow_dependencies=True)
                    self.assertEqual(ex._restart_policy(manifest, files)['ICHARG'], 1)
                    self.assertEqual(ex._incar_value(files['INCAR'], 'ISTART'), '0')
                    self.assertEqual(ex._incar_value(files['INCAR'], 'LCALCPOL'), '.TRUE.')
                    self.assertIsNone(ex._incar_value(files['INCAR'], 'NCORE'))
                for step in run['steps']:
                    _, files = ex.bundle(step['bundle_id'], allow_dependencies=True)
                    self.assertEqual(hashlib.sha256(files['POTCAR']).hexdigest(), potcar_id)
                    self.assertEqual(ex._incar_value(files['INCAR'], 'KPAR'), '4')
                    if step['node_id'] in ('a1-relax', 'a1-scf', 'a1-bands', 'a1-dos', 'a1-pol'):
                        self.assertFalse(step['requires_ncl'])
                    else:
                        self.assertTrue(step['requires_ncl'])
                profile, script = {'node': 'node01', 'ppn': 48}, '#!/bin/bash\ntrue\n'
                resources = {step['node_id']: {'node': 'node01', 'cores': None, 'reason': '',
                             'vasp_executable': 'vasp_ncl' if step['requires_ncl'] else 'vasp_std'}
                             for step in run['steps']}
                approvals = {key: {'profile': profile, 'script': script} for key in resources}
                with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}) as full_probe, \
                     patch.object(chains.servers, 'preview', return_value={
                         'resource_ready': True, 'profile': profile, 'script': script}):
                    run = chains.authorize(run['id'], project['id'], run['plan_hash'],
                                           run['mismatch_hash'], {'alias': 'fake-host',
                                           'vasp_executable': '/bin/vasp_std'}, resources, approvals)
                self.assertEqual(full_probe.call_count, 2)  # std/ncl, not 12 stages
                self.assertEqual(run['state'], 'ACTIVE')
                seen = []

                def fake_prepare(bundle_id, config, probe_id, node, cores, reason, **kwargs):
                    entry = {'id': kwargs['attempt_id'], 'state': 'PREPARED', 'bundle_id': bundle_id,
                             'project_id': kwargs['project_id'], 'config': config, 'node': node,
                             'cores': cores, 'reason': reason,
                             'chain_source_snapshot': {'kind': kwargs['chain_source_kind'],
                                                       'source_attempt_id': kwargs['chain_source_id']}}
                    seen.append((entry['id'], kwargs['chain_source_id'], config['vasp_executable']))
                    with ex.db() as db:
                        db.execute('INSERT INTO attempts VALUES(?,?,?)',
                                   (entry['id'], entry['state'], json.dumps(entry)))
                    return entry

                def fake_submit(key):
                    entry = ex.get(key)
                    entry['state'] = 'SUBMITTED'
                    return ex.save(entry)

                source_structure = project['nodes'][0]['data']['structure']
                with patch.object(chains, '_quota_available', return_value=True), \
                     patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
                     patch.object(chains.servers, 'preview', return_value={
                         'resource_ready': True, 'profile': profile, 'script': script}), \
                     patch.object(chains.artifacts, 'preview', return_value={
                         'partial': False, 'structure': source_structure}), \
                     patch.object(chains, 'convergence', return_value={'status': 'CONVERGED'}), \
                     patch.object(ex, 'prepare', side_effect=fake_prepare), \
                     patch.object(ex, 'submit', side_effect=fake_submit):
                    for _ in range(15):
                        chains.tick()
                        current = chains.get(run['id'])
                        if current['state'] == 'COMPLETE':
                            break
                        for step in current['steps']:
                            if step['state'] == 'SUBMITTED':
                                entry = ex.get(step['attempt_id'])
                                entry['state'] = 'COMPLETED'
                                ex.save(entry)
                current = chains.get(run['id'])
                self.assertEqual(current['state'], 'COMPLETE',
                                 (current.get('last_worker_error'), [(step['node_id'], step['state'], step.get('blocker'))
                                                                       for step in current['steps']]))
                self.assertEqual(len(seen), 12)
                self.assertEqual(len({item[0] for item in seen}), 12)
                finished = {step['node_id']: step for step in current['steps']}
                for key, step in finished.items():
                    self.assertEqual(len(step['attempts']), 1)
                    if step['parent_id'] in finished:
                        self.assertEqual(step['source_snapshot']['source_attempt_id'],
                                         finished[step['parent_id']]['final_attempt_id'])
                    expected_program = 'vasp_ncl' if step['requires_ncl'] else 'vasp_std'
                    self.assertEqual(Path(ex.get(step['attempt_id'])['config']['vasp_executable']).name,
                                     expected_program)

                # Exercise the real prepare/submit assembly for one SCF→LCALCPOL
                # edge. SSH and the scheduler return synthetic receipts only.
                parent_id = finished['a1-scf']['final_attempt_id']
                parent = ex.get(parent_id)
                parent.update(path_schema='fpilot.remote-attempt-path', job_id='12.node01', node_id='a1-scf',
                              project_slug=project['remoteSlug'], stage_sequence=3, stage_slug='a1-scf',
                              remote_workspace_root='$HOME/230269265lht',
                              remote_relative_path='material-projects/recipe-test/flows/main/stages/03-scf/attempts/source',
                              remote_path_absolute='/home/user/230269265lht/material-projects/recipe-test/flows/main/stages/03-scf/attempts/source')
                ex.save(parent)
                charge_hash, structure_hash = 'a' * 64, 'b' * 64
                source_receipt = {'exit_code': 0, 'stderr': '', 'stdout':
                                  'FPILOT_FILE=CHGCAR|-|42|stamp\n'
                                  f'FPILOT_FILE=CONTCAR|{structure_hash}|80|stamp\n'}
                root_receipt = {'exit_code': 0, 'stderr': '', 'stdout':
                                'FPILOT_ROOT_ABS=/home/user/230269265lht\n'
                                'FPILOT_ROOT_SNAPSHOT=1:2:3:4\n'}
                receipts = [source_receipt, root_receipt, source_receipt,
                            {'exit_code': 0, 'stderr': '', 'stdout': 'FPILOT_JOB_ID=13.node01\n'},
                            {'exit_code': 0, 'stderr': '', 'stdout': 'FPILOT_JOB_ID=13.node01\njob_state = R\n'}]
                with patch.object(chains.servers, 'probe', return_value={'id': 'probe'}), \
                     patch.object(chains.servers, 'preview', return_value={
                         'resource_ready': True, 'profile': profile, 'script': script}), \
                     patch.object(ex.servers.pbs, 'audit_inputs', return_value={
                         'status': 'FAIL', 'errors': ['CHGCAR required by INCAR restart settings']}), \
                     patch.object(ex, 'ssh', side_effect=receipts) as ssh, \
                     patch.object(ex, 'start_watcher'):
                    prepared = ex.prepare.__wrapped__(finished['a1-pol']['bundle_id'],
                                          {'alias': 'fake-host', 'vasp_executable': '/bin/vasp_std',
                                           'remote_workspace_root': '$HOME/230269265lht'},
                                          'probe', 'node01', project_id=project['id'],
                                          chain_source_id=parent_id, chain_source_kind='dependency')
                    self.assertEqual(prepared['input_audit']['status'], 'PASS_WITH_REMOTE_DEPENDENCY')
                    self.assertEqual(prepared['source_file_plan']['CONTCAR']['target'], 'POSCAR')
                    self.assertEqual(prepared['source_file_plan']['CHGCAR']['mode'], 'reflink-or-copy')
                    submitted = ex.submit(prepared['id'])
                self.assertEqual(submitted['job_id'], '13.node01')
                command = next(call.args[1] for call in ssh.call_args_list if 'qsub run.pbs' in call.args[1])
                self.assertIn('/CONTCAR POSCAR', command)
                self.assertIn('/CHGCAR CHGCAR', command)
                self.assertIn('sha256sum POSCAR', command)
                self.assertNotIn('sha256sum CHGCAR', command)
                self.assertIn('stat -c %s CHGCAR', command)


if __name__ == '__main__':
    unittest.main()
