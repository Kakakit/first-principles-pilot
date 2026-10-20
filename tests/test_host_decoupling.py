"""Host startup and built-in analysis work without optional extension sources."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import extension_protocol as protocol
from backend.builtin_analysis.comparison_metrics import measure_curves

BASE=Path(__file__).resolve().parents[1]


class HostDecouplingTests(unittest.TestCase):
    def test_installed_catalog_has_no_implicit_source_packages(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(protocol,'ROOT',Path(directory)):
            self.assertEqual(protocol.catalog(),[])
            self.assertEqual(protocol.bundle_ids(),[])
            with self.assertRaisesRegex(ValueError,'ZIP'):protocol.install_builtin('structure-symmetry')
            self.assertFalse((Path(directory)/'extensions-v2').exists())

    def test_host_import_and_builtin_analysis_without_extension_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            shutil.copytree(BASE/'backend',root/'backend',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            shutil.copytree(BASE/'resources',root/'resources',ignore=shutil.ignore_patterns('extensions','__pycache__','*.pyc','templates','wannier90'))
            self.assertFalse((root/'resources/extensions').exists())
            script='''import subprocess,sys,json
original=subprocess.Popen
def guard(argv,*args,**kwargs):
    if isinstance(argv,(list,tuple)) and str(argv[0]).split('/')[-1] in ('ssh','scp','sftp'):raise AssertionError('real SSH forbidden')
    return original(argv,*args,**kwargs)
subprocess.Popen=guard
from backend import app,postprocessing,extension_sdk,electronic_plots,wannier_compare,wannier_scans,wannier_diagnostics,extension_protocol
assert extension_protocol.catalog()==[]
root=extension_sdk.BUNDLES
structure=(root/'structure-symmetry/fixtures/Si.POSCAR').read_text()
assert postprocessing.local_structure_symmetry(structure)['results'][0]['space_group_number']==227
assert electronic_plots.SCRIPT.is_file() and electronic_plots.EXTRACTOR.is_file()
subprocess.run([sys.executable,str(electronic_plots.SCRIPT),'--help'],stdout=subprocess.DEVNULL,check=True,timeout=20)
print(json.dumps({'host_import':True,'space_group':227,'optional_catalog':[],'real_ssh':0}))
'''
            env={**os.environ,'PYTHONPATH':str(root),'FPILOT_WORKSPACE':str(root/'workspace'),'FPILOT_REMOTE_DISABLED':'1'}
            result=subprocess.run([sys.executable,'-c',script],cwd=root,env=env,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr[-3000:])
            self.assertEqual(json.loads(result.stdout)['space_group'],227)

    def test_window_measurement_preserves_offset_units_and_reference_mask(self):
        import csv
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'curves.csv'
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['method','k_index','band','energy_relative_eV'])
                for point in (1,2):
                    for band,energy in enumerate((-10,-.5,.5),1):writer.writerow(['DFT',point,band,energy])
                    writer.writerow(['Wannier90',point,1,-.499]);writer.writerow(['Wannier90',point,2,8])
            metric=measure_curves({'curves_csv':str(path),'target_relative_eV':[-1,1],'dft_band_offset':1})
            self.assertEqual(metric['status'],'MEASURED')
            self.assertEqual(metric['dft_band_first'],2)
            self.assertEqual(metric['reference_values'],4)
            self.assertAlmostEqual(metric['max_abs_diff_meV'],7500)
            self.assertEqual(metric['selection'],'caller_fixed_order_preserving_correspondence')
