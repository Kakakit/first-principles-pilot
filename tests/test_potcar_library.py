import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import potcar_library as lib

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.old = lib.ROOT
        lib.ROOT = Path(self.temp.name) / 'workspace'; self.root = Path(self.temp.name) / 'library'
        for symbol in ('Fe', 'O'):
            p = self.root / symbol / 'POTCAR'; p.parent.mkdir(parents=True)
            p.write_bytes(f'TITEL = PAW_PBE {symbol} test\nLEXCH = PE\nENMAX = 400;\n'.encode()+b'payload\nEnd of Dataset\n')
        lib.configure(str(self.root))
    def tearDown(self):
        lib.ROOT = self.old; self.temp.cleanup()
    def test_order_exact_bytes_provenance_and_repeat(self):
        selections = {'Fe': 'Fe/POTCAR', 'O': 'O/POTCAR'}
        result = lib.assemble(['O', 'Fe', 'O'], selections, 'PBEsol')
        expected = b''.join((self.root / s / 'POTCAR').read_bytes() for s in ['O','Fe','O'])
        self.assertEqual((lib.ROOT/'potcars'/result['id']).read_bytes(), expected)
        self.assertEqual(result['id'], hashlib.sha256(expected).hexdigest())
        self.assertEqual(len(result['provenance']['records']), 3)
        self.assertEqual(lib.assemble(['O','Fe','O'], selections, 'PBEsol')['id'], result['id'])
    def test_reject_wrong_family_element_and_missing(self):
        for selections, family in [({'Fe':'Fe/POTCAR'},'LDA'), ({'Fe':'O/POTCAR'},'PBE'), ({},'PBE')]:
            with self.assertRaises(ValueError): lib.assemble(['Fe'], selections, family)

    def test_saved_directory_overrides_launcher_for_index_and_assembly(self):
        from backend import local_setup
        (lib.ROOT / 'potcar-library.json').unlink()
        new = Path(self.temp.name) / '新 PAW 库'
        dataset = new / 'Fe' / 'POTCAR'
        dataset.parent.mkdir(parents=True)
        dataset.write_bytes((self.root/'Fe'/'POTCAR').read_bytes().replace(b'payload', b'new'))
        with patch.dict(os.environ, FPILOT_POTCAR_LIBRARY=str(self.root)):
            self.assertEqual(lib.catalog()['path'], str(self.root.resolve()))
            saved = lib.configure(str(new))
            self.assertEqual(saved['path'], str(new.resolve()))
            self.assertEqual([entry['element'] for entry in saved['entries']], ['Fe'])
            for invalid in ('', str(new/'missing')):
                with self.assertRaises(ValueError):
                    lib.configure(invalid)
            self.assertEqual(lib.catalog(), saved)
            with patch.object(local_setup, 'ROOT', lib.ROOT):
                self.assertEqual(local_setup.read()['current']['potcar_library'], saved['path'])
            assembled = lib.assemble(['Fe'], {'Fe':'Fe/POTCAR'}, 'PBE')
            self.assertEqual(assembled['id'], hashlib.sha256(dataset.read_bytes()).hexdigest())
    def test_same_bytes_distinct_choice_receipts(self):
        a=lib.assemble(['Fe'],{'Fe':'Fe/POTCAR'},'PBE')
        b=lib.assemble(['Fe'],{'Fe':'Fe/POTCAR'},'PBEsol')
        self.assertEqual(a['id'],b['id'])
        self.assertNotEqual(a['provenance_id'],b['provenance_id'])
        for result in (a,b):
            raw=(lib.ROOT/'potcars'/(result['provenance_id']+'.library.json')).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),result['provenance_id'])
    def test_header_and_conflict_exclusion(self):
        p = self.root/'Fe'/'POTCAR'; p.write_bytes(p.read_bytes()+b'x'*100000)
        self.assertEqual(lib.header(p)['element'], 'Fe')
        q = self.root/'O'/'POTCAR'; q.write_bytes(q.read_bytes().replace(b'LEXCH = PE',b'LEXCH = CA'))
        self.assertEqual(len(lib.catalog()['entries']), 1)
        self.assertEqual(lib.catalog()['skipped'], 1)
