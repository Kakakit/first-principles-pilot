import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, execution_control as control, worker


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(ex,'ROOT',self.root);self.patch.start()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def record(self,key='a',state='RUNNING',alias='jutai-1'):
        r=dict(id=key,state=state,config={'alias':alias},profile={'ppn':48},node='node01',cores=None,reason='',
               bundle_id='b',script='script',script_sha256='x',input_audit={},job_id='123.node01',
               path_schema='fpilot.remote-attempt-path',remote_workspace_root='$HOME/230269265lht',
               remote_workspace_root_abs='/home/user/230269265lht',
               remote_relative_path='material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-'+key,
               remote_path_absolute='/home/user/230269265lht/material-projects/test/flows/main/stages/03-scf/attempts/20260921-120000-'+key,
               remote_project_path_absolute='/home/user/230269265lht/material-projects/test',project_id='test',project_slug='test',
               dispatch_started_at='2020-01-01T00:00:00+00:00')
        with closing(ex.db()) as c:
            c.execute('INSERT INTO attempts VALUES(?,?,?)',(key,state,json.dumps(r)));c.commit()
        return r

    def test_disconnect_unknown_then_reconnect_exact_job_without_submission(self):
        self.record()
        with patch.object(ex,'submit') as submit,patch.object(ex,'ssh',return_value={'exit_code':255,'stdout':'','stderr':'offline'}):
            self.assertEqual(worker.tick()['checked'],1)
            self.assertEqual(ex.get('a')['state'],'UNKNOWN');submit.assert_not_called()
        with patch.object(ex,'submit') as submit,patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_EDIFF=1\njob_state = C\n','stderr':''}) as ssh:
            worker.tick();self.assertEqual(ex.get('a')['state'],'COMPLETED')
            self.assertIn('qstat -f 123.node01',ssh.call_args.args[1]);submit.assert_not_called()
        with patch.object(ex,'ssh') as ssh:worker.tick();ssh.assert_not_called()

    def test_orphan_dispatch_recovers_but_recent_dispatch_is_not_raced(self):
        old=self.record('old','DISPATCHING');old['job_id']=None;ex.save(old)
        recent=self.record('recent','DISPATCHING');recent['dispatch_started_at']=ex.now();ex.save(recent)
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_JOB_ID=555.node01\n','stderr':''}) as ssh:
            self.assertEqual(worker.tick()['checked'],1)
            self.assertEqual(ex.get('old')['job_id'],'555.node01')
            self.assertEqual(ex.get('recent')['state'],'DISPATCHING')
            self.assertEqual(ssh.call_count,1)

    def test_unknown_counts_in_limits_and_prepared_does_not(self):
        self.record('a','UNKNOWN');target=self.record('b','PREPARED')
        with closing(ex.db()) as c:
            with self.assertRaises(ValueError):control.enforce_limits(c,target)
        target['config']['alias']='jutai-5'
        with closing(ex.db()) as c:control.enforce_limits(c,target)
        control.save_settings({'max_inflight_total':2,'max_inflight_per_host':2,'max_cores_per_host':47})
        with closing(ex.db()) as c:
            with self.assertRaises(ValueError):control.enforce_limits(c,target)

    def test_slurm_ntasks_counts_toward_core_limit(self):
        target=self.record('slurm','PREPARED','gengzi-187');target['profile']={'ntasks':4}
        control.save_settings({'max_inflight_total':2,'max_inflight_per_host':2,'max_cores_per_host':3})
        with closing(ex.db()) as c:
            with self.assertRaisesRegex(ValueError,'核数预算'):control.enforce_limits(c,target)

    def test_limit_blocks_submit_before_upload_and_preserves_prepared(self):
        self.record('a','UNKNOWN');target=self.record('b','PREPARED')
        with patch.object(ex.servers,'probe',return_value={'id':'new'}),patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':target['profile']}),patch.object(ex,'bundle',return_value=({},{})),patch.object(ex,'ssh') as ssh:
            with self.assertRaisesRegex(ValueError,'在途'):ex.submit('b')
            ssh.assert_not_called();self.assertEqual(ex.get('b')['state'],'PREPARED')

    def test_retry_only_failed_and_preserves_parent(self):
        for state in ('UNKNOWN','COMPLETED','DISPATCHING','RUNNING'):
            self.record(state,state)
            with patch.object(ex,'prepare') as prepare:
                with self.assertRaises(ValueError):ex.retry(state,'p','node01')
                prepare.assert_not_called()
        parent=self.record('failed','FAILED')
        with patch.object(ex,'prepare',return_value={'id':'new','state':'PREPARED'}) as prepare:
            self.assertEqual(ex.retry('failed','p','node02')['id'],'new')
            self.assertEqual(prepare.call_args.kwargs['retry_of'],'failed')
        self.assertEqual(ex.get('failed'),parent)

    def test_failure_receipt_redacted_and_immutable(self):
        self.record()
        out='FPILOT_EXIT=1\nVariable_List = password=hidden\n\tSECRET=other\n    exit_status = 1\n'
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':out,'stderr':'token=secret-value\n'+'x'*20000}):worker.tick()
        receipt=self.root/'failure-snapshots/a.json';before=receipt.read_bytes()
        data=control.snapshot('a');self.assertFalse(data['includes_potcar'])
        raw=json.dumps(data);self.assertNotIn('hidden',raw);self.assertNotIn('secret-value',raw)
        self.assertLessEqual(len(data['logs']['status_receipt']['stderr']),8192)
        self.assertEqual(data['analysis']['confirmed_cause'],'')
        control.freeze_failure('a');self.assertEqual(before,receipt.read_bytes())

    def test_notes_keep_hypothesis_separate_from_confirmed_cause(self):
        before=self.record('a','FAILED')
        note={'user_note':'测试记录','suspected_cause':'可能 MPI 不匹配','confirmed_cause':'','confirmation_evidence':''}
        control.save_notes('a',note)
        self.assertEqual(control.snapshot('a')['analysis'],note)
        note['confirmed_cause']='MPI 不匹配'
        with self.assertRaises(ValueError):control.save_notes('a',note)
        self.assertEqual(ex.get('a'),before)

    def test_old_refresh_cannot_replace_newer_status(self):
        old=self.record();old['updated_at']='new';ex.save(old)
        stale=dict(old,state='UNKNOWN',updated_at='later')
        result=ex.save(stale,expected_updated_at=None)
        self.assertEqual(result['state'],'RUNNING')

    def test_template_prepare_freezes_script_and_retry_gets_new_path(self):
        config=ex.servers.select_profile('jutai-1')
        config['remote_workspace_root']='$HOME/230269265lht'
        profile=ex.servers.profile(config,'node01',48)
        script=ex.servers.pbs.render_script(profile)
        rendered={'resource_ready':True,'profile':profile,'script':script}
        files={name:b'test\n' for name in ('INCAR','POSCAR','KPOINTS','POTCAR')}
        manifest={'stage':'scf','node_id':'scf','project_id':'test'}
        root_receipt={'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/user/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        project={'id':'test','name':'test','remoteSlug':'test','nodes':[{'id':'scf','data':{'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}}]}
        with patch.object(ex,'prepare',ex.prepare.__wrapped__),patch.object(ex.job_templates,'ROOT',self.root),patch.object(ex.servers,'preview',side_effect=lambda *args:dict(rendered)),patch.object(ex,'bundle',return_value=(manifest,files)),patch.object(ex,'frozen_project',return_value=project),patch.object(ex.servers.pbs,'audit_inputs',return_value={'status':'PASS'}),patch.object(ex,'ssh',return_value=root_receipt):
            template=ex.job_templates.import_template(script,'jutai-1','vasp_std','test')
            parent=ex.prepare('b',config,'p','node01',template_id=template['id'])
            self.assertEqual(parent['template_snapshot']['rendered_text'],script)
            parent.update(state='FAILED');ex.save(parent)
            original=ex.get(parent['id'])
            child=ex.retry(parent['id'],'p','node01')
            self.assertEqual(child['retry_of'],parent['id'])
            self.assertNotEqual(child['remote_path_absolute'],parent['remote_path_absolute'])
            self.assertEqual(ex.get(parent['id']),original)
            child['script']+='echo changed\n';ex.save(child)
            with patch.object(ex.servers,'probe',return_value={'id':'p'}),patch.object(ex,'ssh') as ssh:
                with self.assertRaisesRegex(ValueError,'冻结模板脚本'):ex.submit(child['id'])
                ssh.assert_not_called()

    def test_worker_reads_more_than_history_page(self):
        for i in range(105):self.record(str(i))
        with patch.object(ex,'refresh',side_effect=lambda key:ex.get(key)) as refresh:
            self.assertEqual(worker.tick()['checked'],105);self.assertEqual(refresh.call_count,105)

    def test_real_worker_restart_and_singleton_use_isolated_fake_ssh(self):
        self.record()
        shim=self.root/'bin';shim.mkdir();mode=self.root/'mode';mode.write_text('offline')
        ssh=shim/'ssh'
        ssh.write_text('#!'+sys.executable+'\nimport os,pathlib,sys\nr=pathlib.Path(os.environ["FPILOT_WORKSPACE"])\nwith (r/"ssh-calls").open("a") as f:f.write(" ".join(sys.argv)+"\\n")\nif "-G" in sys.argv:print("hostname test.invalid\\nuser test\\nport 22");sys.exit(0)\nif (r/"mode").read_text()=="offline":sys.exit(255)\nprint("FPILOT_EXIT=0\\nFPILOT_TIMING=1\\njob_state = C")\n')
        ssh.chmod(0o700)
        env=dict(os.environ,FPILOT_WORKSPACE=str(self.root),FPILOT_NOTIFICATIONS='0',FPILOT_SSH_MODE='legacy',PATH=str(shim)+os.pathsep+os.environ.get('PATH',''))
        cwd=Path(__file__).resolve().parents[1]
        def wait_for(predicate):
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if predicate():return
                time.sleep(.05)
            self.fail('worker did not reach expected state')
        first=subprocess.Popen([sys.executable,'-m','backend.worker','--interval','30'],cwd=cwd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            wait_for(lambda:ex.get('a')['state']=='UNKNOWN')
            second=subprocess.run([sys.executable,'-m','backend.worker','--once'],cwd=cwd,env=env,capture_output=True,timeout=8)
            self.assertEqual(second.returncode,0)
            self.assertEqual((self.root/'ssh-calls').read_text().count('BatchMode=yes'),1)
            first.kill();first.wait(timeout=5)
            mode.write_text('online')
            resumed=subprocess.run([sys.executable,'-m','backend.worker','--once'],cwd=cwd,env=env,capture_output=True,timeout=8)
            self.assertEqual(resumed.returncode,0,resumed.stderr.decode())
            self.assertEqual(ex.get('a')['state'],'COMPLETED')
            calls=(self.root/'ssh-calls').read_text();self.assertNotIn('qsub',calls);self.assertIn('qstat -f 123.node01',calls)
        finally:
            if first.poll() is None:first.kill();first.wait(timeout=5)
            first.stderr.close()

if __name__=='__main__':unittest.main()
