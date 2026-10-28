import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from onsite_core import read_onsite,angular,win_model,spectrum,rotation,analyze_files,ORBITALS


def write_hr(path,h,rs=None):
    n=len(h);rs=rs or [(0,0,0)]
    lines=['Synthetic',str(n),str(len(rs)),' '.join(['1']*len(rs))]
    for r in rs:
        for j in range(n):
            for i in range(n):
                v=h[i,j] if r==(0,0,0) else 0
                lines.append(' '.join(map(str,[*r,i+1,j+1,complex(v).real,complex(v).imag])))
    path.write_text('\n'.join(lines)+'\n')


def win(path,proj='Fe:s;p;d',atoms='Fe 0.5 0.5 0.5',n=9,spinors=False):
    path.write_text(f'num_wann={n}\nspinors={str(spinors).lower()}\nbegin unit_cell_cart\n10 0 0\n0 10 0\n0 0 10\nend unit_cell_cart\nbegin atoms_frac\n{atoms}\nend atoms_frac\nbegin projections\n{proj}\nend projections\n')

class OnsiteTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
    def test_complex_hermitian_is_not_discarded(self):
        h=np.array([[1,1j],[-1j,1]]);p=self.root/'x_hr.dat';write_hr(p,h);actual,e=read_onsite(p)
        np.testing.assert_allclose(actual,h);np.testing.assert_allclose(spectrum(actual)['eigenvalues_ev'],[0,2]);self.assertEqual(e['hermiticity_residual_ev'],0)
    def test_truncation_duplicate_and_nonhermitian(self):
        p=self.root/'x_hr.dat';write_hr(p,np.eye(2));p.write_text(p.read_text().rsplit('\n',2)[0]+'\n')
        with self.assertRaisesRegex(ValueError,'截断'):read_onsite(p)
        write_hr(p,np.eye(2));s=p.read_text().splitlines();s[-1]=s[-2];p.write_text('\n'.join(s)+'\n')
        with self.assertRaisesRegex(ValueError,'重复'):read_onsite(p)
        write_hr(p,np.array([[1,1],[0,1]]))
        with self.assertRaisesRegex(ValueError,'Hermitian'):read_onsite(p)
    def test_degeneracy_does_not_silently_rescale_onsite(self):
        p=self.root/'x_hr.dat';write_hr(p,np.eye(2));s=p.read_text().splitlines();s[3]='2';p.write_text('\n'.join(s)+'\n')
        with self.assertRaisesRegex(ValueError,'简并度'):read_onsite(p)
    def test_spd_order_and_multiple_projection_lines(self):
        p=self.root/'x.win';win(p,'Fe:d;p;s');m=win_model(p)
        self.assertEqual([e['orbital'] for e in m['mapping']],sum(ORBITALS.values(),[]))
        win(p,'Fe:d\nFe:p\nFe:s');m=win_model(p)
        self.assertEqual([e['orbital'] for e in m['mapping']],ORBITALS[2]+ORBITALS[1]+ORBITALS[0])
        self.assertEqual(angular('l=2,mr=5,1'),[(2,0),(2,4)])
    def test_atoms_then_angular_momentum_then_spin(self):
        p=self.root/'x.win';win(p,'Fe:p;s','Fe 0 0 0\nFe 0.5 0.5 0.5',16,True);m=win_model(p)
        self.assertEqual([e['atom'] for e in m['mapping']],[1]*8+[2]*8)
        self.assertEqual([e['spin'] for e in m['mapping']],['u','d']*8)
        self.assertEqual([e['orbital'] for e in m['mapping'][:4]],['s','s','pz','pz'])
    def test_rotation_matches_independent_p_vector_and_preserves_complex_spectrum(self):
        q=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
        for l in (0,1,2):
            r=rotation(l,q);np.testing.assert_allclose(r@r.T,np.eye(len(r)),atol=1e-12)
            rng=np.random.default_rng(42);a=rng.normal(size=(len(r),len(r)))+1j*rng.normal(size=(len(r),len(r)));h=a+a.conj().T
            np.testing.assert_allclose(np.linalg.eigvalsh(r@h@r.T),np.linalg.eigvalsh(h),atol=1e-12)
        np.testing.assert_allclose(rotation(1,q),[[1,0,0],[0,0,1],[0,-1,0]])
    def test_uniform_shift_preserves_splittings_and_weights_sum(self):
        rng=np.random.default_rng(1);a=rng.normal(size=(5,5))+1j*rng.normal(size=(5,5));h=a+a.conj().T
        x=spectrum(h);y=spectrum(h+3*np.eye(5));np.testing.assert_allclose(x['relative_eigenvalues_ev'],y['relative_eigenvalues_ev']);np.testing.assert_allclose(np.sum(x['weights'],axis=0),np.ones(5));self.assertAlmostEqual(y['centroid_ev']-x['centroid_ev'],3)
    def test_s_ligands_and_known_oct_tet_delta(self):
        for cn,refs,sign in [(6,[[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]],1),(4,[[1,1,1],[1,-1,-1],[-1,1,-1],[-1,-1,1]],-1)]:
            refs=np.array(refs,float);refs/=np.linalg.norm(refs,axis=1)[:,None]
            positions=np.array([.5,.5,.5])+refs*.2;atoms='Fe .5 .5 .5\n'+'\n'.join('S '+' '.join(map(str,v)) for v in positions)
            p=self.root/'x.win';win(p,'Fe:d\nS:s',atoms,5+cn);h=np.zeros((5+cn,5+cn));h[:5,:5]=np.diag([2,0,0,2,0]);hr=self.root/'x_hr.dat';write_hr(hr,h)
            a=analyze_files(hr,p,options={'elements':'Fe','ligands':'S','coordination':f'Fe:{cn}'})
            c=a['reports'][0]['coordination_candidate'];self.assertAlmostEqual(sign*c['metrics']['cubic_partition']['eg_minus_t2g_ev'],sign*2,places=10);self.assertLess(c['geometry']['direction_rmsd'],1e-10)
    def test_spinor_keeps_ten_dimensional_block_no_fake_cubic_partition(self):
        p=self.root/'x.win';win(p,'Fe:d',n=10,spinors=True);hr=self.root/'x_hr.dat';h=np.diag(np.arange(10.));h[0,1]=h[1,0]=0.5;write_hr(hr,h)
        result=analyze_files(hr,p);r=result['reports'][0];self.assertEqual(len(r['raw']['eigenvalues_ev']),10);self.assertNotIn('cubic_partition',r['raw']);self.assertTrue(result['spinors'])
    def test_wrong_count_hybrid_and_missing_ligand_rejected(self):
        p=self.root/'x.win';win(p,'Fe:sp3',n=4)
        with self.assertRaisesRegex(ValueError,'混成'):win_model(p)
        win(p,n=8)
        with self.assertRaisesRegex(ValueError,'数量'):win_model(p)
        win(p,n=9);hr=self.root/'x_hr.dat';write_hr(hr,np.eye(9))
        with self.assertRaisesRegex(ValueError,'配体'):analyze_files(hr,p,options={'coordination':'Fe:6'})
    def test_whole_atom_contains_inter_shell_coupling(self):
        p=self.root/'x.win';win(p);hr=self.root/'x_hr.dat';h=np.eye(9);h[0,1]=h[1,0]=0.3;write_hr(hr,h)
        result=analyze_files(hr,p);self.assertEqual(len(result['reports']),3);np.testing.assert_allclose(result['site_blocks'][0]['metrics']['eigenvalues_ev'],[.7,1,1,1,1,1,1,1,1.3])

class WoutEvidenceTests(unittest.TestCase):
    def test_final_state_centres_and_unconverged_flag(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'x.win';win(p,'Fe:s',n=1);hr=root/'x_hr.dat';write_hr(hr,np.eye(1));w=root/'x.wout'
            w.write_text('Number of Wannier Functions : 1\nDisentanglement convergence criteria not satisfied\nFinal State\nWF centre and spread 1 ( 5.0, 5.0, 5.0 ) 2.5\nAll done\n')
            data=analyze_files(hr,p,w);self.assertFalse(data['localization']['disentanglement_converged']);self.assertEqual(data['centres']['spreads_ang2'],[2.5]);self.assertAlmostEqual(data['centres']['max_distance_ang'],0);self.assertTrue(any('未收敛' in s for s in data['warnings']))
            w.write_text('Number of Wannier Functions : 2\nFinal State\nAll done\n')
            with self.assertRaisesRegex(ValueError,'num_wann'):analyze_files(hr,p,w)
    def test_wrong_ligand_rejected_before_neighbor_search(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'x.win';win(p,'Fe:d',n=5);hr=root/'x_hr.dat';write_hr(hr,np.eye(5))
            with self.assertRaisesRegex(ValueError,'不在'):analyze_files(hr,p,options={'ligands':'O','coordination':'Fe:6'})

if __name__=='__main__':unittest.main()
