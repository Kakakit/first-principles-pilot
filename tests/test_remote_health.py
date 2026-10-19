import concurrent.futures
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from backend import remote_health as health,remote_transport as transport,remote_worker,remote_monitor,task_summary


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(health,'ROOT',Path(self.temp.name));self.root.start()
        self.clock=patch.object(health.time,'time',return_value=1000);self.now=self.clock.start()
    def tearDown(self):
        transport.close_all();self.clock.stop();self.root.stop();self.temp.cleanup()
    def fail_first(self):
        health.finish(health.claim('test',()),'I/O timeout','stat /same/path')
    def test_five_spaced_retries_then_pause_persist_and_manual_resume(self):
        self.fail_first()
        for index in range(1,6):
            self.now.return_value=1000+index*900-1
            with self.assertRaises(health.Deferred):health.claim('test',(),retry=True)
            self.now.return_value+=1
            ticket=health.claim('test',(),retry=True)
            health.finish(ticket,'still offline')
            self.assertEqual(health.status('test')[0]['retry_count'],index)
        self.assertEqual(health.due(),[])
        self.assertEqual(health.status('test')[0]['state'],'PAUSED')
        self.assertEqual(health.probe_command('test',()),'printf FPILOT_CONNECTION_OK')
        with self.assertRaises(health.Deferred):health.claim('test',(),retry=True)
        # A second process sees the same persisted pause; no SSH is involved.
        code='from backend import remote_health as h;from pathlib import Path;h.ROOT=Path(__import__("sys").argv[1]);print(h.status("test")[0]["state"])'
        value=subprocess.run([sys.executable,'-B','-c',code,self.temp.name],capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'PAUSED')
        ticket=health.claim('test',(),retry='manual');health.finish(ticket)
        self.assertEqual(health.status('test')[0]['state'],'READY')
        self.assertEqual(health.status('test')[0]['retry_count'],0)
    def test_many_page_requests_do_not_consume_retries_or_push_deadline(self):
        self.fail_first();before=health.status('test')[0]
        for _ in range(10):
            with self.assertRaises(health.Deferred):health.claim('test',())
        after=health.status('test')[0]
        self.assertEqual(after['retry_count'],0);self.assertEqual(after['next_retry_at'],before['next_retry_at'])
    def test_only_one_process_may_probe_and_healthy_host_is_independent(self):
        self.fail_first();self.now.return_value+=900
        first=health.claim('test',(),retry=True)
        with self.assertRaises(health.Deferred):health.claim('test',(),retry=True)
        healthy=health.claim('other',());health.finish(healthy)
        health.finish(first,'offline')
        self.assertEqual(health.status('other')[0]['state'],'READY')
        self.assertEqual(health.status('test')[0]['retry_count'],1)
    def test_manual_failure_does_not_restart_automatic_budget(self):
        self.fail_first();health.finish(health.claim('test',(),retry='manual'),'offline')
        self.assertEqual(health.status('test')[0]['retry_count'],0)
        self.assertEqual(health.status('test')[0]['state'],'PAUSED')
        self.assertIsNone(health.status('test')[0]['next_retry_at'])
    def test_expired_queue_item_cannot_execute_later(self):
        session=transport.Session('test','normal');future=concurrent.futures.Future()
        item={'job':{'timeout':1},'future':future,'scope':None,'mutating':True,'queued':time.monotonic(),
              'deadline':time.monotonic()-1,'expired':False,'send_started':False}
        try:
            with patch('backend.site_registration.connection_identity',return_value={}),patch.object(session,'_request') as request:
                session.jobs.put(item)
                with self.assertRaises(transport.RequestFailure) as error:future.result(timeout=2)
                self.assertFalse(error.exception.started);self.assertIn('过期',str(error.exception));request.assert_not_called()
        finally:session.closed=True;session.close()

    def test_changed_connection_supersedes_old_cooldown_without_erasing_it(self):
        self.fail_first();self.now.return_value+=900
        health.finish(health.claim('test',(('host','new'),)))
        self.assertEqual(health.due(),[])
        self.assertEqual(len(health.status('test')),1)
        self.assertEqual(health.status('test')[0]['state'],'READY')
        self.assertEqual(len(health._read()),2)

    def test_fifth_claim_crash_still_exhausts_budget(self):
        self.fail_first()
        for _ in range(5):
            self.now.return_value+=900
            health.claim('test',(),retry=True)
        self.now.return_value+=900
        self.assertEqual(health.due(),[])
        self.assertEqual(health.status('test')[0]['state'],'PAUSED')

    def test_queued_connection_change_is_not_sent(self):
        session=transport.Session('test','normal');future=concurrent.futures.Future()
        item={'job':{'timeout':1},'future':future,'scope':None,'mutating':True,'queued':time.monotonic(),
              'deadline':time.monotonic()+5,'expired':False,'send_started':False,'connection':(('host','old'),)}
        try:
            with patch('backend.site_registration.connection_identity',return_value={'host':'new'}),patch.object(session,'_request') as request:
                session.jobs.put(item)
                with self.assertRaises(transport.RequestFailure) as error:future.result(timeout=2)
                self.assertFalse(error.exception.started);request.assert_not_called()
        finally:session.closed=True;session.close()

    def test_timeout_aborts_only_the_matching_active_request(self):
        session=transport.Session('test','normal');item={};other={}
        process=Mock();process.poll.return_value=None
        try:
            session.process=process;session.active_group=[other]
            session.abort(item);process.terminate.assert_not_called()
            session.active_group=[item];session.abort(item)
            process.terminate.assert_called_once()
        finally:session.process=None;session.closed=True;session.close()


class BoundedOutputTests(unittest.TestCase):
    def test_argv_streaming_keeps_binary_out_of_receipts_and_reports_local_failure(self):
        import base64,hashlib
        digest=hashlib.sha256()
        job={'argv':[sys.executable,'-c','import os;os.write(1,b"x"*1048576)'],'stdout_limit':0,'timeout':10}
        result=remote_worker.execute(job,on_output=digest.update)
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['stdout'],'')
        self.assertEqual(result['received_bytes'],1048576)
        self.assertEqual(digest.digest(),hashlib.sha256(b'x'*1048576).digest())
        def failed(block):raise ValueError('disk full')
        result=remote_worker.execute(job,on_output=failed)
        self.assertEqual(result['exit_code'],1)
        self.assertIn('disk full',base64.b64decode(result['stderr']).decode())

    def test_legacy_plot_download_obeys_shared_transport_and_atomic_file_validation(self):
        from backend import interactive_plots as plots
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'OUTCAR';data=b'plot bytes';digest=hashlib.sha256(data).hexdigest()
            source={'host':'test','remote_dir':'/same/path','name':'OUTCAR','size_bytes':len(data)}
            job={'downloaded_bytes':0}
            def transfer(argv,**kwargs):
                self.assertEqual(kwargs['lane'],'analysis');self.assertEqual(kwargs['stdout_limit'],0)
                kwargs['on_output'](data)
                return {'exit_code':0,'stderr':''}
            with patch.object(plots,'_remote_digest',return_value=digest),patch.object(transport,'legacy',side_effect=transfer) as wire:
                plots._download(source,target,job)
                self.assertEqual(target.read_bytes(),data);self.assertEqual(job['downloaded_bytes'],len(data))
                plots._download(source,target,job);self.assertEqual(wire.call_count,1)

    @staticmethod
    def binary_request(data,kind='image/png'):
        from starlette.requests import Request
        async def receive():return {'type':'http.request','body':data,'more_body':False}
        return Request({'type':'http','method':'POST','headers':[(b'content-type',kind.encode())]},receive)

    def test_binary_export_api_streams_file_and_retains_metadata(self):
        import asyncio
        from backend import app as api
        metadata={'cache_id':'cache','project_id':'project','selected':[],'emin':-4,'emax':4,'title':'图'}
        seen=[]
        def save(*args,**kwargs):
            seen.append((args,kwargs['png_file'].read_bytes()))
            return {'id':'saved'}
        with patch.object(api.interactive_plots,'save',side_effect=save):
            result=asyncio.run(api.interactive_plot_save_binary(self.binary_request(b'png bytes'),json.dumps(metadata)))
        self.assertEqual(result['id'],'saved')
        self.assertEqual(seen[0][1],b'png bytes')
        self.assertEqual(seen[0][0][7],'图')

    def test_binary_export_rejects_wrong_type_metadata_and_oversize_before_save(self):
        import asyncio
        from fastapi import HTTPException
        from backend import app as api
        metadata=json.dumps({'cache_id':'cache','project_id':'project','selected':[],'emin':-4,'emax':4})
        with patch.object(api.interactive_plots,'save') as save:
            for data,meta,kind in [(b'x',metadata,'text/plain'),(b'x','[]','image/png'),(b'x'*15_000_001,metadata,'image/png')]:
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(api.interactive_plot_save_binary(self.binary_request(data,kind),meta))
                self.assertEqual(error.exception.status_code,422)
            save.assert_not_called()

    def test_large_output_is_counted_but_only_tail_is_retained(self):
        import base64,shlex
        command=shlex.quote(sys.executable)+" -c 'import os;os.write(1,b\"x\"*16777216)'"
        receipt=remote_worker.execute({'command':command,'stdout_limit':256,'timeout':10})
        self.assertEqual(receipt['exit_code'],0)
        self.assertEqual(receipt['received_bytes'],16777216)
        self.assertEqual(len(base64.b64decode(receipt['stdout'])),256)
        self.assertTrue(receipt['stdout_truncated'])
    def test_scheduler_batch_does_not_read_output_files(self):
        records=[{'id':key,'job_id':key+'.node','state':'RUNNING','config':{'alias':'test','scheduler':'pbs'}} for key in ('11','12')]
        receipt={'exit_code':0,'stdout':'Job Id: 11.node\n job_state = R\nJob Id: 12.node\n job_state = Q\n','stderr':''}
        with patch.object(remote_monitor.ex,'ssh',return_value=receipt) as ssh:
            result=remote_monitor.batch(records)
        self.assertEqual(ssh.call_count,1)
        self.assertEqual(ssh.call_args.args[1].count('qstat '),1)
        self.assertNotIn('OUTCAR',ssh.call_args.args[1]);self.assertNotIn('OSZICAR',ssh.call_args.args[1])
        self.assertEqual(remote_monitor.active_state(records[0],result['11']),'RUNNING')
        self.assertEqual(remote_monitor.active_state(records[1],result['12']),'QUEUED')
    def test_summary_paging_omits_large_private_details_and_recovery_retains_progress(self):
        rows=[{'id':str(i),'state':'RUNNING','band_attempt_id':'band','config':{'alias':'test','script':'secret'},'script':'x'*10000,'input_audit':{'raw':'y'*10000}} for i in range(113)]
        with patch.object(task_summary.execution,'history',return_value=rows):
            first=task_summary.page('project');second=task_summary.page('project',cursor=first['next_cursor'])
        self.assertEqual(len(first['items']),50);self.assertEqual(first['total'],113)
        self.assertFalse(set(r['id'] for r in first['items']) & set(r['id'] for r in second['items']))
        self.assertNotIn('script',json.dumps(first))
        self.assertEqual(first['items'][0]['band_attempt_id'],'band')
        summary=task_summary.recovery({'id':'op','state':'CONFIRMED','items':[{'id':'a','state':'PREPARED','progress':{'phase':'SUBMITTING'},'script':'huge'}]})
        self.assertEqual(summary['items'][0]['progress']['phase'],'SUBMITTING');self.assertNotIn('script',summary['items'][0])
