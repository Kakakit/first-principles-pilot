"""Role policy acceptance; all remote receipts are isolated fixtures."""
import base64
import copy
import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch

import test_chain_runs
from backend import artifacts, chain_runs, execution as ex, inputs, source_contracts as sources, source_files


class SourceContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_chain_runs.ChainRunTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.project = copy.deepcopy(self.fixture.project)
        self.project['nodes'] = self.project['nodes'][:3]
        self.project['edges'] = self.project['edges'][:2]
        self.project['nodes'][2]['data']['parameters']['values']['LCHARG'] = 'true'
        self.roots = {}
        for target, attribute, value in [(artifacts, 'ROOT', self.fixture.root),
                                         (ex, 'attempt_path', lambda record: {'shell': str(self.roots[record['id']])}),
                                         (source_files, 'snapshot', self.receipt),
                                         (artifacts, 'preview', self.preview),
                                         (source_files, 'structure_header', self.header),
                                         (source_files, 'wave_header', lambda *a: {'nkpoints': 32, 'nbands': 8, 'spin_channels': 1, 'encut_eV': 400})]:
            mocked = patch.object(target, attribute, side_effect=value) if callable(value) else patch.object(target, attribute, value)
            mocked.start(); self.addCleanup(mocked.stop)
        self.source = self.make_source('relax', 'r')

    def receipt(self, alias, path, names, ssh, mode='lightweight'):
        result = {}
        for name in names:
            file = self.roots_by_path(path) / name
            if not file.is_file() or not file.stat().st_size:
                raise ValueError('来源文件不可读取或为空：' + name)
            result[name] = {'size_bytes': file.stat().st_size, 'mtime': 'stamp',
                            'verification_mode': 'metadata' if source_files.is_large(name) and mode == 'lightweight' else 'content'}
            if result[name]['verification_mode'] == 'content':
                result[name]['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
        return result

    def roots_by_path(self, path):
        return next(root for root in self.roots.values() if str(root) == path)

    def header(self, alias, path, name, ssh):
        return (self.roots_by_path(path) / name).read_bytes()

    def preview(self, key, name, *args):
        from backend.structures import parse_structure
        raw = (self.roots[key] / name).read_bytes()
        return {'partial': False, 'structure': parse_structure(name, 'poscar', base64.b64encode(raw).decode())}

    def make_source(self, key, node_id, evidence=None):
        frozen = inputs.freeze(self.project, node_id, 'fixture')
        _, files = ex.bundle(frozen['id'], allow_dependencies=True)
        root = self.fixture.root / 'remote' / key
        root.mkdir(parents=True)
        poscar = files.get('POSCAR', files.get('POSCAR.reference'))
        for name, raw in {'POSCAR': poscar, 'CONTCAR': poscar, 'CHGCAR': poscar, 'WAVECAR': b'fixture wave'}.items():
            (root / name).write_bytes(raw)
        self.roots[key] = root
        record = {'id': key, 'project_id': 'p', 'node_id': node_id, 'bundle_id': frozen['id'],
                  'job_id': key+'.node01', 'state': 'COMPLETED', 'created_at': '2026-10-09',
                  'config': {'alias': 'one'}, 'path_schema': 'fpilot.remote-attempt-path',
                  'effective_input': {'files': {'POSCAR': {'sha256': hashlib.sha256(poscar).hexdigest()}}},
                  'evidence': evidence or {'vasp_timing': True, 'wrapper_exit': 0,
                                          'ediff_value': 1e-6, 'last_delta_e': 1e-8, 'last_delta_eps': 1e-8,
                                          'last_electronic_step': 8, 'ionic_converged': True, 'last_ionic_step': 2}}
        self.store(record)
        return record

    def store(self, record):
        with ex.db() as db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (record['id'], record['state'], json.dumps(record)))

    def add_child(self, parent='c', kind='scf'):
        child = copy.deepcopy(self.project['nodes'][2]); child['id'] = 'next'
        child['data']['kind'] = kind
        if kind == 'relax': child['data']['parameters']['values'].update(NSW='3', EDIFFG='-0.02')
        self.project['nodes'].append(child); self.project['edges'].append({'source': parent, 'target': 'next'})
        child['data']['sourceDraft'] = {'mode': 'upstream', 'purpose': 'charge', 'files': 'charge'}
        return child

    def test_defaults_and_explicit_new_use_actual_parent_roles(self):
        report = inputs.generate(self.project, 'c')
        self.assertEqual(report['source_plan']['required_files'], ['CONTCAR'])
        self.assertEqual(report['source_plan']['initialization'], {'ISTART': 0, 'ICHARG': 2})
        self.make_source('scf', 'c')
        child = self.add_child()
        self.assertEqual(inputs.generate(self.project, 'next')['source_plan']['required_files'], ['POSCAR', 'CHGCAR'])
        child['data']['sourceDraft']['mode'] = 'new'
        policy = inputs.generate(self.project, 'next')['source_plan']
        self.assertEqual(policy['required_files'], ['POSCAR'])
        self.assertEqual(policy['initialization'], {'ISTART': 0, 'ICHARG': 2})

    def test_soc_structure_only_has_no_electronic_acknowledgement_gate(self):
        node = self.project['nodes'][2]
        node['data']['parameters']['values'].update(LSORBIT='true', LNONCOLLINEAR='true', SAXIS='1 1 0')
        self.project['parameters']['shared']['moments'] = {atom['id']: '0 0 0' for atom in self.project['nodes'][0]['data']['structure']['atoms']}
        node['data']['sourceDraft'] = {'mode': 'new', 'purpose': 'charge', 'files': 'charge'}
        resolved = sources.resolve(self.project, 'c')
        self.assertEqual(resolved['candidates'][0]['differences'], [])
        frozen = inputs.freeze(self.project, 'c', 'SOC geometry')
        self.assertEqual(frozen['manifest']['source_contract']['schema'], 'fpilot.source-contract.v2')
        self.assertEqual(set(frozen['manifest']['source_contract']['roles']), {'structure'})
        self.assertIn('LSORBIT = .TRUE.', ex.bundle(frozen['id'])[1]['INCAR'].decode())

    def test_static_source_does_not_require_contcar_or_ionic_convergence(self):
        scf = self.make_source('scf', 'c')
        scf['evidence'].pop('ionic_converged'); self.store(scf)
        (self.roots['scf'] / 'CONTCAR').unlink()
        self.add_child()
        frozen = inputs.freeze(self.project, 'next', 'static actual POSCAR')
        contract = frozen['manifest']['source_contract']
        self.assertEqual(contract['roles']['structure']['file'], 'POSCAR')
        self.assertEqual(contract['initialization'], {'ISTART': 0, 'ICHARG': 1})

    def test_old_running_zero_cache_is_pending_not_terminal_missing(self):
        self.make_source('scf', 'c'); self.add_child()
        cache = self.fixture.root / 'artifact-cache'; cache.mkdir()
        (cache / 'scf.json').write_text(json.dumps({'attempt_id': 'scf', 'files': [{'path': 'POSCAR', 'size_bytes': 0}],
                                                  'observed_state': 'RUNNING', 'refreshed_at': '2026-10-09'}))
        with patch.object(ex, 'ssh', side_effect=AssertionError('draft checks must stay local')):
            candidate = next(c for c in sources.resolve(self.project, 'next')['candidates'] if c['id'] == 'scf')
        self.assertTrue(candidate['eligible']); self.assertEqual(candidate['verification_status'], 'UNVERIFIED')
        self.assertTrue(inputs.freeze(self.project, 'next', 'fresh binding')['manifest']['ready'])

    def test_scf_retry_uses_poscar_and_successful_attempt_is_downstream_source(self):
        scf = self.make_source('first', 'c')
        scf['evidence'].update(last_delta_e=1e-3, last_delta_eps=1e-3, last_electronic_step=60, ediff_reached=True)
        self.store(scf); (self.roots['first'] / 'CONTCAR').unlink()
        manifest, _ = ex.bundle(scf['bundle_id'], allow_dependencies=True)
        retry = ex.chain_source_snapshot(scf['bundle_id'], manifest, 'first', 'one', 'scf-restart')
        self.assertEqual(set(retry['files']), {'POSCAR', 'CHGCAR', 'WAVECAR'})
        self.assertEqual(retry['initialization'], {'ISTART': 1, 'ICHARG': 1})
        child = self.add_child()
        candidates = sources.resolve(self.project, 'next')['candidates']
        self.assertFalse(next(c for c in candidates if c['id'] == 'first')['eligible'])
        good = copy.deepcopy(scf); good.update(id='second', created_at='2026-10-10')
        good['evidence'].update(last_delta_e=1e-8, last_delta_eps=1e-8, last_electronic_step=8)
        self.roots['second'] = self.roots['first']; self.store(good)
        contract = inputs.freeze(self.project, child['id'], 'successful retry')['manifest']['source_contract']
        self.assertEqual(contract['roles']['charge']['attempt_id'], 'second')

    def test_manual_and_chain_consume_identical_roles_and_initialization(self):
        self.project['nodes'][2]['data']['sourceDraft'] = {'mode': 'upstream', 'purpose': 'charge', 'files': 'charge'}
        manual = inputs.freeze(self.project, 'c', 'manual')['manifest']['source_contract']
        chain = inputs.freeze(self.project, 'c', 'chain', defer_sources=True)
        snapshot = ex.chain_source_snapshot(chain['id'], chain['manifest'], 'relax', 'one', 'dependency')
        self.assertEqual(snapshot['source_contract']['roles'], manual['roles'])
        self.assertEqual(snapshot['initialization'], manual['initialization'])
        self.assertEqual(chain_runs.preview(self.project, ['c'], 'one')['sources'][0]['required_files'], ['CONTCAR'])

    def test_wave_constraints_are_hard_blocks_but_charge_grid_changes_are_allowed(self):
        self.project['nodes'][2]['data']['sourceDraft'] = {'mode': 'upstream', 'purpose': 'charge', 'files': 'both', 'acknowledge_differences': True}
        self.project['nodes'][2]['data']['inputDraft']['grid'] = '6 6 6'
        with self.assertRaisesRegex(ValueError, 'k 点'): inputs.freeze(self.project, 'c', 'bad wave')
        self.project['nodes'][2]['data']['sourceDraft']['files'] = 'charge'
        self.project['nodes'][2]['data']['sourceDraft']['read_charge'] = True
        frozen = inputs.freeze(self.project, 'c', 'CHGCAR new grid')
        self.assertEqual(frozen['manifest']['source_contract']['initialization']['ISTART'], 0)

    def test_fixed_charge_rejects_unconverged_and_model_changes(self):
        scf = self.make_source('scf', 'c'); child = self.add_child(kind='dos')
        scf['evidence'].update(last_delta_e=0.1, ediff_reached=True); self.store(scf)
        with self.assertRaisesRegex(ValueError, '电子收敛'): inputs.freeze(self.project, 'next', 'bad fixed charge')
        scf['evidence']['last_delta_e'] = 1e-8; self.store(scf)
        child['data']['parameters']['values']['XC_FUNCTIONAL'] = 'PBEsol'
        child['data']['sourceDraft']['acknowledge_differences'] = True
        with self.assertRaisesRegex(ValueError, 'GGA'): inputs.freeze(self.project, 'next', 'bad Hamiltonian')

    def test_variable_cell_continue_uses_istart_two_and_stops_on_changed_basis(self):
        self.project['nodes'][1]['data']['parameters']['values']['ISIF'] = '3'
        variable = self.make_source('variable', 'r')
        manifest, _ = ex.bundle(variable['bundle_id'], allow_dependencies=True)
        snapshot = ex.chain_source_snapshot(variable['bundle_id'], manifest, 'variable', 'one', 'relax-restart')
        self.assertEqual(snapshot['initialization'], {'ISTART': 2, 'ICHARG': 0})
        source = self.project['nodes'][1]['data']; source['sourceDraft'] = {'mode': 'self', 'purpose': 'continue', 'files': 'both', 'attempt_id': 'variable', 'acknowledge_differences': True}
        source['parameters']['values']['ENCUT'] = '500'
        with self.assertRaisesRegex(ValueError, 'ISTART=2'): inputs.freeze(self.project, 'r', 'changed basis')

    def test_pead_checks_effective_npar_and_retains_frozen_diff(self):
        source = self.project['nodes'][1]['data']['parameters']['values']
        source.update(LCALCPOL='true', NCORE='6')
        self.assertTrue(any('PEAD' in e for e in inputs.generate(self.project, 'r')['errors']))
        with patch.object(sources, 'validate_pead'):
            old = inputs.freeze(self.project, 'r', 'historical PEAD fixture')
        raw_before = (self.fixture.root / 'inputs' / (old['id']+'.zip')).read_bytes()
        source['NCORE'] = '1'
        diff = sources.draft_differences(self.project, 'r', old['id'])['differences']
        self.assertIn({'field': 'NCORE', 'frozen': '6', 'draft': '1'}, diff)
        self.assertEqual((self.fixture.root / 'inputs' / (old['id']+'.zip')).read_bytes(), raw_before)
        ex.validate_parallel('LCALCPOL = .TRUE.\nNPAR = 24\nKPAR = 2\n', 48)
        with self.assertRaisesRegex(ValueError, 'NPAR'): ex.validate_parallel('LCALCPOL = .TRUE.\nNPAR = 6\nKPAR = 2\n', 48)

    def test_missing_structure_wrong_order_and_unknown_do_not_fallback(self):
        self.project['nodes'][2]['data']['sourceDraft'] = {'mode': 'new', 'purpose': 'charge', 'files': 'charge'}
        (self.roots['relax'] / 'CONTCAR').unlink()
        with self.assertRaisesRegex(ValueError, 'CONTCAR'): inputs.freeze(self.project, 'c', 'missing structure')
        record = copy.deepcopy(self.source); record['state'] = 'UNKNOWN'; self.store(record)
        with patch.object(ex, 'ssh') as remote:
            self.assertFalse(sources.resolve(self.project, 'c')['candidates'][0]['eligible'])
            remote.assert_not_called()

    def test_frozen_current_input_has_no_false_boolean_difference(self):
        node = self.project['nodes'][1]
        node['data']['parameters']['values'].update(LCALCPOL='true', NCORE='1')
        frozen = inputs.freeze(self.project, 'r', 'same actual INCAR')
        self.assertEqual(sources.draft_differences(self.project, 'r', frozen['id'])['differences'], [])

    def test_refresh_uses_final_ionic_iteration_and_numeric_electronic_step(self):
        root = self.roots['relax']
        (root / 'INCAR').write_text('EDIFF = 1e-6\n')
        (root / 'OSZICAR').write_text('DAV: 1 -10 1e-8 1e-8\n 2 F= -10\n')
        (root / '.exit-status').write_text('0\n')
        early = ' Iteration 1( 1)\nreached required accuracy\n Iteration 2( 1)\n'
        def local(alias, command, **kwargs):
            result = subprocess.run(['bash', '-c', command], capture_output=True)
            return {'exit_code': result.returncode, 'stdout': result.stdout.decode(), 'stderr': result.stderr.decode()}
        for last, expected in (('', False), ('reached required accuracy\n', True)):
            with self.subTest(final_accuracy=expected):
                (root / 'OUTCAR').write_text(early+last+'General timing and accounting informations\n')
                record = copy.deepcopy(self.source); record['state'] = 'RUNNING'; self.store(record)
                with patch.object(ex, 'ssh', side_effect=local), patch.object(ex, 'pbs_history_command', return_value="printf 'job_state = C\\n'\n"):
                    refreshed = ex.refresh(record['id'])
                self.assertEqual(refreshed['evidence']['ionic_converged'], expected)
                self.assertTrue(sources.electronic(refreshed['evidence']))


class GeometryAndReceipts(unittest.TestCase):
    def test_print_rounding_periodicity_and_atom_order(self):
        from pathlib import Path
        from backend.structures import parse_structure
        a = parse_structure('POSCAR', 'poscar', base64.b64encode((Path(__file__).parent / 'fixtures/Si.POSCAR').read_bytes()).decode())
        b = copy.deepcopy(a); b['lattice'][0][0] += 5e-7; b['atoms'][0]['fractional'][0] += 1+5e-7
        self.assertTrue(sources.compare_geometry(a, b))
        b['lattice'][0][0] += 2e-6; self.assertFalse(sources.compare_geometry(a, b))
        b = copy.deepcopy(a); b['atoms'][0]['element'] = 'Fe'
        with self.assertRaisesRegex(ValueError, '顺序'): sources.compare_geometry(a, b)
        b = copy.deepcopy(a); b['atoms'].reverse()
        with self.assertRaisesRegex(ValueError, '顺序'): sources.assert_no_permutation(a, b)

    def test_wave_header_actual_counts_and_truncation(self):
        receipt = {'exit_code': 0, 'stdout': 'FPILOT_WAVE_FIRST=128 1 45200\nFPILOT_WAVE_SECOND=2 4 400 1 0 0 0 1 0 0 0 1\nFPILOT_WAVE_SIZE=1536\n'}
        header = source_files.wave_header('one', '/safe', lambda *a, **k: receipt)
        self.assertEqual((header['nkpoints'], header['nbands']), (2, 4))
        sources.validate_wave_parallel({'wavefunction_header': header}, b'KPAR = 1\nNCORE = 1\n', 4)
        with self.assertRaisesRegex(ValueError, 'NBANDS'): sources.validate_wave_parallel({'wavefunction_header': header}, b'NCORE = 1\n', 3)
        with self.assertRaisesRegex(ValueError, '不完整'): source_files.wave_header('one', '/safe', lambda *a, **k: {**receipt, 'stdout': receipt['stdout'].replace('1536', '128')})

    def test_charge_header_is_bounded_before_grid(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp); raw = (Path(__file__).parent / 'fixtures/Si.POSCAR').read_bytes()
            (path / 'CHGCAR').write_bytes(raw + b'\n100 100 100\n' + b' 1.0 '*10000)
            def local(alias, command, **kwargs):
                result = subprocess.run(['bash', '-c', command], capture_output=True)
                return {'exit_code': result.returncode, 'stdout': result.stdout.decode()}
            header = source_files.structure_header('one', str(path), 'CHGCAR', local)
            self.assertNotIn(b'100 100 100', header)
            self.assertTrue(sources.compare_geometry(sources.structure_geometry(raw), sources.structure_geometry(header)))
