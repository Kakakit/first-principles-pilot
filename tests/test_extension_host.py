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
from backend import extension_protocol as p, extension_runs as runs, extension_remote as remote, execution as ex, artifacts, execution_control as control

BASE=Path(__file__).resolve().parents[1]
TEMPLATE=BASE/'extension-templates/structure-statistics'
SI=(BASE/'tests/fixtures/Si.POSCAR').read_text()

class ExtensionHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (p,runs,remote,ex,artifacts):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(runs,'schedule'))
        self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('real SSH forbidden in tests')))
        p._cache.clear();p._doctor_cache.clear()
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)
    def install(self,key='structure-volume-statistics'):return p.install_builtin(key)
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
        self.install('lcalcpol-polarization-analysis')
        self.assertNotIn('lcalcpol-polarization-analysis',[e['id'] for e in runs.context(self.payload())['extensions']])
        with self.assertRaisesRegex(ValueError,'来源类型'):runs.start(self.payload('lcalcpol-polarization-analysis',options={'lattice_tolerance_angstrom':1e-8}))
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
        self.stack.enter_context(patch.object(ex,'get',side_effect=lambda key:{'id':key,'project_id':'project','node_id':'node-'+key,'stage':stage,'state':state,'config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path','evidence':evidence}))
        self.stack.enter_context(patch.object(ex,'attempt_path',return_value={'root':'/workspace','relative':'source','shell':'/workspace/source'}))
        self.stack.enter_context(patch.object(artifacts,'listing',side_effect=lambda key:{'files':[{'name':name,'path':name,'size_bytes':len(data),'modified_at':'2026-10-01T00:00:00+00:00'} for name,data in files[key].items()]}))
        def pull(source,name,target):raw=files[source['attempt_id']][name];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);return raw
        self.stack.enter_context(patch.object(remote,'source_pull',side_effect=pull))
    def test_polarization_two_sources_preserve_algorithm_and_units(self):
        self.install('lcalcpol-polarization-analysis');fixture=BASE/'resources/extensions/lcalcpol-polarization-analysis/tests/fixtures'
        first={name:(fixture/name).read_bytes() for name in ('POSCAR','OUTCAR')};files={'aa':first,'bb':dict(first)};self.mock_attempts(files)
        record=self.execute({'extension_id':'lcalcpol-polarization-analysis','project_id':'project','sources':[{'kind':'attempt','attempt_id':'aa'},{'kind':'attempt','attempt_id':'bb'}],'options':{'lattice_tolerance_angstrom':1e-8}})
        self.assertEqual(record['state'],'COMPLETED',record.get('error'));self.assertEqual(record['scientific_validation'],'INSUFFICIENT');self.assertEqual([row['raw'] for row in record['result']['tables'][0]['rows']],[0,0,0]);self.assertEqual(record['result']['tables'][0]['columns'][1]['unit'],'C/m²');self.assertEqual(len(record['result']['sources']),2)
    def test_nonconverged_and_large_files_report_distinct_reasons(self):
        self.install('lcalcpol-polarization-analysis');self.mock_attempts({'aa':{'POSCAR':SI.encode(),'OUTCAR':b'x'}},evidence={'vasp_timing':True})
        sources=runs.describe_sources([{'kind':'attempt','attempt_id':'aa'}],'project');m=p.installed('lcalcpol-polarization-analysis')['manifest'];self.assertIn('已完成运行：缺少 ediff_reached 证据',runs.blockers(m,sources))
        sources[0]['evidence']['ediff_reached']=True;sources[0]['files'][1]['size_bytes']=artifacts.MAX_PULL_BYTES+1
        self.assertTrue(any('10 MiB' in s for s in runs.blockers(m,sources,'local')));self.assertFalse(runs.blockers(m,sources,'scheduler'))
    def runtime(self,scheduler='pbs'):
        value={'alias':'jutai-1','python':sys.executable,'node':'node01','cores':2,'reason':'fixture','config':{'alias':'jutai-1','scheduler':scheduler,'queue':'batch','walltime':'00:10:00','remote_workspace_root':str(self.root/'230269265lht')}}
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':value});return value
    def remote_payload(self,mode='scheduler'):
        self.runtime();self.install();payload=self.payload(location=mode,alias='jutai-1');self.stack.enter_context(patch.object(remote,'resource_preview',return_value={'ready':True,'selection':{'requested_cores':2},'probe':'fixture'}));payload['preview_id']=runs.preview(payload)['preview_id'];return payload
    def test_remote_preview_binding_rejects_later_changes(self):
        payload=self.remote_payload();payload['options']={'include_elements':False}
        with self.assertRaisesRegex(ValueError,'重新预览'):runs.start(payload)
    def test_ssh_preview_accepts_home_workspace_without_resource_probe(self):
        self.install();runtime=self.runtime();runtime['config']['remote_workspace_root']='$HOME/230269265lht'
        p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        with patch.object(remote,'resource_preview') as probe:
            receipt=runs.preview(self.payload(location='ssh',alias='jutai-1'))
        self.assertTrue(receipt['ready']);self.assertIn('analyses/analysis-preview',receipt['script'])
        self.assertIn('doctor.json',receipt['script']);self.assertNotIn('#PBS',receipt['script']);probe.assert_not_called()
    def test_scheduler_dispatch_exact_id_once_and_unknown_recovery(self):
        payload=self.remote_payload();receipt=runs.start(payload);key=receipt['analysis_id']
        with patch.object(remote,'stage',return_value='/workspace/analyses/'+key),patch.object(remote,'submit',side_effect=ValueError('no receipt')) as submit:
            runs._execute(key);runs._execute(key);self.assertEqual(submit.call_count,1)
        self.assertEqual(runs.get(key)['state'],'UNKNOWN')
        with patch.object(remote,'inspect',return_value={'job_id':'731.server','exit':None}),patch.object(remote,'queued',return_value=True),patch.object(remote,'submit') as submit:
            self.assertEqual(runs.refresh(key)['job_id'],'731.server');self.assertEqual(runs.get(key)['state'],'SUBMITTED');submit.assert_not_called()
        with closing(ex.db()) as db:
            with self.assertRaisesRegex(ValueError,'在途'):control.enforce_limits(db,{'id':'other','config':{'alias':'jutai-1'},'profile':{'ppn':2}})
    def test_resource_wait_never_stages_or_submits(self):
        payload=self.remote_payload();receipt=runs.start(payload)
        with patch.object(remote,'resource_preview',return_value={'ready':False,'reason':'busy'}),patch.object(remote,'stage') as stage,patch.object(remote,'submit') as submit:runs._execute(receipt['analysis_id']);stage.assert_not_called();submit.assert_not_called()
        self.assertEqual(runs.get(receipt['analysis_id'])['state'],'WAIT_RESOURCE');self.assertEqual(runs.active_usage(),[])
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

    def test_symmetry_migration_keeps_fixed_tolerances_and_wyckoff_file(self):
        self.install('structure-symmetry');record=self.execute(self.payload('structure-symmetry',options={'angle_tolerance_degree':5}))
        self.assertEqual(record['state'],'COMPLETED',record.get('error'));path,_=runs.output(record['id'],'symmetry.json');data=json.loads(path.read_text());self.assertEqual([r['symprec_angstrom'] for r in data['results']],[0.001,0.01,0.1]);self.assertEqual(data['results'][0]['space_group_number'],227)

    def test_distortion_migration_keeps_metrics_units_and_three_outputs(self):
        self.install('crystal-structure-distortion-analysis');fixture=BASE/'resources/extensions/crystal-structure-distortion-analysis/fixtures/ideal_fecr_polyhedra/POSCAR'
        record=self.execute({'extension_id':'crystal-structure-distortion-analysis','sources':[{'kind':'structure','poscar':fixture.read_text()}],'options':{'center_element':'Cr','ligand_element':'O','coordination':6,'search_radius_angstrom':3}})
        self.assertEqual(record['state'],'COMPLETED',record.get('error'));self.assertEqual(len(record['outputs']),3);rows=record['result']['tables'][0]['rows'];self.assertEqual(next(r['unit'] for r in rows if r['metric']=='mean_bond_length_angstrom'),'Å');self.assertAlmostEqual(next(r['mean'] for r in rows if r['metric']=='baur_distortion_index'),0)

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
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'/workspace/analyses/'+record['id']+'\n'}) as ssh:remote.stage(record,dest,request)
        text=ssh.call_args.args[1];self.assertIn('stat -c',text);self.assertIn('cp --',text);self.assertNotIn('sha256',text);self.assertNotIn('ln ',text)

    def test_active_unknown_analysis_counts_core_budget(self):
        payload=self.remote_payload();receipt=runs.start(payload);record=runs.get(receipt['analysis_id']);record.update(state='UNKNOWN',requested_cores=2);runs.save(record)
        control.save_settings({'max_inflight_total':5,'max_inflight_per_host':5,'max_cores_per_host':3})
        with closing(ex.db()) as db:
            with self.assertRaisesRegex(ValueError,'核数预算'):control.enforce_limits(db,{'id':'other','config':{'alias':'jutai-1'},'profile':{'ppn':2}})

    def test_changed_installed_package_can_still_be_recoverably_uninstalled(self):
        self.install();receipt=p.installed('structure-volume-statistics');(receipt['root']/'scripts/cli.py').write_text('broken package')
        with self.assertRaisesRegex(ValueError,'变化'):p.installed('structure-volume-statistics')
        self.assertTrue(p.uninstall('structure-volume-statistics')['history_preserved']);self.assertTrue(list((self.root/'extensions-v2/recycle').glob('*/scripts/cli.py')))

    def test_evidence_requires_boolean_true_and_source_path_uses_existing_contract(self):
        self.install('lcalcpol-polarization-analysis');self.mock_attempts({'aa':{'POSCAR':SI.encode(),'OUTCAR':b'x'}},evidence={'vasp_timing':True,'ediff_reached':'False'})
        source=runs.describe_sources([{'kind':'attempt','attempt_id':'aa'}],'project')[0]
        self.assertEqual(source['remote_root'],'/workspace/source');self.assertTrue(any('ediff_reached' in reason for reason in runs.blockers(p.installed('lcalcpol-polarization-analysis')['manifest'],[source])))

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

    def test_tb2j_ssh_freezes_options_runs_once_without_scheduler_and_keeps_downloads_after_uninstall(self):
        import os
        from tests.test_tb2j_extension import module as tb
        self.stack.enter_context(patch.dict(os.environ,{'PYTHONPATH':str(tb.FAKE)}))
        installed=self.install('tb2j');self.assertTrue(installed['enabled']);self.assertEqual(installed['execution']['preferred_location'],'ssh')
        req=tb.request(self.root/'source');source=req['sources'][0];key=source['id'];source['alias']='jutai-1';source['input_summary']['energy_reference']['host']='jutai-1'
        files={key:{i['name']:Path(i['path']).read_bytes() for values in req['inputs'].values() for i in values}}
        self.mock_attempts(files,stage='wannier90',evidence={'wannier_all_done':True})
        frozen={'id':key,'kind':'wannier90','project_id':'project','node_id':'w90-node','state':'COMPLETED','config':{'alias':'jutai-1'},'path_schema':'fpilot.remote-attempt-path','evidence':{'wannier_all_done':True},'seed':'wannier90','parent_attempt_id':'parent','source_snapshot':{'seed':'wannier90','spinors':True,'parameters':{'num_wann':8}},'live_energy_reference':source['input_summary']['energy_reference']}
        self.stack.enter_context(patch.object(ex,'get',return_value=frozen));self.runtime();runtime=remote.runtimes()['jutai-1'];runtime.update(node='',cores=None,reason='');p.atomic_json(self.root/'extensions-v2/runtimes.json',{'jutai-1':runtime})
        payload={'extension_id':'tb2j','project_id':'project','sources':[{'kind':'attempt','attempt_id':key}],'options':req['options'],'location':'ssh','alias':'jutai-1'}
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
        self.assertEqual(record['sources'][0]['input_summary']['parent_attempt_id'],'parent');self.assertEqual(record['result']['tables'][0]['rows'][0]['atom_j'],3)
        p.enable('tb2j',False);self.assertFalse(runs.context(payload)['extensions']);p.uninstall('tb2j');self.assertTrue(runs.output(analysis,'exchange.csv')[0].is_file());self.assertEqual(runs.history('project')[0]['id'],analysis)

    def test_tb2j_direct_unknown_is_only_reconciled_and_never_reexecuted(self):
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
