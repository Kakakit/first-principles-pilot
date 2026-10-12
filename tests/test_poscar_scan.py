import copy
import hashlib
import unittest
import numpy as np

import test_scans
import test_source_contract_v2
from backend import scans, inputs, execution as ex, source_contracts as sources
from backend.scan_geometry import transform
from backend.structure_edit import edit_poscar
from test_poscar_edit import DIRECT, parse


class PoscarScanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_scans.ScanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.project = self.fixture.project

    def test_volume_cases_freeze_distinct_poscars_without_changing_base(self):
        before = copy.deepcopy(self.project)
        dims = [{'kind': 'poscar', 'mode': 'volume_ratio', 'start': '0.9', 'end': '1.1', 'step': '0.02'}]
        report = scans.preview(self.project, 'scan', 'c', dims)
        self.assertTrue(report['ready'])
        self.assertEqual(report['case_count'], 11)
        self.assertEqual(len({c['input_hashes']['POSCAR'] for c in report['cases']}), 11)
        self.assertEqual(len({c['input_hashes']['INCAR'] for c in report['cases']}), 1)
        batch = scans.freeze(self.project, 'scan', 'c', dims, 'EOS', report['definition_hash'])
        for case in batch['cases']:
            manifest, files = ex.bundle(case['bundle_id'])
            actual = parse(files['POSCAR'].decode())
            self.assertAlmostEqual(actual['volume'], case['geometry']['volume_A3'])
            self.assertLess(float(files['POSCAR'].decode().splitlines()[1]), 0)
            self.assertEqual(manifest['poscar_scan']['poscar_sha256'], hashlib.sha256(files['POSCAR']).hexdigest())
            self.assertEqual(manifest['execution_scope'], 'scan')
            self.assertEqual(manifest['source_contract']['structure'], 'configured')
        self.assertEqual(self.project, before)

    def test_target_volume_length_factor_and_existing_scale_semantics(self):
        for initial in ('2', '-48', '2 3 4'):
            for coordinate_mode in ('Direct', 'Cartesian'):
                raw = DIRECT.replace('\n1\n', '\n'+initial+'\n', 1).replace('Direct', coordinate_mode)
                old = parse(raw)
                for mode, value, expected in [('volume', '52', 52), ('volume_ratio', '1.1', old['volume']*1.1), ('length_scale', '1.01', old['volume']*1.01**3)]:
                    text, summary = transform(raw, mode, value)
                    new = parse(text)
                    self.assertAlmostEqual(new['volume'], expected)
                    np.testing.assert_allclose([a['fractional'] for a in old['atoms']], [a['fractional'] for a in new['atoms']], atol=1e-12)
                    self.assertEqual(new['atoms'][0]['selective'], old['atoms'][0]['selective'])
                    self.assertAlmostEqual(summary['volume_A3'], expected)

    def test_moments_constraints_and_combined_scan_survive(self):
        doc = parse(DIRECT)
        self.project['nodes'][0]['data']['structure'] = edit_poscar(doc, DIRECT.replace('\n1\n', '\n-48\n', 1))['structure']
        p = self.project
        # Preview a case project before POTCAR/species audit; identity and inputs
        # remain independent of the chosen electronic parameter dimension.
        ids = [a['id'] for a in p['nodes'][0]['data']['structure']['atoms']]
        p['parameters']['shared']['moments'] = dict.fromkeys(ids, '2')
        case = scans.case_project(p, 'c', [{'kind': 'poscar', 'mode': 'volume'}, {'kind': 'incar', 'tag': 'ENCUT'}], ['60', '500'])
        self.assertEqual([a['id'] for a in case['nodes'][0]['data']['structure']['atoms']], ids)
        self.assertEqual(case['parameters']['shared']['moments'], dict.fromkeys(ids, '2'))
        self.assertEqual(case['nodes'][0]['data']['structure']['atoms'][0]['selective'], [True, False, True])
        self.assertEqual(case['nodes'][1]['data']['parameters']['values']['ENCUT'], '500')
        self.assertAlmostEqual(case['nodes'][0]['data']['structure']['volume'], 60)

    def test_positive_values_limits_duplicate_dimensions_and_stale_geometry(self):
        for value in ('0', '-1', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError): scans.axis({'kind': 'poscar', 'mode': 'volume', 'start': value, 'end': value, 'step': '1'}, 'relax')
        dims = [{'kind': 'poscar', 'mode': 'volume', 'start': '20', 'end': '40', 'step': '1'}]
        with self.assertRaisesRegex(ValueError, '重复'): scans.preview(self.project, 'scan', 'c', dims+dims)
        with self.assertRaisesRegex(ValueError, '64'): scans.preview(self.project, 'scan', 'c', dims+[{'kind':'kmesh','meshes':[[2,2,2],[3,3,3],[4,4,4],[5,5,5]]}])
        case = scans.case_project(self.project, 'c', dims, ['30'])
        case['nodes'][1]['data']['poscarScan']['poscar_sha256'] = '0'*64
        self.assertTrue(any('扫描记录' in e for e in inputs.generate(case, 'c')['errors']))


class UpstreamPoscarScanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_source_contract_v2.SourceContractV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.project['nodes'].append({'id':'scan','data':{'kind':'scan','executionMode':'scan','stageSequence':4,'stageSlug':'scan'}})
        self.project['edges'].append({'source':'c','target':'scan'})
        self.project['nodes'][2]['data']['sourceDraft'] = {'mode':'new','purpose':'charge','files':'charge'}
        self.dims = [{'kind':'poscar','mode':'volume_ratio','start':'0.9','end':'1.1','step':'0.1'}]
        file = self.fixture.roots['relax'] / 'CONTCAR'
        lines = file.read_text().splitlines(); lines[1] = '1.2'; file.write_text('\n'.join(lines)+'\n')

    def test_actual_contcar_baseline_is_scaled_once_and_never_copied_over_case(self):
        report = scans.preview(self.project,'scan','c',self.dims)
        actual_volume = parse((self.fixture.roots['relax']/'CONTCAR').read_text())['volume']
        self.assertTrue(report['ready'])
        self.assertAlmostEqual(report['cases'][0]['geometry']['volume_A3'], actual_volume*.9)
        batch = scans.freeze(self.project,'scan','c',self.dims,'upstream EOS',report['definition_hash'])
        for case in batch['cases']:
            manifest, files = ex.bundle(case['bundle_id'])
            contract = manifest['source_contract']
            self.assertEqual(contract['roles']['structure']['usage'], 'geometry-reference')
            self.assertEqual(contract['roles']['structure']['attempt_id'], 'relax')
            snapshot = sources.materialize(contract,'one')
            self.assertEqual(ex.source_file_plan(snapshot,files['INCAR']), {})
            self.assertAlmostEqual(parse(files['POSCAR'].decode())['volume'], case['geometry']['volume_A3'])
        self.assertEqual([c['state'] for c in batch['cases']], ['PENDING']*3)

    def test_changed_remote_baseline_invalidates_preview_and_dispatch(self):
        report = scans.preview(self.project,'scan','c',self.dims)
        batch = scans.freeze(self.project,'scan','c',self.dims,'EOS',report['definition_hash'])
        file = self.fixture.roots['relax']/'CONTCAR'
        file.write_text(file.read_text().replace('\n1.2\n','\n1.3\n',1))
        with self.assertRaisesRegex(ValueError,'重新预览'): scans.freeze(self.project,'scan','c',self.dims,'EOS',report['definition_hash'])
        manifest, _ = ex.bundle(batch['cases'][0]['bundle_id'])
        with self.assertRaisesRegex(ValueError,'已变化'): sources.materialize(manifest['source_contract'],'one')

    def test_charge_initialization_keeps_charge_copy_but_preserves_scaled_poscar(self):
        self.fixture.make_source('scf', 'c')
        child = self.fixture.add_child()
        self.project['edges'] = [e for e in self.project['edges'] if e['target'] != 'scan'] + [{'source':'next','target':'scan'}]
        report = scans.preview(self.project,'scan',child['id'],self.dims)
        batch = scans.freeze(self.project,'scan',child['id'],self.dims,'charge EOS',report['definition_hash'])
        manifest, files = ex.bundle(batch['cases'][0]['bundle_id'])
        self.assertEqual(manifest['source_contract']['initialization'], {'ISTART':0,'ICHARG':1})
        snapshot = sources.materialize(manifest['source_contract'],'one')
        plan = ex.source_file_plan(snapshot, files['INCAR'])
        self.assertEqual(set(plan), {'CHGCAR'})
        self.assertEqual(plan['CHGCAR']['mode'], 'reflink-or-copy')
        self.assertTrue(any('初值外推' in w for w in manifest['source_contract']['warnings']))
        self.assertAlmostEqual(parse(files['POSCAR'].decode())['volume'], batch['cases'][0]['geometry']['volume_A3'])

    def test_wave_restart_and_unknown_source_remain_blocked(self):
        node = self.project['nodes'][2]
        node['data']['sourceDraft'].update(mode='upstream',files='both')
        with self.assertRaisesRegex(ValueError,'WAVECAR'): scans.preview(self.project,'scan','c',self.dims)
        node['data']['sourceDraft'].update(mode='new',files='charge')
        rec = self.fixture.source; rec['state']='UNKNOWN'; self.fixture.store(rec)
        with self.assertRaisesRegex(ValueError,'来源不可用'): scans.preview(self.project,'scan','c',self.dims)
