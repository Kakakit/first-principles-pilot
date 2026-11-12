import base64
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch
from tests.extension_fixtures import attempt_package,direct_package
from backend import extension_protocol as p, extension_runs as runs, extension_remote as remote, execution as ex, artifacts, execution_control as control

BASE=Path(__file__).resolve().parents[1]
TEMPLATE=BASE/'tests/fixtures/extension-package'
SI=(BASE/'tests/fixtures/Si.POSCAR').read_text()

class ExtensionHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (p,runs,remote,ex,artifacts):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(runs,'schedule'))
        # These cases test execution; optional presentation lifecycle is tested separately.
        self.stack.enter_context(patch('backend.extension_presentations.schedule'))
        self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('real SSH forbidden in tests')))
        p._cache.clear();p._doctor_cache.clear()
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)
    def install(self,key='structure-volume-statistics'):
        package=TEMPLATE if key=='structure-volume-statistics' else direct_package(self.root/key) if key=='sample-analysis' else attempt_package(self.root/key,key)
        return p.install_directory(package)
    def payload(self,key='structure-volume-statistics',**extra):return {'extension_id':key,'sources':[{'kind':'structure','poscar':SI}], 'options':{'include_elements':True},**extra}
    def execute(self,payload):
        result=runs.start(payload);runs._execute(result['analysis_id']);return runs.get(result['analysis_id'])
    def test_renamed_template_installs_runs_and_uninstalls_with_history(self):
        package=self.root/'copy';shutil.copytree(TEMPLATE,package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest.update(id='my-volume',name='My volume');p.atomic_json(package/'extension.manifest.json',manifest)
        installed=p.install_directory(package);self.assertTrue(installed['enabled'])
        record=self.execute(self.payload('my-volume'));self.assertEqual(record['state'],'COMPLETED',record.get('error'))
        self.assertEqual(record['result']['tables'][0]['rows'][0]['atoms'],2)
        self.assertGreater(record['result']['tables'][0]['rows'][0]['volume'],0)
        p.enable('my-volume',False);self.assertFalse(any(e['id']=='my-volume' for e in runs.context(self.payload())['extensions']))
        p.uninstall('my-volume');self.assertTrue(list((self.root/'extensions-v2/recycle').iterdir()))
        self.assertEqual(runs.get(record['id'])['state'],'COMPLETED')
        self.assertTrue(runs.output(record['id'],'elements.csv')[0].is_file())
        self.assertEqual((package/'fixtures/POSCAR').read_text(),(TEMPLATE/'fixtures/POSCAR').read_text())
    def test_remote_timeout_reports_the_stage_that_did_not_finish(self):
        doctor = {'schema':'fpilot.extension-doctor.v2','ready':True,'missing':[]}
        check = {'schema':'fpilot.extension-check.v2','status':'APPLICABLE','reason':'ready'}
        for diagnosis, applicable, expected in [
            (None, None, '环境自检超时'),
            (doctor, None, '检查分析输入超时'),
            (doctor, check, '执行分析超时'),
        ]:
            with self.subTest(expected=expected):
                receipt = {'exit':{'exit_code':124},'doctor':diagnosis,'check':applicable}
                record = {'id':'a'*32,'location':'ssh','runtime':{'python':sys.executable}}
                with patch.object(remote,'inspect',return_value=receipt):
                    refreshed = runs._refresh(record)
                self.assertEqual(refreshed['state'],'FAILED')
                self.assertIn(expected,refreshed['error'])
    def test_completed_remote_run_can_retry_collection_without_dispatch(self):
        key='a'*32
        record={'id':key,'state':'FAILED','location':'ssh','exit_code':0,'remote_dir':'/work/analysis'}
        with patch.object(runs,'get',return_value=record), patch.object(remote,'inspect',return_value={'exit':{'exit_code':0}}), patch.object(remote,'output_info',return_value={'result.json':{'size_bytes':11*1024**2}}), patch.object(remote,'pull') as pull:
            updated=runs.refresh(key)
        pull.assert_not_called()
        self.assertIn('结果摘要 result.json 为 11.0 MiB',updated['error'])
        self.assertEqual(updated['exit_code'],0)
    def test_project_artifact_listing_uses_only_available_same_project_outputs(self):
        key='b'*32
        target=runs.directory(key)/'outputs'/'identity.json'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'{}')
        record={'id':key,'project_id':'project-a','created_at':'2026-11-07',
                'state':'COMPLETED','extension_id':'sample-analysis','manifest':{},
                'sources':[{'attempt_id':'source-a','name':'frozen attempt'}],
                'outputs':[{'path':target.name,'available':True,'size_bytes':2}]}
        runs.save(record)
        self.assertEqual(runs.project_artifacts('project-a','sample-analysis',target.name)[0]['sources'],record['sources'])
        self.assertEqual(runs.project_artifacts('project-b','sample-analysis',target.name),[])
        target.write_bytes(b'changed')
        self.assertEqual(runs.project_artifacts('project-a','sample-analysis',target.name),[])
    def test_zip_one_action_and_no_external_schema(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for file in TEMPLATE.rglob('*'):
                if file.is_file() and '__pycache__' not in file.parts:archive.write(file,'sample/'+str(file.relative_to(TEMPLATE)))
        self.assertTrue(p.import_zip(base64.b64encode(stream.getvalue()).decode())['enabled'])
        package=self.root/'invalid';shutil.copytree(TEMPLATE,package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest['options_schema']={'type':'object','$ref':'https://example.invalid/a'};p.atomic_json(package/'extension.manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'引用'):p.install_directory(package)
    def test_missing_dependency_preserves_enabled_but_blocks_execution(self):
        package=self.root/'missing';shutil.copytree(TEMPLATE,package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest['dependencies']=['fpilot_missing_fixture_dependency'];p.atomic_json(package/'extension.manifest.json',manifest)
        item=p.install_directory(package);self.assertTrue(item['enabled']);self.assertIn('fpilot_missing_fixture_dependency',item['doctor']['missing'])
        self.assertTrue(p.enable(item['id'],True)['enabled'])
        with self.assertRaisesRegex(ValueError,'本地环境'):runs.start(self.payload())
        self.assertTrue(any('本机缺依赖' in reason for row in runs.context(self.payload())['extensions'] for reason in row['blockers']))
    def test_options_and_stage_roles_block_without_running(self):
        self.install()
        with self.assertRaisesRegex(ValueError,'参数'):runs.start(self.payload(options={'include_elements':'bad'}))
        self.install('sample-attempt')
        self.assertNotIn('sample-attempt',[e['id'] for e in runs.context(self.payload())['extensions']])
        with self.assertRaisesRegex(ValueError,'来源类型'):runs.start(self.payload('sample-attempt',options={'tolerance':1e-8}))
    def test_bad_structure_is_not_applicable_and_never_runs(self):
        self.install();record=self.execute(self.payload(sources=[{'kind':'structure','poscar':'invalid'}]));self.assertEqual(record['state'],'NOT_APPLICABLE');self.assertIsNone(record['result'])
    def test_uninstall_after_queue_does_not_break_frozen_package(self):
        self.install();receipt=runs.start(self.payload());p.uninstall('structure-volume-statistics');runs._execute(receipt['analysis_id']);self.assertEqual(runs.get(receipt['analysis_id'])['state'],'COMPLETED')
        with patch.object(runs,'_local') as runner:runs._execute(receipt['analysis_id']);runner.assert_not_called()
    def test_result_validation_and_lineage_are_authoritative(self):
        self.install();record=self.execute(self.payload());dest=runs.directory(record['id']);result=record['result'];result['sources']=[{'id':'wrong'}];p.atomic_json(dest/'outputs/result.json',result);runs._complete(record);self.assertNotEqual(record['result']['sources'][0]['id'],'wrong')
        self.assertEqual(json.loads(runs.output(record['id'],'result.json')[0].read_text())['sources'],record['result']['sources'])
        result['artifacts'][0]['path']='../analysis.json';p.atomic_json(dest/'outputs/result.json',result)
        with self.assertRaises(ValueError):runs._complete(record)
    def test_cli_failure_keeps_source_and_error_record(self):
        self.install();receipt=runs.start(self.payload())
        cli=runs.directory(receipt['analysis_id'])/'package/scripts/cli.py'
        cli.write_text(cli.read_text().replace('payload=','payload=')+'\n')
        with patch.object(runs,'_local',side_effect=ValueError('fixture failure')):runs._execute(receipt['analysis_id'])
        record=runs.get(receipt['analysis_id']);self.assertEqual(record['state'],'FAILED');self.assertEqual(record['sources'][0]['poscar'],SI);self.assertEqual(record['error'],'fixture failure')
    def mock_attempts(self,files,stage='scf',state='COMPLETED',evidence=None):
        evidence=evidence if evidence is not None else {'vasp_timing':True,'ediff_reached':True}
        self.stack.enter_context(patch.object(ex,'get',side_effect=lambda key:{'id':key,'project_id':'project','node_id':'node-'+key,'stage':stage,'state':state,'config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path','remote_project_path_absolute':'/workspace/project','evidence':evidence}))
        self.stack.enter_context(patch.object(ex,'attempt_path',return_value={'root':'/workspace','relative':'source','shell':'/workspace/source'}))
        self.stack.enter_context(patch.object(artifacts,'listing',side_effect=lambda key:{'files':[{'name':name,'path':name,'size_bytes':len(data),'modified_at':'2026-10-01T00:00:00+00:00'} for name,data in files[key].items()]}))
        self.stack.enter_context(patch.object(artifacts,'refresh',side_effect=lambda key:{'files':[{'name':name,'path':name,'size_bytes':len(data),'modified_at':'2026-10-01T00:00:00+00:00'} for name,data in files[key].items()]}))
        def pull(source,name,target):raw=files[source['attempt_id']][name];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);return raw
        self.stack.enter_context(patch.object(remote,'source_pull',side_effect=pull))
    def test_two_sources_preserve_result_units_and_lineage(self):
        self.install('sample-attempt')
        first={'POSCAR':SI.encode(),'OUTCAR':b'synthetic output'};files={'aa':first,'bb':dict(first)};self.mock_attempts(files)
        record=self.execute({'extension_id':'sample-attempt','project_id':'project','sources':[{'kind':'attempt','attempt_id':'aa'},{'kind':'attempt','attempt_id':'bb'}],'options':{'tolerance':1e-8}})
        self.assertEqual(record['state'],'COMPLETED',record.get('error'));self.assertEqual(record['scientific_validation'],'INSUFFICIENT');self.assertEqual([row['raw'] for row in record['result']['tables'][0]['rows']],[0,0,0]);self.assertEqual(record['result']['tables'][0]['columns'][0]['unit'],'C/m²');self.assertEqual(len(record['result']['sources']),2)
    def test_nonconverged_and_large_files_report_distinct_reasons(self):
        self.install('sample-attempt');self.mock_attempts({'aa':{'POSCAR':SI.encode(),'OUTCAR':b'x'}},evidence={'vasp_timing':True})
        sources=runs.describe_sources([{'kind':'attempt','attempt_id':'aa'}],'project');m=p.installed('sample-attempt')['manifest'];self.assertIn('已完成运行：缺少 ediff_reached 证据',runs.blockers(m,sources))
        sources[0]['evidence']['ediff_reached']=True;sources[0]['files'][1]['size_bytes']=artifacts.MAX_PULL_BYTES+1
        self.assertTrue(any('10 MiB' in s for s in runs.blockers(m,sources,'local')));self.assertFalse(runs.blockers(m,sources,'ssh'));self.assertTrue(runs.blockers(m,sources,'scheduler'))
    def test_64_sources_manifest_and_request_share_limits(self):
        from jsonschema import Draft7Validator
        package=self.root/'many';shutil.copytree(TEMPLATE,package)
        manifest=json.loads((package/'extension.manifest.json').read_text())
        manifest['contexts']=['attempt'];manifest['stages']=[];manifest['sources']={'min':2,'max':64}
        p.atomic_json(package/'extension.manifest.json',manifest)
        self.assertEqual(p.inspect_package(package)['manifest']['sources']['max'],64)
        data={str(i):{'POSCAR':SI.encode()} for i in range(64)};self.mock_attempts(data)
        raw=[{'kind':'attempt','attempt_id':str(i)} for i in reversed(range(64))]
        sources=runs.describe_sources(raw,'project')
        self.assertFalse(runs.blockers(manifest,sources))
        request=runs._input_request({'id':'check','manifest':manifest,'sources':sources,'location':'ssh','options':{}},self.root/'request')
        self.assertEqual([item['source_id'] for item in request['inputs']['structure']],[str(i) for i in reversed(range(64))])
        self.assertEqual(len(request['inputs']['structure']),64)
        for root in (BASE/'resources/extension-protocol',TEMPLATE/'protocol'):
            Draft7Validator(json.loads((root/'manifest.schema.json').read_text())).validate(manifest)
            Draft7Validator(json.loads((root/'request.schema.json').read_text())).validate(request)
        with self.assertRaisesRegex(ValueError,'1–64'):runs.describe_sources(raw+[raw[0]],'project')
        with self.assertRaisesRegex(ValueError,'重复'):runs.describe_sources([raw[0],raw[0]],'project')
        manifest['sources']['max']=65;p.atomic_json(package/'extension.manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'1–64'):p.inspect_package(package)
    def test_many_sources_keep_package_evidence_and_project_gates(self):
        data={str(i):{'POSCAR':SI.encode()} for i in range(9)};self.mock_attempts(data,evidence={})
        raw=[{'kind':'attempt','attempt_id':str(i)} for i in range(9)]
        manifest=copy.deepcopy(p.inspect_package(TEMPLATE)['manifest']);manifest['contexts']=['attempt'];manifest['stages']=[]
        sources=runs.describe_sources(raw,'project')
        self.assertIn('需要 1–1 个来源',runs.blockers(manifest,sources))
        manifest['sources']['max']=64;manifest['evidence']=['ediff_reached']
        self.assertEqual(len(runs.blockers(manifest,sources)),9)
        with self.assertRaisesRegex(ValueError,'当前项目'):runs.describe_sources(raw,'another-project')
    def test_catalog_includes_older_records_and_excludes_recycled_or_foreign(self):
        # Actual local database path, not the execution page's default 100-row slice.
        from unittest.mock import MagicMock
        store=MagicMock();store.read.return_value={'workspace':{'projects':[{'id':'project','nodes':[{'id':'node','data':{'label':'EOS SCF'}}]}]}}
        with patch.object(runs,'WorkspaceStore',return_value=store),closing(ex.db()) as database:
            (self.root/'inputs').mkdir()
            with zipfile.ZipFile(self.root/'inputs'/('a'*64+'.zip'),'w') as archive:archive.writestr('manifest.json',json.dumps({'reason':'体积比 1.03'}))
            for i in range(103):
                record={'id':str(i),'project_id':'foreign' if i==102 else 'project','node_id':'node','stage':'scf','state':'COMPLETED','job_id':str(i)+'.node01','config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path','recycled':i==101}
                if i==0:record['bundle_id']='a'*64
                database.execute('INSERT INTO attempts VALUES(?,?,?)',(str(i),'COMPLETED',json.dumps(record)))
            database.commit()
            catalog=runs.source_catalog('project')
            self.assertEqual(catalog['max_sources'],64);self.assertEqual(len(catalog['attempts']),101)
            self.assertEqual(catalog['attempts'][-1]['id'],'0');self.assertEqual(catalog['attempts'][0]['name'],'EOS SCF')
            self.assertEqual(catalog['attempts'][-1]['input_reason'],'体积比 1.03')
            self.assertNotIn('files',catalog['attempts'][0])
            with self.assertRaisesRegex(ValueError,'不存在'):runs.source_catalog('missing')
    def runtime(self,scheduler='pbs'):
        value={'alias':'jutai-1','python':sys.executable,'node':'node01','cores':2,'reason':'fixture','config':{'alias':'jutai-1','scheduler':scheduler,'queue':'batch','walltime':'00:10:00','remote_workspace_root':str(self.root/'230269265lht')}}
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':value});return value
    def remote_payload(self,mode='ssh'):
        self.runtime();self.install();payload=self.payload(location=mode,alias='jutai-1');self.stack.enter_context(patch.object(remote,'resource_preview',return_value={'ready':True,'selection':{'requested_cores':2},'probe':'fixture'}));payload['preview_id']=runs.preview(payload)['preview_id'];return payload
    def test_remote_preview_binding_rejects_later_changes(self):
        payload=self.remote_payload();payload['options']={'include_elements':False}
        with self.assertRaisesRegex(ValueError,'重新预览'):runs.start(payload)
    def test_unchanged_remote_preview_survives_age_and_listing_status(self):
        payload=self.remote_payload();path=self.root/'analysis-previews'/(payload['preview_id']+'.json')
        approval=json.loads(path.read_text());approval['created_at']='2000-01-01T00:00:00+00:00'
        approval['binding']['sources'][0].update(host_state='COOLDOWN',needs_refresh=True,refreshing=True)
        p.atomic_json(path,approval)
        self.assertEqual(runs.start(payload)['state'],'QUEUED')
    def test_ssh_preview_accepts_home_workspace_without_resource_probe(self):
        self.install();runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME/230269265lht'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        with patch.object(remote,'resource_preview') as probe:
            receipt=runs.preview(self.payload(location='ssh',alias='jutai-1'))
        self.assertTrue(receipt['ready']);self.assertIn('analyses/analysis-preview',receipt['script'])
        self.assertIn('doctor.json',receipt['script']);self.assertNotIn('#PBS',receipt['script']);probe.assert_not_called()
    def test_source_project_wins_over_home_runtime_and_current_project_settings(self):
        self.install();self.mock_attempts({'aa':{'POSCAR':SI.encode()}})
        runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        payload=self.payload(project_id='project',sources=[{'kind':'attempt','attempt_id':'aa'}],location='ssh',alias='jutai-1')
        with patch.object(runs,'WorkspaceStore') as store:
            preview=runs.preview(payload);payload['preview_id']=preview['preview_id'];key=runs.start(payload)['analysis_id']
            store.assert_not_called()
        self.assertEqual(preview['remote_analysis_root'],'/workspace/project/analyses')
        self.assertIn('/workspace/project/analyses/analysis-preview',preview['script'])
        self.assertIn('/workspace/project/analyses',preview['reason'])
        record=runs.get(key);self.assertEqual(remote.analysis_directory(record),'/workspace/project/analyses/'+key)
        # Waiting analyses keep the approved path even after project/runtime edits.
        with patch.object(remote,'stage',return_value='/workspace/project/analyses/'+key) as stage,patch.object(remote,'direct',side_effect=ValueError('fixture disconnect')):
            runs._execute(key)
        self.assertEqual(stage.call_args.args[0]['remote_analysis_root'],'/workspace/project/analyses')
        self.assertIn('cd /workspace/project/analyses/'+key,(runs.directory(key)/'launch.sh').read_text())
        self.assertNotIn('$HOME/analyses',(runs.directory(key)/'launch.sh').read_text())
    def test_structure_project_layout_and_changes_require_new_preview(self):
        from unittest.mock import MagicMock
        self.install();runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        project={'id':'project','remoteSlug':'sample','remotePathPolicy':'project-directory-v2','remoteWorkspaceRoot':'/work/research'}
        store=MagicMock();store.read.side_effect=lambda:{'workspace':{'projects':[project]}}
        payload=self.payload(project_id='project',location='ssh',alias='jutai-1')
        with patch.object(runs,'WorkspaceStore',return_value=store):
            preview=runs.preview(payload);self.assertEqual(preview['remote_analysis_root'],'/work/research/sample/analyses')
            payload['preview_id']=preview['preview_id'];project['remoteWorkspaceRoot']='/work/moved'
            with self.assertRaisesRegex(ValueError,'分析目录.*重新预览'):runs.start(payload)
            project['remoteDirectories']={'jutai-1':'/scratch/custom-project'}
            preview=runs.preview(payload);self.assertEqual(preview['remote_analysis_root'],'/scratch/custom-project/analyses')
            payload['preview_id']=preview['preview_id'];key=runs.start(payload)['analysis_id']
            project['remoteDirectories']['jutai-1']='/scratch/later'
        self.assertEqual(remote.analysis_directory(runs.get(key)),'/scratch/custom-project/analyses/'+key)
        project.pop('remoteDirectories');project.pop('remotePathPolicy')
        with patch.object(runs,'WorkspaceStore',return_value=store):
            self.assertEqual(runs.preview(payload)['remote_analysis_root'],'/work/moved/material-projects/sample/analyses')
            with self.assertRaisesRegex(ValueError,'项目不存在'):runs.preview({**payload,'project_id':'missing'})
    def test_old_attempt_layout_and_multiple_source_roots_follow_primary(self):
        runtime=self.runtime();sources=[{'kind':'attempt','attempt_id':'aa','remote_root':'/old/project/flows/single/stages/scf/attempts/first'},
                                        {'kind':'attempt','attempt_id':'bb','remote_root':'/new/project/flows/main/stages/02-scf/attempts/second'}]
        with patch.object(ex,'get',side_effect=lambda key:{'project_id':'project'}):
            self.assertEqual(runs._analysis_root({},sources,runtime),'/old/project/analyses')
            self.assertEqual(runs._analysis_root({},list(reversed(sources)),runtime),'/new/project/analyses')
    def test_project_override_stages_and_runs_actual_shell_in_isolated_directory(self):
        from unittest.mock import MagicMock
        self.install();runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        project_root=self.root.resolve()/"remote project's data"
        project={'id':'project','remoteSlug':'sample','remoteDirectories':{'jutai-1':str(project_root)}}
        store=MagicMock();store.read.return_value={'workspace':{'projects':[project]}}
        payload=self.payload(project_id='project',location='ssh',alias='jutai-1')
        with patch.object(runs,'WorkspaceStore',return_value=store):
            payload['preview_id']=runs.preview(payload)['preview_id'];key=runs.start(payload)['analysis_id']
        def ssh(alias,command,data=None,**kwargs):
            response=subprocess.run(['bash','-c',command],input=data,capture_output=True,timeout=30)
            return {'exit_code':response.returncode,'stdout':response.stdout.decode(),'stderr':response.stderr.decode()}
        def direct(record):
            response=subprocess.run(['bash',record['remote_dir']+'/launch.sh'],capture_output=True,text=True,timeout=30)
            return {'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr}
        def pull(record,name,target):
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(record['remote_dir'])/name,target);return target
        with patch.object(ex,'ssh',side_effect=ssh),patch.object(remote,'direct',side_effect=direct),patch.object(remote,'pull',side_effect=pull):
            runs._execute(key)
        record=runs.get(key);self.assertEqual(record['state'],'COMPLETED',record.get('error'))
        destination=project_root/'analyses'/key
        self.assertEqual(record['remote_dir'],str(destination));self.assertTrue((destination/'outputs/result.json').is_file())
        self.assertEqual(json.loads((destination/'request.json').read_text())['output_dir'],str(destination/'outputs'))
        self.assertEqual(record['runtime']['config']['remote_workspace_root'],'$HOME')
    def test_unbound_home_and_unfrozen_legacy_dispatch_do_not_create_remote_directory(self):
        self.install();runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        with self.assertRaisesRegex(ValueError,'指定项目目录或工作区'):runs.preview(self.payload(location='ssh',alias='jutai-1'))
        payload=self.remote_payload();payload['preview_id']=runs.preview(payload)['preview_id']
        approval=self.root/'analysis-previews'/(payload['preview_id']+'.json');old=json.loads(approval.read_text())
        old['binding'].pop('remote_analysis_root');p.atomic_json(approval,old)
        with self.assertRaisesRegex(ValueError,'重新预览'):runs.start(payload)
        with patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'未冻结项目分析目录'):
                remote.stage({'id':'a'*32,'runtime':runtime},self.root,{})
            ssh.assert_not_called()
    def test_old_scheduler_unknown_only_recovers_original_receipt(self):
        control.save_settings({'max_inflight_total':2,'max_inflight_per_host':1,'max_cores_per_host':None})
        payload=self.remote_payload();key=runs.start(payload)['analysis_id']
        record=runs.get(key);record.pop('remote_analysis_root');record.update(location='scheduler',state='UNKNOWN',submission_started=True,remote_dir='/workspace/analyses/'+key);runs.save(record)
        with patch.object(runs,'_analysis_root',side_effect=AssertionError('historical path must stay frozen')),patch.object(remote,'inspect',return_value={'job_id':'731.server','exit':None}) as inspect,patch.object(remote,'queued',return_value=True),patch.object(remote,'submit') as submit:
            self.assertEqual(runs.refresh(key)['job_id'],'731.server');self.assertEqual(runs.get(key)['state'],'SUBMITTED');submit.assert_not_called()
            self.assertEqual(inspect.call_args.args[0]['remote_dir'],'/workspace/analyses/'+key)
        with closing(ex.db()) as db:
            with self.assertRaisesRegex(ValueError,'在途'):control.enforce_limits(db,{'id':'other','config':{'alias':'jutai-1'},'profile':{'ppn':2}})

    def test_scheduler_preview_and_old_unstarted_dispatch_are_blocked(self):
        payload=self.remote_payload()
        with patch.object(remote,'stage') as stage,patch.object(remote,'submit') as submit,patch.object(remote,'resource_preview') as probe:
            for action in (runs.preview,runs.start):
                with self.assertRaisesRegex(ValueError,'直接命令行'):action({**payload,'location':'scheduler'})
            key=runs.start(payload)['analysis_id'];record=runs.get(key);record.update(location='scheduler');runs.save(record);runs._execute(key)
            self.assertEqual(runs.get(key)['state'],'FAILED');stage.assert_not_called();submit.assert_not_called();probe.assert_not_called()

    def test_known_remote_exit_with_invalid_output_is_failed_not_unknown(self):
        payload=self.remote_payload('ssh');receipt=runs.start(payload);key=receipt['analysis_id']
        with patch.object(remote,'stage',return_value='/workspace/analyses/'+key),patch.object(remote,'direct',return_value={'returncode':0}),patch.object(remote,'inspect',return_value={'exit':{'exit_code':0},'check':{'status':'APPLICABLE'}}),patch.object(runs,'_collect',side_effect=ValueError('invalid result fixture')):
            runs._execute(key)
        record=runs.get(key);self.assertEqual(record['state'],'FAILED');self.assertEqual(record['exit_code'],0);self.assertIn('invalid result',record['error'])
        with patch.object(remote,'direct') as direct:runs._execute(key);direct.assert_not_called()
    def test_remote_script_records_environment_applicability_failure_and_timeout(self):
        runtime=self.runtime()
        cases=[('environment',4),('not-applicable',3),('failure',7),('timeout',124)]
        for case,expected in cases:
            with self.subTest(case=case):
                dest=self.root/case;(dest/'package/scripts').mkdir(parents=True)
                cli="import json,sys,time\naction=sys.argv[1]\nif action=='doctor': print(json.dumps({'ready': "+str(case!='environment')+"}))\nelif action=='check': print(json.dumps({'status': '"+('NOT_APPLICABLE' if case=='not-applicable' else 'APPLICABLE')+"'}))\nelse:\n time.sleep("+('20' if case=='timeout' else '0')+")\n sys.exit(7)\n"
                (dest/'package/scripts/cli.py').write_text(cli)
                shutil.copy2(BASE/'backend/extension_process.py',dest/'extension-runner.py')
                (dest/'launch.sh').write_text(remote.script(runtime,str(dest),'scripts/cli.py','ssh',timeout=1))
                response=subprocess.run(['bash',str(dest/'launch.sh')],capture_output=True,text=True,timeout=8)
                self.assertEqual(response.returncode,expected,response.stderr)
                self.assertEqual(json.loads((dest/'exit.json').read_text())['exit_code'],expected)
                if case in ('environment','not-applicable'):self.assertFalse((dest/'stdout.log').exists())
                if case=='timeout':self.assertIn('process group stopped',(dest/'stderr.log').read_text())
    def test_scripts_pbs_slurm_doctor_failure_and_no_science_program(self):
        for scheduler in ('pbs','slurm'):
            script=remote.script(self.runtime(scheduler),'/workspace/analysis','scripts/cli.py','scheduler',2)
            self.assertNotIn('vasp',script);self.assertNotIn('mpirun',script);self.assertIn('doctor.json',script);self.assertIn('else 4',script);self.assertIn('exit.json.tmp',script);self.assertIn('#PBS' if scheduler=='pbs' else '#SBATCH',script)
    def test_remote_geometry_runs_with_same_cli_and_multiple_outputs(self):
        payload=self.remote_payload('ssh');receipt=runs.start(payload);key=receipt['analysis_id'];remote_dir=self.root/'remote'
        def stage(record,dest,request):
            shutil.copytree(dest,remote_dir);request=copy.deepcopy(request)
            request['output_dir']=str(remote_dir/'outputs');request['result_file']=str(remote_dir/'outputs/result.json')
            for values in request['inputs'].values():
                for item in values:item['path']=str(remote_dir/'inputs'/Path(item['path']).name)
            p.atomic_json(remote_dir/'request.json',request);return str(remote_dir)
        def direct(record):
            response=subprocess.run(['bash',str(remote_dir/'launch.sh')],capture_output=True,text=True);return {'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr}
        def ssh(alias,command,data=None,timeout=None,**kwargs):
            response=subprocess.run(['bash','-c',command],capture_output=True,text=True);return {'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr}
        def pull(record,name,target):target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(remote_dir/name,target);return target
        with patch.object(remote,'stage',side_effect=stage),patch.object(remote,'direct',side_effect=direct),patch.object(ex,'ssh',side_effect=ssh),patch.object(remote,'pull',side_effect=pull):
            # Simulate stage path resolution in the generated script.
            original_script=remote.script
            with patch.object(remote,'script',side_effect=lambda runtime,directory,entry,mode,cores,timeout:original_script(runtime,str(remote_dir),entry,mode,cores,timeout)):runs._execute(key)
        record=runs.get(key);self.assertEqual(record['state'],'COMPLETED',record.get('error'));self.assertEqual(len(record['outputs']),2);self.assertEqual(record['result']['sources'][0]['sha256'],record['sources'][0]['sha256'])
    def test_remote_large_artifact_retained_not_pulled(self):
        self.install();record=self.execute(self.payload());dest=runs.directory(record['id']);result=record['result'];result['artifacts']=[{'path':'huge.bin','label':'huge','media_type':'application/octet-stream'}];p.atomic_json(dest/'outputs/result.json',result)
        record.update(location='scheduler',runtime=self.runtime(),remote_dir='/workspace/analysis')
        with patch.object(remote,'output_info',return_value={'huge.bin':{'size_bytes':artifacts.MAX_PULL_BYTES+1}}),patch.object(remote,'pull') as pull:runs._collect(record);pull.assert_not_called()
        self.assertEqual(record['state'],'COMPLETED');self.assertFalse(record['outputs'][0]['available'])
        with self.assertRaisesRegex(ValueError,'远端'):runs.output(record['id'],'huge.bin')

    def test_real_local_timeout_kills_process_and_preserves_source(self):
        package=self.root/'slow';shutil.copytree(TEMPLATE,package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest['execution']['timeout_seconds']=1;p.atomic_json(package/'extension.manifest.json',manifest)
        cli=package/'scripts/cli.py';cli.write_text(cli.read_text().replace('def analyze(request):','def analyze(request):\n    import time; time.sleep(3)'))
        p.install_directory(package);record=self.execute(self.payload());self.assertEqual(record['state'],'FAILED');self.assertIn('超时',record['error']);self.assertEqual(record['sources'][0]['poscar'],SI)



    def test_local_source_change_and_host_mismatch_stop_before_dispatch(self):
        self.install();self.mock_attempts({'aa':{'POSCAR':SI.encode()}})
        payload={'extension_id':'structure-volume-statistics','project_id':'project','sources':[{'kind':'attempt','attempt_id':'aa'}],'options':{'include_elements':True}}
        with patch.object(remote,'source_pull',side_effect=ValueError('来源大小或修改时间变化')):record=self.execute(payload)
        self.assertEqual(record['state'],'FAILED');self.assertIn('修改时间',record['error'])
        self.runtime();value=remote.runtimes()['jutai-1'];value['alias']='jutai-5';value['config']['alias']='jutai-5';p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-5':value})
        with self.assertRaisesRegex(ValueError,'相同'):runs.preview({**payload,'location':'ssh','alias':'jutai-5'})

    def test_remote_staging_uses_metadata_copy_and_controlled_pull(self):
        self.install();self.mock_attempts({'aa':{'POSCAR':SI.encode()}});self.runtime()
        payload={'extension_id':'structure-volume-statistics','project_id':'project','sources':[{'kind':'attempt','attempt_id':'aa'}],'options':{'include_elements':True},'location':'ssh','alias':'jutai-1'}
        payload['preview_id']=runs.preview(payload)['preview_id'];receipt=runs.start(payload);record=runs.get(receipt['analysis_id']);dest=runs.directory(record['id']);request=runs._input_request(record,dest);(dest/'launch.sh').write_text('fixture')
        request['inputs']['duplicate-role']=copy.deepcopy(next(iter(request['inputs'].values())))
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'/workspace/project/analyses/'+record['id']+'\n'}) as ssh:remote.stage(record,dest,request)
        inputs=json.loads((dest/'request.remote.json').read_text())['inputs']
        paths={item['path'] for values in inputs.values() for item in values}
        self.assertEqual(len(paths),1)
        self.assertEqual(ssh.call_args.args[1].count('before=$(stat'),1)
        self.assertIn('cp --reflink=auto',ssh.call_args.args[1])
        text=ssh.call_args.args[1];self.assertIn('dest=/workspace/project/analyses/'+record['id'],text);self.assertIn('stat -c',text);self.assertIn('cp --',text);self.assertNotIn('sha256',text);self.assertNotIn('ln ',text)

    def test_partial_staging_records_frozen_location_before_first_write(self):
        payload=self.remote_payload('ssh')
        receipt=runs.start(payload);key=receipt['analysis_id']
        def fail(record,destination,request):
            persisted=runs.get(key)
            self.assertEqual(persisted['remote_dir_planned'],remote.analysis_directory(record))
            self.assertEqual(persisted['remote_stage'],'creating')
            raise ValueError('fixture second copy failed')
        with patch.object(remote,'stage',side_effect=fail),patch.object(remote,'direct') as start:
            runs._execute(key)
        record=runs.get(key)
        self.assertEqual((record['state'],record['remote_stage']),('FAILED','partial'))
        self.assertFalse(record.get('submission_started'))
        self.assertEqual(record['remote_dir_planned'],remote.analysis_directory(record))
        start.assert_not_called()

    def test_active_unknown_analysis_counts_core_budget(self):
        payload=self.remote_payload();receipt=runs.start(payload);record=runs.get(receipt['analysis_id']);record.update(location='scheduler',state='UNKNOWN',requested_cores=2);runs.save(record)
        control.save_settings({'max_inflight_total':5,'max_inflight_per_host':5,'max_cores_per_host':3})
        with closing(ex.db()) as db:
            with self.assertRaisesRegex(ValueError,'核数预算'):control.enforce_limits(db,{'id':'other','config':{'alias':'jutai-1'},'profile':{'ppn':2}})

    def test_changed_installed_package_can_still_be_recoverably_uninstalled(self):
        self.install();receipt=p.installed('structure-volume-statistics');(receipt['root']/'scripts/cli.py').write_text('broken package')
        with self.assertRaisesRegex(ValueError,'变化'):p.installed('structure-volume-statistics')
        self.assertTrue(p.uninstall('structure-volume-statistics')['history_preserved']);self.assertTrue(list((self.root/'extensions-v2/recycle').glob('*/scripts/cli.py')))

    def test_evidence_requires_boolean_true_and_source_path_uses_existing_contract(self):
        self.install('sample-attempt');self.mock_attempts({'aa':{'POSCAR':SI.encode(),'OUTCAR':b'x'}},evidence={'vasp_timing':True,'ediff_reached':'False'})
        source=runs.describe_sources([{'kind':'attempt','attempt_id':'aa'}],'project')[0]
        self.assertEqual(source['remote_root'],'/workspace/source');self.assertTrue(any('ediff_reached' in reason for reason in runs.blockers(p.installed('sample-attempt')['manifest'],[source])))

    def test_remote_missing_dependency_is_failed_not_inapplicable(self):
        payload=self.remote_payload();receipt=runs.start(payload);record=runs.get(receipt['analysis_id']);record.update(state='SUBMITTED',remote_dir='/workspace/analysis',job_id='731.server');runs.save(record)
        with patch.object(remote,'inspect',return_value={'job_id':'731.server','exit':{'exit_code':4},'doctor':{'ready':False,'missing':['numpy']}}):result=runs.refresh(record['id'])
        self.assertEqual(result['state'],'FAILED');self.assertIn('numpy',result['error'])

    def test_optional_inputs_are_associated_by_source_and_required_defaults_stay_compatible(self):
        manifest=copy.deepcopy(p.inspect_package(TEMPLATE)['manifest']);manifest['sources']['max']=2
        manifest['inputs'].append({'role':'extra-file','title':'可选文件','names':['EXTRA'],'max_bytes':100,'required':False})
        sources=[{'id':'aa','kind':'structure','stage':'structure','name':'a','state':'DRAFT','evidence':{},'chosen_files':{},'poscar':SI,'files':[{'name':'POSCAR','size_bytes':len(SI.encode())}]},{'id':'bb','kind':'structure','stage':'structure','name':'b','state':'DRAFT','evidence':{},'chosen_files':{},'poscar':SI,'files':[{'name':'POSCAR','size_bytes':len(SI.encode())},{'name':'EXTRA','size_bytes':len(SI.encode())}]}]
        manifest['inputs'][1]['max_bytes']=10000
        self.assertEqual(runs.blockers(manifest,sources),[])
        record={'id':'a'*32,'manifest':manifest,'sources':sources,'location':'local','options':{}}
        req=runs._input_request(record,self.root/'request');self.assertEqual([i['source_id'] for i in req['inputs']['extra-file']],['bb'])
        sources[1]['chosen_files']={'extra-file':''};req=runs._input_request(record,self.root/'request2');self.assertNotIn('extra-file',req['inputs'])
        sources[0]['chosen_files']={'structure':'bogus'};self.assertTrue(any('选择无效' in r for r in runs.blockers(manifest,sources)))
        sources[0]['chosen_files']={};sources[0]['files']=[];self.assertTrue(any('缺少有效' in r for r in runs.blockers(manifest,sources)))
        package=self.root/'optional';shutil.copytree(TEMPLATE,package);p.atomic_json(package/'extension.manifest.json',manifest);self.assertIn('extra-file',[r['role'] for r in p.inspect_package(package)['manifest']['inputs']])
        manifest['inputs'][1]['required']='false';p.atomic_json(package/'extension.manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'required'):p.inspect_package(package)

    def test_preferred_location_is_optional_and_validated(self):
        package=self.root/'preferred';shutil.copytree(TEMPLATE,package);manifest=p.inspect_package(package)['manifest'];self.assertNotIn('preferred_location',manifest['execution'])
        manifest['execution']['preferred_location']='ssh';p.atomic_json(package/'extension.manifest.json',manifest);self.assertEqual(p.inspect_package(package)['manifest']['execution']['preferred_location'],'ssh')
        manifest['execution']['preferred_location']='unknown';p.atomic_json(package/'extension.manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'优先'):p.inspect_package(package)

    def test_direct_package_freezes_options_runs_once_without_scheduler_and_keeps_downloads_after_uninstall(self):
        installed=self.install('sample-analysis');self.assertTrue(installed['enabled']);self.assertEqual(installed['execution']['preferred_location'],'ssh')
        req={'sources':[{'id':'sample-source','input_summary':{'energy_reference':{'fermi_eV':5,'host':'jutai-1'}}}],'inputs':{'structure':[{'name':'POSCAR','path':str(TEMPLATE/'fixtures/POSCAR')}]},'options':{'nz':100}};source=req['sources'][0];key=source['id'];source['alias']='jutai-1';source['input_summary']['energy_reference']['host']='jutai-1'
        files={key:{i['name']:Path(i['path']).read_bytes() for values in req['inputs'].values() for i in values}}
        self.mock_attempts(files,stage='wannier90',evidence={'wannier_all_done':True})
        frozen={'id':key,'kind':'wannier90','project_id':'project','node_id':'w90-node','state':'COMPLETED','config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path','evidence':{'wannier_all_done':True},'seed':'wannier90','parent_attempt_id':'parent','source_snapshot':{'seed':'wannier90','spinors':True,'parameters':{'num_wann':8}},'live_energy_reference':source['input_summary']['energy_reference']}
        frozen['remote_project_path_absolute']='/workspace/project'
        self.stack.enter_context(patch.object(ex,'get',return_value=frozen));self.runtime();runtime=remote.runtimes()['jutai-1'];runtime.update(node='',cores=None,reason='');p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        payload={'extension_id':'sample-analysis','project_id':'project','sources':[{'kind':'attempt','attempt_id':key}],'options':req['options'],'location':'ssh','alias':'jutai-1'}
        with patch.object(remote,'resource_preview',side_effect=AssertionError('no node probe in direct SSH')):
            preview=runs.preview(payload);payload['preview_id']=preview['preview_id'];receipt=runs.start(payload)
        analysis=receipt['analysis_id'];payload['options']['nz']=999
        remote_dir=self.root/'simulated-server'
        def stage(record,dest,request):
            shutil.copytree(dest,remote_dir);request=copy.deepcopy(request);request['output_dir']=str(remote_dir/'outputs');request['result_file']=str(remote_dir/'outputs/result.json')
            for values in request['inputs'].values():
                for item in values:
                    target=remote_dir/'inputs'/Path(item['path']).name;target.parent.mkdir(exist_ok=True);target.write_bytes(files[item['source_id']][item['name']]);item['path']=str(target)
            p.atomic_json(remote_dir/'request.json',request);return str(remote_dir)
        def direct(record):
            response=subprocess.run(['bash',str(remote_dir/'launch.sh')],capture_output=True,text=True,timeout=35);return {'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr}
        def ssh(alias,command,data=None,timeout=None,**kwargs):
            self.assertNotIn('qsub',command);self.assertNotIn('sbatch',command);response=subprocess.run(['bash','-c',command],capture_output=True,text=True);return {'exit_code':response.returncode,'stdout':response.stdout,'stderr':response.stderr}
        def pull(record,name,target):target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(remote_dir/name,target);return target
        original=remote.script
        with patch.object(remote,'stage',side_effect=stage),patch.object(remote,'direct',side_effect=direct) as dispatch,patch.object(ex,'ssh',side_effect=ssh),patch.object(remote,'pull',side_effect=pull),patch.object(remote,'submit',side_effect=AssertionError('no scheduler submission')),patch.object(remote,'resource_preview',side_effect=AssertionError('no capacity probe')),patch.object(remote,'script',side_effect=lambda runtime,directory,entry,mode,cores,timeout:original(runtime,str(remote_dir),entry,mode,cores,timeout)):
            runs._execute(analysis);runs._execute(analysis);self.assertEqual(dispatch.call_count,1)
        record=runs.get(analysis);self.assertEqual(record['state'],'COMPLETED',record.get('error'));self.assertEqual(record['options']['nz'],100);self.assertNotIn('requested_cores',record);self.assertNotIn('job_id',record)
        self.assertEqual(record['sources'][0]['input_summary']['parent_attempt_id'],'parent');self.assertEqual(record['result']['tables'][0]['rows'][0]['atoms'],2)
        p.enable('sample-analysis',False);self.assertFalse(runs.context(payload)['extensions']);p.uninstall('sample-analysis');self.assertTrue(runs.output(analysis,'elements.csv')[0].is_file());self.assertEqual(runs.history('project')[0]['id'],analysis)

    def test_direct_unknown_is_only_reconciled_and_never_reexecuted(self):
        payload=self.remote_payload('ssh');receipt=runs.start(payload);key=receipt['analysis_id']
        with patch.object(remote,'stage',return_value='/workspace/analyses/'+key),patch.object(remote,'direct',side_effect=ValueError('SSH disconnected')) as dispatch:
            runs._execute(key);runs._execute(key);self.assertEqual(dispatch.call_count,1)
        self.assertEqual(runs.get(key)['state'],'UNKNOWN')
        with patch.object(remote,'inspect',return_value={'exit':None,'job_id':None}),patch.object(remote,'direct') as dispatch:runs.refresh(key);dispatch.assert_not_called()
        self.assertEqual(runs.get(key)['state'],'UNKNOWN')

    def test_wannier_summary_uses_frozen_bundle_num_wann_parent_and_energy(self):
        key='b'*64;energy={'fermi_eV':3.25,'attempt_id':'parent','host':'site'}
        p.atomic_json(self.root/'wannier-bundles'/(key+'.json'),{'win':'num_wann = 24\nspinors = true\nbegin atoms_cart\nCr 0 0 0\nFe 1 1 1\nO 2 2 2\nCr 3 3 3\nend atoms_cart','seed':'wannier90','parent_attempt_id':'parent','spinors':True,'energy_reference':energy})
        record={'kind':'wannier90','stage':'wannier90','bundle_id':key,'live_energy_reference':{'fermi_eV':999},'source_snapshot':{'parameters':{'num_iter':'100'}}}
        stage,summary=runs._stage(record);self.assertEqual(stage,'wannier90');self.assertEqual(summary['num_wann'],24);self.assertEqual(summary['parent_attempt_id'],'parent');self.assertEqual(summary['energy_reference'],energy);self.assertEqual(summary['elements'],['Cr','Fe','O']);self.assertEqual(summary['parent_name'],'DFT 来源已记录')
        summary['energy_reference']['fermi_eV']=8;self.assertEqual(json.loads((self.root/'wannier-bundles'/(key+'.json')).read_text())['energy_reference']['fermi_eV'],3.25)

if __name__=='__main__':unittest.main()
