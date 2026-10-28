import json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
from pymatgen.core import Structure,Lattice
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'scripts'))
from occupation_core import read_final_matrices,analyze_occupation,density_report
from polyhedron_types import parse_types
from cli import collect,analyze
from presentation import render

def block(atom,up,down):
    matrix=lambda a:'\n'.join(' '.join(f'{x:.6f}' for x in row) for row in a)
    return f'atom = {atom:4d} type = 1 l = 2\n onsite density matrix\n\nspin component  1\n\n{matrix(up)}\n\nspin component  2\n\n{matrix(down)}\n\n occupancies and eigenvectors\n'

def fixture(root):
    structure=Structure(Lattice.cubic(10),['Fe']+['O']*4,[[.5,.5,.5],[.6,.6,.6],[.6,.4,.4],[.4,.6,.4],[.4,.4,.6]])
    p=root/'POSCAR';structure.to(filename=str(p),fmt='poscar');o=root/'OUTCAR';up=np.diag([.1,.2,.9,.3,.4]);down=np.eye(5)*.95
    header='ISPIN = 2\nNSW = 0\nLSORBIT = F\nLNONCOLLINEAR = F\nTITEL = PAW_PBE Fe\nTITEL = PAW_PBE O\nions per type = 1 4\ndirect lattice vectors                 reciprocal lattice vectors\n10 0 0 0 0 0\n0 10 0 0 0 0\n0 0 10 0 0 0\n'
    o.write_text(header+'Iteration 1( 1)\n'+block(1,np.eye(5)*.2,down)+'Iteration 1( 2)\n'+block(1,up,down)+'aborting loop because EDIFF is reached\nGeneral timing and accounting informations\n')
    return p,o,up,down

class OccupationTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
    def test_row_entries_and_legacy(self):
        self.assertEqual(parse_types({'polyhedron_types':['CrO6','FeO4']}),parse_types({'polyhedra':'CrO6，FeO4'}))
        for entries in [['CrO6, FeO4'],[''],['CrO6','CrO4'],['CrO6','CrO6'],['XxO6'],['OO6']]:
            with self.assertRaises(ValueError):parse_types({'polyhedron_types':entries})
    def test_final_step_order_rotation_and_source_protection(self):
        p,o,up,down=fixture(self.root);before=o.read_bytes();r=analyze_occupation(p,o,{'polyhedron_types':['FeO4']});self.assertEqual(o.read_bytes(),before)
        self.assertEqual(r['evidence']['final_iteration'],[1,2]);a=r['atoms'][0];self.assertEqual(a['polyhedron'],'FeO4')
        np.testing.assert_allclose(a['spins'][0]['global']['diagonal'],[.9,.3,.2,.4,.1])
        self.assertAlmostEqual(a['spins'][0]['global']['trace'],1.9)
        for sp in a['spins']:
            np.testing.assert_allclose(sp['global']['natural_occupations'],sp['local']['natural_occupations'],atol=1e-12)
            self.assertAlmostEqual(sp['groups']['e_occupation']+sp['groups']['t_occupation'],sp['global']['trace'])
        self.assertAlmostEqual(a['spins'][2]['global']['trace'],1.9+4.75)
        self.assertEqual(r['unit'],'electron');self.assertNotIn('eigenvalues_ev',a['spins'][0]['global'])
    def test_last_incomplete_step_never_uses_old_atoms(self):
        a=np.eye(5)
        p=self.root/'OUTCAR';p.write_text('Iteration 1(1)\n'+block(1,a,a)+block(2,a,a)+'Iteration 1(2)\n'+block(1,a,a))
        with self.assertRaisesRegex(ValueError,'缺少部分'):read_final_matrices(p)
        p.write_text('Iteration 1(1)\n'+block(1,a,a)+'Iteration 1(2)\natom = 1 l = 2\nonsite density matrix\nspin component  1\n0 0 0 0 0\n')
        with self.assertRaisesRegex(ValueError,'截断'):read_final_matrices(p)
    def test_soc_unfinished_and_structure_mismatch(self):
        p,o,_,_=fixture(self.root);original=o.read_text()
        for old,new,message in [('LSORBIT = F','LSORBIT = T','SOC'),('General timing and accounting informations','','结束'),('10 0 0 0 0 0','11 0 0 0 0 0','晶格')]:
            o.write_text(original.replace(old,new))
            with self.assertRaisesRegex(ValueError,message):analyze_occupation(p,o)
    def test_natural_matrix_retains_mixing(self):
        n=np.array([[.6,.3],[.3,.6]]);r=density_report(n)
        np.testing.assert_allclose(r['natural_occupations'],[.9,.3]);np.testing.assert_allclose(np.sum(r['weights'],axis=0),[1,1])
    def test_protocol_auto_check_render_and_csv(self):
        inputs=self.root/'inputs';inputs.mkdir();p,o,_,_=fixture(inputs);out=self.root/'outputs';out.mkdir();source={'id':'test-source','stage':'scf','state':'COMPLETED','name':'合成验收'}
        request={'sources':[source],'inputs':{role:[{'source_id':'test-source','name':path.name,'path':str(path),'size_bytes':path.stat().st_size}] for role,path in [('structure',p),('outcar',o)]},'options':{'polyhedron_types':['FeO4']},'output_dir':str(out)}
        self.assertEqual(collect(request,False)['mode'],'occupation');result=analyze(request);self.assertEqual(len(result['tables'][0]['rows']),3)
        view=render({'source_dir':str(out),'output_dir':str(self.root/'view')});self.assertEqual(view['entry_html'],'index.html');html=(self.root/'view/index.html').read_text();self.assertIn('局域 d 轨道占据',html);self.assertNotIn('/* OCCUPATION',html)
        self.assertNotIn('eV',(out/'occupation.csv').read_text(encoding='utf-8-sig'))
        request['options']['polyhedron_types']=['FeO6']
        with self.assertRaisesRegex(ValueError,'配体不足'):collect(request,False)
if __name__=='__main__':unittest.main()
