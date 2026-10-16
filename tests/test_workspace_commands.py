"""Portable onboarding and opaque-command contracts; no school jobs are submitted."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from backend import command_process, custom_commands, execution, job_templates, remote_paths, servers, site_registration, workbench_settings


class WorkspaceCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        for module in (execution, job_templates, site_registration, workbench_settings):
            item=patch.object(module,'ROOT',self.root);item.start();self.addCleanup(item.stop)
        self.host={'status':'USER_CONFIGURED','scheduler':'custom','runtime_source':'script','runtimes':[],
                   'python':{'known_interpreter':sys.executable},'remote_workspace_root':'/scratch/my-projects'}
        item=patch.object(servers,'load_registry',return_value={'hosts':{'my-lab':self.host}})
        item.start();self.addCleanup(item.stop)

    def preset(self, text='printf hello\n', scheduler='custom'):
        return job_templates.save_template(text,'我的命令',scheduler,'my-lab','other','用户原文')

    def test_arbitrary_absolute_paths_and_literal_shell_characters(self):
        for path in ['/scratch/我的 目录','/project/groupA','/mnt/other/$data; echo x','/tmp/a/../b','/']:
            self.assertTrue(remote_paths.normalize_workspace_root(path).startswith('/'))
        self.assertEqual(remote_paths.normalize_workspace_root('$HOME/research'),'$HOME/research')
        for path in ['relative','/a\nb','/a\x00b']:
            with self.assertRaises(ValueError):remote_paths.validate_root(path)
        self.assertEqual(remote_paths.new_directory('/scratch/my-project'),'/scratch/my-project')
        with self.assertRaisesRegex(ValueError,'中文'):remote_paths.new_directory('/scratch/新目录')
        self.assertEqual(remote_paths.new_directory('/scratch/旧目录','/scratch/旧目录'),'/scratch/旧目录')

    def test_legacy_and_new_project_layouts_and_per_host_override(self):
        p={'remoteSlug':'test'}
        self.assertEqual(remote_paths.project_location(p,'my-lab','/scratch'),('/scratch','material-projects/test'))
        p['remotePathPolicy']='project-directory-v2'
        self.assertEqual(remote_paths.project_location(p,'my-lab','/scratch'),('/scratch','test'))
        p['remoteWorkspaceRoot']='/shared/workbench'
        self.assertEqual(remote_paths.project_location(p,'my-lab','/scratch'),('/shared/workbench','test'))
        self.assertEqual(remote_paths.project_location(p,'other','/different'),('/shared/workbench','test'))
        p.pop('remoteWorkspaceRoot')
        p['remoteDirectories']={'my-lab':'/数据/独立 项目'}
        self.assertEqual(remote_paths.project_location(p,'my-lab','/scratch'),('/数据/独立 项目',''))
        self.assertEqual(remote_paths.project_location(p,'other','/different'),('/different','test'))
        self.assertEqual(remote_paths.attempt_relative_path('test',3,'scf','run',project_relative=''),'flows/main/stages/03-scf/attempts/run')

    def test_common_workspace_default_is_saved_without_changing_server_or_task_records(self):
        saved=workbench_settings.save({'workspace_root':'/scratch/all-clusters'})
        self.assertEqual(saved['workspace_root'],'/scratch/all-clusters')
        self.assertEqual(workbench_settings.read()['workspace_root'],'/scratch/all-clusters')
        self.assertEqual(self.host['remote_workspace_root'],'/scratch/my-projects')
        with self.assertRaisesRegex(ValueError,'中文'):workbench_settings.save({'workspace_root':'/scratch/中文'})
        self.assertEqual(workbench_settings.read()['workspace_root'],'/scratch/all-clusters')

    @unittest.skipIf(os.name=='nt','POSIX remote shell runs on the server')
    def test_probe_accepts_unicode_spaces_and_does_not_create_directories(self):
        root=self.root/'中文 空格 $(touch unwanted)'/'not-created'
        result=subprocess.run(['bash','-c',remote_paths.workspace_probe_command(str(root))],capture_output=True,text=True)
        value=remote_paths.parse_workspace_probe({'exit_code':result.returncode,'stdout':result.stdout},str(root))
        self.assertEqual(value['absolute_path'],str(root.resolve()));self.assertFalse(root.exists())
        self.assertFalse((self.root/'unwanted').exists())

    def test_two_imports_reopen_edit_default_and_reload(self):
        first=self.preset('echo one\n');second=self.preset('echo two\n')
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(job_templates.get_template(first['id'])['original_text'],'echo one\n')
        copied=job_templates.save_template('echo edited\n','编辑副本','custom','my-lab','other','原文',parent_id=first['id'])
        with patch.object(site_registration,'ssh_aliases',return_value=['my-lab']):
            config=servers.select_profile('my-lab')
            value=workbench_settings.save({'runtime':config,'program':'vasp_std','template_id':copied['id']})
        self.assertEqual(value['defaults']['my-lab:vasp_std'],copied['id'])
        self.assertEqual(workbench_settings.read()['defaults'],value['defaults'])
        self.assertEqual(job_templates.get_template(first['id'])['original_text'],'echo one\n')
        self.assertFalse(list((self.root/'job-templates').rglob('tmp*')))

    def test_replace_happens_after_handle_close_and_preserves_old_file_on_failure(self):
        target=self.root/'old';target.write_bytes(b'old')
        original=os.replace
        def replacement(source,destination):
            # Reopening and renaming this file exercises real Windows sharing semantics.
            with Path(source).open('rb') as handle:self.assertEqual(handle.read(),b'new')
            return original(source,destination)
        with patch.object(job_templates.os,'replace',side_effect=replacement):job_templates.atomic(target,b'new')
        self.assertEqual(target.read_bytes(),b'new')
        with patch.object(job_templates.os,'replace',side_effect=PermissionError('in use')):
            with self.assertRaises(PermissionError):job_templates.atomic(target,b'newer')
        self.assertEqual(target.read_bytes(),b'new');self.assertEqual(list(self.root.iterdir()),[target])

    def test_import_has_no_header_program_mpi_or_trap_gate(self):
        body="cd /my/folder\ntrap 'echo done' EXIT\nmpirun -np 7 arbitrary_GPU_program\n"
        preset=self.preset(body,'pbs')
        result=job_templates.render_template(preset['id'],{},'my-lab',{'scheduler':'pbs','runtime_source':'script','program':'wannier90'})
        self.assertTrue(result['submission_supported'],result['execution_blockers'])
        self.assertEqual(result['rendered_text'],body)

    def test_custom_preview_does_not_probe_or_rewrite_command(self):
        body="export X='a b'\nprintf '%s\\n' \"$X\" | cat\n"
        preset=self.preset(body)
        with patch.object(site_registration,'ssh_aliases',return_value=['my-lab']),patch.object(servers,'_probe_parts') as probe:
            result=servers.preview({**servers.select_profile('my-lab'),'template_id':preset['id']},'','')
        probe.assert_not_called();self.assertTrue(result['resource_ready']);self.assertEqual(result['script'],body)

    def test_template_read_does_not_rehash_body_as_a_security_gate(self):
        preset=self.preset()
        (self.root/'job-templates/scripts'/preset['script_hash']).write_text('echo changed by user\n')
        self.assertEqual(job_templates.get_template(preset['id'])['original_text'],'echo changed by user\n')

    def test_connection_test_preserves_user_multiplexing_configuration(self):
        resolved={'resolved':{'hostname':'example','user':'user','port':'22'}}
        response=subprocess.CompletedProcess([],0,'FPILOT_SSH_OK\nFPILOT_PYTHON={"path":"/usr/bin/python3","version":[3,11,1],"supported":true}\n','')
        with patch.object(site_registration,'check_ssh',return_value=resolved),patch('backend.platform_runtime.ssh_options',return_value=['-o','ControlPath=/user/socket']),patch.object(site_registration.subprocess,'run',return_value=response) as run:
            self.assertTrue(site_registration.test_connection('my-lab')['connected'])
        args=run.call_args.args[0];self.assertIn('ControlPath=/user/socket',args)
        self.assertNotIn('ControlMaster=no',args);self.assertNotIn('ControlPath=none',args)

    def test_start_receipt_loss_is_not_reexecuted_by_refresh(self):
        key='a'*32
        record={'id':key,'state':'PREPARED','config':{'alias':'my-lab','scheduler':'custom'},'script':'echo test','project_id':'p','remote_path_absolute':'/scratch/task'}
        with execution.db() as db: db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,record['state'],json.dumps(record)))
        custom_commands.dispatch_result(record,{'exit_code':None,'stdout':'','stderr':'connection lost'})
        with patch.object(execution,'assert_recovery_target'),patch.object(execution,'ssh',return_value={'exit_code':1,'stderr':'offline'}) as ssh,patch('backend.remote_transport._interpreter',return_value=sys.executable):
            custom_commands.read(key)
        self.assertEqual(execution.get(key)['state'],'UNKNOWN')
        self.assertEqual(ssh.call_count,1);self.assertIn('inspect',ssh.call_args.args[1]);self.assertNotIn("'start'",ssh.call_args.args[1])

    def test_authentication_session_preferences_do_not_invalidate_old_attempt_targets(self):
        from backend.project_sync import continuation_connection
        saved={'hostname':'same','user':'user','port':'22','identityfile':'key'}
        self.assertTrue(continuation_connection(saved,{**saved,'controlpath':'/new/socket','controlmaster':'auto'},'my-lab'))
        self.assertFalse(continuation_connection(saved,{**saved,'hostname':'other'},'my-lab'))

    def test_pyw90_runtime_is_configured_per_server_without_alias_or_directory_whitelists(self):
        from backend import pyw90_remote
        self.assertFalse(pyw90_remote.available('my-lab'))
        self.host['pyw90']={'python':'/opt/自有 环境/bin/python','package':'/scratch/工具/pyw90'}
        with patch.object(execution,'ssh',return_value={'exit_code':0,'stdout':'raw output','stderr':''}) as ssh:
            result=pyw90_remote._run('my-lab','/scratch/source',['pre','dos'],('vasprun.xml',))
        self.assertEqual(result['stdout'],'raw output');self.assertIn("'/opt/自有 环境/bin/python'",ssh.call_args.args[1])
        self.assertIn('/scratch/工具/pyw90',ssh.call_args.args[1]);self.assertNotIn('230269265lht',ssh.call_args.args[1])

    @unittest.skipIf(os.name=='nt','Remote staging uses POSIX shell')
    def test_custom_dispatch_stages_files_and_runs_once_without_queue_commands(self):
        key='c'*32
        destination=self.root/'remote'/'project'/'flows/main/stages/03-scf/attempts/run'
        config={**servers.select_profile('my-lab'),'remote_workspace_root':str(self.root/'remote'),'program':'vasp_std'}
        preset=self.preset("printf 'input='; head -n 1 POSCAR\nprintf 'err\\n' >&2\nexit 3\n")
        config['template_id']=preset['id']
        profile=custom_commands.preview(config)['profile']
        record={'id':key,'state':'PREPARED','config':config,'node':'command','cores':None,'reason':'',
                'profile':profile,'bundle_id':'bundle','project_id':'p','project_slug':'project','flow_id':'main','node_id':'n',
                'remote_workspace_root':str(self.root/'remote'),'remote_workspace_root_abs':str(self.root/'remote'),
                'remote_relative_path':'project/flows/main/stages/03-scf/attempts/run','remote_path_absolute':str(destination),
                'remote_project_path_absolute':str(self.root/'remote/project'),'path_schema':'fpilot.remote-attempt-path',
                'script':preset['original_text'],'script_sha256':'unused','input_audit':{'status':'PASS'},'source_file_plan':{},'probe_id':'custom'}
        with execution.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,'PREPARED',json.dumps(record)))
        calls=[]
        def local_ssh(alias,command,data=None,**kwargs):
            calls.append(command)
            value=subprocess.run(['bash','-c',command],input=data,capture_output=True)
            return {'exit_code':value.returncode,'stdout':value.stdout.decode(),'stderr':value.stderr.decode()}
        files={'INCAR':b'NSW=0\n','POSCAR':b'isolated input\n','KPOINTS':b'test','POTCAR':b'non-scientific fixture'}
        with patch.object(site_registration,'ssh_aliases',return_value=['my-lab']),patch('backend.remote_transport._interpreter',return_value=sys.executable),patch.object(execution,'ssh',side_effect=local_ssh),patch.object(execution,'bundle',return_value=({'stage':'scf'},files)):
            launched=execution.submit(key);again=execution.submit(key)
            self.assertEqual(launched['state'],'COMMAND_RUNNING',launched.get('dispatch_receipt'));self.assertEqual(again['state'],'COMMAND_RUNNING')
            self.assertEqual(len(calls),1);self.assertNotIn('qsub ',calls[0]);self.assertNotIn('sbatch ',calls[0])
            for _ in range(80):
                value=custom_commands.read(key)
                if value['state']=='EXITED':break
                time.sleep(.05)
        self.assertEqual(value['exit_code'],3);self.assertEqual(value['stdout'],'input=isolated input\n')
        self.assertEqual(execution.get(key)['state'],'COMMAND_EXITED');self.assertIsNone(execution.get(key)['job_id'])
        self.assertEqual((destination/'POSCAR').read_bytes(),files['POSCAR']);self.assertFalse((destination/'.job-id').exists())

    @unittest.skipIf(os.name=='nt','POSIX server process')
    def test_detached_long_command_streams_exit_code_and_at_most_once(self):
        key='b'*32
        body="printf 'first\\n'; sleep .3; printf 'last\\n'; printf 'error\\n' >&2; exit 7\n"
        encoded=base64.b64encode(body.encode()).decode()
        result=subprocess.run([sys.executable,command_process.__file__,'start',str(self.root),key,encoded],capture_output=True,text=True,check=True)
        self.assertTrue(json.loads(result.stdout)['reserved'])
        for _ in range(80):
            value=command_process.inspect(str(self.root),key)
            if value['state']=='EXITED':break
            time.sleep(.05)
        self.assertEqual(value['state'],'EXITED');self.assertEqual(value['exit_code'],7)
        self.assertEqual(base64.b64decode(value['stdout']),b'first\nlast\n')
        self.assertEqual(base64.b64decode(value['stderr']),b'error\n')
        again=command_process.start(str(self.root),key,base64.b64encode(b'echo must-not-run').decode())
        self.assertTrue(again['reserved']);self.assertEqual((self.root/'_fpilot/commands'/key/'command.sh').read_text(),body)
        tail=command_process.inspect(str(self.root),key,6,0)
        self.assertEqual(base64.b64decode(tail['stdout']),b'last\n')


if __name__=='__main__':unittest.main()
