import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, execution_control as control, worker


class WorkerIntervalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        root = patch.object(ex, 'ROOT', self.root)
        root.start(); self.addCleanup(root.stop)
        self.legacy = dict(control.DEFAULT)

    def test_legacy_settings_default_and_partial_old_client_save(self):
        self.assertEqual(control.settings()['worker_interval_seconds'], 300)
        control.write_json(self.root/'execution-settings.json', self.legacy)
        self.assertEqual(control.settings()['worker_interval_seconds'], 300)
        control.save_settings({**self.legacy, 'worker_interval_seconds':75})
        saved = control.save_settings({**self.legacy, 'max_inflight_total':4})
        self.assertEqual(saved['worker_interval_seconds'], 75)
        self.assertEqual(saved['max_inflight_total'], 4)
        self.assertEqual(json.loads((self.root/'execution-settings.json').read_text()), saved)

    def test_bad_period_does_not_replace_saved_settings(self):
        control.save_settings({**self.legacy, 'worker_interval_seconds':120})
        before = (self.root/'execution-settings.json').read_bytes()
        for value in (None, True, 0, -1, 2.5, '60', 9007199254740992):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '周期'):
                control.save_settings({**self.legacy, 'worker_interval_seconds':value})
            self.assertEqual((self.root/'execution-settings.json').read_bytes(), before)

    def wait(self, initial, changed=None, override=None, retry_at=None):
        control.save_settings({**self.legacy, 'worker_interval_seconds':initial})
        clock = [0.0]
        class Stop:
            def is_set(self): return False
            def wait(_, seconds):
                clock[0] += seconds
                if changed is not None and clock[0] == 1:
                    control.save_settings({**self.legacy, 'worker_interval_seconds':changed})
        heartbeat = {'interval_seconds':initial, 'heartbeat_epoch':1000, 'running':True}
        health = [] if retry_at is None else [{'state':'COOLDOWN', 'next_retry_at':retry_at}]
        with patch.object(worker.time,'monotonic',side_effect=lambda:clock[0]), patch.object(worker.time,'time',return_value=1000), patch('backend.remote_health.status',return_value=health), patch.dict(os.environ, {'FPILOT_STOP_FILE':''}):
            worker.wait_for_cycle(Stop(), heartbeat, override)
        return clock[0], heartbeat

    def test_shorter_and_longer_period_reschedule_the_same_wait(self):
        elapsed, heartbeat = self.wait(300, changed=60)
        self.assertEqual(elapsed, 60)
        self.assertEqual(heartbeat['interval_seconds'], 60)
        self.assertEqual(json.loads((self.root/'worker-state.json').read_text())['interval_seconds'],60)
        elapsed, heartbeat = self.wait(2, changed=5)
        self.assertEqual(elapsed, 5)
        self.assertEqual(heartbeat['heartbeat_epoch'],1000)

    def test_explicit_cli_period_and_host_recovery_deadline_are_preserved(self):
        elapsed, heartbeat = self.wait(300, changed=60, override=4)
        self.assertEqual(elapsed, 4)
        self.assertEqual(heartbeat['interval_seconds'],4)
        elapsed, heartbeat = self.wait(10, retry_at=1003)
        self.assertEqual(elapsed,3)
        self.assertEqual(heartbeat['interval_seconds'],10)

    def test_once_uses_saved_period_and_reports_cli_override_without_dispatch(self):
        control.save_settings({**self.legacy,'worker_interval_seconds':420})
        with patch.object(worker,'tick',return_value={'checked':0,'errors':[]}) as tick, patch.object(worker.signal,'signal'), patch.dict(os.environ, {'FPILOT_LAUNCHER_PID':''}):
            self.assertEqual(worker.run(once=True),0)
            state = json.loads((self.root/'worker-state.json').read_text())
            self.assertEqual(state['interval_seconds'],420)
            self.assertEqual(state['interval_source'],'settings')
            self.assertFalse(state['running'])
            self.assertEqual(worker.run(interval=30,once=True),0)
            state = json.loads((self.root/'worker-state.json').read_text())
            self.assertEqual(state['interval_seconds'],30)
            self.assertEqual(state['interval_source'],'command-line')
            self.assertEqual(tick.call_count,2)
