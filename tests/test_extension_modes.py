"""Generic mode contracts, deliberately without optional extension imports."""
import copy,json,tempfile,unittest,shutil
from pathlib import Path
from unittest.mock import patch
from backend import extension_protocol as p,extension_runs as runs
BASE=Path(__file__).resolve().parents[1]

class ModeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        shutil.copytree(BASE/'tests/fixtures/extension-package',self.root/'package')
        path=self.root/'package/extension.manifest.json';self.manifest=json.loads(path.read_text())
        self.manifest.update(contexts=['attempt'],stages=['stage-a','stage-b'],evidence=['COMPLETED'])
        self.manifest['inputs']=[{'role':'alpha','title':'Alpha','names':['ALPHA'],'max_bytes':100},{'role':'beta','title':'Beta','names':['BETA'],'max_bytes':100}]
        self.manifest['analysis_modes']=[{'id':'first','title':'First','stages':['stage-a'],'input_roles':['alpha'],'evidence':['alpha_done']},{'id':'second','title':'Second','stages':['stage-b'],'input_roles':['beta'],'evidence':['beta_done']}]
        self.path=path;path.write_text(json.dumps(self.manifest))
    def source(self,stage='stage-a'):
        return {'kind':'attempt','stage':stage,'name':'fixture','id':'source','state':'COMPLETED','evidence':{'alpha_done':True},'files':[{'name':'ALPHA','size_bytes':3,'modified_at':'2026-10-20T00:00:00+00:00'}],'chosen_files':{},'remote_root':'/fixture'}
    def test_legacy_identity_and_mode_evidence(self):
        old=copy.deepcopy(self.manifest);old.pop('analysis_modes');self.assertIs(p.mode_manifest(old,[self.source()]),old)
        m=p.inspect_package(self.root/'package')['manifest'];active=p.mode_manifest(m,[self.source()]);self.assertEqual([r['role'] for r in active['inputs']],['alpha']);self.assertEqual(active['evidence'],['COMPLETED','alpha_done']);self.assertEqual(len(m['inputs']),2)
        self.assertEqual(runs.blockers(m,[self.source()]),[])
        s=self.source();s['evidence']={};self.assertTrue(any('alpha_done' in r for r in runs.blockers(m,[s])))
    def test_mixed_modes_and_missing_file_do_not_pass(self):
        self.assertIn('同一种模式',runs.blockers(self.manifest,[self.source(),self.source('stage-b')])[0])
        s=self.source();s['files']=[];self.assertTrue(any('Alpha' in r for r in runs.blockers(self.manifest,[s])))
    def test_freeze_omits_inactive_files(self):
        s=self.source();s['files'].append({'name':'BETA','size_bytes':10,'modified_at':'2026-10-20T00:00:00+00:00'})
        record={'manifest':self.manifest,'sources':[s],'id':'fixture','options':{},'location':'ssh'}
        request=runs._input_request(record,self.root/'request');self.assertEqual(set(request['inputs']),{'alpha'});self.assertEqual(request['inputs']['alpha'][0]['remote_path'],'/fixture/ALPHA')
    def test_invalid_modes_rejected(self):
        for mutate in [lambda m:m['analysis_modes'][1].update(stages=['stage-a']),lambda m:m['analysis_modes'][0].update(input_roles=['missing']),lambda m:m.update(analysis_modes=[]),lambda m:m['analysis_modes'].pop()]:
            m=copy.deepcopy(self.manifest);mutate(m);self.path.write_text(json.dumps(m));p._cache.clear()
            with self.assertRaises(ValueError):p.inspect_package(self.root/'package')
    def test_schema_accepts_optional_modes(self):
        from jsonschema import Draft7Validator
        schema=json.loads((BASE/'resources/extension-protocol/manifest.schema.json').read_text());Draft7Validator(schema).validate(self.manifest)

if __name__=='__main__':unittest.main()
