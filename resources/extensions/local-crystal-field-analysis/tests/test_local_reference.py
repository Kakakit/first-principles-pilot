import base64,copy,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
from pymatgen.core import Structure
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from local_reference import export_reference,inherit,decode_reference,SCHEMA,BUNDLE
from occupation_core import analyze_occupation
from onsite_core import analyze_files
from cli import analyze,collect
from test_occupation_core import fixture
from test_onsite_core import write_hr,win

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.inputs=self.root/'inputs';self.inputs.mkdir();p,o,_,_=fixture(self.inputs);self.structure=Structure.from_file(p)
        self.model=analyze_occupation(p,o,{'polyhedron_types':['FeO4']});self.model['source']={'id':'vasp-frozen','name':'冻结SCF','stage':'scf'};self.ref=export_reference(self.model)
        atoms='\n'.join(a.specie.symbol+' '+' '.join(map(str,a.frac_coords)) for a in self.structure)
        self.win=self.inputs/'wannier90.win';win(self.win,'Fe:d',atoms,5);self.hr=self.inputs/'wannier90_hr.dat'
        rng=np.random.default_rng(7);a=rng.normal(size=(5,5))+1j*rng.normal(size=(5,5));self.h=a+a.conj().T;write_hr(self.hr,self.h)
    def options(self,ref=None,**kw):return dict(local_reference=json.dumps(ref or self.ref),**kw)
    def test_export_import_roundtrip_and_no_natural_eigenvector_transfer(self):
        encoded='data:application/json;name=reference.json;base64,'+base64.b64encode(json.dumps(self.ref).encode()).decode()
        self.assertEqual(decode_reference(encoded),self.ref);opt,status,sites=inherit({'local_reference':encoded},self.structure)
        self.assertEqual(opt['polyhedron_types'],['FeO4']);self.assertEqual(status['wf_identity'],'UNVERIFIED');self.assertIn('occupation_reference',sites[1]);self.assertNotIn('weights',sites[1])
    def test_frozen_axes_inherited_not_refitted_and_complex_spectrum_preserved(self):
        # Equivalent tetrahedral frame, different x/y convention from the fresh fit.
        ref=copy.deepcopy(self.ref);g=ref['sites'][0]['geometry'];q=np.asarray(g['axes']);g['axes']=(q@np.array([[0,-1,0],[1,0,0],[0,0,1]])).tolist()
        data=analyze_files(self.hr,self.win,options=self.options(ref));r=data['reports'][0]
        np.testing.assert_array_equal(r['coordination_candidate']['geometry']['axes'],g['axes'])
        np.testing.assert_allclose(r['raw']['eigenvalues_ev'],r['coordination_candidate']['metrics']['eigenvalues_ev'],atol=1e-11)
        self.assertEqual(r['reference_alignment']['status'],'REFERENCE_FRAME_MATCHED_WF_UNVERIFIED');self.assertEqual(r['basis_status'],'INITIAL_PROJECTION_CANDIDATE');self.assertNotIn('local_reference',data['options'])
        self.assertGreater(np.max(np.abs(r['coordination_candidate']['metrics']['matrix']['imag'])),0)
    def test_mismatch_geometry_species_order_and_cell_fail(self):
        for key in ['lattice','coordinate','species','order']:
            r=copy.deepcopy(self.ref)
            if key=='lattice':r['structure']['lattice_ang'][0][0]+=0.01
            if key=='coordinate':r['structure']['atoms'][1]['frac'][0]+=.01
            if key=='species':r['structure']['atoms'][0]['element']='Cr'
            if key=='order':r['structure']['atoms'][0],r['structure']['atoms'][1]=r['structure']['atoms'][1],r['structure']['atoms'][0]
            with self.assertRaisesRegex(ValueError,'不符'):inherit(self.options(r),self.structure)
    def test_frame_and_ligand_geometry_validation(self):
        for mode in ['axes','image','vector','duplicate','missing','distance']:
            r=copy.deepcopy(self.ref);g=r['sites'][0]['geometry']
            if mode=='axes':g['axes'][0][0]+=1
            if mode=='image':g['neighbors'][0]['image'][0]=.5
            if mode=='vector':g['neighbors'][0]['vector_ang'][0]+=1
            if mode=='duplicate':g['neighbors'][1]=copy.deepcopy(g['neighbors'][0])
            if mode=='missing':r['sites']=[]
            if mode=='distance':g['neighbors'][0]['distance_ang']+=.01
            with self.assertRaises(ValueError):inherit(self.options(r),self.structure)
    def test_types_conflict_missing_reference_and_old_record(self):
        self.assertEqual(inherit({},self.structure),({},None,{}))
        with self.assertRaisesRegex(ValueError,'冲突'):inherit(self.options(polyhedron_types=['FeO6']),self.structure)
        with self.assertRaisesRegex(ValueError,'最大键长'):inherit(self.options(search_radius_angstrom=1),self.structure)
        m=copy.deepcopy(self.model);del m['structure']
        with self.assertRaisesRegex(ValueError,'旧占据'):export_reference(m)
    def test_bundle_ambiguity_and_bad_input(self):
        with self.assertRaisesRegex(ValueError,'多个参考'):inherit(self.options({'schema':BUNDLE,'references':[self.ref,self.ref]}),self.structure)
        for s in ['{}','not json','data:x;base64,***','{"schema":"'+SCHEMA+'","x":NaN}']:
            with self.assertRaises(ValueError):decode_reference(s)
    def test_protocol_artifact_export_and_check_rejects_mismatch(self):
        out=self.root/'out';out.mkdir();paths={'structure':self.inputs/'POSCAR','outcar':self.inputs/'OUTCAR'}
        req={'sources':[self.model['source']|{'state':'COMPLETED'}],'inputs':{role:[{'source_id':'vasp-frozen','name':p.name,'path':str(p),'size_bytes':p.stat().st_size}] for role,p in paths.items()},'options':{'polyhedron_types':['FeO4']},'output_dir':str(out)}
        result=analyze(req);reference=json.loads((out/'local-orbital-reference.json').read_text());self.assertEqual(reference['source']['id'],'vasp-frozen');self.assertIn('local-orbital-reference.json',[a['path'] for a in result['artifacts']])
        self.win.with_suffix('.wout').write_text('Final State\nAll done\n');paths={'hamiltonian':self.hr,'wannier-input':self.win,'wannier-output':self.win.with_suffix('.wout')};req.update(sources=[{'id':'wannier','stage':'wannier90','state':'COMPLETED'}],inputs={role:[{'source_id':'wannier','name':p.name,'path':str(p),'size_bytes':p.stat().st_size}] for role,p in paths.items()},options=self.options(reference))
        self.assertEqual(collect(req,False)['mode'],'hamiltonian');r=reference['structure']['atoms'][0];r['element']='Cr';req['options']=self.options(reference)
        with self.assertRaisesRegex(ValueError,'不符'):collect(req,False)
    def test_periodic_wrapping_and_separate_spin_models_use_same_axes(self):
        shifted=self.structure.copy();shifted.translate_sites([1],[1,0,0],frac_coords=True,to_unit_cell=False)
        _,_,sites=inherit(self.options(),shifted);self.assertEqual(sites[1]['geometry']['axes'],self.ref['sites'][0]['geometry']['axes'])
        for scale in [1,2]:
            write_hr(self.hr,self.h*scale);data=analyze_files(self.hr,self.win,options=self.options());self.assertEqual(data['reports'][0]['coordination_candidate']['geometry']['axes'],sites[1]['geometry']['axes'])
    def test_spinor_reference_only_transforms_spatial_frame(self):
        atoms='\n'.join(a.specie.symbol+' '+' '.join(map(str,a.frac_coords)) for a in self.structure);win(self.win,'Fe:d',atoms,10,True);write_hr(self.hr,np.kron(self.h,np.eye(2)))
        r=analyze_files(self.hr,self.win,options=self.options())['reports'][0];self.assertEqual(len(r['coordination_candidate']['metrics']['eigenvalues_ev']),10);self.assertNotIn('cubic_partition',r['coordination_candidate']['metrics'])
if __name__=='__main__':unittest.main()
