"""Independent geometry and compatibility checks; no host or remote operations."""
import importlib.util,json,math,subprocess,sys,tempfile,unittest
from pathlib import Path
import numpy as np

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'scripts'))
spec=importlib.util.spec_from_file_location('polyhedron_core',BASE/'src/crystal_distortion.py');core=importlib.util.module_from_spec(spec);sys.modules[spec.name]=core;spec.loader.exec_module(core)
from cli import config
from metrics import METRICS
from presentation import render

def site(number,center,ids,images,vectors,element='Cr'):
    return dict(site_number=number,element=element,ligand='O',center_cartesian_angstrom=center,ligand_site_numbers=ids,ligand_images=images,ligand_vectors_angstrom=vectors)

class DetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
    def request(self,options):
        out=self.root/str(len(list(self.root.iterdir())));out.mkdir()
        return dict(schema='fpilot.extension-request.v2',options=options,inputs={'structure':[{'path':str(BASE/'fixtures/ideal_fecr_polyhedra/POSCAR')}]},sources=[],output_dir=str(out),result_file=str(out/'result.json'))
    def run_cli(self,request,action='run'):
        path=self.root/'request.json';path.write_text(json.dumps(request));r=subprocess.run([sys.executable,str(BASE/'scripts/cli.py'),action,'--request',str(path)],text=True,capture_output=True);self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
    def test_short_input_and_legacy_equivalence(self):
        old=config({'options':dict(center_element='Cr',ligand_element='O',coordination=6,search_radius_angstrom=3)})
        self.assertEqual(old,config({'options':{'polyhedra':'CrO6'}}))
        self.assertEqual(len(config({'options':{'polyhedra':'CrO6， FeO4 CrO6'}})['centers']),2)
        for text in ('CrO5','Cr2O6','CrO6, CrO4','OO4',','):
            with self.assertRaises(ValueError):config({'options':{'polyhedra':text}})
    def test_internal_angles_and_metric_reconstruction(self):
        req=self.request({'polyhedra':'CrO6, FeO4'});self.run_cli(req);data=json.loads((Path(req['output_dir'])/'distortion.json').read_text())
        self.assertEqual(len(data['site_results']),2)
        for s in data['site_results']:
            n=s['coordination'];self.assertEqual(len(s['angles']),n*(n-1)//2)
            self.assertEqual(len(s['faces']),8 if n==6 else 4)
            if n==6:self.assertEqual([a['kind'] for a in s['angles']].count('trans'),3)
            angles=[a for a in s['angles'] if a['kind']!='trans'];variance=sum((a['angle_degree']-a['reference_degree'])**2 for a in angles)/(len(angles)-1)
            self.assertAlmostEqual(variance,s['angle_variance_degree2'],places=12)
            self.assertAlmostEqual(np.mean(np.linalg.norm(s['ligand_vectors_angstrom'],axis=1)),s['mean_bond_length_angstrom'])
            self.assertLess(s['bond_length_distortion_delta'],1e-25);self.assertAlmostEqual(s['quadratic_elongation'],1)
            self.assertTrue(all(k in METRICS for k in data['summary'][s['polyhedron']]['metrics']))
    def test_cross_boundary_and_reversal_with_multiple_ligands(self):
        # B at x=9 and its image x=-1 share two O images with A at x=0.
        a=site(1,[0,0,0],[3,4],[[0,0,0],[0,0,0]],[[-.5,1,0],[-.5,-1,0]])
        b=site(2,[9,0,0],[3,4],[[1,0,0],[1,0,0]],[[.5,1,0],[.5,-1,0]],'Fe')
        rows=core.bridge_records([a,b],np.eye(3)*10)
        self.assertEqual(len(rows),2);self.assertEqual(rows,core.bridge_records([b,a],np.eye(3)*10))
        for r in rows:
            self.assertEqual(r['center_j_image'],[-1,0,0]);self.assertAlmostEqual(r['center_distance_angstrom'],1)
            self.assertAlmostEqual(r['angle_degree'],math.degrees(math.acos(.6)))
            np.testing.assert_allclose(r['ligand_cartesian_angstrom'],np.array(r['center_j_cartesian_angstrom'])+np.array(b['ligand_vectors_angstrom'][r['ligand_site_number']-3]))
    def test_same_site_in_different_cells_is_a_real_bridge(self):
        a=site(1,[0,0,0],[2,2],[[0,0,0],[-1,0,0]],[[1,0,0],[-1,0,0]])
        r=core.bridge_records([a],np.eye(3)*2)
        self.assertEqual(len(r),1);self.assertEqual(r[0]['center_i'],r[0]['center_j']);self.assertEqual(r[0]['center_j_image'],[-1,0,0]);self.assertAlmostEqual(r[0]['angle_degree'],180)
    def test_triclinic_bridge_uses_full_lattice_vector(self):
        a=site(1,[0,0,0],[3],[[0,0,0]],[[.5,-.5,0]])
        b=site(2,[3,7,0],[3],[[0,1,0]],[[.5,.5,0]],'Fe')
        rows=core.bridge_records([a,b],[[10,0,0],[3,8,0],[1,2,9]])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['center_j_image'],[0,-1,0]);self.assertAlmostEqual(rows[0]['angle_degree'],90);self.assertAlmostEqual(rows[0]['center_distance_angstrom'],1)
    def test_doctor_reports_actual_missing_imports(self):
        r=subprocess.run([sys.executable,'-S',str(BASE/'scripts/cli.py'),'doctor'],text=True,capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr);data=json.loads(r.stdout);self.assertFalse(data['ready']);self.assertIn('numpy',data['missing']);self.assertIn('scipy.spatial',data['missing'])
    def test_disconnected_polyhedra_have_no_bridges(self):
        a=site(1,[0,0,0],[3],[[0,0,0]],[[1,0,0]])
        b=site(2,[5,0,0],[4],[[0,0,0]],[[1,0,0]])
        self.assertEqual(core.bridge_records([a,b],np.eye(3)*10),[])
    def test_full_protocol_render_preserves_input_and_raw_result(self):
        request=self.request({'polyhedra':'CrO6, FeO4'});source=BASE/'fixtures/ideal_fecr_polyhedra/POSCAR';before=source.read_bytes();self.run_cli(request,'check');self.run_cli(request)
        out=Path(request['output_dir']);raw=(out/'distortion.json').read_bytes();manifest=render({'source_dir':str(out),'output_dir':str(self.root/'view')})
        self.assertEqual(raw,(out/'distortion.json').read_bytes());self.assertEqual(before,source.read_bytes());self.assertEqual(manifest['data_schema'],'fpilot.polyhedra-geometry.v1')
        for asset in manifest['assets']:self.assertLess((self.root/'view'/asset['path']).stat().st_size,10*1024**2)
        result=json.loads((out/'result.json').read_text());self.assertTrue(all('_' not in row['metric'] for row in result['tables'][0]['rows']))
        self.assertTrue(all((out/name).is_file() for name in ('bonds.csv','angles.csv','bridges.csv')))
    def test_legacy_missing_coordinates_are_never_reconstructed_from_drafts(self):
        source=self.root/'old';source.mkdir();(source/'distortion.json').write_text(json.dumps({'analysis':'local-polyhedral-distortion'}))
        with self.assertRaisesRegex(ValueError,'同一冻结结构'):render({'source_dir':str(source),'output_dir':str(self.root/'view')})
    def test_stretched_octahedron_retains_original_scalar_definitions(self):
        vectors=np.array([[2,0,0],[-2,0,0],[0,2,0],[0,-2,0],[0,0,2.2],[0,0,-2.2]])
        result=core.analyze_vectors(vectors,6);lengths=np.linalg.norm(vectors,axis=1)
        self.assertAlmostEqual(result['bond_length_distortion_delta'],np.mean(((lengths-lengths.mean())/lengths.mean())**2));self.assertAlmostEqual(result['q_jt_angstrom'],math.sqrt(2/3)*.4)
    def test_shell_ambiguity_and_insufficient_neighbors_are_explicit(self):
        vectors=np.array([[1,1,1],[1,-1,-1],[-1,1,-1],[-1,-1,1]],dtype=float)*2/math.sqrt(3)
        coords=np.vstack([[0,0,0],vectors,[2.01,0,0]])
        structure=core.PoscarStructure('shell',np.eye(3)*20,['Fe']+['O']*5,coords/20)
        spec={'coordination':4,'ligand':'O','geometry':'tetrahedron'}
        _,warnings=core.analyze_site(structure,0,spec,3,.15,False);self.assertEqual(len(warnings),1)
        with self.assertRaisesRegex(core.AnalysisError,'壳层间隔'):core.analyze_site(structure,0,spec,3,.15,True)
        with self.assertRaisesRegex(core.AnalysisError,'需要 6 个'):core.analyze_site(structure,0,{**spec,'coordination':6,'geometry':'octahedron'},3,.15,False)

if __name__=='__main__':unittest.main()
