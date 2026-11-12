import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import extension_remote as remote

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'backend/builtin_analysis/legacy/lcalcpol-polarization-analysis'
spec=importlib.util.spec_from_file_location('pol_detail',PACKAGE/'scripts/calc_lcalcpol.py');calc=importlib.util.module_from_spec(spec);spec.loader.exec_module(calc)

class DetailTests(unittest.TestCase):
    def test_outcar_pair_evidence_and_cell_rounding(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'OUTCAR'
            out.write_text('volume of cell : 24.0000\ndirect lattice vectors reciprocal lattice vectors\n2.0000000 0.0000000 0.0000000 0 0 0\n0.0000000 3.0000000 0.0000000 0 0 0\n0.0000000 0.0000000 4.0000000 0 0 0\n'+(PACKAGE/'tests/fixtures/OUTCAR').read_text())
            result=calc._single_result(out,PACKAGE/'tests/fixtures/POSCAR')
            self.assertEqual(result['outcar_evidence']['lattice']['line'],2)
            self.assertIn('p[ion]',result['outcar_evidence']['ion']['text'])
            self.assertEqual(result['volume_angstrom3'],24)
            out.write_text(out.read_text().replace('2.0000000','2.1000000'))
            with self.assertRaisesRegex(ValueError,'晶格'):calc._single_result(out,PACKAGE/'tests/fixtures/POSCAR')
    def source(self):return {'id':'aa','name':'123.node','alias':'jutai-1','remote_root':'/work','files':[{'name':'OUTCAR','size_bytes':3,'modified_at':'1970-01-01T00:00:01+00:00'}]}
    def test_transfer_checks_before_and_after_and_allows_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'file'
            def pull(alias,items,timeout):target.write_bytes(b'abc')
            with patch.object(remote.ex,'ssh',return_value={'exit_code':0,'stdout':'login noise\nFPILOT_SOURCE=3:1\nFPILOT_PATH=/work/OUTCAR\n'}),patch('backend.safe_files.pull_many',side_effect=pull) as transfer:
                self.assertEqual(remote.source_pull(self.source(),'OUTCAR',target),target);self.assertEqual(target.read_bytes(),b'abc');transfer.assert_called_once()
            with patch.object(remote,'source_identity',side_effect=[('3:1','/work/OUTCAR'),('4:2','/work/OUTCAR')]),patch('backend.safe_files.pull_many',side_effect=pull):
                with self.assertRaisesRegex(ValueError,'传输过程中'):remote.source_pull(self.source(),'OUTCAR',target)
    def test_stale_metadata_blocks_transfer(self):
        with patch.object(remote,'source_identity',return_value=('4:2','/work/OUTCAR')),patch('backend.safe_files.pull_many') as pull:
            with self.assertRaisesRegex(ValueError,'123.node / OUTCAR.*预期 3:1.*实际 4:2'):remote.source_pull(self.source(),'OUTCAR',Path('/tmp/unused'))
            pull.assert_not_called()
    def test_distinct_source_failures(self):
        for response,word in [({'exit_code':3,'stdout':'FPILOT_SOURCE_MISSING'},'缺失'),({'exit_code':255,'stderr':'network down'},'SSH'),({'exit_code':0,'stdout':'unexpected'},'格式')]:
            with patch.object(remote.ex,'ssh',return_value=response):
                with self.assertRaisesRegex(ValueError,word):remote.source_identity(self.source(),'OUTCAR')

class FreshSourcesTests(unittest.TestCase):
    def test_terminal_cached_listing_is_bypassed_only_when_requested(self):
        from backend import extension_runs as runs,artifacts
        record={'id':'aa','project_id':'p','stage':'scf','state':'COMPLETED','config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path'}
        def listing(size):return {'files':[{'name':'OUTCAR','path':'OUTCAR','size_bytes':size,'modified_at':'1970-01-01T00:00:01+00:00'}]}
        with patch.object(runs.ex,'get',return_value=record),patch.object(runs.ex,'attempt_path',return_value={'shell':'/work'}),patch.object(artifacts,'listing',return_value=listing(3)),patch.object(artifacts,'refresh',return_value=listing(4)) as refresh:
            raw=[{'kind':'attempt','attempt_id':'aa'}]
            self.assertEqual(runs.describe_sources(raw)[0]['files'][0]['size_bytes'],3)
            self.assertEqual(runs.describe_sources(raw,refresh_files=True)[0]['files'][0]['size_bytes'],4)
            refresh.assert_called_once_with('aa')
