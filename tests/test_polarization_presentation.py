import importlib.util,json,tempfile,unittest
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[1]/'resources/extensions/lcalcpol-polarization-analysis'
spec=importlib.util.spec_from_file_location('pol_detail',PACKAGE/'scripts/calc_lcalcpol.py');calc=importlib.util.module_from_spec(spec);spec.loader.exec_module(calc)
class PresentationTests(unittest.TestCase):
    def test_old_and_new_presentation(self):
        spec=importlib.util.spec_from_file_location('pol_present',PACKAGE/'scripts/presentation.py');view=importlib.util.module_from_spec(spec);spec.loader.exec_module(view)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=calc._single_result(PACKAGE/'tests/fixtures/OUTCAR',PACKAGE/'tests/fixtures/POSCAR')
            for legacy in (False,True):
                if legacy:data.pop('outcar_evidence')
                (root/'polarization.json').write_text(json.dumps(data));out=root/str(legacy)
                manifest=view.render({'source_dir':str(root),'output_dir':str(out)})
                self.assertEqual(manifest['entry_html'],'index.html')
                self.assertEqual(json.loads((out/'data.json').read_text())['endpoints'][0]['p_ion_e_ang'],data['p_ion_e_ang'])

