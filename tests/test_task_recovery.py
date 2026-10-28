import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_scans as fixtures
from backend import task_recovery as recovery, execution as ex, scans, execution_control as control, inputs, servers, source_contracts


class TaskRecoveryTests(unittest.TestCase):
    setUp=fixtures.ScanTests.setUp
    tearDown=fixtures.ScanTests.tearDown

    def batch(self,state='PAUSED'):
        b=scans.freeze(self.project,'scan','c',[{'kind':'incar','tag':'ENCUT','start':'400','end':'450','step':'50'}],'')
        b.update(state=state,authorization={'config':{'alias':'jutai-1','scheduler':'pbs'},'node':'node01','cores':48,'max_parallel':2,'template_id':None})
        return scans.save(b)

    def record(self,b,index,state='COMPLETED'):
        c=b['cases'][index];key=('%032x'%(index+1))
        r={'id':key,'state':state,'bundle_id':c['bundle_id'],'project_id':'p','node_id':'c','scan_batch_id':b['id'],'scan_case_id':c['id'],
           'execution_scope':'scan','config':b['authorization']['config'],'node':'node01','cores':48,'profile':{'ppn':48},'job_id':None,'created_at':ex.now()}
        with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(key,state,json.dumps(r)))
        c.update(attempt_id=key,state=state);scans.save(b)
        return r

    def mocks(self,submit=None):
        stack=ExitStack()
        stack.enter_context(patch.object(servers,'validate',side_effect=lambda c:c))
        stack.enter_context(patch.object(servers,'capacity_probe',return_value={'id':'probe'}))
        stack.enter_context(patch.object(servers,'preview_for_prepare',return_value={'resource_ready':True,'blockers':[],'script':'true','profile':{'ppn':48}}))
        def prepare(bundle,config,probe,node,cores,reason,template,**kw):
            r={'id':kw['attempt_id'],'state':'PREPARED','bundle_id':bundle,'project_id':'p','node_id':'c','config':config,
               'node':node,'cores':cores,'profile':{'ppn':48},'created_at':ex.now(),'execution_scope':'scan'}
            with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(r['id'],r['state'],json.dumps(r)))
            return r
        def send(key):
            r=ex.get(key);r.update(state='SUBMITTED',job_id='123.node01');return ex.save(r)
        prep=stack.enter_context(patch.object(ex,'prepare',side_effect=prepare))
        sender=stack.enter_context(patch.object(ex,'submit',side_effect=submit or send))
        return stack,prep,sender

    def test_single_completed_case_from_paused_batch_and_duplicate_confirmation(self):
        b=self.batch();old=self.record(b,0)
        stack,prep,send=self.mocks()
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            self.assertEqual(prep.call_count,0)
            result=recovery.confirm(op['id'],'p');recovery.confirm(op['id'],'p');recovery.tick()
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['state'],'FINISHED')
        after=scans.get(b['id']);self.assertEqual(after['state'],'PAUSED')
        self.assertIsNone(after['cases'][1]['attempt_id'])
        self.assertEqual(ex.get(old['id'])['state'],'COMPLETED')
        self.assertIn(old['id'],after['cases'][0]['attempt_history'])

    def test_remaining_skips_completed_and_keeps_going_after_failure(self):
        b=self.batch();self.record(b,0,'FAILED')
        def send(key):
            rec=ex.get(key)
            if rec['scan_case_id']==b['cases'][0]['id']:raise ValueError('script rejected')
            rec.update(state='SUBMITTED',job_id='456.node01');return ex.save(rec)
        stack,prep,sender=self.mocks(send)
        with stack:
            op=recovery.preview('p','scans',b['id'],scope='remaining')
            result=recovery.confirm(op['id'],'p');recovery.tick()
        self.assertEqual(sender.call_count,2)
        self.assertEqual([i['dispatch_state'] for i in result['items']],['FAILED','SENT'])
        self.assertEqual(result['state'],'FINISHED')

    def test_unknown_requires_explicit_ack_keeps_old_state_and_releases_quota(self):
        b=self.batch();old=self.record(b,0,'UNKNOWN');b['authorization']['max_parallel']=1;scans.save(b)
        stack,prep,send=self.mocks()
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            with self.assertRaisesRegex(ValueError,'重复'):recovery.confirm(op['id'],'p')
            self.assertEqual(send.call_count,0)
            result=recovery.confirm(op['id'],'p',True)
        self.assertEqual(send.call_count,1,result)
        self.assertEqual(ex.get(old['id'])['state'],'UNKNOWN')
        self.assertTrue(ex.get(old['id'])['quota_released'])

    def test_repeated_recovery_is_not_limited_to_once(self):
        b=self.batch();self.record(b,0,'FAILED')
        stack,prep,send=self.mocks(lambda key:ex.save({**ex.get(key),'state':'FAILED'}))
        with stack:
            for _ in range(3):
                op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
                recovery.confirm(op['id'],'p')
        self.assertEqual(send.call_count,3)
        self.assertEqual(len(scans.get(b['id'])['cases'][0]['attempt_history']),3)

    def test_numeric_edit_preserves_original_bundle_and_geometry(self):
        b=self.batch();old=self.record(b,0)
        original=(self.root/'inputs'/(old['bundle_id']+'.zip')).read_bytes()
        stack,prep,send=self.mocks()
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']],parameter_patch='NELM=200\n')
            changed=op['items'][0]['new_bundle_id'];self.assertNotEqual(changed,old['bundle_id'])
            new,files=ex.bundle(changed,allow_dependencies=True)
            self.assertIn(b'NELM=200',files['INCAR'])
            self.assertEqual(files['POSCAR'],ex.bundle(old['bundle_id'],True)[1]['POSCAR'])
            recovery.confirm(op['id'],'p')
        after=scans.get(b['id']);self.assertEqual(len(after['cases']),2)
        self.assertEqual((self.root/'inputs'/(old['bundle_id']+'.zip')).read_bytes(),original)

    def test_scan_axis_edit_creates_labeled_derived_case(self):
        b=self.batch();self.record(b,0)
        stack,prep,send=self.mocks()
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']],parameter_patch='ENCUT=520\n')
            recovery.confirm(op['id'],'p')
        after=scans.get(b['id']);self.assertEqual(len(after['cases']),3)
        self.assertEqual(after['cases'][0]['parameters'][0]['value'],'400')
        self.assertEqual(after['cases'][2]['parameters'][0]['value'],'520')
        self.assertEqual(after['cases'][2]['derived_from_case_id'],b['cases'][0]['id'])
        self.assertEqual(ex.get(after['cases'][2]['attempt_id'])['scan_case_id'],after['cases'][2]['id'])

    def test_competing_previews_cannot_claim_same_pending_case(self):
        b=self.batch();stack,prep,send=self.mocks()
        with stack:
            a=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']]);other=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            recovery.confirm(a['id'],'p')
            with self.assertRaises(ValueError):recovery.confirm(other['id'],'p')
        self.assertEqual(send.call_count,1)

    def test_live_task_not_recomputed(self):
        b=self.batch();self.record(b,0,'RUNNING');stack,prep,send=self.mocks()
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            self.assertIn('在途',op['items'][0]['error'])
            with self.assertRaisesRegex(ValueError,'没有可提交'):recovery.confirm(op['id'],'p')
        send.assert_not_called()

    def test_optional_limits_and_released_unknown(self):
        self.assertEqual(control.settings(),{'max_inflight_total':None,'max_inflight_per_host':None,'max_cores_per_host':None,'worker_interval_seconds':300})
        self.assertEqual(control.validate_settings({'max_inflight_total':None,'max_inflight_per_host':1,'max_cores_per_host':None})['max_inflight_per_host'],1)
        b=self.batch();old=self.record(b,0,'UNKNOWN');recovery.release_quota('p',old['id'])
        control.save_settings({'max_inflight_total':1,'max_inflight_per_host':1,'max_cores_per_host':48})
        with ex.db() as db:control.enforce_limits(db,{'config':{'alias':'jutai-1'},'profile':{'ppn':48}})

    def test_scheduler_queue_selection_ignores_busy_and_load_but_bounds_request(self):
        facts={'CPUS_TOTAL':'48','CPUS_FREE':'0','STATUS':'DOWN','GPU_ACTIVE_PROCESSES':'4','GPUS_TOTAL':'2'}
        selected=servers.selector.scheduler_resources(facts,48,{'mpi_ranks':2,'gpus':2})
        self.assertEqual(selected['requested_cores'],48)
        self.assertEqual(servers.selector.scheduler_resources(facts,49)['requested_cores'],49)
        self.assertEqual(servers.selector.scheduler_resources(facts,48,{'mpi_ranks':2,'gpus':3})['requested_gpus'],3)

    def test_manual_source_warning_does_not_change_automatic_eligibility(self):
        b=self.batch();r=self.record(b,0,'COMPLETED');r['job_id']='1.node01';r['path_schema']='fpilot.remote-attempt-path'
        with patch.object(source_contracts,'actual_inputs',return_value=({'node_id':'c'},{})),patch.object(ex,'frozen_project',return_value=self.project):
            strict=source_contracts.eligible(r,'upstream','charge',{})
            manual=source_contracts.eligible(r,'upstream','charge',{'electronic_usage':'initial','draft':{'acknowledge_unverified':True}})
        self.assertIn('程序尚未正常完成',strict)
        self.assertEqual(manual,[])


class RealRecoveryPipelineTests(unittest.TestCase):
    setUp=TaskRecoveryTests.setUp
    tearDown=TaskRecoveryTests.tearDown
    batch=TaskRecoveryTests.batch
    # Exercise actual freeze -> prepare -> submit code. The only remote boundary
    # is a synthetic SSH receipt; no scheduler or scientific program is invoked.
    def test_actual_vasp_pipeline_freezes_edited_input_and_sends_once(self):
        from backend import remote_transport
        b=self.batch()
        config={**servers.DEFAULT,'alias':'d-test','queue':'batch','mpi_executable':'/opt/mpi/mpirun','vasp_executable':'/opt/vasp/vasp_std','environment_script':'/opt/env.sh'}
        b['authorization'].update(config=config,cores=4);scans.save(b)
        profile=servers.profile(config,'node01',4);script=servers.pbs.render_script(profile);calls=[]
        def remote(alias,command,input_bytes=None,**kw):
            calls.append(command)
            if '.submit-once' in command:return {'exit_code':0,'stdout':'FPILOT_JOB_ID=777.mock\n','stderr':''}
            return {'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/test/work\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
        rendered={'resource_ready':True,'profile':profile,'script':script,'blockers':[]}
        with patch.object(servers,'validate',side_effect=lambda c:c),patch.object(servers,'capacity_probe',return_value={'id':'mock'}),patch.object(servers,'preview',return_value=rendered),patch.object(remote_transport,'_interpreter',return_value='/usr/bin/python3'),patch.object(ex,'ssh',side_effect=remote):
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']],parameter_patch='NELM=200\n')
            self.assertFalse(op['items'][0].get('error'),op)
            result=recovery.confirm(op['id'],'p');recovery.confirm(op['id'],'p');recovery.tick()
        self.assertEqual(result['items'][0]['job_id'],'777.mock',result)
        self.assertEqual(sum('.submit-once' in c for c in calls),1)
        record=ex.get(result['items'][0]['attempt_key']);self.assertTrue(recovery.authorized(record))
        self.assertEqual(record['input_audit']['status'],'PASS')


class WannierRecoveryPipelineTests(unittest.TestCase):
    def setUp(self):
        import test_wannier_windows as fixture
        self.f=fixture.WindowDispatchTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_window_recovery_uses_new_win_identity_and_writable_checkpoint_copy(self):
        from backend import wannier_execution as w, source_files
        f=self.f;b=f.freeze();b.update(state='PAUSED',authorization={'config':{'alias':'jutai-1','scheduler':'pbs','remote_workspace_root':'$HOME/230269265lht'},'node':'node01','cores':2,'max_parallel':1,'template_id':None});scans.save(b)
        calls=[]
        def remote(alias,command,input_bytes=None,**kw):
            calls.append(command);return {'exit_code':0,'stdout':'FPILOT_JOB_ID='+str(len(calls))+'.mock\n','stderr':''}
        checkpoint={'size_bytes':100,'mtime':'chk-time','verification_mode':'metadata'}
        def snapshot(alias,path,names,*args,**kw):
            return {names[0]:checkpoint} if names==['wannier90.chk'] else f.source
        with patch.object(servers,'validate',side_effect=lambda c:c),patch.object(servers,'capacity_probe',return_value={'id':'mock'}),patch.object(servers,'preview',side_effect=f.rendered),patch.object(ex,'ssh',side_effect=remote),patch.object(w.remote_paths,'parse_workspace_probe',return_value={'absolute_path':'/home/u/230269265lht'}),patch.object(source_files,'snapshot',side_effect=snapshot):
            case=b['cases'][0]
            op=recovery.preview('project','scans',b['id'],[case['id']],parameter_patch='num_iter=300\n')
            self.assertFalse(op['items'][0].get('error'),op)
            result=recovery.confirm(op['id'],'project')
            self.assertEqual(result['items'][0]['dispatch_state'],'SENT',result)
            record=ex.get(result['items'][0]['attempt_key']);self.assertTrue(recovery.authorized(record))
            self.assertEqual(w.configuration(record['bundle_id'])['win_sha256'],w._sha(w.configuration(record['bundle_id'])['win'].encode()))
            self.assertIn('num_iter = 300',w.configuration(record['bundle_id'])['win'])
            record.update(state='COMPLETED');ex.save(record)
            resumed=recovery.preview('project','scans',b['id'],[case['id']],restart_mode='checkpoint')
            self.assertFalse(resumed['items'][0].get('error'),resumed)
            final=recovery.confirm(resumed['id'],'project');self.assertEqual(final['items'][0]['dispatch_state'],'SENT',final)
            newest=ex.get(final['items'][0]['attempt_key'])
            self.assertEqual(newest['source_file_plan']['wannier90.chk']['mode'],'reflink-or-copy')
            self.assertTrue(any('cp --reflink=auto' in c and 'wannier90.chk' in c for c in calls))
            self.assertEqual(scans.get(b['id'])['state'],'PAUSED')


class RecoveryCrashTests(unittest.TestCase):
    setUp=TaskRecoveryTests.setUp
    tearDown=TaskRecoveryTests.tearDown
    batch=TaskRecoveryTests.batch
    record=TaskRecoveryTests.record
    mocks=TaskRecoveryTests.mocks

    def test_derived_case_is_not_duplicated_after_container_save_crash(self):
        b=self.batch();self.record(b,0)
        stack,prep,send=self.mocks()
        original_save=ex.save
        def crash(record,*args,**kwargs):
            if record.get('scan_case_id') not in (None,b['cases'][0]['id']):raise SystemExit('simulated process crash')
            return original_save(record,*args,**kwargs)
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']],parameter_patch='ENCUT=520\n')
            with patch.object(ex,'save',side_effect=crash):
                with self.assertRaises(SystemExit):recovery.confirm(op['id'],'p')
            self.assertEqual(len(scans.get(b['id'])['cases']),3)
            recovery.tick();recovery.tick()
        cases=scans.get(b['id'])['cases'];self.assertEqual(len(cases),3)
        self.assertEqual(send.call_count,1)
        self.assertEqual(ex.get(cases[2]['attempt_id'])['scan_case_id'],cases[2]['id'])
        self.assertEqual(cases[2]['input_hashes']['INCAR'],ex.bundle(cases[2]['bundle_id'],True)[0]['files']['INCAR']['sha256'])


class ResourcePolicyPipelineTests(unittest.TestCase):
    def test_busy_gpu_can_queue_without_weakening_normal_idle_policy_or_live_source_guards(self):
        import test_gpu_submission as fixture
        from backend import fast_dispatch
        f=fixture.GpuSubmissionTests();f.setUp();self.addCleanup(f.tearDown)
        probe=f.probe(active=1,status='BUSY',allocated=1)
        strict=servers.preview(f.config,probe['id'],'node01')
        self.assertFalse(strict['resource_ready'])
        queued=servers.preview({**f.config,'resource_policy':'scheduler_queue'},probe['id'],'node01')
        self.assertTrue(queued['resource_ready'],queued)
        self.assertEqual(queued['profile']['ppn'],10)
        self.assertEqual(queued['profile']['gpus'],1)
        self.assertTrue(any('GPU 活动进程' in w for w in queued['script_warnings']))
        record={'config':{**f.config,'resource_policy':'scheduler_queue'},'node':'node01','cores':10,'reason':'','profile':queued['profile']}
        with patch('backend.remote_transport._interpreter',return_value='/usr/bin/python3'):
            prefix=fast_dispatch.prefix(record)
        self.assertNotIn('qstat -Qf',prefix)
        self.assertIn('FPILOT_NOT_DISPATCHED',prefix)
        guards=fast_dispatch.source_guards('/source',{'CHGCAR':{'size_bytes':100,'mtime':'fixed'}})
        self.assertIn('stat -c %s',guards)
        self.assertIn('test ! -L',guards)


class RecoveryPauseAndSnapshotTests(unittest.TestCase):
    setUp=TaskRecoveryTests.setUp
    tearDown=TaskRecoveryTests.tearDown
    batch=TaskRecoveryTests.batch
    mocks=TaskRecoveryTests.mocks

    def test_explicit_pause_stops_waiting_trial_until_same_scope_is_resumed(self):
        b=self.batch('ACTIVE');stack,prep,send=self.mocks()
        busy={'resource_ready':False,'capacity_wait':True,'node_status':'BUSY','blockers':['selected node CPUS_FREE must be greater than zero'],'script':'true','profile':{'ppn':48}}
        with stack:
            with patch.object(servers,'preview_for_prepare',return_value=busy):
                op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']]);recovery.confirm(op['id'],'p')
            scans.pause(b['id'],'p');recovery.tick();self.assertEqual(send.call_count,0)
            recovery.resume(op['id'],'p');self.assertEqual(send.call_count,1)

    def test_snapshot_preserves_recovery_evidence_and_restores_it_paused(self):
        import tempfile
        from pathlib import Path
        from backend import project_snapshot as snapshot, snapshot_sync
        from test_project_sync import initialize
        root=self.root/'source';root.mkdir();initialize(root)
        key='e'*32
        op={'id':key,'state':'CONFIRMED','project_id':'project-a','kind':'attempts','target_id':'a'*32,'scope':'single','items':[],'created_at':ex.now()}
        folder=root/'task-recovery';folder.mkdir();(folder/(key+'.json')).write_text(json.dumps(op))
        manifest,objects=snapshot.capture(root,'project-a')
        self.assertIn('task-recovery/'+key+'.json',manifest['files'])
        destination=self.root/'destination';destination.mkdir()
        snapshot.restore(destination,manifest,objects,0,pause_automatic=True)
        with patch.object(ex,'ROOT',destination),patch.object(ex,'submit') as send:
            restored=recovery.read(key);self.assertEqual(restored['state'],'PAUSED')
            stale={**restored,'state':'FINISHED'};recovery.save(stale)
            self.assertEqual(recovery.read(key)['state'],'PAUSED')
            recovery.tick();send.assert_not_called()
        with patch.object(ex,'ROOT',root):
            snapshot_sync.pause('project-a')
            self.assertEqual(recovery.read(key)['state'],'PAUSED')


class RecoveryIntentChangesTests(unittest.TestCase):
    setUp=TaskRecoveryTests.setUp
    tearDown=TaskRecoveryTests.tearDown
    batch=TaskRecoveryTests.batch
    mocks=TaskRecoveryTests.mocks

    def test_waiting_trial_can_be_reconfigured_without_reviving_its_old_scope(self):
        b=self.batch();stack,prep,send=self.mocks()
        busy={'resource_ready':False,'capacity_wait':True,'node_status':'BUSY','blockers':['selected node CPUS_FREE must be greater than zero'],'script':'true','profile':{'ppn':48}}
        with stack:
            with patch.object(servers,'preview_for_prepare',return_value=busy):
                old=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']]);recovery.confirm(old['id'],'p')
            new=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']],config_overrides={'resource_policy':'scheduler_queue'})
            recovery.confirm(new['id'],'p');recovery.tick()
        self.assertEqual(send.call_count,1)
        self.assertEqual(recovery.read(old['id'])['items'][0]['dispatch_state'],'SUPERSEDED')
        self.assertEqual(scans.get(b['id'])['state'],'PAUSED')

    def test_cancelled_pending_case_never_dispatches_from_old_recovery_intent(self):
        from backend.execution_cancel import cancel_case
        b=self.batch();stack,prep,send=self.mocks()
        busy={'resource_ready':False,'capacity_wait':True,'node_status':'BUSY','blockers':['selected node CPUS_FREE must be greater than zero'],'script':'true','profile':{'ppn':48}}
        with stack:
            with patch.object(servers,'preview_for_prepare',return_value=busy):
                op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']]);recovery.confirm(op['id'],'p')
            cancel_case('p',b['id'],b['cases'][0]['id'],True)
            recovery.tick();send.assert_not_called()
        self.assertEqual(scans.get(b['id'])['cases'][0]['state'],'CANCELLED')


class RecoveryBackgroundTests(unittest.TestCase):
    setUp=TaskRecoveryTests.setUp
    tearDown=TaskRecoveryTests.tearDown
    batch=TaskRecoveryTests.batch
    record=TaskRecoveryTests.record
    mocks=TaskRecoveryTests.mocks
    def test_http_confirmation_returns_before_remote_work_and_duplicate_cannot_send_again(self):
        import importlib,threading,asyncio,time
        from fastapi import BackgroundTasks
        api=importlib.import_module('backend.app')
        b=self.batch();self.record(b,0,'FAILED')
        entered=threading.Event();release=threading.Event();errors=[]
        def send(key):
            entered.set()
            if not release.wait(5):raise RuntimeError('test release timeout')
            return ex.save({**ex.get(key),'state':'SUBMITTED','job_id':'321.real-contract'})
        stack,prep,sender=self.mocks(send)
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            background=BackgroundTasks();start=__import__('time').monotonic()
            result=api.recovery_confirm(op['id'],api.RecoveryConfirmation(project_id='p'),background)
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(result['state'],'CONFIRMED');self.assertEqual(sender.call_count,0)
            def work():
                try:asyncio.run(background())
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(3))
                current=recovery.status(op['id'],'p')
                self.assertEqual(current['items'][0]['progress']['phase'],'SUBMITTING')
                duplicate=BackgroundTasks()
                repeated=api.recovery_confirm(op['id'],api.RecoveryConfirmation(project_id='p'),duplicate)
                self.assertEqual(repeated['state'],'CONFIRMED')
                recovery.process(op['id']) # parallel worker observes the same lock
                self.assertEqual(sender.call_count,1)
            finally:release.set();thread.join(5)
            asyncio.run(duplicate());recovery.tick()
            self.assertFalse(errors)
            self.assertEqual(sender.call_count,1)
            self.assertEqual(recovery.status(op['id'],'p')['items'][0]['job_id'],'321.real-contract')

    def test_timeout_receipt_is_unknown_and_late_job_id_does_not_resubmit(self):
        b=self.batch();self.record(b,0,'FAILED')
        def send(key):
            return ex.save({**ex.get(key),'state':'UNKNOWN','dispatch_receipt':{'stderr':'SSH 请求超时','transport_state':'UNCERTAIN'}})
        stack,prep,sender=self.mocks(send)
        with stack:
            op=recovery.preview('p','scans',b['id'],[b['cases'][0]['id']])
            result=recovery.confirm(op['id'],'p')
            item=result['items'][0]
            self.assertEqual(item['dispatch_state'],'UNCERTAIN')
            self.assertIn('超时',item['error'])
            record=ex.get(item['attempt_key']);record.update(state='SUBMITTED',job_id='late.123');ex.save(record)
            self.assertEqual(recovery.status(op['id'],'p')['items'][0]['job_id'],'late.123')
            self.assertEqual(recovery.status(op['id'],'p')['items'][0]['dispatch_state'],'SENT')
            recovery.process(op['id']);recovery.tick()
            self.assertEqual(sender.call_count,1)
            with self.assertRaises(ValueError):recovery.status(op['id'],'another-project')

    def test_candidate_pool_reserves_just_submitted_node_and_spreads_queue_mode(self):
        from backend import candidate_pool
        import hashlib
        b=self.batch();record=self.record(b,0,'SUBMITTED')
        choices=[{'node':n,'cores':48,'reason':'','profile':{'node':n},'script_sha256':hashlib.sha256(n.encode()).hexdigest()} for n in ('node01','node02')]
        render=lambda config,probe,node,cores,reason:{'resource_ready':True,'profile':{'node':node},'script':node}
        for policy in ('wait_idle','scheduler_queue'):
            choice,_=candidate_pool.select({'alias':'jutai-1','resource_policy':policy},{'id':'stale'},choices,render=render)
            self.assertEqual(choice['node'],'node02')
        record.update(state='COMPLETED');ex.save(record)
        choice,_=candidate_pool.select({'alias':'jutai-1'},{'id':'stale'},choices,render=render)
        self.assertEqual(choice['node'],'node01')

    def test_unknown_note_identifies_remote_directory_phase(self):
        from backend import fast_dispatch
        record={'state':'UNKNOWN','dispatch_receipt':{'exit_code':None,'stdout':'FPILOT_SUBMIT_PHASE=WORKSPACE\n','stderr':'timeout'}}
        fast_dispatch.tracking_note(record)
        self.assertIn('目录创建或解析未返回',record['note'])
