import sys,json,tempfile,unittest,copy
from pathlib import Path
import numpy as np
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from onsite_core import analyze_files
from wf_identity import analyze,compare_channels
from wf_report import read_report,bind_report,SCHEMA,mapping_rows
from wf_io import read_u,read_amn
from test_onsite_core import write_hr
from cli import analyze as run

def write_amn(path,a):
 nk,nb,np_=a.shape;lines=['Synthetic',f'{nb} {nk} {np_}']
 for k in range(nk):
  for j in range(np_):
   for i in range(nb):lines.append(f'{i+1} {j+1} {k+1} {a[k,i,j].real:.12g} {a[k,i,j].imag:.12g}')
 path.write_text('\n'.join(lines)+'\n')

def write_u(path,ks,u):
 nk,nb,nw=u.shape;lines=['Synthetic',f'{nk} {nw} {nb}']
 for k in range(nk):
  lines+=['',' '.join(map(str,ks[k]))]
  for j in range(nw):
   for i in range(nb):lines.append(f'{u[k,i,j].real:.12g} {u[k,i,j].imag:.12g}')
 path.write_text('\n'.join(lines)+'\n')

def files(root,ks=None,seed='wannier90.1'):
 ks=np.array(ks if ks is not None else [[0,0,0]],float);n=5;p=root/(seed+'.win');p.write_text('num_wann=5\nbegin unit_cell_cart\n10 0 0\n0 10 0\n0 0 10\nend unit_cell_cart\nbegin atoms_frac\nFe .5 .5 .5\nend atoms_frac\nbegin projections\nFe:d\nend projections\nbegin kpoints\n'+'\n'.join(' '.join(map(str,k)) for k in ks)+'\nend kpoints\n');hr=root/(seed+'_hr.dat');h=np.diag([1,2,3,4,5]).astype(complex);h[0,1]=.2j;h[1,0]=-.2j;write_hr(hr,h)
 wo=root/(seed+'.wout');wo.write_text('Final State\nAll done\n');a=root/(seed+'.amn');u=root/(seed+'_u.mat');write_amn(a,np.tile(np.eye(n),(len(ks),1,1)));write_u(u,ks,np.tile(np.eye(n),(len(ks),1,1)));data=analyze_files(hr,p,wo);data.update(source={'id':seed,'stage':'wannier90','state':'COMPLETED','name':'合成'},seed=seed)
 return p,hr,wo,a,u,data,ks

class IdentityTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.r=Path(self.t.name);self.p,self.hr,self.wo,self.a,self.u,self.data,self.ks=files(self.r)
 def calc(self,ud=None):return analyze(self.a,self.u,ud,self.p,self.data,'wannier90.1',eig_path=self.r/'wannier90.1.eig' if (self.r/'wannier90.1.eig').is_file() else None)
 def test_identity_permutation_and_mixture(self):
  r=self.calc();self.assertTrue(all(v['status']=='DOMINANT_REFERENCE_CHARACTER' for v in r['orbitals']));self.assertIn('dz2',r['orbitals'][0]['label'])
  p=np.eye(5)[:,[4,1,2,3,0]];write_u(self.u,self.ks,p[None]);r=self.calc();self.assertIn('dxy',r['orbitals'][0]['label']);self.assertIn('dz2',r['orbitals'][4]['label'])
  p=np.eye(5,dtype=complex);p[:2,:2]=np.array([[1,1j],[1j,1]])/np.sqrt(2);write_u(self.u,self.ks,p[None]);r=self.calc();self.assertEqual(r['orbitals'][0]['status'],'MIXED_REFERENCE_CHARACTER');self.assertAlmostEqual(r['orbitals'][0]['dominant_fraction'],.5)
 def test_local_rotation_applies_to_reference_amplitudes(self):
  theta=np.pi/4;q=np.array([[np.cos(theta),-np.sin(theta),0],[np.sin(theta),np.cos(theta),0],[0,0,1]])
  self.data['reports'][0]['coordination_candidate']={'geometry':{'axes':q.tolist()}}
  r=self.calc();self.assertIn('局域 dxy',r['orbitals'][3]['label']);self.assertIn('局域 dx2-y2',r['orbitals'][4]['label'])
 def test_coherent_home_cell_not_probability_average(self):
  self.p,self.hr,self.wo,self.a,self.u,self.data,self.ks=files(self.r,[[0,0,0],[.5,0,0]])
  u=np.tile(np.eye(5,dtype=complex),(2,1,1));u[1,:,0]*=-1;write_u(self.u,self.ks,u);r=self.calc();row=r['orbitals'][0];self.assertAlmostEqual(row['coverage'],1);self.assertAlmostEqual(row['home_fraction'],0);self.assertEqual(row['status'],'LOW_HOME_OVERLAP');self.assertAlmostEqual(r['orbitals'][1]['home_fraction'],1)
 def test_low_coverage_and_normalization_not_absolute_purity(self):
  a=np.eye(5);a[:,0]=0;write_amn(self.a,a[None]);r=self.calc();self.assertEqual(r['reference_rank_min'],4);self.assertEqual(r['orbitals'][0]['status'],'LOW_COVERAGE');self.assertEqual(r['orbitals'][0]['coverage'],0)
  write_amn(self.a,(np.eye(5)*.001)[None]);r=self.calc();self.assertAlmostEqual(r['orbitals'][0]['coverage'],1);self.assertAlmostEqual(r['reference_projected_norms'][0],1e-6);self.assertTrue(any('不证明绝对' in n for n in r['notes']))
 def test_disentanglement_and_dimensions(self):
  a=np.vstack([np.eye(5),np.zeros((1,5))])[None];write_amn(self.a,a)
  with self.assertRaisesRegex(ValueError,'解缠结'):self.calc()
  (self.r/'wannier90.1.eig').write_text(''.join(f'{i+1} 1 {v}\n' for i,v in enumerate([1,2,3,4,5,100])))
  d=self.r/'wannier90.1_u_dis.mat';write_u(d,self.ks,a);self.assertEqual(self.calc(d)['reference_rank_min'],5)
  write_u(d,[[.1,0,0]],a)
  with self.assertRaisesRegex(ValueError,'k点'):self.calc(d)
 def test_packed_window_rows_are_expanded_before_reference_overlap(self):
  self.p.write_text(self.p.read_text()+'dis_win_min=0.5\ndis_win_max=5.5\n')
  a=np.vstack([np.zeros((1,5)),np.eye(5)])[None];write_amn(self.a,a)
  ud=np.vstack([np.eye(5),np.zeros((1,5))])[None];d=self.r/'wannier90.1_u_dis.mat';write_u(d,self.ks,ud)
  with self.assertRaisesRegex(ValueError,'.eig'):self.calc(d)
  (self.r/'wannier90.1.eig').write_text(''.join(f'{i+1} 1 {v}\n' for i,v in enumerate([-100,1,2,3,4,5])))
  result=self.calc(d);self.assertAlmostEqual(result['orbitals'][0]['coverage'],1);self.assertAlmostEqual(result['orbitals'][0]['home_fraction'],1);self.assertEqual(result['window_validation']['onsite_diagonal_residual_ev'],0)
  (self.r/'wannier90.1.eig').write_text(''.join(f'{i+1} 1 {v}\n' for i,v in enumerate([-100,1,2,3,4,4.9])))
  with self.assertRaisesRegex(ValueError,'onsite'):self.calc(d)
 def test_indices_truncation_nonunitary_and_nonuniform_kpoints(self):
  original=self.a.read_text();lines=original.splitlines();lines[-1]=lines[-2];self.a.write_text('\n'.join(lines))
  with self.assertRaisesRegex(ValueError,'重复'):self.calc()
  self.a.write_text(original);write_u(self.u,self.ks,(np.eye(5)*2)[None])
  with self.assertRaisesRegex(ValueError,'酉矩阵'):self.calc()
  self.p,self.hr,self.wo,self.a,self.u,self.data,self.ks=files(self.r,[[0,0,0],[.4,0,0]])
  with self.assertRaisesRegex(ValueError,'非均匀'):self.calc()
 def test_spin_gauge_phase_and_order_mismatch(self):
  a=self.calc();b=copy.deepcopy(a);b['channel']='wannier90.2';self.assertEqual(compare_channels([a,b])['status'],'REFERENCE_OVERLAPS_AGREE')
  b['home_overlap']['real'][0][0]*=-1
  self.assertEqual(compare_channels([a,b])['status'],'BASIS_ALIGNMENT_NOT_ESTABLISHED')
  b=copy.deepcopy(a);b['channel']='wannier90.2';b['home_overlap']['imag']=b['home_overlap']['real'];b['home_overlap']['real']=np.zeros((5,5)).tolist();self.assertEqual(compare_channels([a,b])['status'],'REFERENCE_OVERLAPS_AGREE')
 def test_model_binding_and_orb_mapping_keep_source(self):
  model=self.calc();raw={'schema':SCHEMA,'models':[model]};d=read_report(json.dumps(raw));self.assertEqual(bind_report(d,self.hr,self.p)['channel'],'wannier90.1');mapping=mapping_rows(model,[{'atom_index':1,'orbital_index':1,'wannier_index':5}]);self.assertIn('dxy',mapping[0]['label']);self.assertEqual(mapping[0]['wannier_index'],5)
  self.hr.write_text(self.hr.read_text()+'\n')
  with self.assertRaisesRegex(ValueError,'不属于'):bind_report(d,self.hr,self.p)
 def test_cli_artifact_freezes_report_and_preserves_inputs(self):
  inp=self.r/'inputs';inp.mkdir()
  self.p,self.hr,self.wo,self.a,self.u,self.data,self.ks=files(inp);before={p.name:p.read_bytes() for p in inp.iterdir()};out=self.r/'out';out.mkdir();req={'sources':[self.data['source']],'inputs':{role:[{'source_id':'wannier90.1','name':p.name,'path':str(p),'size_bytes':p.stat().st_size}] for role,p in [('hamiltonian',self.hr),('wannier-input',self.p),('wannier-output',self.wo),('wf-projections',self.a),('wf-unitary',self.u)]},'options':{},'output_dir':str(out)};result=run(req);self.assertIn('wf-identity.json',[a['path'] for a in result['artifacts']]);self.assertEqual(before,{p.name:p.read_bytes() for p in inp.iterdir()});r=json.loads((out/'onsite.json').read_text());self.assertIsNone(r['models'][0]['reports'][0]['global_axes_candidate']);self.assertIn('WF1',r['models'][0]['reports'][0]['orbital_labels'][0])
if __name__=='__main__':unittest.main()
