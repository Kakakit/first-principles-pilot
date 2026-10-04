"""Persistent-session, cache, transfer, and dispatch boundary regression tests."""
import importlib.util,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend import remote_transport as transport,remote_cache,remote_energy,interactive_plots,fast_dispatch,servers,execution,recycle


class SessionTests(unittest.TestCase):
    def tearDown(self):transport.close_all()
    def local_worker(self):
        original=subprocess.Popen
        source=Path(transport.__file__).with_name('remote_worker.py')
        return patch.object(transport.subprocess,'Popen',side_effect=lambda argv,**kw:original([sys.executable,'-u',str(source)],**kw))
    def test_persistent_session_batches_and_isolates_command_environment(self):
        with self.local_worker(),patch.object(transport,'_interpreter',return_value=sys.executable),transport.operation('test') as scope:
            rows=transport.parallel([lambda:transport.run('test','printf one'),lambda:transport.run('test','printf two')])
            self.assertEqual([r['stdout'] for r in rows],['one','two'])
            self.assertEqual(sum(r['timing']['ssh_requests'] for r in rows),1)
            self.assertEqual(rows[0]['timing']['batch_size'],2)
            process=next(iter(transport.SESSIONS.values())).process.pid
            transport.run('test','cd /private/tmp; export FPILOT_TEST_VALUE=changed; printf changed')
            result=transport.run('test','printf "%s" "${FPILOT_TEST_VALUE:-empty}"')
            self.assertEqual(result['stdout'],'empty')
            self.assertEqual(next(iter(transport.SESSIONS.values())).process.pid,process)
            memo=transport.run('test','printf one')
            self.assertEqual(memo['timing']['ssh_requests'],0)
            self.assertTrue(memo['timing']['cache_hit'])
    def test_dispatch_failure_is_not_replayed_readonly_failure_reconnects_once(self):
        for mutating,expected in ((True,1),(False,2)):
            transport.close_all()
            with patch.object(transport.Session,'_request',side_effect=ConnectionError('offline')) as request:
                result=transport.run('test','qsub task' if mutating else 'stat file',mutating=mutating)
                self.assertIsNone(result['exit_code']);self.assertEqual(request.call_count,expected)
    def test_remote_timeout_has_no_automatic_dispatch_replay(self):
        with self.local_worker(),patch.object(transport,'_interpreter',return_value=sys.executable):
            result=transport.run('test','sleep 1',timeout=.05,mutating=True)
            self.assertIsNone(result['exit_code']);self.assertIn('do not replay',result['stderr'])


class CacheTests(unittest.TestCase):
    def test_cache_survives_reload_expiry_and_clear_retains_research_files(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(remote_cache,'ROOT',Path(tmp)):
            raw=Path(tmp)/'research';raw.write_text('original')
            remote_cache.write('test',{'source':'one'},{'value':2})
            self.assertEqual(remote_cache.read('test',{'source':'one'})['value'],2)
            self.assertIsNone(remote_cache.read('test',{'source':'two'}))
            self.assertIsNone(remote_cache.read('test',{'source':'one'},max_age=-1))
            remote_cache.clear();self.assertEqual(raw.read_text(),'original')
    def test_recycled_energy_source_cannot_be_used_from_cache(self):
        record={'id':'one','config':{'alias':'test'}}
        with patch.object(recycle,'assert_available',side_effect=ValueError('recycled')),patch.object(remote_energy.execution,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'recycled'):remote_energy.reference(record)
            ssh.assert_not_called()


class TransferTests(unittest.TestCase):
    def test_reader_refuses_large_file_directory_and_symlink_before_reading(self):
        path=Path(__file__).parents[1]/'resources/preflight/safe_pull_from_cluster.py'
        spec=importlib.util.spec_from_file_location('safe_pull_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);file=folder/'large';file.write_bytes(b'a'*(10485760+1));link=folder/'link';link.symlink_to(file)
            for target in (file,folder,link):
                result=subprocess.run([sys.executable,'-c',module.SMALL_READER,json.dumps([{'remote_path':str(target)}])],capture_output=True)
                self.assertNotEqual(result.returncode,0);self.assertNotIn(b'FPILOT_SAFE_FILE=',result.stdout)
            small=folder/'small';small.write_text('test')
            result=subprocess.run([sys.executable,'-c',module.SMALL_READER,json.dumps([{'remote_path':str(folder/'missing'),'fallback_path':str(small)}])],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn(b'"bytes": 4',result.stdout)


class DispatchGuardTests(unittest.TestCase):
    def test_capacity_guard_blocks_busy_and_changed_cores_before_mutation(self):
        profile={'ppn':48};config={'alias':'jutai-1','scheduler':'pbs','queue':'batch','walltime':'01:00:00'}
        record={'config':config,'node':'node01','cores':None,'reason':'','profile':profile}
        original=servers.availability_command
        for status,free,okay in (('FULLY_IDLE',48,True),('BUSY',0,False),('FULLY_IDLE',24,False)):
            capacity=f'===== jutai-1 =====\nSCHEDULER=PBS/Torque\nNODE=node01 STATUS={status} CPUS_FREE={free} CPUS_TOTAL=48\n'
            with tempfile.TemporaryDirectory() as tmp:
                bin=Path(tmp);qstat=bin/'qstat';qstat.write_text('#!/bin/sh\nprintf "Queue: batch\\n enabled = True\\n started = True\\n resources_max.walltime = 24:00:00\\n"\n');qstat.chmod(0o700)
                command='printf '+__import__('shlex').quote(capacity)+'\n'
                with patch.object(servers,'availability_command',return_value=command),patch.object(transport,'_interpreter',return_value=sys.executable):
                    body=fast_dispatch.prefix(record)+'printf FPILOT_MUTATION\n'
                result=subprocess.run(['bash','-c',body],capture_output=True,env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH']})
                self.assertEqual(result.returncode==0,okay,result.stderr)
                self.assertEqual(b'FPILOT_MUTATION' in result.stdout,okay)
                if not okay:self.assertIn(b'FPILOT_NOT_DISPATCHED=0',result.stdout)


if __name__=='__main__':unittest.main()


class SingleDispatchTests(unittest.TestCase):
    def test_new_prepared_attempt_uses_one_remote_call_and_duplicate_click_is_local(self):
        from backend import branch_bindings
        import copy,hashlib
        with tempfile.TemporaryDirectory() as tmp,patch.object(execution,'ROOT',Path(tmp)):
            config=copy.deepcopy(servers.DEFAULT);config['remote_workspace_root']='$HOME/230269265lht'
            key='a'*32;path='/home/u/230269265lht/material-projects/p/flows/main/stages/01-scf/attempts/new-a'
            record={'id':key,'state':'PREPARED','bundle_id':'b'*64,'config':config,'profile':servers.profile(config,'node01',48),
                'node':'node01','cores':None,'reason':'','probe_id':'p'*64,'dispatch_protocol':'fpilot.batch-dispatch.v1',
                'project_id':'p','node_id':'n','project_slug':'p','branch_scopes':['n'],'execution_scope':'n','script':'#!/bin/bash\necho test\n',
                'script_sha256':hashlib.sha256(b'#!/bin/bash\necho test\n').hexdigest(),'source_file_plan':{},
                'path_schema':'fpilot.remote-attempt-path','remote_workspace_root':'$HOME/230269265lht',
                'remote_workspace_root_abs':'/home/u/230269265lht','remote_path_absolute':path,
                'remote_relative_path':'material-projects/p/flows/main/stages/01-scf/attempts/new-a',
                'remote_project_path_absolute':'/home/u/230269265lht/material-projects/p','input_audit':{}}
            with execution.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,'PREPARED',json.dumps(record)))
            files={n:b'test\n' for n in ('INCAR','POSCAR','KPOINTS','POTCAR')}
            receipt={'exit_code':0,'stdout':'FPILOT_JOB_ID=123.mock\n','stderr':''}
            with patch.object(execution,'bundle',return_value=({'stage':'scf'},files)),patch.object(execution,'ssh',return_value=receipt) as ssh,patch.object(execution,'start_watcher'),patch.object(servers,'capacity_probe') as probe:
                result=execution.submit(key);self.assertEqual(result['state'],'SUBMITTED');self.assertEqual(ssh.call_count,1);probe.assert_not_called()
                self.assertIn('FPILOT_QUEUE_BEGIN',ssh.call_args.args[1]);self.assertIn('fpilot_submitting=1',ssh.call_args.args[1])
                execution.submit(key);self.assertEqual(ssh.call_count,1)
                self.assertEqual(branch_bindings.inspect('p',['n'])['state'],'BOUND')
