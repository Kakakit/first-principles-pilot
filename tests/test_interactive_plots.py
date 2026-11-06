import tempfile
import unittest
import json
import base64
from pathlib import Path
from unittest.mock import patch

from backend import interactive_plots as plots


POSCAR = """test
1
1 0 0
0 1 0
0 0 1
Fe O
1 1
Direct
0 0 0
0.5 0.5 0.5
"""


class InteractivePlotTests(unittest.TestCase):
    def test_pband_file_groups_exclude_soc_magnetization_components(self):
        self.assertEqual(plots._pband_names(['Fe','O'],{
            'PBAND_Fe_SOC.dat','PBAND_O_SOC.dat','PBAND_Fe_SOC_SX.dat',
            'PBAND_Fe_SOC_SY.dat','PBAND_Fe_SOC_SZ.dat'}),
            ('soc',['PBAND_Fe_SOC.dat','PBAND_O_SOC.dat']))
        self.assertEqual(plots._pband_names(['Fe'],{'PBAND_Fe_UP.dat','PBAND_Fe_DW.dat'}),
                         ('spin2',['PBAND_Fe_UP.dat','PBAND_Fe_DW.dat']))
        self.assertEqual(plots._pband_names(['Fe'],{'PBAND_Fe.dat'}),('spin1',['PBAND_Fe.dat']))

    def test_pband_aligns_to_eigenval_and_rejects_incomplete_band(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'EIGENVAL').write_text('1 1 1 2\n'+'header\n'*4+'2 2 1\n\n'
                '0 0 0 1\n1 5 6 1 1\n\n0.5 0 0 1\n1 5.5 6.5 1 1\n')
            files=[]
            for suffix,energies in (('UP',(4,4.5)),('DW',(5,5.5))):
                path=root/f'PBAND_Fe_{suffix}.dat';files.append(path)
                path.write_text('#K-Path Energy s py pz px dxy dyz dz2 dxz x2-y2 tot\n'
                    '# NKPTS & NBANDS: 2 1\n# Band-Index 1\n'
                    +''.join(f'{x} {energy} 0 .1 .2 .3 .4 0 0 0 0 1\n'
                            for x,energy in zip((0,.5),energies)))
            info=plots._pband(files,['Fe'],'spin2',root/'EIGENVAL',root)
            self.assertEqual(info['spins'],2)
            self.assertAlmostEqual(info['pband_energy_offset_eV'],1)
            import numpy as np
            self.assertAlmostEqual(float(np.load(root/'band_weight.npy')[1,1,0,0,4]),.4)
            files[1].write_text(files[1].read_text().rsplit('\n',2)[0]+'\n')
            with self.assertRaisesRegex(ValueError,'缺少完整能带|不连续'):
                plots._pband(files,['Fe'],'spin2',root/'EIGENVAL',root)

    def test_pband_reverses_every_second_band_k_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'EIGENVAL').write_text('1 1 1 1\n'+'header\n'*4+'2 2 2\n\n'
                '0 0 0 1\n1 5 1\n2 8 0\n\n0.5 0 0 1\n1 6 1\n2 9 0\n')
            path=root/'PBAND_Fe.dat'
            path.write_text('#K-Path Energy s py pz px dxy dyz dz2 dxz x2-y2 tot\n'
                '# NKPTS & NBANDS: 2 2\n# Band-Index 1\n'
                '0 4 .1 0 0 0 0 0 0 0 0 .1\n'
                '.5 5 .2 0 0 0 0 0 0 0 0 .2\n'
                '# Band-Index 2\n'
                '.5 8 .9 0 0 0 0 0 0 0 0 .9\n'
                '0 7 .8 0 0 0 0 0 0 0 0 .8\n')
            plots._pband([path],['Fe'],'spin1',root/'EIGENVAL',root)
            import numpy as np
            weights=np.load(root/'band_weight.npy')
            self.assertAlmostEqual(float(weights[0,0,1,0,0]),.8)
            self.assertAlmostEqual(float(weights[0,1,1,0,0]),.9)

    def test_line_mode_kpoint_labels_follow_segment_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'KPOINTS'
            path.write_text('path\n3\nLine-mode\nReciprocal\n0 0 0 ! Γ\n0.5 0 0 ! X\n\n0.5 0 0 ! X\n0.5 0.5 0 ! M\n')
            self.assertEqual(plots._kpath_labels(path,6),[
                {'index':0,'label':'Γ'},{'index':2,'label':'X'},
                {'index':3,'label':'X'},{'index':5,'label':'M'}])

    def test_energy_reference_parses_precise_fermi_and_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'OUTCAR'
            path.write_text('E-fermi : 6.1794\nval. band max: 5.8460696248\ncond. band min: 7.4056836755\nFermi energy: 6.1793813970\n')
            result = plots._fermi(path)
            self.assertAlmostEqual(result['fermi_eV'], 6.179381397)
            self.assertAlmostEqual(result['vbm_eV'], 5.8460696248)
            self.assertAlmostEqual(result['cbm_eV'], 7.4056836755)
            path.write_text('E-fermi : 2.0\n')
            self.assertIsNone(plots._fermi(path)['vbm_eV'])

    def test_spin_resolved_procar_and_orbital_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            procar = root / 'PROCAR'
            (root/'attempt').mkdir()
            (root/'attempt'/'POSCAR').write_text(POSCAR)
            procar.write_text('PROCAR\n# of k-points: 1 # of bands: 1 # of ions: 2\n'
                              + ''.join(f'spin component {spin}\nk-point 1 : 0 0 0 weight = 1\n'
                                        f'band 1 # energy {energy} # occ. 1\nion s py pz px dxy dyz dz2 dxz dx2-y2 tot\n'
                                        '1 0 0.1 0.2 0.3 0.4 0 0 0 0 1\n'
                                        '2 0 0 0 0 0 0 0 0 0 0\ntot 0 0 0 0 0 0 0 0 0 0\n'
                                        for spin, energy in ((1, 1.0), (2, 2.0))))
            info = plots._procar(procar, ['Fe', 'O'], root, False)
            self.assertEqual(info['spins'], 2)
            self.assertIn('dxy', info['orbitals'])
            meta = {'state': 'READY', 'band_attempt_id':'attempt', 'band': {**info, 'energy_reference': {'fermi_eV': 1.0}}, 'dos': None}
            (root / 'metadata.json').write_text(json.dumps(meta))
            with patch.object(plots, 'CACHE_ROOT', root.parent):
                data = plots.series(root.name, [{'element': 'Fe', 'orbital': 'p'}])
            self.assertEqual(len(data['band']['traces']), 1)
            self.assertAlmostEqual(data['band']['traces'][0]['weights'][0][0][0], 0.6)
            self.assertAlmostEqual(data['band']['energies'][1][0][0], 1.0)
            self.assertEqual(plots._orbital_indexes(info['orbitals'], 'p'), [1, 2, 3])
            meta.update(project_id='project',sources=[],band_attempt_id='attempt',dos_attempt_id=None)
            (root/'metadata.json').write_text(json.dumps(meta))
            png='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
            with patch.object(plots,'CACHE_ROOT',root.parent), patch.object(plots,'SAVED_ROOT',root/'saved'):
                saved=plots.save(root.name,'project',[{'element':'Fe','orbital':'p'}],-4,4,png,'<svg xmlns="http://www.w3.org/2000/svg"></svg>','fixture',.7)
                self.assertEqual(plots.saved('project')[0]['id'],saved['id'])
                self.assertEqual(saved['point_size_scale'],.7)
                self.assertIn('fatband',plots.saved_file(saved['id'],'selected-curves.csv','project').decode())
                # Binary export preserves the PNG and the exact scientific CSV.
                binary=root/'export.png'
                binary.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII='))
                result=plots.save(root.name,'project',[{'element':'Fe','orbital':'p'}],-4,4,'','','binary',.7,png_file=binary)
                self.assertEqual(plots.saved_file(result['id'],'figure.png','project'),binary.read_bytes())
                self.assertEqual(plots.saved_file(result['id'],'selected-curves.csv','project'),plots.saved_file(saved['id'],'selected-curves.csv','project'))
                svg=plots.saved_file(result['id'],'figure.svg','project').decode()
                self.assertIn('width="1" height="1"',svg)
                self.assertIn(base64.b64encode(binary.read_bytes()).decode(),svg)
                binary.write_bytes(b'not png')
                with self.assertRaisesRegex(ValueError,'PNG 数据无效'):
                    plots.save(root.name,'project',[],-4,4,'','','invalid',png_file=binary)
                self.assertEqual(len(plots.saved('project')),2)

    def test_cached_lookup_is_project_scoped_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            key=plots._key('project','band',None)
            (root/key).mkdir()
            (root/key/'metadata.json').write_text(json.dumps({'state':'READY','project_id':'project',
                'band_attempt_id':'band','dos_attempt_id':None,'sources':[]}))
            with patch.object(plots,'CACHE_ROOT',root):
                self.assertEqual(plots.cached('project','band',None)['state'],'READY')
                self.assertEqual(plots.cached('other','band',None)['state'],'ABSENT')
                active = {'id':key,'state':'DOWNLOADING','downloaded_bytes':128}
                with patch.object(plots,'JOBS',{key:active}):
                    self.assertEqual(plots.cached('project','band',None),active)
                    self.assertEqual(plots.cached('other','band',None)['state'],'ABSENT')

    def test_soc_procar_uses_first_charge_projection_table_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            tables=''
            for value in (0.5,9.0,8.0,7.0):
                tables+=f'ion s py pz px dxy dyz dz2 dxz dx2-y2 tot\n1 {value} 0 0 0 0 0 0 0 0 {value}\ntot {value} 0 0 0 0 0 0 0 0 {value}\n'
            (root/'PROCAR').write_text('PROCAR\n# of k-points: 1 # of bands: 1 # of ions: 1\nk-point 1 : 0 0 0 weight = 1\nband 1 # energy 3 # occ. 1\n'+tables)
            info=plots._procar(root/'PROCAR',['Fe'],root,True)
            self.assertEqual(info['spins'],1)
            import numpy as np
            weight=np.load(root/'band_weight.npy')
            self.assertAlmostEqual(float(weight[0,0,0,0,0]),0.5)
            self.assertEqual(info['projection_component'],'charge')

    def test_vasp_fixed_width_negative_k_coordinate_without_space(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'PROCAR').write_text('PROCAR\n# of k-points: 1 # of bands: 1 # of ions: 1\n'
                'k-point 1 : 0.00000000 0.01282051-0.01282051 weight = 1\n'
                'band 1 # energy 3 # occ. 1\nion s py pz px dxy dyz dz2 dxz x2-y2 tot\n'
                '1 0 0 0 0 0 0 0 0 0 0\ntot 0 0 0 0 0 0 0 0 0 0\n')
            plots._procar(root/'PROCAR',['Fe'],root,False)
            import numpy as np
            self.assertAlmostEqual(float(np.load(root/'band_kcoords.npy')[0,2]),-0.01282051)

    def test_doscar_spin_columns_and_projected_sum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doscar = root / 'DOSCAR'
            header = '0 0 2 2.0 0\n'
            doscar.write_text('header\n' * 5 + header + '1 10 20 0 0\n2 11 21 0 0\n'
                              + header + '1 ' + ' '.join(str(i) for i in range(1, 19)) + '\n'
                              + '2 ' + ' '.join(str(i) for i in range(1, 19)) + '\n')
            info = plots._doscar(doscar, ['Fe'], root)
            self.assertEqual(info['spins'], 2)
            self.assertEqual(info['orbitals'][1:4], ['py', 'pz', 'px'])
            self.assertTrue((root / 'dos_projected.npy').exists())
            meta={'state':'READY','band':None,'dos':{**info,'energy_reference':{'fermi_eV':2.0}}}
            (root / 'metadata.json').write_text(json.dumps(meta))
            with patch.object(plots,'CACHE_ROOT',root.parent):
                data=plots.series(root.name,[{'element':'Fe','orbital':'p'}],-4,4)
            self.assertEqual(len(data['dos']['traces'][0]['values']),2)
            self.assertEqual(len(data['dos']['traces'][0]['values'][0]),2)

    def test_total_only_doscar_keeps_total_but_disables_projected_orbitals(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'DOSCAR').write_text('header\n'*5+'0 0 2 2.0 0\n1 10 20 0 0\n2 11 21 0 0\n')
            info=plots._doscar(root/'DOSCAR',['Fe'],root)
            self.assertEqual(info['spins'],2)
            self.assertEqual(info['elements'],[])
            self.assertIn('没有逐原子轨道投影',info['projection_note'])


if __name__ == '__main__':
    unittest.main()
