import copy
import json
import subprocess
import sys
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend import execution as ex, scans
import test_scans as fixtures


class InitialScanDispatchTests(unittest.TestCase):
    setUp = fixtures.ScanTests.setUp
    tearDown = fixtures.ScanTests.tearDown

    def frozen(self):
        return scans.freeze(self.project, 'scan', 'c',
            [{'kind': 'incar', 'tag': 'ENCUT', 'start': '400', 'end': '450', 'step': '50'}], 'first dispatch')

    def rendered(self, busy=False):
        return {'resource_ready': not busy, 'capacity_wait': busy,
                'node_status': 'BUSY' if busy else 'FULLY_IDLE',
                'blockers': ['selected node CPUS_FREE must be greater than zero'] if busy else [],
                'profile': {'node': 'node01', 'ppn': 48}, 'script': '#!/bin/bash\ntrue\n'}

    def payload(self, batch):
        return dict(project_id='p', case_table_hash=batch['case_table_hash'],
                    config={'alias': 'jutai-1'}, candidates=[{'node': 'node01', 'cores': 48, 'reason': ''}],
                    max_parallel=1, approved_previews={'node01': self.rendered()})

    def prepare(self, *args, **kwargs):
        record = dict(id=kwargs['attempt_id'], state='PREPARED', bundle_id=args[0], project_id='p',
                      config=args[1], node=args[3], cores=args[4], reason=args[5],
                      profile=self.rendered()['profile'], execution_scope=kwargs['execution_scope'])
        with ex.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?)', (record['id'], record['state'], json.dumps(record)))
        return record

    def submit(self, key):
        record = ex.get(key); record.update(state='SUBMITTED', job_id='123.node01')
        return ex.save(record)

    def mocks(self, prepare=None, submit=None, busy=False):
        stack = ExitStack()
        stack.enter_context(patch.object(scans.servers, 'probe', return_value={'id': 'probe'}))
        stack.enter_context(patch.object(scans.servers, 'capacity_probe', return_value={'id': 'capacity'}))
        stack.enter_context(patch.object(scans.servers, 'preview', return_value=self.rendered(busy)))
        prep = stack.enter_context(patch.object(ex, 'prepare', side_effect=prepare or self.prepare))
        send = stack.enter_context(patch.object(ex, 'submit', side_effect=submit or self.submit))
        return stack, prep, send

    def test_first_case_dispatches_without_worker_repeated_request_only_reads(self):
        batch = self.frozen(); payload = self.payload(batch)
        stack, prep, send = self.mocks()
        with stack:
            result = scans.authorize_and_dispatch(batch['id'], **payload)
            self.assertEqual(result['cases'][0]['job_id'], '123.node01')
            self.assertEqual(result['cases'][1]['state'], 'PENDING')
            self.assertIn('同时运行上限', result['waiting_reason'])
            self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
            revision = result['revision']
            again = scans.authorize_and_dispatch(batch['id'], **payload)
            self.assertEqual(again['revision'], revision)
            prep.assert_called_once(); send.assert_called_once()
            with self.assertRaises(ValueError):
                scans.authorize_and_dispatch(batch['id'], **{**payload, 'max_parallel': 2})
            send.assert_called_once()

    def test_busy_or_quota_wait_does_not_prepare(self):
        for busy, quota in [(True, False), (False, True)]:
            with self.subTest(busy=busy):
                batch = self.frozen()
                stack, prep, send = self.mocks(busy=busy)
                if quota:
                    stack.enter_context(patch.object(scans.control, 'settings', return_value={
                        'max_inflight_total': 0, 'max_inflight_per_host': 1, 'max_cores_per_host': None}))
                with stack:
                    result = scans.authorize_and_dispatch(batch['id'], **self.payload(batch))
                self.assertEqual(result['cases'][0]['state'], 'PENDING')
                self.assertTrue(result['waiting_reason'])
                self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
                prep.assert_not_called(); send.assert_not_called()

    def test_http_worker_and_second_http_share_batch_lock(self):
        batch = self.frozen(); payload = self.payload(batch)
        entered = threading.Event(); release = threading.Event(); results = []
        def prepare(*args, **kwargs):
            entered.set(); release.wait(5)
            return self.prepare(*args, **kwargs)
        stack, prep, send = self.mocks(prepare=prepare)
        with stack:
            thread = threading.Thread(target=lambda: results.append(scans.authorize_and_dispatch(batch['id'], **payload)))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                scans.tick()
                again = scans.authorize_and_dispatch(batch['id'], **payload)
                self.assertEqual(again['initial_dispatch']['state'], 'IN_PROGRESS')
                send.assert_not_called()
                path = self.root/'scan-locks'/(scans.digest(batch['id'])+'.lock')
                result = subprocess.run([sys.executable, '-c',
                    'from backend.platform_runtime import exclusive_lock;import sys;\nwith exclusive_lock(sys.argv[1]) as owned: print(owned)', str(path)],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.stdout.strip(), 'False', result.stderr)
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive())
            prep.assert_called_once(); send.assert_called_once()
            scans.tick(); send.assert_called_once()

    def test_pause_during_prepare_or_submit_preserves_pause_and_receipt(self):
        for phase in ['prepare', 'submit']:
            with self.subTest(phase=phase):
                batch = self.frozen()
                def prepare(*args, **kwargs):
                    if phase == 'prepare': scans.pause(batch['id'], 'p')
                    return self.prepare(*args, **kwargs)
                def submit(key):
                    scans.pause(batch['id'], 'p')
                    return self.submit(key)
                stack, prep, send = self.mocks(prepare=prepare, submit=submit)
                with stack:
                    result = scans.authorize_and_dispatch(batch['id'], **self.payload(batch))
                self.assertEqual(result['state'], 'PAUSED')
                self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
                if phase == 'prepare': send.assert_not_called()
                else:
                    send.assert_called_once()
                    self.assertEqual(result['cases'][0]['job_id'], '123.node01')

    def test_unknown_response_recovery_does_not_resubmit(self):
        batch = self.frozen(); payload = self.payload(batch)
        def submit(key):
            record = ex.get(key); record.update(state='UNKNOWN', note='回执待核验')
            ex.save(record)
            raise ConnectionError('receipt lost')
        stack, prep, send = self.mocks(submit=submit)
        with stack:
            result = scans.authorize_and_dispatch(batch['id'], **payload)
            self.assertEqual(result['cases'][0]['state'], 'UNKNOWN')
            self.assertEqual(result['state'], 'ACTIVE')
            scans.authorize_and_dispatch(batch['id'], **payload)
            scans.tick()
            send.assert_called_once()

    def test_status_is_local_and_does_not_rewrite_history(self):
        batch = self.frozen()
        record = self.prepare(batch['cases'][0]['bundle_id'], {'alias': 'jutai-1'}, 'p', 'node01', 48, '',
                              attempt_id='local', execution_scope='scan')
        record.update(state='DISPATCHING', scan_batch_id=batch['id']); ex.save(record)
        batch['cases'][0]['attempt_id'] = record['id']; scans.save(batch)
        before = copy.deepcopy(scans.get(batch['id']))
        with patch.object(ex, 'ssh', side_effect=AssertionError('no SSH')), \
             patch.object(ex, 'refresh', side_effect=AssertionError('no refresh')):
            value = scans.status(batch['id'], 'p')
            self.assertEqual(value['cases'][0]['state'], 'DISPATCHING')
            self.assertEqual(scans.get(batch['id']), before)
            with self.assertRaises(ValueError): scans.status(batch['id'], 'other')

    def test_worker_finishes_interrupted_initial_pass(self):
        batch = self.frozen(); payload = self.payload(batch)
        stack, prep, send = self.mocks()
        with stack:
            active = scans.authorize(batch['id'], **payload)
            active['initial_dispatch'] = {'state': 'IN_PROGRESS', 'request_hash': scans.digest(payload)}
            scans.save(active)
            scans.tick()
            result = scans.status(batch['id'], 'p')
            self.assertEqual(result['initial_dispatch']['state'], 'FINISHED')
            self.assertEqual(result['cases'][0]['job_id'], '123.node01')
            scans.authorize_and_dispatch(batch['id'], **payload)
            send.assert_called_once()

    def test_api_uses_immediate_dispatch_and_local_status(self):
        from backend import app
        batch = self.frozen(); payload = app.ScanAuthorization(**self.payload(batch))
        with patch.object(scans, 'authorize_and_dispatch', return_value={'state': 'ACTIVE'}) as dispatch:
            self.assertEqual(app.scan_authorize(batch['id'], payload), {'state': 'ACTIVE'})
            dispatch.assert_called_once_with(batch['id'], **payload.model_dump())
        with patch.object(scans, 'status', return_value={'state': 'ACTIVE'}) as read:
            app.scan_get(batch['id'], 'p')
            read.assert_called_once_with(batch['id'], 'p')
