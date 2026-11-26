import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, ExitStack
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex, recycle, scan_results, summary_collection as queue


class SummaryCollectionTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for module in (ex, recycle):
            stack.enter_context(patch.object(module, 'ROOT', root))
        self.key = 'ab' * 16
        self.record = dict(id=self.key, project_id='p', state='RUNNING', kind='vasp', evidence={})
        with closing(ex.db()) as connection, connection:
            connection.execute('INSERT INTO attempts VALUES(?,?,?)', (self.key, 'RUNNING', json.dumps(self.record)))
        self.metrics = {'energy': {'value': -4., 'unit': 'eV/cell'}}
        self.extract = stack.enter_context(patch.object(scan_results, '_extract', return_value=self.metrics))

    def finish(self):
        self.record.update(state='COMPLETED', evidence={'vasp_timing': True})
        ex.save(self.record)

    def test_completion_and_two_clients_collect_once(self):
        self.finish()
        entered, release = threading.Event(), threading.Event()
        def extract(_):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.metrics
        self.extract.side_effect = extract
        with ThreadPoolExecutor(max_workers=2) as pool:
            work = pool.submit(queue.process_one)
            try:
                self.assertTrue(entered.wait(3))
                for _ in range(2):
                    self.assertEqual(queue.request('p', self.key)['state'], 'pending')
                self.assertFalse(queue.process_one())
            finally:
                release.set()
            self.assertTrue(work.result())
        self.assertEqual(queue.request('p', self.key)['state'], 'ready')
        self.assertFalse(queue.process_one())
        self.extract.assert_called_once()
        self.assertEqual(ex.get(self.key)['state'], 'COMPLETED')

    def test_status_only_and_refresh_from_two_windows(self):
        self.finish()
        with closing(ex.db()) as connection, connection:
            connection.execute('DELETE FROM summary_queue')
        self.assertEqual(queue.status('p', self.key)['state'], 'missing')
        self.assertFalse(queue.process_one())
        queue.request('p', self.key)
        queue.process_one()
        cached = queue.overview.read_cache(self.record)
        for _ in range(2):
            self.assertEqual(queue.request('p', self.key, checked_at=cached['checked_at'])['state'], 'pending')
        self.extract.side_effect = OSError('offline')
        queue.process_one()
        self.assertEqual(queue.status('p', self.key)['state'], 'waiting')
        self.assertEqual(queue.overview.read_cache(self.record), cached)
        with closing(ex.db()) as connection, connection:
            connection.execute('UPDATE summary_queue SET due=0')
        self.extract.side_effect = None
        queue.process_one()
        self.assertEqual(queue.request('p', self.key, checked_at=cached['checked_at'])['state'], 'ready')
        self.assertFalse(queue.process_one())
        self.assertEqual(self.extract.call_count, 3)

    def test_persistent_retry_and_manual_recovery(self):
        self.finish()
        self.extract.side_effect = OSError('offline')
        for _ in range(queue.MAX_FAILURES):
            self.assertTrue(queue.process_one())
            self.assertFalse(queue.process_one())
            with closing(ex.db()) as connection, connection:
                connection.execute('UPDATE summary_queue SET due=0')
        self.assertEqual(queue.request('p', self.key)['state'], 'failed')
        self.extract.side_effect = None
        queue.request('p', self.key, retry=True)
        queue.process_one()
        self.assertEqual(queue.request('p', self.key)['state'], 'ready')

    def test_recycled_project_and_changed_target_skip_remote_read(self):
        self.finish()
        with patch.object(ex, 'assert_recovery_target', side_effect=ValueError('连接目标已变化')):
            queue.process_one()
        self.assertEqual(queue.request('p', self.key)['state'], 'waiting')
        with patch.object(recycle, 'inactive', return_value=True):
            with closing(ex.db()) as connection, connection:
                connection.execute('UPDATE summary_queue SET due=0')
            self.assertRaises(ValueError, queue.request, 'p', self.key)
            queue.process_one()
        self.extract.assert_not_called()

    def test_existing_completion_and_scope(self):
        self.assertRaises(ValueError, queue.request, 'p', self.key)
        self.finish()
        with closing(ex.db()) as connection, connection:
            connection.execute('DELETE FROM summary_queue')
        self.assertFalse(queue.process_one())
        self.assertRaises(ValueError, queue.request, 'other', self.key)
        self.assertEqual(queue.request('p', self.key)['state'], 'pending')
        queue.process_one()
        self.extract.assert_called_once()
