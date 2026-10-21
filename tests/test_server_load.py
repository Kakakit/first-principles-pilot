"""Offline load contracts: no cluster access, real local parsers and bounded failures."""
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import ExitStack,redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch
from backend import (remote_health as health,remote_transport as transport,remote_monitor,
                     artifacts,source_files,scan_results,extension_process as process,
                     extension_protocol as protocol,extension_runs,analysis_queue,interactive_plots)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.stack.close)
        for module in (health,artifacts,protocol):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.dict(os.environ,FPILOT_REMOTE_DISABLED='1'))

    def test_legacy_probe_is_ignored_and_recovery_does_not_start_scientific_python(self):
        health.finish(health.claim('fixture',()),'offline','grep huge-OUTCAR')
        with health.edit() as rows:next(iter(rows.values()))['probe_command']='sha256sum OUTCAR'
        with patch('backend.site_registration.connection_identity',return_value={}),patch.object(transport,'_options',return_value=[]),patch.object(transport,'legacy',return_value={}) as wire:
            transport.recover('fixture',manual=True)
        self.assertEqual(wire.call_args.args[0][-1],'printf FPILOT_CONNECTION_OK')
        self.assertEqual(wire.call_args.kwargs['stdout_limit'],4096)

    def test_business_rejection_does_not_consume_connection_retries(self):
        response={'exit_code':1,'stdout':'','stderr':base64.b64encode(b'qsub rejected resource request').decode()}
        with patch.dict(os.environ,FPILOT_REMOTE_DISABLED='0'),patch('backend.site_registration.connection_identity',return_value={}),patch('backend.remote_worker.execute',return_value=response):
            result=transport.legacy(['ssh','fixture','qsub run.pbs'])
        self.assertEqual(result['exit_code'],1);self.assertEqual(health.status('fixture')[0]['state'],'READY')

    def test_concurrent_manual_recovery_reuses_one_completed_probe(self):
        health.finish(health.claim('fixture',()),'offline')
        entered=threading.Event();release=threading.Event();second_started=threading.Event()
        def execute(*args,**kwargs):
            entered.set();release.wait(3)
            return {'exit_code':0,'stdout':base64.b64encode(b'FPILOT_CONNECTION_OK').decode(),'stderr':''}
        def second():second_started.set();return transport.recover('fixture',manual=True)
        with patch.dict(os.environ,FPILOT_REMOTE_DISABLED='0'),patch('backend.site_registration.connection_identity',return_value={}),patch.object(transport,'_options',return_value=[]),patch('backend.remote_worker.execute',side_effect=execute) as wire:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(transport.recover,'fixture',True);self.assertTrue(entered.wait(2))
                other=pool.submit(second);self.assertTrue(second_started.wait(2));release.set()
                self.assertEqual(first.result()['exit_code'],0);self.assertEqual(other.result()['exit_code'],0)
            self.assertEqual(wire.call_count,1)
        self.assertEqual(health.status('fixture')[0]['state'],'READY')

    def test_twenty_jobs_use_one_scheduler_command_and_missing_id_is_not_complete(self):
        rows=[{'id':str(i),'job_id':str(i)+'.node','state':'RUNNING','config':{'alias':'fixture','scheduler':'pbs'}} for i in range(20)]
        reply={'exit_code':1,'stdout':''.join('Job Id: %s.node\n job_state = R\n'%i for i in range(19)),'stderr':'Unknown Job Id 19.node'}
        with patch.object(remote_monitor.ex,'ssh',return_value=reply) as ssh:result=remote_monitor.batch(rows)
        self.assertEqual(ssh.call_count,1);self.assertEqual(ssh.call_args.args[1].count('qstat '),1)
        self.assertEqual(remote_monitor.active_state(rows[0],result['0']),'RUNNING')
        self.assertIsNone(remote_monitor.active_state(rows[19],result['19']))

    def test_slurm_host_groups_preserve_exact_array_job_ids(self):
        rows=[{'id':key,'job_id':job,'state':'RUNNING','config':{'alias':alias,'scheduler':'slurm'}} for key,job,alias in [('a','123_2','one'),('b','124','one'),('c','125','two')]]
        with patch.object(remote_monitor.ex,'ssh',side_effect=[{'exit_code':0,'stdout':'123_2|RUNNING\n124|PENDING\n'},{'exit_code':0,'stdout':'125|RUNNING\n'}]) as ssh:result=remote_monitor.batch(rows)
        self.assertEqual(ssh.call_count,2);self.assertIn('--jobs=123_2,124',ssh.call_args_list[0].args[1])
        self.assertEqual(remote_monitor.active_state(rows[0],result['a']),'RUNNING')

    def test_get_listing_never_launches_ssh_and_stale_cache_is_not_absence_evidence(self):
        record={'id':'a','state':'RUNNING','config':{'alias':'fixture'}}
        folder=self.root/'artifact-cache';folder.mkdir()
        (folder/'a.json').write_text(json.dumps({'source_identity':{'root':'fixture'},'files':[{'path':'OUTCAR'}],'checked_epoch':time.time()-61,'refreshed_at':'old','truncated':True}))
        with patch.object(artifacts.execution,'get',return_value=record),patch.object(artifacts,'_identity',return_value={'root':'fixture'}),patch.object(artifacts.execution,'ssh',side_effect=AssertionError('SSH forbidden')):
            value=artifacts.listing('a');self.assertIsNone(artifacts.cached_listing(record))
        self.assertTrue(value['needs_refresh']);self.assertEqual(value['files'][0]['path'],'OUTCAR')

    def test_empty_listing_is_unobserved_without_remote_access(self):
        record={'id':'a','config':{'alias':'fixture'}}
        with patch.object(artifacts.execution,'get',return_value=record),patch.object(artifacts,'_root',return_value='/fixture'),patch.object(artifacts.execution,'ssh',side_effect=AssertionError('SSH forbidden')):
            value=artifacts.listing('a')
        self.assertEqual(value['verification_status'],'UNOBSERVED');self.assertTrue(value['needs_refresh'])

    def test_directory_entries_count_against_the_visit_budget(self):
        visits=[]
        class Entry:
            def __init__(self,index):self.path='/fixture/'+str(index)
            def stat(self,**kw):visits.append(self.path);return types.SimpleNamespace(st_dev=1,st_mode=stat.S_IFDIR)
        entries=Mock();entries.__enter__=Mock(return_value=iter(Entry(i) for i in range(2100)));entries.__exit__=Mock(return_value=False)
        output=io.StringIO()
        with patch('os.getcwd',return_value='/fixture'),patch('os.stat',return_value=types.SimpleNamespace(st_dev=1)),patch('os.scandir',return_value=entries),redirect_stdout(output):exec(artifacts.LIST_FILES,{})
        self.assertEqual(len(visits),2000);self.assertIn('FPILOT_TRUNCATED=1',output.getvalue())

    def test_large_outputs_allow_metadata_but_inputs_and_full_checks_require_content(self):
        size=2*1024*1024
        receipt={'exit_code':0,'stdout':'FPILOT_FILE=OUTCAR|-|%d|stamp\n'%size}
        value=source_files.snapshot('fixture','/fixture',['OUTCAR'],lambda *a,**kw:receipt)
        self.assertEqual(value['OUTCAR']['verification_mode'],'metadata')
        with self.assertRaises(ValueError):source_files.snapshot('fixture','/fixture',['OUTCAR'],lambda *a,**kw:receipt,mode='full')
        self.assertTrue(source_files.content_required('POSCAR',size,'lightweight'))
        self.assertTrue(source_files.content_required('OUTCAR',100,'lightweight'))

    def test_summary_opens_once_then_uses_version_cache_and_invalidates_on_change(self):
        file=self.root/'OUTCAR';file.write_text(' free  energy   TOTEN = -2.0 eV\nElapsed time (sec): 4\n')
        wrapper="import builtins,sys,atexit\noriginal=builtins.open\ncount=[0]\ndef counted(*args,**kw):\n count[0]+=int(str(args[0])=='OUTCAR');return original(*args,**kw)\nbuiltins.open=counted\natexit.register(lambda:print(count[0],file=sys.stderr))\n"
        script=wrapper+scan_results.SUMMARY_PARSER
        def run(cached):return subprocess.run([sys.executable,'-c',script,json.dumps(cached)],cwd=self.root,capture_output=True,text=True,check=True)
        first=run({});self.assertEqual(first.stderr.strip(),'1')
        signature=json.loads(next(line.split('=',1)[1] for line in first.stdout.splitlines() if line.startswith('FPILOT_SUMMARY_STAMP=')))
        cached={'signature':signature,'output':first.stdout}
        self.assertEqual(run(cached).stderr.strip(),'0')
        file.write_text(' free  energy   TOTEN = -3.0 eV\nElapsed time (sec): 4\n')
        changed=run(cached);self.assertEqual(changed.stderr.strip(),'1');self.assertIn('-3.0',changed.stdout)
        self.assertIn(hashlib.sha256(file.read_bytes()).hexdigest(),changed.stdout)

    @unittest.skipUnless(hasattr(os,'killpg'),'POSIX process-group cleanup')
    def test_uninterruptible_child_cannot_make_cleanup_wait_forever(self):
        child=Mock(pid=987654);child.wait.side_effect=subprocess.TimeoutExpired('fixture',2)
        with patch.object(process.os,'killpg'),patch.object(Path,'iterdir',side_effect=AssertionError('no /proc walk')):
            self.assertFalse(process.stop_descendants(child))
        self.assertLessEqual(child.wait.call_args.kwargs['timeout'],2)

    def test_cli_unconfirmed_stop_is_persisted_and_kept_unknown(self):
        child=Mock(pid=987654);child.wait.side_effect=subprocess.TimeoutExpired('fixture',1)
        with patch.object(process.pathlib.Path,'cwd',return_value=self.root),patch.object(process.subprocess,'Popen',return_value=child),patch.object(process,'identity',return_value=None),patch.object(process,'stop_descendants',return_value=False):
            self.assertEqual(process.cli(1,['fixture']),124)
        marker=json.loads((self.root/'stop-unconfirmed.json').read_text())
        record={'id':'a','runtime':{'python':sys.executable}}
        with patch.object(extension_runs.remote,'inspect',return_value={'stop_unconfirmed':marker}),patch.object(extension_runs,'save',side_effect=lambda row:row):
            result=extension_runs._refresh(record)
        self.assertEqual(result['state'],'UNKNOWN');self.assertNotIn('exit_code',result)

    def test_local_timeout_is_bounded_and_keeps_unconfirmed_process(self):
        record={'id':'a','runtime':{'python':sys.executable,'cores':3},'manifest':{'entrypoint':'cli.py','execution':{'timeout_seconds':1}}}
        child=Mock(pid=987654);child.wait.side_effect=subprocess.TimeoutExpired('fixture',1)
        with ExitStack() as stack:
            stack.enter_context(patch.object(extension_runs,'assert_available'))
            stack.enter_context(patch.object(extension_runs,'inactive',return_value=False))
            stack.enter_context(patch.object(extension_runs,'save',side_effect=lambda row:row))
            stack.enter_context(patch.object(extension_runs,'directory',return_value=self.root))
            stack.enter_context(patch.object(extension_runs,'_input_request',return_value={}))
            stack.enter_context(patch.object(protocol,'inspect_package',return_value={}))
            stack.enter_context(patch.object(protocol,'doctor',return_value={'ready':True}))
            stack.enter_context(patch.object(protocol,'inside',return_value=self.root/'cli.py'))
            stack.enter_context(patch.object(protocol,'invoke',return_value={'schema':'fpilot.extension-check.v2','status':'APPLICABLE','reason':''}))
            stack.enter_context(patch.object(extension_runs,'stop_child'))
            spawn=stack.enter_context(patch.object(extension_runs.subprocess,'Popen',return_value=child))
            with self.assertRaisesRegex(ValueError,'UNKNOWN'):extension_runs._local(record)
        self.assertTrue(record['stop_unconfirmed']);self.assertNotIn('exit_code',record)
        self.assertEqual(child.wait.call_args.kwargs['timeout'],2)
        self.assertEqual(spawn.call_args.kwargs['env']['OPENBLAS_NUM_THREADS'],'3')

    def test_local_doctor_subprocess_overrides_inherited_blas_threads(self):
        with patch.dict(os.environ,OPENBLAS_NUM_THREADS='64'),patch.object(protocol,'inspect_package',return_value={'manifest':{'entrypoint':'cli.py'}}),patch.object(protocol,'inside',return_value=self.root/'cli.py'),patch.object(protocol.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='{}')) as invoke:
            protocol.invoke(self.root,'doctor')
        self.assertEqual(invoke.call_args.kwargs['env']['OPENBLAS_NUM_THREADS'],'1')

    def test_background_doctor_is_nonblocking_and_shared_with_actual_use(self):
        receipt={'revision':'load-test-unique','root':self.root,'manifest':{'id':'fixture'}}
        entered=threading.Event();release=threading.Event()
        def invoke(*a,**kw):entered.set();release.wait(3);return {'schema':'fpilot.extension-doctor.v2','ready':True,'missing':[]}
        try:
            with patch.object(protocol,'invoke',side_effect=invoke) as check:
                start=time.monotonic();self.assertIsNone(protocol.doctor(receipt,python=sys.executable,background=True))
                self.assertLess(time.monotonic()-start,.5);self.assertTrue(entered.wait(2))
                self.assertIsNone(protocol.doctor(receipt,python=sys.executable,background=True))
                release.set();self.assertTrue(protocol.doctor(receipt,python=sys.executable)['ready'])
                self.assertTrue(protocol.doctor(receipt,python=sys.executable)['ready']);self.assertEqual(check.call_count,1)
                protocol.doctor(receipt,python=sys.executable,refresh=True);self.assertEqual(check.call_count,2)
        finally:release.set()

    def test_context_does_not_wait_for_local_doctors(self):
        item={'id':'fixture','enabled':True,'contexts':['structure'],'stages':[],'execution':{'locations':['local']}}
        with patch.object(extension_runs,'describe_sources',return_value=[]),patch.object(protocol,'catalog',return_value=[item]),patch.object(extension_runs,'blockers',return_value=[]),patch.object(extension_runs.environments,'resolve',return_value={'python':sys.executable}),patch.object(protocol,'installed',return_value={}),patch.object(protocol,'doctor',return_value=None) as doctor:
            result=extension_runs.context({'sources':[]})
        self.assertTrue(result['extensions'][0]['environment_checking']);self.assertTrue(doctor.call_args.kwargs['background'])

    def test_analysis_slot_is_per_host_and_detached_analysis_reserves_its_host(self):
        def other_thread():
            try:
                with analysis_queue.slot('one',wait=False):return True
            except analysis_queue.Busy:return False
        with analysis_queue.slot('one'):
            with analysis_queue.slot('one',wait=False):pass  # Same-thread substeps share ownership.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:self.assertFalse(pool.submit(other_thread).result())
            with analysis_queue.slot('two',wait=False):pass
        folder=self.root/'analyses-v2'/'fixture';folder.mkdir(parents=True)
        (folder/'analysis.json').write_text(json.dumps({'id':'fixture','location':'ssh','state':'RUNNING','runtime':{'alias':'one'}}))
        with self.assertRaises(analysis_queue.Busy):
            with analysis_queue.slot('one',wait=False):pass
        with analysis_queue.slot('one',owner='fixture',wait=False):pass

    def test_streamed_plot_info_preserves_reference_and_spin_without_read_text(self):
        outcar=self.root/'OUTCAR';outcar.write_text('LSORBIT = T\nE-fermi : 1.25\nFermi energy: 1.234567\nval. band max: 1.0\ncond. band min: 2.0\n')
        with patch.object(Path,'read_text',side_effect=AssertionError('no full text allocation')):value=interactive_plots._outcar_info(outcar)
        self.assertEqual(value['reference']['fermi_eV'],1.234567);self.assertTrue(value['noncollinear'])
        self.assertEqual(value['sha256'],hashlib.sha256(outcar.read_bytes()).hexdigest())
