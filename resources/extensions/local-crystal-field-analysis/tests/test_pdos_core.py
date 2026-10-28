import copy,json,math,subprocess,sys,tempfile,unittest
from pathlib import Path
import numpy as np
PACKAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PACKAGE/'scripts'))
from pdos_core import analyze_pdos,spectral_moment
from presentation import render
from cli import collect,check,analyze

class PdosTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.output=tempfile.TemporaryDirectory();self.addCleanup(self.output.cleanup)
        import shutil
        for p in (PACKAGE/'fixtures/pdos').iterdir():shutil.copy(p,self.root/p.name)
    def parsed(self):return analyze_pdos(self.root/'DOSCAR',self.root/'POSCAR',self.root/'OUTCAR')
    def request(self):
        out=Path(self.output.name)
        return {'schema':'fpilot.extension-request.v2','analysis_id':'test','sources':[{'id':'source','stage':'dos','state':'COMPLETED','name':'Synthetic DOS'}],'inputs':{role:[{'source_id':'source','path':str(self.root/name),'name':name,'size_bytes':(self.root/name).stat().st_size}] for role,name in [('doscar','DOSCAR'),('structure','POSCAR'),('outcar','OUTCAR')]},'options':{},'output_dir':str(out),'result_file':str(out/'result.json')}
    def test_linear_density_exact_moment_and_interpolated_endpoints(self):
        for a,b in [(0.,2.),(.25,1.75)]:
            r=spectral_moment([0,1,2],[0,1,2],a,b)
            self.assertAlmostEqual(r['weight'],(b*b-a*a)/2)
            self.assertAlmostEqual(r['centroid_ev'],2*(b**3-a**3)/(3*(b*b-a*a)))
    def test_zero_negative_and_invalid_ranges(self):
        self.assertIsNone(spectral_moment([0,1],[0,0],0,1)['centroid_ev'])
        self.assertIsNone(spectral_moment([0,1],[-1,-1],0,1)['centroid_ev'])
        self.assertTrue(spectral_moment([0,1],[-1,3],0,1)['negative_values'])
        for x,y,a,b in [([0,0],[1,1],0,1),([0,1],[1,1],-1,1),([0,1],[1,1],1,0),([0,1],[1,float('nan')],0,1)]:
            with self.assertRaises(ValueError):spectral_moment(x,y,a,b)
    def test_atom_spin_and_orbital_order(self):
        d=self.parsed();self.assertEqual(len(d['atoms']),2);s=d['atoms'][0]['shells'][2]
        self.assertEqual(s['labels'],['dxy','dyz','dz2','dxz','dx2-y2']);self.assertEqual(len(s['spins']),2)
        self.assertAlmostEqual(s['spins'][1]['full_range_moments'][0]['centroid_ev']-s['spins'][0]['full_range_moments'][0]['centroid_ev'],.24,places=2)
        self.assertNotEqual(s['spins'][0]['density'],d['atoms'][1]['shells'][2]['spins'][0]['density'])
        self.assertEqual(d['evidence']['frame'],'VASP global Cartesian axes')
    def test_truncated_wrong_grid_and_structure(self):
        raw=(self.root/'DOSCAR').read_text()
        for text in [raw.rsplit('\n',4)[0],raw.replace('-3.9 ','-4.1 ',1),raw.replace('2 2 1 0','2 3 1 0',1)]:
            (self.root/'DOSCAR').write_text(text)
            with self.assertRaises(ValueError):self.parsed()
    def test_l_only_does_not_invent_orbitals(self):
        raw=(self.root/'DOSCAR').read_text().splitlines();n=81
        for start in [7+n,8+2*n]:
            for i in range(start,start+n):raw[i]=' '.join(raw[i].split()[:7])
        (self.root/'DOSCAR').write_text('\n'.join(raw)+'\n');d=self.parsed()
        self.assertEqual([s['l'] for s in d['atoms'][0]['shells']],[0]);self.assertTrue(any('未拆分' in w for w in d['warnings']))
    def test_noncollinear_uses_charge_not_magnetization(self):
        n=81;raw=(self.root/'DOSCAR').read_text().splitlines()
        for i in range(6,6+n):raw[i]=' '.join(raw[i].split()[:3])
        for start in [7+n,8+2*n]:
            for i in range(start,start+n):
                row=list(map(float,raw[i].split()));charges=row[1::2]
                raw[i]=' '.join(map(str,[row[0]]+[v for charge in charges for v in [charge,-9,8,-7]]))
        (self.root/'DOSCAR').write_text('\n'.join(raw)+'\n');p=self.root/'OUTCAR';p.write_text(p.read_text().replace('LSORBIT = F','LSORBIT = T'))
        d=self.parsed();s=d['atoms'][0]['shells'][2];self.assertEqual(len(s['spins']),1);self.assertTrue(min(s['spins'][0]['density'][0])>=0)
    def test_nonspinor_single_channel(self):
        n=81;raw=(self.root/'DOSCAR').read_text().splitlines()
        for i in range(6,6+n):raw[i]=' '.join(raw[i].split()[:3])
        for start in [7+n,8+2*n]:
            for i in range(start,start+n):
                row=raw[i].split();raw[i]=' '.join([row[0]]+row[1::2])
        (self.root/'DOSCAR').write_text('\n'.join(raw)+'\n');p=self.root/'OUTCAR';p.write_text(p.read_text().replace('ISPIN = 2','ISPIN = 1'))
        self.assertEqual(len(self.parsed()['atoms'][0]['shells'][2]['spins']),1)
    def test_no_completion_and_no_projections(self):
        p=self.root/'OUTCAR';p.write_text('ISPIN = 2\n')
        with self.assertRaisesRegex(ValueError,'结束'):self.parsed()
    def test_presentation_frozen_data_and_old_hamiltonian(self):
        request=self.request();r=analyze(request);self.assertEqual(r['scientific_validation'],'INSUFFICIENT')
        for name in ['DOSCAR','OUTCAR','POSCAR']:(self.root/name).unlink()
        out=self.root/'view';receipt=render({'source_dir':request['output_dir'],'output_dir':str(out)})
        self.assertTrue(all((out/a['path']).is_file() for a in receipt['assets']))
        index=json.loads((out/'data.json').read_text());self.assertEqual(index['mode'],'pdos');self.assertEqual(len(index['models'][0]['atoms']),2)
        shell=index['models'][0]['atoms'][0]['shells'][2];part=shell['chunks'][0];arrays=json.loads((out/part['path']).read_text())['parts'][part['key']]
        self.assertEqual(len(arrays['energy_ev']),81);self.assertEqual(len(arrays['density'][0]),5)
    def test_check_does_not_parse_large_dos_and_frozen_size_gate(self):
        request=self.request();self.assertIn('pdos',check(request));request['inputs']['doscar'][0]['size_bytes']+=1
        with self.assertRaisesRegex(ValueError,'大小变化'):collect(request)
    def test_mixed_modes_and_source_parent_guard(self):
        r=self.request();r['sources'].append({'id':'w','stage':'wannier90','state':'COMPLETED'})
        with self.assertRaisesRegex(ValueError,'同一种'):collect(r)
        r=self.request();r['output_dir']=str(self.root/'bad-output')
        with self.assertRaisesRegex(ValueError,'原输入目录'):collect(r)
    def test_cli_request_uses_prefixed_paths(self):
        r=self.request()
        for role,items in r['inputs'].items():
            item=items[0];old=Path(item['path']);new=old.with_name('0-'+role+'-'+old.name);old.rename(new);item['path']=str(new)
        p=self.root/'request.json';p.write_text(json.dumps(r));run=subprocess.run([sys.executable,str(PACKAGE/'scripts/cli.py'),'run','--request',str(p)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertEqual(json.loads((Path(r['output_dir'])/'pdos.json').read_text())['mode'],'pdos')
    def test_old_onsite_result_and_hamiltonian_mode(self):
        import shutil
        r=self.request();r['sources'][0]['stage']='wannier90';r['inputs']={}
        for role,name in [('hamiltonian','wannier90_hr.dat'),('wannier-input','wannier90.win'),('wannier-output','wannier90.wout')]:
            path=self.root/('0-'+role+'-'+name);shutil.copy(PACKAGE/'fixtures'/name,path)
            r['inputs'][role]=[{'source_id':'source','path':str(path),'name':name,'size_bytes':path.stat().st_size}]
        analyze(r);path=Path(r['output_dir'])/'onsite.json';old=json.loads(path.read_text());old.pop('mode');path.write_text(json.dumps(old))
        view=self.root/'old-view';render({'source_dir':r['output_dir'],'output_dir':str(view)})
        d=json.loads((view/'h0.json').read_text());self.assertEqual(d['reports'][0]['raw']['eigenvalues_ev'],[1.5]);self.assertEqual(json.loads((view/'data.json').read_text())['mode'],'hamiltonian')

if __name__=='__main__':unittest.main()
