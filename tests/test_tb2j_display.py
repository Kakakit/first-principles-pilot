"""Independent rendering and explicit initial-projector evidence, with no SSH/TB2J."""
import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'resources/extensions/tb2j/scripts'
spec=importlib.util.spec_from_file_location('tb2j_initial_labels',SCRIPTS/'orbital_labels.py');labels=importlib.util.module_from_spec(spec);spec.loader.exec_module(labels)
spec=importlib.util.spec_from_file_location('tb2j_presentation',SCRIPTS/'presentation.py');presentation=importlib.util.module_from_spec(spec);spec.loader.exec_module(presentation)

WIN='''Begin Projections
Fe:d
End Projections
num_wann = 5
begin atoms_cart
Fe 0 0 0
end atoms_cart
'''


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
        (self.root/'model').mkdir();(self.root/'TB2J_results').mkdir()
        (self.root/'model/up.win').write_text(WIN);(self.root/'model/down.win').write_text(WIN)
        (self.root/'TB2J_results/assigned_basis.txt').write_text(''.join('Fe1|orb_%d %d\n'%(i,i) for i in range(1,6)))
        self.full={'mode':'collinear','exchange':[{'atom_i':1,'atom_j':1,'symbol_i':'Fe','symbol_j':'Fe'}]}
    def metadata(self,mode='collinear',options=None):
        return labels.metadata(self.root,{**self.full,'mode':mode},{1:5},options or {})
    def test_automatic_labels_use_case_insensitive_frozen_projectors_and_assigned_basis(self):
        result=self.metadata();self.assertEqual([r['label'] for r in result['automatic_mapping']],labels.ORDERS['d']);self.assertTrue(all(r['source']=='初始投影参考' for r in result['automatic_mapping']))
        (self.root/'TB2J_results/assigned_basis.txt').write_text(''.join('Fe1|orb_%d %d\n'%(i,6-i) for i in range(1,6)))
        self.assertEqual([r['label'] for r in self.metadata()['automatic_mapping']],list(reversed(labels.ORDERS['d'])))
    def test_channels_or_missing_basis_do_not_invent_names(self):
        (self.root/'model/down.win').write_text(WIN.replace('Fe:d','Fe:s;p'))
        result=self.metadata();self.assertFalse(result['automatic_mapping']);self.assertIn('自动标签不可用',result['label_notes'][0])
        (self.root/'model/down.win').unlink();(self.root/'TB2J_results/assigned_basis.txt').unlink()
        self.assertIn('assigned_basis',self.metadata()['label_notes'][0])
    def test_soc_pairs_require_recorded_order_and_match_spatial_labels(self):
        for p in (self.root/'model').glob('*.win'):p.write_text(WIN.replace('num_wann = 5','num_wann = 10'))
        (self.root/'TB2J_results/assigned_basis.txt').write_text(''.join('Fe1|orb_%d %d\n'%(i,i) for i in range(1,11)))
        for order in ('spin','orbital'):
            self.assertEqual([r['label'] for r in self.metadata('spinor',{'groupby':order})['automatic_mapping']],labels.ORDERS['d'])
        self.assertFalse(self.metadata('spinor')['automatic_mapping'])
    def test_centres_must_corroborate_atom_membership_with_periodic_images(self):
        win=WIN.replace('num_wann = 5','num_wann = 10').replace('Fe 0 0 0','Fe 0 0 0\nFe 2 0 0')+'\nbegin unit_cell_cart\n8 0 0\n0 8 0\n0 0 8\nend unit_cell_cart\n'
        for p in (self.root/'model').glob('*.win'):p.write_text(win)
        path=self.root/'model/up_centres.xyz';values=['X 8 0 0']*5+['X 2 0 0']*5+['Fe 0 0 0','Fe 2 0 0'];path.write_text('12\ncentres\n'+'\n'.join(values)+'\n')
        self.assertEqual(len(self.metadata()['automatic_mapping']),5)
        values[0]='X 2 0 0';path.write_text('12\ncentres\n'+'\n'.join(values)+'\n');self.assertFalse(self.metadata()['automatic_mapping'])

    def test_random_and_ambiguous_projections_keep_raw_indices(self):
        for definition in ('random','Fe:sp3','Fe:d(u,d)[1,0,0]'):
            for p in (self.root/'model').glob('*.win'):p.write_text(WIN.replace('Fe:d',definition))
            self.assertFalse(self.metadata()['automatic_mapping'])
    def test_complete_csv_render_preserves_signed_values_translations_and_sources(self):
        columns=['atom_i','atom_j','symbol_i','symbol_j','R','distance_A','J_meV','DMI_meV','Jani_meV']
        with (self.root/'exchange.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
            for n in range(1201):writer.writerow(dict(atom_i=1,atom_j=1,symbol_i='Fe',symbol_j='Fe',R=json.dumps([n,0,0]),distance_A=n/100,J_meV=-1.2345678901234567))
        (self.root/'command.json').write_text(json.dumps({'options':{'mode':'collinear','rcut_angstrom':9},'argv':['--rcut','9']}))
        before=(self.root/'exchange.csv').read_bytes()
        with patch.dict(sys.modules,{'orbital_labels':labels}):manifest=presentation.render(self.root,self.root.parent/(self.root.name+'-view'))
        output=self.root.parent/(self.root.name+'-view');self.addCleanup(__import__('shutil').rmtree,output)
        index=json.loads((output/'index.json').read_text());data=[row for part in index['exchange_parts'] for row in json.loads((output/part['path']).read_text())]
        self.assertEqual(len(data),1201);self.assertEqual(data[-1][3:6],[1200,0,0]);self.assertEqual(data[1][7],-1.2345678901234567);self.assertEqual(index['default_max_distance_A'],9);self.assertEqual(before,(self.root/'exchange.csv').read_bytes());self.assertTrue(all((output/item['path']).stat().st_size<=5*1024**2 for item in manifest['assets'] if item['path'].endswith('.json')))
    def test_orbital_csv_is_required_when_run_requested_decomposition(self):
        (self.root/'exchange.csv').write_text('atom_i,atom_j,R,distance_A,J_meV,symbol_i,symbol_j\n')
        (self.root/'command.json').write_text(json.dumps({'options':{'orb_decomposition':True}}))
        with self.assertRaisesRegex(ValueError,'完整轨道 CSV'):presentation.read_data(self.root)
