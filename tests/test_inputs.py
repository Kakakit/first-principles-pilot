"""Synthetic POTCAR headers test contracts only, not VASP executability."""
import base64
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from backend import inputs, execution


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = inputs.ROOT
        inputs.ROOT = Path(self.temp.name)
        structure = json.loads((Path(__file__).parent / 'fixtures/Si.structure.json').read_text())
        self.potcar = b'TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\nENMAX = 245.0;\nEnd of Dataset\n'
        key = inputs.upload_potcar(base64.b64encode(self.potcar).decode())['id']
        self.project = {'id': 'p', 'name': 'test', 'remoteSlug': 'test', 'nodes': [
            {'id': 's', 'data': {'kind': 'structure', 'structure': structure}},
            {'id': 'c', 'data': {'kind': 'scf', 'parameters': {'preset': 'basic', 'values': {}},
                                'inputDraft': {'grid': '4 4 4', 'center': 'Gamma', 'potcar_id': key}}}],
            'edges': [{'source': 's', 'target': 'c'}],
            'parameters': {'shared': {'values': {'XC_FUNCTIONAL': 'PBE', 'ENCUT': '400', 'EDIFF': '1e-6',
                'ISPIN': '1', 'ISMEAR': '0', 'SIGMA': '0.05', 'LDAU': 'false'}}}}
        for sequence, node in enumerate(self.project['nodes'], 1):
            node['data'].update(executionMode='vasp', stageSequence=sequence, stageSlug=node['data']['kind'])

    def tearDown(self):
        inputs.ROOT = self.old
        self.temp.cleanup()

    def test_known_static_and_regenerated_geometry(self):
        self.project['nodes'][0]['data']['structure']['poscar'] = 'not a POSCAR'
        result = inputs.generate(self.project, 'c')
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['ready'])
        self.assertIn('GGA = PE\n', result['files']['INCAR'])
        self.assertNotIn('XC_FUNCTIONAL', result['files']['INCAR'])
        self.assertEqual(result['files']['KPOINTS'], 'Regular mesh\n0\nGamma\n4 4 4\n0 0 0\n')
        self.assertNotEqual(result['files']['POSCAR'], 'not a POSCAR')

    def test_mesh_generation_information_is_optional_frozen_and_never_recalculates_old_grid(self):
        from backend import kpoints
        lattice=self.project['nodes'][0]['data']['structure']['lattice']
        suggestion=kpoints.suggest(lattice,.04)
        draft=self.project['nodes'][1]['data']['inputDraft']
        draft.update(grid=' '.join(map(str,suggestion['grid'])),center=suggestion['center'],kmesh_info=json.dumps({**suggestion,'lattice':lattice,'source':{'kind':'reference','label':'导入结构'}}))
        report=inputs.generate(self.project,'c')
        self.assertFalse(report['errors']);self.assertEqual(report['kmesh_generation']['grid'],suggestion['grid'])
        frozen=inputs.freeze(self.project,'c','test mesh provenance')
        self.assertEqual(frozen['manifest']['kmesh_generation'],report['kmesh_generation'])
        original=(inputs.ROOT/'inputs'/(frozen['id']+'.zip')).read_bytes()
        draft.update(grid='8 8 8');draft.pop('kmesh_info')
        updated=inputs.generate(self.project,'c');self.assertNotIn('kmesh_generation',updated)
        self.assertIn('8 8 8',updated['files']['KPOINTS'])
        self.assertEqual((inputs.ROOT/'inputs'/(frozen['id']+'.zip')).read_bytes(),original)

    def test_catalog_typed_passthrough_custom_and_restart_contract(self):
        layer=self.project['nodes'][1]['data']['parameters']
        layer['values'].update(ALGO='All',NBANDS='16',
                               LWAVE='true',LCHARG='true',LASPH='true',LREAL='Auto',ISYM='0',
                               NCORE='2',KPAR='2',ADDGRID='.TRUE.')
        report=inputs.generate(self.project,'c')
        self.assertEqual(report['errors'],[])
        self.assertIn('ALGO = All\n',report['files']['INCAR'])
        self.assertIn('ADDGRID = .TRUE.\n',report['files']['INCAR'])
        self.assertIn('LWAVE = .TRUE.\n',report['files']['INCAR'])
        self.assertEqual(report['parameters']['ADDGRID']['validation'],'official-passthrough')
        self.assertEqual(report['incar_tags']['LWAVE']['value'],'.TRUE.')
        self.assertEqual(report['incar_tags']['GGA']['source'],'XC_FUNCTIONAL')
        self.assertEqual(len(report['parameter_catalog']['revision']),64)
        layer['values'].update(NEDOS='501',EMIN='-5',EMAX='5')
        self.assertTrue(inputs.generate(self.project,'c')['errors'])
        for tag in ('NEDOS','EMIN','EMAX'): del layer['values'][tag]
        layer['values']['MYTAG']='7'
        self.assertTrue(inputs.generate(self.project,'c')['errors'])
        layer['confirmed_custom_tags']=['MYTAG']
        self.assertEqual(inputs.generate(self.project,'c')['parameters']['MYTAG']['validation'],'unverified-custom')
        for key,value in [('MYTAG','1; LCHARG=.FALSE.'),('ISTART','1'),('ICHARG','11')]:
            layer['values'][key]=value
            self.assertTrue(inputs.generate(self.project,'c')['errors'],key)
            layer['values'][key]='0' if key=='ISTART' else '2' if key=='ICHARG' else '7'
        layer['values']['EMAX']='-6'
        self.assertTrue(inputs.generate(self.project,'c')['errors'])

    def test_parallel_parameters_match_selected_mpi_ranks(self):
        execution.validate_parallel('ENCUT = 400\nKPAR = 3\nNCORE = 2\n',12)
        with self.assertRaisesRegex(ValueError,'不整除'):
            execution.validate_parallel('KPAR = 3\nNCORE = 2\n',16)
        with self.assertRaisesRegex(ValueError,'正整数'):
            execution.validate_parallel('KPAR = 0\n',16)

    def test_reject_missing_invalid_and_masked_parameters(self):
        for key, value in [('ENCUT', None), ('ENCUT', '-1'), ('ENCUT', 'NaN'), ('NSW', '3'), ('SOC', 'true')]:
            p = copy.deepcopy(self.project)
            p['nodes'][1]['data']['parameters']['values'][key] = value
            self.assertTrue(inputs.generate(p, 'c')['errors'], (key, value))
            with self.assertRaises(ValueError):
                inputs.freeze(p, 'c', 'test reason')

    def test_potcar_order_xc_and_corruption(self):
        for raw in [self.potcar.replace(b'PBE Si', b'PBE Fe'), self.potcar.replace(b'PE\n', b'CA\n')]:
            key = inputs.upload_potcar(base64.b64encode(raw).decode())['id']
            self.project['nodes'][1]['data']['inputDraft']['potcar_id'] = key
            self.assertTrue(inputs.generate(self.project, 'c')['errors'])
        (inputs.ROOT / 'potcars' / key).write_bytes(b'broken')
        self.assertTrue(inputs.generate(self.project, 'c')['errors'])

    def test_moments_atom_order_and_hubbard(self):
        shared = self.project['parameters']['shared']
        shared['values'].update(ISPIN='2', LDAU='true', LDAUTYPE='2', LMAXMIX='2')
        atoms = self.project['nodes'][0]['data']['structure']['atoms']
        shared['moments'] = {atoms[1]['id']: '-1', atoms[0]['id']: '1'}
        shared['hubbard'] = {'Si': {'l': '1', 'u': '2', 'j': '0.5'}}
        report = inputs.generate(self.project, 'c')
        self.assertEqual(report['errors'], [])
        self.assertIn('MAGMOM = 1.0 -1.0\n', report['files']['INCAR'])
        self.assertIn('LDAUU = 2.0\n', report['files']['INCAR'])
        del shared['moments'][atoms[0]['id']]
        self.assertTrue(inputs.generate(self.project, 'c')['errors'])

    def test_relax_and_pending_downstream(self):
        relax = self.project['nodes'][1]
        relax['data']['kind'] = 'relax'
        relax['data']['parameters']['values'].update(NSW='80', EDIFFG='-0.02')
        self.assertTrue(inputs.generate(self.project, 'c')['ready'])
        scf = copy.deepcopy(relax); scf['id'] = 'd'; scf['data']['kind'] = 'scf'
        scf['data']['parameters']['values'] = {}
        self.project['nodes'].append(scf); self.project['edges'].append({'source': 'c', 'target': 'd'})
        report = inputs.generate(self.project, 'd')
        self.assertEqual(report['errors'], [])
        self.assertFalse(report['ready'])
        self.assertNotIn('POSCAR', report['files'])
        self.assertEqual(report['dependencies'][0]['file'], 'CONTCAR')

    def test_bands_charge_dependency_and_bad_kpoints(self):
        bands = copy.deepcopy(self.project['nodes'][1]); bands['id'] = 'b'; bands['data']['kind'] = 'bands'
        bands['data']['inputDraft'].update(line_points='20', path='0 0 0 G\n0.5 0 0 X')
        self.project['nodes'].append(bands); self.project['edges'].append({'source': 'c', 'target': 'b'})
        report = inputs.generate(self.project, 'b')
        self.assertEqual(report['errors'], [])
        self.assertIn('ICHARG = 11', report['files']['INCAR'])
        self.assertEqual([d['file'] for d in report['dependencies']], ['POSCAR', 'CHGCAR'])
        bands['data']['inputDraft']['path'] = '0 0 0'
        self.assertTrue(inputs.generate(self.project, 'b')['errors'])

    def test_dos_window_is_typed_and_preserves_charge_contract(self):
        import copy
        dos=copy.deepcopy(self.project['nodes'][1]);dos['id']='d';dos['data']['kind']='dos'
        dos['data']['parameters']['values'].update(NEDOS='501',EMIN='-5',EMAX='5',ICHARG='11')
        self.project['nodes'].append(dos);self.project['edges'].append({'source':'c','target':'d'})
        report=inputs.generate(self.project,'d')
        self.assertEqual(report['errors'],[])
        self.assertIn('EMIN = -5\n',report['files']['INCAR'])
        self.assertIn('ICHARG = 11\n',report['files']['INCAR'])
        self.assertEqual([d['file'] for d in report['dependencies']],['POSCAR','CHGCAR'])

    def test_project_history_isolated_before_recent_limit_and_recoverable(self):
        first=inputs.freeze(self.project,'c','project one')
        other=copy.deepcopy(self.project);other['id']='other';other['name']='other';other['remoteSlug']='other'
        second=inputs.freeze(other,'c','project two')
        self.assertEqual([row['id'] for row in inputs.history('p')],[first['id']])
        self.assertFalse(inputs.history('p')[0]['requires_ncl'])
        self.assertEqual([row['id'] for row in inputs.history('other')],[second['id']])
        marker=inputs.ROOT/'recycle'/'inputs'/first['id'];marker.parent.mkdir(parents=True);marker.write_text('{}')
        self.assertEqual(inputs.history('p'),[])
        marker.unlink()
        self.assertEqual(inputs.history('p')[0]['id'],first['id'])

    def test_history_reports_runtime_requirement_from_frozen_incar(self):
        values=self.project['parameters']['shared']['values']
        values.update(ISPIN='1', LSORBIT='true', LNONCOLLINEAR='true', SAXIS='0 0 1')
        self.project['nodes'][1]['data']['parameters']['moments']={atom['id']:'0 0 1' for atom in self.project['nodes'][0]['data']['structure']['atoms']}
        frozen=inputs.freeze(self.project,'c','SOC runtime review')
        row=next(item for item in inputs.history('p') if item['id']==frozen['id'])
        self.assertTrue(row['requires_ncl'])

    def test_shared_potcar_and_dos_managed_charge(self):
        key=self.project['nodes'][1]['data']['inputDraft'].pop('potcar_id')
        self.project['sharedPotcar']={'id':key}
        self.project['parameters']['shared']['values']['ICHARG']='2'
        dos=copy.deepcopy(self.project['nodes'][1]);dos['id']='d';dos['data']['kind']='dos'
        self.project['nodes'].append(dos);self.project['edges'].append({'source':'c','target':'d'})
        report=inputs.generate(self.project,'d')
        self.assertEqual(report['errors'],[])
        self.assertEqual(report['potcar_source'],'项目共享')
        self.assertIn('ICHARG = 11\n',report['files']['INCAR'])
        self.assertEqual(report['incar_tags']['ICHARG']['source'],'计算来源合同')
        self.assertIn('LORBIT = 11\n',report['files']['INCAR'])
        self.assertEqual(report['incar_tags']['LORBIT']['source'],'节点执行合同')
        dos['data']['parameters']['values']['LORBIT']='10'
        self.assertIn('LORBIT = 10\n',inputs.generate(self.project,'d')['files']['INCAR'])

    def test_soc_and_noncollinear_require_vector_moments(self):
        shared=self.project['parameters']['shared']
        atoms=self.project['nodes'][0]['data']['structure']['atoms']
        shared['values'].update(LSORBIT='true',SAXIS='0 0 1',GGA_COMPAT='false',LORBMOM='true',LASPH='true')
        shared['moments']={atoms[0]['id']:'0 0 4',atoms[1]['id']:'0 0 -4'}
        result=inputs.generate(self.project,'c')
        self.assertEqual(result['errors'],[])
        incar=result['files']['INCAR']
        for tag,value in [('LSORBIT','.TRUE.'),('SAXIS','0 0 1'),('GGA_COMPAT','.FALSE.'),
                          ('LORBMOM','.TRUE.'),('MAGMOM','0.0 0.0 4.0 0.0 0.0 -4.0')]:
            self.assertIn(f'{tag} = {value}\n',incar)
        with tempfile.TemporaryDirectory() as directory:
            for name, text in result['files'].items():
                Path(directory, 'POSCAR' if name=='POSCAR.reference' else name).write_text(text)
            Path(directory,'POTCAR').write_bytes(self.potcar)
            self.assertEqual(execution.servers.pbs.audit_inputs(directory,profile='static',executable='vasp_ncl')['status'],'PASS')
            self.assertEqual(execution.servers.pbs.audit_inputs(directory,profile='static',executable='vasp_std')['status'],'FAIL')
        shared['values']['ISPIN']='2'
        self.assertTrue(any('ISPIN=1' in error for error in inputs.generate(self.project,'c')['errors']))
        shared['values']['ISPIN']='1'
        shared['moments'][atoms[1]['id']]='-4'
        self.assertTrue(any('三个初始 MAGMOM' in error for error in inputs.generate(self.project,'c')['errors']))
        shared['moments'][atoms[1]['id']]='0 0 -4'
        shared['values']['SAXIS']='0 0 0'
        self.assertTrue(any('SAXIS' in error for error in inputs.generate(self.project,'c')['errors']))

    def test_wannier_projection_count_is_per_channel(self):
        wannier=copy.deepcopy(self.project['nodes'][1]);wannier['id']='w';wannier['data']['kind']='vasp-wannier'
        wannier['data']['stageSlug']='vasp-wannier';wannier['data']['inputDraft'].update(projections='Si:s\nSi:p',num_wann='8')
        self.project['nodes'].append(wannier);self.project['edges'].append({'source':'c','target':'w'})
        for spin in ('1','2'):
            self.project['parameters']['shared']['values']['ISPIN']=spin
            if spin=='2':
                self.project['parameters']['shared']['moments']={a['id']:'0' for a in self.project['nodes'][0]['data']['structure']['atoms']}
            report=inputs.generate(self.project,'w')
            self.assertEqual(report['errors'],[])
            self.assertEqual(report['incar_tags']['NUM_WANN']['value'],'8')
            self.assertIn('Begin Projections\nSi:s\nSi:p\nEnd Projections',report['files']['INCAR'])
            self.assertIn('ICHARG = 11\n',report['files']['INCAR'])
        wannier['data']['inputDraft']['projections']='Si:q'
        self.assertTrue(inputs.generate(self.project,'w')['errors'])

    def test_soc_vasp_wannier_projection_input_is_not_artificially_blocked(self):
        wannier=copy.deepcopy(self.project['nodes'][1]);wannier['id']='w';wannier['data']['kind']='vasp-wannier'
        wannier['data']['stageSlug']='vasp-wannier';wannier['data']['inputDraft'].update(projections='Si:s\nSi:p',num_wann='16')
        self.project['nodes'].append(wannier);self.project['edges'].append({'source':'c','target':'w'})
        atoms=self.project['nodes'][0]['data']['structure']['atoms']
        self.project['parameters']['shared']['values'].update(LSORBIT='true',LNONCOLLINEAR='true',SAXIS='0 0 1')
        self.project['parameters']['shared']['moments']={atom['id']:'0 0 2' for atom in atoms}
        report=inputs.generate(self.project,'w')
        self.assertEqual(report['errors'],[])
        self.assertIn('LSORBIT = .TRUE.\n',report['files']['INCAR'])
        self.assertIn('LNONCOLLINEAR = .TRUE.\n',report['files']['INCAR'])
        self.assertEqual(report['incar_tags']['NUM_WANN']['value'],'16')
        wannier['data']['inputDraft']['num_wann']=''
        self.assertTrue(any('NUM_WANN' in error for error in inputs.generate(self.project,'w')['errors']))

    def test_freeze_is_deterministic_independent_and_verified(self):
        first = inputs.freeze(self.project, 'c', 'synthetic test only')
        again = inputs.freeze(self.project, 'c', 'synthetic test only')
        self.assertEqual(first['id'], again['id'])
        raw = (inputs.ROOT / 'inputs' / (first['id'] + '.zip')).read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for name, meta in manifest['files'].items():
                self.assertEqual(inputs.digest(archive.read(name)), meta['sha256'])
        self.project['parameters']['shared']['values']['ENCUT'] = '500'
        changed = inputs.freeze(self.project, 'c', 'new settings')
        self.assertNotEqual(first['id'], changed['id'])
        self.assertEqual(raw, (inputs.ROOT / 'inputs' / (first['id'] + '.zip')).read_bytes())

    def test_fixed_charge_reports_parent_model_changes_for_review(self):
        bands = copy.deepcopy(self.project['nodes'][1]); bands['id'] = 'b'; bands['data']['kind'] = 'bands'
        bands['data']['inputDraft'].update(line_points='20', path='0 0 0 G\n0.5 0 0 X')
        bands['data']['parameters']['values']['ENCUT'] = '500'
        self.project['nodes'].append(bands); self.project['edges'].append({'source': 'c', 'target': 'b'})
        report = inputs.generate(self.project, 'b')
        self.assertEqual(report['errors'], [])
        self.assertTrue(any('ENCUT' in warning for warning in report['warnings']))

    def test_scf_from_scf_declares_charge_handoff(self):
        child = copy.deepcopy(self.project['nodes'][1])
        child['id'] = 'pol'
        child['data']['parameters']['values'].update(ICHARG='1', LCALCPOL='true')
        self.project['nodes'].append(child)
        self.project['edges'].append({'source': 'c', 'target': 'pol'})
        report = inputs.generate(self.project, 'pol')
        self.assertEqual(report['errors'], [])
        self.assertEqual([item['file'] for item in report['dependencies']], ['POSCAR', 'CHGCAR'])
        self.assertIn('ICHARG = 1\n', report['files']['INCAR'])
        self.assertIn('ISTART = 0\n', report['files']['INCAR'])
        self.assertFalse(report['restart_required'])

    def test_assembly_uses_structure_order_not_selection_order(self):
        si = base64.b64encode(self.potcar).decode()
        fe = base64.b64encode(self.potcar.replace(b'PBE Si', b'PBE Fe')).decode()
        key = inputs.assemble_potcar([si, fe], ['Fe', 'Si'])['id']
        raw = inputs.potcar_bytes(key)
        self.assertLess(raw.index(b'PBE Fe'), raw.index(b'PBE Si'))
        with self.assertRaises(ValueError):
            inputs.assemble_potcar([si, si], ['Si'])
        with self.assertRaises(ValueError):
            inputs.assemble_potcar([si], ['Fe', 'Si'])


if __name__ == '__main__':
    unittest.main()
