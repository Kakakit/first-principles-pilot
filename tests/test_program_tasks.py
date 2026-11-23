"""Custom program input, submission and output contracts in an isolated work directory."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from backend import execution_cancel as cancel, custom_commands, execution as ex, program_tasks
from test_direct_execution import DirectExecutionTests as _DirectFixture, wait_for


class ProgramTaskTests(unittest.TestCase):
    setUp = _DirectFixture.setUp
    install = _DirectFixture.install
    ssh = _DirectFixture.ssh

    def prepare(self, command="python3 main.py"):
        self.project['edges'] = []
        self.project['nodes'][0]['data'].update(kind='program', executionMode='program', stageSequence=9,
            stageSlug='program', programDraft={'alias':'lab','command':command,
            'files':[{'name':'main.py','text':'from pathlib import Path\nprint(Path("input.txt").read_text())\nPath("answer.txt").write_text("done")'},
                     {'name':'input.txt','text':'hello'}], 'outputs':['answer.txt','missing.txt','']})
        return program_tasks.prepare(self.project, 'n')

    def test_frozen_execution_and_output_evidence(self):
        record = self.prepare()
        self.project['nodes'][0]['data']['programDraft']['files'][1]['text']='changed'
        self.assertEqual(program_tasks.frozen(record)['files']['input.txt'],'hello')
        started = ex.submit(record['id'])
        self.assertEqual(started['state'], 'DISPATCHING', started.get('dispatch_receipt'))
        run_dir = Path(record['remote_path_absolute'])
        self.assertEqual((run_dir/'input.txt').read_text(), 'hello')
        if not sys.platform.startswith('linux') and not os.environ.get('FPILOT_TEST_POSIX_RUNNER'):
            result = subprocess.run([sys.executable,'main.py'], cwd=run_dir, check=True, capture_output=True,text=True)
            self.remote_state = {'state':'EXITED','exit_code':0,'process_pid':123,'protocol_version':1,'stdout':result.stdout}
        final = wait_for(lambda: ex.refresh(record['id']),lambda r:r['state']=='COMPLETED')
        self.assertEqual(custom_commands.read(record['id'],tail=True)['stdout'],'hello\n')
        self.assertEqual(final['evidence']['outputs'],{'answer.txt':True,'missing.txt':False})
        self.assertNotIn('vasp_timing',final['evidence'])
        self.assertEqual(ex.submit(record['id'])['state'],'COMPLETED')

    def test_snapshot_preserves_frozen_file_bytes(self):
        from backend import project_snapshot
        from backend.storage import WorkspaceStore
        record = self.prepare()
        WorkspaceStore(self.root).save({'projects':[self.project],'activeId':'p'},0)
        manifest, objects = project_snapshot.capture(self.root,'p')
        path = 'program-bundles/'+record['bundle_id']+'.json'
        self.assertEqual(manifest['files'][path]['sha256'],record['bundle_id'])
        self.assertEqual(objects[record['bundle_id']], (self.root/path).read_bytes())
        self.assertFalse(project_snapshot.mappable(path))

    def test_lost_receipt_does_not_relaunch(self):
        record = self.prepare()
        self.remote_receipt_lost = True
        self.assertEqual(ex.submit(record['id'])['state'],'UNKNOWN')
        self.assertEqual(ex.submit(record['id'])['state'],'UNKNOWN')
        self.assertEqual(sum('FPILOT_SUBMIT_PHASE=SCHEDULER' in cmd for cmd,_ in self.calls),1)

    def test_stop_reconciles_original_attempt(self):
        key = self.prepare('exec sleep 20')['id']
        self.assertEqual(ex.submit(key)['state'],'DISPATCHING')
        real = sys.platform.startswith('linux') or os.environ.get('FPILOT_TEST_POSIX_RUNNER')
        if not real:
            self.remote_state = dict(state='RUNNING', process_pid=123, protocol_version=1)
        wait_for(lambda: ex.refresh(key), lambda r:r['state']=='RUNNING')
        operation = cancel.preview('p',key)
        if not real:
            self.remote_state['stop'] = dict(state='ACCEPTED', operation_id=operation['operation_id'])
        self.assertEqual(cancel.cancel('p',key,operation['token'],True)['state'],'ACCEPTED')
        if not real:
            self.remote_state.update(state='CANCELLED',exit_code=-15)
        confirmed = wait_for(lambda:cancel.recover('p',key),lambda r:r['state']=='CONFIRMED')
        self.assertEqual(confirmed['attempt_state'],'CANCELLED')
        self.assertEqual(sum(' stop-direct ' in cmd for cmd,_ in self.calls),1)

    def test_pbs_and_slurm_batch_receipts(self):
        for scheduler in ('pbs','slurm'):
            with self.subTest(scheduler=scheduler):
                self.host['scheduler']=scheduler
                r=self.prepare()
                wrapper=program_tasks.frozen(r)['batch_script']
                self.assertIn('#PBS ' if scheduler=='pbs' else '#SBATCH ',wrapper)
                key=r['id']
                token='23.node01' if scheduler=='pbs' else '23;clusterA'
                with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'FPILOT_JOB_ID='+token,'stderr':''}) as send:
                    submitted=ex.submit(key)
                    self.assertEqual(submitted['state'],'SUBMITTED')
                    self.assertEqual(ex.submit(key)['job_id'],token.split(';')[0])
                    self.assertEqual(send.call_count,1)
                state='job_state = C\nexit_status = 0' if scheduler=='pbs' else 'FPILOT_SLURM_ACCOUNT=COMPLETED|0:0'
                with patch.object(ex,'ssh',side_effect=[{'exit_code':0,'stdout':state+'\nFPILOT_EXIT=0'},{'exit_code':0,'stdout':'0\n'}]):
                    self.assertEqual(ex.refresh(key)['state'],'COMPLETED')
                self.assertEqual(ex.get(key)['evidence']['scheduler_exit_code'],'0' if scheduler=='pbs' else '0:0')
                self.assertEqual(ex.get(key)['evidence']['outputs'],{'answer.txt':True,'missing.txt':False})

    def test_batch_requires_program_exit_evidence(self):
        self.host['scheduler']='pbs'
        r=self.prepare()
        ex.save({**r,'job_id':'12.node01','state':'RUNNING'})
        with patch.object(ex,'ssh',return_value={'exit_code':0,'stdout':'job_state = C\nExit_status = 0'}):
            self.assertEqual(ex.refresh(r['id'])['state'],'UNKNOWN')

    def test_invalid_names_and_server(self):
        for name in ('../input','_fpilot','input-manifest.sha256'):
            with self.assertRaises(ValueError):
                program_tasks.check_document(dict(command='true',files=[dict(name=name,text='')]))
        self.host['scheduler']='unsupported'
        with self.assertRaisesRegex(ValueError,'直接运行'):
            self.prepare()


del _DirectFixture
