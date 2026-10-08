import json
import math
import unittest
from backend import kpoints


class KpointsTests(unittest.TestCase):
    def test_vaspkit_spacing_two_pi_and_ceil_for_anisotropic_cell(self):
        result=kpoints.suggest([[5,0,0],[0,10,0],[0,0,20]],.04)
        self.assertEqual(result['grid'],[5,3,2]);self.assertEqual(result['total_points'],30)
        self.assertAlmostEqual(result['target_spacing_A_inv'],2*math.pi*.04)
        self.assertTrue(all(x<=result['target_spacing_A_inv']+1e-10 for x in result['actual_spacing_A_inv']))
        self.assertFalse(result['remote_checked'])

    def test_skew_lattice_uses_full_reciprocal_vectors_not_inverse_direct_lengths(self):
        result=kpoints.suggest([[4,0,0],[2,2,0],[0,0,10]],.1)
        self.assertEqual(result['grid'],[4,5,1])
        self.assertEqual(kpoints.suggest([[4,0,0],[2,2,0],[0,0,10]],.05)['grid'],[8,10,2])

    def test_gamma_only_explicit_and_mp_center_preserved(self):
        lattice=[[4,0,0],[0,4,0],[0,0,4]]
        self.assertEqual(kpoints.suggest(lattice,.04,'Monkhorst-Pack')['center'],'Monkhorst-Pack')
        only=kpoints.suggest(lattice,.04,'Monkhorst-Pack',True)
        self.assertEqual((only['grid'],only['center']),([1,1,1],'Gamma'))

    def test_missing_singular_left_handed_nonfinite_and_out_of_range(self):
        for matrix in (None,[],[[0,0,0]]*3,[[1,0,0],[0,1,0],[0,0,-1]],[[float('nan'),0,0],[0,1,0],[0,0,1]]):
            with self.assertRaises(ValueError):kpoints.suggest(matrix)
        for value in (0,-.1,'',float('inf'),True):
            with self.assertRaises(ValueError):kpoints.suggest([[1,0,0],[0,1,0],[0,0,1]],value)
        with self.assertRaisesRegex(ValueError,'1000'):kpoints.suggest([[1,0,0],[0,1,0],[0,0,1]],.00001)

    def test_provenance_matches_applied_grid_and_cannot_claim_a_different_lattice(self):
        lattice=[[5,0,0],[0,5,0],[0,0,5]];value={**kpoints.suggest(lattice,.04),'lattice':lattice,'source':{'kind':'reference','label':'测试结构'}}
        self.assertEqual(kpoints.generation_info(json.dumps(value),[5,5,5],'Gamma')['grid'],[5,5,5])
        with self.assertRaises(ValueError):kpoints.generation_info(json.dumps(value),[6,6,6],'Gamma')
        value['lattice'][0][0]=8
        with self.assertRaises(ValueError):kpoints.generation_info(json.dumps(value),[5,5,5],'Gamma')
