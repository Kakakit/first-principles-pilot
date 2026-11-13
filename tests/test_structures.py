import base64
import hashlib
import unittest
from pathlib import Path
import numpy as np
from pymatgen.io.vasp import Poscar
from backend.structures import parse_structure

FIXTURES = Path(__file__).parent / 'fixtures'


def parse(raw, fmt='poscar'):
    return parse_structure('test.'+fmt, fmt, base64.b64encode(raw).decode())


class StructureTests(unittest.TestCase):
    def test_poscar_nonorthogonal_and_selective_roundtrip(self):
        raw=(FIXTURES/'Si.POSCAR').read_bytes()
        s=parse(raw)
        self.assertEqual(base64.b64decode(s['source']['base64']),raw)
        self.assertEqual(s['source']['sha256'],hashlib.sha256(raw).hexdigest())
        np.testing.assert_allclose(s['atoms'][1]['cartesian'],[1.3575]*3)
        self.assertEqual(s['atoms'][1]['selective'],[False,True,False])
        p=Poscar.from_str(s['poscar'])
        np.testing.assert_allclose(p.structure.frac_coords,[[0,0,0],[.25,.25,.25]])
        np.testing.assert_allclose(p.structure.lattice.matrix,s['lattice'],atol=1e-12)
        self.assertEqual(list(p.selective_dynamics[1]),[False,True,False])

    def test_cartesian_scale_and_negative_volume(self):
        raw=b'Na\n2\n1 0 0\n0 1 0\n0 0 1\nNa\n1\nCartesian\n0.25 0.5 0.75\n'
        s=parse(raw)
        np.testing.assert_allclose(s['atoms'][0]['cartesian'],[.5,1,1.5])
        self.assertAlmostEqual(s['volume'],8)
        self.assertAlmostEqual(parse(raw.replace(b'\n2\n',b'\n-27\n'))['volume'],27)

    def test_cif_and_normalized_roundtrip(self):
        s=parse((FIXTURES/'Si.cif').read_bytes(),'cif')
        self.assertEqual(len(s['atoms']),2)
        self.assertAlmostEqual(s['volume'],5.43**3)
        p=Poscar.from_str(s['poscar'])
        np.testing.assert_allclose(p.structure.frac_coords,[a['fractional'] for a in s['atoms']])
        np.testing.assert_allclose(p.structure.lattice.matrix,s['lattice'],atol=1e-12)

    def test_reject_ambiguous_species_disorder_and_multi_cif(self):
        raw=(FIXTURES/'Si.POSCAR').read_bytes().replace(b'\r\n',b'\n')
        with self.assertRaisesRegex(ValueError,'元素名称'):parse(raw.replace(b'Si\n2',b'2'))
        cif=(FIXTURES/'Si.cif').read_bytes().replace(b'\r\n',b'\n')
        with self.assertRaisesRegex(ValueError,'占位'):parse(cif.replace(b'Si1 Si 0 0 0 1',b'Si1 Si 0 0 0 0.5'),'cif')
        with self.assertRaisesRegex(ValueError,'多个结构'):parse(cif+cif.replace(b'data_Si',b'data_Second'),'cif')

    def test_invalid_and_size_limit(self):
        for raw in [b'',b'broken',b'X'*262145]:
            with self.assertRaises(ValueError):parse(raw)

    def test_cif_symmetry_expansion_and_periodic_boundary(self):
        raw=(FIXTURES/'Si.cif').read_bytes()
        raw=raw.replace(b"'x,y,z'", b"'x,y,z'\n'-x,-y,-z'")
        s=parse(raw,'cif')
        self.assertEqual(len(s['atoms']),3)
        self.assertTrue(any(np.allclose(a['fractional'],[.75,.75,.75]) for a in s['atoms']))
        p=Poscar.from_str(s['poscar'])
        np.testing.assert_allclose(p.structure.distance_matrix,[[0,2.35125897,2.35125897],[2.35125897,0,4.70251794],[2.35125897,4.70251794,0]],atol=1e-7)

    def test_bom_crlf_original_unchanged(self):
        raw=b'\xef\xbb\xbf'+(FIXTURES/'Si.POSCAR').read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        self.assertEqual(base64.b64decode(parse(raw)['source']['base64']),raw)

if __name__=='__main__':unittest.main()
