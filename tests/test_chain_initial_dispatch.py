import copy
import json
import subprocess
import sys
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend import chain_runs as chains, execution as ex
import test_chain_runs as fixtures


class InitialDispatchTests(unittest.TestCase):
    setUp = fixtures.ChainRunTests.setUp
    tearDown = fixtures.ChainRunTests.tearDown

    def frozen(self):
        second = copy.deepcopy(self.project['nodes'][1])
        second['id'] = 'r2'
        second['data'].update(label='second root', stageSequence=6, stageSlug='relax-copy')
        if not any(node['id'] == 'r2' for node in self.project['nodes']):
            self.project['nodes'].append(second)
            self.project['edges'].append({'source': 's', 'target': 'r2'})
        report = chains.preview(self.project, ['r', 'r2', 'c'])
        return chains.freeze(self.project, report['node_ids'], 'initial dispatch', report['definition_hash'])

    def payload(self, run):
        config = {'alias': 'jutai-1', 'vasp_executable': '/bin/vasp_std'}
        resource = {'vasp_executable': 'vasp_std', 'candidates': [{'node': n, 'cores': 48, 'reason': ''} for n in ('node01','node02')]}
        return dict(project_id='p', plan_hash=run['plan_hash'], mismatch_hash=run['mismatch_hash'],
                    config=config, resources={key: resource for key in run['node_ids']},
                    approved_previews={key: {n:self.render(node=n) for n in ('node01','node02')} for key in run['node_ids']})

    def render(self, busy=False,node='node01'):
        return {'resource_ready': not busy, 'node_status': 'BUSY' if busy else 'FULLY_IDLE',
                'capacity_wait': busy, 'blockers': ['selected node CPUS_FREE must be greater than zero'] if busy else [],
                'script': '#!/bin/bash\ntrue\n', 'profile': {'node': node, 'ppn': 48}}

    def prepare(self, *args, **kwargs):
        record = dict(id=kwargs['attempt_id'], state='PREPARED', bundle_id=args[0], project_id='p',
                      config=args[1], node=args[3], cores=args[4], reason=args[5])
        self.insert_attempt(record)
        return record

    def insert_attempt(self, record):
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)',
                       (record['id'], record['state'], json.dumps(record)))

    def submit(self, key):
        record = ex.get(key)
        record.update(state='SUBMITTED', job_id='123.node01')
        ex.save(record)
        return record

    def dispatch_mocks(self, prepare=None, submit=None, busy=False, quota=True):
        stack = ExitStack()
        stack.enter_context(patch.object(chains.servers, 'probe', return_value={'id': 'probe'}))
        stack.enter_context(patch.object(chains.servers, 'preview', side_effect=lambda *a,**kw:self.render(busy,node=a[2])))
        stack.enter_context(patch.object(chains, '_quota_available', return_value=quota))
        prep = stack.enter_context(patch.object(ex, 'prepare', side_effect=prepare or self.prepare))
        send = stack.enter_context(patch.object(ex, 'submit', side_effect=submit or self.submit))
        return stack, prep, send

    def test_authorization_dispatches_roots_without_worker_and_repeated_request_is_read_only(self):
        run = self.frozen()
        payload = self.payload(run)
        stack, prep, send = self.dispatch_mocks()
        with stack:
            result = chains.authorize_and_dispatch(run['id'], **payload)
            self.assertEqual(prep.call_count, 2)
            self.assertEqual(send.call_count, 2)
            self.assertEqual([call.args[3] for call in prep.call_args_list],['node01','node02'])
            states = {step['node_id']: step for step in result['steps']}
            self.assertEqual(states['r']['job_id'], '123.node01')
            self.assertEqual(states['c']['waiting_kind'], 'DEPENDENCY')
            self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
            again = chains.authorize_and_dispatch(run['id'], **payload)
            self.assertEqual(again['revision'], result['revision'])
            self.assertEqual(send.call_count, 2)
            changed = {**payload, 'config': {**payload['config'], 'alias': 'other'}}
            with self.assertRaises(ValueError):
                chains.authorize_and_dispatch(run['id'], **changed)
            self.assertEqual(send.call_count, 2)

    def test_busy_and_quota_wait_do_not_prepare_or_submit(self):
        for busy, quota, kind in [(True, True, None), (False, False, 'QUOTA')]:
            with self.subTest(busy=busy):
                run = self.frozen()
                stack, prep, send = self.dispatch_mocks(busy=busy, quota=quota)
                with stack:
                    result = chains.authorize_and_dispatch(run['id'], **self.payload(run))
                prep.assert_not_called()
                send.assert_not_called()
                first = result['steps'][0]
                self.assertEqual(first['state'], 'PENDING')
                self.assertTrue(first['waiting_reason'])
                if kind: self.assertEqual(first['waiting_kind'], kind)

    def test_worker_and_http_share_lock_and_duplicate_call_does_not_submit_twice(self):
        run = self.frozen(); payload = self.payload(run)
        entered = threading.Event(); release = threading.Event(); results = []
        def prepare(*args, **kwargs):
            entered.set(); release.wait(5)
            return self.prepare(*args, **kwargs)
        stack, prep, send = self.dispatch_mocks(prepare=prepare)
        with stack:
            thread = threading.Thread(target=lambda: results.append(chains.authorize_and_dispatch(run['id'], **payload)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                chains.tick()
                again = chains.authorize_and_dispatch(run['id'], **payload)
                self.assertEqual(again['initial_dispatch']['state'], 'IN_PROGRESS')
                send.assert_not_called()
                lock_path = self.root/'chain-locks'/(chains.digest(run['id'])+'.lock')
                result = subprocess.run([sys.executable, '-c',
                    'from backend.platform_runtime import exclusive_lock;import sys;\nwith exclusive_lock(sys.argv[1]) as owned: print(owned)', str(lock_path)],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.stdout.strip(), 'False', result.stderr)
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(send.call_count, 2)
            chains.tick()
            self.assertEqual(send.call_count, 2)

    def test_pause_during_prepare_keeps_pause_and_never_submits(self):
        run = self.frozen()
        def prepare(*args, **kwargs):
            chains.pause(run['id'], 'p')
            return self.prepare(*args, **kwargs)
        stack, prep, send = self.dispatch_mocks(prepare=prepare)
        with stack:
            result = chains.authorize_and_dispatch(run['id'], **self.payload(run))
        self.assertEqual(result['state'], 'PAUSED')
        self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
        send.assert_not_called()

    def test_exception_after_authorization_and_unknown_receipt_remain_inspectable(self):
        run = self.frozen()
        def submit(key):
            record = ex.get(key); record.update(state='UNKNOWN', note='回执丢失，核查原任务')
            ex.save(record)
            raise ConnectionError('receipt lost')
        stack, prep, send = self.dispatch_mocks(submit=submit)
        with stack:
            result = chains.authorize_and_dispatch(run['id'], **self.payload(run))
            self.assertTrue(result['authorization'])
            self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
            self.assertEqual(result['steps'][0]['attempt_state'], 'UNKNOWN')
            self.assertIn('receipt lost', result['last_worker_error'])
            chains.authorize_and_dispatch(run['id'], **self.payload(run))
            self.assertEqual(send.call_count, 1)

    def test_status_reads_attempt_progress_without_remote_reads_or_revision_changes(self):
        run = self.frozen()
        run['steps'][0]['attempt_id'] = 'local'
        run = chains.save(run)
        self.insert_attempt({'id': 'local', 'project_id': 'p', 'state': 'DISPATCHING', 'job_id': None})
        with patch.object(ex, 'ssh', side_effect=AssertionError('no SSH')), \
             patch.object(ex, 'refresh', side_effect=AssertionError('no refresh')):
            value = chains.status(run['id'], 'p')
            self.assertEqual(value['steps'][0]['attempt_state'], 'DISPATCHING')
            self.assertEqual(chains.get(run['id'])['revision'], run['revision'])
            self.assertNotIn('attempt_state', chains.get(run['id'])['steps'][0])
            with self.assertRaises(ValueError): chains.status(run['id'], 'other')

    def test_pause_during_submit_preserves_job_id_and_stops_other_roots(self):
        run = self.frozen()
        def submit(key):
            chains.pause(run['id'], 'p')
            return self.submit(key)
        stack, prep, send = self.dispatch_mocks(submit=submit)
        with stack:
            result = chains.authorize_and_dispatch(run['id'], **self.payload(run))
        self.assertEqual(result['state'], 'PAUSED')
        self.assertEqual(result['steps'][0]['job_id'], '123.node01')
        self.assertEqual(send.call_count, 1)

    def test_worker_finishes_interrupted_initial_pass_without_new_authorization(self):
        run = self.frozen(); payload = self.payload(run)
        stack, prep, send = self.dispatch_mocks()
        with stack:
            active = chains.authorize(run['id'], **payload, _initial_request_hash=chains.digest(payload))
            self.assertEqual(active['initial_dispatch']['state'], 'IN_PROGRESS')
            chains.tick()
            result = chains.status(run['id'], 'p')
            self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
            self.assertEqual(send.call_count, 2)
            chains.tick()
            self.assertEqual(send.call_count, 2)

    def test_api_uses_initial_dispatch_and_local_status(self):
        from backend import app
        run = self.frozen()
        payload = app.ChainAuthorization(**self.payload(run))
        with patch.object(chains, 'authorize_and_dispatch', return_value={'state': 'ACTIVE'}) as dispatch:
            self.assertEqual(app.chain_authorize(run['id'], payload), {'state': 'ACTIVE'})
            dispatch.assert_called_once_with(run['id'], **payload.model_dump())
        with patch.object(chains, 'status', return_value={'state': 'ACTIVE'}) as read:
            app.chain_get(run['id'], 'p')
            read.assert_called_once_with(run['id'], 'p')

    def test_single_candidate_waits_locally_while_scheduler_snapshot_is_stale(self):
        run=self.frozen();payload=self.payload(run)
        for resource in payload['resources'].values():resource['candidates']=resource['candidates'][:1]
        payload['approved_previews']={key:{'node01':values['node01']} for key,values in payload['approved_previews'].items()}
        stack,prep,send=self.dispatch_mocks()
        with stack:
            result=chains.authorize_and_dispatch(run['id'],**payload)
            self.assertEqual(send.call_count,1)
            second=next(s for s in result['steps'] if s['node_id']=='r2')
            self.assertEqual(second['state'],'PENDING')
            self.assertIn('候选节点暂时占用',second['waiting_reason'])
            chains.tick();self.assertEqual(send.call_count,1)
