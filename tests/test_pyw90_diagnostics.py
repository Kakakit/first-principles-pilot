"""Offline numerical and user-visible comparison contracts; no SSH or optimizer."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack,redirect_stdout
from io import StringIO
from unittest.mock import patch
import numpy as np
from scipy.interpolate import splrep,splint
from scipy.integrate import fixed_quad
from backend.pyw90_dos import DosIntegrator,dos_ratio,projection_keys
from backend import wannier_diagnostics as diag,pyw90_adapter,pyw90_score,wannier_compare


class DosNumericsTests(unittest.TestCase):
    def test_same_cubic_spline_is_integrated_directly_and_reused(self):
        x=np.linspace(-4,4,301);y=np.exp(-((x+1)/.13)**2)+.3*np.exp(-((x-2)/.2)**2)
        integral=DosIntegrator();actual=integral(x,y,(-3,3))
        self.assertAlmostEqual(actual,splint(-3,3,splrep(x,y,s=0)),places=13)
        integral(x.copy(),y.copy(),(-2,2));integral(x,y,(-3,3))
        self.assertEqual(integral.fits,1)
        self.assertGreater(abs(fixed_quad(lambda e:np.exp(-((e+1)/.13)**2)+.3*np.exp(-((e-2)/.2)**2),-3,3,n=5)[0]-actual),.05)
        self.assertLess(abs(DosIntegrator('gauss300')(x,y,(-3,3))-actual),1e-5)

    def test_grid_domain_and_nonpositive_denominator_are_explicit(self):
        integral=DosIntegrator();x=np.arange(5,dtype=float)
        self.assertEqual(integral(x,np.ones(5),(10,11)),0)
        self.assertAlmostEqual(integral(x,np.ones(5),(-2,8)),4)
        for y in (np.array([1,2,np.nan,3,4]),np.ones(4)):
            with self.assertRaises(ValueError):integral(x,y,(0,1))
        with self.assertRaises(ValueError):integral(x[::-1],np.ones(5),(0,1))
        self.assertIsNone(dos_ratio(1,0)['percent'])
        self.assertEqual(dos_ratio(2,1)['percent'],2)
        self.assertTrue(dos_ratio(2,1)['warning'])

    def test_explicit_projection_mapping_never_substitutes_auto_orbitals(self):
        import pandas as pd
        names=['s','py','pz','px','dxy','dyz','dz2','dxz','dx2']
        frame=pd.DataFrame([{'species':'Fe','structure_id':site,'orb_name':orb,'key_string':f'Fe_{site}_{orb}'} for site in (0,1) for orb in names])
        self.assertEqual(len(projection_keys(['Fe:d'],frame)),10)
        self.assertEqual(len(projection_keys(['Fe@1:p'],frame)),3)
        for model in (['random'],['c=0,0,0:sp3'],['Fe:sp3'],['Cr:d']):
            with self.subTest(model=model),self.assertRaises(ValueError):projection_keys(model,frame)

    def test_one_xml_parse_generates_fixed_model_and_manual_window_evidence(self):
        try:
            from pymatgen.core import Structure,Lattice
            from pymatgen.electronic_structure.dos import CompleteDos,Dos
            from pymatgen.electronic_structure.core import Spin,Orbital
            from pyw90.lib.dos import DOS
            from pyw90.lib.w90 import W90
            from pyw90 import pre_w90_tool as pre
        except ImportError:self.skipTest('registered PyW90 environment')
        structure=Structure(Lattice.cubic(3),['Fe'],[[0,0,0]])
        x=np.linspace(-4,6,101);values=np.exp(-x*x)+.1
        complete=CompleteDos(structure,Dos(0,x,{Spin.up:9*values,Spin.down:18*values}),
                             {structure[0]:{orb:{Spin.up:values,Spin.down:2*values} for orb in list(Orbital)[:9]}})
        parsed=SimpleNamespace(parameters={'ISPIN':2},tdos=complete,complete_dos=complete)
        with tempfile.TemporaryDirectory() as temp,ExitStack() as stack:
            path=Path(temp)/'EIGENVAL'
            path.write_text('2 2 1 2\n1 1 1\n0\nCAR\ntest\n2 2 3\n\n 0.0 0.0 0.0 0.5\n1 -1 -1 1 1\n2 0 0 1 1\n3 1 1 0 0\n\n 0.5 0.0 0.0 0.5\n1 -.9 -.9 1 1\n2 .1 .1 1 1\n3 1.1 1.1 0 0\n')
            for module,name in ((W90,'read_eigenval'),(DOS,'get_dos_integral'),(DOS,'get_dos_df'),(DOS,'get_dos_analysis_df'),(DOS,'get_dos_from_selected'),(pre,'main_features')):
                stack.enter_context(patch.object(module,name,module.__dict__[name]))
            load=stack.enter_context(patch.object(pre,'Vasprun',return_value=parsed))
            stack.enter_context(patch('os.getcwd',return_value=temp))
            # Native W90 resolves the relative EIGENVAL with open(), not getcwd().
            original=pyw90_adapter.validate_layout
            stack.enter_context(patch.object(pyw90_adapter,'validate_layout',side_effect=lambda name:original(path)))
            from pymatgen.io.vasp.outputs import Eigenval
            stack.enter_context(patch('pymatgen.io.vasp.outputs.Eigenval',side_effect=lambda name:Eigenval(path)))
            with redirect_stdout(StringIO()):
                result=pyw90_adapter.dos_report({'num_wann':2,'channel':'down','absolute_range':[-2,3],
                    'projection_lines':['Fe:d'],'absolute_windows':{'dis_froz_min':-1.7,'dis_froz_max':.3}})
            self.assertEqual(load.call_count,1)
            self.assertEqual(result['projection_basis']['status'],'READY')
            self.assertEqual(len(result['projection_basis']['keys']),5)
            self.assertEqual(result['eigenval']['channel'],'down')
            self.assertAlmostEqual(result['current_dos']['percent'],5/9)
            self.assertLessEqual(result['numerics']['spline_fits'],2)


class ComparisonMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def channel(self,energies):
        return SimpleNamespace(x=np.arange(len(energies),dtype=float),energies=np.asarray(energies,dtype=float),segments=[(0,len(energies)-1)])

    def test_low_band_mismatch_and_out_of_window_samples_remain_in_groups(self):
        dft=np.tile(np.arange(12)*.1,(5,1));wannier=dft.copy();wannier[:,:6]+=10
        report=diag.metrics.reference_metrics(dft,wannier,0,[-1,2],0,6)
        self.assertEqual(report['reference_values'],60)
        self.assertEqual(report['groups'][0]['rmse_meV'],10000)
        self.assertEqual(report['groups'][1]['rmse_meV'],0)
        self.assertEqual(report['mae_meV'],5000)

    def test_reference_is_fixed_across_models_and_user_change_keeps_old_snapshot(self):
        dft=self.channel(np.tile(np.arange(6),(5,1)))
        first=self.channel(dft.energies[:,1:3]+.01);second=self.channel(dft.energies[:,2:4]+.01)
        identity={'project_id':'p','seed':'wannier90.1','band_attempt_id':'bands'}
        one=diag.evaluate(self.root,identity,dft,first,[-2,8])
        two=diag.evaluate(self.root,identity,dft,second,[-2,8])
        self.assertEqual(one['offset'],1);self.assertEqual(two['offset'],1)
        self.assertEqual(one['reference']['id'],two['reference']['id'])
        three=diag.evaluate(self.root,identity,dft,second,[-2,8],dft_band_start=3)
        self.assertNotEqual(three['reference']['id'],one['reference']['id'])
        self.assertEqual(len(list((self.root/'references').glob('*/'+one['reference']['id']+'.json'))),1)

    def test_missing_reference_bands_are_partial_without_complete_model_score(self):
        dft=np.tile(np.arange(69),(4,1));wannier=np.tile(np.arange(20,74),(4,1))
        report=diag.metrics.reference_metrics(dft,wannier,0,[0,100],20,allow_partial=True)
        self.assertEqual(report['status'],'PARTIAL');self.assertEqual(report['matched_bands'],49)
        self.assertIsNone(report['rmse_meV']);self.assertEqual(report['partial_metrics']['rmse_meV'],0)

    def test_disconnected_segments_are_interpolated_independently(self):
        dft=np.array([[0],[1],[100],[101]],float);model=dft.copy()
        aligned,_=diag.metrics.align_segments(np.array([0,1,1,2]),dft,[(0,1),(2,3)],np.array([0,2,2,3]),model,[(0,1),(2,3)])
        np.testing.assert_allclose(aligned,dft)

    def test_reference_samples_do_not_change_with_wannier_plot_density(self):
        dft=self.channel(np.tile(np.array([0.,2.]),(7,1)))
        first=self.channel(np.tile(np.array([.1,2.1]),(3,1)))
        second=self.channel(np.tile(np.array([.1,2.1]),(20,1)))
        identity={'project_id':'p','seed':'wannier90.1','band_attempt_id':'bands'}
        one=diag.evaluate(self.root,identity,dft,first,[-1,3])
        two=diag.evaluate(self.root,identity,dft,second,[-1,3])
        self.assertEqual(one['reference_values'],14);self.assertEqual(two['reference_values'],14)
        self.assertAlmostEqual(one['rmse_meV'],two['rmse_meV'])

    def test_native_evaluation_uses_installed_library_without_a_plot_or_eig(self):
        try:__import__('pyw90')
        except ImportError:self.skipTest('registered PyW90 environment')
        x=np.linspace(0,1,21);dft=np.tile(np.arange(6),(21,1))+x[:,None]*.01
        model=dft[:,1:3]+.001
        result=pyw90_score.score({'dft_x':x.tolist(),'wannier_x':x.tolist(),'dft_energy':dft.tolist(),'wannier_energy':model.tolist(),
                                  'metric_window_relative_eV':[-1,6],'fermi_eV':0})
        self.assertEqual(result['status'],'COMPLETED',result)
        self.assertAlmostEqual(result['quality_value'],1,places=8)
        self.assertEqual(result['dft_band_first'],2)
        self.assertEqual(pyw90_score.score({'spinors':True})['status'],'NOT_APPLICABLE')

    def test_native_failure_keeps_the_existing_plot_and_primary_metrics(self):
        identifier='a'*32;folder=self.root/identifier;folder.mkdir()
        (folder/'comparison.png').write_bytes(b'original plot')
        (folder/'score-input.json').write_text('{}')
        record={'id':identifier,'wannier_attempt_id':'w','diagnostics':{'rmse_meV':3},
                'diagnostic_input_sha256':hashlib.sha256(b'{}').hexdigest()}
        with patch.object(wannier_compare,'ROOT_COMPARE',self.root),patch.object(wannier_compare.execution,'get',return_value={'config':{'alias':'fixture'}}),patch.object(diag,'native',side_effect=ValueError('bounded failure')),patch.object(wannier_compare,'_render'):
            result=wannier_compare._augment(record,False)
        self.assertEqual(result['pyw90_diagnostic']['status'],'FAILED')
        self.assertEqual((folder/'comparison.png').read_bytes(),b'original plot')
        self.assertEqual(json.loads((folder/'record.json').read_text())['diagnostics']['rmse_meV'],3)
