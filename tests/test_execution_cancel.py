import copy
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, execution_cancel as cancel, execution_cancel_remote as adapter, scans, chain_runs, project_sync, recycle


class CancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module in (ex, recycle):
            mock = patch.object(module, 'ROOT', self.root); mock.start(); self.addCleanup(mock.stop)
        self.key = 'a'*32
        self.record = {'id': self.key, 'state': 'RUNNING', 'project_id': 'p', 'config': {'alias': 'host', 'scheduler': 'pbs'},
                       'node_id': 'n', 'job_id': '12.node01', 'updated_at': ex.now(),
                       'path_schema': 'fpilot.remote-attempt-path', 'remote_path_absolute': '/home/user/fpilot/material-projects/p/flows/main/stages/02-scf/attempts/a',
                       'remote_workspace_root_abs': '/home/user/fpilot', 'dispatch_started_at': '2026-10-13T00:00:00+00:00'}
        with closing(ex.db()) as db, db: db.execute('INSERT INTO attempts VALUES(?,?,?)', (self.key, 'RUNNING', json.dumps(self.record)))
        for module, name, result in ((ex, 'attempt_path', {'relative':'material-projects/p/flows/main/stages/02-scf/attempts/a'}),):
            mock = patch.object(module, name, return_value=result); mock.start(); self.addCleanup(mock.stop)
        self.live = {'result': 'LIVE', 'scheduler_state': 'R', 'terminal': False, 'owner': 'user'}

    def preview(self):
        with patch.object(cancel, 'remote', return_value=self.live): return cancel.preview('p', self.key)

    def test_exact_once_confirmation_and_terminal_accounting(self):
        report = self.preview()
        with patch.object(cancel, 'remote', return_value={**self.live, 'result':'ACCEPTED'}) as send:
            result = cancel.cancel('p', self.key, report['token'], True)
            self.assertEqual(result['state'], 'ACCEPTED'); self.assertEqual(ex.get(self.key)['state'], 'RUNNING')
            cancel.cancel('p', self.key, report['token'], True); send.assert_called_once()
            self.assertEqual(send.call_args.args[1], 'cancel')
        with patch.object(cancel, 'remote', return_value={'result':'TERMINAL','terminal':True,'scheduler_state':'C','exit_code':271}) as read:
            result = cancel.recover('p', self.key); self.assertEqual(result['state'],'CONFIRMED')
            self.assertEqual(ex.get(self.key)['state'],'CANCELLED'); self.assertEqual(read.call_args.args[1],'inspect')
        with patch.object(ex, 'ssh') as ssh:
            self.assertEqual(ex.refresh(self.key)['state'], 'CANCELLED'); ssh.assert_not_called()
        record = ex.get(self.key); record['state']='PREPARED'; self.assertEqual(ex.save(record)['state'],'CANCELLED')
        # CANCELLED records stay immutable; explicit new attempts are tested in task recovery.

    def test_network_unknown_never_resends_and_keeps_quota(self):
        report = self.preview()
        with patch.object(cancel, 'remote', side_effect=ConnectionError('lost reply')) as send:
            result = cancel.cancel('p',self.key,report['token'],True)
            self.assertEqual(result['state'],'UNKNOWN'); self.assertEqual(ex.get(self.key)['state'],'RUNNING')
            cancel.cancel('p',self.key,report['token'],True); send.assert_called_once()
        with patch.object(cancel,'remote',return_value=self.live) as read:
            cancel.preview('p',self.key); self.assertEqual(read.call_args.args[1],'inspect')
            self.assertEqual(ex.get(self.key)['cancellation']['state'],'UNKNOWN')

    def test_cancel_transport_is_mutating_and_inspection_read_only(self):
        receipt={'exit_code':0,'stdout':'FPILOT_CANCEL='+json.dumps(self.live),'stderr':''}
        with patch('backend.remote_transport._interpreter',return_value='/usr/bin/python3'),patch.object(ex,'ssh',return_value=receipt) as ssh:
            cancel.remote(self.record,'cancel');self.assertIs(ssh.call_args.kwargs['mutating'],True)
            cancel.remote(self.record,'inspect');self.assertIs(ssh.call_args.kwargs['mutating'],False)
        with patch('backend.remote_transport._interpreter',return_value='/usr/bin/python3'),patch.object(ex,'ssh',return_value={'exit_code':None,'stdout':'','transport_state':'NOT_SENT','stderr':'not sent'}):
            self.assertEqual(cancel.remote(self.record,'cancel')['result'],'NOT_SENT')

    def test_missing_directory_fallback_is_only_bound_accepted_read_only_recovery(self):
        import shlex
        receipt={'exit_code':0,'stdout':'FPILOT_CANCEL='+json.dumps(self.live),'stderr':''}
        self.record['cancellation']={'state':'UNKNOWN','accepted_at':ex.now(),'binding':cancel.binding(self.record),'facts':{'owner':'user'}}
        with patch('backend.remote_transport._interpreter',return_value='/usr/bin/python3'),patch.object(ex,'ssh',return_value=receipt) as ssh:
            def payload():return json.loads(shlex.split(ssh.call_args.args[1])[-1])
            cancel.remote(self.record,'inspect');self.assertTrue(payload()['allow_missing_identity']);self.assertEqual(payload()['verified_owner'],'user')
            cancel.remote(self.record,'cancel');self.assertFalse(payload()['allow_missing_identity'])
            self.record['cancellation']['binding']['job_id']='another'
            cancel.remote(self.record,'inspect');self.assertFalse(payload()['allow_missing_identity'])

    def test_no_identity_guessing_wrong_project_token_expiry_and_changes(self):
        with self.assertRaises(ValueError): cancel.preview('other', self.key)
        report=self.preview()
        with self.assertRaises(ValueError): cancel.cancel('p',self.key,report['token'],False)
        with self.assertRaises(ValueError): cancel.cancel('p',self.key,'wrong',True)
        record=ex.get(self.key); record['cancellation']['expires_at']='2020-01-01T00:00:00+00:00'; ex.save(record)
        with self.assertRaisesRegex(ValueError,'过期'): cancel.cancel('p',self.key,report['token'],True)
        report=self.preview(); record=ex.get(self.key); record['job_id']='13.node01'; ex.save(record)
        with self.assertRaisesRegex(ValueError,'变化'): cancel.cancel('p',self.key,report['token'],True)
        record=ex.get(self.key);record.update(state='UNKNOWN',job_id=None);ex.save(record)
        with self.assertRaisesRegex(ValueError,'job ID'): cancel.preview('p',self.key)

    def test_local_prepared_and_pending_case_no_remote_action(self):
        record=ex.get(self.key);record.update(state='PREPARED',job_id=None);record.pop('dispatch_started_at');ex.save(record)
        with patch.object(cancel,'remote') as remote:
            report=cancel.preview('p',self.key); cancel.cancel('p',self.key,report['token'],True);remote.assert_not_called()
        batch=scans.save({'id':'b','project_id':'p','state':'ACTIVE','authorization':{'at':'kept'},'cases':[
            {'id':'case','state':'PENDING','attempt_id':None},{'id':'other','state':'RUNNING','attempt_id':self.key}]})
        result=cancel.cancel_case('p','b','case',True)
        self.assertEqual(result['state'],'ACTIVE');self.assertEqual(result['cases'][0]['state'],'CANCELLED')
        self.assertEqual(result['cases'][1],batch['cases'][1]);self.assertEqual(result['authorization'],batch['authorization'])
        cancel.cancel_case('p','b','case',True)
        with self.assertRaises(ValueError): cancel.cancel_case('p','b','other',True)

    def test_completion_race_preserves_state_and_cancel_receipt(self):
        report=self.preview()
        def finish(record, action):
            current=ex.get(self.key);current['state']='COMPLETED';ex.save(current)
            return {**self.live,'result':'ACCEPTED'}
        with patch.object(cancel,'remote',side_effect=finish):cancel.cancel('p',self.key,report['token'],True)
        self.assertEqual(ex.get(self.key)['state'],'COMPLETED')
        with patch.object(cancel,'remote') as read:
            self.assertEqual(cancel.recover('p',self.key)['state'],'FINISHED');read.assert_not_called()

    def test_cancel_preserves_unrelated_dispatch_intent(self):
        scans.save({'id':'b','project_id':'p','state':'ACTIVE','authorization':{'at':'kept'},'cases':[{'id':'case','state':'RUNNING','attempt_id':self.key}]})
        run=chain_runs.save({'id':'c','revision':0,'project_id':'p','state':'ACTIVE','authorization':{'at':'kept'},'steps':[{'node_id':'n','state':'RUNNING','attempt_id':self.key}]})
        report=self.preview()
        with patch.object(cancel,'remote',return_value={**self.live,'result':'ACCEPTED'}):cancel.cancel('p',self.key,report['token'],True)
        self.assertEqual(scans.get('b')['state'],'ACTIVE');self.assertEqual(chain_runs.get('c')['state'],'ACTIVE')
        self.assertEqual(chain_runs.get('c')['authorization'],run['authorization'])
        with scans._dispatch_lock('b'):
            with self.assertRaisesRegex(ValueError,'正在派发'): cancel.preview('p',self.key)

    def test_natural_completion_and_unconfirmed_failure_stay_distinct(self):
        report=self.preview()
        with patch.object(cancel,'remote',side_effect=ConnectionError('before command')):cancel.cancel('p',self.key,report['token'],True)
        with patch.object(cancel,'remote',return_value={'result':'TERMINAL','terminal':True,'scheduler_state':'C','exit_code':271}),patch.object(ex,'refresh',return_value=ex.get(self.key)):
            cancel.recover('p',self.key)
        self.assertNotEqual(ex.get(self.key)['state'],'CANCELLED')
        self.assertEqual(ex.get(self.key)['cancellation']['state'],'FINISHED')


class SchedulerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();(self.root/'_fpilot').mkdir()
        (self.root/'_fpilot/attempt.json').write_text(json.dumps({'id':'a'*32}))
        (self.root/'.job-id').write_text('12.node01\n')
        self.payload={'root':str(self.root),'attempt_id':'a'*32,'job_id':'12.node01','scheduler':'pbs','action':'cancel'}
        self.row='Job Id: 12.node01\n Job_Owner = user@host\n job_state = R\n'

    def test_adapter_issues_only_exact_job_and_no_force(self):
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',side_effect=[(0,self.row,''),(0,'','')]) as command:
            result=adapter.run(self.payload);self.assertEqual(result['result'],'ACCEPTED')
            self.assertEqual(command.call_args_list[-1].args[0],['qdel','12.node01'])
        self.payload.update(job_id='12',scheduler='slurm');(self.root/'.job-id').write_text('12')
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',side_effect=[(0,'JobId=12 UserId=user(1000) JobState=RUNNING ExitCode=0:0',''),(0,'','')]) as command:
            self.assertEqual(adapter.run(self.payload)['result'],'ACCEPTED');self.assertEqual(command.call_args_list[-1].args[0],['scancel','12'])

    def test_owner_marker_and_missing_queue_not_terminal(self):
        with patch.object(adapter.getpass,'getuser',return_value='other'),patch.object(adapter,'command',return_value=(0,self.row,'')) as cmd:
            with self.assertRaises(ValueError):adapter.run(self.payload)
            self.assertEqual(cmd.call_count,1)
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',return_value=(1,'','Unknown Job Id')) as cmd:
            with self.assertRaises(ValueError):adapter.run(self.payload)
            self.assertFalse(any(c.args[0][0]=='qdel' for c in cmd.call_args_list))
        (self.root/'.job-id').write_text('13.node01')
        with patch.object(adapter,'command') as cmd:
            with self.assertRaises(ValueError):adapter.run(self.payload)
            cmd.assert_not_called()

    def test_terminal_and_read_only_recovery_never_cancel(self):
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',return_value=(0,self.row.replace('= R','= C')+' Exit_status = 271\n','')) as cmd:
            self.assertEqual(adapter.run(self.payload)['result'],'TERMINAL');self.assertEqual(cmd.call_count,1)
        self.payload['action']='inspect'
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',return_value=(0,self.row,'')) as cmd:
            self.assertEqual(adapter.run(self.payload)['result'],'LIVE');self.assertEqual(cmd.call_count,1)

    def test_removed_directory_after_accepted_cancel_recovers_exact_accounting(self):
        (self.root/'_fpilot/attempt.json').unlink()
        self.payload.update(action='inspect',allow_missing_identity=True,verified_owner='user')
        history='Job: 12.node01\nExit_status=271\ndequeuing from batch, state COMPLETE\n'
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',side_effect=[(1,'','Unknown Job Id'),(1,'',''),(0,history,'')]) as cmd:
            result=adapter.run(self.payload)
            self.assertTrue(result['terminal']);self.assertEqual(result['exit_code'],271)
            self.assertEqual(result['identity_source'],'accepted_cancellation_and_accounting')
            self.assertFalse(any(c.args[0][0] in ('qdel','scancel') for c in cmd.call_args_list))
        for history in ('Job: 99.node01\nExit_status=271\nstate COMPLETE\n','Job: 12.node01\n',
                        'Job: 12.node01\nJob_Owner=other@host\nExit_status=271\nstate COMPLETE\n'):
            with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command',side_effect=[(1,'',''),(1,'',''),(0,history,'')]):
                with self.assertRaises(ValueError):adapter.run(self.payload)

    def test_missing_identity_never_authorizes_cancel_and_mismatched_marker_still_blocks(self):
        (self.root/'_fpilot/attempt.json').unlink()
        self.payload.update(allow_missing_identity=True,verified_owner='user')
        with patch.object(adapter,'command') as command:
            with self.assertRaises(FileNotFoundError):adapter.run(self.payload)
            command.assert_not_called()
        self.payload['action']='inspect';self.payload['verified_owner']='other'
        with patch.object(adapter.getpass,'getuser',return_value='user'),patch.object(adapter,'command') as command:
            with self.assertRaisesRegex(ValueError,'所有者'):adapter.run(self.payload)
            command.assert_not_called()
        (self.root/'_fpilot/attempt.json').write_text(json.dumps({'id':'wrong'}))
        self.payload['verified_owner']='user'
        with patch.object(adapter,'command') as command:
            with self.assertRaisesRegex(ValueError,'记录不符'):adapter.run(self.payload)
            command.assert_not_called()
