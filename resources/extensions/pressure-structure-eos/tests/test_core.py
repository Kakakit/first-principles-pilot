import copy,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
from pymatgen.analysis.eos import EOS,BirchMurnaghan,Vinet,Murnaghan
PACKAGE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PACKAGE/'scripts'));sys.path.insert(0,str(PACKAGE/'tests'))
from eos_core import parse_outcar,extract_point,fit_groups,pressure,EV_A3_TO_GPA
from fixture_builder import outcar

class CoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
 def point(self,text,stage='relax'):
  file=self.root/'OUTCAR';file.write_text(text)
  return extract_point({'id':'p','name':'22808','stage':stage},{'outcar':{'path':str(file),'name':'OUTCAR'}},{'force_tolerance':0.02})
 def test_final_complete_step_and_no_field_cross_pair(self):
  p=self.point(outcar(40,-5,10,ending=False)+outcar(44,-6,20,step=2))
  self.assertEqual(p['step'],2);self.assertAlmostEqual(p['values']['volume_a3'],44);self.assertEqual(p['values']['energy_sigma0_ev'],-6);self.assertEqual(p['values']['external_pressure_gpa'],2)
  p=self.point(outcar(40,-5)+outcar(44,-6,step=2,ending=False).split(' free  energy')[0])
  self.assertEqual(p['step'],1);self.assertEqual(p['values']['energy_sigma0_ev'],-5);self.assertIn('末尾离子步不完整',p['fit_blockers'])
 def test_truncated_electronic_step_never_overwrites_previous_ionic_energy(self):
  p=self.point(outcar(40,-5)+' Iteration 2( 1)\n free energy TOTEN = -99 eV\n energy without entropy = -99 energy(sigma->0) = -99\n');self.assertEqual(p['values']['energy_sigma0_ev'],-5);self.assertEqual(p['step'],1);self.assertIn('末尾离子步不完整',p['fit_blockers'])
 def test_missing_field_is_none_not_zero_and_units(self):
  p=self.point(outcar().replace(' external pressure = 0.0 kB Pullay stress = 0 kB\n','').replace(' energy  without entropy= -5.000500000000 energy(sigma->0) = -5.000000000000\n',''))
  self.assertIsNone(p['values']['external_pressure_gpa']);self.assertIsNone(p['values']['energy_sigma0_ev']);self.assertEqual(p['values']['free_energy_ev'],-5.001)
  self.assertAlmostEqual(self.point(outcar(pressure=-12.3))['values']['stress_xx_gpa'],-1.23)
 def test_printed_volume_rounding_is_not_false_mismatch(self):
  p=self.point(outcar(109.8344).replace('109.83440000','109.83'));self.assertFalse(any('核对' in r for r in p['fit_blockers']));self.assertEqual(p['evidence']['volume']['rounding_half_unit_a3'],0.005)
 def test_pstress_convergence_and_force(self):
  p=self.point(outcar(pstress=20,force=0.03));self.assertEqual(p['values']['pstress_gpa'],2);self.assertAlmostEqual(p['values']['force_max_ev_ang'],0.03);self.assertTrue(any('PSTRESS' in s for s in p['fit_blockers']))
  p=self.point(outcar().replace(' aborting loop because EDIFF is reached\n',''));self.assertTrue(any('EDIFF' in s for s in p['fit_blockers']))
 def test_lattice_checks_and_initial_poscar(self):
  p=self.point(outcar().replace('volume of cell : 40.00000000','volume of cell : 42.00000000'));self.assertTrue(any('核对' in s for s in p['fit_blockers']))
 def fit_points(self,model):
  cls={'birch_murnaghan':BirchMurnaghan,'vinet':Vinet,'murnaghan':Murnaghan}[model];params=[-5,0.8,4.2,40];curve=cls(np.array([35,40,45]),np.array([0,0,0]));points=[]
  for i,v in enumerate(np.linspace(35,45,11)):
   p=self.point(outcar(v,float(curve._func(v,params))));p['id']=str(i);points.append(p)
  return points,params
 def test_known_models_and_pressure_matches_derivative(self):
  for model in ('birch_murnaghan','vinet','murnaghan'):
   points,params=self.fit_points(model);fit=fit_groups(points,{'model':model})[0];self.assertEqual(fit['status'],'FITTED',fit);self.assertAlmostEqual(fit['params']['v0_a3_atom'],40,places=4);self.assertAlmostEqual(fit['params']['b0_gpa'],0.8*EV_A3_TO_GPA,places=3)
   cls={'birch_murnaghan':BirchMurnaghan,'vinet':Vinet,'murnaghan':Murnaghan}[model];curve=cls(np.array([35,40,45]),np.zeros(3));v=38;h=1e-4;numeric=-(curve._func(v+h,params)-curve._func(v-h,params))/(2*h)*EV_A3_TO_GPA;self.assertAlmostEqual(float(pressure(v,params,model)),numeric,places=6)
 def test_exclusions_missing_energy_sparse_and_mixed_group(self):
  points,_=self.fit_points('birch_murnaghan');selection={'excluded':[str(i) for i in range(6)]};self.assertEqual(fit_groups(points,selection)[0]['status'],'UNAVAILABLE')
  points[0]['values']['energy_sigma0_ev']=None;fit=fit_groups(points,{})[0];self.assertEqual(len(fit['point_ids']),10);self.assertIn('未提供',fit['excluded'][0]['reasons'][0])
  points[-1]['settings_signature']['ENCUT']=900;fit=fit_groups(points,{})[0];self.assertIn('不一致',fit['reason'])
 def test_duplicate_volume_and_no_minimum_bracket(self):
  points,_=self.fit_points('birch_murnaghan');points=points[:6];self.assertEqual(fit_groups(points,{})[0]['status'],'UNAVAILABLE')
  for p in points:p['values']['volume_a3']=40
  self.assertIn('不同体积',fit_groups(points,{})[0]['reason'])
 def test_evidence_and_presentation_selection_no_source_reads(self):
  import presentation
  points,_=self.fit_points('birch_murnaghan');data={'points':points,'options':{'energy':'energy_sigma0_ev','model':'birch_murnaghan'}}
  source=self.root/'source';source.mkdir();raw=json.dumps(data).encode();(source/'pressure-eos.json').write_bytes(raw)
  import hashlib
  state={'value':{'identity':hashlib.sha256(raw).hexdigest(),'excluded':['0'],'model':'vinet'}}
  presentation.render({'source_dir':str(source),'output_dir':str(self.root/'view'),'view_state':state});view=json.loads((self.root/'view/data.json').read_text());self.assertEqual(view['fits'][0]['model'],'vinet');self.assertEqual(len(view['fits'][0]['point_ids']),10);self.assertEqual((source/'pressure-eos.json').read_bytes(),raw)
if __name__=='__main__':unittest.main()
