"""Local comparison uses only explicitly selected, small output files."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import wannier_compare as compare


FIXTURE = Path(__file__).resolve().parents[1] / 'resources/extensions/electronic-structure-characterization/fixtures/minimal'


class WannierCompareTest(unittest.TestCase):
    def test_repeated_generate_shares_running_and_completed_operation(self):
        from backend import analysis_queue
        pending=[]
        class Executor:
            def submit(self,work):pending.append(work)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);identifier='c'*32;calls=[]
            def generate(**options):
                calls.append(options)
                directory=root/identifier;directory.mkdir()
                result={'id':identifier,'project_id':'project'}
                (directory/'record.json').write_text(json.dumps(result))
                options['on_plot_ready'](result)
                return result
            with patch.object(compare,'ROOT_COMPARE',root),patch.object(compare,'JOBS',{}), \
                 patch.object(analysis_queue,'executor',Executor()),patch.object(compare,'run',side_effect=generate), \
                 patch.object(compare.execution,'get',side_effect=lambda key:{'id':key}), \
                 patch.object(compare.recycle,'assert_available'):
                options={'project_id':'project','wannier_attempt_id':'model','band_attempt_id':'band'}
                first=compare.start(**options);second=compare.start(**options)
                self.assertEqual(first['id'],second['id'])
                self.assertEqual(len(pending),1)
                pending.pop()()
                cached=compare.start(**options)
                self.assertTrue(cached['cached'])
                self.assertEqual(cached['result']['id'],identifier)
                self.assertEqual(len(calls),1)
                self.assertTrue(cached['plot_ready'])

    def test_two_panel_fixture_saves_local_figure_and_curves_without_suggestions(self):
        records = {
            'wannier': {'id': 'wannier', 'kind': 'wannier90', 'state': 'COMPLETED',
                        'project_id': 'project', 'bundle_id': 'w90', 'config': {'alias': 'jutai-1'},
                        'evidence': {'wannier_all_done': True}},
            'parent': {'id': 'parent', 'project_id': 'project', 'config': {'alias': 'jutai-1'},
                       'dependency_snapshot': {'upstream_attempt_id': 'scf'}},
            'bands': {'id': 'bands', 'state': 'COMPLETED', 'project_id': 'project',
                      'bundle_id': 'vasp-band', 'config': {'alias': 'jutai-1'},
                      'dependency_snapshot': {'upstream_attempt_id': 'scf'},
                      'evidence': {'vasp_timing': True}},
            'scf': {'id': 'scf', 'project_id': 'project', 'config': {'alias': 'jutai-1'}},
        }
        names = {'bands': {'EIGENVAL', 'KPOINTS', 'POSCAR'}, 'scf': {'OUTCAR'},
                 'wannier': {'wannier90.1_band.dat', 'wannier90.1_band.labelinfo.dat'}}
        source = lambda name: FIXTURE / ('BAND_OUTCAR' if name == 'OUTCAR' else name)

        def listing(identifier):
            return {'files': [{'path': name, 'size_bytes': source(name).stat().st_size}
                              for name in names[identifier]]}

        def pull(identifier, name):
            self.assertIn(name, names[identifier])
            return source(name).read_bytes(), {}

        ready=[]
        def shown(record):
            self.assertTrue(Path(record['local_path'],'comparison.png').is_file())
            ready.append(record['id'])

        def failed_native(directory,record,*args):
            self.assertEqual(ready,[record['id']])
            return {'status':'FAILED','reason':'isolated diagnostic failure'}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'wannier-bundles').mkdir()
            (root / 'wannier-bundles/w90.json').write_text(json.dumps({
                'project_id': 'project', 'parent_attempt_id': 'parent', 'seed': 'wannier90.1',
                'band_path': {'band_attempt_id': 'bands', 'bands_num_points': 3,
                              'path_lines': ['GAMMA 0 0 0 X 0.5 0 0', 'X 0.5 0 0 M 0.5 0.5 0'], 'kpoints_sha256':hashlib.sha256(source('KPOINTS').read_bytes()).hexdigest(),'poscar_sha256':hashlib.sha256(source('POSCAR').read_bytes()).hexdigest()}}))
            data={'sources':[{'name':n} for n in ('EIGENVAL','KPOINTS','POSCAR','OUTCAR')],'metadata':{'energy_reference':{'fermi_eV':compare.read_fermi_from_outcar(source('OUTCAR'))}}}
            for name in ('EIGENVAL','KPOINTS','POSCAR'):data[name.lower()+'_text']=source(name).read_text()
            with patch.object(compare.compact_plots,'fetch',return_value=data),patch.object(compare,'_wannier_data',return_value={'directory':str(FIXTURE),'sources':[{'name':n} for n in names['wannier']]}),patch.object(compare, 'ROOT', root), patch.object(compare, 'ROOT_COMPARE', root / 'wannier-comparisons'), \
                 patch.object(compare.execution, 'get', side_effect=records.__getitem__), \
                 patch.object(compare.execution, 'bundle', return_value=({'stage': 'bands'}, {})), \
                 patch.object(compare.artifacts, 'refresh', side_effect=listing), \
                 patch.object(compare.artifacts, 'pull', side_effect=pull), \
                 patch.object(compare.wannier_bands, 'recheck'),patch.object(compare.diagnostics,'native',side_effect=failed_native):
                result = compare.run('project', 'wannier', 'bands',diagnostic=True,on_plot_ready=shown)
                self.assertEqual(len(result['sources']), 6)
                self.assertEqual(result['seed'], 'wannier90.1')
                self.assertTrue(compare.output('project', result['id'], 'comparison.png').startswith(b'\x89PNG'))
                self.assertIn(b'DFT', compare.output('project', result['id'], 'curves.csv'))
                self.assertEqual(compare.saved('project')[0]['id'], result['id'])
                self.assertEqual(result['pyw90_diagnostic']['status'],'FAILED')
                self.assertIn('groups',result['diagnostics'])
                self.assertEqual(json.loads(compare.output('project',result['id'],'diagnostics.json'))['pyw90_diagnostic']['status'],'FAILED')


if __name__ == '__main__':
    unittest.main()
