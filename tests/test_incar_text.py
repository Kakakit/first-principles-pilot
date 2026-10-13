"""Free text must preserve effective assignments across local/remote readers."""
import unittest
from backend import incar_text as text


class IncarTextTests(unittest.TestCase):
    def test_continuations_quotes_comments_and_semicolons(self):
        raw = ('ENCUT = 400; NELM = 80 # keep\n'
               'MAGMOM = 2*4 \\\n 2*-4\nM_CONSTR = 0 0 1 \\\n 0 0 -1\n'
               'WANNIER90_WIN = "\nBegin Projections\nFe:d ! orbital\n'
               'End Projections\nENCUT = 999\n"\nCUSTOM_TAG = any value\n')
        tags, duplicate, invalid = text.parse(raw)
        self.assertEqual((duplicate, invalid), ([], []))
        self.assertEqual(tags['ENCUT'], '400')
        self.assertEqual(tags['MAGMOM'].split(), ['2*4','2*-4'])
        self.assertIn('Fe:d ! orbital', tags['WANNIER90_WIN'])
        self.assertIn('ENCUT = 999', tags['WANNIER90_WIN'])
        self.assertEqual(tags['CUSTOM_TAG'], 'any value')

    def test_higher_layer_forms_then_same_layer_raw_and_delete_restore(self):
        layers = [('shared', {'ENCUT':'300','NELM':'40'}, '# shared\nENCUT=400; NELM=80\n'),
                  ('node', {'ENCUT':'500'}, '# node\nNELM=90\nNELM=100\n')]
        values, origins, active, fragments = text.resolve(layers)
        self.assertEqual(values['ENCUT'], '500')
        self.assertEqual(values['NELM'], '100')
        self.assertEqual(origins['NELM'], 'node · 手动文本')
        output = text.render(values, fragments)
        self.assertEqual(text.parse(output)[0]['NELM'], '100')
        self.assertEqual(text.parse(output)[1], [])
        self.assertIn('# shared', output)
        self.assertIn('# node', output)
        layers[-1] = ('node', {'ENCUT':'500'}, '')
        self.assertEqual(text.resolve(layers)[0]['NELM'], '80')

    def test_unclosed_quote_is_an_error_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError,'未闭合'):
            text.resolve([('node', {}, 'WANNIER90_WIN = "\nFe:d\n')])

    def test_replacement_preserves_unrelated_quoted_block(self):
        raw='ENCUT=400\nWANNIER90_WIN="\nENCUT=1\n" # block\nENCUT=450\n'
        changed=text.replace(raw,'ENCUT','520')
        self.assertEqual(text.parse(changed)[0]['ENCUT'], '520')
        self.assertIn('WANNIER90_WIN="\nENCUT=1\n" # block',changed)
        self.assertEqual(text.parse(changed)[1], [])
