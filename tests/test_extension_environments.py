"""Environment precedence and execution receipts, with no real SSH or new venv."""
import copy
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from backend import extension_protocol as p, extension_runs as runs, extension_remote as remote, extension_environments as env, extension_api as api, execution as ex, artifacts

BASE=Path(__file__).resolve().parents[1]
KEY='environment-fixture'
SI=(BASE/'tests/fixtures/Si.POSCAR').read_text()


class ExtensionEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (p,runs,remote,ex,artifacts):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(runs,'schedule'))
        self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('real SSH forbidden')))
        self.stack.enter_context(patch.dict(os.environ,{'FPILOT_ENV_TRACE':str(self.root/'trace.jsonl')}))
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)
        p._cache.clear();p._doctor_cache.clear()
        package=self.root/'template';shutil.copytree(BASE/'tests/fixtures/extension-package',package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest['id']=KEY;p.atomic_json(package/'extension.manifest.json',manifest)
        cli=package/'scripts/cli.py'
        cli.write_text("import os,sys,json\nfrom pathlib import Path\nif os.environ.get('FPILOT_ENV_TRACE'):\n with Path(os.environ['FPILOT_ENV_TRACE']).open('a') as trace: trace.write(json.dumps({'phase':sys.argv[1],'python':sys.executable,'prefix':sys.prefix})+'\\n')\n"+cli.read_text())
        p.install_directory(package)
        self.runtime={'alias':'server-a','python':sys.executable,'node':'','cores':None,'reason':'','config':{'alias':'server-a','scheduler':'pbs','queue':'batch','walltime':'00:10:00','remote_workspace_root':str(self.root/'fpilot')}}
        other=copy.deepcopy(self.runtime);other['alias']='server-b';other['config']['alias']='server-b';other['python']='/server-b/python'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'server-a':self.runtime,'server-b':other})
    def payload(self,**extra):return {'extension_id':KEY,'sources':[{'kind':'structure','poscar':SI}],'options':{'include_elements':True},**extra}
    def trace(self):return [json.loads(line) for line in (self.root/'trace.jsonl').read_text().splitlines()]
    def second(self):
        candidates=[os.environ.get('FPILOT_TEST_SECOND_PYTHON'),shutil.which('python3'),str(Path(sys.base_prefix)/'bin/python3')]
        for candidate in candidates:
            if candidate and Path(candidate).is_file() and candidate!=sys.executable:
                actual=json.loads(subprocess.check_output([candidate,'-c','import sys,json; print(json.dumps([sys.executable,sys.prefix,sys.version_info[:2]]))'],text=True))
                if actual[2]>=[3,9] and actual[:2]!=[sys.executable,sys.prefix]:return candidate,actual[0]
        self.skipTest('a second existing Python >=3.9 is unavailable')
    def test_default_override_scopes_and_clear(self):
        self.assertEqual(env.resolve(KEY)['python'],sys.executable)
        env.save_local({'python':'/local/default/python'})
        self.assertEqual(env.resolve(KEY)['python'],'/local/default/python')
        env.save_override(KEY,{'scope':'local','python':'/local/override/python'})
        env.save_override(KEY,{'scope':'remote','alias':'server-a','python':'/server-a/custom/python'})
        self.assertEqual(env.resolve(KEY)['python'],'/local/override/python')
        self.assertEqual(env.resolve(KEY,'ssh','server-a')['python'],'/server-a/custom/python')
        self.assertEqual(env.resolve(KEY,'scheduler','server-a')['python'],'/server-a/custom/python')
        self.assertEqual(env.resolve(KEY,'ssh','server-b')['python'],'/server-b/python')
        env.save_override(KEY,{'scope':'local','python':None});env.save_override(KEY,{'scope':'remote','alias':'server-a','python':None})
        self.assertEqual(env.settings()['overrides'],{})
        self.assertEqual(env.resolve(KEY)['python'],'/local/default/python')
        self.assertEqual(env.resolve(KEY,'ssh','server-a')['python'],sys.executable)
        env.save_local({'python':None});self.assertEqual(env.resolve(KEY)['python'],sys.executable)
    def test_invalid_paths_and_missing_override_do_not_fall_back(self):
        for value in ('python3','/path\npython',''):
            with self.assertRaises(ValueError):env.save_local({'python':value})
        env.save_override(KEY,{'scope':'local','python':str(self.root/'missing-python')})
        checked=env.check(KEY)
        self.assertFalse(checked['doctor']['ready']);self.assertTrue(p.installed(KEY)['enabled'])
        with self.assertRaisesRegex(ValueError,'本地环境'):runs.start(self.payload())
        self.assertEqual(runs.history(),[])
        env.save_override(KEY,{'scope':'local','python':None});self.assertTrue(env.check(KEY)['doctor']['ready'])
    def test_two_existing_interpreters_and_all_local_phases(self):
        second,actual=self.second()
        env.save_local({'python':second})
        inherited=env.check(KEY);self.assertEqual(inherited['runtime']['python'],second)
        self.assertEqual(self.trace()[-1]['python'],actual)
        env.save_override(KEY,{'scope':'local','python':sys.executable})
        self.assertTrue(env.check(KEY)['doctor']['ready'])
        record=runs.start(self.payload(runtime_python=sys.executable));key=record['analysis_id']
        (self.root/'trace.jsonl').write_text('')
        # A later default and override change must not rewrite an existing analysis.
        env.save_override(KEY,{'scope':'local','python':None});env.save_local({'python':str(self.root/'missing-new-default')})
        runs._execute(key);finished=runs.get(key)
        self.assertEqual(finished['state'],'COMPLETED',finished.get('error'))
        self.assertEqual(finished['runtime']['python'],sys.executable)
        # The already checked, frozen interpreter reuses its doctor receipt.
        self.assertEqual([r['phase'] for r in self.trace()],['check','run'])
        self.assertEqual({r['python'] for r in self.trace()},{sys.executable})
        self.assertEqual(finished['runtime_doctor']['python'],sys.executable)
        self.assertTrue(finished['runtime_doctor']['python_version'])
    def test_dependency_failure_is_specific_to_selected_environment(self):
        second,_=self.second()
        package=self.root/'dependency';shutil.copytree(BASE/'tests/fixtures/extension-package',package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest.update(id='dependency-fixture',dependencies=['jsonschema']);p.atomic_json(package/'extension.manifest.json',manifest)
        p.install_directory(package);self.assertTrue(env.check('dependency-fixture')['doctor']['ready'])
        env.save_override('dependency-fixture',{'scope':'local','python':second})
        result=env.check('dependency-fixture')['doctor']
        if result['ready']:self.skipTest('the second interpreter also has jsonschema')
        self.assertIn('jsonschema',result['missing']);self.assertTrue(p.recheck('dependency-fixture')['enabled'])
        env.save_override('dependency-fixture',{'scope':'local','python':None})
        self.assertTrue(env.check('dependency-fixture')['doctor']['ready'])
    def test_environment_change_invalidates_local_and_remote_previews(self):
        for location in ('local','ssh'):
            payload=self.payload(location=location,alias='server-a')
            preview=runs.preview(payload);payload['preview_id']=preview['preview_id']
            scope='local' if location=='local' else 'remote'
            binding={'scope':scope,'python':'/other/python',**({'alias':'server-a'} if scope=='remote' else {})}
            env.save_override(KEY,binding)
            with self.assertRaisesRegex(ValueError,'重新预览'):runs.start(payload)
            with self.assertRaisesRegex(ValueError,'环境已变化'):runs.check({**payload,'runtime_python':sys.executable})
            binding['python']=None;env.save_override(KEY,binding)
    def test_remote_doctor_runs_only_doctor_with_selected_python(self):
        second,actual=self.second();env.save_override(KEY,{'scope':'remote','alias':'server-a','python':second})
        (self.root/'trace.jsonl').write_text('')
        def ssh(alias,command,data=None,**kwargs):
            self.assertEqual(alias,'server-a');self.assertNotIn('qsub',command);self.assertNotIn('sbatch',command)
            result=subprocess.run(['bash','-c',command],input=data,capture_output=True,timeout=40)
            return {'exit_code':result.returncode,'stdout':result.stdout.decode(),'stderr':result.stderr.decode()}
        with patch.object(ex,'ssh',side_effect=ssh),patch.object(remote,'resource_preview') as probe,patch.object(remote,'submit') as submit,patch.object(remote,'direct') as direct:
            result=env.check(KEY,{'location':'ssh','alias':'server-a'})
        self.assertTrue(result['doctor']['ready'],result);self.assertEqual(result['doctor']['python'],second)
        self.assertEqual(self.trace(),[{'phase':'doctor','python':actual,'prefix':self.trace()[0]['prefix']}])
        self.assertEqual(runs.history(),[]);probe.assert_not_called();submit.assert_not_called();direct.assert_not_called()
    def test_remote_disconnect_and_bad_doctor_response_do_not_disable(self):
        for response in ({'exit_code':255,'stderr':'connection refused'},{'exit_code':0,'stdout':'not json'},{'exit_code':0,'stdout':'{"ready":true}'}):
            with patch.object(ex,'ssh',return_value=response):result=env.check(KEY,{'location':'ssh','alias':'server-a'})
            self.assertFalse(result['doctor']['ready']);self.assertTrue(p.installed(KEY)['enabled']);self.assertEqual(runs.history(),[])
    def test_remote_record_and_unknown_reconciliation_keep_frozen_python(self):
        preview=runs.preview(self.payload(location='ssh',alias='server-a'))
        receipt=runs.start(self.payload(location='ssh',alias='server-a',preview_id=preview['preview_id']))
        record=runs.get(receipt['analysis_id']);record.update(state='UNKNOWN',remote_dir='/workspace/analysis',submission_started=True);runs.save(record)
        env.save_override(KEY,{'scope':'remote','alias':'server-a','python':'/new/python'})
        def inspect(frozen):self.assertEqual(frozen['runtime']['python'],sys.executable);return {'exit':None,'job_id':None}
        with patch.object(remote,'inspect',side_effect=inspect),patch.object(remote,'direct') as direct,patch.object(remote,'submit') as submit:
            runs.refresh(record['id']);runs._execute(record['id'])
        self.assertEqual(runs.get(record['id'])['state'],'UNKNOWN');direct.assert_not_called();submit.assert_not_called()
    def test_api_old_fields_and_targetless_doctor_remain_compatible(self):
        app=FastAPI();app.include_router(api.router)
        def request(method,path,payload=None):
            async def exchange():
                messages=[];raw=b'' if payload is None else json.dumps(payload).encode()
                async def receive():return {'type':'http.request','body':raw,'more_body':False}
                async def send(value):messages.append(value)
                await app({'type':'http','method':method,'path':path,'query_string':b'','headers':[(b'content-type',b'application/json')],'http_version':'1.1','scheme':'http','server':('localhost',80),'client':('localhost',1),'root_path':''},receive,send)
                return messages[0]['status'],json.loads(b''.join(m.get('body',b'') for m in messages))
            return asyncio.run(exchange())
        with patch.object(api.servers,'registered_profiles',return_value=[]):
            old=request('GET','/api/extension-host/runtimes')[1]
            self.assertEqual(old['runtimes']['server-a'],self.runtime);self.assertEqual(old['aliases'],[])
            self.assertIn('local',old);self.assertIn('overrides',old)
            self.assertEqual(request('PUT','/api/extension-host/runtimes/local',{'python':sys.executable})[0],200)
            legacy=request('POST','/api/extension-host/packages/'+KEY+'/doctor')[1]
            self.assertTrue(legacy['doctor']['ready']);self.assertEqual(legacy['runtime']['python'],sys.executable)
            self.assertEqual(request('PUT','/api/extension-host/packages/'+KEY+'/runtime',{'scope':'local','python':None})[0],200)
    def test_old_history_does_not_gain_fabricated_environment(self):
        receipt=runs.start(self.payload());record=runs.get(receipt['analysis_id']);record.pop('runtime');record.pop('runtime_doctor');record['state']='COMPLETED';runs.save(record)
        env.save_local({'python':'/new/python'})
        self.assertNotIn('runtime',runs.history()[0]);self.assertNotIn('runtime_doctor',runs.get(record['id']))


if __name__=='__main__':unittest.main()
