"""Portable startup, process, lock, path and controlled-transfer regressions."""
import importlib.util,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path,PurePosixPath
from unittest.mock import Mock,patch
from backend import platform_runtime as platform,local_setup,site_registration,servers,remote_transport as transport,safe_files


class PlatformTests(unittest.TestCase):
    def test_launcher_shutdown_and_abrupt_exit_do_not_leave_a_worker(self):
        root=Path(__file__).resolve().parents[1]
        if not (root/'dist/index.html').exists():self.skipTest('Build the frontend before launcher acceptance')
        from urllib.request import urlopen
        for abrupt in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                data=Path(tmp)/'中文 数据'
                with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
                command=[sys.executable,str(root/'launch.py'),'--workspace',str(data),'--config',str(Path(tmp)/'local.json'),'--port',str(port),'--no-browser']
                process=subprocess.Popen(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                worker_pid=None
                try:
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline:
                        try:
                            with urlopen('http://127.0.0.1:'+str(port)+'/api/execution-control',timeout=1) as response:state=json.load(response)['worker']
                            if state.get('healthy'):worker_pid=state['pid'];break
                        except OSError:pass
                        if process.poll() is not None:break
                        time.sleep(.1)
                    self.assertIsNotNone(worker_pid,'launcher/worker did not become ready')
                    process.kill() if abrupt else process.terminate()
                    process.wait(timeout=12)
                    deadline=time.monotonic()+5
                    while platform.process_alive(worker_pid) and time.monotonic()<deadline:time.sleep(.1)
                    self.assertFalse(platform.process_alive(worker_pid),'orphaned worker after launcher exit')
                    self.assertFalse(json.loads((data/'worker-state.json').read_text())['running'])
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
                    process.communicate(timeout=5)

    def test_windows_liveness_never_uses_kill_and_only_owned_children_stop(self):
        with patch.object(platform.os,'name','nt'),patch.object(platform,'_windows_alive',return_value=True) as check,patch.object(platform.os,'kill') as kill:
            self.assertTrue(platform.process_alive(1234));self.assertFalse(platform.process_alive(0));kill.assert_not_called();check.assert_called_once_with(1234)
            child=Mock(pid=1234);child.poll.side_effect=[None,0]
            with patch.object(platform.subprocess,'run') as command:platform.stop_child(child)
            self.assertEqual(command.call_args.args[0],['taskkill','/PID','1234','/T','/F']);child.kill.assert_not_called()
            self.assertEqual(platform.ssh_options('host'),[])

    def test_process_lock_excludes_second_process_and_is_released_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock'
            script="from backend.platform_runtime import exclusive_lock; import sys;\nwith exclusive_lock(sys.argv[1]) as acquired: print(acquired)"
            with platform.exclusive_lock(path) as first:
                self.assertTrue(first)
                out=subprocess.check_output([sys.executable,'-c',script,str(path)],text=True)
                self.assertEqual(out.strip(),'False')
            out=subprocess.check_output([sys.executable,'-c',script,str(path)],text=True)
            self.assertEqual(out.strip(),'True')

    def test_pipe_reader_handles_binary_chunks_without_unix_select(self):
        session=transport.Session('unused','normal')
        session.closed=True
        session.process=subprocess.Popen([sys.executable,'-u','-c',"import sys;sys.stderr.write('diagnostic');sys.stdout.buffer.write(b'a'*150000+b'\\nlast\\n');sys.stdout.flush()"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        try:
            session._pipe_readers()
            self.assertEqual(len(session._line(time.monotonic()+5)),150000)
            self.assertEqual(session._line(time.monotonic()+5),b'last')
        finally:session.close()


class SetupTests(unittest.TestCase):
    def test_empty_workspace_freeze_dispatch_and_recovery_smoke(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            command=[sys.executable,str(root/'tests/portable_workflow.py'),str(Path(tmp)/'中文 数据'),str(root/'tests/fixtures/Si.structure.json')]
            result=subprocess.run(command,cwd=root,text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt=json.loads(result.stdout);self.assertTrue(receipt['simulation']);self.assertEqual(receipt['real_ssh_requests'],0)
            self.assertEqual(receipt['unknown_no_resubmit'],'PASS');self.assertEqual(receipt['project_recycle_restore'],'PASS')

    def test_unicode_space_paths_saved_and_invalid_config_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);current=root/'原始';current.mkdir();research=root/'研究 文件';research.mkdir();config=root/'local.json'
            with patch.object(local_setup,'ROOT',current),patch.object(local_setup,'CONFIG',config),patch.dict(os.environ,{'FPILOT_PORT':'8765'},clear=False):
                value={'workspace':str(root/'中文 数据'),'research_root':str(research),'potcar_library':'','port':8766,'worker_enabled':False}
                result=local_setup.save(value);self.assertTrue(result['restart_required']);self.assertFalse(result['saved']['worker_enabled'])
                before=config.read_bytes();self.assertEqual(json.loads(before)['python'],sys.executable)
                with self.assertRaisesRegex(ValueError,'目录不存在'):local_setup.save({**value,'potcar_library':str(root/'missing')})
                self.assertEqual(config.read_bytes(),before);self.assertTrue(current.is_dir())

    def test_new_server_persists_without_inheriting_smoke_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=root/'builtin.json';registry.write_text('{"hosts":{}}')
            (root/'.ssh').mkdir();(root/'.ssh/config').write_text('Host my-lab\n  HostName invalid.example\n')
            config={'alias':'my-lab','scheduler':'pbs','queue':'batch','python':'/opt/python/bin/python3','vasp_executable':'/opt/vasp/bin/vasp_std',
                'mpi_executable':'/opt/mpi/mpirun','environment_script':'/opt/env.sh','remote_workspace_root':'$HOME/230269265lht/my-work'}
            with patch.object(site_registration,'ROOT',root),patch.object(servers,'ROOT',root),patch.object(servers,'REGISTRY_PATH',registry),patch.object(servers.Path,'home',return_value=root):
                saved=site_registration.register(config)
                self.assertEqual(saved['config']['vasp_executable'],config['vasp_executable'])
                self.assertEqual(servers.load_registry()['hosts']['my-lab']['program_validation'],'NOT_PERFORMED')
                self.assertEqual(servers.registered_profiles()[0]['runtime_status'],'USER_CONFIGURED')
                self.assertIn('尚无',servers.registered_profiles()[0]['reason'])
                self.assertEqual(servers.default_config()['alias'],'my-lab')
                with self.assertRaises(ValueError):site_registration.register({**config,'python':'C:\\python.exe'})
                before=(root/'site-registry.json').read_bytes()
                with self.assertRaisesRegex(ValueError,'明确别名'):site_registration.register({**config,'alias':'missing'})
                self.assertEqual((root/'site-registry.json').read_bytes(),before)
                with patch.object(servers,'save_config',side_effect=ValueError('invalid script configuration')):
                    with self.assertRaisesRegex(ValueError,'invalid script'):site_registration.register({**config,'queue':'other'})
                self.assertEqual((root/'site-registry.json').read_bytes(),before)

    def test_script_site_with_include_alias_and_read_only_connection_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=root/'builtin.json';registry.write_text('{"hosts":{}}')
            (root/'.ssh'/'sites').mkdir(parents=True);(root/'.ssh/config').write_text('Include sites/*\n')
            (root/'.ssh/sites/lab').write_text('Host my-lab\n HostName invalid.example\n Port 103\n')
            config={'alias':'my-lab','scheduler':'pbs','runtime_source':'script','remote_workspace_root':'$HOME/fpilot','python':'/usr/bin/python3'}
            with patch.object(site_registration,'ROOT',root),patch.object(servers,'ROOT',root),patch.object(servers,'REGISTRY_PATH',registry),patch.object(Path,'home',return_value=root):
                self.assertEqual(site_registration.ssh_aliases(),['my-lab'])
                saved=site_registration.register(config)
                self.assertEqual(saved['config']['runtime_source'],'script');self.assertEqual(saved['config']['queue'],'')
                self.assertEqual(saved['config']['remote_workspace_root'],'$HOME/fpilot')
                parsed=Mock(returncode=0,stdout='hostname invalid.example\nuser test\nport 103\n',stderr='')
                connected=Mock(returncode=0,stdout='FPILOT_SSH_OK\nFPILOT_TOOL_qsub=/bin/qsub\nFPILOT_TOOL_qstat=/bin/qstat\nFPILOT_PYTHON={"path":"/usr/bin/python3","version":[3,9,6],"supported":true}\nFPILOT_ROOT_ABS=/home/test/fpilot\nFPILOT_ROOT_PARENT=/home/test\nFPILOT_ROOT_SNAPSHOT=1:2\nFPILOT_ROOT_READY=1\n',stderr='')
                with patch.object(site_registration.subprocess,'run',return_value=parsed),patch.object(transport,'legacy',return_value={'exit_code':0,'stdout':connected.stdout,'stderr':''}) as remote:
                    report=site_registration.test_connection('my-lab',remote_workspace_root='$HOME/fpilot')
                self.assertTrue(report['connected']);self.assertTrue(report['workspace']['ready'])
                self.assertFalse(report['remote_mutation']);self.assertEqual(report['python']['path'],'/usr/bin/python3')
                argv=remote.call_args.args[0]
                self.assertEqual(remote.call_args.kwargs['retry'],'manual')
                self.assertNotIn('ControlMaster=no',argv);self.assertNotIn('ControlPath=none',argv);self.assertIn('BatchMode=yes',argv)
                self.assertNotIn('mkdir',argv[-1]);self.assertNotIn('source ',argv[-1])
                self.assertEqual(report['schedulers'],['pbs'])
                denied=Mock(returncode=255,stdout='',stderr='Permission denied (publickey)')
                with patch.object(site_registration.subprocess,'run',return_value=parsed),patch.object(transport,'legacy',return_value={'exit_code':None,'stdout':'','stderr':denied.stderr}):
                    with self.assertRaisesRegex(ValueError,'软件进程'):site_registration.test_connection('my-lab')
                no_python=Mock(returncode=0,stdout='FPILOT_SSH_OK\n',stderr='')
                with patch.object(site_registration.subprocess,'run',return_value=parsed),patch.object(transport,'legacy',return_value={'exit_code':0,'stdout':no_python.stdout,'stderr':''}):
                    report=site_registration.test_connection('my-lab')
                    self.assertIsNone(report['python']);self.assertIn('3.8+',report['note'])

    def test_research_path_and_worker_changes_require_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();config=root/'local.json';research=root/'research';research.mkdir()
            with patch.object(local_setup,'ROOT',root),patch.object(local_setup,'CONFIG',config),patch.dict(os.environ,{'FPILOT_PORT':'8765','FPILOT_RESEARCH_ROOT':''},clear=False):
                value={'workspace':str(root),'research_root':'','potcar_library':'','port':8765,'worker_enabled':True}
                self.assertFalse(local_setup.save(value)['restart_required'])
                self.assertTrue(local_setup.save({**value,'research_root':str(research)})['restart_required'])
                self.assertTrue(local_setup.save({**value,'worker_enabled':False})['restart_required'])

    def test_empty_release_has_no_preselected_school_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=root/'empty.json';registry.write_text('{"hosts":{}}')
            with patch.object(servers,'REGISTRY_PATH',registry),patch.object(site_registration,'ROOT',root):
                self.assertEqual(servers.default_config()['alias'],'');self.assertEqual(servers.registered_profiles(),[])


class TransferTests(unittest.TestCase):
    def tearDown(self):transport.close_all()
    def test_windows_tcp_bridge_uses_same_small_file_gate_and_single_request(self):
        helper=Path(__file__).parents[1]/'resources/preflight/safe_pull_from_cluster.py'
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'FPILOT_IPC_MODE':'tcp'}),patch.object(transport,'_interpreter',return_value=sys.executable):
            source=Path(tmp)/'source';source.write_text('same values');destination=Path(tmp)/'returned'
            def local(alias,command,**kwargs):
                result=subprocess.run(['bash','-c',command],capture_output=True,text=True)
                return {'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'timing':{'ssh_requests':1}}
            with patch.object(transport,'run',side_effect=local) as remote:
                receipt=safe_files.pull_many('my-lab',[{'remote_path':str(source),'destination':str(destination)}])
                self.assertEqual(destination.read_bytes(),source.read_bytes());self.assertEqual(remote.call_count,1)
                self.assertEqual(receipt['limit_bytes'],10485760)
            endpoint=transport.bridge_path();port=int(endpoint.rsplit(':',1)[1])
            with socket.create_connection(('127.0.0.1',port)) as sock:
                stream=sock.makefile('rwb');stream.write(b'{"token":"wrong"}\n');stream.flush();receipt=json.loads(stream.readline())
                self.assertIsNone(receipt['exit_code'])


if __name__=='__main__':unittest.main()
