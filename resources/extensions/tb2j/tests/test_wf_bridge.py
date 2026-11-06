"""Synthetic identity transport; never runs a research calculation."""
import copy,csv,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import test_v2
from test_v2 import request,BASE,FAKE
sys.path.insert(0,str(BASE/'scripts'))
from wf_report import read_report,SCHEMA
from orbital_labels import assigned_indices

def report(req):
 models=[]
 for i,s in enumerate(req['sources']):
  binding={k:hashlib.sha256(Path(req['inputs'][r][i]['path']).read_bytes()).hexdigest() for k,r in [('hr_sha256','hamiltonian'),('win_sha256','wannier-input')]}
  rows=[dict(wf=w,atom=1 if w<=2 else 3,element='Fe',label='混合 WF' if i else '投影轴 dxy型 WF',status='MIXED_REFERENCE_CHARACTER',coverage=1.,home_fraction=1.,owner_fraction=1.,dominant_fraction=.6,components=[dict(label='dxy',l=2,relative_weight=.6),dict(label='dz2',l=2,relative_weight=.4)]) for w in range(1,5)]
  models.append(dict(channel=s['input_summary']['seed'],num_wann=4,binding=binding,orbitals=rows,home_overlap={'real':[[int(a==b) for a in range(4)] for b in range(4)],'imag':[[0]*4 for _ in range(4)]}))
 return dict(schema=SCHEMA,models=models,spin_alignment={'status':'BASIS_ALIGNMENT_NOT_ESTABLISHED'})

class WFBridgeTests(unittest.TestCase):
 cli=test_v2.TB2JV2Tests.cli
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def test_check_explains_vasp_reference_upload(self):
  req = request(self.root, 'collinear')
  req['options']['wf_identity_report'] = json.dumps({'schema': 'fpilot.local-orbital-reference.v1'})
  result = self.cli('check', req)
  self.assertNotEqual(result['status'], 'APPLICABLE')
  self.assertIn('VASP 局域坐标参考', result['reason'])
  self.assertIn('wf-identity.json', result['reason'])
 def test_frozen_binding_check_rejects_wrong_channel_and_model(self):
  req=request(self.root,'collinear');r=report(req);req['options']['wf_identity_report']=json.dumps(r);self.assertEqual(self.cli('check',req)['status'],'APPLICABLE')
  r['models'][1]['channel']='wrong';req['options']['wf_identity_report']=json.dumps(r);self.assertNotEqual(self.cli('check',req)['status'],'APPLICABLE')
  r=report(req);r['models'][0]['binding']['hr_sha256']='other';req['options']['wf_identity_report']=json.dumps(r);self.assertNotEqual(self.cli('check',req)['status'],'APPLICABLE')
 def test_attached_report_exports_components_and_preserves_native_j(self):
  req=request(self.root,'collinear');r=report(req);req['options']['wf_identity_report']=json.dumps(r)
  self.cli('run',req,{'TB2J_TEST_BASIS':'Fe1|orb_1 1\nFe1|orb_2 2\nFe2|orb_1 3\nFe2|orb_2 4\n'})
  out=Path(req['output_dir']);d=json.loads((out/'exchange.json').read_text());self.assertEqual(d['exchange'][0]['J_meV'],1.2345678901234567);self.assertEqual(d['orbital'][0]['J_meV'],.5)
  row=d['orbital'][0];self.assertEqual((row['wf_i'],row['wf_j']),(1,3));self.assertIn('自旋成分不同',row['label_i']);self.assertEqual(set(row['identity_components_i']),{'wannier90.1','wannier90.2'})
  with (out/'orbitals.csv').open() as f:self.assertIn('wf_i',next(csv.reader(f)))
  import presentation
  view=self.root/'view';presentation.render(out,view);index=json.loads((view/'index.json').read_text());self.assertEqual(len(index['wf_identity_rows']),8);self.assertEqual(index['basis_rows'][2]['atom_index'],3)
 def test_native_basis_does_not_need_initial_projection_names(self):
  root=self.root;(root/'model').mkdir();(root/'TB2J_results').mkdir();(root/'model/up.win').write_text('num_wann=4\nbegin atoms_cart\nFe 0 0 0\nO 1 1 1\nFe 2 2 2\nend atoms_cart\n');(root/'TB2J_results/assigned_basis.txt').write_text('Fe1|orb_1 1\nFe1|orb_2 2\nFe2|orb_1 3\nFe2|orb_2 4\n')
  rows=assigned_indices(root,{'mode':'collinear','exchange':[dict(atom_i=1,atom_j=3,symbol_i='Fe',symbol_j='Fe')]},{1:2,3:2});self.assertEqual(rows[-1]['atom_index'],3)
if __name__=='__main__':unittest.main()
