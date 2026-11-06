"""Direct execution contracts using only temporary files and synthetic scripts."""
import base64
import concurrent.futures
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from backend import command_process as runner, custom_commands, execution as ex, execution_cancel as cancel
from backend import chain_runs, execution_control as control, job_templates, recycle, scans, servers, site_registration, task_summary, worker
from backend.execution_modes import command_mode


def wait_for(read, accept, seconds=6):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = read()
        if accept(value):
            return value
        time.sleep(.05)
    raise AssertionError({k:value[k] for k in ('state','tracking_error','note') if k in value})


class DirectExecutionTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name).resolve()
        self.remote = self.root / 'remote'
        self.host = {'scheduler': 'custom', 'runtime_source': 'script', 'status': 'USER_CONFIGURED',
                     'command_mode': 'direct', 'remote_workspace_root': str(self.remote)}
        self.project = {'id':'p','name':'Direct test','remoteSlug':'direct-test','remotePathPolicy':'project-directory-v2',
                        'remoteWorkspaceRoot':str(self.remote),'nodes':[{'id':'n','data':{'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}}]}
        self.files = {'POSCAR': b'synthetic input\n', 'INCAR': b'EDIFF=1e-6\n', 'KPOINTS': b'fixture', 'POTCAR': b'fixture'}
        self.calls = []
        self.run_dir = None
        self.remote_state = {}
        self.remote_receipt_lost = False
        for module in (ex, recycle, job_templates, site_registration):
            self.install(patch.object(module, 'ROOT', self.root))
        self.install(patch.object(servers, 'load_registry', return_value={'hosts':{'lab':self.host}}))
        self.install(patch.object(site_registration, 'ssh_aliases', return_value=['lab']))
        self.install(patch.object(site_registration, 'connection_identity', return_value={'hostname':'local-fixture'}))
        self.install(patch('backend.remote_transport._interpreter', return_value=sys.executable))
        self.install(patch.object(ex, 'frozen_project', return_value=self.project))
        self.install(patch.object(ex, 'bundle', side_effect=lambda *a, **k: (
            {'stage':'scf','node_id':'n','dependencies':[]}, dict(self.files))))
        self.install(patch.object(ex.servers.pbs, 'audit_inputs', return_value={'status':'PASS'}))
        self.install(patch.object(ex, 'ssh', side_effect=self.ssh))

    def install(self, mock):
        self.addCleanup(mock.stop)
        return mock.start()

    def ssh(self, alias, command, data=None, **kwargs):
        self.calls.append((command, kwargs))
        if data:
            if not sys.platform.startswith('linux') and not os.environ.get('FPILOT_TEST_POSIX_RUNNER'):
                # The upload still runs through tar and sha256sum. Only the
                # Linux launch receipt is replaced on non-Linux test machines.
                key = re.search(r' start-direct .* ([a-f0-9]{32}) [A-Za-z0-9+/=]+ >', command)[1]
                prefix = command[:command.index('\n', command.index('FPILOT_SUBMIT_PHASE=SCHEDULER'))]
                command = prefix + '\nprintf "FPILOT_DIRECT_START=%s\\n" ' + shlex.quote(json.dumps(
                    {'id': key, 'state':'STARTING'}))
            result = subprocess.run(['/bin/bash','-c',command],input=data,capture_output=True)
            if self.remote_receipt_lost and result.returncode == 0:
                return {'exit_code':None,'stdout':'','stderr':'synthetic connection loss'}
        elif self.remote_state and any(' '+action+' ' in command for action in ('status-direct','logs-direct','stop-direct')):
            return {'exit_code':0,'stdout':json.dumps({'id':shlex.split(command)[5], **self.remote_state}),'stderr':''}
        else:
            result = subprocess.run(['/bin/bash','-c',command],capture_output=True)
        return {'exit_code':result.returncode,'stdout':result.stdout.decode(),'stderr':result.stderr.decode()}

    def prepare(self, script='printf "input="; head -n 1 POSCAR; exit 0\n'):
        preset = job_templates.save_template(script, 'synthetic', 'custom', 'lab', 'vasp_std', 'test')
        cfg = {**servers.select_profile('lab'),'template_id':preset['id']}
        record = ex.prepare('a'*64, cfg, '', 'command', project_id='p')
        self.run_dir = Path(record['remote_path_absolute'])
        return record

    def test_prepare_freezes_mode_and_recovers_lost_start_without_replay(self):
        record = self.prepare()
        self.assertFalse(self.run_dir.exists())
        self.host['command_mode'] = 'opaque'
        self.assertEqual(command_mode(servers.select_profile('lab')), 'opaque')
        self.assertEqual(command_mode(record['config']), 'direct')
        self.remote_receipt_lost = True
        result = ex.submit(record['id'])
        self.assertEqual(result['state'], 'UNKNOWN', result.get('dispatch_receipt'))
        self.assertEqual(ex.submit(record['id'])['state'], 'UNKNOWN')
        self.assertEqual(sum('FPILOT_SUBMIT_PHASE=INPUTS' in command for command, _ in self.calls),1)
        self.assertTrue((self.run_dir / 'POSCAR').is_file())
        self.assertFalse((self.run_dir / '.job-id').exists())
        if not sys.platform.startswith('linux') and not os.environ.get('FPILOT_TEST_POSIX_RUNNER'):
            self.remote_state={'state':'EXITED','exit_code':0,'process_pid':124,'protocol_version':1,
                               'stdout':'input=synthetic input\n','stderr':''}
        final = wait_for(lambda: ex.refresh(record['id']), lambda row: row['state']=='COMPLETED')
        self.assertEqual(final['direct_runtime']['exit_code'], 0)
        self.assertFalse(final['evidence']['vasp_timing'])
        self.assertEqual(task_summary.attempt(final)['config']['command_mode'],'direct')
        self.assertEqual(custom_commands.read(record['id'],tail=True)['stdout'],'input=synthetic input\n')

    def test_direct_stop_reconciles_same_operation(self):
        record = self.prepare('printf "started\n"; exec sleep 30\n')
        result = ex.submit(record['id'])
        self.assertEqual(result['state'], 'DISPATCHING', result.get('dispatch_receipt'))
        real = sys.platform.startswith('linux') or os.environ.get('FPILOT_TEST_POSIX_RUNNER')
        if not real:
            self.remote_state={'state':'RUNNING','process_pid':124,'protocol_version':1}
        wait_for(lambda: ex.refresh(record['id']), lambda row: row['state']=='RUNNING')
        preview = cancel.preview('p',record['id'])
        if not real:
            self.remote_state['stop']={'state':'ACCEPTED','operation_id':preview['operation_id']}
        stopped = cancel.cancel('p', record['id'], preview['token'], True)
        self.assertEqual(stopped['state'], 'ACCEPTED', stopped)
        cancel.cancel('p', record['id'], preview['token'], True)
        if not real:
            self.remote_state.update(state='CANCELLED', exit_code=-15)
        result = wait_for(lambda:cancel.recover('p',record['id']),lambda op:op['state']=='CONFIRMED')
        self.assertEqual(result['attempt_state'],'CANCELLED')
        self.assertEqual(sum(' stop-direct ' in command for command, _ in self.calls),1)

    def test_direct_stop_retries_only_on_explicit_confirmation_of_same_operation(self):
        record = self.prepare()
        key = record['id']
        ex.save({**record,'state':'RUNNING','command_run_id':key})
        self.remote_state = {'state':'RUNNING','process_pid':124}
        preview = cancel.preview('p', key)
        token, operation = preview['token'], preview['operation_id']
        self.remote_state['stop'] = {'state':'ACCEPTED','operation_id':operation}
        self.assertEqual(cancel.cancel('p', key, token, True)['state'],'ACCEPTED')
        self.remote_state['stop'] = {'state':'RETRYABLE','operation_id':'different'}
        self.assertEqual(cancel.recover('p',key)['state'],'ACCEPTED')
        self.remote_state['stop']['operation_id'] = operation
        recovered = ex.refresh(key, automatic=True)['cancellation']
        self.assertEqual(recovered['state'],'RETRYABLE')
        self.assertEqual(cancel.preview('p',key)['operation_id'],operation)
        self.assertEqual(cancel.cancel('p',key,token,True)['state'],'RETRYABLE')
        self.assertEqual(sum(' stop-direct ' in command for command, _ in self.calls),1)

        self.remote_state['stop']['state'] = 'ACCEPTED'
        result = cancel.cancel('p',key,token,True,retry=True)
        self.assertEqual((result['operation_id'],result['state']),(operation,'ACCEPTED'))
        cancel.cancel('p',key,token,True,retry=True)
        stops = [(shlex.split(command)[5:],options['mutating']) for command,options in self.calls
                 if ' stop-direct ' in command]
        self.assertEqual(stops,[([key,operation],True),([key,operation,'resume'],True)])
        self.remote_state.update(state='CANCELLED',exit_code=-15)
        self.assertEqual(cancel.recover('p',key)['state'],'CONFIRMED')

    def test_failed_exit_can_retry_only_the_missing_file_evidence(self):
        record = self.prepare()
        key = record['id']
        ex.save({**record,'state':'RUNNING','command_run_id':key})
        status = {'exit_code':0,'stdout':json.dumps({'id':key,'state':'EXITED','exit_code':7})}
        files = {'exit_code':0,'stdout':'FPILOT_TIMING=1\nFPILOT_EDIFF_NUMERIC=1e-6|5|2e-7|3e-7\n'}
        with patch.object(ex,'ssh',side_effect=[status,OSError('lost connection'),files]) as ssh:
            failed = ex.refresh(key)
            self.assertEqual(failed['state'],'FAILED')
            self.assertEqual(failed['evidence']['wrapper_exit'],7)
            self.assertFalse(failed['evidence'].get('file_evidence_checked'))
            refreshed = ex.refresh(key)
            self.assertEqual(refreshed['state'],'FAILED')
            self.assertEqual(refreshed['direct_runtime'],failed['direct_runtime'])
            self.assertEqual(refreshed['evidence']['wrapper_exit'],7)
            self.assertTrue(refreshed['evidence']['file_evidence_checked'])
            self.assertTrue(refreshed['evidence']['ediff_reached'])
            self.assertEqual(ex.refresh(key),refreshed)
        self.assertEqual(ssh.call_count,3)
        self.assertEqual(ssh.call_args_list[1],ssh.call_args_list[2])
        self.assertIs(ssh.call_args.kwargs['mutating'],False)

    def test_uncertain_run_reserves_last_slot_and_ssh_error_keeps_state(self):
        first = self.prepare()
        second = self.prepare()
        self.remote_state = {'state':'RUNNING','process_pid':124,'protocol_version':1}
        ex.save({**first,'state':'UNKNOWN','quota_released':True})
        with closing(ex.db()) as db:
            with self.assertRaisesRegex(ValueError,first['id'][:8]):
                control.enforce_limits(db,second)
        self.assertEqual(ex.get(second['id'])['state'],'PREPARED')
        ex.save({**first,'state':'RUNNING'})
        with patch.object(ex,'ssh',return_value={'exit_code':None,'stderr':'offline'}) as ssh:
            result=ex.refresh(first['id'])
        self.assertEqual(result['state'],'RUNNING')
        self.assertEqual(result['tracking_error'],'offline')
        self.assertFalse(ssh.call_args.kwargs['mutating'])

    def test_two_attempts_compete_for_one_direct_slot(self):
        first, second = self.prepare(), self.prepare()

        def submit(row):
            try:
                return ex.submit(row['id'])['state']
            except ValueError:
                return ex.get(row['id'])['state']

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (first, second)))
        self.assertEqual(sorted(results), ['DISPATCHING', 'PREPARED'])
        self.assertEqual(sum('FPILOT_SUBMIT_PHASE=INPUTS' in command for command, _ in self.calls),1)

    def test_program_end_and_electronic_nonconvergence_are_separate(self):
        from backend.source_contracts import convergence
        for code, state, evidence_state in ((0, 'COMPLETED', 'EXHAUSTED'), (7, 'FAILED', 'UNVERIFIED')):
            with self.subTest(exit_code=code):
                record = self.prepare(f'exit {code}\n')
                ex.submit(record['id'])
                (self.run_dir/'OUTCAR').write_text('General timing and accounting informations\n')
                (self.run_dir/'OSZICAR').write_text('DAV: 60 -5.0 1e-3 1e-4\n')
                if not sys.platform.startswith('linux') and not os.environ.get('FPILOT_TEST_POSIX_RUNNER'):
                    self.remote_state={'state':'EXITED','exit_code':code,'process_pid':124,'protocol_version':1}
                result=wait_for(lambda:ex.refresh(record['id']),lambda row:row['state']==state)
                self.assertEqual(result['direct_runtime']['exit_code'],code)
                self.assertTrue(result['evidence']['vasp_timing'])
                self.assertFalse(result['evidence']['ediff_reached'])
                self.assertEqual(convergence(result,'scf','NELM=60\n')['status'],evidence_state)

    def test_worker_filters_opaque_and_protects_upload_grace(self):
        direct = self.prepare()
        opaque = self.prepare()
        uploading = self.prepare()
        ex.save({**direct,'state':'RUNNING'})
        ex.save({**opaque,'state':'COMMAND_RUNNING','config':{**opaque['config'],'command_mode':None}})
        ex.save({**uploading,'state':'DISPATCHING','dispatch_started_at':ex.now()})
        with patch('backend.remote_health.due',return_value=[]), patch('backend.remote_monitor.batch',return_value={}) as batch, \
             patch('backend.task_recovery.tick'),patch('backend.scans.tick'),patch('backend.chain_runs.tick'),patch('backend.extension_runs.tick'), \
             patch.object(ex,'refresh',return_value={'state':'RUNNING'}) as refresh:
            self.assertEqual(worker.tick(),{'checked':1,'errors':[]})
        batch.assert_called_once_with([])
        self.assertEqual(refresh.call_args.args[0],direct['id'])

    def test_batch_entry_points_reject_direct_before_dispatch(self):
        config = servers.select_profile('lab')
        for call in (lambda:scans.resource_preview('scan','p',config,[]),
                     lambda:scans.authorize('scan','p','hash',config),
                     lambda:chain_runs.resource_preview('chain','p',config,{}),
                     lambda:chain_runs.authorize('chain','p','hash','hash',config,{},{})):
            with self.assertRaisesRegex(ValueError,'暂不支持自动派发'):
                call()
        self.assertEqual(self.calls, [])


class DirectRunnerTests(unittest.TestCase):
    def test_stop_launch_failure_can_resume_original_request_once(self):
        key, operation = 'a'*32, 'b'*32
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)

            def observe(path):
                return {'state':'RUNNING','stop':runner.stop_progress(path,runner.read_json(path,'cancel-receipt.json'))}

            api = (lambda pid:os.open(os.devnull,os.O_RDONLY),lambda fd,sig:None)
            with patch.object(runner,'observe_direct',side_effect=observe), patch.object(runner,'pidfd_api',return_value=api), \
                 patch.object(runner,'detached',side_effect=[OSError('synthetic launch failure'),None]) as launch:
                first = runner.stop_direct(directory,key,directory,operation)
                self.assertEqual(first['stop']['state'],'RETRYABLE')
                request = runner.read_json(directory,'cancel-request.json')
                self.assertEqual(runner.stop_direct(directory,key,directory,operation)['stop']['state'],'RETRYABLE')
                self.assertEqual(runner.stop_direct(directory,key,directory,'c'*32,resume=True)['stop']['state'],'UNKNOWN')
                resumed = runner.stop_direct(directory,key,directory,operation,resume=True)
                self.assertEqual(resumed['stop']['state'],'ACCEPTED')
                self.assertEqual(runner.stop_direct(directory,key,directory,operation,resume=True),resumed)
            self.assertEqual(runner.read_json(directory,'cancel-request.json'),request)
            self.assertEqual([call.args[-2:] for call in launch.call_args_list],[(operation,'1'),(operation,'2')])

    def test_stop_progress_checks_worker_identity_and_fences_delayed_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            operation = 'b'*32
            worker = {'pid':42,'ticks':9,'state':b'S'}
            receipt = {'operation_id':operation,'state':'ACCEPTED','worker_generation':2,
                       'worker_launch_at':time.time(),'worker_pid':42,'worker_start_ticks':9}
            with patch.object(runner,'process_stat',return_value=worker):
                self.assertEqual(runner.stop_progress(directory,receipt)['state'],'ACCEPTED')
            with patch.object(runner,'process_stat',return_value={**worker,'ticks':10}):
                self.assertEqual(runner.stop_progress(directory,receipt)['state'],'RETRYABLE')
            unclaimed = {k:v for k,v in receipt.items() if k not in ('worker_pid','worker_start_ticks')}
            unclaimed['worker_launch_at'] -= runner.STOP_START_GRACE + 1
            self.assertEqual(runner.stop_progress(directory,unclaimed)['state'],'RETRYABLE')
            self.assertEqual(runner.stop_progress(directory,{'operation_id':operation,'state':'UNKNOWN'})['state'],'RETRYABLE')
            runner.write(directory/'cancel-receipt.json',unclaimed)
            with patch.object(runner,'pidfd_api') as api:
                runner.stop_worker(directory,operation,1)
                api.assert_not_called()
            self.assertEqual(runner.read_json(directory,'cancel-receipt.json'),unclaimed)

    def test_utf8_boundaries_tail_truncation_and_recreation(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'out'
            log.write_bytes(b'a'*(runner.LOG_BYTES-1)+b'\xe4\xb8')
            text,offset,more,token,_ = runner.read_log(log,0,'',False,False)
            self.assertEqual((len(text),offset,more),(runner.LOG_BYTES-1,runner.LOG_BYTES-1,True))
            with log.open('ab') as stream:
                stream.write(b'\xad')
            self.assertEqual(runner.read_log(log,offset,token,False,False)[0],'中')
            tail=runner.read_log(log,0,'',True,True)
            self.assertTrue(tail[0].endswith('中'))
            self.assertNotIn('�',tail[0])
            replacement = log.with_suffix('.new')
            replacement.write_text('new')
            replacement.replace(log)
            self.assertEqual(runner.read_log(log,offset,token,False,True)[::4],('new',True))

    def test_pid_reuse_and_boot_or_host_change_cannot_inherit_running_status(self):
        identity={'execution_host':'a','boot_id':'boot','process_pid':22,'process_group_id':22,'process_start_ticks':3}
        replacement={'state':b'S','pid':22,'session':22,'group':22,'ticks':4}
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            runner.write(directory/'identity.json',identity)
            runner.write(directory/'status.json',{'state':'STARTING'})
            with patch.object(runner,'process_stat',return_value=replacement),patch.object(runner,'host_identity',return_value=identity):
                self.assertEqual(runner.observe_direct(directory)['state'],'LOST')
                self.assertEqual(runner.group_members(identity),[])
            with patch.object(runner,'host_identity',return_value={**identity,'execution_host':'b'}):
                self.assertEqual(runner.observe_direct(directory)['state'],'UNKNOWN')
            with patch.object(runner,'host_identity',return_value={**identity,'boot_id':'new'}):
                self.assertEqual(runner.observe_direct(directory)['state'],'LOST')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Requires a real Linux /proc and process groups')
    def test_linux_process_lifecycle_and_duplicate_run_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            key='a'*32
            command=lambda body: subprocess.run([sys.executable,runner.__file__,'start-direct',temp,key,
                base64.b64encode(body.encode()).decode()],check=True,capture_output=True,text=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(command,['printf x >> launched; sleep .7 & exit 7\n']*2))
            directory=root/'_fpilot'/'commands'/key
            wait_for(lambda:runner.observe_direct(directory),lambda value:value['state']=='EXITING')
            final=wait_for(lambda:runner.observe_direct(directory),lambda value:value['state']=='EXITED')
            self.assertEqual(final['exit_code'],7)
            self.assertEqual((root/'launched').read_text(),'x')
            self.assertEqual(runner.stop_direct(root,key,directory,'b'*32)['state'],'EXITED')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Requires a real Linux /proc and pidfd')
    def test_linux_kill_fallback_without_supervisor(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            key='c'*32
            directory=root/'_fpilot'/'commands'/key
            runner.start_direct(root,key,base64.b64encode(b'trap "" TERM; echo ready; exec sleep 40\n').decode())
            observed=wait_for(lambda:runner.observe_direct(directory),lambda value:value['state']=='RUNNING'
                              and (directory/'stdout.log').stat().st_size>0)
            try:
                os.kill(observed['supervisor_pid'],signal.SIGKILL)
                wait_for(lambda:runner.observe_direct(directory),lambda value:'收集器已中断' in value.get('note',''))
                result=runner.stop_direct(root,key,directory,'d'*32)
                self.assertEqual(result['stop']['state'],'ACCEPTED')
                stopped=wait_for(lambda:runner.observe_direct(directory),lambda value:value['state']=='CANCELLED',seconds=16)
                self.assertEqual(stopped['stop']['signal_sent'],'SIGKILL')
            finally:
                if runner.group_members(observed):
                    os.kill(observed['process_pid'],signal.SIGKILL)
