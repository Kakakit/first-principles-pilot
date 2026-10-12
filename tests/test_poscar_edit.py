import base64
import copy
import unittest
import numpy as np
from backend.structures import parse_structure
from backend.structure_edit import edit_poscar, edit_structure, input_geometry

DIRECT = 'editable\n1\n2 0 0\n0.5 3 0\n0.25 0.75 4\nNa Cl\n1 1\nSelective dynamics\nDirect\n0.1 0.2 0.3 T F T\n0.4 0.5 0.6 F T F\n'


def parse(text):
    return parse_structure('POSCAR', 'poscar', base64.b64encode(text.encode()).decode())


class PoscarEditTests(unittest.TestCase):
    def test_isotropic_scale_volume_and_identity_preserved(self):
        parent = parse(DIRECT); before = copy.deepcopy(parent)
        text = DIRECT.replace('\n1\n', '\n1.01\n', 1)
        proposal = edit_poscar(parent, text)
        doc = proposal['structure']
        self.assertEqual(parent, before)
        self.assertEqual(doc['source'], parent['source'])
        self.assertEqual(doc['poscarText'], text)
        self.assertEqual(input_geometry(doc)['poscar'], text)
        self.assertAlmostEqual(proposal['summary']['volume_ratio'], 1.030301)
        self.assertEqual([a['id'] for a in doc['atoms']], [a['id'] for a in parent['atoms']])
        np.testing.assert_allclose(doc['lattice'], np.array(parent['lattice']) * 1.01)
        np.testing.assert_allclose([a['fractional'] for a in doc['atoms']], [a['fractional'] for a in parent['atoms']])

    def test_negative_volume_cartesian_uses_positive_derived_length_factor(self):
        raw = 'Na\n-27\n1 0 0\n0 1 0\n0 0 1\nNa\n1\nCartesian\n0.25 0.5 0.75\n'
        doc = parse(raw)
        self.assertAlmostEqual(doc['volume'], 27)
        np.testing.assert_allclose(doc['atoms'][0]['cartesian'], [0.75, 1.5, 2.25])
        np.testing.assert_allclose(doc['atoms'][0]['fractional'], [.25, .5, .75])

    def test_three_scale_factors_act_on_columns_not_lattice_rows(self):
        parent = parse(DIRECT)
        for mode in ('Direct', 'Cartesian'):
            text = DIRECT.replace('\n1\n', '\n2 3 4\n', 1).replace('Direct', mode)
            doc = parse(text)
            np.testing.assert_allclose(doc['lattice'], np.array(parent['lattice']) * [2, 3, 4])
            self.assertAlmostEqual(doc['volume'] / parent['volume'], 24)
            if mode == 'Cartesian':
                np.testing.assert_allclose(doc['atoms'][0]['cartesian'], [.2, .6, 1.2])
            else:
                np.testing.assert_allclose(doc['atoms'][0]['fractional'], [.1, .2, .3])

    def test_positive_cartesian_coordinates_and_constraints(self):
        doc = parse(DIRECT.replace('\n1\n', '\n2\n', 1).replace('Direct', 'Cartesian'))
        np.testing.assert_allclose(doc['atoms'][0]['cartesian'], [.2, .4, .6])
        self.assertEqual(doc['atoms'][0]['selective'], [True, False, True])

    def test_rebuild_or_changed_composition_never_reuses_old_atom_ids(self):
        parent = parse(DIRECT)
        old = set(a['id'] for a in parent['atoms'])
        for text, rebuild in [(DIRECT, True), (DIRECT.replace('Na Cl', 'K Cl'), False),
                              (DIRECT.replace('1 1', '2 1') + '0 0 0 T T T\n', False)]:
            proposal = edit_poscar(parent, text, rebuild)
            self.assertFalse(proposal['summary']['atom_ids_preserved'])
            self.assertTrue(old.isdisjoint(a['id'] for a in proposal['structure']['atoms']))
            self.assertTrue(proposal['summary']['messages'])

    def test_geometry_edit_discards_text_and_keeps_original_bytes(self):
        parent = parse(DIRECT); doc = edit_poscar(parent, DIRECT)['structure']
        atoms = [dict(a, coordinates=a['fractional']) for a in doc['atoms']]
        edited = edit_structure(doc, (np.array(doc['lattice']) * 1.02).tolist(), atoms, 'fractional')
        self.assertNotIn('poscarText', edited)
        self.assertEqual(edited['source'], parent['source'])
        np.testing.assert_allclose(parse(input_geometry(edited)['poscar'])['lattice'], edited['lattice'])

    def test_stale_text_geometry_or_constraints_are_rejected(self):
        good = edit_poscar(parse(DIRECT), DIRECT)['structure']
        for key in ('text', 'lattice', 'fractional', 'cartesian', 'selective', 'element', 'volume'):
            bad = copy.deepcopy(good)
            if key == 'text': bad['poscarText'] = DIRECT.replace('\n1\n', '\n1.01\n', 1)
            elif key == 'lattice': bad['lattice'][0][0] += .01
            elif key == 'volume': bad['volume'] += 1
            elif key == 'element': bad['atoms'][0]['element'] = 'K'
            elif key == 'selective': bad['atoms'][0]['selective'][0] = False
            else: bad['atoms'][0][key][0] += .01
            with self.assertRaisesRegex(ValueError, '不一致'): input_geometry(bad)

    def test_velocity_lattice_velocity_and_predictor_sections_stay_verbatim(self):
        tail = ('Lattice velocities and vectors\n1\n' + '0 0 0\n' * 3 +
                '2 0 0\n0.5 3 0\n0.25 0.75 4\n\nCartesian\n0.1 0.2 0.3\n0.4 0.5 0.6\n\n1\n0.5\n1 2 3\n' + '0 0 0\n' * 6)
        text = DIRECT + tail
        proposal = edit_poscar(parse(DIRECT), text)
        self.assertEqual(input_geometry(proposal['structure'])['poscar'], text)
        self.assertEqual(proposal['summary']['extra_sections'], ['晶格速度', '原子速度', '预测校正'])
        # Velocities are independent of the POSCAR position scaling factor.
        scaled = edit_poscar(parse(DIRECT), text.replace('\n1\n', '\n2\n', 1))
        self.assertEqual(scaled['structure']['poscarText'].split('Lattice velocities')[1], tail.split('Lattice velocities')[1])

    def test_reject_bad_scale_counts_constraints_and_unknown_tail(self):
        parent = parse(DIRECT)
        bad = [DIRECT.replace('\n1\n', f'\n{s}\n', 1) for s in ('0', 'NaN', '1 2', '1 -2 3', '1 2 Inf')]
        bad += [DIRECT.replace('1 1', '1 2'), DIRECT.replace('T F T', 'T X T'),
                DIRECT + 'unrecognized extra\n', DIRECT + '0 0 0\n' * 2]
        for text in bad:
            with self.assertRaises(ValueError, msg=text): edit_poscar(parent, text)

    def test_unicode_title_crlf_and_source_bytes_survive(self):
        text = DIRECT.replace('editable', 'EOS 体积测试').replace('\n', '\r\n')
        result = edit_poscar(parse(DIRECT), text)['structure']
        self.assertEqual(input_geometry(result)['poscar'], text)
        self.assertEqual(base64.b64decode(result['source']['base64']).decode(), DIRECT)


if __name__ == '__main__':
    unittest.main()
